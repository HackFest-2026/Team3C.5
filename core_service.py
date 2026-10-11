"""Flexible FastAPI core for worker and mine telemetry JSON records."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query

from config import CORE_DB
from work_status import canonical_work_status

app = FastAPI(title="MineGuard Edge Core", version="0.1.0")

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node_id TEXT NOT NULL,
  boot_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  node_type TEXT NOT NULL,
  received_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(node_id, boot_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_type_received ON telemetry(node_type, received_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_node ON telemetry(node_id, id);
CREATE TABLE IF NOT EXISTS alerts (
  alert_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  boot_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  node_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def conn():
    c = sqlite3.connect(CORE_DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=10000")
    return c


def init_db():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    c.close()


init_db()


def check_packet(row: Dict[str, Any], fields: tuple[str, ...]):
    if not isinstance(row, dict):
        raise HTTPException(status_code=422, detail="Each item must be a JSON object")
    for key in fields:
        if key not in row:
            raise HTTPException(status_code=422, detail=f"Missing field: {key}")
    if str(row.get("node_type", "")).lower() not in ("worker", "mine"):
        raise HTTPException(status_code=422, detail="node_type must be worker or mine")
    try:
        int(row["seq"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="seq must be an integer")


@app.get("/health")
def health():
    return {"ok": True, "service": "mineguard-core"}


@app.post("/ingest/telemetry")
def ingest_telemetry(rows: List[Dict[str, Any]]):
    c = conn()
    try:
        before = c.total_changes
        values = []
        for row in rows:
            check_packet(row, ("node_id", "node_type", "seq"))
            boot = str(row.get("boot_id", "boot-unknown"))
            node_type = str(row.get("node_type", "")).lower()
            if node_type not in {"mine", "worker"}:
                raise HTTPException(status_code=400, detail="node_type must be mine or worker")
            # Data minimization: core storage keeps only an allowlisted upstream
            # work_status for workers; physiological/motion fields stay out of core.db.
            stored_payload = row
            if node_type == "worker":
                source_payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
                stored_payload = {"work_status": canonical_work_status(source_payload.get("work_status"))}
            values.append((
                str(row["node_id"]), boot, int(row["seq"]),
                node_type, str(row.get("received_at") or utc_now()),
                json.dumps(stored_payload, separators=(",", ":"), allow_nan=False),
            ))
        c.executemany(
            "INSERT OR IGNORE INTO telemetry(node_id,boot_id,seq,node_type,received_at,payload_json) VALUES(?,?,?,?,?,?)",
            values,
        )
        c.commit()
        added = c.total_changes - before
        return {"received": len(rows), "new": added}
    finally:
        c.close()


@app.post("/ingest/alerts")
def ingest_alerts(rows: List[Dict[str, Any]]):
    c = conn()
    try:
        before = c.total_changes
        values = []
        for row in rows:
            check_packet(row, ("alert_id", "node_id", "node_type", "seq"))
            node_type = str(row["node_type"]).lower()
            if node_type not in {"mine", "worker"}:
                raise HTTPException(status_code=400, detail="node_type must be mine or worker")
            # Worker alerts may contain health-derived data; they are not stored
            # or exposed by the general Core API in this privacy-scoped prototype.
            if node_type == "worker":
                continue
            values.append((
                str(row["alert_id"]), str(row["node_id"]),
                str(row.get("boot_id", "boot-unknown")), int(row["seq"]),
                node_type, str(row.get("created_at") or utc_now()),
                json.dumps(row, separators=(",", ":"), allow_nan=False),
            ))
        c.executemany(
            "INSERT OR IGNORE INTO alerts(alert_id,node_id,boot_id,seq,node_type,created_at,payload_json) VALUES(?,?,?,?,?,?,?)",
            values,
        )
        c.commit()
        added = c.total_changes - before
        return {"received": len(rows), "new": added}
    finally:
        c.close()


def decode_telemetry(row):
    return {
        "node_id": row["node_id"],
        "boot_id": row["boot_id"],
        "seq": row["seq"],
        "node_type": row["node_type"],
        "received_at": row["received_at"],
        "payload": json.loads(row["payload_json"]),
    }


def public_latest_record(item):
    """Expose only the allowlisted approved work status for worker nodes."""
    if item.get("node_type") != "worker":
        return item
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return {
        "node_type": "worker",
        "payload": {"work_status": canonical_work_status(payload.get("work_status"))},
    }


@app.get("/latest")
def latest():
    c = conn()
    try:
        rows = c.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 1000").fetchall()
        newest = {}
        for row in rows:
            key = row["node_id"]
            if key not in newest:
                newest[key] = decode_telemetry(row)
        return [public_latest_record(item) for item in newest.values()]
    finally:
        c.close()


@app.get("/telemetry")
def telemetry(node_id: Optional[str] = None, limit: int = Query(100, ge=1, le=2000)):
    c = conn()
    try:
        if node_id:
            rows = c.execute(
                "SELECT * FROM telemetry WHERE node_id=? AND node_type='mine' ORDER BY id DESC LIMIT ?",
                (node_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM telemetry WHERE node_type='mine' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        # Worker time-series history is not exposed; current approved status is
        # available only as an anonymous, status-only item from /latest.
        return [decode_telemetry(row) for row in rows]
    finally:
        c.close()


@app.get("/alerts")
def alerts(limit: int = Query(100, ge=1, le=2000)):
    c = conn()
    try:
        # Keep worker health-related alerts out of the general dashboard API.
        rows = c.execute(
            "SELECT * FROM alerts WHERE node_type='mine' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]
    finally:
        c.close()


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


@app.get("/twin")
def twin(stale_after_seconds: int = Query(60, ge=5, le=3600)):
    """Latest-value site snapshot; not a physics model or validated digital twin."""
    c = conn()
    try:
        rows = c.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 2000").fetchall()
        newest = {}
        for row in rows:
            key = row["node_id"]
            if key not in newest:
                newest[key] = decode_telemetry(row)
        now = datetime.now(timezone.utc)
        nodes = []
        for item in newest.values():
            # Worker identities, locations and telemetry are excluded from the twin.
            if item.get("node_type") != "mine":
                continue
            payload = item.get("payload", {})
            received = parse_time(item.get("received_at"))
            age = max(0.0, (now - received).total_seconds()) if received else None
            nodes.append({
                "node_id": item["node_id"], "boot_id": item["boot_id"],
                "node_type": item["node_type"], "zone_id": payload.get("zone_id", "unassigned"),
                "seq": item["seq"], "last_seen": item["received_at"],
                "age_seconds": round(age, 1) if age is not None else None,
                "freshness": "unknown" if age is None else ("recent" if age <= stale_after_seconds else "stale"),
                "telemetry": payload,
            })
        return {
            "view_type": "latest-telemetry snapshot",
            "scope_note": "Mine-node snapshot only. Worker identities, locations and telemetry are omitted for privacy; this is not an engineering simulation, mine-safety assessment, or validated digital twin.",
            "generated_at": utc_now(), "stale_after_seconds": stale_after_seconds,
            "nodes": nodes,
        }
    finally:
        c.close()


@app.get("/sync-gaps")
def sync_gaps(limit: int = Query(2000, ge=1, le=10000)):
    """Report sequence/timestamp gaps as heuristics, not proof of radio loss."""
    c = conn()
    try:
        rows = c.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        groups = {}
        for row in rows:
            item = decode_telemetry(row)
            if item.get("node_type") != "mine":
                continue
            key = (item["node_id"], item["boot_id"])
            groups.setdefault(key, []).append(item)
        now = datetime.now(timezone.utc)
        results = []
        for (node_id, boot_id), items in groups.items():
            seqs = sorted({int(x["seq"]) for x in items})
            gaps = [(a, b, b - a - 1) for a, b in zip(seqs, seqs[1:]) if b - a > 1]
            missing = sum(g[2] for g in gaps)
            timed = []
            event_clock_used = False
            for item in items:
                payload = item.get("payload", {})
                event_stamp = payload.get("event_time") or payload.get("timestamp_utc")
                dt = parse_time(event_stamp) if event_stamp else None
                if dt is not None:
                    event_clock_used = True
                if dt is None:
                    dt = parse_time(item.get("received_at"))
                if dt is not None:
                    timed.append(dt)
            timed.sort()
            intervals = [(b - a).total_seconds() for a, b in zip(timed, timed[1:]) if b > a]
            median_interval = median(intervals) if intervals else None
            gap_threshold = max(30.0, median_interval * 3.0) if median_interval is not None else 60.0
            largest_gap = max(intervals) if intervals else None
            last_time = timed[-1] if timed else None
            since_last = max(0.0, (now - last_time).total_seconds()) if last_time else None
            likely_gap = bool(
                missing > 0 or
                (largest_gap is not None and largest_gap > gap_threshold) or
                (since_last is not None and since_last > gap_threshold)
            )
            results.append({
                "node_id": node_id, "boot_id": boot_id,
                "records_checked": len(items), "sequence_gap_count": len(gaps),
                "missing_sequence_estimate": missing,
                "largest_sequence_gap": max((g[2] for g in gaps), default=0),
                "median_interval_seconds": round(median_interval, 2) if median_interval is not None else None,
                "largest_timestamp_gap_seconds": round(largest_gap, 2) if largest_gap is not None else None,
                "seconds_since_latest_record": round(since_last, 2) if since_last is not None else None,
                "heuristic_gap_threshold_seconds": round(gap_threshold, 2),
                "event_time_used": event_clock_used,
                "heuristic_gap_detected": likely_gap,
                "note": "Heuristic only; sequence gaps may reflect resets/filtering and timestamp gaps do not prove packet loss.",
            })
        return {
            "scope_note": "Mine-node heuristic sync-gap checks only; worker sync metadata is omitted for privacy. Not a validated radio-loss detector.",
            "checked_at": utc_now(), "nodes": results,
        }
    finally:
        c.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    if args.reset and CORE_DB.exists():
        for suffix in ("", "-wal", "-shm"):
            Path(str(CORE_DB) + suffix).unlink(missing_ok=True)
        init_db()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")

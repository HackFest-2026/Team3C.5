"""LoRa gateway/JSONL -> local buffer -> rules/ML -> FastAPI core.

The gateway must output one JSON object per line. This Python process does not
speak radio directly; collector_esp32.ino combines ESP-NOW and SX1278 receive.
"""
import argparse
import json
import sqlite3
from collections import defaultdict, deque
from pathlib import Path

import requests

from anomaly_model import AnomalyModels
from config import (ANOMALY_OFF_STREAK, ANOMALY_ON_STREAK, EDGE_DB,
                    FORECAST_LIMITS, FORECAST_MODEL_PATH, HARD_LIMITS,
                    MODEL_PATH, SERIAL_BAUD, SYNC_BATCH_SIZE)
from demo_rules import DemoBaselineRules
from forecasting import MineForecasters
from protocol import finite_float, normalize_message
from sensor_adapter import normalize_sensor_fields
from sensor_calibration import SensorCalibration
from sheets_sink import GoogleSheetsSink

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node_id TEXT NOT NULL,
  boot_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  node_type TEXT NOT NULL,
  received_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  synced INTEGER DEFAULT 0,
  UNIQUE(node_id, boot_id, seq)
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id TEXT UNIQUE NOT NULL,
  node_id TEXT NOT NULL,
  boot_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  node_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  synced INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sheets_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node_id TEXT NOT NULL,
  boot_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  node_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  synced INTEGER DEFAULT 0,
  UNIQUE(node_id, boot_id, seq)
);
"""


def open_db(path: Path, reset=False):
    if reset and path.exists():
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=10000")
    c.executescript(SCHEMA)
    c.commit()
    return c


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[edge] skip invalid JSONL line {line_no}: {exc}")



def iter_serial(port: str, baud: int):
    import time
    import uuid

    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is required; install requirements.txt") from exc

    session_id = f"collector-{uuid.uuid4().hex[:12]}"
    sequences = {}
    ser = serial.Serial(port, baudrate=baud, timeout=1)
    time.sleep(2)
    ser.reset_input_buffer()

    print(f"[edge] reading collector data from {port} at {baud} baud", flush=True)
    print(f"[edge] session={session_id}", flush=True)

    def next_sequence(node_id):
        sequences[node_id] = sequences.get(node_id, 0) + 1
        return sequences[node_id]

    try:
        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                raw = None

            if isinstance(raw, dict):
                packet_type = str(raw.get("type", "")).lower()

                if packet_type == "radio_metadata":
                    print(
                        f"[radio] RSSI={raw.get('rssi_dbm')} dBm "
                        f"SNR={raw.get('snr_db')} dB",
                        flush=True,
                    )
                    continue

                node_type = str(raw.get("node_type", raw.get("type", ""))).lower()
                if node_type not in {"worker", "mine"}:
                    print(f"[edge] skipped unsupported JSON packet: {line[:100]}", flush=True)
                    continue

                node_id = str(raw.get("node_id", "")).strip()
                if not node_id:
                    print("[edge] rejected JSON packet: missing node_id", flush=True)
                    continue

                raw["node_type"] = node_type
                raw["seq"] = next_sequence(node_id)
                raw["boot_id"] = session_id
                raw["source_kind"] = "collector_serial"

                if node_type == "worker" and any(
                    key in raw for key in (
                        "heart_rate_simulated", "ir_simulated", "red_simulated"
                    )
                ):
                    raw["simulated_fields"] = [
                        key for key in (
                            "heart_rate_simulated", "ir_simulated", "red_simulated"
                        ) if key in raw
                    ]

                yield raw
                continue

            fields = {}
            try:
                for item in line.split(","):
                    key, value = item.split(":", 1)
                    fields[key.strip().upper()] = float(value.strip())

                required = {"MQ5", "MQ7", "TEMP", "HUM"}
                if not required.issubset(fields):
                    raise ValueError("missing required mine sensor fields")

                values = [fields[key] for key in required]
                if not all(__import__("math").isfinite(value) for value in values):
                    raise ValueError("non-finite sensor value")

            except (ValueError, TypeError):
                print(f"[edge] rejected malformed serial line: {line[:100]}", flush=True)
                continue

            node_id = "MINE_01"
            yield {
                "node_type": "mine",
                "node_id": node_id,
                "seq": next_sequence(node_id),
                "boot_id": session_id,
                "zone_id": "unassigned",
                "source_kind": "collector_serial",
                "mq5_raw": fields["MQ5"],
                "mq7_raw": fields["MQ7"],
                "ambient_temp_c": fields["TEMP"],
                "humidity_pct": fields["HUM"],
            }

    finally:
        ser.close()



def make_alert(msg: dict, signal: str, severity: str, status: str,
               description: str, details: dict | None = None):
    return {
        "alert_id": f"{msg['node_id']}:{msg['boot_id']}:{msg['seq']}:{signal}:{status}",
        "node_id": msg["node_id"], "boot_id": msg["boot_id"],
        "node_type": msg["node_type"], "seq": msg["seq"],
        "zone_id": msg.get("zone_id", "unassigned"),
        "created_at": msg["received_at"], "signal": signal,
        "severity": severity, "status": status,
        "description": description, "details": details or {},
    }


def transition(conn, states, msg, key, on, severity, description, details=None, required_on=1):
    state_key = (msg["node_id"], msg["boot_id"], key)
    state = states.setdefault(state_key, {"active": False, "on": 0, "off": 0})
    new_alert = None
    if on:
        state["on"] += 1
        state["off"] = 0
        if not state["active"] and state["on"] >= required_on:
            state["active"] = True
            new_alert = make_alert(msg, key, severity, "raised", description, details)
    else:
        state["on"] = 0
        if state["active"]:
            state["off"] += 1
            if state["off"] >= ANOMALY_OFF_STREAK:
                state["active"] = False
                state["off"] = 0
                new_alert = make_alert(msg, key, severity, "cleared", description, details)
    if new_alert:
        conn.execute(
            "INSERT OR IGNORE INTO alerts(alert_id,node_id,boot_id,seq,node_type,created_at,payload_json) VALUES(?,?,?,?,?,?,?)",
            (new_alert["alert_id"], new_alert["node_id"], new_alert["boot_id"],
             new_alert["seq"], new_alert["node_type"], new_alert["created_at"],
             json.dumps(new_alert, separators=(",", ":"))),
        )
        conn.commit()
        demo_label = "DEMO ONLY " if new_alert["signal"].startswith("demo_baseline:") else ""
        print(f"[{new_alert['severity'].upper()}] {new_alert['status']} {demo_label}{new_alert['signal']} "
              f"node={new_alert['node_id']} seq={new_alert['seq']}", flush=True)


def process_message(conn, states, models, forecasters, mine_windows, demo_rules, raw, calibration=None, queue_sheets=False):
    msg = normalize_sensor_fields(normalize_message(raw))
    if calibration is not None:
        msg = calibration.apply(msg)
    msg["boot_id"] = str(msg.get("boot_id", "boot-unknown"))

    # Drop duplicate radio packets before they affect rolling windows or alert persistence.
    exists = conn.execute(
        "SELECT 1 FROM telemetry WHERE node_id=? AND boot_id=? AND seq=?",
        (msg["node_id"], msg["boot_id"], msg["seq"]),
    ).fetchone()
    if exists:
        return False

    forecasts = None
    if msg["node_type"] == "mine" and forecasters.bundle:
        key = (msg["node_id"], msg["boot_id"])
        mine_windows[key].append(dict(msg))
        forecasts = forecasters.predict(list(mine_windows[key]))
        if forecasts:
            msg["forecasts"] = forecasts

    score = models.score(msg)
    if score is not None:
        msg["ml"] = score

    demo_findings = demo_rules.evaluate(msg) if demo_rules is not None else []
    if demo_findings:
        msg["demo_baseline"] = demo_findings

    conn.execute(
        "INSERT OR IGNORE INTO telemetry(node_id,boot_id,seq,node_type,received_at,payload_json) VALUES(?,?,?,?,?,?)",
        (msg["node_id"], msg["boot_id"], msg["seq"], msg["node_type"], msg["received_at"],
         json.dumps(msg, separators=(",", ":"), allow_nan=False)),
    )
    # Only queue a record when an explicitly loaded reference profile applies
    # to at least one field present in this packet. Raw-only records stay local.
    if queue_sheets and calibration is not None and msg.get("calibration"):
        conn.execute(
            "INSERT OR IGNORE INTO sheets_queue(node_id,boot_id,seq,node_type,payload_json) VALUES(?,?,?,?,?)",
            (msg["node_id"], msg["boot_id"], msg["seq"], msg["node_type"],
             json.dumps(msg, separators=(",", ":"), allow_nan=False)),
        )
        conn.commit()

    # Only explicitly configured, calibrated/approved fields can trigger hard rules.
    for field, limit in HARD_LIMITS.items():
        value = finite_float(msg.get(field))
        if limit is None or value is None:
            continue
        transition(conn, states, msg, f"hard:{field}", value >= float(limit), "critical",
                   f"Configured limit check for {field}",
                   {"field": field, "value": value, "limit": float(limit)}, required_on=1)

    # Forecast thresholds are also disabled unless approved/calibrated values are supplied.
    if forecasts:
        for target, result in forecasts.items():
            limit = FORECAST_LIMITS.get(target)
            if limit is None:
                continue
            transition(conn, states, msg, f"forecast:{target}", result["fusion"] >= float(limit), "warning",
                       f"Forecast approaches configured limit for {target}",
                       {"target": target, **result, "limit": float(limit)}, required_on=ANOMALY_ON_STREAK)

    if score is not None:
        transition(conn, states, msg, "baseline_anomaly", score["is_anomaly"], "info",
                   "Unusual pattern versus reviewed clean baseline",
                   {"score": score["score"], "threshold": score["threshold"],
                    "model_version": score["model_version"]}, required_on=ANOMALY_ON_STREAK)

    for finding in demo_findings:
        field = finding["field"]
        transition(
            conn, states, msg, f"demo_baseline:{field}", finding["triggered"], "info",
            f"DEMO ONLY: {field} deviated from its startup baseline; not a calibrated safety or medical alarm",
            finding, required_on=ANOMALY_ON_STREAK,
        )
    return True


def flush_table(conn, core, table_name, endpoint, batch_size):
    rows = conn.execute(
        f"SELECT id,payload_json FROM {table_name} WHERE synced=0 ORDER BY id LIMIT ?",
        (batch_size,),
    ).fetchall()
    if not rows:
        return 0
    payload = [json.loads(r["payload_json"]) for r in rows]
    response = requests.post(core + endpoint, json=payload, timeout=5)
    response.raise_for_status()
    ids = [r["id"] for r in rows]
    marks = ",".join("?" for _ in ids)
    conn.execute(f"UPDATE {table_name} SET synced=1 WHERE id IN ({marks})", ids)
    conn.commit()
    return len(rows)


def flush_pending(conn, core, batch_size=SYNC_BATCH_SIZE):
    try:
        total = 0
        while True:
            sent = flush_table(conn, core, "telemetry", "/ingest/telemetry", batch_size)
            sent += flush_table(conn, core, "alerts", "/ingest/alerts", batch_size)
            total += sent
            if sent == 0:
                break
        if total:
            print(f"[sync] sent {total} records to core", flush=True)
        return total
    except requests.RequestException as exc:
        print(f"[sync] core offline; records remain buffered: {exc}", flush=True)
        return 0


def flush_sheets_pending(conn, sink, batch_size=100):
    """Append calibrated records; leave local rows queued if Sheets is offline."""
    if sink is None:
        return 0
    total = 0
    try:
        while True:
            rows = conn.execute(
                "SELECT id,payload_json FROM sheets_queue WHERE synced=0 ORDER BY id LIMIT ?",
                (batch_size,),
            ).fetchall()
            if not rows:
                break
            payload = [json.loads(row["payload_json"]) for row in rows]
            sink.append_records(payload)
            ids = [row["id"] for row in rows]
            marks = ",".join("?" for _ in ids)
            conn.execute(f"UPDATE sheets_queue SET synced=1 WHERE id IN ({marks})", ids)
            conn.commit()
            total += len(rows)
        if total:
            print(f"[sheets] appended {total} calibrated record(s)", flush=True)
        return total
    except Exception as exc:
        print(f"[sheets] append failed; records remain in local queue: {exc}", flush=True)
        return 0


def main():
    ap = argparse.ArgumentParser()
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="USB serial port of the LoRa gateway, e.g. COM4")
    source.add_argument("--jsonl", type=Path, help="replay newline-delimited JSON packets")
    ap.add_argument("--baud", type=int, default=SERIAL_BAUD)
    ap.add_argument("--core", default="http://127.0.0.1:8000")
    ap.add_argument("--db", type=Path, default=EDGE_DB)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--sync-every", type=int, default=10)
    ap.add_argument("--demo-mode", action="store_true",
                    help="opt in to clearly labeled, demo-only startup-baseline alerts")
    ap.add_argument("--calibration-table", type=Path, default=None,
                    help="optional per-sensor reference CSV; uses software-only interpolation")
    ap.add_argument("--sheets", action="store_true",
                    help="append calibrated records to Google Sheets using service-account environment settings")
    args = ap.parse_args()
    if args.demo_mode and args.port:
        ap.error("--demo-mode is restricted to --jsonl software replay; do not enable it on live hardware")
    if args.sheets and not args.calibration_table:
        ap.error("--sheets requires a populated --calibration-table; raw-only telemetry is not uploaded")

    calibration = None
    if args.calibration_table:
        try:
            calibration = SensorCalibration.from_csv(args.calibration_table)
        except (OSError, ValueError) as exc:
            ap.error(f"invalid sensor calibration table: {exc}")
        print(f"[edge] software sensor calibration enabled from {args.calibration_table}; source fields are retained", flush=True)
    else:
        print("[edge] no reference table supplied; readings remain uncalibrated/raw or current sensor estimates", flush=True)

    sheets_sink = None
    if args.sheets:
        try:
            sheets_sink = GoogleSheetsSink.from_env()
        except (OSError, ValueError, RuntimeError) as exc:
            ap.error(f"Google Sheets configuration error: {exc}")
        print("[edge] Sheets append enabled; worker values go only to the configured private worker worksheet", flush=True)

    conn = open_db(args.db, args.reset)
    models = AnomalyModels(MODEL_PATH)
    forecasters = MineForecasters(FORECAST_MODEL_PATH)
    demo_rules = DemoBaselineRules() if args.demo_mode else None
    if args.demo_mode:
        print("[edge] DEMO MODE: baseline-relative alerts are illustrative only, not validated safety/medical alarms", flush=True)
    if not models.bundle and not forecasters.bundle:
        print("[edge] no trained hardware models found; model alerts disabled until clean/calibrated data is trained", flush=True)
    states = {}
    mine_windows = defaultdict(lambda: deque(maxlen=2000))
    source_iter = iter_jsonl(args.jsonl) if args.jsonl else iter_serial(args.port, args.baud)
    count = 0
    try:
        for raw in source_iter:
            try:
                if process_message(conn, states, models, forecasters, mine_windows, demo_rules, raw, calibration, queue_sheets=sheets_sink is not None):
                    count += 1
                if count and count % max(1, args.sync_every) == 0:
                    flush_pending(conn, args.core)
                    flush_sheets_pending(conn, sheets_sink)
            except (ValueError, TypeError, KeyError) as exc:
                print(f"[edge] packet rejected: {exc}", flush=True)
        flush_pending(conn, args.core)
        flush_sheets_pending(conn, sheets_sink)
    except KeyboardInterrupt:
        print("[edge] stopping; attempting final sync", flush=True)
        flush_pending(conn, args.core)
        flush_sheets_pending(conn, sheets_sink)
    finally:
        pending = conn.execute("SELECT COUNT(*) FROM telemetry WHERE synced=0").fetchone()[0]
        pending += conn.execute("SELECT COUNT(*) FROM alerts WHERE synced=0").fetchone()[0]
        sheets_pending = conn.execute("SELECT COUNT(*) FROM sheets_queue WHERE synced=0").fetchone()[0]
        print(f"[edge] total new packets={count}; unsynced core records={pending}; unsynced Sheets rows={sheets_pending}", flush=True)
        conn.close()


if __name__ == "__main__":
    main()

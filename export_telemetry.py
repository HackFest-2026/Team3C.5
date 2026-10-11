"""Flatten locally buffered telemetry JSON to CSV for review/labeling.
Exported data is not automatically clean or calibration-quality; review it first.
"""
import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=Path("edge_buffer.db"))
    ap.add_argument("--out", type=Path, default=Path("telemetry_export.csv"))
    ap.add_argument("--node-type", choices=["worker", "mine", "all"], default="all")
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"database not found: {args.db}")
    con = sqlite3.connect(args.db)
    rows = con.execute("SELECT received_at,payload_json FROM telemetry ORDER BY id").fetchall()
    con.close()
    flattened = []
    for received_at, raw in rows:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if args.node_type != "all" and payload.get("node_type") != args.node_type:
            continue
        payload["received_at"] = received_at
        flattened.append(payload)
    if not flattened:
        raise SystemExit("no matching telemetry rows")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(flattened).to_csv(args.out, index=False)
    print(f"wrote {len(flattened)} rows to {args.out}; review/label and split clean train/calibration data before training")


if __name__ == "__main__":
    main()

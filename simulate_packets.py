"""Create fictional JSONL packets for a software-only end-to-end demo.
Not sensor calibration data and not suitable for model training.
"""
import argparse
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("demo_packets.jsonl"))
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--demo-shift", action="store_true",
                    help="add fictional baseline-relative shifts for testing --demo-mode alerts")
    args = ap.parse_args()
    random.seed(7)
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=max(0, args.count - 1) * 5)
    run_id = uuid.uuid4().hex[:8]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    seq_w = seq_m = 0
    with args.out.open("w", encoding="utf-8") as f:
        f.write("# FICTIONAL SOFTWARE TEST DATA — not sensor calibration or health data\n")
        for i in range(args.count):
            event_time = (start + timedelta(seconds=i * 5)).isoformat().replace("+00:00", "Z")
            if i % 2 == 0:
                seq_w += 1
                hr = 145.0 if args.demo_shift and seq_w >= 8 else round(random.uniform(65, 105), 1)
                msg = {
                    "schema_version": 1, "node_type": "worker", "node_id": "wearable_01",
                    "boot_id": f"demo-w1-{run_id}", "worker_id": "worker_demo", "zone_id": "zone_01",
                    "seq": seq_w, "uptime_ms": i * 5000, "event_time": event_time,
                  "heart_rate_bpm": hr,
"spo2_pct": round(random.uniform(94, 99), 1),
"ntc_skin_temp_c": round(random.uniform(31, 35), 2),
"accel_g": round(random.uniform(0.95, 1.15), 3),
"ppg_quality": "good",
"work_status": "good_to_work",
                }
            else:
                seq_m += 1
                mq5 = 1800 if args.demo_shift and seq_m >= 8 else random.randint(800, 1250)
                msg = {
                    "schema_version": 1, "node_type": "mine", "node_id": "mine_node_01",
                    "boot_id": f"demo-m1-{run_id}", "zone_id": "zone_01", "seq": seq_m,
                    "uptime_ms": i * 5000, "event_time": event_time,
                    "mq5_raw": mq5, "mq7_raw": random.randint(650, 1100),
                    "ambient_temp_c": round(random.uniform(25, 31), 1),
                    "humidity_pct": round(random.uniform(40, 75), 1),
                    "gp2y_raw_adc": random.randint(100, 900),
                }
            f.write(json.dumps(msg, separators=(",", ":")) + "\n")
    print(f"Wrote {args.count} fictional packets to {args.out}")


if __name__ == "__main__":
    main()

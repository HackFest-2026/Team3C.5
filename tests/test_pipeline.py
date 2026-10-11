"""Hardware-independent smoke tests for replay, rules, and the local API."""
import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

import config

_TEST_DIR = tempfile.TemporaryDirectory(prefix="mineguard-tests-")
config.CORE_DB = Path(_TEST_DIR.name) / "core.db"

import core_service  # noqa: E402  (CORE_DB is redirected before module initialization)
from anomaly_model import AnomalyModels  # noqa: E402
from demo_rules import DemoBaselineRules  # noqa: E402
from edge_service import main as edge_main, flush_sheets_pending, open_db, process_message  # noqa: E402
from forecasting import MineForecasters, make_features  # noqa: E402
from sensor_calibration import SensorCalibration  # noqa: E402
from protocol import normalize_message  # noqa: E402
from train_forecasters import choose_time_column  # noqa: E402
from twin_view import render_twin_svg  # noqa: E402


class PipelineTests(unittest.TestCase):
    def test_normalizer_preserves_jsonl_event_time(self):
        event_time = "2026-01-01T12:30:00Z"
        normalized = normalize_message({
            "node_id": "mine-1", "node_type": "mine", "seq": 1,
            "event_time": event_time,
        })
        self.assertEqual(normalized["event_time"], event_time)
        self.assertIn("received_at", normalized)

    def test_forecaster_training_prefers_event_time(self):
        self.assertEqual(choose_time_column(["received_at", "event_time"]), "event_time")
        self.assertEqual(choose_time_column(["received_at"]), "received_at")
        self.assertEqual(choose_time_column(["event_time", "received_at"], "received_at"), "received_at")

    def test_demo_rules_are_opt_in_and_labeled(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            db = open_db(td / "edge.db", reset=True)
            models = AnomalyModels(td / "no-model.joblib")
            forecasters = MineForecasters(td / "no-forecaster.joblib")
            rules = DemoBaselineRules()
            states = {}
            windows = defaultdict(lambda: deque(maxlen=2000))
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            with contextlib.redirect_stdout(io.StringIO()):
                for seq in range(1, 16):
                    for domain in ("worker", "mine"):
                        worker = domain == "worker"
                        raw = {
                            "node_type": domain,
                            "node_id": "worker-1" if worker else "mine-1",
                            "boot_id": "test-boot", "seq": seq, "zone_id": "z1",
                            "event_time": (start + timedelta(seconds=seq * 5)).isoformat().replace("+00:00", "Z"),
                        }
                        if worker:
                            raw.update(heart_rate_bpm=80 if seq < 8 else 145, accel_g=1.0)
                        else:
                            raw.update(mq5_raw=1000 if seq < 8 else 1800, mq7_raw=800)
                        self.assertTrue(process_message(db, states, models, forecasters, windows, rules, raw))
            alerts = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM alerts")]
            signals = {alert["signal"] for alert in alerts}
            self.assertIn("demo_baseline:heart_rate_bpm", signals)
            self.assertIn("demo_baseline:mq5_raw", signals)
            self.assertTrue(all("DEMO ONLY" in alert["description"] for alert in alerts))
            saved = json.loads(db.execute(
                "SELECT payload_json FROM telemetry WHERE node_type='worker' ORDER BY seq DESC LIMIT 1"
            ).fetchone()[0])
            self.assertIn("event_time", saved)
            self.assertTrue(saved["demo_baseline"][0]["demo_only"])
            db.close()

    def test_software_sensor_calibration_is_applied_before_storage(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            table = td / "references.csv"
            with table.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow([
                    "sensor", "source_field", "target", "output_field", "unit", "input_value", "reference_value"
                ])
                writer.writerows([
                    ["mq5", "mq5_raw", "synthetic test gas", "test_value", "test-unit", 100, 0],
                    ["mq5", "mq5_raw", "synthetic test gas", "test_value", "test-unit", 200, 10],
                    ["mq5", "mq5_raw", "synthetic test gas", "test_value", "test-unit", 300, 20],
                ])
            calibration = SensorCalibration.from_csv(table)
            db = open_db(td / "edge.db", reset=True)
            raw = {
                "node_type": "mine", "node_id": "mine-test", "boot_id": "b1", "seq": 1,
                "mq5_raw": 150, "mq7_raw": 820,
            }
            accepted = process_message(
                db, {}, AnomalyModels(td / "missing-model.joblib"),
                MineForecasters(td / "missing-forecaster.joblib"),
                defaultdict(lambda: deque(maxlen=10)), None, raw, calibration,
            )
            self.assertTrue(accepted)
            saved = json.loads(db.execute(
                "SELECT payload_json FROM telemetry WHERE node_id='mine-test'"
            ).fetchone()[0])
            self.assertEqual(saved["mq5_raw"], 150)
            self.assertEqual(saved["test_value"], 5)
            self.assertEqual(saved["calibration"]["test_value"]["status"], "calibrated")
            self.assertEqual(saved["calibration"]["test_value"]["unit"], "test-unit")
            db.close()

    def test_calibrated_telemetry_waits_in_sheets_queue_until_sink_acknowledges(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            table = td / "references.csv"
            with table.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow([
                    "sensor", "source_field", "target", "output_field", "unit", "input_value", "reference_value"
                ])
                writer.writerows([
                    ["mq5", "mq5_raw", "synthetic test gas", "mq5_test_value", "test-unit", 100, 0],
                    ["mq5", "mq5_raw", "synthetic test gas", "mq5_test_value", "test-unit", 900, 8],
                ])
            calibration = SensorCalibration.from_csv(table)
            db = open_db(td / "edge.db", reset=True)
            raw = {
                "node_type": "mine", "node_id": "mine-queue", "boot_id": "b1", "seq": 1,
                "mq5_raw": 500, "mq7_raw": 650,
            }
            accepted = process_message(
                db, {}, AnomalyModels(td / "no-model"),
                MineForecasters(td / "no-forecast"),
                defaultdict(lambda: deque(maxlen=10)), None, raw, calibration, queue_sheets=True,
            )
            self.assertTrue(accepted)

            class Sink:
                def __init__(self): self.records = []
                def append_records(self, records): self.records.extend(records)
            sink = Sink()
            self.assertEqual(flush_sheets_pending(db, sink), 1)
            self.assertEqual(sink.records[0]["mq5_raw"], 500)
            self.assertEqual(sink.records[0]["mq5_test_value"], 4)
            self.assertEqual(db.execute("SELECT synced FROM sheets_queue").fetchone()[0], 1)
            db.close()

    def test_uncalibrated_records_do_not_enter_sheets_queue(self):
        with tempfile.TemporaryDirectory() as td:
            db = open_db(Path(td) / "edge.db", reset=True)
            raw = {"node_type": "mine", "node_id": "mine-raw", "boot_id": "b1", "seq": 1, "mq5_raw": 500}
            process_message(
                db, {}, AnomalyModels(Path(td) / "no-model"),
                MineForecasters(Path(td) / "no-forecast"),
                defaultdict(lambda: deque(maxlen=10)), None, raw, queue_sheets=True,
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sheets_queue").fetchone()[0], 0)
            db.close()

    def test_demo_mode_rejects_live_serial(self):
        with patch.object(sys, "argv", ["edge_service.py", "--port", "COM1", "--demo-mode"]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    edge_main()

    def test_sheets_mode_requires_a_populated_calibration_table(self):
        with patch.object(sys, "argv", ["edge_service.py", "--jsonl", "unused.jsonl", "--sheets"]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    edge_main()

    def test_forecaster_prefers_replay_event_time(self):
        class DummyModel:
            def predict(self, frame):
                return np.array([0.02])

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        records = [{
            "event_time": (start + timedelta(seconds=30 * i)).isoformat(),
            "received_at": "2026-01-01T00:00:00+00:00",  # deliberately identical
            "methane_pct": 1.0 + i * 0.01,
        } for i in range(60)]
        forecaster = MineForecasters("missing-model.joblib")
        feature_names = list(make_features(pd.DataFrame({"methane_pct": [1.0]}), ["methane_pct"]).columns)
        forecaster.bundle = {
            "sample_seconds": 30, "horizon_steps": 30, "horizon_minutes": 15,
            "channels": ["methane_pct"],
            "models": {"methane_pct": {"model": DummyModel(), "feature_names": feature_names}},
        }
        self.assertIn("methane_pct", forecaster.predict(records))

    def test_core_closes_after_malformed_record_and_reports_gaps(self):
        for suffix in ("", "-wal", "-shm"):
            Path(str(core_service.CORE_DB) + suffix).unlink(missing_ok=True)
        core_service.init_db()
        client = TestClient(core_service.app)
        bad = client.post("/ingest/telemetry", json=[{"node_type": "mine", "seq": 1}])
        self.assertEqual(bad.status_code, 422)
        rows = [
            {"node_id": "mine-1", "node_type": "mine", "boot_id": "b1", "seq": 1,
             "event_time": "2026-01-01T00:00:00Z", "mq5_raw": 1000},
            {"node_id": "mine-1", "node_type": "mine", "boot_id": "b1", "seq": 3,
             "event_time": "2026-01-01T00:01:00Z", "mq5_raw": 1100},
        ]
        accepted = client.post("/ingest/telemetry", json=rows)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(client.get("/twin").json()["view_type"], "latest-telemetry snapshot")
        gap = client.get("/sync-gaps").json()["nodes"][0]
        self.assertEqual(gap["missing_sequence_estimate"], 1)
        stored = client.get("/telemetry").json()
        self.assertTrue(any(item["payload"].get("event_time") == "2026-01-01T00:00:00Z" for item in stored))

    def test_worker_privacy_redacts_core_storage_and_public_endpoints(self):
        for suffix in ("", "-wal", "-shm"):
            Path(str(core_service.CORE_DB) + suffix).unlink(missing_ok=True)
        core_service.init_db()
        client = TestClient(core_service.app)
        rows = [
            {
                "node_id": "private-worker-1", "node_type": "WORKER", "boot_id": "b1", "seq": 1,
                "received_at": "2026-10-10T00:00:00Z", "zone_id": "private-zone",
                "work_status": "good_to_work", "heart_rate_bpm": 145, "spo2_pct": 91.2,
                "ntc_skin_temp_c": 38.1, "accel_g": 2.3, "ppg_quality": "poor",
            },
            {
                "node_id": "private-worker-2", "node_type": "worker", "boot_id": "b1", "seq": 1,
                "received_at": "2026-10-10T00:00:00Z", "work_status": "unrecognized/free text",
                "spo2_pct": 88.0,
            },
            {
                "node_id": "mine-1", "node_type": "mine", "boot_id": "b1", "seq": 1,
                "received_at": "2026-10-10T00:00:00Z", "mq5_raw": 812,
            },
        ]
        self.assertEqual(client.post("/ingest/telemetry", json=rows).status_code, 200)

        latest = client.get("/latest").json()
        worker_items = [item for item in latest if item.get("node_type") == "worker"]
        self.assertEqual(len(worker_items), 2)
        self.assertTrue(all(set(item) == {"node_type", "payload"} for item in worker_items))
        self.assertEqual(
            {item["payload"]["work_status"] for item in worker_items},
            {"good_to_work", None},
        )
        serialized_latest = json.dumps(latest)
        for private_value in ("private-worker", "private-zone", "heart_rate_bpm", "spo2_pct", "ntc_skin_temp_c", "accel_g"):
            self.assertNotIn(private_value, serialized_latest)

        telemetry = client.get("/telemetry").json()
        self.assertTrue(all(item["node_type"] == "mine" for item in telemetry))
        twin = client.get("/twin").json()
        self.assertTrue(all(node["node_type"] == "mine" for node in twin["nodes"]))
        gaps = client.get("/sync-gaps").json()
        self.assertTrue(all(node["node_id"] == "mine-1" for node in gaps["nodes"]))

        worker_alert = {
            "alert_id": "private-worker-1:health", "node_id": "private-worker-1",
            "node_type": "worker", "seq": 1, "signal": "heart_rate_bpm",
            "description": "private worker health detail",
        }
        alert_response = client.post("/ingest/alerts", json=[worker_alert])
        self.assertEqual(alert_response.status_code, 200)
        self.assertEqual(alert_response.json()["new"], 0)
        self.assertEqual(client.get("/alerts").json(), [])

        db = core_service.conn()
        stored = json.loads(db.execute(
            "SELECT payload_json FROM telemetry WHERE node_id='private-worker-1'"
        ).fetchone()[0])
        worker_alert_rows = db.execute(
            "SELECT COUNT(*) FROM alerts WHERE node_type='worker'"
        ).fetchone()[0]
        db.close()
        self.assertEqual(stored, {"work_status": "good_to_work"})
        self.assertEqual(worker_alert_rows, 0)

    def test_twin_schematic_is_valid_svg_and_escapes_node_labels(self):
        svg = render_twin_svg([{
            "node_id": "<worker>&1", "node_type": "worker", "zone_id": "z1",
            "freshness": "recent",
        }])
        ET.fromstring(svg)
        self.assertIn("ESP-NOW", svg)
        self.assertIn("not surveyed geometry", svg)
        self.assertIn("&lt;worker&gt;&amp;1", svg)
        self.assertNotIn("<worker>", svg)

    def test_compact_json_wire_fixtures_stay_below_200_bytes(self):
        worker = {"v": 1, "t": "w", "id": "w1", "wid": "w1", "b": "deadbeef",
                  "q": 4294967295, "z": "z1", "up": 4294967295, "hr": 300,
                  "o2": 100, "nt": 99.99, "ag": 9.999, "mn": 9.999, "ac": 65535, "pq": 1}
        mine = {"v": 1, "t": "m", "id": "m1", "b": "deadbeef", "q": 4294967295,
                "z": "z1", "up": 4294967295, "g5": 1023, "g9": 1023, "tc": 99.9,
                "rh": 100, "ti": 1, "p25": 999.99, "p10": 999.99}
        lengths = [len(json.dumps(row, separators=(",", ":")).encode("utf-8")) for row in (worker, mine)]
        self.assertEqual(lengths, [160, 152])
        self.assertTrue(all(length <= 200 for length in lengths))


if __name__ == "__main__":
    unittest.main()

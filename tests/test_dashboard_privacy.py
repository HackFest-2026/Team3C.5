"""UI-level smoke test for worker-data minimization in the dashboard."""
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from streamlit.testing.v1 import AppTest


class MockResponse:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        return None

    def json(self):
        return self.value


class DashboardPrivacyTests(unittest.TestCase):
    def test_dashboard_shows_only_aggregate_approved_worker_status(self):
        stamp = "2026-10-10T00:00:00Z"
        worker_payload = {
            "work_status": "needs_attention", "heart_rate_bpm": 147,
            "spo2_pct": 89, "ntc_skin_temp_c": 39.1, "accel_g": 3.2,
            "ppg_quality": "poor", "zone_id": "private-zone",
        }
        worker = {
            "node_id": "private-worker-1", "boot_id": "b1", "seq": 9,
            "node_type": "worker", "received_at": stamp, "payload": worker_payload,
        }
        mine_payload = {
            "zone_id": "z1", "event_time": stamp, "mq5_raw": 150, "mq7_raw": 820, "gp2y_raw_adc": 350,
            "ambient_temp_c": 26.5, "humidity_pct": 51, "pm25_ug_m3": 7.2,
            "pm10_ug_m3": 11.5, "tilt_active": False,
        }
        mine = {
            "node_id": "mine-1", "boot_id": "b2", "seq": 1,
            "node_type": "mine", "received_at": stamp, "payload": mine_payload,
        }
        responses = {
            "/health": {"ok": True},
            "/latest": [worker, mine],
            "/twin": {"scope_note": "mine-node test snapshot", "nodes": [
                {"node_id": "private-worker-1", "node_type": "worker", "zone_id": "private-zone",
                 "seq": 9, "last_seen": stamp, "age_seconds": 1, "freshness": "recent",
                 "telemetry": worker_payload},
                {"node_id": "mine-1", "node_type": "mine", "zone_id": "z1", "seq": 1,
                 "last_seen": stamp, "age_seconds": 1, "freshness": "recent", "telemetry": mine_payload},
            ]},
            "/alerts": [{"node_id": "private-worker-1", "node_type": "worker",
                         "signal": "heart_rate_bpm", "details": {"value": 147}}],
            "/sync-gaps": {"scope_note": "test", "nodes": [
                {"node_id": "private-worker-1", "heuristic_gap_detected": True},
                {"node_id": "mine-1", "heuristic_gap_detected": False},
            ]},
            "/telemetry": [worker, mine],
        }

        def fake_get(url, **kwargs):
            path = url.split("8000")[-1].split("?")[0]
            return MockResponse(responses[path])

        with patch.object(requests, "get", side_effect=fake_get):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py")).run()

        self.assertEqual(len(app.exception), 0, app.exception)
        self.assertTrue(any(
            metric.label == "Needs attention" and str(metric.value) == "1"
            for metric in app.metric
        ))
        self.assertTrue(any(metric.label == "Not fit" for metric in app.metric))
        self.assertTrue(any(metric.label == "Not assessed" for metric in app.metric))

        visible = []
        for name in ("markdown", "caption", "info", "warning", "success", "subheader", "title"):
            visible.extend(str(element.value) for element in getattr(app, name, []))
        for metric in app.metric:
            visible.extend((str(metric.label), str(metric.value)))
        visible.extend(str(table.value) for table in app.dataframe)
        visible.extend(str(element.value) for element in app.json)
        visible_text = "\n".join(visible)
        self.assertNotIn("tilt_active", visible_text)  # Legacy sketch field is not part of the new requested model.
        for private_value in (
            "147", "89.00", "39.10", "3.20", "private-zone",
            "private-worker-1", "heart_rate_bpm",
        ):
            self.assertNotIn(private_value, visible_text)


if __name__ == "__main__":
    unittest.main()

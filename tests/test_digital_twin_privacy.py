"""UI-level worker privacy test for the separate Digital Twin application."""
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from streamlit.testing.v1 import AppTest


class MockResponse:
    def __init__(self, value): self.value = value
    def raise_for_status(self): return None
    def json(self): return self.value


class DigitalTwinPrivacyTests(unittest.TestCase):
    def test_twin_shows_mine_data_and_only_worker_status(self):
        stamp = "2026-10-10T00:00:00Z"
        worker_payload = {
            "work_status": "not_fit", "heart_rate_bpm": 155, "spo2_pct": 87,
            "ntc_skin_temp_c": 39.4, "accel_g": 3.8, "zone_id": "secret-zone",
        }
        mine_payload = {
            "mq5_raw": 100, "mq7_raw": 200, "ambient_temp_c": 28,
            "humidity_pct": 50, "gp2y_raw_adc": 400, "pm25_ug_m3": 1.2,
            "zone_id": "z1",
        }
        response_data = {
            "/twin": {"nodes": [
                {"node_id": "private-worker", "node_type": "worker", "zone_id": "secret-zone",
                 "freshness": "recent", "seq": 4, "telemetry": worker_payload},
                {"node_id": "mine-1", "node_type": "mine", "zone_id": "z1",
                 "freshness": "recent", "seq": 5, "telemetry": mine_payload},
            ], "scope_note": "test"},
            "/latest": [
                {"node_type": "worker", "payload": {"work_status": "not_fit", **worker_payload}},
                {"node_type": "mine", "node_id": "mine-1", "seq": 5,
                 "received_at": stamp, "payload": mine_payload},
            ],
        }
        def fake_get(url, **kwargs):
            return MockResponse(response_data[url.split("8000")[-1].split("?")[0]])

        with patch.object(requests, "get", side_effect=fake_get):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "digital_twin_app.py")).run()
        self.assertEqual(len(app.exception), 0, app.exception)
        self.assertTrue(any(m.label == "Not fit" and str(m.value) == "1" for m in app.metric))
        visible = []
        for name in ("markdown", "caption", "info", "warning", "success", "subheader", "title"):
            visible.extend(str(x.value) for x in getattr(app, name, []))
        for metric in app.metric:
            visible.extend((str(metric.label), str(metric.value)))
        visible.extend(str(table.value) for table in app.dataframe)
        visible.extend(str(item.value) for item in app.json)
        text = "\n".join(visible)
        for private in ("155", "87.00", "39.40", "3.80", "secret-zone", "private-worker", "heart_rate_bpm"):
            self.assertNotIn(private, text)
        self.assertTrue(any(metric.label == "MQ-7 raw ADC counts" for metric in app.metric))


if __name__ == "__main__":
    unittest.main()

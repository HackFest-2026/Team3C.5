"""Reference-table calibration and adapter tests using synthetic-only anchors."""
import csv
import tempfile
import unittest
from pathlib import Path

from sensor_adapter import normalize_sensor_fields
from sensor_calibration import SensorCalibration, SensorCalibrationError


class SensorCalibrationTests(unittest.TestCase):
    def write_table(self, path, rows):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow([
                "sensor", "source_field", "target", "output_field", "unit", "input_value", "reference_value"
            ])
            writer.writerows(rows)

    def test_interpolates_preserves_source_and_withholds_out_of_range(self):
        with tempfile.TemporaryDirectory() as td:
            table = Path(td) / "cal.csv"
            self.write_table(table, [
                ["mq5", "mq5_raw", "synthetic gas", "test_value", "test-unit", 100, 0],
                ["mq5", "mq5_raw", "synthetic gas", "test_value", "test-unit", 200, 10],
                ["mq5", "mq5_raw", "synthetic gas", "test_value", "test-unit", 300, 20],
            ])
            calibration = SensorCalibration.from_csv(table)
            in_range = calibration.apply({"node_type": "mine", "mq5_raw": 150})
            self.assertEqual(in_range["mq5_raw"], 150)
            self.assertEqual(in_range["test_value"], 5)
            self.assertEqual(in_range["calibration"]["test_value"]["status"], "calibrated")
            out_of_range = calibration.apply({"node_type": "mine", "mq5_raw": 350})
            self.assertIsNone(out_of_range["test_value"])
            self.assertEqual(out_of_range["calibration"]["test_value"]["status"], "out_of_range")

    def test_blank_or_single_anchor_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            table = Path(td) / "cal.csv"
            self.write_table(table, [])
            with self.assertRaises(SensorCalibrationError):
                SensorCalibration.from_csv(table)
            self.write_table(table, [["dht11", "ambient_temp_c", "reference thermometer", "temp_cal_c", "C", 20, 21]])
            with self.assertRaises(SensorCalibrationError):
                SensorCalibration.from_csv(table)

    def test_non_monotonic_reference_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            table = Path(td) / "cal.csv"
            self.write_table(table, [
                ["adxl345", "accel_x_g", "reference g", "accel_x_cal_g", "g", -1, -1],
                ["adxl345", "accel_x_g", "reference g", "accel_x_cal_g", "g", 0, 0],
                ["adxl345", "accel_x_g", "reference g", "accel_x_cal_g", "g", 1, -0.5],
            ])
            with self.assertRaises(SensorCalibrationError):
                SensorCalibration.from_csv(table)

    def test_legacy_wire_field_is_mapped_to_new_mq7_sensor_name(self):
        mapped = normalize_sensor_fields({"node_type": "mine", "mq5_raw": 250, "mq9_raw": 700})
        self.assertEqual(mapped["mq7_raw"], 700)
        self.assertNotIn("mq9_raw", mapped)
        self.assertIn("mq7_raw", mapped["sensor_field_aliases"])

    def test_profiles_do_not_create_missing_values_on_other_node_types(self):
        with tempfile.TemporaryDirectory() as td:
            table = Path(td) / "cal.csv"
            self.write_table(table, [
                ["mq5", "mq5_raw", "synthetic gas", "test_value", "test-unit", 100, 0],
                ["mq5", "mq5_raw", "synthetic gas", "test_value", "test-unit", 200, 10],
            ])
            result = SensorCalibration.from_csv(table).apply({
                "node_type": "worker", "heart_rate_bpm": 80, "work_status": "good_to_work",
            })
            self.assertNotIn("test_value", result)
            self.assertEqual(result["calibration"], {})

    def test_worker_status_and_source_values_are_not_modified_by_calibration(self):
        with tempfile.TemporaryDirectory() as td:
            table = Path(td) / "cal.csv"
            self.write_table(table, [
                ["max30102", "heart_rate_bpm", "reference monitor", "heart_rate_adj_bpm", "bpm", 60, 60],
                ["max30102", "heart_rate_bpm", "reference monitor", "heart_rate_adj_bpm", "bpm", 100, 100],
            ])
            calibrated = SensorCalibration.from_csv(table).apply({
                "node_type": "worker", "heart_rate_bpm": 80, "work_status": "needs_attention",
            })
            self.assertEqual(calibrated["heart_rate_bpm"], 80)
            self.assertEqual(calibrated["heart_rate_adj_bpm"], 80)
            self.assertEqual(calibrated["work_status"], "needs_attention")


if __name__ == "__main__":
    unittest.main()

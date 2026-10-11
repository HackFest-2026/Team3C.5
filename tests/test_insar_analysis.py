"""Tests for processed InSAR upload parsing; no synthetic hazard labels."""
import unittest

from insar_analysis import InSARInputError, load_insar_upload


class InSARAnalysisTests(unittest.TestCase):
    def test_processed_csv_is_validated_and_parsed(self):
        raw = b"longitude,latitude,displacement_mm,date,zone_id\n77.5,12.9,2.3,2026-01-01,z1\n77.6,13.0,-1.0,2026-01-02,z1\n"
        frame, values = load_insar_upload("processed.csv", raw)
        self.assertEqual(len(frame), 2)
        self.assertEqual(float(values.max()), 2.3)
        self.assertIn("date", frame.columns)

    def test_raw_or_malformed_inputs_are_rejected(self):
        with self.assertRaises(InSARInputError):
            load_insar_upload("scene.zip", b"not an InSAR product")
        with self.assertRaises(InSARInputError):
            load_insar_upload("wrong.csv", b"x,y\n1,2\n")


if __name__ == "__main__":
    unittest.main()

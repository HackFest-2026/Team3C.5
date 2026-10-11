"""Apply a user-reference calibration table to saved CSV or JSONL telemetry."""
import argparse
import csv
import json
from pathlib import Path

from sensor_calibration import SensorCalibration


def _read_jsonl(path):
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Line {line_no} must be a JSON object")
            yield row


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")


def calibrate_file(source: Path, destination: Path, table: Path):
    calibration = SensorCalibration.from_csv(table)
    suffix = source.suffix.lower()
    if suffix in {".jsonl", ".ndjson"}:
        _write_jsonl(destination, (calibration.apply(row) for row in _read_jsonl(source)))
    elif suffix == ".csv":
        with source.open("r", newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
            fields = list(rows[0].keys()) if rows else []
        calibrated = [calibration.apply(row) for row in rows]
        new_fields = sorted({key for row in calibrated for key in row} - set(fields))
        with destination.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields + new_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(calibrated)
    else:
        raise ValueError("input must be .csv, .jsonl, or .ndjson")
    return calibration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration-table", type=Path, required=True)
    args = parser.parse_args()
    calibration = calibrate_file(args.input, args.output, args.calibration_table)
    print(f"Applied {len(calibration.profiles)} user-supplied profile(s) to {args.input}")
    print("Raw/source fields were retained; out-of-range values are null; no extrapolation was used.")


if __name__ == "__main__":
    main()

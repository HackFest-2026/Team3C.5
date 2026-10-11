"""Reference-table calibration for the MineGuard sensor streams.

The module never invents coefficients. It interpolates only between user-entered
reference pairs, preserves source readings, and withholds values outside the
reference range. Human wearable outputs remain measurements/estimates, not
medical determinations.
"""
from dataclasses import dataclass
import csv
from pathlib import Path
import re

from protocol import finite_float

SUPPORTED_SENSORS = {
    "mq5", "mq7", "dht11", "gp2y1010au0f", "max30102", "adxl345", "ntc_thermistor"
}
REQUIRED_COLUMNS = {
    "sensor", "source_field", "target", "output_field", "unit", "input_value", "reference_value"
}
FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class SensorCalibrationError(ValueError):
    """Invalid or incomplete user-supplied calibration table."""


@dataclass(frozen=True)
class CalibrationProfile:
    sensor: str
    source_field: str
    target: str
    output_field: str
    unit: str
    points: tuple[tuple[float, float], ...]

    @property
    def input_range(self):
        return self.points[0][0], self.points[-1][0]

    def interpolate(self, value: float):
        low, high = self.input_range
        if value < low or value > high:
            return None
        if value == high:
            return self.points[-1][1]
        for (x0, y0), (x1, y1) in zip(self.points, self.points[1:]):
            if x0 <= value <= x1:
                fraction = (value - x0) / (x1 - x0)
                return y0 + fraction * (y1 - y0)
        return None


class SensorCalibration:
    def __init__(self, profiles: dict[tuple[str, str], CalibrationProfile]):
        if not profiles:
            raise SensorCalibrationError("no complete calibration profiles were loaded")
        self.profiles = dict(profiles)

    @classmethod
    def from_csv(cls, path: str | Path):
        path = Path(path)
        if not path.is_file():
            raise SensorCalibrationError(f"calibration table not found: {path}")
        grouped = {}
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
            if missing:
                raise SensorCalibrationError(
                    "calibration table missing columns: " + ", ".join(sorted(missing))
                )
            for line_no, row in enumerate(reader, start=2):
                if not any(str(value or "").strip() for value in row.values()):
                    continue
                sensor = str(row.get("sensor", "")).strip().lower()
                source = str(row.get("source_field", "")).strip()
                target = str(row.get("target", "")).strip()
                output = str(row.get("output_field", "")).strip()
                unit = str(row.get("unit", "")).strip()
                if sensor not in SUPPORTED_SENSORS:
                    raise SensorCalibrationError(
                        f"row {line_no}: unsupported sensor {sensor!r}; use one of {', '.join(sorted(SUPPORTED_SENSORS))}"
                    )
                if not source or not target or not unit or not FIELD_NAME.fullmatch(output):
                    raise SensorCalibrationError(
                        f"row {line_no}: source_field, target, unit, and a simple output_field are required"
                    )
                if output in {"node_id", "node_type", "work_status", "calibration"}:
                    raise SensorCalibrationError(f"row {line_no}: output_field {output!r} is reserved")
                x = finite_float(row.get("input_value"))
                y = finite_float(row.get("reference_value"))
                if x is None or y is None:
                    raise SensorCalibrationError(
                        f"row {line_no}: input_value and reference_value must be finite numbers"
                    )
                grouped.setdefault((sensor, source), []).append(
                    (line_no, target, output, unit, x, y)
                )

        profiles = {}
        used_outputs = set()
        for key, rows in grouped.items():
            settings = {(r[1], r[2], r[3]) for r in rows}
            if len(settings) != 1:
                raise SensorCalibrationError(
                    f"{key[0]} / {key[1]}: target, output_field, and unit must be consistent"
                )
            target, output, unit = next(iter(settings))
            if output in used_outputs:
                raise SensorCalibrationError(f"output_field {output!r} is assigned more than once")
            used_outputs.add(output)
            points = sorted((r[4], r[5]) for r in rows)
            inputs = [point[0] for point in points]
            if len(points) < 2:
                raise SensorCalibrationError(f"{key[0]} / {key[1]} needs at least two reference pairs")
            if len(set(inputs)) != len(inputs):
                raise SensorCalibrationError(f"{key[0]} / {key[1]} input values must be distinct")
            refs = [point[1] for point in points]
            deltas = [b - a for a, b in zip(refs, refs[1:])]
            if not (all(delta > 0 for delta in deltas) or all(delta < 0 for delta in deltas)):
                raise SensorCalibrationError(
                    f"{key[0]} / {key[1]}: reference values must be strictly monotonic against input values"
                )
            profiles[key] = CalibrationProfile(
                sensor=key[0], source_field=key[1], target=target,
                output_field=output, unit=unit, points=tuple(points),
            )
        if not profiles:
            raise SensorCalibrationError("the template is blank; enter measured reference pairs before enabling calibration")
        return cls(profiles)

    def apply(self, message: dict):
        """Copy a record, retain inputs, and add calibrated fields/status metadata."""
        result = dict(message)
        if str(result.get("node_type", "")).lower() not in {"mine", "worker"}:
            return result
        metadata = {}
        for (sensor, source), profile in self.profiles.items():
            # Do not attach irrelevant/missing-input profiles from other node types.
            if source not in result:
                continue
            input_value = finite_float(result.get(source))
            calibrated = profile.interpolate(input_value) if input_value is not None else None
            status = "calibrated" if calibrated is not None else (
                "out_of_range" if input_value is not None else "missing_input"
            )
            result[profile.output_field] = round(calibrated, 6) if calibrated is not None else None
            metadata[profile.output_field] = {
                "sensor": sensor,
                "source_field": source,
                "target": profile.target,
                "unit": profile.unit,
                "status": status,
                "method": "piecewise_linear_between_user_reference_points",
                "extrapolation": "disabled",
                "input_value": input_value,
                "input_range": list(profile.input_range),
            }
        result["calibration"] = metadata
        result["calibrated_fields"] = sorted(
            key for key, entry in metadata.items()
            if isinstance(entry, dict) and entry.get("status") == "calibrated"
        )
        return result

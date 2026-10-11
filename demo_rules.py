"""Controlled-demo-only startup-baseline deviation rules.

These rules demonstrate an alert path. They are not gas limits, worker-health
limits, calibrated hazard detectors, or suitable for operational decisions.
"""
from collections import defaultdict
from statistics import median

from config import DEMO_BASELINE_SAMPLES, DEMO_RELATIVE_THRESHOLDS
from protocol import finite_float


class DemoBaselineRules:
    def __init__(self):
        self.samples = defaultdict(list)
        self.baselines = {}

    def evaluate(self, msg: dict):
        domain = msg.get("node_type")
        limits = DEMO_RELATIVE_THRESHOLDS.get(domain, {})
        findings = []
        for field, threshold in limits.items():
            value = finite_float(msg.get(field))
            if value is None:
                continue
            key = (msg.get("node_id"), msg.get("boot_id"), field)
            if key not in self.baselines:
                values = self.samples[key]
                if len(values) < DEMO_BASELINE_SAMPLES:
                    values.append(value)
                if len(values) >= DEMO_BASELINE_SAMPLES:
                    self.baselines[key] = float(median(values))
                continue

            base = self.baselines[key]
            denom = max(abs(base), 1.0)
            deviation = abs(value - base) / denom
            findings.append({
                "field": field,
                "value": value,
                "baseline": base,
                "relative_deviation": deviation,
                "threshold": float(threshold),
                "triggered": deviation >= float(threshold),
                "demo_only": True,
            })
        return findings

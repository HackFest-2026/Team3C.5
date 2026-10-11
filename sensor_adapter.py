"""Normalize sensor field names from the current compact collector packets.

The project now identifies the second mine gas channel as MQ-7. The existing
collector firmware still expands compact key `g9` to `mq9_raw`; this adapter
maps that legacy name to `mq7_raw` for the newly specified hardware. Disable
that mapping if the attached module is actually MQ-9.
"""
import os

from config import MINEGUARD_MAP_LEGACY_MQ9_TO_MQ7


def normalize_sensor_fields(message: dict):
    result = dict(message)
    if str(result.get("node_type", "")).lower() == "mine":
        default = "true" if MINEGUARD_MAP_LEGACY_MQ9_TO_MQ7 else "false"
        map_mq7 = os.environ.get("MINEGUARD_MAP_LEGACY_MQ9_TO_MQ7", default).strip().lower()
        if map_mq7 in {"1", "true", "yes", "on"} and "mq7_raw" not in result and "mq9_raw" in result:
            result["mq7_raw"] = result.pop("mq9_raw")
            aliases = dict(result.get("sensor_field_aliases", {})) if isinstance(result.get("sensor_field_aliases"), dict) else {}
            aliases["mq7_raw"] = "mq9_raw (legacy compact wire field g9)"
            result["sensor_field_aliases"] = aliases

        aliases = {
            "gp2y_raw": "gp2y_raw_adc",
            "gp2y_adc": "gp2y_raw_adc",
            "dust_raw_adc": "gp2y_raw_adc",
            "ntc_raw": "ntc_raw_adc",
        }
        for old, canonical in aliases.items():
            if canonical not in result and old in result:
                result[canonical] = result.pop(old)
    return result

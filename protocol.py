"""Validation and normalization for gateway JSON packets."""
from datetime import datetime, timezone
import math
from typing import Any

VALID_NODE_TYPES = {"worker", "mine"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def finite_float(value: Any):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def normalize_message(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("packet must be a JSON object")

    msg = dict(raw)
    node_id = str(msg.get("node_id", "")).strip()
    node_type = str(msg.get("node_type", "")).strip().lower()
    if not node_id:
        raise ValueError("missing node_id")
    if node_type not in VALID_NODE_TYPES:
        raise ValueError("node_type must be 'worker' or 'mine'")

    try:
        seq = int(msg["seq"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("seq must be an integer")
    if seq < 0:
        raise ValueError("seq must be >= 0")

    msg["schema_version"] = int(msg.get("schema_version", 1))
    msg["node_id"] = node_id
    msg["node_type"] = node_type
    msg["seq"] = seq
    msg.setdefault("boot_id", "boot-unknown")
    msg.setdefault("received_at", utc_now())
    msg.setdefault("zone_id", "unassigned")
    return msg

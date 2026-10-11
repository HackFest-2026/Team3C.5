"""Downloadable audit trail from recorded MineGuard mine-node alerts."""
import csv
from io import StringIO
import json

AUDIT_COLUMNS = [
    "alert_id", "created_at", "node_id", "seq", "severity", "status",
    "signal", "description", "why", "recommended_action", "details_json",
]


def incident_audit_csv(alerts):
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=AUDIT_COLUMNS)
    writer.writeheader()
    for alert in alerts or []:
        if alert.get("node_type") == "worker":
            continue
        details = alert.get("details") if isinstance(alert.get("details"), dict) else {}
        writer.writerow({
            "alert_id": alert.get("alert_id", ""),
            "created_at": alert.get("created_at", ""),
            "node_id": alert.get("node_id", ""),
            "seq": alert.get("seq", ""),
            "severity": alert.get("severity", ""),
            "status": alert.get("status", ""),
            "signal": alert.get("signal", ""),
            "description": alert.get("description", ""),
            "why": details.get("why", details.get("reason", "")),
            "recommended_action": alert.get("recommended_action", details.get("recommended_action", "")),
            "details_json": json.dumps(details, ensure_ascii=False, separators=(",", ":")),
        })
    return stream.getvalue()

"""Optional append-only Google Sheets sink for reference-processed telemetry.

The edge service gates queueing on a populated calibration table with a profile
matching a field in the packet. Rows preserve raw sources and calibration status.
Credentials use a secret service-account file; worker readings use a separate
restricted worksheet.
"""
from collections import defaultdict
import json
import os
from urllib.parse import quote

import requests

HEADERS = [
    "record_key", "node_type", "node_id", "boot_id", "seq", "event_time",
    "received_at", "zone_id", "work_status", "payload_json", "calibration_json",
]
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


class GoogleSheetsSink:
    def __init__(self, spreadsheet_id, session, mine_range="mine_data!A:K",
                 worker_range="worker_private!A:K"):
        self.spreadsheet_id = spreadsheet_id
        self.session = session
        self.ranges = {"mine": mine_range, "worker": worker_range}

    @classmethod
    def from_env(cls):
        credentials_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")
        if not credentials_file or not spreadsheet_id:
            raise RuntimeError(
                "Set GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_SHEETS_SPREADSHEET_ID; do not put keys in source code"
            )
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2.service_account import Credentials
        except ImportError as exc:
            raise RuntimeError("Google Sheets output needs google-auth; install requirements.txt") from exc
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_file(credentials_file, scopes=scopes)
        session = AuthorizedSession(credentials)
        return cls(
            spreadsheet_id,
            session,
            mine_range=os.environ.get("GOOGLE_SHEETS_MINE_RANGE", "mine_data!A:K"),
            worker_range=os.environ.get("GOOGLE_SHEETS_WORKER_RANGE", "worker_private!A:K"),
        )

    def _values_url(self, sheet_range):
        return f"{SHEETS_API}/{self.spreadsheet_id}/values/{quote(sheet_range, safe='!:') }"

    def _append(self, sheet_range, rows):
        url = self._values_url(sheet_range) + ":append"
        response = self.session.post(
            url,
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": rows},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def _ensure_headers(self, sheet_range):
        response = self.session.get(self._values_url(sheet_range), timeout=20)
        response.raise_for_status()
        values = response.json().get("values", [])
        if not values:
            self._append(sheet_range, [HEADERS])
        elif values[0][:len(HEADERS)] != HEADERS:
            raise RuntimeError(
                f"The worksheet for {sheet_range!r} has different headers; create it with: {', '.join(HEADERS)}"
            )

    @staticmethod
    def _row(record):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
        node_id = str(record.get("node_id", payload.get("node_id", "unknown")))
        boot_id = str(record.get("boot_id", payload.get("boot_id", "boot-unknown")))
        seq = record.get("seq", payload.get("seq", ""))
        node_type = str(record.get("node_type", payload.get("node_type", ""))).lower()
        record_key = f"{node_id}:{boot_id}:{seq}"
        calibration = payload.get("calibration", {})
        return [
            record_key,
            node_type,
            node_id,
            boot_id,
            seq,
            payload.get("event_time", payload.get("timestamp_utc", "")),
            record.get("received_at", payload.get("received_at", "")),
            payload.get("zone_id", ""),
            payload.get("work_status", ""),
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
            json.dumps(calibration, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        ]

    def append_records(self, records):
        grouped = defaultdict(list)
        for record in records:
            node_type = str(record.get("node_type", "")).lower()
            if node_type in self.ranges:
                grouped[node_type].append(self._row(record))
        results = {}
        for node_type, rows in grouped.items():
            sheet_range = self.ranges[node_type]
            self._ensure_headers(sheet_range)
            results[node_type] = self._append(sheet_range, rows)
        return results

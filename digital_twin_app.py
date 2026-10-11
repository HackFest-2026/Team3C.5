"""Separate MineGuard Digital Twin application (run on its own URL)."""
import os

import pandas as pd
import requests
import streamlit as st
from insar_analysis import render_insar_upload
from twin_view import render_twin_svg
from work_status import STATUS_LABELS, display_work_status

CORE = os.environ.get("CORE_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="MineGuard | Digital Twin", page_icon="⛏️", layout="wide")
st.title("MineGuard digital-twin schematic")
st.caption("Mine-node view only · worker identities, health data, and locations are not shown")
st.warning(
    "Prototype visualization—not surveyed mine geometry, a physics model, a geotechnical assessment, "
    "or an evacuation route. InSAR outputs are descriptive only."
)


WORK_STATUS_LABELS = (
    STATUS_LABELS["good_to_work"], STATUS_LABELS["needs_attention"],
    STATUS_LABELS["not_fit"], "Not assessed",
)


def worker_status_counts(items):
    counts = {label: 0 for label in WORK_STATUS_LABELS}
    for item in items:
        if item.get("node_type") == "worker":
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            counts[display_work_status(payload.get("work_status"))] += 1
    return counts


def fetch(path):
    try:
        response = requests.get(CORE + path, timeout=4)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"Cannot reach Core API at {CORE}: {exc}")
        return None


twin = fetch("/twin")
telemetry = fetch("/latest") or []
st.subheader("Worker work readiness · aggregate status only")
worker_counts = worker_status_counts(telemetry)
status_columns = st.columns(4)
for column, label in zip(status_columns, WORK_STATUS_LABELS):
    column.metric(label, worker_counts[label])
st.caption("Only approved upstream work_status is shown; no individual IDs, health readings, zones, or status history are exposed.")
if twin is not None:
    # Defense in depth: only render mine nodes even if an older API returns worker rows.
    nodes = [item for item in twin.get("nodes", []) if item.get("node_type") == "mine"]
    recent = sum(node.get("freshness") == "recent" for node in nodes)
    stale = sum(node.get("freshness") == "stale" for node in nodes)
    unknown = sum(node.get("freshness") == "unknown" for node in nodes)
    a, b, c, d = st.columns(4)
    a.metric("Mine nodes", len(nodes))
    b.metric("Recent", recent)
    c.metric("Stale", stale)
    d.metric("Unknown freshness", unknown)
    st.html(render_twin_svg(nodes))
    st.caption(twin.get("scope_note", "Mine-node snapshot only."))

    mine_latest = {str(item.get("node_id")): item for item in telemetry if item.get("node_type") == "mine"}
    st.subheader("Labeled mine-node readings")
    if not nodes:
        st.info("No mine-node telemetry is available yet.")
    for node in nodes:
        node_id = str(node.get("node_id", "mine node"))
        item = mine_latest.get(node_id, {})
        payload = item.get("payload") or node.get("telemetry") or {}
        with st.container(border=True):
            st.markdown(f"**Mine node `{node_id}`** · assigned zone `{payload.get('zone_id', node.get('zone_id', 'unassigned'))}`")
            st.caption(f"Received {item.get('received_at', node.get('last_seen', '—'))} · sequence {item.get('seq', node.get('seq', '—'))}")
            fields = (
                ("mq5_raw", "MQ-5 raw ADC counts", ""),
                ("mq7_raw", "MQ-7 raw ADC counts", ""),
                ("ambient_temp_c", "DHT11 ambient temperature", " °C"),
                ("humidity_pct", "DHT11 relative humidity", "%"),
                ("gp2y_raw_adc", "GP2Y1010AU0F raw ADC", ""),
                ("pm25_ug_m3", "PM2.5 estimate · reference-calibrated", " µg/m³"),
            )
            metrics = st.columns(3)
            for index, (field, label, unit) in enumerate(fields):
                value = payload.get(field)
                if value is None:
                    display = "—"
                elif isinstance(value, bool):
                    display = "ON" if value else "OFF"
                else:
                    try:
                        display = f"{float(value):.2f}{unit}"
                    except (TypeError, ValueError):
                        display = f"{value}{unit}"
                metrics[index % 3].metric(label, display)
            calibration = payload.get("calibration", {})
            profiles = [(field, meta) for field, meta in calibration.items() if isinstance(meta, dict)] if isinstance(calibration, dict) else []
            if profiles:
                st.markdown("**User-reference calibration status**")
                st.dataframe(pd.DataFrame([
                    {"Sensor": meta.get("sensor"), "Channel": field,
                     "Target": meta.get("target"), "Unit": meta.get("unit"),
                     "Calibrated value": (f"{payload.get(field)} {meta.get('unit', '')}".strip()
                                           if meta.get("status") == "calibrated" and payload.get(field) is not None
                                           else "—"),
                     "Status": meta.get("status"),
                     "Extrapolation": meta.get("extrapolation", "disabled")}
                    for field, meta in profiles
                ]), hide_index=True)

st.divider()
render_insar_upload("twin_insar")

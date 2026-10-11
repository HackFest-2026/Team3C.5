"""MineGuard prototype dashboard for worker and mine-node telemetry."""
import os

import pandas as pd
import requests
import streamlit as st
from work_status import STATUS_LABELS, display_work_status
from insar_analysis import render_insar_upload
from reporting import incident_audit_csv

CORE = os.environ.get("CORE_URL", "http://127.0.0.1:8000")
WORK_STATUS_LABELS = (
    STATUS_LABELS["good_to_work"],
    STATUS_LABELS["needs_attention"],
    STATUS_LABELS["not_fit"],
    "Not assessed",
)

st.set_page_config(page_title="MineGuard | Operations view", page_icon="⛏️", layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 1.35rem; padding-bottom: 2rem; max-width: 1500px;}
  .mg-eyebrow {color:#2563eb; font-size:0.76rem; font-weight:800; letter-spacing:.13em; text-transform:uppercase; margin-bottom:.15rem;}
  .mg-subtitle {color:#64748b; font-size:1rem; margin-top:-.6rem; margin-bottom:1.0rem;}
  .mg-panel {background:linear-gradient(135deg,#f8fafc,#eff6ff); border:1px solid #dbeafe; border-radius:14px; padding:14px 18px; margin:6px 0 16px 0; color:#0f172a;}
  .mg-small {color:#64748b; font-size:.88rem;}
  div[data-testid="stMetric"] {background:#f8fafc; border:1px solid #e2e8f0; padding:12px 14px; border-radius:12px; color:#0f172a;}
  div[data-testid="stMetricLabel"] p {font-size:.82rem; color:#475569;}
  div[data-testid="stMetricValue"] {font-size:1.55rem; color:#0f172a;}
  .stTabs [data-baseweb="tab-list"] {gap:8px;}
  .stTabs [data-baseweb="tab"] {border-radius:9px 9px 0 0; padding:9px 14px;}
</style>
""", unsafe_allow_html=True)
st.markdown('<div class="mg-eyebrow">MineGuard · hardware prototype</div>', unsafe_allow_html=True)
st.title("Telemetry operations dashboard")
st.markdown(
    '<div class="mg-subtitle">Worker wearable + mine node · local edge/core view</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="mg-panel"><b>Prototype boundary:</b> this is not a mine-safety or medical decision system. '
    'Worker heart-rate, SpO₂, temperature, motion and identity details are not displayed. The dashboard shows only '
    'aggregate counts from an approved upstream <code>work_status</code> field; it does not infer fitness or detect falls. '
    'Missing or unrecognized status is shown as <b>Not assessed</b>. Raw MQ readings are ADC counts; calibrated values '
    'appear only when a user reference table is supplied.</div>',
    unsafe_allow_html=True,
)
st.caption(
    "Privacy boundary: the Core stores only allowlisted work status for new worker records and redacts worker details "
    "from read APIs. The edge buffer still receives raw telemetry; restrict host/database access and retention. "
    "This prototype does not provide encryption or user authentication."
)


def get(path, report_error=True, **params):
    try:
        response = requests.get(CORE + path, params=params, timeout=3)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        if report_error:
            st.error(f"Cannot reach the core API at {CORE}: {exc}")
        return None


def show_value(payload, key, suffix=""):
    value = payload.get(key)
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def worker_status(item):
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return display_work_status(payload.get("work_status", item.get("work_status")))


def render_worker_status_summary(workers):
    counts = {label: 0 for label in WORK_STATUS_LABELS}
    for item in workers:
        counts[worker_status(item)] += 1
    columns = st.columns(4)
    for column, label in zip(columns, WORK_STATUS_LABELS):
        column.metric(label, counts[label])
    st.caption(
        "Aggregate counts only. Status comes from the approved upstream work_status field; "
        "no sensor thresholds or fall classification are applied here. Missing/invalid values count as Not assessed."
    )
    if not workers:
        st.info("No worker status records are available.")


def render_live(latest):
    if not latest:
        st.info("The core is ready, but no telemetry has arrived yet.")
        st.markdown(
            "Start the collector and edge service, or replay `demo_packets.jsonl` for a fictional software-only view."
        )
        return

    workers = [item for item in latest if item.get("node_type") == "worker"]
    mines = [item for item in latest if item.get("node_type") == "mine"]
    left, right = st.columns(2)

    with left:
        st.subheader("Worker work readiness · aggregate status only")
        render_worker_status_summary(workers)

    with right:
        st.subheader("Mine-node telemetry")
        if not mines:
            st.caption("No mine-node packets received.")
        for item in mines:
            payload = item.get("payload", {})
            st.markdown(f"**{item.get('node_id', 'mine')}** · assigned zone `{payload.get('zone_id', 'unassigned')}`")
            st.caption(f"Received {item.get('received_at', '—')} · sequence {item.get('seq', '—')}")
            fields = (
                ("mq5_raw", "MQ-5 raw ADC counts", ""),
                ("mq7_raw", "MQ-7 raw ADC counts", ""),
                ("ambient_temp_c", "DHT11 ambient temperature", " °C"),
                ("humidity_pct", "DHT11 relative humidity", "%"),
                ("gp2y_raw_adc", "GP2Y1010AU0F raw ADC", ""),
                ("pm25_ug_m3", "PM2.5 estimate · reference-calibrated", " µg/m³"),
            )
            for offset in range(0, len(fields), 2):
                metric_columns = st.columns(2)
                for column, (key, label, suffix) in zip(metric_columns, fields[offset:offset + 2]):
                    column.metric(label, show_value(payload, key, suffix))
            calibration = payload.get("calibration", {})
            profiles = [(field, meta) for field, meta in calibration.items() if isinstance(meta, dict)] if isinstance(calibration, dict) else []
            if profiles:
                st.markdown("**Software sensor calibration · user reference table**")
                calcols = st.columns(min(2, len(profiles)))
                for index, (output_field, meta) in enumerate(profiles):
                    unit = meta.get("unit", "")
                    calibrated = meta.get("status") == "calibrated"
                    value_text = show_value(payload, output_field, f" {unit}" if unit else "") if calibrated else "—"
                    sensor_label = str(meta.get("sensor", "sensor")).upper()
                    label = f"{sensor_label} · {meta.get('target', 'reference')}"
                    calcols[index % len(calcols)].metric(label, value_text)
                    st.caption(f"{output_field}: {meta.get('status', 'unknown')} · out-of-range values withheld")
            forecasts = payload.get("forecasts", {})
            if forecasts:
                st.caption("Experimental forecast values for configured channels")
                fcols = st.columns(min(3, len(forecasts)))
                for j, (target, result) in enumerate(forecasts.items()):
                    fcols[j % len(fcols)].metric(f"{target} forecast", show_value(result, "fusion"))
            st.caption("Raw MQ values are ADC counts, not gas concentrations; see calibration status above when configured.")


def render_trends(records):
    worker_history_count = sum(record.get("node_type") == "worker" for record in records)
    records = [record for record in records if record.get("node_type") != "worker"]
    if worker_history_count:
        st.caption("Worker biometric, motion, identity, and status history is omitted; only current aggregate status counts appear in Live telemetry.")
    if not records:
        st.info("No mine-node history is available. Worker history is hidden for privacy.")
        return
    flattened = []
    for record in records:
        payload = record.get("payload") or {}
        stamp = (payload.get("event_time") or payload.get("timestamp_utc") or
                 payload.get("received_at") or record.get("received_at"))
        flattened.append({**payload, "node_id": record.get("node_id"),
                          "node_type": record.get("node_type"), "_time": stamp})
    frame = pd.DataFrame(flattened)
    frame["_time"] = pd.to_datetime(frame["_time"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["_time"])
    if frame.empty:
        st.info("Records do not have parseable event/receive timestamps.")
        return
    ids = sorted(str(value) for value in frame["node_id"].dropna().unique())
    if not ids:
        st.info("No node identifiers are available.")
        return
    selected_node = st.selectbox("Node", ids)
    subset = frame[frame["node_id"].astype(str) == selected_node].sort_values("_time")
    options = ["mq5_raw", "mq7_raw", "ambient_temp_c", "humidity_pct",
               "gp2y_raw_adc", "pm25_ug_m3"]
    for calibration in subset.get("calibration", pd.Series(dtype=object)):
        if isinstance(calibration, dict):
            options.extend(
                field for field, meta in calibration.items()
                if isinstance(meta, dict) and meta.get("sensor") and meta.get("unit")
            )
    options = tuple(dict.fromkeys(options))
    available = [name for name in options if name in subset and
                 pd.to_numeric(subset[name], errors="coerce").notna().any()]
    if not available:
        st.info("No numeric trend fields are present for this node.")
        return
    defaults = [name for name in available if name in ("mq5_raw", "mq7_raw", "pm25_ug_m3")]
    chosen = st.multiselect("Signals", available, default=defaults or available[:1])
    if chosen:
        chart = subset.set_index("_time")[chosen].apply(pd.to_numeric, errors="coerce")
        chart = chart[~chart.index.duplicated(keep="last")].sort_index()
        st.line_chart(chart)
    st.caption(
        "JSONL replay charts use preserved event_time. Live firmware currently provides uptime only, "
        "so live charts fall back to collector receive time. Raw MQ ADC is not ppm."
    )


def render_sync(data, worker_node_ids=()):
    if data is None:
        return
    st.caption(data.get("scope_note", "Mine-node heuristic sync-gap checks."))
    all_nodes = data.get("nodes", [])
    worker_node_ids = {str(node_id) for node_id in worker_node_ids}
    nodes = [node for node in all_nodes if str(node.get("node_id", "")) not in worker_node_ids]
    if len(nodes) != len(all_nodes):
        st.caption("Worker sync metadata is hidden for privacy.")
    if not nodes:
        st.info("No mine-node records are available to check; worker sync metadata is hidden.")
        return
    frame = pd.DataFrame(nodes)
    st.dataframe(frame, hide_index=True)
    if any(node.get("heuristic_gap_detected") for node in nodes):
        st.warning("A heuristic sequence/timestamp gap was observed. It does not prove packet loss.")
    else:
        st.success("No heuristic gap was found in the available mine-node records.")
    st.caption("This view checks stored sequence/timestamp continuity; it cannot see unsynced rows still buffered on the edge PC.")


def render_alerts(alerts, worker_node_ids=()):
    worker_node_ids = {str(node_id) for node_id in worker_node_ids}
    mine_alerts = [alert for alert in alerts if str(alert.get("node_id", "")) not in worker_node_ids and alert.get("node_type") != "worker"]
    if len(mine_alerts) != len(alerts):
        st.caption("Worker alerts and sensor details are hidden; approved aggregate work-status counts appear in Operations.")
    if not mine_alerts:
        st.info("No mine-node alerts are recorded. Operational limits remain disabled unless calibrated and approved.")
        return
    if any("DEMO ONLY" in str(alert.get("description", "")) for alert in mine_alerts):
        st.warning("DEMO-ONLY baseline alerts are illustrative software tests, not mine-safety or medical alarms.")
    severity_order = {"critical": 0, "high": 1, "warning": 2, "medium": 3, "info": 4, "low": 5}
    ordered = sorted(mine_alerts, key=lambda item: (severity_order.get(str(item.get("severity", "info")).lower(), 6), str(item.get("created_at", ""))))
    st.markdown("**Prioritized mine-node alerts**")
    for alert in ordered:
        details = alert.get("details") if isinstance(alert.get("details"), dict) else {}
        title = f"{str(alert.get('severity', 'info')).upper()} · {alert.get('signal', 'event')} · {alert.get('status', 'unknown')}"
        with st.expander(title):
            st.write(alert.get("description", "No explanation was attached by the upstream rule/model."))
            st.write("**Why it fired:**", details.get("why", details.get("reason", "No reason field supplied.")))
            action = alert.get("recommended_action", details.get("recommended_action"))
            st.write("**Approved mitigation action:**", action or "No action is configured; follow site procedures.")
            st.json(details)
    st.dataframe(pd.DataFrame(ordered), hide_index=True)


def render_compliance(alerts):
    mine_alerts = [alert for alert in alerts if alert.get("node_type") != "worker"]
    st.markdown("### Incident audit export")
    st.caption("Exports the recorded mine-node alert trail with timestamp, signal, explanation, and any explicitly supplied action.")
    st.download_button(
        "Download incident audit CSV",
        data=incident_audit_csv(mine_alerts),
        file_name="mineguard_incident_audit.csv",
        mime="text/csv",
    )
    st.info("ESG/emissions reporting is not generated: this sensor set does not provide fuel, energy, or emissions data. No values are imputed.")


health = get("/health", report_error=False)
if health is None:
    st.error(f"Core API is unavailable at {CORE}.")
    st.code("python core_service.py --port 8000\nstreamlit run dashboard.py")
    st.stop()

latest = get("/latest") or []
twin_data = get("/twin")
alert_data = get("/alerts", limit=100) or []
gap_data = get("/sync-gaps")
record_data = get("/telemetry", limit=2000) or []

# A demo boot ID makes simulator-only data visibly distinct from hardware data.
if any(str(item.get("boot_id", "")).startswith("demo-") for item in latest):
    st.info("Fictional simulator records are present. They are software-demo data, not hardware observations.")

nodes = [node for node in (twin_data or {}).get("nodes", []) if node.get("node_type") == "mine"]
recent_mine = sum(node.get("freshness") == "recent" for node in nodes)
stale_mine = sum(node.get("freshness") == "stale" for node in nodes)
unknown_mine = sum(node.get("freshness") == "unknown" for node in nodes)
summary = st.columns(4)
summary[0].metric("Mine nodes in snapshot", len(nodes))
summary[1].metric("Recent mine nodes", recent_mine)
summary[2].metric("Stale mine nodes", stale_mine)
summary[3].metric("Unknown mine-node freshness", unknown_mine)
worker_node_ids = {
    str(item.get("node_id")) for item in latest
    if item.get("node_type") == "worker" and item.get("node_id")
}

with st.sidebar:
    st.markdown("### Connection")
    st.success("Core API connected")
    st.caption(f"`{CORE}`")
    st.markdown("### Data path")
    st.caption("Wearable → ESP-NOW → collector")
    st.caption("Mega → LoRa 433.92 MHz → collector")
    st.caption("Collector → USB serial → edge/core")
    st.divider()
    st.caption("Prototype display only · not a certified alarm system")

tabs = st.tabs(["Operations", "Mine trends", "Sync checks", "Alerts", "Audit export", "InSAR analysis"])
with tabs[0]:
    render_live(latest)
with tabs[1]:
    render_trends(record_data)
with tabs[2]:
    render_sync(gap_data, worker_node_ids)
with tabs[3]:
    render_alerts(alert_data, worker_node_ids)
with tabs[4]:
    render_compliance(alert_data)
with tabs[5]:
    render_insar_upload("dashboard_insar")

with st.expander("Latest mine-node records"):
    mine_latest = [item for item in latest if item.get("node_type") == "mine"]
    if mine_latest:
        allowed_mine_fields = {
            "zone_id", "event_time", "timestamp_utc", "received_at",
            "mq5_raw", "mq7_raw", "ambient_temp_c", "humidity_pct",
            "gp2y_raw_adc", "pm25_ug_m3", "calibration", "calibrated_fields", "forecasts",
        }
        table_rows = []
        for item in mine_latest:
            payload = item.get("payload") or {}
            visible_payload = {key: value for key, value in payload.items() if key in allowed_mine_fields}
            calibration = payload.get("calibration") if isinstance(payload.get("calibration"), dict) else {}
            for output_field in calibration:
                if output_field in payload:
                    visible_payload[output_field] = payload[output_field]
            table_rows.append({
                "node_id": item.get("node_id"), "type": item.get("node_type"),
                "sequence": item.get("seq"), "received_at": item.get("received_at"),
                **visible_payload,
            })
        st.dataframe(pd.DataFrame(table_rows), hide_index=True)
    else:
        st.caption("No mine-node records are available. Worker records are intentionally suppressed.")

if st.button("Refresh dashboard"):
    st.rerun()

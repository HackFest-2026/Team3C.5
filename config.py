"""Configuration for the MineGuard sensor-to-core prototype."""
from pathlib import Path
import os

HERE = Path(__file__).resolve().parent
EDGE_DB = Path(os.environ.get("EDGE_DB_PATH", HERE / "edge_buffer.db"))
CORE_DB = Path(os.environ.get("CORE_DB_PATH", HERE / "core.db"))
MODEL_PATH = HERE / "models" / "hardware_anomaly.joblib"
FORECAST_MODEL_PATH = HERE / "models" / "mine_forecasters.joblib"

SERIAL_BAUD = 115200
SYNC_BATCH_SIZE = 100
ANOMALY_ON_STREAK = 3
ANOMALY_OFF_STREAK = 10

# Illustrative software replay only. Never use these relative shifts as safety,
# medical, or work-fitness thresholds.
DEMO_BASELINE_SAMPLES = 5
DEMO_RELATIVE_THRESHOLDS = {
    "worker": {"heart_rate_bpm": 0.30, "accel_g": 0.50},
    "mine": {"mq5_raw": 0.35, "mq7_raw": 0.35, "gp2y_raw_adc": 0.50},
}

# No operational limits are enabled. Configure only after reference calibration,
# independent validation, and qualified approval.
HARD_LIMITS: dict[str, float] = {}
FORECAST_LIMITS: dict[str, float] = {}

# Available outputs and optional channels. Missing channels are not synthesized.
DOMAIN_FEATURES = {
    "worker": ["heart_rate_bpm", "spo2_pct", "ntc_skin_temp_c", "accel_g"],
    "mine": [
        "mq5_raw", "mq7_raw", "ambient_temp_c", "humidity_pct",
        "gp2y_raw_adc", "pm25_ug_m3",
    ],
}

# Incoming raw/derived packets are normalized in the Python sensor adapter before
# calibration. The legacy collector sends the second MQ channel as mq9_raw/g9.
MINEGUARD_MAP_LEGACY_MQ9_TO_MQ7 = True

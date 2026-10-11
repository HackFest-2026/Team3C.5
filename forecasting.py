"""Optional HGB + trend forecast for calibrated mine channels.

Training and inference assume time-resampled rows at bundle['sample_seconds'].
Only train targets with a validated engineering unit (e.g. calibrated gas or
PM concentration). Raw MQ ADC values are not hazard concentrations.
"""
from pathlib import Path
import joblib
import numpy as np
import pandas as pd

LAGS = (1, 5, 10, 20)


def make_features(df: pd.DataFrame, channels: list[str]) -> pd.DataFrame:
    out = {}
    for channel in channels:
        if channel not in df:
            continue
        x = pd.to_numeric(df[channel], errors="coerce")
        out[f"{channel}_x"] = x
        for lag in LAGS:
            out[f"{channel}_d{lag}"] = x - x.shift(lag)
        out[f"{channel}_sd20"] = x.rolling(20, min_periods=2).std()
    return pd.DataFrame(out, index=df.index).replace([np.inf, -np.inf], np.nan).fillna(0.0)


class MineForecasters:
    def __init__(self, path):
        self.path = Path(path)
        self.bundle = joblib.load(self.path) if self.path.exists() else None

    def predict(self, records: list[dict]):
        if not self.bundle or not records:
            return None
        df = pd.DataFrame(records)
        time_cols = [c for c in ("event_time", "timestamp_utc", "received_at") if c in df]
        if not time_cols:
            return None
        # Replay event time is authoritative when supplied; live hardware packets
        # without a wall clock fall back to collector receive time.
        event_time = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
        for col in time_cols:
            parsed = pd.to_datetime(df[col], errors="coerce", utc=True)
            event_time = event_time.fillna(parsed)
        df["_model_time"] = event_time
        df = df.dropna(subset=["_model_time"]).sort_values("_model_time").set_index("_model_time")
        if df.empty:
            return None

        # Resample radio arrivals to the same time base used in training.
        seconds = int(self.bundle["sample_seconds"])
        numeric = [c for c in self.bundle["channels"] if c in df.columns]
        if not numeric:
            return None
        for col in numeric:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[numeric].resample(f"{seconds}s").median().ffill(limit=2)
        df = df.dropna(how="all")
        if len(df) < 25:
            return None

        h = int(self.bundle["horizon_steps"])
        forecasts = {}
        for target, item in self.bundle["models"].items():
            if target not in df.columns or pd.isna(df[target].iloc[-1]):
                continue
            F = make_features(df, self.bundle["channels"])
            X = F.iloc[[-1]].reindex(columns=item["feature_names"], fill_value=0.0)
            current = float(df[target].iloc[-1])
            delta = float(item["model"].predict(X)[0])
            learned = current + delta
            recent = df[target].dropna()
            trend = None
            if len(recent) >= 11:
                trend = current + ((float(recent.iloc[-1]) - float(recent.iloc[-11])) / 10.0) * h
            fused = max(learned, trend) if trend is not None else learned
            forecasts[target] = {
                "current": current,
                "learned": learned,
                "trend": trend,
                "fusion": fused,
                "horizon_minutes": float(self.bundle["horizon_minutes"]),
            }
        return forecasts or None

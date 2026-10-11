"""Train 15-minute HGB delta forecasters on calibrated mine time series.

Input CSV should contain node_id, received_at (UTC or parseable timestamp),
calibrated target columns, and optional explanatory channels. It must contain
real or explicitly labelled test data suitable for the intended sensor/site.
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from forecasting import make_features

DEFAULT_CHANNELS = [
    "methane_pct", "co_ppm", "pm25_ug_m3",
    "mq5_raw", "mq7_raw", "gp2y_raw_adc", "ambient_temp_c", "humidity_pct",
]
DEFAULT_TARGETS = ["methane_pct", "co_ppm", "pm25_ug_m3"]
TIME_COLUMN_PRIORITY = ("event_time", "timestamp_utc", "received_at")


def choose_time_column(columns, requested=None):
    available = set(columns)
    if requested:
        if requested not in available:
            raise ValueError(f"time column {requested!r} is not present")
        return requested
    for candidate in TIME_COLUMN_PRIORITY:
        if candidate in available:
            return candidate
    raise ValueError(f"CSV needs one of {', '.join(TIME_COLUMN_PRIORITY)}")


def resample_group(group, time_col, sample_seconds, channels):
    g = group.copy()
    g[time_col] = pd.to_datetime(g[time_col], errors="coerce", utc=True)
    g = g.dropna(subset=[time_col]).sort_values(time_col).set_index(time_col)
    available = [c for c in channels if c in g.columns]
    if not available:
        return pd.DataFrame()
    for c in available:
        g[c] = pd.to_numeric(g[c], errors="coerce")
    out = g[available].resample(f"{sample_seconds}s").median().interpolate(limit=2).ffill(limit=2)
    return out


def make_training_rows(series, channels, target, horizon_steps):
    F = make_features(series, channels)
    y = pd.to_numeric(series[target], errors="coerce").shift(-horizon_steps) - pd.to_numeric(series[target], errors="coerce")
    return F, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path, help="calibrated mine telemetry CSV")
    ap.add_argument("--out", type=Path, default=Path("models/mine_forecasters.joblib"))
    ap.add_argument("--time-column", default=None,
                    help="timestamp column; default priority is event_time, timestamp_utc, then received_at")
    ap.add_argument("--node-column", default="node_id")
    ap.add_argument("--sample-seconds", type=int, default=30)
    ap.add_argument("--horizon-minutes", type=int, default=15)
    ap.add_argument("--targets", nargs="+", default=DEFAULT_TARGETS)
    args = ap.parse_args()

    if args.sample_seconds <= 0 or args.horizon_minutes <= 0:
        raise SystemExit("sample-seconds and horizon-minutes must be positive")
    df = pd.read_csv(args.csv)
    if args.node_column not in df:
        raise SystemExit(f"CSV must contain node column {args.node_column!r}")
    try:
        time_column = choose_time_column(df.columns, args.time_column)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"resampling with timestamp column {time_column!r}")
    channels = [c for c in DEFAULT_CHANNELS if c in df.columns]
    targets = [t for t in args.targets if t in channels]
    if not targets:
        raise SystemExit("No target columns found. Provide calibrated methane_pct, co_ppm, pm25_ug_m3, or explicit --targets.")
    horizon_steps = max(1, round(args.horizon_minutes * 60 / args.sample_seconds))
    groups=[]
    group_cols=[args.node_column]
    if "boot_id" in df.columns:
        group_cols.append("boot_id")
    for _, group in df.groupby(group_cols, dropna=False):
        rs = resample_group(group, time_column, args.sample_seconds, channels)
        if len(rs) >= horizon_steps + 25:
            groups.append(rs)
    if not groups:
        raise SystemExit("No node stream is long enough after resampling; collect more data or reduce the configured horizon for testing.")

    models={}; metrics={}
    for target in targets:
        train_X=[]; train_y=[]; test_X=[]; test_y=[]; feature_names=None
        for series in groups:
            if target not in series or series[target].notna().sum() < horizon_steps + 25:
                continue
            F,y=make_training_rows(series,channels,target,horizon_steps)
            n=len(series); split=int(n*0.8)
            # Leave an H-step gap so training labels do not cross into the time holdout.
            tr_idx=np.arange(n) < max(0,split-horizon_steps)
            te_idx=np.arange(n) >= split
            tr_mask=tr_idx & y.notna().to_numpy()
            te_mask=te_idx & y.notna().to_numpy()
            if tr_mask.sum() and te_mask.sum():
                train_X.append(F.loc[tr_mask]); train_y.append(y.loc[tr_mask])
                test_X.append(F.loc[te_mask]); test_y.append(y.loc[te_mask])
                feature_names=list(F.columns)
        if not train_X or not test_X:
            print(f"skip {target}: not enough train/test windows")
            continue
        Xtr=pd.concat(train_X); ytr=pd.concat(train_y)
        Xte=pd.concat(test_X); yte=pd.concat(test_y)
        model=HistGradientBoostingRegressor(max_iter=200, learning_rate=0.06, max_leaf_nodes=31, random_state=42)
        model.fit(Xtr,ytr)
        mae=float(mean_absolute_error(yte,model.predict(Xte)))
        models[target]={"model":model,"feature_names":feature_names,"mae_delta_holdout":mae}
        metrics[target]={"holdout_mae_delta":mae,"train_rows":len(Xtr),"test_rows":len(Xte)}
        print(f"{target}: holdout MAE on future change={mae:.5f} ({len(Xtr)} train, {len(Xte)} test rows)")

    if not models:
        raise SystemExit("No forecasts were trained.")
    bundle={
        "version":1,"sample_seconds":args.sample_seconds,"horizon_minutes":args.horizon_minutes,
        "horizon_steps":horizon_steps,"channels":channels,"models":models,"metrics":metrics,
        "warning":"Forecasts are experimental. Only use calibrated targets and validate against separate events/sites.",
    }
    args.out.parent.mkdir(parents=True,exist_ok=True)
    joblib.dump(bundle,args.out)
    print(f"Saved {args.out}")


if __name__=="__main__":
    main()

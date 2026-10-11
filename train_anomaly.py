"""Fit separate Isolation Forest baselines from verified-clean hardware logs.

CSV rows need `node_type` and telemetry fields. Use distinct, reviewed clean
training and calibration periods. Output is anomaly scoring, not a safety limit.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from config import DOMAIN_FEATURES, MODEL_PATH


def numeric_frame(df, features):
    x = df.reindex(columns=features).copy()
    for col in features:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    return x.replace([np.inf, -np.inf], np.nan)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-csv", required=True, type=Path)
    ap.add_argument("--calibration-csv", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=MODEL_PATH)
    ap.add_argument("--quantile", type=float, default=0.99)
    ap.add_argument("--trees", type=int, default=200)
    args = ap.parse_args()
    if not 0.5 < args.quantile < 1.0:
        raise SystemExit("--quantile must be > 0.5 and < 1.0")

    train = pd.read_csv(args.train_csv)
    calibration = pd.read_csv(args.calibration_csv)
    for name, frame in [("train", train), ("calibration", calibration)]:
        if "node_type" not in frame:
            raise SystemExit(f"{name} CSV must contain node_type")

    bundle = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "models": {},
              "note": "Unsupervised baseline on user-reviewed clean data; not a safety limit."}
    for domain, candidates in DOMAIN_FEATURES.items():
        tr = train[train.node_type.astype(str).str.lower() == domain]
        ca = calibration[calibration.node_type.astype(str).str.lower() == domain]
        xtr_all = numeric_frame(tr, [c for c in candidates if c in tr.columns])
        # Ignore features that contain no usable numeric training values (PM may be TBD).
        features = [c for c in xtr_all.columns if xtr_all[c].notna().any()]
        if len(features) < 2:
            print(f"skip {domain}: fewer than two usable numeric features")
            continue
        if len(tr) < 30 or len(ca) < 20:
            print(f"skip {domain}: need >=30 train and >=20 calibration rows; got {len(tr)}/{len(ca)}")
            continue
        xtr = numeric_frame(tr, features)
        xcal = numeric_frame(ca, features)
        pipe = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("iforest", IsolationForest(n_estimators=args.trees, contamination="auto", random_state=42, n_jobs=-1)),
        ])
        pipe.fit(xtr)
        scores = -pipe.decision_function(xcal)
        threshold = float(np.quantile(scores, args.quantile))
        bundle["models"][domain] = {
            "features": features, "pipeline": pipe, "threshold": threshold,
            "calibration_quantile": args.quantile, "train_rows": int(len(tr)),
            "calibration_rows": int(len(ca)),
        }
        print(f"{domain}: {len(features)} features; train/cal={len(tr)}/{len(ca)}; threshold={threshold:.5f}")

    if not bundle["models"]:
        raise SystemExit("No model trained. Provide reviewed clean worker and/or mine records.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.out)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()

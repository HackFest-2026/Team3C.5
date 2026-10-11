"""Optional domain-specific anomaly scoring; not a safety-limit detector."""
from pathlib import Path
import joblib
import pandas as pd


class AnomalyModels:
    def __init__(self, path):
        self.path = Path(path)
        self.bundle = None
        if self.path.exists():
            self.bundle = joblib.load(self.path)

    def score(self, message: dict):
        if not self.bundle:
            return None
        domain = message.get("node_type")
        model_info = self.bundle.get("models", {}).get(domain)
        if not model_info:
            return None

        features = model_info["features"]
        row = {name: message.get(name) for name in features}
        X = pd.DataFrame([row], columns=features)
        pipeline = model_info["pipeline"]
        # IsolationForest decision_function: smaller values are more unusual.
        anomaly_score = float(-pipeline.decision_function(X)[0])
        threshold = float(model_info["threshold"])
        return {
            "score": anomaly_score,
            "threshold": threshold,
            "is_anomaly": anomaly_score >= threshold,
            "model_version": self.bundle.get("version", 1),
        }

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from washdetect.features import MODEL_FEATURE_COLUMNS, TRADE_FEATURE_COLUMNS, model_matrix

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"


class ModelNotTrained(NotImplementedError):
    pass


def _require_ml():
    try:
        import numpy  # noqa: F401
        import xgboost  # noqa: F401
        from sklearn.ensemble import IsolationForest  # noqa: F401
    except ImportError as exc:
        raise ModelNotTrained(
            "ML dependencies are not installed. From the repo root run: "
            'pip install -e ".[ml]"'
        ) from exc


def _logit(values):
    import numpy as np

    clipped = np.clip(np.asarray(values, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(clipped / (1 - clipped))


class PlattCalibrator:

    def __init__(self, coef: float = 1.0, intercept: float = 0.0, enabled: bool = False) -> None:
        self.coef = float(coef)
        self.intercept = float(intercept)
        self.enabled = bool(enabled)

    def transform(self, raw) -> list[float]:
        import numpy as np

        values = np.asarray(raw, dtype=float)
        if not self.enabled or values.size == 0:
            return np.clip(values, 0.0, 1.0)
        logged = _logit(values)
        calibrated = 1.0 / (1.0 + np.exp(-(self.coef * logged + self.intercept)))
        return np.clip(calibrated, 0.0, 1.0)

    def to_dict(self) -> dict[str, float | bool]:
        return {"coef": self.coef, "intercept": self.intercept, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, payload: dict) -> PlattCalibrator:
        return cls(
            coef=float(payload.get("coef", 1.0)),
            intercept=float(payload.get("intercept", 0.0)),
            enabled=bool(payload.get("enabled", False)),
        )


def fit_platt(raw, labels) -> PlattCalibrator:
    """A one-class slice cannot be calibrated, so the calibrator stays off."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    y = np.asarray(labels, dtype=int)
    x = np.asarray(raw, dtype=float)
    if len(y) < 8 or len(set(y.tolist())) < 2:
        return PlattCalibrator(enabled=False)
    model = LogisticRegression(C=1.0, solver="lbfgs")
    model.fit(_logit(x).reshape(-1, 1), y)
    return PlattCalibrator(coef=float(model.coef_[0, 0]), intercept=float(model.intercept_[0]), enabled=True)


class IsolationForestDetector:

    def __init__(self) -> None:
        self.model = None
        self.columns: list[str] = list(MODEL_FEATURE_COLUMNS)
        self.quantiles: list[float] = []

    def fit(self, features: pd.DataFrame, columns: Sequence[str] | None = None) -> None:
        _require_ml()
        from sklearn.ensemble import IsolationForest

        self.columns = list(columns or _infer_columns(features))
        matrix = model_matrix(features, self.columns)
        if matrix.empty:
            raise ValueError("Isolation Forest needs at least one row.")
        sample = min(256, len(matrix))
        self.model = IsolationForest(
            n_estimators=80,
            max_samples=sample,
            contamination="auto",
            random_state=0,
            n_jobs=1,
        )
        self.model.fit(matrix)
        raw = _anomaly_raw(self.model, matrix)
        import numpy as np

        self.quantiles = np.quantile(raw, np.linspace(0, 1, 101)).astype(float).tolist()

    def score(self, features: pd.DataFrame) -> pd.Series:
        if self.model is None or not self.quantiles:
            raise ModelNotTrained(
                "Isolation Forest is not trained. Run `washdetect train` or ship artifacts/."
            )
        import numpy as np

        matrix = model_matrix(features, self.columns)
        raw = _anomaly_raw(self.model, matrix)
        quantiles = np.asarray(self.quantiles, dtype=float)
        ranks = np.searchsorted(quantiles, raw, side="right") / 100.0
        return pd.Series(np.clip(ranks, 0.0, 1.0), index=features.index, name="anomaly_score")


def _anomaly_raw(model, matrix):
    # decision_function is higher for inliers. Negate so high means anomalous.
    return -model.decision_function(matrix)


class WashXGBoost:

    def __init__(self) -> None:
        self.model = None
        self.columns: list[str] = list(MODEL_FEATURE_COLUMNS)
        self.calibrator = PlattCalibrator()

    def fit(self, features: pd.DataFrame, weak_labels: Sequence[int], columns: Sequence[str] | None = None) -> None:
        _require_ml()
        import numpy as np
        from xgboost import XGBClassifier

        self.columns = list(columns or _infer_columns(features))
        matrix = model_matrix(features, self.columns)
        labels = np.asarray(list(weak_labels), dtype=int)
        if len(labels) != len(matrix):
            raise ValueError("weak_labels must have one entry per feature row.")
        if len(set(labels.tolist())) < 2:
            raise ValueError("XGBoost needs both wash and non-wash rows.")
        positives = max(1, int(labels.sum()))
        negatives = max(1, int(len(labels) - positives))
        self.model = XGBClassifier(
            n_estimators=80,
            max_depth=3,
            learning_rate=0.08,
            min_child_weight=2,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=1.5,
            objective="binary:logistic",
            eval_metric="aucpr",
            scale_pos_weight=negatives / positives,
            random_state=0,
            n_jobs=2,
            verbosity=0,
        )
        self.model.fit(matrix, labels)

    def raw_proba(self, features: pd.DataFrame):
        if self.model is None:
            raise ModelNotTrained(
                "XGBoost is not trained. Run `washdetect train` or ship artifacts/."
            )
        import numpy as np

        matrix = model_matrix(features, self.columns)
        probabilities = self.model.predict_proba(matrix)
        classes = list(self.model.classes_)
        if 1 not in classes:
            return np.zeros(len(matrix))
        return probabilities[:, classes.index(1)]

    def predict_proba(self, features: pd.DataFrame) -> pd.Series:
        calibrated = self.calibrator.transform(self.raw_proba(features))
        return pd.Series(calibrated, index=features.index, name="ml_score")

    def set_calibrator(self, calibrator: PlattCalibrator) -> None:
        self.calibrator = calibrator


class ShapExplainer:

    def explain(self, model: WashXGBoost, features: pd.DataFrame) -> pd.DataFrame:
        if model.model is None:
            raise ModelNotTrained("SHAP needs a trained XGBoost model.")
        _require_ml()
        import numpy as np
        import xgboost as xgb

        matrix = model_matrix(features, model.columns)
        contribs = model.model.get_booster().predict(
            xgb.DMatrix(matrix, feature_names=list(matrix.columns)),
            pred_contribs=True,
        )
        values = np.asarray(contribs)[:, : len(model.columns)]  # last column is the bias term
        return pd.DataFrame(values, index=features.index, columns=list(model.columns))

    def top_reasons(self, model: WashXGBoost, features: pd.DataFrame, k: int = 5) -> pd.DataFrame:
        wide = self.explain(model, features)
        matrix = model_matrix(features, model.columns)
        rows: list[dict[str, object]] = []
        for idx in wide.index:
            shap_row = wide.loc[idx]
            ordered = shap_row.abs().sort_values(ascending=False).head(max(1, k))
            wallet = ""
            if "wallet" in features.columns:
                wallet = str(features.loc[idx, "wallet"])
            for rank, feature in enumerate(ordered.index, start=1):
                shap_value = float(shap_row[feature])
                rows.append(
                    {
                        "wallet": wallet,
                        "rank": rank,
                        "feature": str(feature),
                        "feature_value": float(matrix.loc[idx, feature]),
                        "shap_value": shap_value,
                        "direction": "increases" if shap_value >= 0 else "decreases",
                    }
                )
        return pd.DataFrame(
            rows,
            columns=["wallet", "rank", "feature", "feature_value", "shap_value", "direction"],
        )


class ModelBundle:

    def __init__(self) -> None:
        self.wallet_xgb = WashXGBoost()
        self.trade_xgb = WashXGBoost()
        self.wallet_iforest = IsolationForestDetector()
        self.trade_iforest = IsolationForestDetector()
        self.manifest: dict = {}

    def save(self, directory: str | Path) -> Path:
        _require_ml()
        import joblib

        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        self.wallet_xgb.model.save_model(path / "wallet_xgb.json")
        self.trade_xgb.model.save_model(path / "trade_xgb.json")
        joblib.dump(
            {"model": self.wallet_iforest.model, "quantiles": self.wallet_iforest.quantiles, "columns": self.wallet_iforest.columns},
            path / "wallet_iforest.joblib",
        )
        joblib.dump(
            {"model": self.trade_iforest.model, "quantiles": self.trade_iforest.quantiles, "columns": self.trade_iforest.columns},
            path / "trade_iforest.joblib",
        )
        payload = {
            "wallet_columns": self.wallet_xgb.columns,
            "trade_columns": self.trade_xgb.columns,
            "wallet_calibrator": self.wallet_xgb.calibrator.to_dict(),
            "trade_calibrator": self.trade_xgb.calibrator.to_dict(),
            "manifest": self.manifest,
        }
        (path / "bundle.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, directory: str | Path | None = None) -> ModelBundle:
        _require_ml()
        import joblib
        from xgboost import XGBClassifier

        path = Path(directory) if directory is not None else default_model_dir()
        bundle_path = path / "bundle.json"
        if not bundle_path.is_file():
            raise ModelNotTrained(
                f"No trained model at {path}. Run `washdetect train` to create one."
            )
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle = cls()
        bundle.wallet_xgb.model = XGBClassifier()
        bundle.wallet_xgb.model.load_model(path / "wallet_xgb.json")
        bundle.trade_xgb.model = XGBClassifier()
        bundle.trade_xgb.model.load_model(path / "trade_xgb.json")
        bundle.wallet_xgb.columns = list(payload["wallet_columns"])
        bundle.trade_xgb.columns = list(payload["trade_columns"])
        bundle.wallet_xgb.calibrator = PlattCalibrator.from_dict(payload["wallet_calibrator"])
        bundle.trade_xgb.calibrator = PlattCalibrator.from_dict(payload["trade_calibrator"])
        wallet_forest = joblib.load(path / "wallet_iforest.joblib")
        trade_forest = joblib.load(path / "trade_iforest.joblib")
        bundle.wallet_iforest.model = wallet_forest["model"]
        bundle.wallet_iforest.quantiles = list(wallet_forest["quantiles"])
        bundle.wallet_iforest.columns = list(wallet_forest["columns"])
        bundle.trade_iforest.model = trade_forest["model"]
        bundle.trade_iforest.quantiles = list(trade_forest["quantiles"])
        bundle.trade_iforest.columns = list(trade_forest["columns"])
        bundle.manifest = dict(payload.get("manifest") or {})
        return bundle


def default_model_dir() -> Path:
    import os

    override = os.environ.get("WASHDETECT_MODEL_DIR")
    if override:
        return Path(override)
    return ARTIFACT_DIR


class WashModel:
    """Platt scaling uses a held-out token. SHAP stays on the raw margin; calibration is monotone."""

    def __init__(self, bundle: ModelBundle | None = None) -> None:
        self.bundle = bundle or ModelBundle()

    def train(
        self,
        wallet_fit: pd.DataFrame,
        trade_fit: pd.DataFrame,
        wallet_normal: pd.DataFrame,
        trade_normal: pd.DataFrame,
        wallet_calib: pd.DataFrame | None = None,
        trade_calib: pd.DataFrame | None = None,
    ) -> None:
        self.bundle.wallet_xgb.fit(wallet_fit, wallet_fit["label"].tolist(), columns=MODEL_FEATURE_COLUMNS)
        self.bundle.trade_xgb.fit(trade_fit, trade_fit["label"].tolist(), columns=TRADE_FEATURE_COLUMNS)
        if wallet_calib is not None and not wallet_calib.empty:
            self.bundle.wallet_xgb.set_calibrator(
                fit_platt(self.bundle.wallet_xgb.raw_proba(wallet_calib), wallet_calib["label"].tolist())
            )
        if trade_calib is not None and not trade_calib.empty:
            self.bundle.trade_xgb.set_calibrator(
                fit_platt(self.bundle.trade_xgb.raw_proba(trade_calib), trade_calib["label"].tolist())
            )
        self.bundle.wallet_iforest.fit(wallet_normal, columns=MODEL_FEATURE_COLUMNS)
        self.bundle.trade_iforest.fit(trade_normal, columns=TRADE_FEATURE_COLUMNS)

    def score(self, features: pd.DataFrame, *, kind: str) -> pd.DataFrame:
        if kind not in {"wallet", "trade"}:
            raise ValueError("kind must be 'wallet' or 'trade'.")
        out = features.copy()
        if out.empty:
            return out
        xgb = self.bundle.wallet_xgb if kind == "wallet" else self.bundle.trade_xgb
        forest = self.bundle.wallet_iforest if kind == "wallet" else self.bundle.trade_iforest
        out["ml_score"] = xgb.predict_proba(out).to_numpy()
        out["anomaly_score"] = forest.score(out).to_numpy()
        return out

    def explain(self, features: pd.DataFrame, *, kind: str = "wallet", k: int = 5) -> pd.DataFrame:
        model = self.bundle.wallet_xgb if kind == "wallet" else self.bundle.trade_xgb
        return ShapExplainer().top_reasons(model, features, k=k)

    def save(self, directory: str | Path) -> Path:
        return self.bundle.save(directory)

    @classmethod
    def load(cls, directory: str | Path | None = None) -> WashModel:
        return cls(ModelBundle.load(directory))


def _infer_columns(features: pd.DataFrame) -> list[str]:
    if set(TRADE_FEATURE_COLUMNS) <= set(features.columns):
        return list(TRADE_FEATURE_COLUMNS)
    if set(MODEL_FEATURE_COLUMNS) <= set(features.columns):
        return list(MODEL_FEATURE_COLUMNS)
    raise ValueError(
        "The frame does not have the wallet or swap feature columns. "
        "Build it with washdetect.features."
    )


def probability_weighted_wash_percent(
    usd_values: Sequence[float],
    probabilities: Sequence[float],
    *,
    n_bootstrap: int = 1_000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict[str, float | int]:
    """100 * sum(p*v) / sum(v). The bootstrap resamples this window only."""
    if len(usd_values) != len(probabilities):
        raise ValueError("usd_values and probabilities must be the same length")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1")
    pairs = [
        (float(value), min(1.0, max(0.0, float(prob))))
        for value, prob in zip(usd_values, probabilities, strict=True)
        if value is not None and float(value) > 0
    ]
    if not pairs:
        return {"estimate": 0.0, "ci_low": 0.0, "ci_high": 0.0, "n": 0}

    total = sum(value for value, _prob in pairs)
    estimate = 100.0 * sum(value * prob for value, prob in pairs) / total

    rng = random.Random(seed)
    size = len(pairs)
    samples: list[float] = []
    draws = max(1, n_bootstrap)
    for _ in range(draws):
        num = 0.0
        den = 0.0
        for _pick in range(size):
            value, prob = pairs[rng.randrange(size)]
            den += value
            num += value * prob
        samples.append(0.0 if den == 0 else 100.0 * num / den)
    samples.sort()
    last = draws - 1
    lo_i = int((alpha / 2) * last)
    hi_i = int((1 - alpha / 2) * last)
    return {
        "estimate": estimate,
        "ci_low": samples[lo_i],
        "ci_high": samples[hi_i],
        "n": size,
    }

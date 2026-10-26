"""Interfaces for the milestone-2 models.

The classes are real call sites, not comments: the graph's "ML wash score"
box and the CLI both point here. Training data is the feature frame from
`washdetect.features`. Nothing in this module imports scikit-learn, XGBoost,
or SHAP, so the base install stays light.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

import pandas as pd


class ModelNotTrained(NotImplementedError):
    """Raised by the stub models. The message says what the next milestone plugs in."""


class IsolationForestDetector:
    """Unsupervised anomaly scores over wallet feature rows.

    Drop-in plan: `sklearn.ensemble.IsolationForest` fit on
    `FEATURE_COLUMNS`, then map `score_samples` into [0, 1] where 1 is the
    most wash-like. Contamination is a hyperparameter, not a label.
    """

    def fit(self, features: pd.DataFrame) -> None:
        raise ModelNotTrained(
            "Isolation Forest training is milestone 2. Fit "
            "sklearn.ensemble.IsolationForest on features.FEATURE_COLUMNS."
        )

    def score(self, features: pd.DataFrame) -> pd.Series:
        """Return one anomaly score per row, in [0, 1], higher = more wash-like."""
        raise ModelNotTrained(
            "Isolation Forest scoring is milestone 2. score() should return a "
            "Series aligned with features.index."
        )


class WashXGBoost:
    """Supervised model on weak labels plus synthetically injected rings.

    Labels for milestone 2: the heuristic's `heuristic_label`, with extra
    rows created by planting wash rings into clean pools. `predict_proba`
    must return P(wash) per wallet, which `probability_weighted_wash_percent`
    turns into a pool-level percentage and a bootstrap interval.
    """

    def fit(self, features: pd.DataFrame, weak_labels: Sequence[int]) -> None:
        raise ModelNotTrained(
            "XGBoost training is milestone 2. Fit on FEATURE_COLUMNS with the "
            "heuristic weak labels and synthetic rings."
        )

    def predict_proba(self, features: pd.DataFrame) -> pd.Series:
        """Return P(wash) in [0, 1] for each wallet row."""
        raise ModelNotTrained(
            "XGBoost predict_proba is milestone 2. Return a Series of "
            "probabilities aligned with features.index."
        )


class ShapExplainer:
    """SHAP values for one scored wallet, so the UI can say why it was flagged."""

    def explain(self, model: WashXGBoost, features: pd.DataFrame) -> pd.DataFrame:
        raise ModelNotTrained(
            "SHAP explanations are milestone 2. explain() should return a "
            "DataFrame of shap values with FEATURE_COLUMNS."
        )


def probability_weighted_wash_percent(
    usd_values: Sequence[float],
    probabilities: Sequence[float],
    *,
    n_bootstrap: int = 1_000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict[str, float | int]:
    """Wash percent = 100 * sum(p_i * v_i) / sum(v_i), plus a percentile bootstrap CI.

    Hard labels are the special case p_i in {0, 1}: the estimate is then the
    rule-based wash percentage, and the interval is the resampling uncertainty
    of that ratio, not a statement about model calibration. Swaps with a
    missing or non-positive USD value are left out of both sums.
    """
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

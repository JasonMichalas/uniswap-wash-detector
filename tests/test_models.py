import pandas as pd
import pytest

from washdetect.features import MODEL_FEATURE_COLUMNS
from washdetect.models import (
    IsolationForestDetector,
    ModelBundle,
    ModelNotTrained,
    ShapExplainer,
    WashXGBoost,
    probability_weighted_wash_percent,
)

pytest.importorskip("xgboost")


def _frame(rows: list[tuple[float, int]]) -> tuple[pd.DataFrame, list[int]]:
    """Only out_transfer_ratio differs, so the model cannot recite another column."""
    records = []
    labels = []
    for out_ratio, label in rows:
        record = dict.fromkeys(MODEL_FEATURE_COLUMNS, 0.0)
        record["n_swaps"] = 2.0
        record["n_buys"] = 2.0
        record["buy_fraction"] = 1.0
        record["out_transfer_ratio"] = out_ratio
        records.append(record)
        labels.append(label)
    return pd.DataFrame(records), labels


def test_bootstrap_on_hard_labels_and_probabilities():
    certain = probability_weighted_wash_percent([10, 10, 10], [1, 1, 1], n_bootstrap=50)
    assert certain["estimate"] == 100
    assert certain["ci_low"] == 100
    assert certain["ci_high"] == 100

    none = probability_weighted_wash_percent([5, 5], [0, 0], n_bootstrap=20)
    assert none["estimate"] == 0

    mixed = probability_weighted_wash_percent([100, 100], [1, 0], n_bootstrap=200, seed=1)
    assert mixed["estimate"] == 50
    assert mixed["ci_low"] <= mixed["estimate"] <= mixed["ci_high"]

    empty = probability_weighted_wash_percent([0, None], [1, 1])  # type: ignore[list-item]
    assert empty["n"] == 0


def test_unfitted_models_say_what_is_missing():
    frame, _labels = _frame([(0.0, 0)])
    with pytest.raises(ModelNotTrained, match="Isolation Forest"):
        IsolationForestDetector().score(frame)
    with pytest.raises(ModelNotTrained, match="XGBoost"):
        WashXGBoost().predict_proba(frame)
    with pytest.raises(ModelNotTrained, match="SHAP"):
        ShapExplainer().explain(WashXGBoost(), frame)


def test_xgboost_separates_transfer_out_from_a_round_trip_and_explains_it(tmp_path):
    train_rows = [(1.0, 1)] * 40 + [(0.0, 0)] * 40
    test_pos, _ = _frame([(1.0, 1)] * 12)
    test_neg, _ = _frame([(0.0, 0)] * 12)
    frame, labels = _frame(train_rows)
    model = WashXGBoost()
    model.fit(frame, labels)
    high = model.predict_proba(test_pos).mean()
    low = model.predict_proba(test_neg).mean()
    assert high > 0.7
    assert low < 0.3
    reasons = ShapExplainer().top_reasons(model, test_pos.head(1), k=5)
    out_ratio = reasons.set_index("feature").loc["out_transfer_ratio"]
    assert out_ratio["direction"] == "increases"
    assert float(out_ratio["shap_value"]) > 0

    forest = IsolationForestDetector()
    forest.fit(frame)
    scores = forest.score(pd.concat([test_pos, test_neg], ignore_index=True))
    assert ((scores >= 0) & (scores <= 1)).all()

    bundle = ModelBundle()
    bundle.wallet_xgb = model
    bundle.wallet_xgb.columns = list(MODEL_FEATURE_COLUMNS)
    bundle.trade_xgb.fit(frame, labels)
    bundle.wallet_iforest = forest
    bundle.trade_iforest.fit(frame)
    bundle.manifest = {"note": "unit"}
    bundle.save(tmp_path)
    loaded = ModelBundle.load(tmp_path)
    again = loaded.wallet_xgb.predict_proba(test_pos).mean()
    assert abs(again - high) < 1e-6

import pandas as pd
import pytest

from washdetect.models import (
    IsolationForestDetector,
    ModelNotTrained,
    ShapExplainer,
    WashXGBoost,
    probability_weighted_wash_percent,
)


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


def test_model_stubs_say_what_is_missing():
    frame = pd.DataFrame([{"wallet": "0xa"}])
    with pytest.raises(ModelNotTrained, match="Isolation Forest"):
        IsolationForestDetector().fit(frame)
    with pytest.raises(ModelNotTrained, match="XGBoost"):
        WashXGBoost().predict_proba(frame)
    with pytest.raises(ModelNotTrained, match="SHAP"):
        ShapExplainer().explain(WashXGBoost(), frame)

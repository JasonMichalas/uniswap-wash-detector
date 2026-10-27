"""ML boxes score the loaded window once a model bundle exists."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from washdetect.features import MODEL_FEATURE_COLUMNS, TRADE_FEATURE_COLUMNS
from washdetect.models import IsolationForestDetector, ModelBundle, WashXGBoost
from washdetect.querylang.engine import execute_frames
from washdetect.querylang.errors import QueryError
from washdetect.querylang.parser import parse

pytest.importorskip("xgboost")

START = datetime(2026, 1, 1)
END = START + timedelta(days=7)


def _bundle(path) -> None:
    wallet = pd.DataFrame([{column: 0.0 for column in MODEL_FEATURE_COLUMNS} for _ in range(20)])
    wallet.loc[:9, "out_transfer_ratio"] = 1.0
    trade = pd.DataFrame([{column: 0.0 for column in TRADE_FEATURE_COLUMNS} for _ in range(20)])
    trade.loc[:9, "wallet_out_transfer_ratio"] = 1.0
    labels = [1] * 10 + [0] * 10
    bundle = ModelBundle()
    bundle.wallet_xgb = WashXGBoost()
    bundle.trade_xgb = WashXGBoost()
    bundle.wallet_xgb.fit(wallet, labels)
    bundle.trade_xgb.fit(trade, labels)
    bundle.wallet_iforest = IsolationForestDetector()
    bundle.trade_iforest = IsolationForestDetector()
    bundle.wallet_iforest.fit(wallet)
    bundle.trade_iforest.fit(trade)
    bundle.save(path)


def _swaps() -> pd.DataFrame:
    rows = [
        ("0xa", 100, "buy", 10, 10, "0x1", 0, "0xpool", START),
        ("0xa", 140, "sell", 10, 10, "0x2", 0, "0xpool", START + timedelta(hours=1)),
        ("0xb", 160, "buy", 4, 4, "0x3", 0, "0xpool", START + timedelta(hours=2)),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "trader",
            "block_number",
            "side",
            "amount_token",
            "usd_value",
            "tx_hash",
            "log_index",
            "pool_address",
            "block_timestamp",
        ],
    )


def _run(query: str, model_dir):
    return execute_frames(
        parse(query),
        swaps=_swaps(),
        transfers=pd.DataFrame(),
        end_ts=END,
        window_start=START,
        window_end=END,
        model_dir=str(model_dir),
    )


def test_missing_bundle_names_the_train_command(tmp_path):
    with pytest.raises(QueryError, match="washdetect train"):
        _run("ml_wash_percent", tmp_path)


def test_scored_swaps_carry_a_probability_and_a_bootstrap_interval(tmp_path):
    _bundle(tmp_path)
    scored = _run(
        's = source swaps\nm = ml_score s\noutput table m as "Scored"\n',
        tmp_path,
    )
    rows = scored.outputs[0].rows
    assert rows
    for row in rows:
        assert 0.0 <= float(row["ml_score"]) <= 1.0
        assert abs(float(row["ml_weighted_usd"]) - float(row["ml_score"]) * float(row["usd_value"])) < 1e-9

    percent = _run("ml_wash_percent", tmp_path)
    value = float(percent.outputs[0].rows[0]["value"])
    assert 0.0 <= value <= 100.0
    assert percent.outputs[0].note
    assert "bootstrap" in percent.outputs[0].note

    anomaly = _run(
        's = source swaps\na = anomaly_score s\noutput table a as "Anomaly"\n',
        tmp_path,
    )
    assert all(0.0 <= float(row["anomaly_score"]) <= 1.0 for row in anomaly.outputs[0].rows)

    reasons = _run(
        'w = source wallets\ne = explain w\noutput table e as "Reasons"\n',
        tmp_path,
    )
    assert reasons.outputs[0].rows
    assert "feature" in reasons.outputs[0].columns
    assert "shap_value" in reasons.outputs[0].columns
    wallets = {row["wallet"] for row in reasons.outputs[0].rows}
    assert "0xa" in wallets

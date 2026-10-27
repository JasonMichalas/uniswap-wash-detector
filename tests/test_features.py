"""Model features describe behaviour. They do not include the rule's decision."""

from washdetect.detector import SwapEvent, TransferEvent, detect
from washdetect.features import (
    MODEL_FEATURE_COLUMNS,
    RULE_OUTPUT_COLUMNS,
    TRADE_FEATURE_COLUMNS,
    build_feature_frame,
    build_trade_frame,
    model_matrix,
)
from washdetect.synthetic import inject_wash_groups


def test_model_columns_exclude_rule_outputs():
    assert set(MODEL_FEATURE_COLUMNS).isdisjoint(RULE_OUTPUT_COLUMNS)
    assert set(TRADE_FEATURE_COLUMNS).isdisjoint(RULE_OUTPUT_COLUMNS)
    assert "round_trip_count" not in MODEL_FEATURE_COLUMNS
    assert "wash_volume_usd" not in MODEL_FEATURE_COLUMNS
    assert "heuristic_label" not in TRADE_FEATURE_COLUMNS


def test_fan_in_buyer_and_collector_look_different_without_the_rule():
    swaps = [
        SwapEvent("0xa", 100, "buy", 2, 2, "0xba", 0),
        SwapEvent("0xb", 105, "buy", 2, 2, "0xbb", 0),
        SwapEvent("0xc", 140, "sell", 4.01, 4.01, "0xsc", 0),
    ]
    transfers = [
        TransferEvent("0xa", "0xc", 120, 2, "0xta", 0),
        TransferEvent("0xb", "0xc", 125, 2, "0xtb", 0),
    ]
    found = detect(swaps, transfers)
    frame = build_feature_frame(swaps, transfers, found).set_index("wallet")
    assert frame.loc["0xa", "n_sells"] == 0
    assert frame.loc["0xa", "out_transfer_ratio"] == 1
    assert frame.loc["0xc", "n_buys"] == 0
    assert frame.loc["0xc", "in_transfer_ratio"] > 0.9
    matrix = model_matrix(frame.reset_index(), MODEL_FEATURE_COLUMNS)
    assert list(matrix.columns) == MODEL_FEATURE_COLUMNS
    assert matrix.isna().sum().sum() == 0


def test_trade_rows_follow_swaps_and_not_the_rule_flag():
    planted = inject_wash_groups([], [], seed=3, block_lo=10_000, block_hi=40_000)
    found = detect(planted.swaps, planted.transfers)
    trades = build_trade_frame(planted.swaps, planted.transfers, detection=found)
    assert len(trades) == len(planted.swaps)
    assert set(TRADE_FEATURE_COLUMNS) <= set(trades.columns)
    # The label is stored beside the features, not inside them.
    assert trades["heuristic_label"].sum() > 0
    matrix = model_matrix(trades, TRADE_FEATURE_COLUMNS)
    assert "heuristic_label" not in matrix.columns

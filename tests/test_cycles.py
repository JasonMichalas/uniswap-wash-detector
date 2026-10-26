"""Synthetic rings. These are the shapes the heuristic must and must not flag."""

from washdetect.config import HeuristicConfig
from washdetect.detector import SwapEvent, TransferEvent, detect
from washdetect.features import build_feature_frame

CFG = HeuristicConfig()


def swap(wallet, block, side, amount, usd, tx, log_index=0):
    return SwapEvent(wallet, block, side, amount, usd, tx, log_index)


def transfer(src, dst, block, amount, tx):
    return TransferEvent(src, dst, block, amount, tx, 0)


def test_self_round_trip_is_flagged_and_a_holder_is_not():
    swaps = [
        swap("0xa", 100, "buy", 1000, 500, "0x1"),
        swap("0xa", 150, "sell", 1000, 500, "0x2"),
        swap("0xb", 120, "buy", 100, 50, "0x3"),
    ]
    found = detect(swaps, [], config=CFG)
    assert abs(found.wash_pct - (1000 / 1050 * 100)) < 1e-6
    assert ("0x1", 0) in found.flagged
    assert ("0x2", 0) in found.flagged
    assert ("0x3", 0) not in found.flagged
    wallet_a = next(row for row in found.wallets if row.wallet == "0xa")
    assert wallet_a.confidence > 0.8
    assert wallet_a.round_trip_count == 1
    trip = next(cycle for cycle in found.cycles if "0xa" in cycle.wallets and cycle.n_wallets == 1)
    assert trip.pattern_type == "round_trip"
    assert trip.n_buyers == 1 and trip.n_sellers == 1
    features = build_feature_frame(swaps, [], found)
    row = features.set_index("wallet").loc["0xa"]
    assert row["heuristic_label"] == 1
    assert row["round_trip_count"] == 1
    assert features.set_index("wallet").loc["0xb", "heuristic_label"] == 0


def test_amount_mismatch_and_wide_gap_are_not_wash():
    mismatch = detect(
        [swap("0xa", 100, "buy", 1000, 10, "0x1"), swap("0xa", 150, "sell", 500, 5, "0x2")],
        [],
        config=CFG,
    )
    assert mismatch.flagged == {}
    wide = detect(
        [swap("0xa", 100, "buy", 1000, 10, "0x1"), swap("0xa", 400, "sell", 1000, 10, "0x2")],
        [],
        config=CFG,
    )
    assert wide.flagged == {}


def test_borderline_match_is_labelled_but_not_high_confidence():
    found = detect(
        [
            swap("0xa", 100, "buy", 100, 10, "0x1"),
            swap("0xa", 300, "sell", 95, 9, "0x2"),
        ],
        [],
        config=CFG,
    )
    # |100-95|/100 = 0.05, span = 200. The rule matches. The score stays near the floor.
    assert ("0x1", 0) in found.flagged
    wallet = next(row for row in found.wallets if row.wallet == "0xa")
    assert wallet.confidence <= 0.55
    assert wallet.confidence < 0.8


def test_three_wallet_transfer_cycle_and_not_a_line():
    ring = [
        transfer("0xa", "0xb", 10, 100, "0xt1"),
        transfer("0xb", "0xc", 20, 100, "0xt2"),
        transfer("0xc", "0xa", 30, 100, "0xt3"),
    ]
    found = detect([], ring, config=CFG)
    sizes = [cycle.size for cycle in found.cycles if cycle.kind == "transfer_cycle"]
    assert 3 in sizes

    line = [
        transfer("0xa", "0xb", 10, 100, "0xt1"),
        transfer("0xb", "0xc", 20, 100, "0xt2"),
    ]
    found = detect([], line, config=CFG)
    assert [c for c in found.cycles if c.kind == "transfer_cycle"] == []


def test_two_wallet_ring_flags_the_matching_swaps():
    swaps = [
        swap("0xa", 10, "buy", 100, 40, "0xs1"),
        swap("0xb", 40, "sell", 100, 40, "0xs2"),
    ]
    transfers = [
        transfer("0xa", "0xb", 20, 100, "0xt1"),
        transfer("0xb", "0xa", 30, 100, "0xt2"),
    ]
    found = detect(swaps, transfers, config=CFG)
    assert any(cycle.size >= 2 for cycle in found.cycles)
    assert ("0xs1", 0) in found.flagged
    assert ("0xs2", 0) in found.flagged
    assert abs(found.wash_pct - 100) < 1e-6


def test_a_hub_does_not_glue_unrelated_traders():
    traders = [f"0x{i:040x}" for i in range(1, 31)]
    transfers = [transfer("0xhub", wallet, 10 + i, 1, f"0xt{i}") for i, wallet in enumerate(traders)]
    swaps = []
    for index, wallet in enumerate(traders[:2]):
        # Each wallet only buys. Nothing round-trips unless the hub links them
        # into a fake group, which it must not.
        swaps.append(swap(wallet, 50, "buy", 10, 10, f"0xs{index}"))
    found = detect(swaps, transfers, config=CFG)
    assert found.flagged == {}

"""Flow-graph wash groups: every buyer/seller shape, not only pairwise loops.

The shapes follow the dissertation brief. Amounts use a few percent of slack
so a fee-sized gap still matches, and the student's 2 + 2 -> 4.01 fan-in is
copied exactly.
"""

from washdetect.config import HeuristicConfig
from washdetect.detector import detect

CFG = HeuristicConfig()


def swap(wallet, block, side, amount, usd, tx, log_index=0):
    return (wallet, block, side, amount, usd, tx, log_index)


def as_swaps(rows):
    from washdetect.detector import SwapEvent

    return [SwapEvent(*row) for row in rows]


def as_transfers(rows):
    from washdetect.detector import TransferEvent

    return [TransferEvent(src, dst, block, amount, tx, 0) for src, dst, block, amount, tx in rows]


def _groups(found, pattern):
    return [cycle for cycle in found.cycles if cycle.pattern_type == pattern]


def test_student_fan_in_two_plus_two_sells_4_01():
    # A buys 2, B buys 2, each sends 2 to C, C sells 4.01.
    swaps = as_swaps(
        [
            swap("0xa", 100, "buy", 2, 2, "0xba"),
            swap("0xb", 105, "buy", 2, 2, "0xbb"),
            swap("0xc", 140, "sell", 4.01, 4.01, "0xsc"),
        ]
    )
    transfers = as_transfers(
        [
            ("0xa", "0xc", 120, 2, "0xta"),
            ("0xb", "0xc", 125, 2, "0xtb"),
        ]
    )
    found = detect(swaps, transfers, config=CFG)
    groups = _groups(found, "many_to_one")
    assert len(groups) == 1
    group = groups[0]
    assert group.n_buyers == 2
    assert group.n_sellers == 1
    assert group.n_wallets == 3
    assert group.size == 3
    assert {key[0] for key in group.swap_ids} == {"0xba", "0xbb", "0xsc"}
    assert abs(group.volume_usd - 8.01) < 1e-9
    assert abs(found.wash_pct - 100) < 1e-6
    assert found.flagged.keys() >= {("0xba", 0), ("0xbb", 0), ("0xsc", 0)}


def test_fan_in_is_not_a_match_when_the_sell_is_far_from_the_buys():
    swaps = as_swaps(
        [
            swap("0xa", 100, "buy", 2, 2, "0xba"),
            swap("0xb", 105, "buy", 2, 2, "0xbb"),
            swap("0xc", 140, "sell", 10, 10, "0xsc"),
        ]
    )
    transfers = as_transfers(
        [
            ("0xa", "0xc", 120, 2, "0xta"),
            ("0xb", "0xc", 125, 2, "0xtb"),
        ]
    )
    found = detect(swaps, transfers, config=CFG)
    assert _groups(found, "many_to_one") == []
    assert found.flagged == {}


def test_fan_out_one_buyer_many_sellers():
    swaps = as_swaps(
        [
            swap("0xc", 10, "buy", 10, 10, "0xbuy"),
            swap("0xa", 40, "sell", 4, 4, "0xsa"),
            swap("0xb", 45, "sell", 6.03, 6.03, "0xsb"),
        ]
    )
    transfers = as_transfers(
        [
            ("0xc", "0xa", 20, 4, "0xt1"),
            ("0xc", "0xb", 22, 6, "0xt2"),
        ]
    )
    found = detect(swaps, transfers, config=CFG)
    group = _groups(found, "one_to_many")[0]
    assert group.n_buyers == 1
    assert group.n_sellers == 2
    assert group.n_wallets == 3
    assert abs(found.wash_pct - 100) < 1e-6


def test_many_to_many_with_intermediate_shuffle():
    # 3 buyers, 2 intermediates, 4 sellers. Only the full group nets out.
    swaps = as_swaps(
        [
            swap("0xb1", 10, "buy", 2, 2, "0xb1"),
            swap("0xb2", 12, "buy", 2, 2, "0xb2"),
            swap("0xb3", 14, "buy", 4, 4, "0xb3"),
            swap("0xs1", 40, "sell", 2, 2, "0xs1"),
            swap("0xs2", 42, "sell", 2, 2, "0xs2"),
            swap("0xs3", 44, "sell", 2, 2, "0xs3"),
            swap("0xs4", 46, "sell", 2.04, 2.04, "0xs4"),
        ]
    )
    transfers = as_transfers(
        [
            ("0xb1", "0xi1", 20, 2, "0xt1"),
            ("0xb2", "0xi1", 21, 2, "0xt2"),
            ("0xb3", "0xi2", 22, 4, "0xt3"),
            ("0xi1", "0xi2", 24, 2, "0xt4"),
            ("0xi1", "0xs3", 30, 2, "0xt5"),
            ("0xi1", "0xs4", 31, 2, "0xt6"),
            ("0xi2", "0xs1", 32, 2, "0xt7"),
            ("0xi2", "0xs2", 33, 2, "0xt8"),
        ]
    )
    found = detect(swaps, transfers, config=CFG)
    group = _groups(found, "many_to_many")[0]
    assert group.n_buyers == 3
    assert group.n_sellers == 4
    assert group.n_wallets == 9
    assert "0xi1" in group.wallets and "0xi2" in group.wallets
    assert abs(group.volume_usd - 16.04) < 1e-9


def test_ring_of_three_wallets_returning_to_the_pool():
    swaps = as_swaps(
        [
            swap("0xa", 10, "buy", 10, 50, "0xbuy"),
            swap("0xa", 50, "sell", 10.02, 50, "0xsell"),
        ]
    )
    transfers = as_transfers(
        [
            ("0xa", "0xb", 20, 10, "0xt1"),
            ("0xb", "0xc", 30, 10, "0xt2"),
            ("0xc", "0xa", 40, 10, "0xt3"),
        ]
    )
    found = detect(swaps, transfers, config=CFG)
    rings = [cycle for cycle in found.cycles if cycle.pattern_type == "ring" and cycle.n_wallets >= 3]
    assert rings
    assert rings[0].n_buyers == 1
    assert rings[0].n_sellers == 1
    assert {"0xa", "0xb", "0xc"} <= set(rings[0].wallets)
    assert ("0xbuy", 0) in found.flagged
    assert ("0xsell", 0) in found.flagged


def test_wallet_that_buys_and_sells_inside_a_larger_group():
    # A buys 5 and sells 2, forwards 3 to B, B sells 3.02.
    # A is both a buyer and a seller, so n_sellers counts A and B.
    swaps = as_swaps(
        [
            swap("0xa", 10, "buy", 5, 5, "0xbuy"),
            swap("0xa", 40, "sell", 2, 2, "0xsella"),
            swap("0xb", 50, "sell", 3.02, 3.02, "0xsellb"),
        ]
    )
    transfers = as_transfers([("0xa", "0xb", 20, 3, "0xt1")])
    found = detect(swaps, transfers, config=CFG)
    group = _groups(found, "one_to_many")[0]
    assert group.n_buyers == 1
    assert group.n_sellers == 2
    assert group.n_wallets == 2
    assert ("0xbuy", 0) in found.flagged
    assert ("0xsella", 0) in found.flagged
    assert ("0xsellb", 0) in found.flagged


def test_window_and_group_size_are_configurable():
    swaps = as_swaps(
        [
            swap("0xa", 0, "buy", 2, 2, "0xba"),
            swap("0xb", 1, "buy", 2, 2, "0xbb"),
            swap("0xc", 250, "sell", 4, 4, "0xsc"),
        ]
    )
    transfers = as_transfers(
        [
            ("0xa", "0xc", 10, 2, "0xta"),
            ("0xb", "0xc", 11, 2, "0xtb"),
        ]
    )
    missed = detect(swaps, transfers, config=CFG)
    assert missed.flagged == {}
    caught = detect(swaps, transfers, config=HeuristicConfig(max_block_gap=300))
    assert _groups(caught, "many_to_one")

    tight = detect(
        as_swaps(
            [
                swap("0xa", 100, "buy", 2, 2, "0xba"),
                swap("0xb", 105, "buy", 2, 2, "0xbb"),
                swap("0xc", 140, "sell", 4, 4, "0xsc"),
            ]
        ),
        as_transfers(
            [
                ("0xa", "0xc", 120, 2, "0xta"),
                ("0xb", "0xc", 125, 2, "0xtb"),
            ]
        ),
        config=HeuristicConfig(max_group_size=2),
    )
    assert tight.flagged == {}

"""Planted wash groups stay flagged, and the detector recovers each shape."""

from washdetect.detector import SwapEvent, detect
from washdetect.synthetic import DEFAULT_MENU, inject_wash_groups


def test_injection_is_separated_from_the_real_swap():
    real = SwapEvent("0xabc", 50_000, "buy", 7, 70, "0xreal", 0, "0xpool")
    planted = inject_wash_groups([real], [], seed=1, block_lo=50_000, block_hi=80_000)
    assert ("0xreal", 0) not in planted.swap_ids
    assert real in planted.swaps
    assert all(group.injected for group in planted.groups)
    assert all(wallet.startswith("0x57") for wallet in planted.wallets)
    assert {group.pattern_type for group in planted.groups} >= {
        "round_trip",
        "ring",
        "one_to_many",
        "many_to_one",
        "many_to_many",
    }
    assert len(planted.groups) == len(DEFAULT_MENU)


def test_detector_recalls_each_planted_shape_including_the_2_plus_2_case():
    planted = inject_wash_groups([], [], seed=2, block_lo=1_000_000, block_hi=1_040_000)
    found = detect(planted.swaps, planted.transfers)
    recovered = {
        (
            cycle.pattern_type,
            cycle.n_buyers,
            cycle.n_sellers,
        )
        for cycle in found.cycles
    }
    for group in planted.groups:
        assert (group.pattern_type, group.n_buyers, group.n_sellers) in recovered, group
        for key in group.swap_ids:
            assert key in found.flagged

    student = next(group for group in planted.groups if group.buy_amount == 4 and group.n_buyers == 2)
    assert abs(student.sell_amount - 4.01) < 1e-9
    assert student.pattern_type == "many_to_one"
    match = next(
        cycle
        for cycle in found.cycles
        if set(cycle.swap_ids) == set(student.swap_ids)
    )
    assert match.pattern_type == "many_to_one"
    assert match.n_wallets == 3
    assert abs(match.volume_usd - 8.01) < 1e-9


def test_menu_covers_wide_group_sizes_and_buyer_seller_pairs():
    sizes = set()
    pairs = set()
    for kind, buyers, sellers, _exact in DEFAULT_MENU:
        if kind == "round_trip":
            sizes.add(1)
            pairs.add((1, 1))
        elif kind == "ring":
            sizes.add(buyers)
            pairs.add((1, 1))
        elif kind == "both_sides":
            sizes.add(4)
            pairs.add((2, 3))
        elif kind == "many_to_many":
            sizes.add(buyers + sellers + 2)
            pairs.add((buyers, sellers))
        else:
            sizes.add(buyers + sellers)
            pairs.add((buyers, sellers))
    assert min(sizes) == 1
    assert max(sizes) >= 10
    assert (1, 8) in pairs
    assert (10, 1) in pairs
    assert (3, 4) in pairs and (5, 6) in pairs

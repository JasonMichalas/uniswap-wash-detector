"""One test per wash rule, plus the case that must stay unmarked."""

from washdetect.config import CEX_HOT, MEV_ACTORS, ROUTERS, HeuristicConfig
from washdetect.detector import SwapEvent, TransferEvent, detect

DAY = 7_200


def swap(wallet, block, side, amount, usd, tx, log_index=0):
    return SwapEvent(wallet, block, side, amount, usd, tx, log_index)


def transfer(src, dst, block, amount, tx):
    return TransferEvent(src, dst, block, amount, tx, 0)


def rules_of(found, tx):
    return found.reasons[(tx, 0)]["rules"].split(",")


def test_r1_same_wallet_round_trip_includes_the_same_block():
    found = detect(
        [swap("0xa", 100, "buy", 10, 10, "0xbuy"), swap("0xa", 100, "sell", 10, 10, "0xsell")],
        [],
    )
    assert "R1" in rules_of(found, "0xbuy")
    assert ("0xsell", 0) in found.flagged
    assert "bought and sold" in found.reasons[("0xbuy", 0)]["why"]


def test_r1_does_not_match_outside_the_window():
    found = detect(
        [swap("0xa", 100, "buy", 10, 10, "0xbuy"), swap("0xa", 400, "sell", 10, 10, "0xsell")],
        [],
    )
    assert found.flagged == {}


def test_r2_partial_lot_washes_only_the_amount_that_was_sold():
    found = detect(
        [swap("0xa", 100, "buy", 10, 10, "0xbuy"), swap("0xb", 140, "sell", 1, 1, "0xsell")],
        [transfer("0xa", "0xb", 120, 1, "0xsend")],
    )
    assert "R2" in rules_of(found, "0xsell")
    assert found.washed_amount[("0xbuy", 0)] == 1
    assert found.washed_amount[("0xsell", 0)] == 1
    assert abs(found.wash_volume_usd - 2) < 1e-9
    assert "block 100" in found.reasons[("0xsell", 0)]["path"]
    assert "0xsend" in found.reasons[("0xsell", 0)]["path"]


def test_r2_follows_a_chain_of_twenty_hops():
    wallets = [f"0x{index:040x}" for index in range(1, 22)]
    swaps = [
        swap(wallets[0], 100, "buy", 5, 5, "0xbuy"),
        swap(wallets[-1], 160, "sell", 5, 5, "0xsell"),
    ]
    transfers = [
        transfer(wallets[index], wallets[index + 1], 110 + index, 5, f"0xt{index}")
        for index in range(len(wallets) - 1)
    ]
    wide = detect(swaps, transfers, config=HeuristicConfig(max_group_size=24, max_block_gap=500))
    assert "R2" in rules_of(wide, "0xsell")
    assert wallets[0] in wide.reasons[("0xsell", 0)]["path"]
    assert wallets[-1] in wide.reasons[("0xsell", 0)]["path"]
    short = detect(swaps, transfers, config=HeuristicConfig(max_group_size=16, max_block_gap=500))
    assert ("0xsell", 0) not in short.flagged


def test_r2_fan_in_can_wait_hours_when_the_window_allows_it():
    swaps = [swap(f"0x{i}", 100 + i, "buy", 2, 2, f"0xb{i}") for i in range(5)]
    swaps.append(swap("0xcol", 100 + 3_000, "sell", 10, 10, "0xsell"))
    transfers = [transfer(f"0x{i}", "0xcol", 200 + i, 2, f"0xt{i}") for i in range(5)]
    late = detect(swaps, transfers, config=HeuristicConfig(max_block_gap=4_000, max_group_size=8))
    assert "R2" in rules_of(late, "0xsell")
    assert abs(late.wash_volume_usd - 20) < 1e-6
    early = detect(swaps, transfers)
    assert ("0xsell", 0) not in early.flagged


def test_r2_fan_out_and_many_to_many():
    fan_out = detect(
        [
            swap("0xa", 10, "buy", 10, 10, "0xbuy"),
            swap("0xb", 40, "sell", 4, 4, "0xs1"),
            swap("0xc", 45, "sell", 6, 6, "0xs2"),
        ],
        [transfer("0xa", "0xb", 20, 4, "0xt1"), transfer("0xa", "0xc", 22, 6, "0xt2")],
    )
    assert "R2" in rules_of(fan_out, "0xs1")
    assert "R2" in rules_of(fan_out, "0xs2")
    assert ("0xbuy", 0) in fan_out.flagged
    assert abs(fan_out.wash_volume_usd - 20) < 1e-6

    many = detect(
        [
            swap("0xa", 10, "buy", 4, 4, "0xba"),
            swap("0xb", 12, "buy", 6, 6, "0xbb"),
            swap("0xc", 40, "sell", 4, 4, "0xsc"),
            swap("0xd", 42, "sell", 6, 6, "0xsd"),
        ],
        [
            transfer("0xa", "0xc", 20, 4, "0xt1"),
            transfer("0xb", "0xd", 22, 6, "0xt2"),
        ],
    )
    assert "R2" in rules_of(many, "0xsc")
    assert "R2" in rules_of(many, "0xsd")


def test_r3_common_funder_links_traders_and_a_hub_does_not():
    linked = detect(
        [swap("0xa", 100, "buy", 5, 5, "0xbuy"), swap("0xb", 120, "sell", 5, 5, "0xsell")],
        [transfer("0xfunder", "0xa", 50, 1, "0xf1"), transfer("0xfunder", "0xb", 50, 1, "0xf2")],
    )
    assert "R3" in rules_of(linked, "0xbuy")
    assert "funded by 0xfunder" in linked.reasons[("0xbuy", 0)]["why"]

    traders = [f"0x{i:040x}" for i in range(1, 31)]
    transfers = [transfer("0xhub", wallet, 10 + i, 1, f"0xt{i}") for i, wallet in enumerate(traders)]
    swaps = [swap(wallet, 80, "buy", 10, 10, f"0xs{i}") for i, wallet in enumerate(traders[:2])]
    assert detect(swaps, transfers).flagged == {}


def test_r4_circle_is_labelled_when_the_path_returns():
    found = detect(
        [swap("0xa", 10, "buy", 5, 5, "0xbuy"), swap("0xb", 40, "sell", 5, 5, "0xsell")],
        [
            transfer("0xa", "0xb", 15, 5, "0xt1"),
            transfer("0xb", "0xc", 20, 5, "0xt2"),
            transfer("0xc", "0xb", 25, 5, "0xt3"),
        ],
    )
    assert "R4" in rules_of(found, "0xsell")


def test_r5_churn_with_a_flat_position():
    found = detect(
        [
            swap("0xa", 10, "buy", 4, 4, "0x1"),
            swap("0xa", 20, "sell", 4, 4, "0x2"),
            swap("0xa", 30, "buy", 4, 4, "0x3"),
            swap("0xa", 40, "sell", 4, 4, "0x4"),
        ],
        [],
    )
    assert "R5" in rules_of(found, "0x1")
    assert "finished flat" in found.reasons[("0x1", 0)]["why"]


def test_r6_same_transaction_back_and_forth():
    found = detect(
        [swap("0xa", 10, "buy", 8, 8, "0xsame", 0), swap("0xa", 10, "sell", 8, 8, "0xsame", 1)],
        [],
    )
    assert "R6" in found.reasons[("0xsame", 0)]["rules"].split(",")
    assert "R6" in found.reasons[("0xsame", 1)]["rules"].split(",")
    assert "same transaction" in found.reasons[("0xsame", 0)]["why"]


def test_unrelated_wallets_trading_within_ten_minutes_are_not_wash():
    found = detect(
        [swap("0xa", 100, "buy", 2, 2, "0xbuy"), swap("0xb", 140, "sell", 2, 2, "0xsell")],
        [],
    )
    assert found.flagged == {}
    assert found.wash_pct == 0


def test_a_router_is_not_a_hop():
    router = next(iter(ROUTERS))
    found = detect(
        [swap("0xa", 10, "buy", 5, 5, "0xbuy"), swap("0xb", 40, "sell", 5, 5, "0xsell")],
        [transfer("0xa", router, 20, 5, "0xt1"), transfer(router, "0xb", 21, 5, "0xt2")],
    )
    assert found.flagged == {}


def test_a_cex_an_mev_bot_and_a_pool_are_not_hops():
    cex = next(iter(CEX_HOT))
    bot = next(iter(MEV_ACTORS))
    pool = "0x" + "ab" * 20
    for mid in (cex, bot, pool):
        excluded = {pool} if mid == pool else None
        found = detect(
            [swap("0xa", 10, "buy", 5, 5, "0xbuy"), swap("0xb", 40, "sell", 5, 5, "0xsell")],
            [transfer("0xa", mid, 20, 5, "0xt1"), transfer(mid, "0xb", 21, 5, "0xt2")],
            excluded=excluded,
        )
        assert found.flagged == {}, mid


def test_turning_rules_off_drops_that_kind():
    swaps = [swap("0xa", 100, "buy", 10, 10, "0xbuy"), swap("0xa", 110, "sell", 10, 10, "0xsell")]
    assert ("0xbuy", 0) in detect(swaps, []).flagged
    quiet = detect(swaps, [], config=HeuristicConfig(enabled_rules=("R2",)))
    assert quiet.flagged == {}

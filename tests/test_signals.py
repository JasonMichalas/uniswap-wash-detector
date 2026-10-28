from datetime import datetime, timedelta

import pandas as pd

from washdetect.config import CEX_HOT, MEV_ACTORS
from washdetect.signals import WindowSignals, _gini

BINANCE = "0x28c6c06298d514db089934071355e5743bf21d60"
MEV = "0xae2fc483527b8ef99eb5d9b44875f005ba1fae13"
ZERO = "0x" + "00" * 20


def _swaps(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _transfers(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _wallets(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _swap(**kwargs) -> dict:
    base = {
        "block_number": 10,
        "block_timestamp": datetime(2026, 10, 5, 12, 0),
        "pool_address": "0xpool",
        "trader": "0xaa",
        "side": "buy",
        "amount_token": 1.0,
        "usd_value": 10.0,
        "tx_hash": "0xtx",
        "log_index": 1,
    }
    base.update(kwargs)
    return base


def test_gini_is_zero_when_balances_match_and_high_when_they_do_not():
    assert _gini([10, 10, 10]) == 0
    assert _gini([1]) == 0
    assert abs(_gini([20, 30, 50]) - 0.2) < 1e-9


def test_concentration_uses_measured_balances_and_skips_pools():
    wallets = _wallets(
        [
            {"wallet": "0xaa", "balance": 50, "holder_kind": "wallet"},
            {"wallet": "0xbb", "balance": 30, "holder_kind": "wallet"},
            {"wallet": "0xcc", "balance": 20, "holder_kind": "wallet"},
            {"wallet": "0xpool", "balance": 1000, "holder_kind": "pool"},
            {"wallet": ZERO, "balance": 5, "holder_kind": "wallet"},
        ]
    )
    frame, note = WindowSignals(None, None, wallets, pools={"0xpool"}).run("concentration", top_n=2)
    assert list(frame["wallet"]) == ["0xaa", "0xbb"]
    assert abs(frame.iloc[0]["share_percent"] - 0.5) < 1e-9
    assert "80%" in note
    assert "Gini is 20%" in note
    assert "not the full token supply" in note


def test_concentration_says_when_no_balance_was_read():
    frame, note = WindowSignals(None, None, _wallets([])).run("concentration")
    assert frame.empty
    assert "No holder balances" in note


def test_pnl_uses_in_window_average_cost_and_refuses_an_earlier_cost():
    swaps = _swaps(
        [
            _swap(trader="0xaa", side="buy", amount_token=10, usd_value=100, log_index=1, tx_hash="0xb"),
            _swap(trader="0xbb", side="sell", amount_token=5, usd_value=50, log_index=2, tx_hash="0xold"),
            _swap(trader="0xaa", side="sell", amount_token=4, usd_value=80, log_index=3, tx_hash="0xs"),
        ]
    )
    wallets = _wallets(
        [
            {"wallet": "0xaa", "balance": 6, "holder_kind": "wallet"},
            {"wallet": "0xbb", "balance": 0, "holder_kind": "wallet"},
        ]
    )
    frame, note = WindowSignals(swaps, None, wallets).run("pnl", top_n=5)
    winner = frame[frame["wallet"] == "0xaa"].iloc[0]
    assert winner["cost_status"] == "Known"
    assert winner["side"] == "Winner"
    assert abs(winner["realised_usd"] - 40) < 1e-9
    # Last trade is $20 per token. Unrealised is the 6 tokens still held at that price, minus cost.
    assert abs(winner["unrealised_usd"] - 60) < 1e-9
    unknown = frame[frame["wallet"] == "0xbb"].iloc[0]
    assert unknown["cost_status"] == "Unknown"
    assert unknown["side"] == "Unknown"
    assert pd.isna(unknown["realised_usd"])
    assert "not inventing" in note or "left blank" in note or "Unknown" in note


def test_sandwich_is_same_block_same_pool_and_same_direction():
    swaps = _swaps(
        [
            _swap(trader="0xatk", side="buy", usd_value=10, log_index=1, tx_hash="0xbuy", block_number=5),
            _swap(trader="0xvic", side="buy", usd_value=50, log_index=2, tx_hash="0xvic", block_number=5),
            _swap(trader="0xatk", side="sell", usd_value=18, log_index=3, tx_hash="0xsell", block_number=5),
            _swap(trader="0xatk", side="buy", usd_value=10, log_index=1, tx_hash="0xother", block_number=6),
            _swap(trader="0xvic", side="buy", usd_value=10, log_index=2, tx_hash="0xother2", block_number=6),
            _swap(trader="0xatk", side="sell", usd_value=12, log_index=3, tx_hash="0xother3", block_number=7),
        ]
    )
    frame, note = WindowSignals(swaps, None, None).run("sandwich")
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["attacker"] == "0xatk"
    assert row["victim"] == "0xvic"
    assert row["profit_usd"] == 8
    assert row["buy_tx"] == "0xbuy"
    assert row["victim_tx"] == "0xvic"
    assert row["sell_tx"] == "0xsell"
    assert "Profit" in note


def test_sandwich_empty_message():
    frame, note = WindowSignals(_swaps([_swap()]), None, None).run("sandwich")
    assert frame.empty
    assert "No sandwich attacks" in note


def test_insiders_need_a_creator_and_then_a_sale():
    transfers = _transfers(
        [{"block_number": 1, "log_index": 1, "from_address": "0xcreator", "to_address": "0xwallet", "amount": 100, "tx_hash": "0xin"}]
    )
    swaps = _swaps([_swap(trader="0xwallet", side="sell", usd_value=40, tx_hash="0xsell")])
    empty, missing = WindowSignals(swaps, transfers, None, creator_status="no_mint").run("insiders")
    assert empty.empty
    assert "Paste the creator" in missing
    frame, note = WindowSignals(swaps, transfers, None).run("insiders", creator="0xcreator")
    assert frame.iloc[0]["wallet"] == "0xwallet"
    assert frame.iloc[0]["sold_usd"] == 40
    assert "pasted" in note
    assert "ETH" in note
    found, how = WindowSignals(swaps, transfers, None, creator="0xcreator", creator_status="found").run("insiders")
    assert found.iloc[0]["creator"] == "0xcreator"
    assert "transaction sender" in how


def test_mint_without_a_sender_does_not_invent_a_creator():
    _frame, note = WindowSignals(None, None, None, creator_status="mint_without_sender").run("insiders")
    assert "first holder" in note
    assert "Paste the creator" in note


def test_fake_holders_cluster_a_shared_funder_without_requiring_trades():
    rows = []
    for index, wallet in enumerate(["0xa", "0xb", "0xc", "0xd", "0xe", "0xf"]):
        rows.append(
            {
                "block_number": 100 + index,
                "log_index": 1,
                "from_address": "0xsource",
                "to_address": wallet,
                "amount": 1,
                "tx_hash": f"0x{index}",
            }
        )
    rows.append(
        {
            "block_number": 50,
            "log_index": 1,
            "from_address": BINANCE,
            "to_address": "0xnot",
            "amount": 1,
            "tx_hash": "0xex",
        }
    )
    wallets = _wallets(
        [{"wallet": name, "balance": 2, "holder_kind": "wallet"} for name in ["0xa", "0xb", "0xc", "0xd", "0xe", "0xf"]]
    )
    frame, note = WindowSignals(None, _transfers(rows), wallets).run("fake_holders", minutes=60, min_wallets=5)
    assert len(frame) == 1
    assert frame.iloc[0]["source"] == "0xsource"
    assert frame.iloc[0]["wallet_count"] == 6
    assert frame.iloc[0]["holdings"] == 12
    assert "does not require" in note
    short, empty_note = WindowSignals(None, _transfers(rows[:4]), wallets).run("fake_holders", min_wallets=5)
    assert short.empty
    assert "No cluster" in empty_note


def test_liquidity_pull_flags_a_fast_removal_by_the_original_adder():
    liquidity = pd.DataFrame(
        [
            {
                "block_number": 10,
                "block_timestamp": datetime(2026, 10, 5, 12, 0),
                "pool_address": "0xpool",
                "kind": "mint",
                "sender": "0xadder",
                "amount0_raw": "100",
                "amount1_raw": "0",
                "tx_hash": "0xadd",
                "log_index": 1,
            },
            {
                "block_number": 20,
                "block_timestamp": datetime(2026, 10, 5, 12, 2),
                "pool_address": "0xpool",
                "kind": "burn",
                "sender": "0xadder",
                "amount0_raw": "60",
                "amount1_raw": "0",
                "tx_hash": "0xburn",
                "log_index": 2,
            },
        ]
    )
    frame, note = WindowSignals(None, None, None, liquidity=liquidity).run(
        "liquidity_pull", minutes=60, percent=50
    )
    assert frame.iloc[0]["removed_percent"] == 0.6
    assert pd.isna(frame.iloc[0]["removed_usd"])
    assert bool(frame.iloc[0]["same_wallet"]) is True
    assert "raw pool units" in note


def test_whales_tag_exchange_flow_and_ignore_pool_hops():
    swaps = _swaps([_swap(amount_token=10, usd_value=2000)])
    transfers = _transfers(
        [
            {
                "block_number": 10,
                "block_timestamp": datetime(2026, 10, 5, 12, 0),
                "from_address": "0xwhale",
                "to_address": BINANCE,
                "amount": 1000,
                "tx_hash": "0xtoex",
                "log_index": 1,
            },
            {
                "block_number": 11,
                "block_timestamp": datetime(2026, 10, 5, 12, 1),
                "from_address": "0xwhale",
                "to_address": "0xpool",
                "amount": 5000,
                "tx_hash": "0xpool",
                "log_index": 1,
            },
            {
                "block_number": 12,
                "block_timestamp": datetime(2026, 10, 5, 12, 2),
                "from_address": "0xsmall",
                "to_address": "0xother",
                "amount": 1,
                "tx_hash": "0xsmall",
                "log_index": 1,
            },
        ]
    )
    wallets = _wallets([{"wallet": "0xwhale", "balance": 10_000, "holder_kind": "wallet"}])
    frame, _note = WindowSignals(swaps, transfers, wallets, pools={"0xpool"}).run("whales", usd=100_000, percent=1)
    assert list(frame["tx_hash"]) == ["0xtoex"]
    assert frame.iloc[0]["tag"] == "Sent to an exchange"
    assert frame.iloc[0]["amount_usd"] == 200_000
    assert BINANCE in CEX_HOT


def test_pressure_is_hourly_bought_minus_sold():
    swaps = _swaps(
        [
            _swap(side="buy", usd_value=30, block_timestamp=datetime(2026, 10, 5, 12, 15), amount_token=3),
            _swap(side="sell", usd_value=10, block_timestamp=datetime(2026, 10, 5, 12, 40), amount_token=1, log_index=2),
            _swap(side="buy", usd_value=5, block_timestamp=datetime(2026, 10, 5, 13, 5), amount_token=1, log_index=3),
        ]
    )
    frame, note = WindowSignals(swaps, None, None).run("pressure")
    assert list(frame["day"]) == ["2026-10-05 12:00", "2026-10-05 13:00"]
    assert frame.iloc[0]["net_usd"] == 20
    assert frame.iloc[0]["price_usd"] == 10
    assert "one hour" in note


def test_bots_use_the_mev_list_back_to_back_blocks_and_a_full_day():
    assert MEV in MEV_ACTORS
    mev = _swaps([_swap(trader=MEV, tx_hash="0xmev")])
    labelled, _note = WindowSignals(mev, None, None).run("bots")
    assert labelled.iloc[0]["label"] == "Bot"
    assert "jaredfromsubway" in labelled.iloc[0]["reason"]

    burst = []
    for index in range(21):
        burst.append(_swap(trader="0xfast", block_number=100 + index, log_index=1, tx_hash=f"0x{index}"))
    fast, _note = WindowSignals(_swaps(burst), None, None).run("bots")
    assert fast[fast["wallet"] == "0xfast"].iloc[0]["label"] == "Bot"

    start = datetime(2026, 10, 5, 0, 0)
    steady = []
    for index in range(25):
        steady.append(
            _swap(
                trader="0xawake",
                block_number=1000 + index * 300,
                block_timestamp=start + timedelta(minutes=60 * index),
                tx_hash=f"0xh{index}",
                log_index=1,
            )
        )
    awake, _note = WindowSignals(_swaps(steady), None, None).run("bots")
    assert awake[awake["wallet"] == "0xawake"].iloc[0]["label"] == "Bot"
    assert "24" in awake[awake["wallet"] == "0xawake"].iloc[0]["reason"] or "hours" in awake.iloc[0]["reason"]

    person, note = WindowSignals(_swaps([_swap(trader="0xhuman"), _swap(trader="0xhuman", log_index=2, block_number=50)]), None, None).run("bots")
    assert person.iloc[0]["label"] == "Human"
    assert "people" in note


def test_wash_share_over_time_flags_a_round_trip():
    swaps = _swaps(
        [
            _swap(side="buy", amount_token=10, usd_value=100, block_number=10, log_index=1, tx_hash="0xbuy"),
            _swap(side="sell", amount_token=10, usd_value=100, block_number=12, log_index=1, tx_hash="0xsell"),
            _swap(
                trader="0xbb",
                side="buy",
                amount_token=1,
                usd_value=10,
                block_number=14,
                log_index=1,
                tx_hash="0xplain",
            ),
        ]
    )
    frame, note = WindowSignals(swaps, _transfers([]), None).run("wash_over_time")
    assert frame.iloc[0]["day"] == "2026-10-05"
    assert frame.iloc[0]["wash_usd"] == 200
    assert abs(frame.iloc[0]["wash_percent"] - 200 / 210) < 1e-9
    assert "about a day" in note


def test_insight_query_and_graph_keep_the_settings():
    from washdetect.graph.compile import compile_graph
    from washdetect.querylang.engine import execute_frames
    from washdetect.querylang.parser import parse

    wallets = _wallets(
        [
            {"wallet": "0xaa", "balance": 80, "holder_kind": "wallet"},
            {"wallet": "0xbb", "balance": 20, "holder_kind": "wallet"},
        ]
    )
    result = execute_frames(
        parse('c = insight concentration\noutput table c as "Concentration"\n'),
        wallets=wallets,
    )
    assert result.outputs[0].rows[0]["wallet"] == "0xaa"
    assert result.outputs[0].note and "Gini" in result.outputs[0].note
    graph = {
        "nodes": [
            {"id": "q", "type": "whales", "data": {"usd": 250000, "percent": 2}},
            {"id": "o", "type": "output_table", "data": {"title": "Whales"}},
        ],
        "edges": [
            {"id": "e", "source": "q", "target": "o", "sourceHandle": "out", "targetHandle": "in"}
        ],
    }
    program, text = compile_graph(graph)
    assert "insight whales" in text
    assert program.statements[0].expr.usd == 250000
    assert program.statements[0].expr.percent == 2
    chart = {
        "nodes": [
            {"id": "q", "type": "pressure", "data": {}},
            {"id": "o", "type": "output_chart", "data": {"title": "Pressure"}},
        ],
        "edges": [
            {"id": "e", "source": "q", "target": "o", "sourceHandle": "out", "targetHandle": "in"}
        ],
    }
    assert compile_graph(chart)[1].startswith("n_q")


def test_creator_is_the_mint_sender_not_the_recipient(tmp_path):
    from washdetect.store import Store

    store = Store(tmp_path / "cache.duckdb")
    try:
        store.insert_transfers(
            [
                (
                    "0xtoken",
                    "0xmint",
                    0,
                    10,
                    datetime(2026, 10, 1),
                    ZERO,
                    "0xfirst",
                    "1",
                    1.0,
                )
            ]
        )
        assert store.creator_from_window("0xtoken", 1, 20) == ("", "mint_without_sender")
        store.remember_senders([("0xmint", "0xDeployer")])
        assert store.creator_from_window("0xtoken", 1, 20) == ("0xdeployer", "found")
        assert store.creator_from_window("0xtoken", 100, 200)[1] == "no_mint"
    finally:
        store.close()

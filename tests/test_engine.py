"""The query language executes as read-only SQL, including a planted round-trip."""

from datetime import datetime, timedelta

import pandas as pd

from washdetect.querylang.engine import _strip_strings, execute_frames
from washdetect.querylang.parser import parse

START = datetime(2026, 1, 1)
END = START + timedelta(days=30)


def _swaps():
    rows = [
        ("0xa", 100, "buy", 1000, 500, "0x1", 0, "0xpool", START + timedelta(hours=1)),
        ("0xa", 150, "sell", 1000, 500, "0x2", 0, "0xpool", START + timedelta(hours=2)),
        ("0xb", 120, "buy", 100, 50, "0x3", 0, "0xpool", START + timedelta(hours=3)),
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


def _run(query, swaps=None, transfers=None, wallets=None):
    return execute_frames(
        parse(query),
        swaps=swaps if swaps is not None else _swaps(),
        transfers=transfers if transfers is not None else pd.DataFrame(),
        wallets=wallets,
        end_ts=END,
        window_start=START,
        window_end=END,
    )


def test_wash_percent_on_a_planted_round_trip():
    result = _run("wash_percent")
    value = result.outputs[0].rows[0]["value"]
    assert abs(value - (1000 / 1050 * 100)) < 0.01
    assert result.outputs[0].format == "percent"
    assert "CREATE VIEW" in result.sql
    assert "detect_cycles" in result.query


def test_confidence_filter_keeps_only_the_round_trip_wallet():
    result = _run("wash_wallets where confidence > 0.8")
    wallets = [row["wallet"] for row in result.outputs[0].rows]
    assert wallets == ["0xa"]


def test_holder_queries():
    wallets = pd.DataFrame(
        [
            {
                "wallet": "0xlong",
                "balance": 10,
                "held_days": 120,
                "ever_sold": False,
                "days_since_sell": None,
                "swap_count": 1,
                "buy_volume_usd": 10,
                "sell_volume_usd": 0,
                "held_days_is_lower_bound": False,
                "acquired_before_window": False,
            },
            {
                "wallet": "0xnew",
                "balance": 4,
                "held_days": 3,
                "ever_sold": True,
                "days_since_sell": 2,
                "swap_count": 2,
                "buy_volume_usd": 4,
                "sell_volume_usd": 1,
                "held_days_is_lower_bound": False,
                "acquired_before_window": False,
            },
            {
                "wallet": "0xflat",
                "balance": 0,
                "held_days": None,
                "ever_sold": False,
                "days_since_sell": None,
                "swap_count": 0,
                "buy_volume_usd": 0,
                "sell_volume_usd": 0,
                "held_days_is_lower_bound": False,
                "acquired_before_window": False,
            },
        ]
    )
    long_term = _run("holders where held_days > 90", wallets=wallets)
    assert [row["wallet"] for row in long_term.outputs[0].rows] == ["0xlong"]

    top = _run("top_holders limit 1", wallets=wallets)
    assert top.outputs[0].rows[0]["wallet"] == "0xlong"

    quiet = _run("wallets where not sold_in(30d)", wallets=wallets)
    assert {row["wallet"] for row in quiet.outputs[0].rows} == {"0xlong", "0xflat"}


def test_string_filter_cannot_break_out_of_the_literal():
    nasty = "0xabc'; DROP TABLE secrets; --"
    wallets = pd.DataFrame(
        [
            {
                "wallet": nasty,
                "balance": 1,
                "held_days": 1,
                "ever_sold": False,
                "days_since_sell": None,
                "swap_count": 0,
                "buy_volume_usd": 0,
                "sell_volume_usd": 0,
                "held_days_is_lower_bound": False,
                "acquired_before_window": False,
            }
        ]
    )
    query = f'wallets where wallet = "{nasty}"'
    result = _run(query, wallets=wallets)
    assert result.outputs[0].rows[0]["wallet"] == nasty
    assert "drop " not in _strip_strings(result.sql).lower()


def test_unknown_field_is_rejected():
    from washdetect.querylang.errors import QueryError

    with pytest_raises(QueryError) as caught:
        _run("holders where confidence > 0.8")
    assert "confidence" in str(caught.value)


def pytest_raises(error):
    import pytest

    return pytest.raises(error)


def test_cycles_query_on_a_three_ring():
    transfers = pd.DataFrame(
        [
            ("0xa", "0xb", 10, 100, "0xt1", 0, START),
            ("0xb", "0xc", 20, 100, "0xt2", 0, START),
            ("0xc", "0xa", 30, 100, "0xt3", 0, START),
        ],
        columns=["from_address", "to_address", "block_number", "amount", "tx_hash", "log_index", "block_timestamp"],
    )
    result = _run("cycles where size >= 3", swaps=pd.DataFrame(), transfers=transfers)
    assert result.outputs[0].rows
    assert all(row["size"] >= 3 for row in result.outputs[0].rows)

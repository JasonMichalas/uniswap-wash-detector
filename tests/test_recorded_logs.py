"""Recorded mainnet logs must still decode. A zero-swap window is not a wash result."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from washdetect.abi import decode_transfer_value, topic_address
from washdetect.discover import PoolMeta, parse_v2_pair, parse_v3_pool, parse_v4_initialize
from washdetect.logs import decode_pool_log
from washdetect.pipeline import _empty_window_note

FIXTURES = Path(__file__).parent / "fixtures"
DACITY = "0x54ce4b27caabba24926865f035d432dbde2901f7"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_recorded_dacity_v3_swap_decodes_as_a_sell():
    pool = PoolMeta(
        pool_address="0x8d5288b22eaef3084df16159d5491de6140e95ea",
        protocol="v3",
        token0=DACITY,
        token1=WETH,
        fee=100,
        token0_decimals=18,
        token1_decimals=18,
        token0_symbol="DACITY",
        token1_symbol="WETH",
    )
    kind, fields = decode_pool_log(_load("dacity_v3_swap.json"), pool, DACITY)
    assert kind == "swap"
    assert fields["side"] == "sell"
    assert fields["amount_token"] > 0
    assert fields["amount_quote"] > 0
    assert fields["quote_token"] == WETH


def test_recorded_dacity_transfer_has_a_value():
    log = _load("dacity_transfer.json")
    assert decode_transfer_value(log["data"]) > 0
    assert topic_address(log["topics"][1]) != topic_address(log["topics"][2])


def test_recorded_v4_swap_decodes():
    log = _load("v4_swap.json")
    pool_id = log["topics"][1].lower()
    pool = PoolMeta(
        pool_address=pool_id,
        protocol="v4",
        token0=DACITY,
        token1=WETH,
        fee=0,
        token0_decimals=18,
        token1_decimals=18,
        token0_symbol="DACITY",
        token1_symbol="WETH",
    )
    kind, fields = decode_pool_log(log, pool, DACITY)
    assert kind == "swap"
    assert fields["side"] in {"buy", "sell"}
    assert fields["amount_token"] > 0
    assert fields["amount_quote"] > 0


def test_recorded_factory_logs_become_pools():
    v4 = parse_v4_initialize(_load("v4_initialize.json"))
    assert v4 is not None
    assert v4[1] == "v4"
    assert v4[3].startswith("0x") and v4[4].startswith("0x")
    assert v4[3] != v4[4]

    v3 = parse_v3_pool(_load("v3_pool_created.json"))
    assert v3 is not None
    assert v3[1] == "v3"
    assert len(v3[0]) == 42

    v2 = parse_v2_pair(_load("v2_pair_created.json"))
    assert v2 is not None
    assert v2[1] == "v2"
    assert v2[5] == 3000


def test_zero_swap_logs_are_not_described_as_no_wash(monkeypatch):
    import httpx

    def offline(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", offline)
    note = _empty_window_note(DACITY, datetime(2026, 10, 5), 0, 14_344)
    assert "not a wash result" in note
    assert "no Swap logs" in note


def test_undecoded_logs_are_called_a_reader_problem():
    note = _empty_window_note(DACITY, datetime(2026, 10, 5), 12, 100)
    assert "reader problem" in note
    assert "not a wash result" in note

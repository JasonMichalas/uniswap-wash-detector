"""USD pricing: native ETH, a quote pool, and a price implied by other swaps."""

from typing import Any

from eth_abi import encode

from washdetect.abi import calldata
from washdetect.config import USDC, ZERO_ADDRESS
from washdetect.prices import (
    QuoteOracle,
    apply_token_implied,
    direct_source,
    nearest_value,
    pricing_note,
    quote_usd_from_other,
    swap_usd,
)

TOKEN = "0x" + "ab" * 20
QUOTE = "0x" + "22" * 20
POOL = "0x" + "33" * 20


def test_native_eth_uses_the_eth_price():
    assert direct_source(TOKEN, ZERO_ADDRESS) == "eth"
    assert swap_usd(TOKEN, 2.0, ZERO_ADDRESS, 1.5, 2000.0) == 3000.0


def test_a_stable_quote_does_not_need_eth():
    assert direct_source(TOKEN, USDC) == "stable"
    assert swap_usd(TOKEN, 2.0, USDC, 4.5, None) == 4.5


def test_nearest_sample_and_implied_token_price():
    assert nearest_value(50, [(10, 1.0), (80, 3.0)]) == 3.0
    rows = [
        {"block": 10, "amount_token": 2.0, "usd": 4.0, "source": "stable"},
        {"block": 12, "amount_token": 3.0, "usd": None, "source": None},
        {"block": 14, "amount_token": 0.0, "usd": None, "source": None},
    ]
    apply_token_implied(rows)
    assert rows[1]["source"] == "token-implied"
    assert rows[1]["usd"] == 6.0
    assert rows[2]["usd"] is None


def test_pricing_note_keeps_unpriced_swaps_in_token_totals():
    note = pricing_note(3, ["stable", "quote-pool", None])
    assert note is not None
    assert "2 of 3" in note
    assert "own units" in note
    assert "no stable or ETH leg" in note
    done = pricing_note(2, ["eth", "token-implied"])
    assert done is not None
    assert done.startswith("All 2 swaps")


class _Calls:
    def batch(self, calls: list[tuple[str, list[Any]]]) -> list[str]:
        return [self.eth_call(params[0]["to"], params[0]["data"]) for _method, params in calls]

    def eth_call(self, to: str, data: str, block: int | None = None) -> str:
        selector = data[:10]
        if selector == calldata("get_pool")[:10] or selector == calldata("get_pair")[:10]:
            if selector == calldata("get_pair")[:10] and USDC[2:] in data.lower():
                return "0x" + POOL[2:].rjust(64, "0")
            return "0x" + "00" * 32
        if selector == calldata("token0")[:10]:
            return "0x" + QUOTE[2:].rjust(64, "0")
        if selector == calldata("token1")[:10]:
            return "0x" + USDC[2:].rjust(64, "0")
        if selector == calldata("decimals")[:10]:
            decimals = 18 if to.lower() == QUOTE else 6
            return "0x" + encode(["uint256"], [decimals]).hex()
        if selector == calldata("get_reserves")[:10]:
            # 2 quote (18 decimals) against 10 USDC (6 decimals) => 5 USDC per quote.
            return (
                "0x" + encode(["uint112", "uint112", "uint32"], [2 * 10**18, 10 * 10**6, 1]).hex()
            )
        raise AssertionError(data[:10])


def test_quote_pool_prices_from_a_direct_stable_pair():
    oracle = QuoteOracle(_Calls())  # type: ignore[arg-type]
    samples = oracle.samples(QUOTE, [100, 400], eth_usd=2000.0)
    assert samples
    assert quote_usd_from_other(5.0, other_is_stable=True, eth_usd=None) == 5.0
    assert abs(nearest_value(390, samples) - 5.0) < 1e-9

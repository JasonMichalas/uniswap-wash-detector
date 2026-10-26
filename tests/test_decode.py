import math

from eth_abi import encode

from washdetect.abi import (
    SWAP_V2,
    classify_leg,
    decode_v2_swap_amounts,
    human_amount,
    pad_address,
    selector,
    topic_address,
)
from washdetect.prices import eth_usd_from_usdc_weth_sqrt, swap_usd


def test_known_selectors():
    assert selector("balanceOf(address)").hex() == "70a08231"
    assert selector("decimals()").hex() == "313ce567"
    assert selector("symbol()").hex() == "95d89b41"


def test_v2_swap_buy_and_sell():
    token0 = "0x" + "11" * 20
    token1 = "0x" + "22" * 20
    # Pool received 2e18 of token0 and paid out 1e6 of token1: the trader sold token0.
    data = "0x" + encode(["uint256", "uint256", "uint256", "uint256"], [2 * 10**18, 0, 0, 10**6]).hex()
    a0_in, a1_in, a0_out, a1_out = decode_v2_swap_amounts(data)
    sold = classify_leg(token0, token0, token1, 18, 6, a0_in - a0_out, a1_in - a1_out)
    assert sold is not None
    assert sold["side"] == "sell"
    assert sold["amount_token"] == 2
    assert sold["amount_quote"] == 1
    bought = classify_leg(token0, token0, token1, 18, 6, -(2 * 10**18), 10**6)
    assert bought is not None
    assert bought["side"] == "buy"
    assert topic_address(pad_address(token0)) == token0
    assert human_amount(10**6, 6) == 1


def test_eth_usd_from_sqrt_price_round_trips():
    eth = 2000.0
    raw_ratio = 1e18 / (eth * 1e6)  # WETH raw per USDC raw
    sqrt_price = int(math.sqrt(raw_ratio) * 2**96)
    assert abs(eth_usd_from_usdc_weth_sqrt(sqrt_price) - eth) < 0.01


def test_swap_usd_prefers_the_stable_leg():
    usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    token = "0x" + "ab" * 20
    assert swap_usd(token, 10, usdc, 25, eth_usd=99999) == 25
    assert swap_usd(token, 2, weth, 0.5, eth_usd=2000) == 1000
    assert swap_usd(usdc, 25, weth, 0.01, eth_usd=2000) == 25


def test_v2_topic_is_stable():
    assert SWAP_V2.startswith("0x") and len(SWAP_V2) == 66

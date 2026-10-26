"""USD marks for swaps.

Stable-quoted swaps are priced from the stable leg, so they do not depend on
ETH. WETH-quoted swaps use one ETH/USD fix taken from the USDC/WETH 0.05%
pool at the end of the window. A constant ETH price scales every WETH-quoted
swap the same way, so a wash *percentage* computed entirely inside WETH pools
does not depend on that fix. Mixing stable and WETH pools can distort the
mix; that is recorded as a limitation, not hidden.
"""

from __future__ import annotations

from washdetect.config import STABLECOINS, WETH

Q96 = 2**96


def price_token1_per_token0(sqrt_price_x96: int, dec0: int, dec1: int) -> float:
    """Human token1 per one human token0, from a v3 sqrtPriceX96."""
    if sqrt_price_x96 <= 0:
        raise ValueError("sqrtPriceX96 must be positive")
    raw_ratio = (sqrt_price_x96 / Q96) ** 2  # token1 raw / token0 raw
    return raw_ratio * 10 ** (dec0 - dec1)


def eth_usd_from_usdc_weth_sqrt(sqrt_price_x96: int) -> float:
    """USDC per WETH for the mainnet USDC/WETH pool (token0 = USDC, 6 decimals)."""
    weth_per_usdc = price_token1_per_token0(sqrt_price_x96, 6, 18)
    if weth_per_usdc <= 0:
        raise ValueError("could not invert the USDC/WETH price")
    return 1.0 / weth_per_usdc


def swap_usd(
    token: str,
    amount_token: float,
    quote_token: str,
    amount_quote: float,
    eth_usd: float | None,
) -> float | None:
    """Dollar value of one swap, or None when neither leg is priced."""
    token = token.lower()
    quote = quote_token.lower()
    if token in STABLECOINS:
        return abs(amount_token)
    if quote in STABLECOINS:
        return abs(amount_quote)
    if eth_usd is None or eth_usd <= 0:
        return None
    if token == WETH:
        return abs(amount_token) * eth_usd
    if quote == WETH:
        return abs(amount_quote) * eth_usd
    return None


def price_is_plausible(eth_usd: float) -> bool:
    return 100.0 < eth_usd < 100_000.0

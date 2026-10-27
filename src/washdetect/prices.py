from __future__ import annotations

from statistics import median

from washdetect.abi import (
    calldata,
    decode_address_word,
    decode_reserves,
    decode_slot0_sqrt,
    decode_uint,
    is_zero,
)
from washdetect.config import (
    DAI,
    STABLECOINS,
    USDC,
    USDT,
    V2_FACTORY,
    V3_FACTORY,
    V3_FEES,
    WETH,
    ZERO_ADDRESS,
)
from washdetect.rpc import RpcClient

Q96 = 2**96


def price_token1_per_token0(sqrt_price_x96: int, dec0: int, dec1: int) -> float:
    if sqrt_price_x96 <= 0:
        raise ValueError("sqrtPriceX96 must be positive")
    raw_ratio = (sqrt_price_x96 / Q96) ** 2
    return raw_ratio * 10 ** (dec0 - dec1)


def eth_usd_from_usdc_weth_sqrt(sqrt_price_x96: int) -> float:
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
    """One ETH/USD fix scales every WETH-quoted swap equally, so a wash percentage inside those pools ignores it."""
    token = token.lower()
    quote = quote_token.lower()
    if token in STABLECOINS:
        return abs(amount_token)
    if quote in STABLECOINS:
        return abs(amount_quote)
    if eth_usd is None or eth_usd <= 0:
        return None
    if token in {WETH, ZERO_ADDRESS}:
        return abs(amount_token) * eth_usd
    if quote in {WETH, ZERO_ADDRESS}:
        return abs(amount_quote) * eth_usd
    return None


def price_is_plausible(eth_usd: float) -> bool:
    return 100.0 < eth_usd < 100_000.0


def direct_source(token: str, quote_token: str) -> str | None:
    """stable or eth when the leg can be priced without another pool."""
    token = token.lower()
    quote = quote_token.lower()
    if token in STABLECOINS or quote in STABLECOINS:
        return "stable"
    if token in {WETH, ZERO_ADDRESS} or quote in {WETH, ZERO_ADDRESS}:
        return "eth"
    return None


def other_per_quote(token1_per_token0: float, quote_is_token0: bool) -> float | None:
    if token1_per_token0 <= 0:
        return None
    if quote_is_token0:
        return token1_per_token0
    return 1.0 / token1_per_token0


def quote_usd_from_other(other_per_one_quote: float, *, other_is_stable: bool, eth_usd: float | None) -> float | None:
    if other_per_one_quote <= 0:
        return None
    if other_is_stable:
        return other_per_one_quote
    if eth_usd is None or eth_usd <= 0:
        return None
    return other_per_one_quote * eth_usd


def nearest_value(block: int, samples: list[tuple[int, float]]) -> float | None:
    if not samples:
        return None
    return min(samples, key=lambda item: (abs(item[0] - block), item[0]))[1]


def rolling_median(block: int, points: list[tuple[int, float]], radius: int = 300) -> float | None:
    """Median of prices near this block, or the single nearest price when none are close."""
    near = [price for at, price in points if abs(at - block) <= radius and price > 0]
    if near:
        return float(median(near))
    return nearest_value(block, points)


def apply_token_implied(rows: list[dict[str, object]], radius: int = 300) -> None:
    """Fill usd from the token price implied by swaps that already have one."""
    points: list[tuple[int, float]] = []
    for row in rows:
        usd = row.get("usd")
        amount = row.get("amount_token")
        if not isinstance(usd, (int, float)) or not isinstance(amount, (int, float)):
            continue
        if amount <= 0 or usd <= 0:
            continue
        price = float(usd) / float(amount)
        if 0 < price < 1e9:
            points.append((int(row["block"]), price))
    for row in rows:
        if row.get("usd") is not None:
            continue
        amount = row.get("amount_token")
        if not isinstance(amount, (int, float)) or amount <= 0 or not points:
            continue
        price = rolling_median(int(row["block"]), points, radius)
        if price is None or not 0 < price < 1e9:
            continue
        row["usd"] = abs(float(amount)) * price
        row["source"] = "token-implied"


class QuoteOracle:
    """USD per unit of a quote, from a direct WETH or stable pool, nearest in time."""

    def __init__(self, client: RpcClient) -> None:
        self.client = client
        self._pool: dict[str, tuple[str, str, str, bool] | None] = {}
        self._meta: dict[str, tuple[str, str, int, int] | None] = {}

    def samples(self, quote: str, blocks: list[int], eth_usd: float | None) -> list[tuple[int, float]]:
        quote = quote.lower()
        if quote in STABLECOINS:
            return [(block, 1.0) for block in blocks]
        if quote in {WETH, ZERO_ADDRESS}:
            if eth_usd is None:
                return []
            return [(block, eth_usd) for block in blocks]
        found = self._find(quote)
        if found is None or not blocks:
            return []
        pool, protocol, _other, other_is_stable = found
        if not other_is_stable and eth_usd is None:
            return []
        points = _sample_blocks(blocks, 8)
        out: list[tuple[int, float]] = []
        for block in points:
            price = self._at(pool, protocol, quote, other_is_stable, block, eth_usd)
            if price is not None and 0 < price < 1e9:
                out.append((block, price))
        return out

    def _find(self, quote: str) -> tuple[str, str, str, bool] | None:
        if quote in self._pool:
            return self._pool[quote]
        found = self._lookup(quote)
        self._pool[quote] = found
        return found

    def _lookup(self, quote: str) -> tuple[str, str, str, bool] | None:
        # Stable pool first so the price is already USD. WETH is the fallback.
        pairs: list[tuple[str, bool]] = [(USDC, True), (USDT, True), (DAI, True), (WETH, False)]
        for other, stable in pairs:
            for fee in V3_FEES:
                pool = _call_address(
                    self.client,
                    V3_FACTORY,
                    calldata("get_pool", ["address", "address", "uint24"], [quote, other, fee]),
                )
                if pool:
                    return pool, "v3", other, stable
        for other, stable in ((USDC, True), (USDT, True), (DAI, True), (WETH, False)):
            pool = _call_address(
                self.client,
                V2_FACTORY,
                calldata("get_pair", ["address", "address"], [quote, other]),
            )
            if pool:
                return pool, "v2", other, stable
        return None

    def _at(
        self,
        pool: str,
        protocol: str,
        quote: str,
        other_is_stable: bool,
        block: int,
        eth_usd: float | None,
    ) -> float | None:
        meta = self._tokens(pool)
        if meta is None:
            return None
        token0, token1, dec0, dec1 = meta
        quote_is_token0 = token0 == quote
        if token0 != quote and token1 != quote:
            return None
        try:
            if protocol == "v2":
                reserve0, reserve1 = decode_reserves(
                    self.client.eth_call(pool, calldata("get_reserves"), block)
                )
                if reserve0 <= 0 or reserve1 <= 0:
                    return None
                human0 = reserve0 / 10**dec0
                human1 = reserve1 / 10**dec1
                ratio = human1 / human0
            else:
                sqrt_price = decode_slot0_sqrt(self.client.eth_call(pool, calldata("slot0"), block))
                ratio = price_token1_per_token0(sqrt_price, dec0, dec1)
        except Exception:
            return None
        other = other_per_quote(ratio, quote_is_token0)
        if other is None:
            return None
        return quote_usd_from_other(other, other_is_stable=other_is_stable, eth_usd=eth_usd)

    def _tokens(self, pool: str) -> tuple[str, str, int, int] | None:
        if pool in self._meta:
            return self._meta[pool]
        try:
            token0 = decode_address_word(self.client.eth_call(pool, calldata("token0")))
            token1 = decode_address_word(self.client.eth_call(pool, calldata("token1")))
            dec0 = int(decode_uint(self.client.eth_call(token0, calldata("decimals"))))
            dec1 = int(decode_uint(self.client.eth_call(token1, calldata("decimals"))))
        except Exception:
            self._meta[pool] = None
            return None
        if is_zero(token0) or is_zero(token1):
            return None
        found = (token0, token1, dec0, dec1)
        self._meta[pool] = found
        return found


def _call_address(client: RpcClient, to: str, data: str) -> str | None:
    try:
        raw = client.eth_call(to, data)
    except Exception:
        return None
    if not raw or raw == "0x":
        return None
    address = decode_address_word(raw)
    if is_zero(address):
        return None
    return address


def _sample_blocks(blocks: list[int], cap: int) -> list[int]:
    uniq = sorted(set(blocks))
    if len(uniq) <= cap:
        return uniq
    step = (len(uniq) - 1) / (cap - 1)
    picked = [uniq[round(index * step)] for index in range(cap)]
    return sorted(set(picked))


def pricing_note(total: int, sources: list[str | None]) -> str | None:
    if total <= 0:
        return None
    labels = {
        "stable": "stable",
        "eth": "ETH",
        "quote-pool": "quote pool",
        "token-implied": "implied from other swaps",
    }
    counts: dict[str, int] = {}
    missing = 0
    for source in sources:
        if not source:
            missing += 1
            continue
        counts[source] = counts.get(source, 0) + 1
    priced = total - missing
    parts = [f"{counts[key]} {labels[key]}" for key in labels if counts.get(key)]
    detail = ", ".join(parts) if parts else "none priced from a pool"
    if missing == 0:
        return f"All {total} swaps have a USD price ({detail})."
    return (
        f"{priced} of {total} swaps have a USD price ({detail}). "
        f"{missing} have no USD price: no stable or ETH leg, no WETH or stable pool for the quote, "
        f"and no other swap in this window implied a token price. "
        f"Totals in the token's own units still include every swap."
    )

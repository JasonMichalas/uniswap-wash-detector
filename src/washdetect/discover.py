"""Find Uniswap v2 and v3 pools for one token.

Discovery is a handful of `eth_call`s: `getPair` on the v2 factory and
`getPool` on the v3 factory, against WETH, USDC, USDT, and DAI, at the four
standard v3 fee tiers. That misses pools quoted in exotic tokens. Those are
rare for the volume a wash-trading percentage cares about, and scanning
`PairCreated` from the factory's deployment block is a poor fit for public
RPC limits. `washdetect fetch --rediscover` refreshes this set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from washdetect.abi import (
    calldata,
    decode_address_word,
    decode_symbol,
    decode_uint,
    is_zero,
    normalize_address,
)
from washdetect.config import QUOTE_TOKENS, V2_FACTORY, V3_FACTORY, V3_FEES
from washdetect.rpc import RpcClient


@dataclass
class PoolMeta:
    pool_address: str
    protocol: str
    token0: str
    token1: str
    fee: int
    token0_decimals: int
    token1_decimals: int
    token0_symbol: str
    token1_symbol: str


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


def _decimals(client: RpcClient, token: str, cache: dict[str, int]) -> int:
    if token in cache:
        return cache[token]
    try:
        value = decode_uint(client.eth_call(token, calldata("decimals")))
    except Exception:
        value = 18
    cache[token] = int(value)
    return cache[token]


def _symbol(client: RpcClient, token: str, cache: dict[str, str]) -> str:
    if token in cache:
        return cache[token]
    try:
        text = decode_symbol(client.eth_call(token, calldata("symbol"))) or token[:8]
    except Exception:
        text = token[:8]
    cache[token] = text
    return text


def read_pool(client: RpcClient, pool: str, protocol: str, fee: int, meta_cache: dict[str, Any]) -> PoolMeta:
    token0 = decode_address_word(client.eth_call(pool, calldata("token0")))
    token1 = decode_address_word(client.eth_call(pool, calldata("token1")))
    if protocol == "v3":
        try:
            fee = int(decode_uint(client.eth_call(pool, calldata("fee"))))
        except Exception:
            pass
    return PoolMeta(
        pool_address=normalize_address(pool),
        protocol=protocol,
        token0=token0,
        token1=token1,
        fee=fee,
        token0_decimals=_decimals(client, token0, meta_cache.setdefault("dec", {})),
        token1_decimals=_decimals(client, token1, meta_cache.setdefault("dec", {})),
        token0_symbol=_symbol(client, token0, meta_cache.setdefault("sym", {})),
        token1_symbol=_symbol(client, token1, meta_cache.setdefault("sym", {})),
    )


def discover_pools(client: RpcClient, token: str) -> list[PoolMeta]:
    """Return every standard-quote v2 pair and v3 pool that exists for `token`."""
    token = normalize_address(token)
    found: list[PoolMeta] = []
    seen: set[str] = set()
    cache: dict[str, Any] = {}
    for quote in QUOTE_TOKENS:
        if quote == token:
            continue
        pair = _call_address(
            client,
            V2_FACTORY,
            calldata("get_pair", ["address", "address"], [token, quote]),
        )
        if pair and pair not in seen:
            seen.add(pair)
            found.append(read_pool(client, pair, "v2", 3000, cache))
        for fee in V3_FEES:
            pool = _call_address(
                client,
                V3_FACTORY,
                calldata("get_pool", ["address", "address", "uint24"], [token, quote, fee]),
            )
            if pool and pool not in seen:
                seen.add(pool)
                found.append(read_pool(client, pool, "v3", fee, cache))
    return found


def read_token_meta(client: RpcClient, token: str) -> tuple[str, int]:
    cache: dict[str, Any] = {}
    symbol = _symbol(client, token, cache.setdefault("sym", {}))
    decimals = _decimals(client, token, cache.setdefault("dec", {}))
    return symbol, decimals

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from washdetect.abi import (
    INITIALIZE_V4,
    PAIR_CREATED,
    POOL_CREATED,
    calldata,
    decode_address_word,
    decode_pair_created,
    decode_pool_created,
    decode_symbol,
    decode_uint,
    is_zero,
    normalize_address,
    topic_address,
)
from washdetect.config import (
    FORK_VENUES,
    POOL_MANAGER,
    QUOTE_TOKENS,
    V2_FACTORY,
    V2_FACTORY_BLOCK,
    V3_FACTORY,
    V3_FACTORY_BLOCK,
    V3_FEES,
    V4_START_BLOCK,
    ZERO_ADDRESS,
    protocol_family,
)
from washdetect.rpc import RpcClient
from washdetect.store import CHAIN, Store


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
    dex: str = "uniswap"


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


def read_pool(
    client: RpcClient,
    pool: str,
    protocol: str,
    fee: int,
    meta_cache: dict[str, Any],
    dex: str = "uniswap",
) -> PoolMeta:
    token0 = decode_address_word(client.eth_call(pool, calldata("token0")))
    token1 = decode_address_word(client.eth_call(pool, calldata("token1")))
    if protocol_family(protocol) == "v3":
        try:
            fee = _fit_fee(int(decode_uint(client.eth_call(pool, calldata("fee")))))
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
        dex=dex,
    )


def discover_pools(
    client: RpcClient,
    token: str,
    *,
    store: Store | None = None,
    head: int | None = None,
    progress: Callable[[str], None] | None = None,
    include_forks: bool = False,
    cancel: Callable[[], bool] | None = None,
) -> list[PoolMeta]:
    """Quote pairs plus every v2/v3 factory pair and every v4 pool touching the token."""
    token = normalize_address(token)
    say = progress or (lambda _message: None)
    if store is not None:
        if head is None:
            head = client.block_number()
        index_chain(client, store, head, say, include_forks=include_forks, cancel=cancel)
    found: list[PoolMeta] = []
    seen: set[str] = set()
    cache: dict[str, Any] = {}
    meta = client.spawn()
    meta._timeout = 10
    meta._max_attempts = 3
    try:
        return _assemble_pools(meta, client, token, store, cache, found, seen, include_forks)
    finally:
        meta.close()


def _assemble_pools(
    meta: RpcClient,
    client: RpcClient,
    token: str,
    store: Store | None,
    cache: dict[str, Any],
    found: list[PoolMeta],
    seen: set[str],
    include_forks: bool,
) -> list[PoolMeta]:
    if store is not None:
        rows = store.chain_pools_for(token)
        _prefetch_meta(meta, [str(row["token0"]) for row in rows] + [str(row["token1"]) for row in rows], cache)
        for row in rows:
            dex = str(row["dex"] or "uniswap")
            if dex != "uniswap" and not include_forks:
                continue
            address = str(row["pool_address"])
            if address in seen:
                continue
            seen.add(address)
            found.append(_meta_from_chain(meta, row, cache))
    venues: list[tuple[str, str, str]] = [("uniswap", "v2", V2_FACTORY), ("uniswap", "v3", V3_FACTORY)]
    if include_forks:
        venues.extend((dex, family, factory) for dex, family, factory, _start in FORK_VENUES)
    for dex, family, factory in venues:
        for quote in QUOTE_TOKENS:
            if quote == token:
                continue
            if family == "v2":
                pair = _call_address(client, factory, calldata("get_pair", ["address", "address"], [token, quote]))
                protocol = "v2" if dex == "uniswap" else f"{dex}-v2"
                if pair and pair not in seen:
                    seen.add(pair)
                    found.append(read_pool(meta, pair, protocol, 3000, cache, dex))
            else:
                for fee in V3_FEES:
                    pool = _call_address(
                        client, factory, calldata("get_pool", ["address", "address", "uint24"], [token, quote, fee])
                    )
                    protocol = "v3" if dex == "uniswap" else f"{dex}-v3"
                    if pool and pool not in seen:
                        seen.add(pool)
                        found.append(read_pool(meta, pool, protocol, fee, cache, dex))
    return found


def index_chain(
    client: RpcClient,
    store: Store,
    head: int,
    progress: Callable[[str], None],
    *,
    include_forks: bool,
    cancel: Callable[[], bool] | None = None,
) -> None:
    jobs: list[tuple[str, str, str, list[Any], int, Callable[[dict[str, Any]], tuple | None], int]] = [
        ("Uniswap v4 pools", "v4-initialize", POOL_MANAGER, [INITIALIZE_V4], V4_START_BLOCK, parse_v4_initialize, 8_000),
        ("Uniswap v3 pools", "v3-pools", V3_FACTORY, [POOL_CREATED], V3_FACTORY_BLOCK, parse_v3_pool, 8_000),
        ("Uniswap v2 pairs", "v2-pairs", V2_FACTORY, [PAIR_CREATED], V2_FACTORY_BLOCK, parse_v2_pair, 8_000),
    ]
    if include_forks:
        for dex, family, factory, start in FORK_VENUES:
            if family == "v2":
                jobs.append((f"{dex} v2 pairs", f"{dex}-v2-pairs", factory, [PAIR_CREATED], start, _fork_v2(dex), 8_000))
            else:
                jobs.append((f"{dex} v3 pools", f"{dex}-v3-pools", factory, [POOL_CREATED], start, _fork_v3(dex), 8_000))
    for label, dataset, address, topics, start, parse, span in jobs:
        gaps = store.missing(CHAIN, dataset, start, head)
        total = max(1, head - start + 1)
        for gap_start, gap_end in gaps:
            def on_chunk(
                frm: int,
                to: int,
                logs: list[dict[str, Any]],
                *,
                parse: Callable[[dict[str, Any]], tuple | None] = parse,
                dataset: str = dataset,
            ) -> None:
                rows = []
                for log in logs:
                    row = parse(log)
                    if row:
                        rows.append(row)
                store.upsert_chain_pools(rows)
                store.add_coverage(CHAIN, dataset, frm, to)

            def on_progress(
                at: int,
                _range_end: int,
                _count: int,
                *,
                label: str = label,
                start: int = start,
                total: int = total,
            ) -> None:
                pct = min(100, int(100 * (at - start + 1) / total))
                progress(f"Indexing {label}: block {at:,} of {head:,} ({pct}%)")

            progress(f"Indexing {label}: blocks {gap_start:,}–{gap_end:,}")
            client.get_logs(
                address=address,
                topics=topics,
                start_block=gap_start,
                end_block=gap_end,
                init_span=span,
                workers=4,
                on_chunk=on_chunk,
                on_progress=on_progress,
                cancel=cancel,
            )


def parse_v4_initialize(log: dict[str, Any]) -> tuple | None:
    topics = [str(topic).lower() for topic in (log.get("topics") or [])]
    if len(topics) < 4 or topics[0] != INITIALIZE_V4:
        return None
    pool_id = topics[1]
    if len(pool_id) != 66:
        return None
    try:
        fee, _spacing, hooks, *_rest = decode_initialize_words(str(log.get("data") or "0x"))
    except Exception:
        return None
    return (
        pool_id,
        "v4",
        "uniswap",
        topic_address(topics[2]),
        topic_address(topics[3]),
        _fit_fee(int(fee)),
        normalize_address(hooks) if hooks else ZERO_ADDRESS,
    )


def parse_v2_pair(log: dict[str, Any]) -> tuple | None:
    return _parse_created(log, PAIR_CREATED, "v2", "uniswap", decode_pair_created, fee=3000)


def parse_v3_pool(log: dict[str, Any]) -> tuple | None:
    return _parse_created(log, POOL_CREATED, "v3", "uniswap", decode_pool_created, fee_from_topic=True)


def _fork_v2(dex: str) -> Callable[[dict[str, Any]], tuple | None]:
    def parse(log: dict[str, Any]) -> tuple | None:
        return _parse_created(log, PAIR_CREATED, f"{dex}-v2", dex, decode_pair_created, fee=3000)

    return parse


def _fork_v3(dex: str) -> Callable[[dict[str, Any]], tuple | None]:
    def parse(log: dict[str, Any]) -> tuple | None:
        return _parse_created(log, POOL_CREATED, f"{dex}-v3", dex, decode_pool_created, fee_from_topic=True)

    return parse


def _parse_created(
    log: dict[str, Any],
    topic: str,
    protocol: str,
    dex: str,
    decode_pool: Callable[[str], str],
    *,
    fee: int = 0,
    fee_from_topic: bool = False,
) -> tuple | None:
    topics = [str(item).lower() for item in (log.get("topics") or [])]
    if len(topics) < 3 or topics[0] != topic:
        return None
    try:
        pool = decode_pool(str(log.get("data") or "0x"))
        if fee_from_topic:
            if len(topics) < 4:
                return None
            fee = int(topics[3], 16)
    except Exception:
        return None
    if is_zero(pool):
        return None
    fee = _fit_fee(int(fee))
    return (pool, protocol, dex, topic_address(topics[1]), topic_address(topics[2]), fee, "")


def _fit_fee(value: int) -> int:
    if 0 <= value <= 2**24:
        return value
    return 0


def decode_initialize_words(data: str) -> tuple[Any, ...]:
    from washdetect.abi import _bytes, decode

    return decode(["uint24", "int24", "address", "uint160", "int24"], _bytes(data))


def _meta_from_chain(client: RpcClient, row: dict[str, Any], cache: dict[str, Any]) -> PoolMeta:
    token0 = str(row["token0"])
    token1 = str(row["token1"])
    protocol = str(row["protocol"])
    return PoolMeta(
        pool_address=str(row["pool_address"]),
        protocol=protocol,
        token0=token0,
        token1=token1,
        fee=_fit_fee(int(row["fee"] or 0)),
        token0_decimals=_asset_decimals(client, token0, cache),
        token1_decimals=_asset_decimals(client, token1, cache),
        token0_symbol=_asset_symbol(client, token0, cache),
        token1_symbol=_asset_symbol(client, token1, cache),
        dex=str(row["dex"] or "uniswap"),
    )


def _prefetch_meta(client: RpcClient, tokens: list[str], cache: dict[str, Any]) -> None:
    """One multicall for decimals and symbols. A dead token must not stall discovery."""
    from washdetect.abi import decode_aggregate3, encode_aggregate3
    from washdetect.config import MULTICALL3

    dec = cache.setdefault("dec", {})
    sym = cache.setdefault("sym", {})
    pending: list[str] = []
    for token in tokens:
        if is_zero(token):
            dec[ZERO_ADDRESS] = 18
            sym[ZERO_ADDRESS] = "ETH"
            continue
        if token not in dec or token not in sym:
            pending.append(token)
    pending = list(dict.fromkeys(pending))
    raw_dec = bytes.fromhex(calldata("decimals").removeprefix("0x"))
    raw_sym = bytes.fromhex(calldata("symbol").removeprefix("0x"))
    for offset in range(0, len(pending), 80):
        part = pending[offset : offset + 80]
        calls = []
        for token in part:
            calls.append((token, True, raw_dec))
            calls.append((token, True, raw_sym))
        try:
            decoded = decode_aggregate3(client.eth_call(MULTICALL3, encode_aggregate3(calls)))
        except Exception:
            for token in part:
                dec.setdefault(token, 18)
                sym.setdefault(token, token[:8])
            continue
        for index, token in enumerate(part):
            ok_dec, ret_dec = decoded[index * 2] if index * 2 < len(decoded) else (False, b"")
            ok_sym, ret_sym = decoded[index * 2 + 1] if index * 2 + 1 < len(decoded) else (False, b"")
            if ok_dec and len(ret_dec) >= 32:
                value = int.from_bytes(ret_dec[-32:], "big")
                dec[token] = value if 0 <= value <= 36 else 18
            else:
                dec[token] = 18
            if ok_sym and ret_sym:
                sym[token] = decode_symbol("0x" + ret_sym.hex()) or token[:8]
            else:
                sym[token] = token[:8]


def _asset_decimals(client: RpcClient, token: str, cache: dict[str, Any]) -> int:
    if is_zero(token):
        cache.setdefault("dec", {})[ZERO_ADDRESS] = 18
        return 18
    return _decimals(client, token, cache.setdefault("dec", {}))


def _asset_symbol(client: RpcClient, token: str, cache: dict[str, Any]) -> str:
    if is_zero(token):
        cache.setdefault("sym", {})[ZERO_ADDRESS] = "ETH"
        return "ETH"
    return _symbol(client, token, cache.setdefault("sym", {}))


def read_token_meta(client: RpcClient, token: str) -> tuple[str, int]:
    cache: dict[str, Any] = {}
    symbol = _symbol(client, token, cache.setdefault("sym", {}))
    decimals = _decimals(client, token, cache.setdefault("dec", {}))
    return symbol, decimals

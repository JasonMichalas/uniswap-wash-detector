from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
    pad_address,
    topic_address,
)
from washdetect.config import (
    FORK_VENUES,
    POOL_BASES,
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


# One token's Initialize logs are sparse. The archive endpoint accepts a wide page,
# and an empty page from that endpoint is the answer. Smaller pages are what made
# a fresh token sit on "Finding pools" for several minutes.
_FILTERED_SPAN = 500_000

# v4-currency0/v4-currency1 were written when 32-byte pool ids were dropped, so a
# covered range did not mean those pools were saved. New names force one rescan.
_DIRECT_DATASETS = (
    "direct-quotes",
    "v4-init-currency0",
    "v4-init-currency1",
    "v2-token0",
    "v2-token1",
    "v3-token0",
    "v3-token1",
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
    rediscover: bool = False,
    use_v4_index: bool = False,
) -> list[PoolMeta]:
    """Pools for this one token. A full-chain factory index is not part of a load."""
    token = normalize_address(token)
    say = progress or (lambda _message: None)
    if head is None:
        head = client.block_number()
    if store is not None and rediscover:
        for dataset in _direct_datasets(include_forks):
            store.clear_coverage(token, dataset)
    found: list[PoolMeta] = []
    seen: set[str] = set()
    cache: dict[str, Any] = {}
    meta = client.spawn()
    meta._timeout = 10
    meta._max_attempts = 3
    indexed_v4 = bool(
        use_v4_index
        and store is not None
        and _index_covers(store, "v4-initialize", V4_START_BLOCK, head)
    )
    indexed_factories = bool(
        use_v4_index
        and store is not None
        and _index_covers(store, "v2-pairs", V2_FACTORY_BLOCK, head)
        and _index_covers(store, "v3-pools", V3_FACTORY_BLOCK, head)
    )
    try:
        say("Finding pools")
        # A batch refreshes quote pools with getPair/getPool. The saved list can
        # predate the v4 index, so it is not the whole answer in that mode.
        if (
            store is not None
            and not use_v4_index
            and not rediscover
            and store.range_covered(token, "direct-quotes", 0, 0)
        ):
            _take_saved(store, token, found, seen)
        else:
            _quote_pairs(meta, token, include_forks, found, seen, cache)
            if store is not None:
                _save_found(store, token, found)
                store.add_coverage(token, "direct-quotes", 0, 0)
        if indexed_v4:
            _take_indexed(store, meta, token, found, seen, cache, {"v4"}, include_forks)
        else:
            _v4_pools(client, meta, token, head, store, found, seen, cache, say, cancel)
        if indexed_factories:
            _take_indexed(store, meta, token, found, seen, cache, {"v2", "v3"}, include_forks)
        elif not any(protocol_family(pool.protocol) in {"v2", "v3"} for pool in found):
            # PairCreated/PoolCreated from the factory start is only for a token that
            # has no pool against WETH, the stables, WBTC, USDe, or USDS.
            _odd_quote_pools(client, meta, token, head, store, include_forks, found, seen, cache, say, cancel)
        if store is not None:
            _save_found(store, token, found)
        say(f"Found {len(found)} pools")
        return found
    finally:
        meta.close()


def _direct_datasets(include_forks: bool) -> tuple[str, ...]:
    names = list(_DIRECT_DATASETS)
    if include_forks:
        for dex, family, _factory, _start in FORK_VENUES:
            names.append(f"{dex}-{family}-token0")
            names.append(f"{dex}-{family}-token1")
    return tuple(names)


def _take_saved(store: Store, token: str, found: list[PoolMeta], seen: set[str]) -> None:
    for row in store.pools_for(token):
        pool = str(row["pool_address"])
        if pool in seen:
            continue
        seen.add(pool)
        found.append(_meta_from_saved(row))


def _meta_from_saved(row: dict[str, Any]) -> PoolMeta:
    protocol = str(row["protocol"])
    return PoolMeta(
        pool_address=str(row["pool_address"]),
        protocol=protocol,
        token0=str(row["token0"]),
        token1=str(row["token1"]),
        fee=_fit_fee(int(row["fee"] or 0)),
        token0_decimals=int(row["token0_decimals"] or 18),
        token1_decimals=int(row["token1_decimals"] or 18),
        token0_symbol=str(row["token0_symbol"] or ""),
        token1_symbol=str(row["token1_symbol"] or ""),
        dex=str(row.get("dex") or "") or _dex_of(protocol),
    )


def _dex_of(protocol: str) -> str:
    if protocol in {"v2", "v3", "v4"}:
        return "uniswap"
    return protocol.split("-", 1)[0] or "uniswap"


def _save_found(store: Store, token: str, found: list[PoolMeta]) -> None:
    if not found:
        return
    store.save_pools(
        [
            (
                token,
                pool.pool_address,
                pool.protocol,
                pool.token0,
                pool.token1,
                pool.fee,
                pool.token0_decimals,
                pool.token1_decimals,
                pool.token0_symbol,
                pool.token1_symbol,
            )
            for pool in found
        ]
    )


def _quote_venues(include_forks: bool) -> list[tuple[str, str, str]]:
    venues = [("uniswap", "v2", V2_FACTORY), ("uniswap", "v3", V3_FACTORY)]
    if include_forks:
        venues.extend((dex, family, factory) for dex, family, factory, _start in FORK_VENUES)
    return venues


def _quote_pairs(
    client: RpcClient,
    token: str,
    include_forks: bool,
    found: list[PoolMeta],
    seen: set[str],
    cache: dict[str, Any],
) -> None:
    pending: list[tuple[str, str, int, str, str]] = []
    calls: list[tuple[str, list[Any]]] = []
    for dex, family, factory in _quote_venues(include_forks):
        for base in POOL_BASES:
            if base == token:
                continue
            if family == "v2":
                data = calldata("get_pair", ["address", "address"], [token, base])
                protocol = "v2" if dex == "uniswap" else f"{dex}-v2"
                pending.append((dex, protocol, 3000, factory, data))
                calls.append(("eth_call", [{"to": factory, "data": data}, "latest"]))
            else:
                for fee in V3_FEES:
                    data = calldata("get_pool", ["address", "address", "uint24"], [token, base, fee])
                    protocol = "v3" if dex == "uniswap" else f"{dex}-v3"
                    pending.append((dex, protocol, fee, factory, data))
                    calls.append(("eth_call", [{"to": factory, "data": data}, "latest"]))
    results = client.batch(calls, batch_size=25) if calls else []
    for (dex, protocol, fee, _factory, _data), raw in zip(pending, results, strict=False):
        if not isinstance(raw, str):
            continue
        pool = decode_address_word(raw)
        if is_zero(pool) or pool in seen:
            continue
        try:
            meta = read_pool(client, pool, protocol, fee, cache, dex)
        except Exception:
            continue
        seen.add(pool)
        found.append(meta)


def _v4_pools(
    client: RpcClient,
    meta: RpcClient,
    token: str,
    head: int,
    store: Store | None,
    found: list[PoolMeta],
    seen: set[str],
    cache: dict[str, Any],
    progress: Callable[[str], None],
    cancel: Callable[[], bool] | None,
) -> None:
    # Initialize(id, currency0, currency1) indexes the pool id first, then the two currencies.
    padded = pad_address(token)
    _topic_scan(
        client,
        meta,
        token,
        head,
        store,
        found,
        seen,
        cache,
        progress,
        cancel,
        address=POOL_MANAGER,
        topic0=INITIALIZE_V4,
        start_block=V4_START_BLOCK,
        datasets=("v4-init-currency0", "v4-init-currency1"),
        positions=(2, 3),
        parse=parse_v4_initialize,
        token_topic=padded,
    )


def _odd_quote_pools(
    client: RpcClient,
    meta: RpcClient,
    token: str,
    head: int,
    store: Store | None,
    include_forks: bool,
    found: list[PoolMeta],
    seen: set[str],
    cache: dict[str, Any],
    progress: Callable[[str], None],
    cancel: Callable[[], bool] | None,
) -> None:
    """PairCreated/PoolCreated filtered by this token, for a quote that is not in the base list."""
    padded = pad_address(token)
    jobs: list[tuple] = [
        (V2_FACTORY, PAIR_CREATED, V2_FACTORY_BLOCK, ("v2-token0", "v2-token1"), parse_v2_pair),
        (V3_FACTORY, POOL_CREATED, V3_FACTORY_BLOCK, ("v3-token0", "v3-token1"), parse_v3_pool),
    ]
    if include_forks:
        for dex, family, factory, start in FORK_VENUES:
            names = (f"{dex}-{family}-token0", f"{dex}-{family}-token1")
            if family == "v2":
                jobs.append((factory, PAIR_CREATED, start, names, _fork_v2(dex)))
            else:
                jobs.append((factory, POOL_CREATED, start, names, _fork_v3(dex)))
    for address, topic, start, datasets, parse in jobs:
        _topic_scan(
            client,
            meta,
            token,
            head,
            store,
            found,
            seen,
            cache,
            progress,
            cancel,
            address=address,
            topic0=topic,
            start_block=start,
            datasets=datasets,
            positions=(1, 2),
            parse=parse,
            token_topic=padded,
        )


class _PoolScan:
    """Progress across every filter of one token, from the first block to the last."""

    def __init__(self, start: int, head: int, parts: int, say: Callable[[str], None]) -> None:
        self.start = start
        self.head = head
        self.parts = max(1, parts)
        self.say = say
        self.width = max(1, head - start + 1)
        self.done = [0] * self.parts
        self.shown = -1
        self.lock = threading.Lock()

    def seed(self, index: int, blocks: int) -> None:
        self._set(index, blocks, self.start)

    def advance(self, index: int, at: int) -> None:
        self._set(index, at - self.start + 1, at)

    def _set(self, index: int, blocks: int, chain: int) -> None:
        with self.lock:
            self.done[index] = max(0, min(self.width, max(self.done[index], blocks)))
            scanned = sum(self.done)
            if scanned < self.shown:
                return
            self.shown = scanned
            total = self.width * self.parts
            message = f"Finding pools · blocks {scanned:,} of {total:,} · chain {chain:,}"
            self.say(message)


def _topic_scan(
    client: RpcClient,
    meta: RpcClient,
    token: str,
    head: int,
    store: Store | None,
    found: list[PoolMeta],
    seen: set[str],
    cache: dict[str, Any],
    progress: Callable[[str], None],
    cancel: Callable[[], bool] | None,
    *,
    address: str,
    topic0: str,
    start_block: int,
    datasets: tuple[str, str],
    positions: tuple[int, int],
    parse: Callable[[dict[str, Any]], tuple | None],
    token_topic: str,
) -> None:
    if head < start_block:
        return
    filters = []
    for dataset, position in zip(datasets, positions, strict=True):
        topics: list[Any] = [topic0, *([None] * position)]
        topics[position] = token_topic
        filters.append((dataset, topics))
    scan = _PoolScan(start_block, head, len(filters), progress)
    # Two currency filters at once. Each one still reads one chunk at a time on the
    # archive endpoint: several chunks on that endpoint have come back empty for
    # ranges that do contain logs. Other public RPCs cap the width or omit history.
    parallel = len(filters) > 1 and getattr(client, "_transport", None) is None

    def run(index: int, dataset: str, topics: list[Any]) -> list[tuple]:
        gaps = [(start_block, head)]
        if store is not None:
            gaps = store.missing(token, dataset, start_block, head)
        width = head - start_block + 1
        missing = sum(end - begin + 1 for begin, end in gaps)
        scan.seed(index, width - missing)
        rows: list[tuple] = []
        for gap_start, gap_end in gaps:

            def on_chunk(
                frm: int,
                to: int,
                logs: list[dict[str, Any]],
                *,
                parse: Callable[[dict[str, Any]], tuple | None] = parse,
                dataset: str = dataset,
                rows: list[tuple] = rows,
            ) -> None:
                parsed = []
                for log in logs:
                    row = parse(log)
                    if row:
                        parsed.append(row)
                with scan.lock:
                    rows.extend(parsed)
                if store is not None:
                    store.add_coverage(token, dataset, frm, to)

            def on_progress(at: int, _range_end: int, _count: int, *, index: int = index) -> None:
                scan.advance(index, at)

            logs_client = client.spawn()
            archive = [url for url in client.endpoints if "mevblocker" in url]
            if archive:
                logs_client.endpoints = archive
            try:
                logs_client.get_logs(
                    address=address,
                    topics=topics,
                    start_block=gap_start,
                    end_block=gap_end,
                    init_span=_FILTERED_SPAN,
                    workers=1,
                    on_chunk=on_chunk,
                    on_progress=on_progress,
                    cancel=cancel,
                    empty_limit=_FILTERED_SPAN,
                )
            finally:
                logs_client.close()
        return rows

    def run_one(item: tuple[int, tuple[str, list[Any]]]) -> list[tuple]:
        index, pair = item
        dataset, topics = pair
        return run(index, dataset, topics)

    if parallel:
        with ThreadPoolExecutor(max_workers=len(filters)) as pool:
            batches = list(pool.map(run_one, list(enumerate(filters))))
    else:
        batches = [run_one(item) for item in enumerate(filters)]
    for rows in batches:
        _add_rows(meta, rows, found, seen, cache)


def _add_rows(
    client: RpcClient,
    rows: list[tuple],
    found: list[PoolMeta],
    seen: set[str],
    cache: dict[str, Any],
) -> None:
    fresh = []
    for row in rows:
        pool = str(row[0])
        # A v4 pool id is 32 bytes. is_zero() only accepts 20-byte addresses, so it
        # would drop every Initialize result.
        if pool in seen or not pool.removeprefix("0x").strip("0"):
            continue
        seen.add(pool)
        fresh.append(row)
    if not fresh:
        return
    _prefetch_meta(client, [str(row[3]) for row in fresh] + [str(row[4]) for row in fresh], cache)
    for row in fresh:
        found.append(
            _meta_from_chain(
                client,
                {
                    "pool_address": str(row[0]),
                    "protocol": str(row[1]),
                    "dex": str(row[2]),
                    "token0": str(row[3]),
                    "token1": str(row[4]),
                    "fee": row[5],
                },
                cache,
            )
        )


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


def _index_covers(store: Store, dataset: str, start: int, head: int) -> bool:
    """True when the shared index reaches head, or only a short tip is still open.

    Head moves while a token loads. A gap of a few thousand blocks must not
    send the next token back through a full Initialize scan.
    """
    if store.range_covered(CHAIN, dataset, start, head):
        return True
    gaps = store.missing(CHAIN, dataset, start, head)
    if len(gaps) != 1:
        return False
    gap_start, _gap_end = gaps[0]
    return gap_start > start and head - gap_start + 1 <= 8_000


def _take_indexed(
    store: Store,
    meta: RpcClient,
    token: str,
    found: list[PoolMeta],
    seen: set[str],
    cache: dict[str, Any],
    families: set[str],
    include_forks: bool,
) -> None:
    rows = []
    for row in store.chain_pools_for(token):
        family = protocol_family(str(row["protocol"]))
        if family not in families:
            continue
        dex = str(row.get("dex") or "uniswap")
        if dex != "uniswap" and not include_forks:
            continue
        rows.append(
            (
                str(row["pool_address"]),
                str(row["protocol"]),
                dex,
                str(row["token0"]),
                str(row["token1"]),
                row["fee"],
                str(row.get("hooks") or ""),
            )
        )
    _add_rows(meta, rows, found, seen, cache)


def _archive_client(client: RpcClient) -> RpcClient:
    logs = client.spawn()
    archive = [url for url in client.endpoints if "mevblocker" in url]
    if archive:
        logs.endpoints = archive
    return logs


def _scan_coverage(
    client: RpcClient,
    store: Store,
    head: int,
    progress: Callable[[str], None],
    cancel: Callable[[], bool] | None,
    *,
    label: str,
    dataset: str,
    address: str,
    topics: list[Any],
    start: int,
    parse: Callable[[dict[str, Any]], tuple | None],
    span: int,
    workers: int,
) -> None:
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
            workers=workers,
            on_chunk=on_chunk,
            on_progress=on_progress,
            cancel=cancel,
        )


def index_v4(
    client: RpcClient,
    store: Store,
    head: int,
    progress: Callable[[str], None],
    cancel: Callable[[], bool] | None = None,
) -> None:
    """One Initialize scan for the whole batch. Each token then looks its pools up."""
    logs = _archive_client(client)
    try:
        _scan_coverage(
            logs,
            store,
            head,
            progress,
            cancel,
            label="Uniswap v4 pools",
            dataset="v4-initialize",
            address=POOL_MANAGER,
            topics=[INITIALIZE_V4],
            start=V4_START_BLOCK,
            parse=parse_v4_initialize,
            span=8_000,
            workers=1,
        )
    finally:
        logs.close()


def catch_up_factory_tips(
    client: RpcClient,
    store: Store,
    head: int,
    progress: Callable[[str], None],
    cancel: Callable[[], bool] | None = None,
) -> None:
    """Extend a v2/v3 index that already exists. A missing index is not started."""
    jobs = [
        ("Uniswap v3 pools", "v3-pools", V3_FACTORY, [POOL_CREATED], V3_FACTORY_BLOCK, parse_v3_pool),
        ("Uniswap v2 pairs", "v2-pairs", V2_FACTORY, [PAIR_CREATED], V2_FACTORY_BLOCK, parse_v2_pair),
    ]
    logs = _archive_client(client)
    try:
        for label, dataset, address, topics, start, parse in jobs:
            if not store.coverage(CHAIN, dataset):
                continue
            gaps = store.missing(CHAIN, dataset, start, head)
            if any(end - begin + 1 > 80_000 for begin, end in gaps):
                continue
            _scan_coverage(
                logs,
                store,
                head,
                progress,
                cancel,
                label=label,
                dataset=dataset,
                address=address,
                topics=topics,
                start=start,
                parse=parse,
                span=8_000,
                workers=1,
            )
    finally:
        logs.close()


def index_chain(
    client: RpcClient,
    store: Store,
    head: int,
    progress: Callable[[str], None],
    *,
    include_forks: bool,
    cancel: Callable[[], bool] | None = None,
) -> None:
    """Optional chain-wide index. A single-token load does not call this."""
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
        _scan_coverage(
            client,
            store,
            head,
            progress,
            cancel,
            label=label,
            dataset=dataset,
            address=address,
            topics=topics,
            start=start,
            parse=parse,
            span=span,
            workers=4,
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

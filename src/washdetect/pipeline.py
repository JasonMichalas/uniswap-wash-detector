from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from washdetect.abi import (
    SWAP_V2,
    SWAP_V3,
    SWAP_V4,
    TRANSFER,
    calldata,
    decode_slot0_sqrt,
    decode_transfer_value,
    decode_uint,
    encode_aggregate3,
    encode_balance_of,
    human_amount,
    normalize_address,
    topic_address,
)
from washdetect.config import (
    BALANCE_CAP,
    MULTICALL3,
    POOL_MANAGER,
    ROUTERS,
    WETH_USDC_POOL,
    protocol_family,
)
from washdetect.discover import PoolMeta, discover_pools, read_token_meta
from washdetect.logs import (
    _hex_int,
    _log_ts,
    _pool_topics,
    _swap_tuple,
    decode_pool_log,
    fetch_timestamps,
)
from washdetect.prices import eth_usd_from_usdc_weth_sqrt, price_is_plausible, swap_usd
from washdetect.rpc import RpcClient, RpcError
from washdetect.store import Store, pool_log_gaps
from washdetect.subgraph import SubgraphError, api_key, fetch_swaps, map_subgraph_swap
from washdetect.window import _naive, _unix, resolve_window

Progress = Callable[[str], None]


@dataclass
class LoadReport:
    token: str
    symbol: str
    decimals: int
    start_block: int
    end_block: int
    start_time: str
    end_time: str
    pools: int
    v2_pools: int
    v3_pools: int
    swaps: int
    transfers: int
    priced_swaps: int
    balance_wallets: int
    balances_truncated: bool
    blocks_fetched: int
    blocks_cached: int
    seconds: float
    eth_usd: float | None
    used_subgraph: bool
    v4_pools: int
    swap_logs: int
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "symbol": self.symbol,
            "decimals": self.decimals,
            "start_block": self.start_block,
            "end_block": self.end_block,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "pools": self.pools,
            "v2_pools": self.v2_pools,
            "v3_pools": self.v3_pools,
            "v4_pools": self.v4_pools,
            "swap_logs": self.swap_logs,
            "swaps": self.swaps,
            "transfers": self.transfers,
            "priced_swaps": self.priced_swaps,
            "balance_wallets": self.balance_wallets,
            "balances_truncated": self.balances_truncated,
            "blocks_fetched": self.blocks_fetched,
            "blocks_cached": self.blocks_cached,
            "seconds": round(self.seconds, 2),
            "eth_usd": self.eth_usd,
            "used_subgraph": self.used_subgraph,
            "notes": self.notes,
            "analysis_ran": False,
        }


def fetch_balances(
    client: RpcClient, token: str, wallets: list[str], block: int, decimals: int
) -> dict[str, float]:
    found: dict[str, float] = {}
    step = 150
    for offset in range(0, len(wallets), step):
        part = wallets[offset : offset + step]
        calls = [(token, True, encode_balance_of(wallet)) for wallet in part]
        try:
            raw = client.eth_call(MULTICALL3, encode_aggregate3(calls), block)
            from washdetect.abi import decode_aggregate3

            decoded = decode_aggregate3(raw)
        except Exception:
            for wallet in part:
                try:
                    word = client.eth_call(token, "0x" + encode_balance_of(wallet).hex(), block)
                    found[wallet] = human_amount(decode_uint(word), decimals)
                except Exception:
                    continue
            continue
        for wallet, (ok, ret) in zip(part, decoded, strict=False):
            if not ok or len(ret) < 32:
                continue
            found[wallet] = human_amount(int.from_bytes(ret[-32:], "big"), decimals)
    return found


def _rebuild_daily(
    store: Store,
    token: str,
    balances: dict[str, float],
    start: int,
    end: int,
    end_ts: datetime,
) -> None:
    frame = store.read_transfers(token, start, end)
    running: dict[str, float] = defaultdict(float)
    daily: dict[tuple[str, str], tuple[int, float]] = {}
    if not frame.empty:
        for row in frame.itertuples(index=False):
            amount = float(row.amount or 0)
            block = int(row.block_number)
            ts = row.block_timestamp
            day = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else str(ts)[:10]
            src = str(row.from_address or "")
            dst = str(row.to_address or "")
            if src in balances:
                running[src] -= amount
                daily[(src, day)] = (block, running[src])
            if dst in balances:
                running[dst] += amount
                daily[(dst, day)] = (block, running[dst])
    end_day = end_ts.strftime("%Y-%m-%d")
    rows: list[tuple[Any, ...]] = []
    seen: set[tuple[str, str]] = set()
    for (wallet, day), (block, net) in daily.items():
        absolute = balances[wallet] - running.get(wallet, 0.0) + net
        rows.append((token, wallet, day, block, absolute))
        seen.add((wallet, day))
    for wallet, end_balance in balances.items():
        if (wallet, end_day) not in seen:
            rows.append((token, wallet, end_day, end, end_balance))
    store.replace_daily_balances(token, rows)


def _gap_blocks(gaps: list[tuple[int, int]]) -> int:
    return sum(end - start + 1 for start, end in gaps)


_BLOCK_OF = re.compile(r"blocks? ([\d,]+) of ([\d,]+)")
_CHAIN_AT = re.compile(r"\bchain ([\d,]+)")
_PHASE_NOISE = re.compile(r":?\s*blocks? [\d,]+ of [\d,]+(?:\s*\(\d+%\))?")
_EMBEDDED_PCT = re.compile(r"\s*\(\d+%\)")
_SWAP_TOPICS = {topic.lower() for topic in (SWAP_V2, SWAP_V3, SWAP_V4)}


def _eta(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def format_load_line(
    *,
    phase: str,
    at: int,
    end: int,
    swaps: int,
    transfers: int,
    elapsed: float,
    network: int,
    cached: int,
    show_blocks: bool = True,
    swaps_started: bool = False,
    transfers_started: bool = False,
    chain: int | None = None,
) -> str:
    """One status line. The phase does not carry its own percentage."""
    swap_bit = f"{swaps:,} swaps found" if swaps_started else "swaps not started yet"
    transfer_bit = f"{transfers:,} transfers found" if transfers_started else "transfers not started yet"
    counts = f"{swap_bit} · {transfer_bit}"
    tail = f"{elapsed:.0f}s elapsed · {network:,} blocks from network, {cached:,} cached"
    if not show_blocks or end <= 0:
        return f"{phase} · {counts} · {tail}"
    width = max(1, end)
    pct = min(100, max(0, int(100 * at / width)))
    extra = f" · chain {chain:,}" if chain else ""
    eta = ""
    if phase.startswith("Finding pools") and elapsed >= 4 and 1 <= pct <= 99:
        eta = f" · about {_eta(elapsed * (100 - pct) / pct)} left"
    return f"{phase} · block {at:,} of {end:,} ({pct}%){extra}{eta} · {counts} · {tail}"


def render_progress(
    message: str,
    stats: dict[str, int],
    *,
    elapsed: float,
    network: int,
    cached: int,
) -> str:
    """Turn a loader note into one phase and, when the note has a range, one percentage."""
    found = _BLOCK_OF.search(message)
    chain_at = _CHAIN_AT.search(message)
    if found:
        stats["at"] = int(found.group(1).replace(",", ""))
        stats["end"] = int(found.group(2).replace(",", ""))
    phase = _EMBEDDED_PCT.sub("", _PHASE_NOISE.sub("", message.split(" · ")[0])).strip(" :") or "Working"
    if phase.startswith("Reading swaps"):
        stats["swaps_started"] = 1
    if phase.startswith("Reading transfers"):
        stats["transfers_started"] = 1
    return format_load_line(
        phase=phase,
        at=stats["at"],
        end=stats["end"],
        swaps=stats["swaps"],
        transfers=stats["transfers"],
        elapsed=elapsed,
        network=network,
        cached=cached,
        show_blocks=found is not None,
        swaps_started=bool(stats.get("swaps_started")),
        transfers_started=bool(stats.get("transfers_started")),
        chain=int(chain_at.group(1).replace(",", "")) if chain_at else None,
    )


def _count_swap_logs(logs: list[dict[str, Any]]) -> int:
    found = 0
    for log in logs:
        topics = log.get("topics") or []
        if topics and str(topics[0]).lower() in _SWAP_TOPICS:
            found += 1
    return found


def load_token(
    token: str,
    *,
    days: int | None = None,
    start: str | None = None,
    end: str | None = None,
    db_path: str = "data/washdetect.duckdb",
    progress: Progress | None = None,
    client: RpcClient | None = None,
    store: Store | None = None,
    rediscover: bool = False,
    include_forks: bool = False,
    cancel: Callable[[], bool] | None = None,
    hours: int | None = None,
    use_v4_index: bool = False,
) -> LoadReport:
    """Fills the cache only. A wash score is a separate step."""
    raw_progress = progress or (lambda _msg: None)
    started = time.perf_counter()
    stats: dict[str, int] = {
        "at": 0,
        "end": 0,
        "swaps": 0,
        "transfers": 0,
        "network": 0,
        "cached": 0,
    }
    def say(message: str) -> None:
        raw_progress(
            render_progress(
                message,
                stats,
                elapsed=time.perf_counter() - started,
                network=stats["network"],
                cached=stats["cached"],
            )
        )

    token = normalize_address(token)
    own_client = client is None
    client = client or RpcClient()
    own_store = store is None
    store = store or Store(db_path)
    blocks_fetched = 0
    blocks_cached = 0
    swap_logs = 0
    used_subgraph = False
    stop = cancel or (lambda: False)
    try:
        say("Resolving the block window")
        start_block, end_block, start_ts, end_ts = resolve_window(
            client, days=days, start=start, end=end, hours=hours
        )
        stats["end"] = end_block
        stats["at"] = start_block
        head_ts = end_ts
        span_blocks = end_block - start_block + 1
        say(
            f"Window {start_ts:%Y-%m-%d %H:%M} → {end_ts:%Y-%m-%d %H:%M} "
            f"(blocks {start_block:,}–{end_block:,})"
        )
        symbol, decimals = read_token_meta(client, token)
        store.upsert_token(token, symbol, decimals)
        say(f"Token {symbol} ({decimals} decimals)")

        pools = discover_pools(
            client,
            token,
            store=store,
            head=end_block,
            progress=say,
            include_forks=include_forks,
            cancel=stop,
            rediscover=rediscover,
            use_v4_index=use_v4_index,
        )
        if not pools:
            raise RpcError(
                "No Uniswap v2, v3, or v4 pool was found for this token, "
                "including token-token pairs and native ETH. "
                "It may only trade on a DEX this tool does not read."
            )
        store.save_pools(
            [
                (
                    token, p.pool_address, p.protocol, p.token0, p.token1, p.fee,
                    p.token0_decimals, p.token1_decimals, p.token0_symbol, p.token1_symbol,
                )
                for p in pools
            ]
        )
        say(f"Found {len(pools)} pools")

        if api_key():
            try:
                say("Trying the Uniswap subgraph for swaps")
                _fill_swaps_from_subgraph(
                    store, token, pools, start_block, end_block, start_ts, say
                )
                used_subgraph = True
            except (SubgraphError, Exception) as exc:
                client.note(f"subgraph skipped: {exc}")
                say(f"Subgraph unavailable ({exc}). Using RPC for swaps.")

        fetched, cached, swap_logs = _fetch_swap_groups(
            store, client, token, pools, start_block, end_block, span_blocks, head_ts, say, stop, stats
        )
        blocks_fetched += fetched
        blocks_cached += cached
        stats["cached"] = blocks_cached
        stats["network"] = blocks_fetched

        transfer_gaps = store.missing(token, "transfers", start_block, end_block)
        transfer_cached = span_blocks - _gap_blocks(transfer_gaps)
        blocks_cached += transfer_cached
        stats["cached"] = blocks_cached
        for gap_start, gap_end in transfer_gaps:
            def on_progress(at: int, _end: int, count: int, *, gap_end: int = gap_end) -> None:
                say(f"Reading transfers: blocks {at:,} of {end_block:,}")

            def on_transfer_chunk(frm: int, to: int, chunk: list[dict[str, Any]], *, gap_end: int = gap_end) -> None:
                _keep_transfers(store, client, token, decimals, chunk, end_block, head_ts, frm, to)
                stats["transfers"] += len(chunk)
                stats["at"] = to

            say(f"Reading transfers: blocks {gap_start:,} of {end_block:,}")
            client.get_logs(
                address=token,
                topics=[TRANSFER],
                start_block=gap_start,
                end_block=gap_end,
                workers=4,
                on_progress=on_progress,
                on_chunk=on_transfer_chunk,
                cancel=stop,
            )
            blocks_fetched += gap_end - gap_start + 1
            stats["network"] = blocks_fetched
            stats["cached"] = blocks_cached

        say("Resolving traders from transaction senders")
        _resolve_traders(store, client, token, start_block, end_block, say)

        say("Pricing swaps")
        eth_usd = _price_swaps(store, client, token, start_block, end_block, say)

        say("Reading balances for the most active wallets")
        wallets = _candidate_wallets(store, token, start_block, end_block)
        truncated = len(wallets) >= BALANCE_CAP
        balances: dict[str, float] = {}
        balance_block = end_block
        try:
            balances = fetch_balances(client, token, wallets, end_block, decimals)
        except Exception as exc:
            client.note(f"balanceOf at block {end_block} failed ({exc}); tried latest")
            balances = fetch_balances(client, token, wallets, client.block_number(), decimals)
            balance_block = client.block_number()
        store.save_balances(token, balance_block, list(balances.items()))
        _rebuild_daily(store, token, balances, start_block, end_block, end_ts)

        report = LoadReport(
            token=token,
            symbol=symbol,
            decimals=decimals,
            start_block=start_block,
            end_block=end_block,
            start_time=start_ts.isoformat(timespec="minutes"),
            end_time=end_ts.isoformat(timespec="minutes"),
            pools=len(pools),
            v2_pools=sum(1 for p in pools if protocol_family(p.protocol) == "v2"),
            v3_pools=sum(1 for p in pools if protocol_family(p.protocol) == "v3"),
            swaps=store.count_swaps(token, start_block, end_block),
            transfers=store.count_transfers(token, start_block, end_block),
            priced_swaps=store.priced_swap_count(token, start_block, end_block),
            balance_wallets=len(balances),
            balances_truncated=truncated,
            blocks_fetched=blocks_fetched,
            blocks_cached=max(0, blocks_cached),
            seconds=time.perf_counter() - started,
            eth_usd=eth_usd,
            used_subgraph=used_subgraph,
            v4_pools=sum(1 for p in pools if protocol_family(p.protocol) == "v4"),
            swap_logs=swap_logs,
            notes=_load_notes(client.notes, pools),
        )
        if report.swaps == 0:
            # The whole window was checked. blocks_fetched is only the part that was not cached.
            report.notes.insert(0, _empty_window_note(token, start_ts, report.swap_logs, span_blocks))
        say(
            f"Cached {report.swaps:,} swaps and {report.transfers:,} transfers "
            f"in {report.seconds:.1f}s. No analysis was run."
        )
        return report
    finally:
        if own_client:
            client.close()
        if own_store:
            store.close()


def _window_pct(start: int, end: int, at: int) -> int:
    width = max(1, end - start + 1)
    return min(100, max(0, int(100 * (at - start + 1) / width)))


def _fetch_swap_groups(
    store: Store,
    client: RpcClient,
    token: str,
    pools: list[PoolMeta],
    start_block: int,
    end_block: int,
    span_blocks: int,
    head_ts: datetime,
    say: Progress,
    cancel: Callable[[], bool],
    stats: dict[str, int] | None = None,
) -> tuple[int, int, int]:
    pending: list[tuple[PoolMeta, tuple[tuple[int, int], ...]]] = []
    cached = 0
    have = store.coverage_by_dataset(token)
    for pool in pools:
        gaps = tuple(pool_log_gaps(have, pool.pool_address, start_block, end_block))
        cached += span_blocks - _gap_blocks(list(gaps))
        if gaps:
            pending.append((pool, gaps))
    groups: dict[tuple[tuple[int, int], ...], list[PoolMeta]] = defaultdict(list)
    for pool, gaps in pending:
        groups[gaps].append(pool)
    if stats is not None:
        stats["cached"] = cached
    fetched = 0
    raw = 0
    for gaps, group in groups.items():
        for gap_start, gap_end in gaps:
            got_raw, got_blocks = _fetch_one_gap(
                store,
                client,
                token,
                group,
                gap_start,
                gap_end,
                start_block,
                end_block,
                head_ts,
                say,
                cancel,
                stats,
            )
            raw += got_raw
            fetched += got_blocks
    return fetched, cached, raw


# One PoolManager eth_getLogs for a short window is a few thousand logs. Past this
# many pools, filtering eight ids at a time costs more round trips than reading them all.
_V4_BULK_AT = 8
_ADDRESS_BATCH = 400


def _use_v4_bulk(pool_count: int, width: int) -> bool:
    if pool_count <= _V4_BULK_AT:
        return False
    filtered = (pool_count + _V4_BULK_AT - 1) // _V4_BULK_AT * max(1, (width + 3_999) // 4_000)
    # About 15 v4 logs per block on mainnet, and a page caps near 8,000 logs.
    bulk = max(1, (width + 399) // 400)
    return bulk < filtered


def _too_many_addresses(message: str) -> bool:
    text = message.lower()
    return "address" in text and any(hint in text for hint in ("maximum", "too many", "limit", "allowed", "exceed"))


def _event_topics(chunk: list[PoolMeta]) -> list[str]:
    topics: list[str] = []
    for pool in chunk:
        for topic in _pool_topics(pool.protocol)[0]:
            if topic not in topics:
                topics.append(topic)
    return topics


def _fetch_one_gap(
    store: Store,
    client: RpcClient,
    token: str,
    group: list[PoolMeta],
    gap_start: int,
    gap_end: int,
    start_block: int,
    end_block: int,
    head_ts: datetime,
    say: Progress,
    cancel: Callable[[], bool],
    stats: dict[str, int] | None = None,
) -> tuple[int, int]:
    raw = 0
    v4 = [pool for pool in group if protocol_family(pool.protocol) == "v4"]
    rest = [pool for pool in group if protocol_family(pool.protocol) != "v4"]

    def consume(logs: list[dict[str, Any]], pools_in_call: list[PoolMeta], frm: int, to: int) -> None:
        nonlocal raw
        by_addr = {pool.pool_address: pool for pool in pools_in_call}
        by_id = {pool.pool_address.lower(): pool for pool in pools_in_call}
        bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for log in logs:
            pool = _match_pool(log, by_addr, by_id)
            if pool is not None:
                bucket[pool.pool_address].append(log)
        raw += sum(len(rows) for rows in bucket.values())
        if stats is not None:
            stats["swaps"] += _count_swap_logs([log for rows in bucket.values() for log in rows])
        for address, rows in bucket.items():
            _store_pool_logs(
                store, client, token, by_addr[address], rows, end_block, head_ts, allow_rpc=False
            )
        spans = []
        for pool in pools_in_call:
            spans.append((token, f"swaps:{pool.pool_address}", frm, to))
            spans.append((token, f"liquidity:{pool.pool_address}", frm, to))
        if len(spans) > 16:
            store.add_coverage_many(spans)
        else:
            for row in spans:
                store.add_coverage(row[0], row[1], row[2], row[3])

    def progress(at: int, _range_end: int, count: int) -> None:
        say(f"Reading swaps: blocks {at:,} of {end_block:,}")

    say(f"Reading swaps: blocks {gap_start:,} of {end_block:,}")
    workers = 4 if gap_end - gap_start > 8_000 else 1
    width = gap_end - gap_start + 1
    _scan_pool_addresses(
        client, rest, gap_start, gap_end, workers, progress, cancel, consume, say
    )
    _scan_v4(client, v4, gap_start, gap_end, width, workers, progress, cancel, consume, say)
    if stats is not None and group:
        stats["network"] += width
        stats["at"] = max(stats["at"], gap_end)
    return raw, width if group else 0


def _scan_v4(
    client: RpcClient,
    pools: list[PoolMeta],
    gap_start: int,
    gap_end: int,
    width: int,
    workers: int,
    progress: Callable[[int, int, int], None],
    cancel: Callable[[], bool],
    consume: Callable[[list[dict[str, Any]], list[PoolMeta], int, int], None],
    say: Progress,
) -> None:
    if not pools:
        return
    if _use_v4_bulk(len(pools), width):
        say(
            f"Reading Uniswap v4 Swap and ModifyLiquidity logs for {len(pools):,} pools "
            "in one pass"
        )

        def on_bulk(frm: int, to: int, logs: list[dict[str, Any]]) -> None:
            consume(logs, pools, frm, to)

        client.get_logs(
            address=POOL_MANAGER,
            topics=_pool_topics("v4"),
            start_block=gap_start,
            end_block=gap_end,
            workers=workers,
            on_chunk=on_bulk,
            on_progress=progress,
            cancel=cancel,
        )
        return
    for offset in range(0, len(pools), _V4_BULK_AT):
        chunk = pools[offset : offset + _V4_BULK_AT]

        def on_v4(frm: int, to: int, logs: list[dict[str, Any]], chunk: list[PoolMeta] = chunk) -> None:
            consume(logs, chunk, frm, to)

        client.get_logs(
            address=POOL_MANAGER,
            topics=[_pool_topics("v4")[0], [pool.pool_address for pool in chunk]],
            start_block=gap_start,
            end_block=gap_end,
            workers=workers,
            on_chunk=on_v4,
            on_progress=progress,
            cancel=cancel,
        )


def _scan_pool_addresses(
    client: RpcClient,
    pools: list[PoolMeta],
    gap_start: int,
    gap_end: int,
    workers: int,
    progress: Callable[[int, int, int], None],
    cancel: Callable[[], bool],
    consume: Callable[[list[dict[str, Any]], list[PoolMeta], int, int], None],
    say: Progress,
) -> None:
    if not pools:
        return
    chunks = [pools[offset : offset + _ADDRESS_BATCH] for offset in range(0, len(pools), _ADDRESS_BATCH)]
    parallel = (
        len(chunks) > 1
        and workers == 1
        and getattr(client, "_transport", None) is None
    )
    if not parallel:
        for chunk in chunks:
            _consume_address_chunk(client, chunk, gap_start, gap_end, workers, progress, cancel, consume)
        return
    say(f"Reading {len(pools):,} v2/v3 pools, {_ADDRESS_BATCH} addresses at a time")
    errors: list[BaseException] = []

    def run(chunk: list[PoolMeta]) -> tuple[list[PoolMeta], list[dict[str, Any]], list[str]]:
        child = client.spawn()
        try:
            logs = _load_address_chunk(child, chunk, gap_start, gap_end, 1, progress, cancel)
            return chunk, logs, list(child.notes)
        finally:
            child.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run, chunk) for chunk in chunks]
        for future in futures:
            try:
                chunk, logs, notes = future.result()
            except BaseException as exc:
                errors.append(exc)
                continue
            client.notes.extend(notes)
            consume(logs, chunk, gap_start, gap_end)
    if errors:
        raise errors[0]


def _consume_address_chunk(
    client: RpcClient,
    chunk: list[PoolMeta],
    gap_start: int,
    gap_end: int,
    workers: int,
    progress: Callable[[int, int, int], None],
    cancel: Callable[[], bool],
    consume: Callable[[list[dict[str, Any]], list[PoolMeta], int, int], None],
) -> None:
    def on_chunk(frm: int, to: int, logs: list[dict[str, Any]]) -> None:
        consume(logs, chunk, frm, to)

    try:
        client.get_logs(
            address=[pool.pool_address for pool in chunk],
            topics=[_event_topics(chunk)],
            start_block=gap_start,
            end_block=gap_end,
            workers=workers,
            on_chunk=on_chunk,
            on_progress=progress,
            cancel=cancel,
        )
    except RpcError as exc:
        if len(chunk) <= 1 or not _too_many_addresses(str(exc)):
            raise
        mid = len(chunk) // 2
        _consume_address_chunk(client, chunk[:mid], gap_start, gap_end, workers, progress, cancel, consume)
        _consume_address_chunk(client, chunk[mid:], gap_start, gap_end, workers, progress, cancel, consume)


def _load_address_chunk(
    client: RpcClient,
    chunk: list[PoolMeta],
    gap_start: int,
    gap_end: int,
    workers: int,
    progress: Callable[[int, int, int], None],
    cancel: Callable[[], bool],
) -> list[dict[str, Any]]:
    try:
        return client.get_logs(
            address=[pool.pool_address for pool in chunk],
            topics=[_event_topics(chunk)],
            start_block=gap_start,
            end_block=gap_end,
            workers=workers,
            on_progress=progress,
            cancel=cancel,
        )
    except RpcError as exc:
        if len(chunk) <= 1 or not _too_many_addresses(str(exc)):
            raise
        mid = len(chunk) // 2
        left = _load_address_chunk(client, chunk[:mid], gap_start, gap_end, workers, progress, cancel)
        right = _load_address_chunk(client, chunk[mid:], gap_start, gap_end, workers, progress, cancel)
        return left + right


def _match_pool(
    log: dict[str, Any],
    by_addr: dict[str, PoolMeta],
    by_id: dict[str, PoolMeta],
) -> PoolMeta | None:
    address = str(log.get("address") or "").lower()
    if address == POOL_MANAGER:
        topics = log.get("topics") or []
        if len(topics) < 2:
            return None
        return by_id.get(str(topics[1]).lower())
    return by_addr.get(address)


def _keep_transfers(
    store: Store,
    client: RpcClient,
    token: str,
    decimals: int,
    logs: list[dict[str, Any]],
    head: int,
    head_ts: datetime,
    frm: int,
    to: int,
) -> None:
    _store_transfers(store, client, token, decimals, logs, head, head_ts, allow_rpc=False)
    store.add_coverage(token, "transfers", frm, to)


def _load_notes(notes: list[str], pools: list[PoolMeta]) -> list[str]:
    dexes = sorted({pool.dex for pool in pools})
    extra: list[str] = []
    if any(item != "uniswap" for item in dexes):
        extra.append("DEXes in this load: " + ", ".join(dexes) + ".")
    return extra + list(notes)


def _empty_window_note(token: str, start_ts: datetime, raw_logs: int, blocks: int) -> str:
    if raw_logs > 0:
        return (
            f"The chain returned {raw_logs:,} pool logs in this window but none decoded as swaps. "
            "That is a reader problem, not a wash result."
        )
    return (
        f"Scanned {blocks:,} blocks and found no Swap logs. "
        "An empty table is not a wash result — there were no trades in this window."
        + _gecko_window_hint(token, start_ts)
    )


def _gecko_window_hint(token: str, start_ts: datetime) -> str:
    try:
        import httpx

        response = httpx.get(
            f"https://api.geckoterminal.com/api/v2/networks/eth/tokens/{token}/pools?include=dex",
            headers={"Accept": "application/json", "User-Agent": "washdetect/0.1"},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return ""
    pools = payload.get("data") if isinstance(payload, dict) else None
    if not pools:
        return ""
    pool = pools[0]
    attrs = pool.get("attributes") or {}
    rel = pool.get("relationships") or {}
    dex = str(((rel.get("dex") or {}).get("data") or {}).get("id") or "")
    raw = attrs.get("pool_created_at")
    created: datetime | None = None
    if raw:
        try:
            created = _naive(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
        except ValueError:
            created = None
    if created is not None and created > start_ts:
        when = created.strftime("%Y-%m-%d %H:%M")
        where = dex or "unknown DEX"
        return f" The top pool ({where}) was created {when}, after this window."
    if dex and not any(name in dex.lower() for name in ("uniswap", "sushi", "pancake")):
        return f" GeckoTerminal's top pool is on {dex}, which this tool does not read."
    return ""


def _fill_swaps_from_subgraph(
    store: Store,
    token: str,
    pools: list[PoolMeta],
    start_block: int,
    end_block: int,
    start_ts: datetime,
    say: Progress,
) -> None:
    v2 = [p.pool_address for p in pools if p.protocol == "v2"]
    v3 = [p.pool_address for p in pools if p.protocol == "v3"]
    raw_rows = fetch_swaps(v2_pools=v2, v3_pools=v3, start_ts=_unix(start_ts))
    by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in raw_rows:
        mapped = map_subgraph_swap(raw, token)
        if not mapped:
            continue
        if not start_block <= mapped["block_number"] <= end_block:
            continue
        by_pool[mapped["pool_address"]].append(mapped)
    for pool in pools:
        dataset = f"swaps:{pool.pool_address}"
        gaps = store.missing(token, dataset, start_block, end_block)
        if not gaps:
            continue
        rows = []
        for item in by_pool.get(pool.pool_address, []):
            ts = datetime.fromtimestamp(item["timestamp"], UTC)
            ts = _naive(ts)
            leg = {
                "side": item["side"],
                "amount_token": item["amount_token"],
                "amount_quote": item["amount_quote"],
                "quote_token": item["quote_token"],
                "amount0_raw": "",
                "amount1_raw": "",
            }
            rows.append(
                _swap_tuple(
                    token, item["tx_hash"], item["log_index"], item["block_number"], ts,
                    pool.pool_address, pool.protocol, item["sender"], item["recipient"],
                    item["trader"], leg, item["usd_value"],
                )
            )
        store.insert_swaps(rows)
        # An empty subgraph page is not proof the pool was quiet.
        if rows:
            store.add_coverage(token, dataset, start_block, end_block)
            say(f"Subgraph returned {len(rows)} swaps for {pool.pool_address[:10]}")


def _store_pool_logs(
    store: Store,
    client: RpcClient,
    token: str,
    pool: PoolMeta,
    logs: list[dict[str, Any]],
    head: int,
    head_ts: datetime,
    *,
    allow_rpc: bool = True,
) -> None:
    if not logs:
        return
    numbers = []
    embedded: dict[int, datetime] = {}
    for log in logs:
        block = _hex_int(log["blockNumber"])
        numbers.append(block)
        raw_ts = _log_ts(log)
        if raw_ts is not None:
            embedded[block] = _naive(datetime.fromtimestamp(raw_ts, UTC))
    if allow_rpc:
        stamps = fetch_timestamps(client, store, numbers, head, head_ts)
        stamps.update(embedded)
    else:
        stamps = dict(embedded)
        for number in numbers:
            if number not in stamps:
                stamps[number] = head_ts - timedelta(seconds=12 * (head - number))
    swaps: list[tuple[Any, ...]] = []
    liquidity: list[tuple[Any, ...]] = []
    for log in logs:
        decoded = decode_pool_log(log, pool, token)
        if not decoded:
            continue
        kind, fields = decoded
        block = _hex_int(log["blockNumber"])
        tx_hash = str(log["transactionHash"]).lower()
        log_index = _hex_int(log["logIndex"])
        ts = stamps.get(block, head_ts)
        if kind == "swap":
            swaps.append(
                _swap_tuple(
                    token, tx_hash, log_index, block, ts, pool.pool_address, pool.protocol,
                    fields["sender"], fields["recipient"], None, fields, None,
                )
            )
        else:
            liquidity.append(
                (
                    token, tx_hash, log_index, block, ts, pool.pool_address, pool.protocol,
                    fields["kind"], fields["sender"], fields["amount0_raw"], fields["amount1_raw"],
                )
            )
    store.insert_swaps(swaps)
    store.insert_liquidity(liquidity)


def _store_transfers(
    store: Store,
    client: RpcClient,
    token: str,
    decimals: int,
    logs: list[dict[str, Any]],
    head: int,
    head_ts: datetime,
    *,
    allow_rpc: bool = True,
) -> None:
    if not logs:
        return
    numbers = [_hex_int(log["blockNumber"]) for log in logs]
    embedded: dict[int, datetime] = {}
    for log in logs:
        raw_ts = _log_ts(log)
        if raw_ts is not None:
            embedded[_hex_int(log["blockNumber"])] = _naive(
                datetime.fromtimestamp(raw_ts, UTC)
            )
    if allow_rpc:
        stamps = fetch_timestamps(client, store, numbers, head, head_ts)
        stamps.update(embedded)
    else:
        stamps = dict(embedded)
        for number in numbers:
            if number not in stamps:
                stamps[number] = head_ts - timedelta(seconds=12 * (head - number))
    rows: list[tuple[Any, ...]] = []
    for log in logs:
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        if str(topics[0]).lower() != TRANSFER:
            continue
        data = str(log.get("data") or "0x")
        if data in {"", "0x"}:
            continue
        try:
            value = decode_transfer_value(data)
        except Exception:
            continue
        block = _hex_int(log["blockNumber"])
        rows.append(
            (
                token,
                str(log["transactionHash"]).lower(),
                _hex_int(log["logIndex"]),
                block,
                stamps.get(block, head_ts),
                topic_address(str(topics[1])),
                topic_address(str(topics[2])),
                str(value),
                human_amount(value, decimals),
            )
        )
    store.insert_transfers(rows)


def _resolve_traders(
    store: Store, client: RpcClient, token: str, start: int, end: int, say: Progress
) -> None:
    missing = store.swaps_missing_trader(token, start, end)
    if not missing:
        return
    known = store.known_senders(missing)
    need = [tx for tx in missing if tx not in known]
    say(f"Looking up {len(need)} transaction senders ({len(known)} cached)")
    fetched: list[tuple[str, str]] = []
    calls = [("eth_getTransactionByHash", [tx]) for tx in need]
    results = client.batch(calls, batch_size=20) if need else []
    for tx, result in zip(need, results, strict=False):
        if isinstance(result, dict) and result.get("from"):
            # tx.from is the trader. Swap.sender is often the router.
            sender = normalize_address(str(result["from"]))
            known[tx] = sender
            fetched.append((tx, sender))
    store.remember_senders(fetched)
    store.apply_traders(token, list(known.items()))


def _price_swaps(
    store: Store, client: RpcClient, token: str, start: int, end: int, say: Progress
) -> float | None:
    eth_usd: float | None = None
    try:
        raw = client.eth_call(WETH_USDC_POOL, calldata("slot0"), end)
        eth_usd = eth_usd_from_usdc_weth_sqrt(decode_slot0_sqrt(raw))
        if not price_is_plausible(eth_usd):
            say(f"ETH/USD {eth_usd:.0f} looks wrong; WETH-quoted swaps will be unpriced")
            eth_usd = None
        else:
            say(f"ETH/USD ≈ {eth_usd:,.0f} from the USDC/WETH 0.05% pool")
    except Exception as exc:
        client.note(f"could not read ETH/USD ({exc})")
        say("Could not read an ETH/USD price. Stable-quoted swaps are still priced.")
    pending = store.swaps_missing_price(token, start, end)
    updates: list[tuple[float, str, int]] = []
    if not pending.empty:
        for row in pending.itertuples(index=False):
            usd = swap_usd(
                token,
                float(row.amount_token or 0),
                str(row.quote_token),
                float(row.amount_quote or 0),
                eth_usd,
            )
            if usd is None:
                continue
            updates.append((usd, str(row.tx_hash), int(row.log_index)))
    store.apply_prices(token, updates)
    return eth_usd


def _candidate_wallets(store: Store, token: str, start: int, end: int) -> list[str]:
    traders = store.distinct_traders(token, start, end, BALANCE_CAP)
    receivers = store.top_receivers(token, start, end, BALANCE_CAP)
    ordered: list[str] = []
    seen: set[str] = set()
    for wallet in [*traders, *receivers]:
        if not wallet or wallet in ROUTERS or wallet in seen:
            continue
        seen.add(wallet)
        ordered.append(wallet)
        if len(ordered) >= BALANCE_CAP:
            break
    return ordered

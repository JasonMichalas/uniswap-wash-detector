from __future__ import annotations

import re
import threading
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
    decode_aggregate3,
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
    BURN_ADDRESS,
    MULTICALL3,
    POOL_MANAGER,
    ROUTERS,
    WETH_USDC_POOL,
    ZERO_ADDRESS,
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
from washdetect.meter import BlockMeter
from washdetect.prices import (
    QuoteOracle,
    apply_token_implied,
    direct_source,
    eth_usd_from_usdc_weth_sqrt,
    nearest_value,
    price_is_plausible,
    pricing_note,
    swap_usd,
)
from washdetect.rpc import Cancelled, RpcClient, RpcError
from washdetect.store import Store, pool_log_gaps
from washdetect.window import _naive, resolve_window

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
    v4_pools: int
    swap_logs: int
    notes: list[str] = field(default_factory=list)
    note_details: list[str] = field(default_factory=list)

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
            "notes": self.notes,
            "note_details": self.note_details,
            "analysis_ran": False,
        }


def fetch_balances(
    client: RpcClient, token: str, wallets: list[str], block: int, decimals: int
) -> dict[str, float]:
    step = 150
    parts = [wallets[offset : offset + step] for offset in range(0, len(wallets), step)]

    def read(part: list[str]) -> dict[str, float]:
        found: dict[str, float] = {}
        calls = [(token, True, encode_balance_of(wallet)) for wallet in part]
        try:
            decoded = decode_aggregate3(
                client.eth_call(MULTICALL3, encode_aggregate3(calls), block)
            )
        except Exception:
            for wallet in part:
                try:
                    word = client.eth_call(token, "0x" + encode_balance_of(wallet).hex(), block)
                    found[wallet] = human_amount(decode_uint(word), decimals)
                except Exception:
                    continue
            return found
        for wallet, (ok, ret) in zip(part, decoded, strict=False):
            if ok and len(ret) >= 32:
                found[wallet] = human_amount(int.from_bytes(ret[-32:], "big"), decimals)
        return found

    with ThreadPoolExecutor(max_workers=_threads(client)) as pool:
        return {wallet: value for found in pool.map(read, parts) for wallet, value in found.items()}


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


def _threads(client: RpcClient, wanted: int = 6) -> int:
    # A test transport answers in order, so it gets one thread.
    return 1 if getattr(client, "_transport", None) is not None else wanted


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
    eta: float | None = None,
) -> str:
    """One status line. The phase does not carry its own percentage."""
    swap_bit = f"{swaps:,} swaps found" if swaps_started else "swaps not started yet"
    transfer_bit = (
        f"{transfers:,} transfers found" if transfers_started else "transfers not started yet"
    )
    counts = f"{swap_bit} · {transfer_bit}"
    tail = f"{elapsed:.0f}s elapsed · {network:,} blocks from network, {cached:,} cached"
    if not show_blocks or end <= 0:
        return f"{phase} · {counts} · {tail}"
    width = max(1, end)
    pct = min(100, max(0, int(100 * at / width)))
    extra = f" · chain {chain:,}" if chain else ""
    if eta is None and phase.startswith("Finding pools") and elapsed >= 4 and pct >= 1:
        eta = elapsed * (100 - pct) / pct
    left = f" · about {_eta(eta)} left in this step" if eta is not None and 1 <= pct <= 99 else ""
    return f"{phase} · block {at:,} of {end:,} ({pct}%){extra}{left} · {counts} · {tail}"


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
    phase = (
        _EMBEDDED_PCT.sub("", _PHASE_NOISE.sub("", message.split(" · ")[0])).strip(" :")
        or "Working"
    )
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
        eta=stats.pop("eta", None) if found else None,
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
                    token,
                    p.pool_address,
                    p.protocol,
                    p.token0,
                    p.token1,
                    p.fee,
                    p.token0_decimals,
                    p.token1_decimals,
                    p.token0_symbol,
                    p.token1_symbol,
                )
                for p in pools
            ]
        )
        say(f"Found {len(pools)} pools")

        meter = BlockMeter(span_blocks, ("swaps", "transfers"), say, stats)
        stats["swaps_started"] = stats["transfers_started"] = 1
        groups, swaps_cached = _swap_groups(store, token, pools, start_block, end_block)
        transfer_gaps = store.missing(token, "transfers", start_block, end_block)
        meter.seed("swaps", swaps_cached)
        meter.seed("transfers", span_blocks - _gap_blocks(transfer_gaps))
        meter.begin()
        swap_client = client.spawn()
        transfer_client = client.spawn(cursor=client._cursor + 1)
        try:
            swap_logs, _ = _together(
                stop,
                lambda halt: _fetch_planned(
                    store,
                    swap_client,
                    token,
                    pools,
                    groups,
                    start_block,
                    end_block,
                    head_ts,
                    say,
                    halt,
                    meter,
                ),
                lambda halt: _fetch_transfers(
                    store,
                    transfer_client,
                    token,
                    decimals,
                    transfer_gaps,
                    end_block,
                    head_ts,
                    halt,
                    meter,
                ),
            )
        finally:
            swap_client.close()
            transfer_client.close()
        blocks_fetched = meter.fetched
        blocks_cached = meter.cached

        say("Resolving traders from transaction senders")
        _resolve_traders(store, client, token, start_block, end_block, say)

        say("Pricing swaps")
        eth_usd = _price_swaps(store, client, token, start_block, end_block, say)

        say("Reading balances for the most active wallets")
        wallets = _candidate_wallets(store, token, start_block, end_block)
        truncated = len(wallets) >= 8000
        balances: dict[str, float] = {}
        balance_block = end_block
        try:
            balances = fetch_balances(client, token, wallets, end_block, decimals)
        except Exception as exc:
            client.detail(f"balanceOf at block {end_block} failed ({exc}); tried latest")
            balances = fetch_balances(client, token, wallets, client.block_number(), decimals)
            balance_block = client.block_number()
        store.save_balances(token, balance_block, list(balances.items()))
        _rebuild_daily(store, token, balances, start_block, end_block, end_ts)

        notes, details = _load_notes(client, pools)
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
            v4_pools=sum(1 for p in pools if protocol_family(p.protocol) == "v4"),
            swap_logs=swap_logs,
            notes=notes,
            note_details=details,
        )
        if report.swaps == 0:
            # The whole window was checked. blocks_fetched is only the part that was not cached.
            report.notes.insert(
                0, _empty_window_note(token, start_ts, report.swap_logs, span_blocks)
            )
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


def _together(cancel: Callable[[], bool], *jobs: Callable[[Callable[[], bool]], Any]) -> list[Any]:
    """Run jobs side by side. When one fails the others stop at their next chunk; what they saved stays."""
    failed = threading.Event()

    def halt() -> bool:
        return failed.is_set() or cancel()

    def guarded(job: Callable[[Callable[[], bool]], Any]) -> Any:
        try:
            return job(halt)
        except BaseException:
            failed.set()
            raise

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(guarded, job) for job in jobs]
    errors = [future.exception() for future in futures if future.exception() is not None]
    if errors:
        if cancel():
            raise Cancelled("Load cancelled")
        raise next((exc for exc in errors if not isinstance(exc, Cancelled)), errors[0])
    return [future.result() for future in futures]


def _scan_span(width: int, workers: int) -> int:
    """Small enough that every worker has a chunk from the start; LogScan grows it back on success."""
    return max(1, min(_LOG_SPAN, -(-width // max(1, workers))))


def _fetch_transfers(
    store: Store,
    client: RpcClient,
    token: str,
    decimals: int,
    gaps: list[tuple[int, int]],
    end_block: int,
    head_ts: datetime,
    cancel: Callable[[], bool],
    meter: BlockMeter,
) -> None:
    workers = client.log_workers()
    for gap_start, gap_end in gaps:

        def on_chunk(frm: int, to: int, chunk: list[dict[str, Any]]) -> None:
            _keep_transfers(store, client, token, decimals, chunk, end_block, head_ts, frm, to)
            meter.advance("transfers", to - frm + 1, len(chunk))

        span = _scan_span(gap_end - gap_start + 1, workers)
        client.get_logs(
            address=token,
            topics=[TRANSFER],
            start_block=gap_start,
            end_block=gap_end,
            init_span=span,
            workers=workers,
            on_chunk=on_chunk,
            cancel=cancel,
            empty_limit=span,
        )


SwapGroups = dict[tuple[tuple[int, int], ...], list[PoolMeta]]


def _swap_groups(
    store: Store, token: str, pools: list[PoolMeta], start_block: int, end_block: int
) -> tuple[SwapGroups, float]:
    """Pools that miss the same block ranges share one scan. Also returns the cached share of the window."""
    have = store.coverage_by_dataset(token)
    groups: SwapGroups = defaultdict(list)
    missing = 0
    for pool in pools:
        gaps = tuple(pool_log_gaps(have, pool.pool_address, start_block, end_block))
        missing += _gap_blocks(list(gaps))
        if gaps:
            groups[gaps].append(pool)
    window = end_block - start_block + 1
    return groups, window - missing / max(1, len(pools))


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
    meter: BlockMeter | None = None,
) -> int:
    groups, _ = _swap_groups(store, token, pools, start_block, end_block)
    return _fetch_planned(
        store, client, token, pools, groups, start_block, end_block, head_ts, say, cancel, meter
    )


def _fetch_planned(
    store: Store,
    client: RpcClient,
    token: str,
    pools: list[PoolMeta],
    groups: SwapGroups,
    start_block: int,
    end_block: int,
    head_ts: datetime,
    say: Progress,
    cancel: Callable[[], bool],
    meter: BlockMeter | None,
) -> int:
    def on_read(blocks: int, share: int, found: int) -> None:
        if meter is not None:
            meter.advance("swaps", blocks * share / max(1, len(pools)), found)

    raw = 0
    for gaps, group in groups.items():
        for gap_start, gap_end in gaps:
            raw += _fetch_one_gap(
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
                on_read,
            )
    return raw


# One filtered eth_getLogs for every pool of the token. A page of all v4 logs is what
# made a one-day load sit for twenty minutes.
_V4_ID_BATCH = 128
_ADDRESS_BATCH = 400
_LOG_SPAN = 20_000


def _too_many_addresses(message: str) -> bool:
    text = message.lower()
    return "address" in text and any(
        hint in text for hint in ("maximum", "too many", "limit", "allowed", "exceed")
    )


def _event_topics(chunk: list[PoolMeta]) -> list[str]:
    topics: list[str] = []
    for pool in chunk:
        for topic in _pool_topics(pool.protocol)[0]:
            if topic not in topics:
                topics.append(topic)
    return topics


Consume = Callable[[list[dict[str, Any]], list[PoolMeta], int, int], None]


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
    on_read: Callable[[int, int, int], None] | None = None,
) -> int:
    """Raw pool logs read for this gap. Each chunk is saved with its coverage as soon as it lands."""
    raw = 0
    lock = threading.Lock()
    v4 = [pool for pool in group if protocol_family(pool.protocol) == "v4"]
    rest = [pool for pool in group if protocol_family(pool.protocol) != "v4"]

    def consume(
        logs: list[dict[str, Any]], pools_in_call: list[PoolMeta], frm: int, to: int
    ) -> None:
        nonlocal raw
        by_addr = {pool.pool_address: pool for pool in pools_in_call}
        by_id = {pool.pool_address.lower(): pool for pool in pools_in_call}
        bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for log in logs:
            pool = _match_pool(log, by_addr, by_id)
            if pool is not None:
                bucket[pool.pool_address].append(log)
        kept = [log for rows in bucket.values() for log in rows]
        for address, rows in bucket.items():
            _store_pool_logs(
                store, client, token, by_addr[address], rows, end_block, head_ts, allow_rpc=False
            )
        spans = []
        for pool in pools_in_call:
            spans.append((token, f"swaps:{pool.pool_address}", frm, to))
            spans.append((token, f"liquidity:{pool.pool_address}", frm, to))
        store.add_coverage_many(spans)
        with lock:
            raw += len(kept)
        if on_read is not None:
            on_read(to - frm + 1, len(pools_in_call), _count_swap_logs(kept))

    workers = client.log_workers()
    span = _scan_span(gap_end - gap_start + 1, workers)
    for offset in range(0, len(rest), _ADDRESS_BATCH):
        chunk = rest[offset : offset + _ADDRESS_BATCH]
        _scan_addresses(client, chunk, gap_start, gap_end, workers, span, cancel, consume)
    for offset in range(0, len(v4), _V4_ID_BATCH):
        chunk = v4[offset : offset + _V4_ID_BATCH]
        topics = [_pool_topics("v4")[0], [pool.pool_address for pool in chunk]]
        _scan(
            client, chunk, POOL_MANAGER, topics, gap_start, gap_end, workers, span, cancel, consume
        )
    return raw


def _scan(
    client: RpcClient,
    chunk: list[PoolMeta],
    address: str | list[str],
    topics: list[Any],
    gap_start: int,
    gap_end: int,
    workers: int,
    span: int,
    cancel: Callable[[], bool],
    consume: Consume,
) -> None:
    def on_chunk(frm: int, to: int, logs: list[dict[str, Any]]) -> None:
        consume(logs, chunk, frm, to)

    client.get_logs(
        address=address,
        topics=topics,
        start_block=gap_start,
        end_block=gap_end,
        init_span=span,
        workers=workers,
        on_chunk=on_chunk,
        cancel=cancel,
        empty_limit=span,
    )


def _scan_addresses(
    client: RpcClient,
    chunk: list[PoolMeta],
    gap_start: int,
    gap_end: int,
    workers: int,
    span: int,
    cancel: Callable[[], bool],
    consume: Consume,
) -> None:
    addresses = [pool.pool_address for pool in chunk]
    topics = [_event_topics(chunk)]
    try:
        _scan(client, chunk, addresses, topics, gap_start, gap_end, workers, span, cancel, consume)
    except RpcError as exc:
        if len(chunk) <= 1 or not _too_many_addresses(str(exc)):
            raise
        mid = len(chunk) // 2
        _scan_addresses(client, chunk[:mid], gap_start, gap_end, workers, span, cancel, consume)
        _scan_addresses(client, chunk[mid:], gap_start, gap_end, workers, span, cancel, consume)


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


def _load_notes(client: RpcClient, pools: list[PoolMeta]) -> tuple[list[str], list[str]]:
    dexes = sorted({pool.dex for pool in pools})
    extra: list[str] = []
    if any(item != "uniswap" for item in dexes):
        extra.append("DEXes in this load: " + ", ".join(dexes) + ".")
    details = list(getattr(client, "details", []))
    notes = list(getattr(client, "notes", []))
    if details:
        notes.append("One free data server refused some requests; we used the others.")
    return extra + notes, details


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
                    token,
                    tx_hash,
                    log_index,
                    block,
                    ts,
                    pool.pool_address,
                    pool.protocol,
                    fields["sender"],
                    fields["recipient"],
                    None,
                    fields,
                    None,
                )
            )
        else:
            liquidity.append(
                (
                    token,
                    tx_hash,
                    log_index,
                    block,
                    ts,
                    pool.pool_address,
                    pool.protocol,
                    fields["kind"],
                    fields["sender"],
                    fields["amount0_raw"],
                    fields["amount1_raw"],
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
            embedded[_hex_int(log["blockNumber"])] = _naive(datetime.fromtimestamp(raw_ts, UTC))
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


_SENDER_PART = 2_000


def _resolve_traders(
    store: Store, client: RpcClient, token: str, start: int, end: int, say: Progress
) -> None:
    missing = store.swaps_missing_trader(token, start, end)
    known = store.known_senders(missing)
    need = sorted({tx for tx in missing if tx not in known} | set(store.txs_missing_meta(token, start, end)))
    # Saved in parts so a lookup that is stopped halfway picks up where it was.
    for done in range(0, len(need), _SENDER_PART):
        say(f"Looking up transaction senders: {done:,} of {len(need):,} ({len(known):,} cached)")
        part = need[done : done + _SENDER_PART]
        found = [
            (tx, result)
            for tx, result in zip(
                part,
                client.batch([("eth_getTransactionByHash", [tx]) for tx in part]),
                strict=False,
            )
            if isinstance(result, dict) and result.get("from")
        ]
        # tx.from is the trader. Swap.sender is often the router.
        fetched = [(tx, normalize_address(str(result["from"]))) for tx, result in found]
        store.remember_senders(fetched)
        store.remember_tx_meta([tx_meta_row(tx, result) for tx, result in found])
        store.apply_traders(token, fetched)
    store.apply_traders(token, list(known.items()))


def tx_meta_row(tx: str, result: dict) -> tuple:
    def number(key: str) -> int | None:
        value = result.get(key)
        return int(str(value), 16) if value not in (None, "") else None

    def gwei(key: str) -> float | None:
        value = number(key)
        return value / 1e9 if value is not None else None

    to = result.get("to")
    return (
        tx,
        normalize_address(str(to)) if to else None,
        number("nonce"),
        number("transactionIndex"),
        gwei("gasPrice"),
        gwei("maxPriorityFeePerGas"),
        number("gas"),
    )


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
        client.detail(f"could not read ETH/USD ({exc})")
        say("Could not read an ETH/USD price. Stable-quoted swaps are still priced.")
    pending = store.swaps_missing_price(token, start, end)
    rows: list[dict[str, object]] = []
    if not pending.empty:
        for row in pending.itertuples(index=False):
            amount_token = float(row.amount_token or 0)
            amount_quote = float(row.amount_quote or 0)
            quote = str(row.quote_token or "")
            usd = swap_usd(token, amount_token, quote, amount_quote, eth_usd)
            source = direct_source(token, quote) if usd is not None else None
            rows.append(
                {
                    "tx": str(row.tx_hash),
                    "log": int(row.log_index),
                    "block": int(row.block_number),
                    "amount_token": amount_token,
                    "amount_quote": amount_quote,
                    "quote": quote.lower(),
                    "usd": usd,
                    "source": source,
                }
            )
        oracle = QuoteOracle(client)
        quotes = sorted({str(row["quote"]) for row in rows if row["usd"] is None and row["quote"]})

        def sample(quote: str) -> list[tuple[int, float]]:
            blocks = [
                int(row["block"]) for row in rows if row["quote"] == quote and row["usd"] is None
            ]
            return oracle.samples(quote, blocks, eth_usd)

        samples: dict[str, list[tuple[int, float]]] = {}
        with ThreadPoolExecutor(max_workers=_threads(client)) as pool:
            for quote, found in zip(quotes, pool.map(sample, quotes), strict=True):
                say(f"Pricing swaps: {len(samples):,} of {len(quotes):,} other currencies checked")
                samples[quote] = found
        for row in rows:
            if row["usd"] is not None:
                continue
            sample = samples.get(str(row["quote"]), [])
            price = nearest_value(int(row["block"]), sample)
            amount_quote = row["amount_quote"]
            if price is None or not isinstance(amount_quote, (int, float)) or amount_quote <= 0:
                continue
            row["usd"] = abs(float(amount_quote)) * price
            row["source"] = "quote-pool"
        apply_token_implied(rows)
    updates = [
        (float(row["usd"]), str(row["source"]), str(row["tx"]), int(row["log"]))
        for row in rows
        if isinstance(row["usd"], (int, float)) and isinstance(row["source"], str)
    ]
    store.apply_prices(token, updates)
    frame = store.read_swaps(token, start, end)
    sources: list[str | None] = []
    if not frame.empty:
        source_col = (
            frame["price_source"].tolist()
            if "price_source" in frame.columns
            else [None] * len(frame)
        )
        for usd, source in zip(frame["usd_value"].tolist(), source_col, strict=True):
            if usd is None or (isinstance(usd, float) and usd != usd):
                sources.append(None)
            else:
                sources.append(str(source) if isinstance(source, str) and source else "stable")
    note = pricing_note(len(sources), sources)
    if note:
        say(note)
        client.note(note)
    return eth_usd


def _candidate_wallets(store: Store, token: str, start: int, end: int) -> list[str]:
    """Every address seen in transfers, plus traders. Zero, burn, and routers are left out."""
    skip = {ZERO_ADDRESS, BURN_ADDRESS, *(addr.lower() for addr in ROUTERS)}
    ordered: list[str] = []
    seen: set[str] = set()
    parties = store.transfer_parties(token, start, end, 8000)
    traders = store.distinct_traders(token, start, end, 8000)
    for wallet in [*traders, *parties]:
        text = str(wallet or "").lower()
        if not text or text in skip or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered

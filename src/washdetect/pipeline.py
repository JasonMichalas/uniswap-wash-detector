"""Load one token into the local cache.

The loader never runs the wash detector. It discovers pools, fills the gaps
in the swap and transfer logs, resolves `tx.from` as the trader, marks USD
values, and snapshots balances. A second call with a wider window fetches
only the missing blocks.

Block ranges are inclusive. Coverage is recorded only after a gap has been
written, so a crashed run retries that gap instead of leaving a hole that
looks cached.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from washdetect.abi import (
    BURN_V2,
    BURN_V3,
    MINT_V2,
    MINT_V3,
    SWAP_V2,
    SWAP_V3,
    TRANSFER,
    calldata,
    classify_leg,
    decode_slot0_sqrt,
    decode_transfer_value,
    decode_uint,
    decode_v2_mint_amounts,
    decode_v2_swap_amounts,
    decode_v3_burn_amounts,
    decode_v3_mint_amounts,
    decode_v3_swap_amounts,
    encode_aggregate3,
    encode_balance_of,
    human_amount,
    normalize_address,
    topic_address,
)
from washdetect.config import (
    BALANCE_CAP,
    MAX_WINDOW_DAYS,
    MULTICALL3,
    ROUTERS,
    WETH_USDC_POOL,
)
from washdetect.discover import PoolMeta, discover_pools, read_token_meta
from washdetect.prices import eth_usd_from_usdc_weth_sqrt, price_is_plausible, swap_usd
from washdetect.rpc import RpcClient, RpcError
from washdetect.store import Store
from washdetect.subgraph import SubgraphError, api_key, fetch_swaps, map_subgraph_swap

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


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _unix(dt: datetime) -> int:
    return int(_naive(dt).replace(tzinfo=UTC).timestamp())


def parse_day(value: str, *, end_of_day: bool) -> datetime:
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"could not read the date {value!r}; use YYYY-MM-DD") from exc
    parsed = _naive(parsed)
    if len(text) == 10 and end_of_day:
        parsed = parsed + timedelta(days=1) - timedelta(seconds=1)
    return parsed


def resolve_window(
    client: RpcClient,
    *,
    days: int | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[int, int, datetime, datetime]:
    """Turn a user window into inclusive blocks. There is no default length."""
    if days is None and not (start and end):
        raise ValueError(
            "Choose a window: pass days (1-90) or both start and end (YYYY-MM-DD). "
            "There is no default history length."
        )
    if days is not None and (start or end):
        raise ValueError("Pass either days or start/end, not both.")
    head = client.block_number()
    head_block = client.get_block(head)
    head_ts = datetime.fromtimestamp(int(head_block["timestamp"], 16), UTC)
    head_ts = _naive(head_ts)
    if days is not None:
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_WINDOW_DAYS:
            raise ValueError(f"days must be an integer from 1 to {MAX_WINDOW_DAYS}")
        end_ts = head_ts
        start_ts = end_ts - timedelta(days=days)
        end_block = head
    else:
        assert start and end
        start_ts = parse_day(start, end_of_day=False)
        end_ts = parse_day(end, end_of_day=True)
        if end_ts > head_ts:
            end_ts = head_ts
        span = (end_ts - start_ts).total_seconds() / 86400.0
        if span <= 0:
            raise ValueError("The window ends before it starts.")
        if span > MAX_WINDOW_DAYS + 0.01:
            raise ValueError(f"A window can be at most {MAX_WINDOW_DAYS} days (this one is {span:.1f}).")
        end_block = client.find_block_by_timestamp(_unix(end_ts), 1, head)
    start_block = client.find_block_by_timestamp(_unix(start_ts), 1, end_block)
    if start_block > end_block:
        raise ValueError("The window does not cover any blocks.")
    return start_block, end_block, start_ts, end_ts


def _hex_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value), 16)


def _log_ts(log: dict[str, Any]) -> int | None:
    raw = log.get("blockTimestamp")
    if raw is None:
        return None
    try:
        return _hex_int(raw)
    except (TypeError, ValueError):
        return None


def fetch_timestamps(
    client: RpcClient, store: Store, numbers: list[int], head: int, head_ts: datetime
) -> dict[int, datetime]:
    unique = sorted(set(numbers))
    known = store.known_blocks(unique)
    missing = [n for n in unique if n not in known]
    if missing:
        calls = [("eth_getBlockByNumber", [hex(n), False]) for n in missing]
        results = client.batch(calls, batch_size=25)
        rows: list[tuple[int, datetime]] = []
        for number, result in zip(missing, results, strict=False):
            if not isinstance(result, dict) or "timestamp" not in result:
                continue
            ts = datetime.fromtimestamp(int(result["timestamp"], 16), UTC)
            ts = _naive(ts)
            known[number] = ts
            rows.append((number, ts))
        store.remember_blocks(rows)
    for number in unique:
        if number not in known:
            known[number] = head_ts - timedelta(seconds=12 * (head - number))
    return known


def _swap_tuple(
    token: str,
    tx_hash: str,
    log_index: int,
    block: int,
    ts: datetime,
    pool: str,
    protocol: str,
    sender: str,
    recipient: str,
    trader: str | None,
    leg: dict[str, Any],
    usd: float | None,
) -> tuple[Any, ...]:
    return (
        token,
        tx_hash,
        log_index,
        block,
        ts,
        pool,
        protocol,
        sender,
        recipient,
        trader,
        leg["side"],
        leg["amount_token"],
        leg["amount_quote"],
        leg["quote_token"],
        usd,
        leg["amount0_raw"],
        leg["amount1_raw"],
    )


def decode_pool_log(
    log: dict[str, Any], pool: PoolMeta, token: str
) -> tuple[str, dict[str, Any]] | None:
    """Return ('swap', fields) or ('liq', fields) or None if the log is irrelevant."""
    topics = [str(t).lower() for t in (log.get("topics") or [])]
    if not topics:
        return None
    kind_topic = topics[0]
    data = str(log.get("data") or "0x")
    try:
        if pool.protocol == "v2" and kind_topic == SWAP_V2:
            a0_in, a1_in, a0_out, a1_out = decode_v2_swap_amounts(data)
            leg = classify_leg(
                token, pool.token0, pool.token1, pool.token0_decimals, pool.token1_decimals,
                a0_in - a0_out, a1_in - a1_out,
            )
            if not leg:
                return None
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            recipient = topic_address(topics[2]) if len(topics) > 2 else ""
            return "swap", {**leg, "sender": sender, "recipient": recipient}
        if pool.protocol == "v3" and kind_topic == SWAP_V3:
            amount0, amount1 = decode_v3_swap_amounts(data)
            leg = classify_leg(
                token, pool.token0, pool.token1, pool.token0_decimals, pool.token1_decimals,
                amount0, amount1,
            )
            if not leg:
                return None
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            recipient = topic_address(topics[2]) if len(topics) > 2 else ""
            return "swap", {**leg, "sender": sender, "recipient": recipient}
        if pool.protocol == "v2" and kind_topic == MINT_V2:
            a0, a1 = decode_v2_mint_amounts(data)
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            return "liq", {"kind": "mint", "sender": sender, "amount0_raw": str(a0), "amount1_raw": str(a1)}
        if pool.protocol == "v2" and kind_topic == BURN_V2:
            a0, a1 = decode_v2_mint_amounts(data)
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            return "liq", {"kind": "burn", "sender": sender, "amount0_raw": str(a0), "amount1_raw": str(a1)}
        if pool.protocol == "v3" and kind_topic == MINT_V3:
            a0, a1 = decode_v3_mint_amounts(data)
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            return "liq", {"kind": "mint", "sender": sender, "amount0_raw": str(a0), "amount1_raw": str(a1)}
        if pool.protocol == "v3" and kind_topic == BURN_V3:
            a0, a1 = decode_v3_burn_amounts(data)
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            return "liq", {"kind": "burn", "sender": sender, "amount0_raw": str(a0), "amount1_raw": str(a1)}
    except Exception:
        return None
    return None


def _pool_topics(protocol: str) -> list[Any]:
    if protocol == "v2":
        return [[SWAP_V2, MINT_V2, BURN_V2]]
    return [[SWAP_V3, MINT_V3, BURN_V3]]


def fetch_balances(
    client: RpcClient, token: str, wallets: list[str], block: int, decimals: int
) -> dict[str, float]:
    """`balanceOf` for many wallets via Multicall3, with a per-call fallback."""
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
    """Anchor a daily balance path on the end-of-window `balanceOf`.

    Replaying transfers gives the net flow. The absolute level is
    `balanceOf(end) - net_after(day)`, so the last point matches the chain
    and earlier points are that balance walked backwards through the window.
    """
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
) -> LoadReport:
    """Fetch and cache one token. Does not compute a wash percentage."""
    say = progress or (lambda _msg: None)
    started = time.perf_counter()
    token = normalize_address(token)
    own_client = client is None
    client = client or RpcClient()
    own_store = store is None
    store = store or Store(db_path)
    blocks_fetched = 0
    blocks_cached = 0
    used_subgraph = False
    try:
        say("Resolving the block window")
        start_block, end_block, start_ts, end_ts = resolve_window(
            client, days=days, start=start, end=end
        )
        head_ts = end_ts
        span_blocks = end_block - start_block + 1
        say(
            f"Window {start_ts:%Y-%m-%d %H:%M} → {end_ts:%Y-%m-%d %H:%M} "
            f"(blocks {start_block:,}–{end_block:,})"
        )
        symbol, decimals = read_token_meta(client, token)
        store.upsert_token(token, symbol, decimals)
        say(f"Token {symbol} ({decimals} decimals)")

        cached_pools = [] if rediscover else store.pools_for(token)
        if cached_pools:
            pools = [
                PoolMeta(
                    pool_address=str(row["pool_address"]),
                    protocol=str(row["protocol"]),
                    token0=str(row["token0"]),
                    token1=str(row["token1"]),
                    fee=int(row["fee"] or 0),
                    token0_decimals=int(row["token0_decimals"] or 18),
                    token1_decimals=int(row["token1_decimals"] or 18),
                    token0_symbol=str(row["token0_symbol"] or ""),
                    token1_symbol=str(row["token1_symbol"] or ""),
                )
                for row in cached_pools
            ]
            say(f"Using {len(pools)} cached pools")
        else:
            say("Discovering Uniswap v2 and v3 pools")
            pools = discover_pools(client, token)
            if not pools:
                raise RpcError(
                    "No Uniswap v2/v3 pool was found against WETH, USDC, USDT, or DAI. "
                    "Check the address, or the token only trades in an exotic quote."
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

        # Optional subgraph accelerator for swaps. Failures are notes, not errors.
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

        for pool in pools:
            dataset = f"swaps:{pool.pool_address}"
            gaps = store.missing(token, dataset, start_block, end_block)
            blocks_cached += span_blocks - _gap_blocks(gaps)
            for gap_start, gap_end in gaps:
                say(
                    f"Swaps {pool.protocol} {pool.token0_symbol}/{pool.token1_symbol} "
                    f"blocks {gap_start:,}–{gap_end:,}"
                )
                logs = client.get_logs(
                    address=pool.pool_address,
                    topics=_pool_topics(pool.protocol),
                    start_block=gap_start,
                    end_block=gap_end,
                )
                blocks_fetched += gap_end - gap_start + 1
                _store_pool_logs(store, client, token, pool, logs, end_block, head_ts)
                store.add_coverage(token, dataset, gap_start, gap_end)

        transfer_gaps = store.missing(token, "transfers", start_block, end_block)
        blocks_cached += span_blocks - _gap_blocks(transfer_gaps)
        for gap_start, gap_end in transfer_gaps:
            say(f"Token transfers blocks {gap_start:,}–{gap_end:,}")
            logs = client.get_logs(
                address=token,
                topics=[TRANSFER],
                start_block=gap_start,
                end_block=gap_end,
            )
            blocks_fetched += gap_end - gap_start + 1
            _store_transfers(store, client, token, decimals, logs, end_block, head_ts)
            store.add_coverage(token, "transfers", gap_start, gap_end)

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
            v2_pools=sum(1 for p in pools if p.protocol == "v2"),
            v3_pools=sum(1 for p in pools if p.protocol == "v3"),
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
            notes=list(client.notes),
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
        # Only mark the window covered when the subgraph returned rows, or the
        # pool was quiet. An empty response on an active pool is treated as a
        # failure so RPC can fill the gap. We cannot tell those apart here, so
        # coverage is marked only when at least one swap was returned. Quiet
        # pools fall through to a (cheap, empty) RPC scan.
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
    stamps = fetch_timestamps(client, store, numbers, head, head_ts)
    stamps.update(embedded)
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
    stamps = fetch_timestamps(client, store, numbers, head, head_ts)
    stamps.update(embedded)
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

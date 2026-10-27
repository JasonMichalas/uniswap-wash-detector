from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from washdetect.config import HeuristicConfig
from washdetect.detector import SwapEvent, TransferEvent

PATTERN_TYPES = (
    "round_trip",
    "ring",
    "one_to_many",
    "many_to_one",
    "many_to_many",
)

MAX_GROUP_HARD_CAP = 64


@dataclass
class FlowGroup:
    wallets: tuple[str, ...]
    swaps: list[SwapEvent]
    n_buyers: int
    n_sellers: int
    n_wallets: int
    pattern_type: str
    net_ratio: float
    start_block: int
    end_block: int
    buy_amount: float
    sell_amount: float


def classify_pattern(n_wallets: int, n_buyers: int, n_sellers: int, *, has_cycle: bool) -> str:
    if n_wallets <= 1:
        return "round_trip"
    if n_buyers > 1 and n_sellers > 1:
        return "many_to_many"
    if n_buyers > 1 and n_sellers == 1:
        return "many_to_one"
    if n_buyers == 1 and n_sellers > 1:
        return "one_to_many"
    if has_cycle or (n_buyers == 1 and n_sellers == 1 and n_wallets > 1):
        return "ring"
    return "ring"


def _has_directed_cycle(nodes: Iterable[str], edges: Sequence[tuple[str, str]]) -> bool:
    wanted = set(nodes)
    adj: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        if src in wanted and dst in wanted and src != dst:
            adj[src].append(dst)
    color = dict.fromkeys(wanted, 0)

    def visit(node: str) -> bool:
        color[node] = 1
        for nxt in adj.get(node, ()):
            if color.get(nxt) == 1 or (color.get(nxt) == 0 and visit(nxt)):
                return True
        color[node] = 2
        return False

    return any(color[node] == 0 and visit(node) for node in wanted)


def _bfs(starts: Iterable[str], adj: dict[str, set[str]], limit: int) -> tuple[set[str], bool]:
    seen: set[str] = set()
    stack: list[str] = []
    for start in starts:
        if start not in seen:
            seen.add(start)
            stack.append(start)
    while stack:
        node = stack.pop()
        for nxt in adj.get(node, ()):
            if nxt in seen:
                continue
            if len(seen) >= limit:
                return seen, True
            seen.add(nxt)
            stack.append(nxt)
    return seen, False


def _slice(events: Sequence, lo: int, hi: int, blocks: Sequence[int]) -> Sequence:
    from bisect import bisect_left, bisect_right

    left = bisect_left(blocks, lo)
    right = bisect_right(blocks, hi)
    return events[left:right]


def find_flow_groups(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    excluded: set[str],
    config: HeuristicConfig,
) -> list[FlowGroup]:
    if not swaps:
        return []
    limit = max(2, min(int(config.max_group_size), MAX_GROUP_HARD_CAP))
    ordered_swaps = sorted(swaps, key=lambda item: (item.block, item.log_index, item.tx_hash))
    ordered_transfers = sorted(transfers, key=lambda item: (item.block, item.log_index, item.tx_hash))
    swap_blocks = [item.block for item in ordered_swaps]
    transfer_blocks = [item.block for item in ordered_transfers]

    degree: dict[str, int] = defaultdict(int)
    for transfer in ordered_transfers:
        if transfer.src in excluded or transfer.dst in excluded or transfer.src == transfer.dst:
            continue
        if transfer.amount <= 0:
            continue
        degree[transfer.src] += 1
        degree[transfer.dst] += 1
    hubs = {node for node, count in degree.items() if count > config.hub_degree}

    buys = [swap for swap in ordered_swaps if swap.side == "buy"]
    cap = max(1, int(config.max_flow_seeds))
    if len(buys) > cap:
        step = len(buys) / cap
        buys = [buys[min(len(buys) - 1, int(i * step))] for i in range(cap)]

    found: list[FlowGroup] = []
    seen: set[frozenset[tuple[str, int]]] = set()
    max_groups = max(1, int(config.max_flow_groups))

    for seed in buys:
        if len(found) >= max_groups:
            break
        # One window before the seed so an earlier buy can join. Attributed swaps
        # must themselves span at most max_block_gap, or two episodes would glue together.
        lo = seed.block - config.max_block_gap
        hi = seed.block + config.max_block_gap
        window_transfers = _slice(ordered_transfers, lo, hi, transfer_blocks)
        window_swaps = list(_slice(ordered_swaps, lo, hi, swap_blocks))
        if len(window_swaps) < 2:
            continue

        forward: dict[str, set[str]] = defaultdict(set)
        backward: dict[str, set[str]] = defaultdict(set)
        directed: list[tuple[str, str]] = []
        for transfer in window_transfers:
            src, dst = transfer.src, transfer.dst
            if src in excluded or dst in excluded or src in hubs or dst in hubs or src == dst:
                continue
            if transfer.amount <= 0:
                continue
            if dst not in forward[src]:
                forward[src].add(dst)
                backward[dst].add(src)
                directed.append((src, dst))

        seller_here = any(swap.wallet == seed.wallet and swap.side == "sell" for swap in window_swaps)
        # Nothing leaves this buyer and they do not sell. A lone round-trip is labelled elsewhere.
        if seed.wallet not in forward and not seller_here:
            continue

        downstream, overflow = _bfs([seed.wallet], forward, limit)
        if overflow or len(downstream) > limit:
            continue
        sellers = {
            swap.wallet
            for swap in window_swaps
            if swap.side == "sell" and swap.wallet in downstream
        }
        if not sellers:
            continue
        group, back_overflow = _bfs(sellers, backward, limit)
        if back_overflow:
            continue
        group |= sellers
        if len(group) < 2 or len(group) > limit:
            continue

        member_swaps = [swap for swap in window_swaps if swap.wallet in group]
        if member_swaps:
            span = max(swap.block for swap in member_swaps) - min(swap.block for swap in member_swaps)
            if span > config.max_block_gap:
                member_swaps = [
                    swap
                    for swap in member_swaps
                    if seed.block <= swap.block <= seed.block + config.max_block_gap
                ]
                kept = {swap.wallet for swap in member_swaps}
                group = {wallet for wallet in group if wallet in kept}
        if len(group) < 2:
            continue
        buy_amount = sum(swap.amount for swap in member_swaps if swap.side == "buy")
        sell_amount = sum(swap.amount for swap in member_swaps if swap.side == "sell")
        denom = max(buy_amount, sell_amount)
        if buy_amount <= 0 or sell_amount <= 0 or denom <= 0:
            continue
        ratio = abs(buy_amount - sell_amount) / denom
        if ratio > config.net_ratio + 1e-12:
            continue

        buyers = {swap.wallet for swap in member_swaps if swap.side == "buy"}
        seller_wallets = {swap.wallet for swap in member_swaps if swap.side == "sell"}
        if not buyers or not seller_wallets:
            continue
        keys = frozenset((swap.tx_hash, swap.log_index) for swap in member_swaps)
        if keys in seen:
            continue
        seen.add(keys)
        has_cycle = _has_directed_cycle(group, directed)
        pattern = classify_pattern(len(group), len(buyers), len(seller_wallets), has_cycle=has_cycle)
        blocks = [swap.block for swap in member_swaps]
        found.append(
            FlowGroup(
                wallets=tuple(sorted(group)),
                swaps=member_swaps,
                n_buyers=len(buyers),
                n_sellers=len(seller_wallets),
                n_wallets=len(group),
                pattern_type=pattern,
                net_ratio=ratio,
                start_block=min(blocks),
                end_block=max(blocks),
                buy_amount=buy_amount,
                sell_amount=sell_amount,
            )
        )

    return _maximal(found)[:max_groups]


def _maximal(groups: list[FlowGroup]) -> list[FlowGroup]:
    ranked = sorted(groups, key=lambda group: (-len(group.swaps), group.start_block, group.wallets))
    kept: list[FlowGroup] = []
    covered: list[frozenset[tuple[str, int]]] = []
    for group in ranked:
        keys = frozenset((swap.tx_hash, swap.log_index) for swap in group.swaps)
        if any(keys <= other for other in covered):
            continue
        kept.append(group)
        covered.append(keys)
    kept.sort(key=lambda group: (group.start_block, group.pattern_type, group.wallets))
    return kept

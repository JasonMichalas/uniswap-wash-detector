from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha1

from washdetect.config import HeuristicConfig
from washdetect.events import SwapEvent, TransferEvent


@dataclass
class _Cluster:
    kind: str
    swaps: list[SwapEvent]
    net_ratio: float
    start_block: int
    end_block: int

    @property
    def wallets(self) -> tuple[str, ...]:
        return tuple(sorted({s.wallet for s in self.swaps}))

    @property
    def size(self) -> int:
        return len(self.wallets)


def score_confidence(
    net_ratio: float,
    block_span: int,
    n_wallets: int,
    *,
    limit_ratio: float,
    limit_span: int,
) -> float:
    """A bare match is about 0.5; a tighter net and a shorter span score higher."""
    tightness = 1.0 - min(max(net_ratio, 0.0) / limit_ratio, 1.0) if limit_ratio else 0.0
    freshness = 1.0 - min(max(block_span, 0) / limit_span, 1.0) if limit_span else 0.0
    base = 0.45 * tightness + 0.25 * freshness + 0.50
    if n_wallets >= 2:
        base += 0.05
    return round(min(1.0, max(0.0, base)), 4)


def _cycle_id(kind: str, wallets: tuple[str, ...], start: int, end: int) -> str:
    raw = f"{kind}|{','.join(wallets)}|{start}|{end}"
    return sha1(raw.encode()).hexdigest()[:12]


def _amounts_close(amounts: list[float], tol: float) -> bool:
    if not amounts:
        return False
    hi = max(amounts)
    lo = min(amounts)
    if hi <= 0:
        return False
    return (hi - lo) / hi <= tol + 1e-12


def find_round_trips(swaps: list[SwapEvent], config: HeuristicConfig) -> list[_Cluster]:
    """Smallest window that nets out. The caller has already limited this to one trader."""
    clusters: list[_Cluster] = []
    ordered = sorted(swaps, key=lambda s: (s.block, s.log_index, s.tx_hash))
    i = 0
    n = len(ordered)
    while i < n:
        buy = 0.0
        sell = 0.0
        found: int | None = None
        ratio = 1.0
        for j in range(i, n):
            if ordered[j].block - ordered[i].block > config.max_block_gap:
                break
            if j - i + 1 > config.max_swaps:
                break
            if ordered[j].side == "buy":
                buy += ordered[j].amount
            else:
                sell += ordered[j].amount
            count = j - i + 1
            if count < config.min_swaps or buy <= 0 or sell <= 0:
                continue
            denom = max(buy, sell)
            ratio = abs(buy - sell) / denom if denom else 1.0
            if ratio <= config.net_ratio + 1e-12:
                found = j
                break
        if found is None:
            i += 1
            continue
        group = ordered[i : found + 1]
        clusters.append(
            _Cluster(
                kind="self" if len({s.wallet for s in group}) == 1 else "linked_pair",
                swaps=group,
                net_ratio=ratio,
                start_block=group[0].block,
                end_block=group[-1].block,
            )
        )
        i = found + 1
    return clusters


@dataclass
class _Link:
    a: str
    b: str
    start_block: int
    end_block: int


def _canon_cycle(wallets: list[str]) -> tuple[str, ...]:
    pivot = min(range(len(wallets)), key=lambda i: wallets[i])
    return tuple(wallets[pivot:] + wallets[:pivot])


def find_transfer_cycles(
    transfers: list[TransferEvent],
    excluded: set[str],
    config: HeuristicConfig,
) -> list[tuple[tuple[str, ...], int, int, float]]:
    """Hubs are removed so an airdrop sender does not invent a cycle."""
    degree: dict[str, int] = defaultdict(int)
    usable: list[TransferEvent] = []
    for transfer in transfers:
        if transfer.src in excluded or transfer.dst in excluded:
            continue
        if transfer.src == transfer.dst or transfer.amount <= 0:
            continue
        degree[transfer.src] += 1
        degree[transfer.dst] += 1
        usable.append(transfer)
    hubs = {node for node, count in degree.items() if count > config.hub_degree}

    adj: dict[str, list[TransferEvent]] = defaultdict(list)
    for transfer in usable:
        if transfer.src in hubs or transfer.dst in hubs:
            continue
        adj[transfer.src].append(transfer)
    for edges in adj.values():
        edges.sort(key=lambda item: (-item.amount, item.block))
        del edges[config.max_edges_per_node :]

    found: list[tuple[tuple[str, ...], int, int, float]] = []
    seen: set[tuple[str, ...]] = set()
    visits = 0

    def dfs(path: list[str], amounts: list[float], blocks: list[int], used: set[tuple[str, int]]) -> None:
        nonlocal visits
        if len(found) >= config.max_cycles or visits >= config.max_dfs_visits:
            return
        node = path[-1]
        for transfer in adj.get(node, []):
            visits += 1
            if visits >= config.max_dfs_visits or len(found) >= config.max_cycles:
                return
            edge_id = (transfer.tx_hash, transfer.log_index)
            if edge_id in used:
                continue
            if blocks and transfer.block < blocks[-1]:
                continue
            next_blocks = blocks + [transfer.block]
            if next_blocks and max(next_blocks) - min(next_blocks) > config.max_block_gap:
                continue
            if transfer.dst == path[0] and len(path) >= 2:
                all_amounts = amounts + [transfer.amount]
                if _amounts_close(all_amounts, config.net_ratio):
                    key = _canon_cycle(path)
                    if key not in seen:
                        seen.add(key)
                        found.append(
                            (key, min(next_blocks), max(next_blocks), sum(all_amounts) / len(all_amounts))
                        )
                continue
            if transfer.dst in path or transfer.dst in excluded or transfer.dst in hubs:
                continue
            if len(path) >= config.max_cycle_len:
                continue
            used.add(edge_id)
            dfs(path + [transfer.dst], amounts + [transfer.amount], next_blocks, used)
            used.discard(edge_id)

    for start in list(adj):
        if len(found) >= config.max_cycles or visits >= config.max_dfs_visits:
            break
        dfs([start], [], [], set())
    found.sort(key=lambda item: -item[3])
    return found[: config.max_cycles]


def _build_links(
    transfers: list[TransferEvent],
    active: set[str],
    excluded: set[str],
    config: HeuristicConfig,
) -> list[_Link]:
    degree: dict[str, int] = defaultdict(int)
    cleaned: list[TransferEvent] = []
    for transfer in transfers:
        if transfer.src in excluded or transfer.dst in excluded or transfer.src == transfer.dst:
            continue
        if transfer.amount <= 0:
            continue
        degree[transfer.src] += 1
        degree[transfer.dst] += 1
        cleaned.append(transfer)
    hubs = {node for node, count in degree.items() if count > config.hub_degree}

    out: dict[str, list[TransferEvent]] = defaultdict(list)
    links: list[_Link] = []
    for transfer in cleaned:
        if transfer.src in hubs or transfer.dst in hubs:
            continue
        out[transfer.src].append(transfer)
        if transfer.src in active and transfer.dst in active:
            links.append(_Link(transfer.src, transfer.dst, transfer.block, transfer.block))

    for src, edges in out.items():
        if src not in active:
            continue
        for first in edges:
            mid = first.dst
            if mid in hubs or mid in excluded:
                continue
            for second in out.get(mid, []):
                if second.block < first.block:
                    continue
                if second.block - first.block > config.max_block_gap:
                    continue
                dst = second.dst
                if dst == src or dst not in active or dst in hubs:
                    continue
                links.append(_Link(src, dst, first.block, second.block))

    # Earliest inbound from a non-hub: wallets that share a funder.
    first_in: dict[str, tuple[str, int]] = {}
    for transfer in sorted(cleaned, key=lambda item: (item.block, item.log_index)):
        if transfer.dst in active and transfer.dst not in first_in and transfer.src not in hubs:
            first_in[transfer.dst] = (transfer.src, transfer.block)
    by_funder: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for wallet, (funder, block) in first_in.items():
        by_funder[funder].append((wallet, block))
    for members in by_funder.values():
        if len(members) < 2 or len(members) > config.hub_degree:
            continue
        for i, (w1, b1) in enumerate(members):
            for w2, b2 in members[i + 1 :]:
                if abs(b1 - b2) <= config.max_block_gap:
                    links.append(_Link(w1, w2, min(b1, b2), max(b1, b2)))

    links.sort(key=lambda link: (link.start_block, link.a, link.b))
    if len(links) > config.max_links:  # hard cap; ranking by volume is not worth the time
        links = links[: config.max_links]
    return links


def _swaps_by_wallet(swaps: list[SwapEvent]) -> dict[str, list[SwapEvent]]:
    grouped: dict[str, list[SwapEvent]] = defaultdict(list)
    for swap in swaps:
        grouped[swap.wallet].append(swap)
    for rows in grouped.values():
        rows.sort(key=lambda item: (item.block, item.log_index))
    return grouped


def _in_blocks(swaps: list[SwapEvent], lo: int, hi: int) -> list[SwapEvent]:
    return [swap for swap in swaps if lo <= swap.block <= hi]


def _shape(swaps: list[SwapEvent], wallets: tuple[str, ...] | set[str], *, has_cycle: bool = False) -> tuple[str, int, int, int]:
    from washdetect.flow import classify_pattern

    buyers = {swap.wallet for swap in swaps if swap.side == "buy"}
    sellers = {swap.wallet for swap in swaps if swap.side == "sell"}
    n_wallets = len(wallets) if wallets else len({swap.wallet for swap in swaps})
    if n_wallets <= 1:
        return "round_trip", len(buyers), len(sellers), max(n_wallets, 1 if swaps else 0)
    return (
        classify_pattern(n_wallets, len(buyers), len(sellers), has_cycle=has_cycle),
        len(buyers),
        len(sellers),
        n_wallets,
    )

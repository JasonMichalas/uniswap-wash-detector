"""Rule-based wash labels, in the style of Gan et al. 2024 (ACM TOIT).

The paper's rule, as used here:

* Look at sequences of 2 to 10 swaps that sit inside a 200-block window.
* If the net change in the token position is at most 5% of the larger side
  (buys nearly equal sells), those swaps are a round-trip.
* A single wallet can round-trip with itself.
* Several wallets count as one trader when they are linked by a token
  transfer of one or two hops, or funded by the same non-hub address, and
  the link is close in time to the swaps.

Native ETH transfers, which the paper uses for the hop graph, are not
visible to `eth_getLogs`. The link graph is built from the target token's
ERC-20 transfers instead. Hubs (airdrop senders, routers, pools) are not
allowed to glue unrelated wallets together.

The output is a hard label plus a heuristic confidence in [0, 1]. That
confidence is not a probability. Trained models replace it with
`predict_proba`, and `probability_weighted_wash_percent` turns those
probabilities into a percentage and a bootstrap interval.

Besides the pairwise Gan pass, `washdetect.flow` labels a connected flow
group whenever the wallets' pooled buys and sells cancel within tolerance.
That is what catches fan-in, fan-out, and M:N shuffles whose pairs do not
each net out. Each cycle carries `pattern_type`, `n_buyers`, `n_sellers`,
and `n_wallets`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from hashlib import sha1

from washdetect.config import HeuristicConfig

ZERO = "0x" + "00" * 20


@dataclass
class SwapEvent:
    wallet: str
    block: int
    side: str
    amount: float
    usd: float
    tx_hash: str
    log_index: int
    pool: str = ""


@dataclass
class TransferEvent:
    src: str
    dst: str
    block: int
    amount: float
    tx_hash: str
    log_index: int


@dataclass
class Cycle:
    cycle_id: str
    size: int
    kind: str
    wallets: tuple[str, ...]
    volume_usd: float
    confidence: float
    start_block: int
    end_block: int
    swap_ids: list[tuple[str, int]] = field(default_factory=list)
    token_amount: float = 0.0
    # pattern_type is the stable label the editor filters on:
    # round_trip, ring, one_to_many, many_to_one, many_to_many.
    # n_wallets counts intermediates that only forward tokens.
    # n_buyers and n_sellers count wallets that trade that side
    # (a wallet that does both is in each set).
    pattern_type: str = ""
    n_buyers: int = 0
    n_sellers: int = 0
    n_wallets: int = 0


@dataclass
class WalletWash:
    wallet: str
    confidence: float
    wash_volume_usd: float
    round_trip_count: int
    ring_count: int
    swap_count: int
    net_ratio: float
    volume_usd: float


@dataclass
class Detection:
    cycles: list[Cycle]
    wallets: list[WalletWash]
    # (tx_hash, log_index) -> (confidence, cycle_id)
    flagged: dict[tuple[str, int], tuple[float, str]]

    @property
    def wash_volume_usd(self) -> float:
        return self._wash_volume

    @property
    def total_volume_usd(self) -> float:
        return self._total_volume

    _wash_volume: float = 0.0
    _total_volume: float = 0.0

    @property
    def wash_pct(self) -> float:
        if self._total_volume <= 0:
            return 0.0
        return 100.0 * self._wash_volume / self._total_volume


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
    """Map a match onto [0, 1]. A bare Gan match scores about 0.5; a tight one scores higher.

    tightness is 1 when the two sides match exactly and 0 at the 5% boundary.
    freshness is 1 when the window is a single block and 0 at 200 blocks.
    """
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
    """Greedy smallest-window round-trips.

    Swaps must already be restricted to the wallets (one wallet, or a linked
    group) the caller wants to treat as a single trader. The inner window
    stops at `max_swaps`, so this is linear in the number of swaps.
    """
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
    """Directed token-transfer cycles of length 2..max_cycle_len.

    Edges must be in non-decreasing block order and the whole cycle must fit
    in `max_block_gap`. Amounts along the cycle must agree within `net_ratio`.
    Hub addresses (very high degree) are removed so an airdrop does not
    invent a cycle through the sender.
    """
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
    """1-hop, 2-hop, and shared-funder links between wallets that actually swapped."""
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

    # Outgoing edges that do not touch a hub, grouped for the 2-hop walk.
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

    # Shared funder: the earliest inbound transfer from a non-hub.
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

    # Cap, preferring links whose endpoints both trade a lot is unnecessary;
    # a hard cap keeps a busy token inside the time budget. Stable order.
    links.sort(key=lambda link: (link.start_block, link.a, link.b))
    if len(links) > config.max_links:
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


class WashDetector:
    """Rule labeler for one tolerance, block window, and group-size cap.

    `tolerance` is the fraction by which buys and sells may differ (the
    paper uses 5%). `window_blocks` is how far apart those trades may sit.
    """

    def __init__(
        self,
        tolerance: float | None = None,
        window_blocks: int | None = None,
        max_group_size: int | None = None,
        *,
        config: HeuristicConfig | None = None,
    ) -> None:
        from dataclasses import replace

        base = config or HeuristicConfig()
        changes: dict[str, float | int] = {}
        if tolerance is not None:
            changes["net_ratio"] = tolerance
        if window_blocks is not None:
            changes["max_block_gap"] = window_blocks
        if max_group_size is not None:
            changes["max_group_size"] = max_group_size
        self.config = replace(base, **changes) if changes else base

    def find_groups(
        self,
        swaps: list[SwapEvent],
        transfers: list[TransferEvent],
        excluded: set[str] | None = None,
    ) -> Detection:
        return _find_groups(swaps, transfers, excluded=excluded, config=self.config)

    def wash_percent(
        self,
        swaps: list[SwapEvent],
        transfers: list[TransferEvent],
        excluded: set[str] | None = None,
    ) -> float:
        return self.find_groups(swaps, transfers, excluded).wash_pct


def detect(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    *,
    excluded: set[str] | None = None,
    config: HeuristicConfig | None = None,
) -> Detection:
    """Run the heuristic. Pure function: no database and no network."""
    return WashDetector(config=config).find_groups(swaps, transfers, excluded)


def _find_groups(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    *,
    excluded: set[str] | None = None,
    config: HeuristicConfig | None = None,
) -> Detection:
    config = config or HeuristicConfig()
    excluded = {addr.lower() for addr in (excluded or set())}
    excluded.add(ZERO)

    norm_swaps = [
        SwapEvent(
            wallet=s.wallet.lower(),
            block=int(s.block),
            side=s.side,
            amount=float(s.amount),
            usd=float(s.usd or 0.0),
            tx_hash=s.tx_hash.lower(),
            log_index=int(s.log_index),
            pool=(s.pool or "").lower(),
        )
        for s in swaps
        if s.wallet and s.side in {"buy", "sell"} and s.amount > 0
    ]
    norm_transfers = [
        TransferEvent(
            src=t.src.lower(),
            dst=t.dst.lower(),
            block=int(t.block),
            amount=float(t.amount),
            tx_hash=t.tx_hash.lower(),
            log_index=int(t.log_index),
        )
        for t in transfers
        if t.src and t.dst and t.amount > 0
    ]

    books = _swaps_by_wallet(norm_swaps)
    active = set(books)
    clusters: list[_Cluster] = []
    for wallet_swaps in books.values():
        clusters.extend(find_round_trips(wallet_swaps, config))

    links = _build_links(norm_transfers, active, excluded, config)
    seen_windows: set[tuple[tuple[str, ...], int, int]] = set()

    def consider(group: list[SwapEvent]) -> None:
        for cluster in find_round_trips(group, config):
            if cluster.size < 2:
                continue
            sig = (cluster.wallets, cluster.start_block, cluster.end_block)
            if sig in seen_windows:
                continue
            seen_windows.add(sig)
            cluster.kind = "linked_pair"
            clusters.append(cluster)

    # Pairs.
    for link in links:
        lo = link.start_block - config.max_block_gap
        hi = link.end_block + config.max_block_gap
        group = _in_blocks(books.get(link.a, []), lo, hi) + _in_blocks(books.get(link.b, []), lo, hi)
        consider(group)

    # Triangles, so a three-wallet ring whose pairs do not each net out can
    # still be caught when the three books together do.
    neighbours: dict[str, set[str]] = defaultdict(set)
    for link in links:
        neighbours[link.a].add(link.b)
        neighbours[link.b].add(link.a)
    triangles = 0
    tested: set[tuple[str, str, str]] = set()
    for a, nbs in neighbours.items():
        for b in nbs:
            if b <= a:
                continue
            for c in nbs & neighbours.get(b, set()):
                if c <= b:
                    continue
                key = (a, b, c)
                if key in tested:
                    continue
                tested.add(key)
                triangles += 1
                if triangles > config.max_triangles:
                    break
                group = books.get(a, []) + books.get(b, []) + books.get(c, [])
                consider(group)
            if triangles > config.max_triangles:
                break

    transfer_cycles = find_transfer_cycles(norm_transfers, excluded, config)

    flagged: dict[tuple[str, int], tuple[float, str]] = {}
    cycles: list[Cycle] = []

    def flag_swap(swap: SwapEvent, confidence: float, cycle_id: str) -> None:
        key = (swap.tx_hash, swap.log_index)
        current = flagged.get(key)
        if current is None or confidence > current[0]:
            flagged[key] = (confidence, cycle_id)

    for cluster in clusters:
        span = cluster.end_block - cluster.start_block
        confidence = score_confidence(
            cluster.net_ratio,
            span,
            cluster.size,
            limit_ratio=config.net_ratio,
            limit_span=config.max_block_gap,
        )
        cycle_id = _cycle_id(cluster.kind, cluster.wallets, cluster.start_block, cluster.end_block)
        ids = [(s.tx_hash, s.log_index) for s in cluster.swaps]
        volume = sum(s.usd for s in cluster.swaps)
        pattern, n_buyers, n_sellers, n_wallets = _shape(cluster.swaps, cluster.wallets)
        cycles.append(
            Cycle(
                cycle_id=cycle_id,
                size=n_wallets,
                kind=cluster.kind,
                wallets=cluster.wallets,
                volume_usd=volume,
                confidence=confidence,
                start_block=cluster.start_block,
                end_block=cluster.end_block,
                swap_ids=ids,
                token_amount=sum(s.amount for s in cluster.swaps),
                pattern_type=pattern,
                n_buyers=n_buyers,
                n_sellers=n_sellers,
                n_wallets=n_wallets,
            )
        )
        for swap in cluster.swaps:
            flag_swap(swap, confidence, cycle_id)

    for wallets, start, end, amount in transfer_cycles:
        span = end - start
        ratio = 0.0  # amounts were already within tolerance; the cycle stores the tight match
        confidence = score_confidence(
            ratio,
            span,
            len(wallets),
            limit_ratio=config.net_ratio,
            limit_span=config.max_block_gap,
        )
        cycle_id = _cycle_id("transfer_cycle", wallets, start, end)
        members = set(wallets)
        matched: list[SwapEvent] = []
        for swap in norm_swaps:
            if swap.wallet not in members or not start <= swap.block <= end:
                continue
            if amount <= 0:
                continue
            if abs(swap.amount - amount) / max(swap.amount, amount) <= config.net_ratio + 1e-12:
                matched.append(swap)
        volume = sum(s.usd for s in matched)
        pattern, n_buyers, n_sellers, n_wallets = _shape(matched, wallets, has_cycle=True)
        if not matched:
            pattern, n_buyers, n_sellers, n_wallets = "ring", 0, 0, len(wallets)
        cycles.append(
            Cycle(
                cycle_id=cycle_id,
                size=n_wallets,
                kind="transfer_cycle",
                wallets=wallets,
                volume_usd=volume,
                confidence=confidence,
                start_block=start,
                end_block=end,
                swap_ids=[(s.tx_hash, s.log_index) for s in matched],
                token_amount=amount,
                pattern_type=pattern,
                n_buyers=n_buyers,
                n_sellers=n_sellers,
                n_wallets=n_wallets,
            )
        )
        for swap in matched:
            flag_swap(swap, confidence, cycle_id)

    from washdetect.flow import find_flow_groups

    flow_groups = find_flow_groups(norm_swaps, norm_transfers, excluded, config)
    by_swaps: dict[frozenset[tuple[str, int]], Cycle] = {}
    for cycle in cycles:
        by_swaps.setdefault(frozenset(cycle.swap_ids), cycle)
    for group in flow_groups:
        keys = frozenset((swap.tx_hash, swap.log_index) for swap in group.swaps)
        # A pairwise round-trip can be the swap set of a larger flow (the
        # intermediates do not themselves trade). Keep one cycle and record
        # the fuller shape. A strict subset is dropped so the same volume
        # is not listed twice; the swaps stay flagged.
        if keys in by_swaps:
            current = by_swaps[keys]
            current.pattern_type = group.pattern_type
            current.n_buyers = group.n_buyers
            current.n_sellers = group.n_sellers
            current.n_wallets = group.n_wallets
            current.size = group.n_wallets
            current.wallets = group.wallets
            continue
        covered = [other for other in by_swaps if other and other < keys]
        for other in covered:
            cycles.remove(by_swaps.pop(other))
        span = group.end_block - group.start_block
        confidence = score_confidence(
            group.net_ratio,
            span,
            group.n_wallets,
            limit_ratio=config.net_ratio,
            limit_span=config.max_block_gap,
        )
        cycle_id = _cycle_id(group.pattern_type, group.wallets, group.start_block, group.end_block)
        cycle = Cycle(
            cycle_id=cycle_id,
            size=group.n_wallets,
            kind=group.pattern_type,
            wallets=group.wallets,
            volume_usd=sum(swap.usd for swap in group.swaps),
            confidence=confidence,
            start_block=group.start_block,
            end_block=group.end_block,
            swap_ids=[(swap.tx_hash, swap.log_index) for swap in group.swaps],
            token_amount=group.buy_amount + group.sell_amount,
            pattern_type=group.pattern_type,
            n_buyers=group.n_buyers,
            n_sellers=group.n_sellers,
            n_wallets=group.n_wallets,
        )
        cycles.append(cycle)
        by_swaps[keys] = cycle
        for swap in group.swaps:
            flag_swap(swap, confidence, cycle_id)

    # Wallet rollup. Every wallet that swapped is listed, with confidence 0
    # when none of its swaps were flagged, so a filter can threshold it.
    wallet_rows: list[WalletWash] = []
    for wallet, wallet_swaps in books.items():
        buy = sum(s.amount for s in wallet_swaps if s.side == "buy")
        sell = sum(s.amount for s in wallet_swaps if s.side == "sell")
        denom = max(buy, sell)
        net_ratio = abs(buy - sell) / denom if denom else 0.0
        flagged_swaps = [s for s in wallet_swaps if (s.tx_hash, s.log_index) in flagged]
        confidence = max((flagged[(s.tx_hash, s.log_index)][0] for s in flagged_swaps), default=0.0)
        self_n = sum(1 for c in cycles if c.kind == "self" and wallet in c.wallets)
        ring_n = sum(1 for c in cycles if c.kind != "self" and wallet in c.wallets)
        wallet_rows.append(
            WalletWash(
                wallet=wallet,
                confidence=confidence,
                wash_volume_usd=sum(s.usd for s in flagged_swaps),
                round_trip_count=self_n,
                ring_count=ring_n,
                swap_count=len(wallet_swaps),
                net_ratio=net_ratio,
                volume_usd=sum(s.usd for s in wallet_swaps),
            )
        )
    wallet_rows.sort(key=lambda row: (-row.wash_volume_usd, row.wallet))

    total = sum(s.usd for s in norm_swaps)
    wash = sum(s.usd for s in norm_swaps if (s.tx_hash, s.log_index) in flagged)
    detection = Detection(cycles=cycles, wallets=wallet_rows, flagged=flagged)
    detection._total_volume = total
    detection._wash_volume = wash
    return detection

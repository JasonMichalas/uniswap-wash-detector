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

from washdetect.config import HeuristicConfig
from washdetect.detector_match import (
    _build_links,
    _Cluster,
    _cycle_id,
    _in_blocks,
    _shape,
    _swaps_by_wallet,
    find_round_trips,
    find_transfer_cycles,
    score_confidence,
)
from washdetect.events import Cycle, Detection, SwapEvent, TransferEvent, WalletWash

ZERO = "0x" + "00" * 20

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

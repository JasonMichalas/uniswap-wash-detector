from __future__ import annotations

from collections import defaultdict

from washdetect.config import HeuristicConfig, hop_addresses
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
from washdetect.trades import TradeResolver

ZERO = "0x" + "00" * 20

class WashDetector:
    """`tolerance` is how far buys and sells may differ. The paper uses 5% inside 200 blocks."""

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


def detect(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    *,
    excluded: set[str] | None = None,
    config: HeuristicConfig | None = None,
) -> Detection:
    """No database and no network."""
    return WashDetector(config=config).find_groups(swaps, transfers, excluded)


def _find_groups(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    *,
    excluded: set[str] | None = None,
    config: HeuristicConfig | None = None,
) -> Detection:
    config = config or HeuristicConfig()
    enabled = set(config.enabled_rules)
    excluded = {addr.lower() for addr in (excluded or set())}
    excluded.add(ZERO)
    excluded.update(hop_addresses())

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

    excluded.update(s.pool for s in norm_swaps if s.pool)
    norm_swaps = TradeResolver(norm_transfers, excluded, config.net_ratio).resolve(norm_swaps)
    books = _swaps_by_wallet(norm_swaps)
    active = set(books)
    clusters: list[_Cluster] = []
    if "R1" in enabled:
        for wallet_swaps in books.values():
            clusters.extend(find_round_trips(wallet_swaps, config, config.max_self_swaps))

    links = _build_links(norm_transfers, active, excluded, config) if "R2" in enabled else []
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

    for link in links:
        lo = link.start_block - config.max_block_gap
        hi = link.end_block + config.max_block_gap
        group = _in_blocks(books.get(link.a, []), lo, hi) + _in_blocks(books.get(link.b, []), lo, hi)
        consider(group)

    # A three-wallet ring can net out together when no pair does.
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

    transfer_cycles = find_transfer_cycles(norm_transfers, excluded, config) if "R4" in enabled else []

    flagged: dict[tuple[str, int], tuple[float, str]] = {}
    washed_amount: dict[tuple[str, int], float] = {}
    reasons: dict[tuple[str, int], dict[str, list[str] | str]] = {}
    cycles: list[Cycle] = []

    def flag_swap(
        swap: SwapEvent,
        confidence: float,
        cycle_id: str,
        *,
        rule: str = "",
        why: str = "",
        path: str = "",
        washed: float | None = None,
    ) -> None:
        key = (swap.tx_hash, swap.log_index)
        already = key in flagged
        current = flagged.get(key)
        if current is None or confidence > current[0]:
            flagged[key] = (confidence, cycle_id)
        if rule or why or path:
            rec = reasons.setdefault(key, {"rules": [], "why": [], "path": ""})
            rules = rec["rules"]
            whys = rec["why"]
            assert isinstance(rules, list) and isinstance(whys, list)
            if rule and rule not in rules:
                rules.append(rule)
            if why and why not in whys:
                whys.append(why)
            if path:
                rec["path"] = path
        if washed is not None:
            if not already:
                washed_amount[key] = washed
            elif key in washed_amount:
                washed_amount[key] = min(swap.amount, washed_amount[key] + washed)

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
        rule = "R1" if cluster.kind == "self" else "R2"
        why = _cycle_why(rule, cluster.swaps, cluster.wallets, cluster.start_block, cluster.end_block)
        path = _swap_path(cluster.swaps)
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
                rules=rule,
                why=why,
                path=path,
            )
        )
        for swap in cluster.swaps:
            same_tx = len({item.tx_hash for item in cluster.swaps}) == 1
            flag_swap(swap, confidence, cycle_id, rule=rule, why=why, path=path)
            if same_tx and "R6" in enabled:
                flag_swap(swap, confidence, cycle_id, rule="R6", why=why, path=path)

    for wallets, start, end, amount in transfer_cycles:
        span = end - start
        ratio = 0.0  # cycle amounts already matched within tolerance
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
        if not matched:
            continue
        volume = sum(s.usd for s in matched)
        pattern, n_buyers, n_sellers, n_wallets = _shape(matched, wallets, has_cycle=True)
        why = (
            f"Tokens moved in a circle through {len(wallets)} wallets "
            f"between blocks {start} and {end}."
        )
        path = " → ".join(wallets)
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
                rules="R4",
                why=why,
                path=path,
            )
        )
        for swap in matched:
            flag_swap(swap, confidence, cycle_id, rule="R4", why=why, path=path)

    from washdetect.flow import find_flow_groups

    flow_groups = find_flow_groups(norm_swaps, norm_transfers, excluded, config) if "R2" in enabled else []
    by_swaps: dict[frozenset[tuple[str, int]], Cycle] = {}
    for cycle in cycles:
        by_swaps.setdefault(frozenset(cycle.swap_ids), cycle)
    for group in flow_groups:
        keys = frozenset((swap.tx_hash, swap.log_index) for swap in group.swaps)
        # Same swap set keeps the fuller shape. A strict subset is dropped so volume is not listed twice.
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
        why = (
            f"{group.n_buyers} buyer(s) and {group.n_sellers} seller(s) "
            f"passed tokens and the amounts cancel "
            f"({group.buy_amount:g} bought, {group.sell_amount:g} sold)."
        )
        path = _swap_path(group.swaps)
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
            rules="R2",
            why=why,
            path=path,
        )
        cycles.append(cycle)
        by_swaps[keys] = cycle
        for swap in group.swaps:
            flag_swap(swap, confidence, cycle_id, rule="R2", why=why, path=path)

    from washdetect.rules import find_rule_hits

    for hit in find_rule_hits(norm_swaps, norm_transfers, excluded, config):
        cycle_id = _cycle_id(hit.rule, hit.wallets, hit.start_block, hit.end_block)
        fresh = [swap for swap in hit.swaps if (swap.tx_hash, swap.log_index) not in flagged]
        if not fresh:
            for swap in hit.swaps:
                flag_swap(swap, 0.72, cycle_id, rule=hit.rule, why=hit.why, path=hit.path)
            continue
        cycles.append(
            Cycle(
                cycle_id=cycle_id,
                size=len(hit.wallets),
                kind=hit.rule,
                wallets=hit.wallets,
                volume_usd=sum(swap.usd for swap in hit.swaps),
                confidence=0.72,
                start_block=hit.start_block,
                end_block=hit.end_block,
                swap_ids=[(swap.tx_hash, swap.log_index) for swap in hit.swaps],
                token_amount=sum(hit.portions.values()),
                pattern_type=hit.pattern_type,
                n_buyers=hit.n_buyers,
                n_sellers=hit.n_sellers,
                n_wallets=len(hit.wallets),
                rules=hit.rule,
                why=hit.why,
                path=hit.path,
            )
        )
        for swap in hit.swaps:
            key = (swap.tx_hash, swap.log_index)
            flag_swap(
                swap,
                0.72,
                cycle_id,
                rule=hit.rule,
                why=hit.why,
                path=hit.path,
                washed=hit.portions.get(key),
            )

    wallet_rows: list[WalletWash] = []
    # Every swapper is listed. Confidence 0 lets a threshold filter drop them.
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
                wash_volume_usd=sum(_flagged_usd(s, flagged, washed_amount) for s in flagged_swaps),
                round_trip_count=self_n,
                ring_count=ring_n,
                swap_count=len(wallet_swaps),
                net_ratio=net_ratio,
                volume_usd=sum(s.usd for s in wallet_swaps),
            )
        )
    wallet_rows.sort(key=lambda row: (-row.wash_volume_usd, row.wallet))

    total = sum(s.usd for s in norm_swaps)
    wash = sum(_flagged_usd(s, flagged, washed_amount) for s in norm_swaps)
    plain_reasons = {
        key: {
            "rules": ",".join(str(rule) for rule in rec["rules"]),
            "why": " ".join(str(line) for line in rec["why"]),
            "path": str(rec["path"]),
        }
        for key, rec in reasons.items()
    }
    detection = Detection(
        cycles=cycles,
        wallets=wallet_rows,
        flagged=flagged,
        washed_amount=washed_amount,
        reasons=plain_reasons,
    )
    detection._total_volume = total
    detection._wash_volume = wash
    return detection


def _swap_path(swaps: list[SwapEvent]) -> str:
    ordered = sorted(swaps, key=lambda swap: (swap.block, swap.log_index, swap.tx_hash))
    return " → ".join(
        f"{swap.wallet} {swap.side} {swap.amount:g} in {swap.tx_hash} at block {swap.block}" for swap in ordered
    )


def _cycle_why(rule: str, swaps: list[SwapEvent], wallets: tuple[str, ...], start: int, end: int) -> str:
    if rule == "R1" and wallets:
        return f"{wallets[0]} bought and sold about the same amount between blocks {start} and {end}."
    return (
        f"Linked wallets {', '.join(wallets)} bought and sold about the same amount "
        f"between blocks {start} and {end}."
    )


def _flagged_usd(
    swap: SwapEvent,
    flagged: dict[tuple[str, int], tuple[float, str]],
    washed_amount: dict[tuple[str, int], float],
) -> float:
    key = (swap.tx_hash, swap.log_index)
    if key not in flagged:
        return 0.0
    part = washed_amount.get(key)
    if part is None or swap.amount <= 0:
        return swap.usd
    return swap.usd * min(1.0, part / swap.amount)

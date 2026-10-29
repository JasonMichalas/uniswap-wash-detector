"""Wash rules R1–R6.

R1 is still the same-wallet round trip the older detector already found.
R2 traces token lots through transfers, including a partial amount and a long chain.
The other rules add controllers, circles, churn, and a buy and a sell in one transaction.
A swap is wash only for the token amount a rule could follow. Two strangers who both
trade, with nothing linking them, are not a group.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from washdetect.config import HeuristicConfig, hop_addresses
from washdetect.events import SwapEvent, TransferEvent

RULE_NAMES = {
    "R1": "Same wallet buys and sells",
    "R2": "Tokens passed along, then sold",
    "R3": "Wallets funded or paid together",
    "R4": "A circle of wallets",
    "R5": "Many trades, nothing left",
    "R6": "Buy and sell in one transaction",
}


@dataclass
class RuleHit:
    rule: str
    why: str
    path: str
    wallets: tuple[str, ...]
    start_block: int
    end_block: int
    portions: dict[tuple[str, int], float]
    swaps: list[SwapEvent]
    pattern_type: str
    n_buyers: int
    n_sellers: int


@dataclass
class _Lot:
    left: float
    origin: SwapEvent
    owner: str
    at_block: int
    path: list[str]
    steps: list[str] = field(default_factory=list)
    hops: int = 0
    circled: bool = False


def hop_blocklist(excluded: set[str]) -> set[str]:
    """Routers, aggregators, MEV actors, CEX hot wallets, pools, and the zero address are not hops."""
    # Uniswap v4 keeps every pool's tokens in the PoolManager, so it is a pool, not a hop.
    return {addr.lower() for addr in excluded} | hop_addresses()


def find_rule_hits(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    excluded: set[str],
    config: HeuristicConfig,
) -> list[RuleHit]:
    enabled = set(config.enabled_rules)
    blocked = hop_blocklist(excluded)
    hits: list[RuleHit] = []
    if "R2" in enabled or "R4" in enabled:
        hits.extend(_trace_lots(swaps, transfers, blocked, config, enabled))
    if "R3" in enabled:
        hits.extend(_common_controllers(swaps, transfers, blocked, config))
    if "R5" in enabled:
        hits.extend(_churn(swaps, config))
    if "R6" in enabled:
        hits.extend(_same_transaction(swaps))
    return hits


def _close(left: float, right: float, tol: float) -> bool:
    denom = max(left, right)
    if denom <= 0:
        return False
    return abs(left - right) / denom <= tol + 1e-12


def _trace_lots(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    blocked: set[str],
    config: HeuristicConfig,
    enabled: set[str],
) -> list[RuleHit]:
    """Move bought tokens along transfers. A later sell washes only the amount that arrived."""
    owned: dict[str, list[_Lot]] = defaultdict(list)
    events: list[tuple[int, int, int, str, object]] = []
    for swap in swaps:
        events.append((swap.block, 0 if swap.side == "buy" else 2, swap.log_index, swap.tx_hash, swap))
    for transfer in transfers:
        events.append((transfer.block, 1, transfer.log_index, transfer.tx_hash, transfer))
    events.sort(key=lambda item: item[:4])
    max_hops = max(1, int(config.max_group_size) - 1)
    hits: list[RuleHit] = []

    for _block, kind, _log, _tx, event in events:
        if kind == 0:
            swap = event
            assert isinstance(swap, SwapEvent)
            if swap.wallet in blocked or swap.amount <= 0:
                continue
            owned[swap.wallet].append(
                _Lot(
                    left=swap.amount,
                    origin=swap,
                    owner=swap.wallet,
                    at_block=swap.block,
                    path=[swap.wallet],
                    steps=[f"{swap.wallet} bought {swap.amount:g} in {swap.tx_hash} at block {swap.block}"],
                )
            )
            continue
        if kind == 1:
            transfer = event
            assert isinstance(transfer, TransferEvent)
            if (
                transfer.src in blocked
                or transfer.dst in blocked
                or transfer.src == transfer.dst
                or transfer.amount <= 0
            ):
                continue
            _move(owned, transfer, config, max_hops)
            continue
        swap = event
        assert isinstance(swap, SwapEvent)
        if "R2" not in enabled and "R4" not in enabled:
            continue
        if swap.wallet in blocked or swap.side != "sell":
            continue
        hit = _match_sell(owned, swap, config, enabled)
        if hit is not None:
            hits.append(hit)
    return hits


def _move(
    owned: dict[str, list[_Lot]],
    transfer: TransferEvent,
    config: HeuristicConfig,
    max_hops: int,
) -> None:
    need = transfer.amount
    sources = owned.get(transfer.src, [])
    index = 0
    while need > 1e-12 and index < len(sources):
        lot = sources[index]
        index += 1
        if lot.left <= 1e-12:
            continue
        if transfer.block < lot.at_block:
            continue
        if transfer.block - lot.origin.block > config.max_block_gap:
            continue
        if lot.hops + 1 > max_hops:
            continue
        moved = min(lot.left, need)
        lot.left -= moved
        need -= moved
        path = [*lot.path, transfer.dst]
        owned[transfer.dst].append(
            _Lot(
                left=moved,
                origin=lot.origin,
                owner=transfer.dst,
                at_block=transfer.block,
                path=path,
                steps=[
                    *lot.steps,
                    f"{transfer.src} sent {moved:g} to {transfer.dst} in {transfer.tx_hash} at block {transfer.block}",
                ],
                hops=lot.hops + 1,
                circled=lot.circled or transfer.dst in lot.path,
            )
        )


def _match_sell(
    owned: dict[str, list[_Lot]],
    sell: SwapEvent,
    config: HeuristicConfig,
    enabled: set[str],
) -> RuleHit | None:
    eligible = [
        lot
        for lot in owned.get(sell.wallet, [])
        if lot.hops >= 1
        and lot.left > 1e-12
        and sell.block >= lot.at_block
        and sell.block - lot.origin.block <= config.max_block_gap
    ]
    eligible.sort(key=lambda lot: (lot.origin.block, lot.hops, lot.origin.tx_hash))
    chosen: list[tuple[_Lot, float]] = []
    running = 0.0
    for lot in eligible:
        if running > 0 and _close(running, sell.amount, config.net_ratio):
            break
        room = sell.amount - running
        if room <= 1e-12:
            break
        take = min(lot.left, room)
        if take <= 1e-12:
            continue
        chosen.append((lot, take))
        running += take
    if not chosen or not _close(running, sell.amount, config.net_ratio):
        return None
    circled = any(lot.circled for lot, _take in chosen)
    rule = "R4" if circled and "R4" in enabled else "R2"
    if rule == "R4" and "R4" not in enabled:
        return None
    if rule == "R2" and "R2" not in enabled:
        if circled and "R4" in enabled:
            rule = "R4"
        else:
            return None
    wallets: set[str] = {sell.wallet}
    for lot, _take in chosen:
        wallets.update(lot.path)
    if len(wallets) > config.max_group_size:
        return None
    portions: dict[tuple[str, int], float] = defaultdict(float)
    steps: list[str] = []
    buyers: set[str] = set()
    for lot, take in chosen:
        lot.left -= take
        key = (lot.origin.tx_hash, lot.origin.log_index)
        portions[key] += take
        buyers.add(lot.origin.wallet)
        wallets.update(lot.path)
        steps.extend(lot.steps)
    portions[(sell.tx_hash, sell.log_index)] = running
    steps.append(f"{sell.wallet} sold {sell.amount:g} in {sell.tx_hash} at block {sell.block}")
    start = min(lot.origin.block for lot, _take in chosen)
    why = (
        f"{running:g} tokens bought and later sold after "
        f"{'a circle' if rule == 'R4' else 'being passed along'} "
        f"({len(wallets)} wallets, blocks {start}–{sell.block})."
    )
    swaps = [lot.origin for lot, _take in chosen] + [sell]
    return RuleHit(
        rule=rule,
        why=why,
        path=" → ".join(dict.fromkeys(steps)),
        wallets=tuple(sorted(wallets)),
        start_block=start,
        end_block=sell.block,
        portions=dict(portions),
        swaps=swaps,
        pattern_type="ring" if rule == "R4" else _flow_pattern(buyers, {sell.wallet}, len(wallets)),
        n_buyers=len(buyers),
        n_sellers=1,
    )


def _flow_pattern(buyers: set[str], sellers: set[str], n_wallets: int) -> str:
    if len(buyers) > 1 and len(sellers) > 1:
        return "many_to_many"
    if len(buyers) > 1:
        return "many_to_one"
    if len(sellers) > 1:
        return "one_to_many"
    if n_wallets > 1:
        return "ring"
    return "round_trip"


def _common_controllers(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    blocked: set[str],
    config: HeuristicConfig,
) -> list[RuleHit]:
    """Wallets that traded and share a funder, or send proceeds to the same wallet."""
    traded = {swap.wallet for swap in swaps if swap.wallet not in blocked}
    books: dict[str, list[SwapEvent]] = defaultdict(list)
    for swap in swaps:
        if swap.wallet in traded:
            books[swap.wallet].append(swap)
    incoming: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    outgoing: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for transfer in transfers:
        if transfer.src in blocked or transfer.dst in blocked or transfer.src == transfer.dst:
            continue
        if transfer.dst in traded and transfer.src not in traded:
            incoming[transfer.src].append((transfer.dst, transfer.block, transfer.tx_hash))
        if transfer.src in traded and transfer.dst not in traded:
            outgoing[transfer.dst].append((transfer.src, transfer.block, transfer.tx_hash))
    hits: list[RuleHit] = []
    for funder, rows in incoming.items():
        hit = _controller_hit(books, rows, config, f"funded by {funder}")
        if hit is not None:
            hits.append(hit)
    for sink, rows in outgoing.items():
        hit = _controller_hit(books, rows, config, f"proceeds sent to {sink}")
        if hit is not None:
            hits.append(hit)
    return hits


def _controller_hit(
    books: dict[str, list[SwapEvent]],
    rows: list[tuple[str, int, str]],
    config: HeuristicConfig,
    link: str,
) -> RuleHit | None:
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    cluster: list[tuple[str, int, str]] = []
    best: RuleHit | None = None
    for row in ordered:
        if cluster and row[1] - cluster[0][1] > config.max_block_gap:
            hit = _score_controller(books, cluster, config, link)
            if hit is not None:
                best = hit
            cluster = []
        cluster.append(row)
    if cluster:
        hit = _score_controller(books, cluster, config, link)
        if hit is not None:
            best = hit
    return best


def _score_controller(
    books: dict[str, list[SwapEvent]],
    cluster: list[tuple[str, int, str]],
    config: HeuristicConfig,
    link: str,
) -> RuleHit | None:
    wallets = tuple(sorted({row[0] for row in cluster}))
    if len(wallets) < 2 or len(wallets) > config.max_group_size:
        return None
    swaps = [swap for wallet in wallets for swap in books.get(wallet, [])]
    if len(swaps) < 2:
        return None
    start = min(swap.block for swap in swaps)
    end = max(swap.block for swap in swaps)
    if end - start > config.max_block_gap:
        return None
    buy = sum(swap.amount for swap in swaps if swap.side == "buy")
    sell = sum(swap.amount for swap in swaps if swap.side == "sell")
    if buy <= 0 or sell <= 0 or not _close(buy, sell, config.net_ratio):
        return None
    buyers = {swap.wallet for swap in swaps if swap.side == "buy"}
    sellers = {swap.wallet for swap in swaps if swap.side == "sell"}
    portions = {(swap.tx_hash, swap.log_index): swap.amount for swap in swaps}
    steps = [f"{wallet} {link} in {tx} at block {block}" for wallet, block, tx in cluster]
    steps.extend(f"{swap.wallet} {swap.side} {swap.amount:g} in {swap.tx_hash} at block {swap.block}" for swap in swaps)
    return RuleHit(
        rule="R3",
        why=(
            f"{len(wallets)} wallets that traded were {link}, and their buys ({buy:g}) "
            f"and sells ({sell:g}) cancel."
        ),
        path=" → ".join(steps),
        wallets=wallets,
        start_block=start,
        end_block=end,
        portions=portions,
        swaps=swaps,
        pattern_type=_flow_pattern(buyers, sellers, len(wallets)),
        n_buyers=len(buyers),
        n_sellers=len(sellers),
    )


def _churn(swaps: list[SwapEvent], config: HeuristicConfig) -> list[RuleHit]:
    books: dict[str, list[SwapEvent]] = defaultdict(list)
    for swap in swaps:
        books[swap.wallet].append(swap)
    hits: list[RuleHit] = []
    for wallet, rows in books.items():
        buys = [swap for swap in rows if swap.side == "buy"]
        sells = [swap for swap in rows if swap.side == "sell"]
        if len(buys) < 2 or len(sells) < 2:
            continue
        start = min(swap.block for swap in rows)
        end = max(swap.block for swap in rows)
        if end - start > config.max_block_gap:
            continue
        buy = sum(swap.amount for swap in buys)
        sell = sum(swap.amount for swap in sells)
        if buy <= 0 or sell <= 0 or not _close(buy, sell, config.net_ratio):
            continue
        hits.append(
            RuleHit(
                rule="R5",
                why=f"{wallet} bought {buy:g} and sold {sell:g} across {len(rows)} trades and finished flat.",
                path=" → ".join(
                    f"{swap.side} {swap.amount:g} in {swap.tx_hash} at block {swap.block}" for swap in rows
                ),
                wallets=(wallet,),
                start_block=start,
                end_block=end,
                portions={(swap.tx_hash, swap.log_index): swap.amount for swap in rows},
                swaps=rows,
                pattern_type="round_trip",
                n_buyers=1,
                n_sellers=1,
            )
        )
    return hits


def _same_transaction(swaps: list[SwapEvent]) -> list[RuleHit]:
    groups: dict[tuple[str, str], list[SwapEvent]] = defaultdict(list)
    for swap in swaps:
        groups[(swap.wallet, swap.tx_hash)].append(swap)
    hits: list[RuleHit] = []
    for (wallet, tx), rows in groups.items():
        buys = [swap for swap in rows if swap.side == "buy"]
        sells = [swap for swap in rows if swap.side == "sell"]
        if not buys or not sells:
            continue
        buy = sum(swap.amount for swap in buys)
        sell = sum(swap.amount for swap in sells)
        matched = min(buy, sell)
        if matched <= 0:
            continue
        portions: dict[tuple[str, int], float] = {}
        for swap in buys:
            portions[(swap.tx_hash, swap.log_index)] = matched * (swap.amount / buy)
        for swap in sells:
            portions[(swap.tx_hash, swap.log_index)] = matched * (swap.amount / sell)
        block = min(swap.block for swap in rows)
        hits.append(
            RuleHit(
                rule="R6",
                why=f"{wallet} bought and sold in the same transaction {tx}.",
                path=" → ".join(
                    f"{swap.side} {swap.amount:g} in {tx} at block {swap.block}" for swap in rows
                ),
                wallets=(wallet,),
                start_block=block,
                end_block=max(swap.block for swap in rows),
                portions=portions,
                swaps=rows,
                pattern_type="round_trip",
                n_buyers=1,
                n_sellers=1,
            )
        )
    return hits

from __future__ import annotations

import random
from dataclasses import dataclass, field

from washdetect.detector import SwapEvent, TransferEvent

DEFAULT_MENU: tuple[tuple[str, int, int, bool], ...] = (
    ("round_trip", 1, 1, False),
    ("ring", 3, 1, False),
    ("ring", 4, 1, False),
    ("ring", 5, 1, False),
    ("ring", 8, 1, False),
    ("one_to_many", 1, 2, False),
    ("one_to_many", 1, 3, False),
    ("one_to_many", 1, 5, False),
    ("one_to_many", 1, 8, False),
    ("many_to_one", 2, 1, True),
    ("many_to_one", 3, 1, False),
    ("many_to_one", 6, 1, False),
    ("many_to_one", 10, 1, False),
    ("many_to_many", 2, 2, False),
    ("many_to_many", 3, 4, False),
    ("many_to_many", 4, 4, False),
    ("many_to_many", 5, 6, False),
    ("both_sides", 2, 3, False),
)


@dataclass
class InjectedGroup:
    pattern_type: str
    n_buyers: int
    n_sellers: int
    n_wallets: int
    wallets: tuple[str, ...]
    swap_ids: list[tuple[str, int]] = field(default_factory=list)
    buy_amount: float = 0.0
    sell_amount: float = 0.0
    injected: bool = True

    @property
    def group_size(self) -> int:
        return self.n_wallets


@dataclass
class Injection:
    swaps: list[SwapEvent]
    transfers: list[TransferEvent]
    groups: list[InjectedGroup]

    @property
    def swap_ids(self) -> set[tuple[str, int]]:
        return {key for group in self.groups for key in group.swap_ids}

    @property
    def wallets(self) -> set[str]:
        return {wallet for group in self.groups for wallet in group.wallets}


class _Ids:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self._n = 0

    def addr(self) -> str:
        self._n += 1
        return "0x57" + f"{self._n:038d}"

    def tx(self) -> str:
        self._n += 1
        return "0x57c" + f"{self._n:037d}"


def inject_wash_groups(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    *,
    seed: int = 0,
    block_lo: int | None = None,
    block_hi: int | None = None,
    menu: tuple[tuple[str, int, int, bool], ...] | None = None,
) -> Injection:
    """Planted rows use 0x57 addresses and are not written back to the chain cache."""
    menu = menu or DEFAULT_MENU
    ids = _Ids(seed)
    if block_lo is None or block_hi is None:
        blocks = [swap.block for swap in swaps] + [transfer.block for transfer in transfers]
        if blocks:
            block_lo = min(blocks) if block_lo is None else block_lo
            block_hi = max(blocks) if block_hi is None else block_hi
        else:
            block_lo = 1_000_000 if block_lo is None else block_lo
            block_hi = block_lo + 20_000 if block_hi is None else block_hi
    assert block_lo is not None and block_hi is not None
    starts = _schedule(len(menu), block_lo, block_hi)

    extra_swaps: list[SwapEvent] = []
    extra_transfers: list[TransferEvent] = []
    groups: list[InjectedGroup] = []
    # Groups may overlap in time: each one has its own wallets.
    for spec, start in zip(menu, starts, strict=True):
        kind, n_buyers, n_sellers, exact = spec
        built = _build(kind, ids, start, n_buyers, n_sellers, exact=exact)
        extra_swaps.extend(built[0])
        extra_transfers.extend(built[1])
        groups.append(built[2])
    return Injection(
        swaps=list(swaps) + extra_swaps,
        transfers=list(transfers) + extra_transfers,
        groups=groups,
    )


def _schedule(count: int, lo: int, hi: int) -> list[int]:
    lead = 520
    tail = 90
    first = lo + lead
    last = hi - tail
    if count <= 1:
        return [first if first < last else lo]
    if last <= first:
        return [lo + i * 120 for i in range(count)]
    step = max(120, (last - first) // max(count - 1, 1))
    starts = [first + i * step for i in range(count)]
    if starts[-1] > last:
        span = max(120, (last - first) // max(count - 1, 1))
        starts = [first + i * span for i in range(count)]
        if starts[-1] > last:
            starts = [first + i * max(1, (last - first) // max(count - 1, 1)) for i in range(count)]
    return starts


def _build(
    kind: str,
    ids: _Ids,
    start: int,
    n_buyers: int,
    n_sellers: int,
    *,
    exact: bool,
) -> tuple[list[SwapEvent], list[TransferEvent], InjectedGroup]:
    if kind == "round_trip":
        return _round_trip(ids, start)
    if kind == "ring":
        return _ring(ids, start, n_wallets=n_buyers)
    if kind == "one_to_many":
        return _one_to_many(ids, start, n_sellers)
    if kind == "many_to_one":
        return _many_to_one(ids, start, n_buyers, exact=exact)
    if kind == "many_to_many":
        return _many_to_many(ids, start, n_buyers, n_sellers)
    if kind == "both_sides":
        return _both_sides(ids, start)
    raise ValueError(f"unknown synthetic pattern {kind!r}")


def _fund(ids: _Ids, wallets: list[str], start: int) -> list[TransferEvent]:
    funder = ids.addr()
    block = start - 500
    return [
        TransferEvent(funder, wallet, block, 0.01, ids.tx(), 0)
        for wallet in wallets
    ]


def _swap(wallet: str, block: int, side: str, amount: float, price: float, ids: _Ids) -> SwapEvent:
    return SwapEvent(wallet, block, side, amount, amount * price, ids.tx(), 0)


def _group(
    pattern: str,
    wallets: list[str],
    swaps: list[SwapEvent],
    *,
    n_buyers: int,
    n_sellers: int,
) -> InjectedGroup:
    buyers = {swap.wallet for swap in swaps if swap.side == "buy"}
    sellers = {swap.wallet for swap in swaps if swap.side == "sell"}
    return InjectedGroup(
        pattern_type=pattern,
        n_buyers=len(buyers) if n_buyers < 0 else n_buyers,
        n_sellers=len(sellers) if n_sellers < 0 else n_sellers,
        n_wallets=len(wallets),
        wallets=tuple(wallets),
        swap_ids=[(swap.tx_hash, swap.log_index) for swap in swaps],
        buy_amount=sum(swap.amount for swap in swaps if swap.side == "buy"),
        sell_amount=sum(swap.amount for swap in swaps if swap.side == "sell"),
    )


def _round_trip(ids: _Ids, start: int):
    wallet = ids.addr()
    amount = ids.rng.uniform(10, 200)
    sell = amount * (1 + ids.rng.uniform(-0.03, 0.03))
    price = ids.rng.uniform(0.5, 8)
    swaps = [
        _swap(wallet, start, "buy", amount, price, ids),
        _swap(wallet, start + ids.rng.randint(5, 40), "sell", sell, price, ids),
    ]
    return swaps, _fund(ids, [wallet], start), _group("round_trip", [wallet], swaps, n_buyers=1, n_sellers=1)


def _ring(ids: _Ids, start: int, n_wallets: int):
    wallets = [ids.addr() for _ in range(n_wallets)]
    amount = ids.rng.uniform(15, 180)
    sell = amount * (1 + ids.rng.uniform(0.005, 0.035))
    price = ids.rng.uniform(0.5, 8)
    buyer = wallets[0]
    swaps = [
        _swap(buyer, start, "buy", amount, price, ids),
        _swap(buyer, start + 30 + n_wallets, "sell", sell, price, ids),
    ]
    transfers = _fund(ids, wallets, start)
    for index, wallet in enumerate(wallets):
        nxt = wallets[(index + 1) % n_wallets]
        transfers.append(TransferEvent(wallet, nxt, start + 5 + index, amount, ids.tx(), 0))
    return swaps, transfers, _group("ring", wallets, swaps, n_buyers=1, n_sellers=1)


def _one_to_many(ids: _Ids, start: int, n_sellers: int):
    buyer = ids.addr()
    sellers = [ids.addr() for _ in range(n_sellers)]
    shares = _shares(ids, n_sellers, ids.rng.uniform(20, 400))
    sell_total = sum(shares) * (1 + ids.rng.uniform(0.005, 0.035))
    sell_shares = _shares(ids, n_sellers, sell_total)
    price = ids.rng.uniform(0.5, 8)
    swaps = [_swap(buyer, start, "buy", sum(shares), price, ids)]
    transfers = _fund(ids, [buyer, *sellers], start)
    for index, (seller, share, sold) in enumerate(zip(sellers, shares, sell_shares, strict=True)):
        transfers.append(TransferEvent(buyer, seller, start + 10 + index, share, ids.tx(), 0))
        swaps.append(_swap(seller, start + 30 + index, "sell", sold, price, ids))
    wallets = [buyer, *sellers]
    return swaps, transfers, _group("one_to_many", wallets, swaps, n_buyers=1, n_sellers=n_sellers)


def _many_to_one(ids: _Ids, start: int, n_buyers: int, *, exact: bool):
    buyers = [ids.addr() for _ in range(n_buyers)]
    seller = ids.addr()
    price = 1.0 if exact else ids.rng.uniform(0.5, 8)
    if exact:
        amounts = [2.0, 2.0]
        sell = 4.01
    else:
        amounts = _shares(ids, n_buyers, ids.rng.uniform(20, 500))
        sell = sum(amounts) * (1 + ids.rng.uniform(0.005, 0.035))
    swaps = []
    transfers = _fund(ids, [*buyers, seller], start)
    for index, (buyer, amount) in enumerate(zip(buyers, amounts, strict=True)):
        swaps.append(_swap(buyer, start + index, "buy", amount, price, ids))
        transfers.append(TransferEvent(buyer, seller, start + 15 + index, amount, ids.tx(), 0))
    swaps.append(_swap(seller, start + 20 + n_buyers, "sell", sell, price, ids))
    wallets = [*buyers, seller]
    return swaps, transfers, _group("many_to_one", wallets, swaps, n_buyers=n_buyers, n_sellers=1)


def _many_to_many(ids: _Ids, start: int, n_buyers: int, n_sellers: int):
    buyers = [ids.addr() for _ in range(n_buyers)]
    sellers = [ids.addr() for _ in range(n_sellers)]
    left = ids.addr()
    right = ids.addr()
    buy_amounts = _shares(ids, n_buyers, ids.rng.uniform(30, 400))
    sell_total = sum(buy_amounts) * (1 + ids.rng.uniform(0.005, 0.035))
    sell_amounts = _shares(ids, n_sellers, sell_total)
    price = ids.rng.uniform(0.5, 8)
    swaps = []
    wallets = [*buyers, left, right, *sellers]
    transfers = _fund(ids, wallets, start)
    for index, (buyer, amount) in enumerate(zip(buyers, buy_amounts, strict=True)):
        swaps.append(_swap(buyer, start + index, "buy", amount, price, ids))
        transfers.append(TransferEvent(buyer, left, start + 12 + index, amount, ids.tx(), 0))
    transfers.append(TransferEvent(left, right, start + 20 + n_buyers, sum(buy_amounts) / 2, ids.tx(), 0))
    for index, (seller, amount) in enumerate(zip(sellers, sell_amounts, strict=True)):
        hop = left if index % 2 == 0 else right
        transfers.append(TransferEvent(hop, seller, start + 30 + index, amount, ids.tx(), 0))
        swaps.append(_swap(seller, start + 40 + index, "sell", amount, price, ids))
    return (
        swaps,
        transfers,
        _group("many_to_many", wallets, swaps, n_buyers=n_buyers, n_sellers=n_sellers),
    )


def _both_sides(ids: _Ids, start: int):
    buyer_seller, buyer, seller_c, seller_d = (ids.addr() for _ in range(4))
    price = ids.rng.uniform(0.5, 8)
    # Buys 6+4. Sells 3+4+3.05, just inside the 5% tolerance, and one wallet does both.
    swaps = [
        _swap(buyer_seller, start, "buy", 6, price, ids),
        _swap(buyer, start + 2, "buy", 4, price, ids),
        _swap(buyer_seller, start + 30, "sell", 3, price, ids),
        _swap(seller_c, start + 32, "sell", 4, price, ids),
        _swap(seller_d, start + 34, "sell", 3.05, price, ids),
    ]
    wallets = [buyer_seller, buyer, seller_c, seller_d]
    transfers = _fund(ids, wallets, start)
    transfers.extend(
        [
            TransferEvent(buyer_seller, seller_c, start + 10, 3, ids.tx(), 0),
            TransferEvent(buyer, seller_c, start + 12, 1, ids.tx(), 0),
            TransferEvent(buyer, seller_d, start + 14, 3, ids.tx(), 0),
        ]
    )
    return swaps, transfers, _group("many_to_many", wallets, swaps, n_buyers=2, n_sellers=3)


def _shares(ids: _Ids, count: int, total: float) -> list[float]:
    raw = [ids.rng.uniform(0.4, 1.6) for _ in range(count)]
    scale = total / sum(raw)
    return [value * scale for value in raw]

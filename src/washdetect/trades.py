from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from washdetect.events import SwapEvent, TransferEvent


class TradeResolver:
    """Gives each swap to the address whose token balance moved, and drops trades that are not a holder's.

    A route hop or an atomic arbitrage nets to zero across several pools in one transaction.
    A split route is one trade spread over pools. A sandwich brackets another trader in one block.
    """

    def __init__(self, transfers: list[TransferEvent], hops: set[str], net_ratio: float) -> None:
        self.hops = hops
        self.net_ratio = net_ratio
        self.by_tx: dict[str, list[TransferEvent]] = defaultdict(list)
        for transfer in transfers:
            self.by_tx[transfer.tx_hash].append(transfer)

    def resolve(self, swaps: list[SwapEvent]) -> list[SwapEvent]:
        by_tx: dict[str, list[SwapEvent]] = defaultdict(list)
        for swap in swaps:
            by_tx[swap.tx_hash].append(swap)
        senders = {tx: rows[0].wallet for tx, rows in by_tx.items()}
        out: list[SwapEvent] = []
        for tx, rows in by_tx.items():
            out.extend(self._resolve_tx(tx, sorted(rows, key=lambda s: s.log_index)))
        return self._drop_sandwiches(self._drop_shared(out, senders))

    @staticmethod
    def _drop_shared(swaps: list[SwapEvent], senders: dict[str, str]) -> list[SwapEvent]:
        """A contract that many one-off senders trade through is a service (RFQ maker, vault), not one owner."""
        txs: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for swap in swaps:
            sender = senders[swap.tx_hash]
            if sender != swap.wallet:
                txs[swap.wallet][sender].add(swap.tx_hash)
        shared = {
            holder
            for holder, by_sender in txs.items()
            if len(by_sender) >= 4 and sum(map(len, by_sender.values())) < 3 * len(by_sender)
        }
        return [s for s in swaps if s.wallet not in shared or senders[s.tx_hash] == s.wallet]

    def _resolve_tx(self, tx: str, rows: list[SwapEvent]) -> list[SwapEvent]:
        buy = sum(s.amount for s in rows if s.side == "buy")
        sell = sum(s.amount for s in rows if s.side == "sell")
        mixed = buy > 0 and sell > 0
        if mixed and len({s.pool for s in rows}) > 1:
            net = buy - sell
            if abs(net) <= self.net_ratio * max(buy, sell):
                return []
            side = "buy" if net > 0 else "sell"
            scale = abs(net) / (buy if side == "buy" else sell)
            rows = [replace(s, amount=s.amount * scale, usd=s.usd * scale) for s in rows if s.side == side]
            mixed = False
        holder = self._holder(tx, rows, mixed)
        if holder is None:
            return []
        return [replace(s, wallet=holder) for s in rows] if holder else rows

    def _holder(self, tx: str, rows: list[SwapEvent], mixed: bool) -> str | None:
        """'' keeps tx.from (no transfers seen); None means nobody outside pools and routers held the token."""
        transfers = self.by_tx.get(tx)
        if not transfers:
            return ""
        delta: dict[str, float] = defaultdict(float)
        gross: dict[str, float] = defaultdict(float)
        for t in transfers:
            if t.src not in self.hops:
                delta[t.src] -= t.amount
                gross[t.src] += t.amount
            if t.dst not in self.hops:
                delta[t.dst] += t.amount
                gross[t.dst] += t.amount
        if mixed:
            return max(gross, key=gross.__getitem__) if gross else ""
        total = sum(s.amount for s in rows)
        sign = 1.0 if rows[0].side == "buy" else -1.0
        best = max(delta, key=lambda a: sign * delta[a], default=None)
        if best is None or sign * delta[best] < 0.25 * total:
            return None
        return best

    @staticmethod
    def _drop_sandwiches(swaps: list[SwapEvent]) -> list[SwapEvent]:
        by_slot: dict[tuple[int, str], list[SwapEvent]] = defaultdict(list)
        for swap in swaps:
            by_slot[(swap.block, swap.pool)].append(swap)
        dropped: set[tuple[str, int]] = set()
        for rows in by_slot.values():
            if len(rows) < 3:
                continue
            mine: dict[str, list[SwapEvent]] = defaultdict(list)
            for swap in rows:
                mine[swap.wallet].append(swap)
            for wallet, own in mine.items():
                if len({s.side for s in own}) < 2 or len({s.tx_hash for s in own}) < 2:
                    continue
                lo = min(s.log_index for s in own)
                hi = max(s.log_index for s in own)
                if any(s.wallet != wallet and lo < s.log_index < hi for s in rows):
                    dropped.update((s.tx_hash, s.log_index) for s in own)
        return [s for s in swaps if (s.tx_hash, s.log_index) not in dropped]

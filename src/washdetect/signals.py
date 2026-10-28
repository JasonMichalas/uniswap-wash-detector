"""Questions answered from one loaded window. No extra RPC."""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from washdetect.config import (
    AGGREGATORS,
    BURN_ADDRESS,
    CEX_HOT,
    MEV_ACTORS,
    ROUTERS,
    ZERO_ADDRESS,
    HeuristicConfig,
)
from washdetect.detector import WashDetector
from washdetect.events import SwapEvent, TransferEvent
from washdetect.querylang.errors import QueryError

# A mainnet block is about 12 seconds, so a minute is 5 blocks.
_BLOCKS_PER_MINUTE = 5
_KNOWN_MEV = {
    "0xae2fc483527b8ef99eb5d9b44875f005ba1fae13": "jaredfromsubway.eth",
}
_SKIP_FUNDERS = set(ROUTERS) | set(CEX_HOT) | set(AGGREGATORS) | set(MEV_ACTORS)


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _addr(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    return str(value).strip().lower()


def _stamp(value: object) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    if isinstance(value, float) and value != value:
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    return stamp


def _pct(fraction: float) -> str:
    shown = fraction * 100
    if abs(shown - round(shown)) < 0.05:
        return f"{round(shown):.0f}%"
    return f"{shown:.1f}%"


def _money(amount: float) -> str:
    return f"${amount:,.0f}"


def _whole(value: object, default: int, low: int, high: int, label: str) -> int:
    if value is None or value == "":
        return default
    number = _num(value)
    if number is None or int(number) != number or not low <= int(number) <= high:
        raise QueryError(f"{label} must be a whole number from {low} to {high}.")
    return int(number)


def _real(value: object, default: float, low: float, high: float, label: str) -> float:
    if value is None or value == "":
        return default
    number = _num(value)
    if number is None or not low <= number <= high:
        raise QueryError(f"{label} must be a number from {low:g} to {high:g}.")
    return number


def _table(rows: list[dict[str, object]], columns: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=columns)
    return frame.reindex(columns=columns)


def _gini(balances: list[float]) -> float:
    """0 means equal balances. 1 means one wallet holds the whole measured supply."""
    ordered = sorted(balance for balance in balances if balance > 0)
    count = len(ordered)
    if count <= 1:
        return 0.0
    total = sum(ordered)
    if total <= 0:
        return 0.0
    weighted = sum((index + 1) * balance for index, balance in enumerate(ordered))
    return (2 * weighted) / (count * total) - (count + 1) / count


class _Ask:
    def __init__(self, settings: dict[str, object]) -> None:
        self._raw = settings
        self.creator = _addr(settings.get("creator"))

    def whole(self, name: str, default: int, low: int, high: int, label: str) -> int:
        return _whole(self._raw.get(name), default, low, high, label)

    def real(self, name: str, default: float, low: float, high: float, label: str) -> float:
        return _real(self._raw.get(name), default, low, high, label)


class _Swap:
    def __init__(self, row: dict[str, object]) -> None:
        self.block = int(_num(row.get("block_number")) or 0)
        self.log = int(_num(row.get("log_index")) or 0)
        self.time = _stamp(row.get("block_timestamp"))
        self.pool = _addr(row.get("pool_address"))
        self.trader = _addr(row.get("trader"))
        self.side = str(row.get("side") or "").lower()
        self.amount = _num(row.get("amount_token")) or 0.0
        self.usd = _num(row.get("usd_value"))
        self.tx = str(row.get("tx_hash") or "")


class _Transfer:
    def __init__(self, row: dict[str, object]) -> None:
        self.block = int(_num(row.get("block_number")) or 0)
        self.log = int(_num(row.get("log_index")) or 0)
        self.time = _stamp(row.get("block_timestamp"))
        self.src = _addr(row.get("from_address"))
        self.dst = _addr(row.get("to_address"))
        self.amount = _num(row.get("amount")) or 0.0
        self.tx = str(row.get("tx_hash") or "")


class _Liquidity:
    def __init__(self, row: dict[str, object]) -> None:
        self.block = int(_num(row.get("block_number")) or 0)
        self.log = int(_num(row.get("log_index")) or 0)
        self.time = _stamp(row.get("block_timestamp"))
        self.pool = _addr(row.get("pool_address"))
        self.kind = str(row.get("kind") or "").lower()
        self.sender = _addr(row.get("sender"))
        left = abs(_num(row.get("amount0_raw")) or 0.0)
        right = abs(_num(row.get("amount1_raw")) or 0.0)
        self.size = left + right
        self.tx = str(row.get("tx_hash") or "")


class WindowSignals:
    """One pass over the swaps, transfers, balances, and liquidity already loaded."""

    def __init__(
        self,
        swaps: pd.DataFrame | None,
        transfers: pd.DataFrame | None,
        wallets: pd.DataFrame | None,
        liquidity: pd.DataFrame | None = None,
        pools: set[str] | None = None,
        creator: str = "",
        creator_status: str = "",
    ) -> None:
        self.swaps = [_Swap(row) for row in _records(swaps)]
        self.transfers = [_Transfer(row) for row in _records(transfers)]
        self.liquidity = [_Liquidity(row) for row in _records(liquidity)]
        self.balances: dict[str, float] = {}
        self.kinds: dict[str, str] = {}
        for row in _records(wallets):
            wallet = _addr(row.get("wallet"))
            if not wallet:
                continue
            self.kinds[wallet] = str(row.get("holder_kind") or "wallet")
            balance = _num(row.get("balance"))
            if balance is not None:
                self.balances[wallet] = balance
        self.blocked = {_addr(pool) for pool in (pools or set())} | set(ROUTERS)
        self.creator = _addr(creator)
        self.creator_status = creator_status or ""

    def run(self, kind: str, **settings: object) -> tuple[pd.DataFrame, str]:
        ask = _Ask(settings)
        method = {
            "concentration": self.concentration,
            "pnl": self.pnl,
            "sandwich": self.sandwich,
            "insiders": self.insiders,
            "fake_holders": self.fake_holders,
            "liquidity_pull": self.liquidity_pull,
            "whales": self.whales,
            "pressure": self.pressure,
            "bots": self.bots,
            "wash_over_time": self.wash_over_time,
        }.get(kind)
        if method is None:
            raise QueryError(f"Unknown question {kind}.")
        return method(ask)

    def concentration(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        top_n = ask.whole("top_n", 10, 1, 100, "How many holders")
        holders = self._holders()
        columns = ["wallet", "balance", "share_percent"]
        if not holders:
            return _table([], columns), (
                "No holder balances were read for this window, so concentration cannot be measured. "
                "Pools, the zero address, and the burn address are left out."
            )
        supply = sum(balance for _wallet, balance in holders)
        top = holders[:top_n]
        rows = [
            {"wallet": wallet, "balance": balance, "share_percent": balance / supply}
            for wallet, balance in top
        ]
        gini = _gini([balance for _wallet, balance in holders])
        top_share = sum(balance for _wallet, balance in top) / supply
        note = (
            f"The top {len(top)} wallets hold {_pct(top_share)} of the balances we could read. "
            f"Gini is {_pct(gini)}: 0% would mean every holder has the same balance, and 100% "
            "would mean one wallet holds all of it. "
            "Pools, routers, the zero address, and the burn address are left out. "
            "This is not the full token supply, only wallets whose balance was read."
        )
        return _table(rows, columns), note

    def pnl(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        top_n = ask.whole("top_n", 10, 1, 100, "How many wallets")
        columns = [
            "wallet",
            "side",
            "realised_usd",
            "unrealised_usd",
            "total_usd",
            "cost_status",
            "why",
            "tokens_bought",
            "tokens_sold",
            "balance",
        ]
        books: dict[str, dict[str, float | bool]] = {}
        for swap in self.swaps:
            if not swap.trader or swap.trader in self.blocked:
                continue
            book = books.setdefault(
                swap.trader,
                {"bought": 0.0, "spent": 0.0, "sold": 0.0, "received": 0.0, "gap": False},
            )
            if swap.side == "buy":
                book["bought"] = float(book["bought"]) + swap.amount
                if swap.usd is None:
                    book["gap"] = True
                else:
                    book["spent"] = float(book["spent"]) + swap.usd
            elif swap.side == "sell":
                book["sold"] = float(book["sold"]) + swap.amount
                if swap.usd is None:
                    book["gap"] = True
                else:
                    book["received"] = float(book["received"]) + swap.usd
        end_price = self._end_price()
        known: list[dict[str, object]] = []
        unknown: list[dict[str, object]] = []
        for wallet, book in books.items():
            bought = float(book["bought"])
            sold = float(book["sold"])
            if bought <= 0 and sold <= 0:
                continue
            balance = self.balances.get(wallet)
            row, is_known = _pnl_row(wallet, book, balance, end_price)
            (known if is_known else unknown).append(row)
        known.sort(key=lambda row: float(row["total_usd"] or 0), reverse=True)
        winners = [row for row in known if float(row["total_usd"] or 0) > 0][:top_n]
        losers = sorted(
            (row for row in known if float(row["total_usd"] or 0) < 0),
            key=lambda row: float(row["total_usd"] or 0),
        )[:top_n]
        for row in winners:
            row["side"] = "Winner"
        for row in losers:
            row["side"] = "Loser"
        unknown.sort(key=lambda row: float(row["tokens_sold"] or 0), reverse=True)
        shown_unknown = unknown[:top_n]
        for row in shown_unknown:
            row["side"] = "Unknown"
        rows = winners + losers + shown_unknown
        if not books:
            note = "No wallet bought or sold in this window, so there is no profit or loss to show."
        else:
            note = (
                "Realised profit is dollars received from sells minus the average price of buys "
                "in this window. Unrealised profit is the end balance valued at the last trade price, "
                "minus that same average cost. "
            )
            if unknown:
                note += (
                    f"{len(unknown)} wallets are marked Unknown: they sold or still hold more tokens "
                    "than they bought here, or a trade had no dollar price. "
                    "Their profit is left blank instead of inventing a cost from before the window. "
                )
            if end_price is None:
                note += "There is no priced trade, so unrealised profit is blank. "
            if not rows:
                note += "Nothing to rank."
        return _table(rows, columns), note.strip()

    def sandwich(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        del ask
        columns = [
            "attacker",
            "victim",
            "pool_address",
            "direction",
            "profit_usd",
            "block_number",
            "buy_tx",
            "victim_tx",
            "sell_tx",
        ]
        grouped: dict[tuple[int, str], list[_Swap]] = defaultdict(list)
        for swap in self.swaps:
            if swap.trader and swap.pool and swap.trader not in self.blocked:
                grouped[(swap.block, swap.pool)].append(swap)
        rows: list[dict[str, object]] = []
        for (block, pool), swaps in grouped.items():
            ordered = sorted(swaps, key=lambda swap: (swap.log, swap.tx))
            rows.extend(_sandwich_side(ordered, block, pool, "buy", "sell", "Buy sandwich"))
            rows.extend(_sandwich_side(ordered, block, pool, "sell", "buy", "Sell sandwich"))
        rows.sort(key=lambda row: float(row["profit_usd"] or 0), reverse=True)
        note = (
            "No sandwich attacks found. A sandwich is the same wallet trading before and after "
            "someone else's swap, in the same block and the same pool, in the same direction."
            if not rows
            else (
                "Profit is the attacker's sell dollars minus their buy dollars. "
                "A buy sandwich buys, lets the victim buy, then sells. "
                "A sell sandwich sells, lets the victim sell, then buys back."
            )
        )
        return _table(rows, columns), note

    def insiders(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        columns = ["wallet", "received_amount", "sold_usd", "creator", "why"]
        limit = (
            " ETH sent by the creator is not in this cache, so only token transfers from that wallet count."
        )
        pasted = ask.creator
        if pasted:
            creator = pasted
            how = "Using the creator address pasted into the box."
        elif self.creator:
            creator = self.creator
            how = (
                "The creator is the wallet that sent the earliest mint in this window. "
                "That is the transaction sender. The wallet that received the new tokens is only the first holder."
            )
        else:
            if self.creator_status == "mint_without_sender":
                how = (
                    "A mint from the zero address is in this window, but the cache has no transaction "
                    "sender for it. The wallet that received the mint is the first holder, not the creator. "
                    "Paste the creator address into the box."
                )
            else:
                how = (
                    "The creator cannot be found from this window. There is no mint from the zero "
                    "address here, and a deploy transaction is not in the log cache. "
                    "Paste the creator address into the box."
                )
            return _table([], columns), how + limit
        received: dict[str, float] = defaultdict(float)
        for transfer in self.transfers:
            if transfer.src != creator or transfer.dst in {creator, ""} or transfer.dst in self.blocked:
                continue
            received[transfer.dst] += transfer.amount
        sold: dict[str, float] = defaultdict(float)
        sold_known: dict[str, bool] = defaultdict(lambda: True)
        for swap in self.swaps:
            if swap.side != "sell" or swap.trader not in received:
                continue
            if swap.usd is None:
                sold_known[swap.trader] = False
            else:
                sold[swap.trader] += swap.usd
        sellers = {swap.trader for swap in self.swaps if swap.side == "sell" and swap.trader}
        rows = []
        for wallet, amount in received.items():
            if wallet not in sellers:
                continue
            rows.append(
                {
                    "wallet": wallet,
                    "received_amount": amount,
                    "sold_usd": sold[wallet] if sold_known[wallet] else None,
                    "creator": creator,
                    "why": "Received the token from the creator, then sold.",
                }
            )
        rows.sort(key=lambda row: float(row["sold_usd"] or 0), reverse=True)
        if not rows:
            detail = " No wallet that received the token from the creator then sold it in this window."
        else:
            detail = ""
        return _table(rows, columns), how + limit + detail

    def fake_holders(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        minutes = ask.whole("minutes", 60, 1, 10080, "Minutes")
        minimum = ask.whole("min_wallets", 5, 2, 500, "Minimum wallets")
        columns = ["source", "wallet_count", "holdings", "span_minutes", "first_block", "last_block"]
        gap = minutes * _BLOCKS_PER_MINUTE
        # Same common-funder map as wash rule R3, without requiring buys and sells to cancel.
        # The log cache has token transfers, not ETH traces.
        first: dict[str, tuple[str, int]] = {}
        seen: set[str] = set()
        ordered = sorted(self.transfers, key=lambda transfer: (transfer.block, transfer.log, transfer.tx))
        for transfer in ordered:
            wallet = transfer.dst
            if not wallet or wallet in seen or wallet == transfer.src or wallet in self.blocked:
                continue
            seen.add(wallet)
            if transfer.src in _SKIP_FUNDERS or not transfer.src:
                continue
            first[wallet] = (transfer.src, transfer.block)
        by_source: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for wallet, (source, block) in first.items():
            by_source[source].append((wallet, block))
        rows: list[dict[str, object]] = []
        for source, funded in by_source.items():
            funded.sort(key=lambda item: item[1])
            start = 0
            while start < len(funded):
                end = start
                while end + 1 < len(funded) and funded[end + 1][1] - funded[start][1] <= gap:
                    end += 1
                count = end - start + 1
                if count >= minimum:
                    cluster = funded[start : end + 1]
                    holdings = sum(self.balances.get(wallet, 0.0) for wallet, _block in cluster)
                    span_blocks = cluster[-1][1] - cluster[0][1]
                    rows.append(
                        {
                            "source": source,
                            "wallet_count": count,
                            "holdings": holdings,
                            "span_minutes": round(span_blocks / _BLOCKS_PER_MINUTE, 1),
                            "first_block": cluster[0][1],
                            "last_block": cluster[-1][1],
                        }
                    )
                    start = end + 1
                else:
                    start += 1
        rows.sort(key=lambda row: int(row["wallet_count"]), reverse=True)
        note = (
            f"No cluster of at least {minimum} wallets got their first tokens from the same wallet "
            f"within {minutes} minutes. "
            if not rows
            else (
                f"Each row is a group of at least {minimum} wallets whose first token transfer "
                f"came from one wallet, within {minutes} minutes. "
            )
        )
        note += (
            "ETH funding is not in the log cache. Exchanges, routers, and pools are not treated as a "
            "shared funder. Holdings are the end balances we could read, added up. "
            "This does not require the wallets to have washed; it only shares a funder."
        )
        return _table(rows, columns), note

    def liquidity_pull(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        minutes = ask.whole("minutes", 60, 1, 10080, "Minutes")
        percent = ask.real("percent", 50, 0, 100, "Percent removed")
        columns = [
            "pool_address",
            "removed_percent",
            "removed_usd",
            "span_minutes",
            "remover",
            "original_adder",
            "same_wallet",
            "block_timestamp",
        ]
        if not self.liquidity:
            return _table([], columns), (
                "No liquidity adds or removes were loaded for this window, so there is no pull to show."
            )
        gap = minutes * _BLOCKS_PER_MINUTE
        by_pool: dict[str, list[_Liquidity]] = defaultdict(list)
        for event in self.liquidity:
            if event.pool:
                by_pool[event.pool].append(event)
        rows: list[dict[str, object]] = []
        pools_without_adds = 0
        for pool, events in by_pool.items():
            events.sort(key=lambda event: (event.block, event.log))
            added = sum(event.size for event in events if event.kind == "mint")
            burns = [event for event in events if event.kind == "burn" and event.size > 0]
            if added <= 0:
                if burns:
                    pools_without_adds += 1
                continue
            adder = next((event.sender for event in events if event.kind == "mint" and event.sender), "")
            best: tuple[float, list[_Liquidity]] | None = None
            for start, _burn in enumerate(burns):
                end = start
                while end + 1 < len(burns) and burns[end + 1].block - burns[start].block <= gap:
                    end += 1
                window = burns[start : end + 1]
                removed = sum(event.size for event in window)
                share = removed / added
                if share >= percent / 100 and (best is None or share > best[0]):
                    best = (share, window)
            if best is None:
                continue
            share, window = best
            remover = _largest_sender(window)
            last = window[-1]
            when = last.time.isoformat() if last.time is not None else ""
            span_blocks = window[-1].block - window[0].block
            rows.append(
                {
                    "pool_address": pool,
                    "removed_percent": share,
                    "removed_usd": None,
                    "span_minutes": round(span_blocks / _BLOCKS_PER_MINUTE, 1),
                    "remover": remover,
                    "original_adder": adder,
                    "same_wallet": bool(remover and remover == adder),
                    "block_timestamp": when,
                }
            )
        rows.sort(key=lambda row: float(row["removed_percent"] or 0), reverse=True)
        note = (
            f"A pull is removals of at least {_pct(percent / 100)} of the liquidity added in this window, "
            f"packed inside {minutes} minutes. "
            "Dollars removed are blank: liquidity is stored as raw pool units, without the decimals "
            "needed to price it. The original adder is the first add we can see in this window, "
            "which may be later than the pool's real first add. "
        )
        if pools_without_adds:
            noun = "pool" if pools_without_adds == 1 else "pools"
            note += (
                f"{pools_without_adds} {noun} had removals but no add in this window, so their percent "
                "would be a guess and they are left out. "
            )
        if any(float(row["removed_percent"] or 0) > 1 for row in rows):
            note += (
                "More than 100% means the removal was larger than the liquidity added in this window, "
                "so liquidity added earlier is in the removal but not in the denominator. "
            )
        if not rows:
            note += "No pool matched."
        return _table(rows, columns), note.strip()

    def whales(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        dollars = ask.real("usd", 100_000, 0, 1_000_000_000_000, "Dollars")
        percent = ask.real("percent", 1, 0, 100, "Percent of supply")
        columns = [
            "from_address",
            "to_address",
            "amount_token",
            "amount_usd",
            "supply_percent",
            "tag",
            "tx_hash",
            "block_timestamp",
        ]
        price = self._average_price()
        supply = sum(balance for _wallet, balance in self._holders())
        if price is None and supply <= 0:
            return _table([], columns), (
                "There is no dollar price and no measured holder supply, so a transfer cannot be "
                "sized as a whale move."
            )
        rows: list[dict[str, object]] = []
        for transfer in self.transfers:
            if not transfer.src or not transfer.dst or transfer.src == transfer.dst:
                continue
            src_exchange = transfer.src in CEX_HOT
            dst_exchange = transfer.dst in CEX_HOT
            # A swap also writes a transfer to the pool. Those are trades, not wallet moves.
            if not src_exchange and not dst_exchange:
                if transfer.src in self.blocked or transfer.dst in self.blocked:
                    continue
            usd = transfer.amount * price if price is not None else None
            share = transfer.amount / supply if supply > 0 else None
            big_dollars = usd is not None and usd >= dollars
            big_share = share is not None and share >= percent / 100
            if not big_dollars and not big_share:
                continue
            if src_exchange:
                tag = "Withdrew from an exchange"
            elif dst_exchange:
                tag = "Sent to an exchange"
            else:
                tag = "Large transfer"
            when = transfer.time.isoformat() if transfer.time is not None else ""
            rows.append(
                {
                    "from_address": transfer.src,
                    "to_address": transfer.dst,
                    "amount_token": transfer.amount,
                    "amount_usd": usd,
                    "supply_percent": share,
                    "tag": tag,
                    "tx_hash": transfer.tx,
                    "block_timestamp": when,
                }
            )
        rows.sort(key=lambda row: float(row["amount_usd"] or 0), reverse=True)
        threshold = _money(dollars)
        note = (
            f"No transfer was at least {threshold} or {_pct(percent / 100)} of the balances we could read. "
            if not rows
            else (
                f"Transfers of at least {threshold} or {_pct(percent / 100)} of the balances we could read. "
            )
        )
        note += (
            "The dollar size uses the average trade price in this window. "
            "If that price is missing, only the share of measured supply is used. "
            "Transfers into or out of a pool are left out, except exchange deposits and withdrawals."
        )
        return _table(rows, columns), note

    def pressure(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        del ask
        columns = ["day", "buy_usd", "sell_usd", "net_usd", "price_usd"]
        buckets: dict[str, dict[str, float]] = {}
        for swap in self.swaps:
            if swap.time is None:
                continue
            hour = swap.time.floor("h")
            key = hour.strftime("%Y-%m-%d %H:%M")
            bucket = buckets.setdefault(key, {"buy": 0.0, "sell": 0.0, "usd": 0.0, "amount": 0.0})
            if swap.usd is not None and swap.side == "buy":
                bucket["buy"] += swap.usd
            elif swap.usd is not None and swap.side == "sell":
                bucket["sell"] += swap.usd
            if swap.usd is not None and swap.amount > 0:
                bucket["usd"] += swap.usd
                bucket["amount"] += swap.amount
        rows = []
        for key in sorted(buckets):
            bucket = buckets[key]
            price = bucket["usd"] / bucket["amount"] if bucket["amount"] > 0 else None
            rows.append(
                {
                    "day": key,
                    "buy_usd": bucket["buy"],
                    "sell_usd": bucket["sell"],
                    "net_usd": bucket["buy"] - bucket["sell"],
                    "price_usd": price,
                }
            )
        note = (
            "No priced buys or sells in this window, so there is no buy or sell pressure to plot."
            if not rows
            else (
                "Each bar is one hour. Bought and sold are dollar totals. "
                "Net, in the table, is bought minus sold. The price is the average dollar price that hour."
            )
        )
        return _table(rows, columns), note

    def bots(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        top_n = ask.whole("top_n", 10, 1, 100, "How many people")
        columns = ["wallet", "label", "reason", "trade_count", "back_to_back_percent", "active_hours"]
        by_wallet: dict[str, list[_Swap]] = defaultdict(list)
        for swap in self.swaps:
            if swap.trader and swap.trader not in self.blocked:
                by_wallet[swap.trader].append(swap)
        bots: list[dict[str, object]] = []
        humans: list[dict[str, object]] = []
        for wallet, swaps in by_wallet.items():
            ordered = sorted(swaps, key=lambda swap: (swap.block, swap.log, swap.tx))
            row = _classify_wallet(wallet, ordered)
            (bots if row["label"] == "Bot" else humans).append(row)
        bots.sort(key=lambda row: int(row["trade_count"]), reverse=True)
        humans.sort(key=lambda row: int(row["trade_count"]), reverse=True)
        rows = bots + humans[:top_n]
        if not by_wallet:
            note = "No swaps in this window, so no wallet could be labelled as a bot or a person."
        else:
            bot_word = "wallet looks like a bot" if len(bots) == 1 else "wallets look like bots"
            person_word = "person" if len(humans) == 1 else "people"
            note = (
                f"{len(bots)} {bot_word} and {len(humans)} look like {person_word}. "
                f"The table lists every bot and the {min(top_n, len(humans))} most active people. "
                "A bot is a known MEV wallet, or has at least 20 trades with half of them in "
                "back-to-back blocks, or traded for at least 24 hours with no quiet stretch longer "
                "than 60 minutes."
            )
        return _table(rows, columns), note

    def wash_over_time(self, ask: _Ask) -> tuple[pd.DataFrame, str]:
        del ask
        columns = ["day", "volume_usd", "wash_usd", "wash_percent"]
        if not self.swaps:
            return _table([], columns), "No swaps in this window, so there is no wash share to plot."
        events: list[SwapEvent] = []
        volume: dict[str, float] = defaultdict(float)
        usd_by_id: dict[tuple[str, int], tuple[str, float]] = {}
        for swap in self.swaps:
            day = swap.time.strftime("%Y-%m-%d") if swap.time is not None else "unknown"
            if swap.usd is not None:
                volume[day] += swap.usd
            if not swap.trader:
                continue
            events.append(
                SwapEvent(
                    wallet=swap.trader,
                    block=swap.block,
                    side=swap.side,
                    amount=swap.amount,
                    usd=swap.usd or 0.0,
                    tx_hash=swap.tx,
                    log_index=swap.log,
                    pool=swap.pool,
                )
            )
            if swap.usd is not None:
                usd_by_id[(swap.tx, swap.log)] = (day, swap.usd)
        transfers = [
            TransferEvent(
                src=transfer.src,
                dst=transfer.dst,
                block=transfer.block,
                amount=transfer.amount,
                tx_hash=transfer.tx,
                log_index=transfer.log,
            )
            for transfer in self.transfers
            if transfer.src and transfer.dst
        ]
        detection = WashDetector(
            config=HeuristicConfig(net_ratio=0.05, max_block_gap=7200, max_group_size=24)
        ).find_groups(events, transfers, excluded=self.blocked)
        wash: dict[str, float] = defaultdict(float)
        for key, (day, usd) in usd_by_id.items():
            if key in detection.flagged:
                wash[day] += usd
        rows = []
        for day in sorted(set(volume) | set(wash)):
            total = volume.get(day, 0.0)
            flagged = wash.get(day, 0.0)
            rows.append(
                {
                    "day": day,
                    "volume_usd": total,
                    "wash_usd": flagged,
                    "wash_percent": (flagged / total) if total else 0.0,
                }
            )
        note = (
            "Each day is flagged wash dollars divided by all swap dollars. "
            "The flag uses the same wash rules as Find wash groups, with buys and sells allowed "
            "up to about a day apart. A day at 0% had trades and no flagged wash."
        )
        return _table(rows, columns), note

    def _holders(self) -> list[tuple[str, float]]:
        rows = []
        for wallet, balance in self.balances.items():
            if balance <= 0 or self.kinds.get(wallet) == "pool" or wallet in self.blocked:
                continue
            if wallet in {ZERO_ADDRESS, BURN_ADDRESS}:
                continue
            rows.append((wallet, balance))
        rows.sort(key=lambda item: item[1], reverse=True)
        return rows

    def _end_price(self) -> float | None:
        priced = [swap for swap in self.swaps if swap.usd is not None and swap.amount > 0]
        if not priced:
            return None
        last = max(priced, key=lambda swap: (swap.block, swap.log))
        return float(last.usd) / last.amount if last.usd is not None else None

    def _average_price(self) -> float | None:
        usd = 0.0
        amount = 0.0
        for swap in self.swaps:
            if swap.usd is None or swap.amount <= 0:
                continue
            usd += swap.usd
            amount += swap.amount
        if amount <= 0:
            return None
        return usd / amount


def _records(frame: pd.DataFrame | None) -> list[dict[str, object]]:
    if frame is None or frame.empty:
        return []
    return frame.to_dict("records")


def _pnl_row(
    wallet: str,
    book: dict[str, float | bool],
    balance: float | None,
    end_price: float | None,
) -> tuple[dict[str, object], bool]:
    bought = float(book["bought"])
    sold = float(book["sold"])
    spent = float(book["spent"])
    received = float(book["received"])
    gap = bool(book["gap"])
    held = max(balance, 0.0) if balance is not None else None
    # Tokens that were not bought inside the window have no honest cost here.
    unexplained = sold > bought + 1e-9
    if held is not None and held > max(0.0, bought - sold) + 1e-9:
        unexplained = True
    row: dict[str, object] = {
        "wallet": wallet,
        "side": "",
        "realised_usd": None,
        "unrealised_usd": None,
        "total_usd": None,
        "cost_status": "Unknown",
        "why": "",
        "tokens_bought": bought,
        "tokens_sold": sold,
        "balance": balance,
    }
    if gap:
        row["why"] = "A buy or sell in this window has no dollar price, so the profit is left blank."
        return row, False
    if unexplained or bought <= 0:
        row["why"] = (
            "Cost is unknown because this wallet sold or still holds more tokens than it bought "
            "in this window. The earlier buy price is not here, so no profit is invented."
        )
        return row, False
    average = spent / bought
    realised = received - sold * average
    unrealised = None
    if held is not None and end_price is not None:
        unrealised = held * end_price - held * average
    elif held is None:
        row["why"] = (
            "Realised profit uses the average buy price in this window. "
            "Unrealised profit is blank because the end balance was not read."
        )
    elif end_price is None:
        row["why"] = (
            "Realised profit uses the average buy price in this window. "
            "Unrealised profit is blank because no trade had a dollar price at the end."
        )
    else:
        row["why"] = "Profit uses the average price of buys inside this window."
    row["realised_usd"] = realised
    row["unrealised_usd"] = unrealised
    row["total_usd"] = realised + (unrealised or 0.0)
    row["cost_status"] = "Known"
    return row, True


def _sandwich_side(
    ordered: list[_Swap],
    block: int,
    pool: str,
    open_side: str,
    close_side: str,
    label: str,
) -> list[dict[str, object]]:
    used: set[int] = set()
    found: list[dict[str, object]] = []
    for index, opening in enumerate(ordered):
        if index in used or opening.side != open_side:
            continue
        victim_at = None
        for cursor in range(index + 1, len(ordered)):
            step = ordered[cursor]
            if step.side == open_side and step.trader != opening.trader:
                victim_at = cursor
                break
            if step.trader == opening.trader and step.side == close_side:
                break
        if victim_at is None:
            continue
        close_at = None
        for cursor in range(victim_at + 1, len(ordered)):
            step = ordered[cursor]
            if step.trader == opening.trader and step.side == close_side:
                close_at = cursor
                break
        if close_at is None:
            continue
        victim = ordered[victim_at]
        closing = ordered[close_at]
        buy = opening if open_side == "buy" else closing
        sell = closing if open_side == "buy" else opening
        profit = None
        if buy.usd is not None and sell.usd is not None:
            profit = sell.usd - buy.usd
        used.add(index)
        used.add(close_at)
        found.append(
            {
                "attacker": opening.trader,
                "victim": victim.trader,
                "pool_address": pool,
                "direction": label,
                "profit_usd": profit,
                "block_number": block,
                "buy_tx": buy.tx,
                "victim_tx": victim.tx,
                "sell_tx": sell.tx,
            }
        )
    return found


def _largest_sender(events: list[_Liquidity]) -> str:
    totals: dict[str, float] = defaultdict(float)
    for event in events:
        if event.sender:
            totals[event.sender] += event.size
    if not totals:
        return ""
    return max(totals, key=lambda sender: totals[sender])


def _classify_wallet(wallet: str, ordered: list[_Swap]) -> dict[str, object]:
    count = len(ordered)
    back = _back_to_back(ordered)
    hours, max_gap = _activity(ordered)
    reasons: list[str] = []
    if wallet in MEV_ACTORS:
        name = _KNOWN_MEV.get(wallet, "a known MEV searcher")
        reasons.append(f"This wallet is {name}.")
    if count >= 20 and back >= 0.5:
        reasons.append(
            f"{count} trades, and {_pct(back)} of the steps from one trade to the next "
            "are in the same block or the next one."
        )
    if hours >= 24 and max_gap <= 60:
        reasons.append(
            f"Traded for {hours:.0f} hours with no quiet stretch longer than 60 minutes."
        )
    if reasons:
        label = "Bot"
        reason = " ".join(reasons)
    else:
        label = "Human"
        reason = (
            "None of the bot checks matched: not a known MEV wallet, not a long run of "
            "back-to-back blocks, and not active for 24 hours without a 60 minute gap."
        )
    return {
        "wallet": wallet,
        "label": label,
        "reason": reason,
        "trade_count": count,
        "back_to_back_percent": back,
        "active_hours": round(hours, 1),
    }


def _back_to_back(ordered: list[_Swap]) -> float:
    if len(ordered) < 2:
        return 0.0
    close = 0
    for left, right in zip(ordered, ordered[1:], strict=False):
        if right.block - left.block <= 1:
            close += 1
    return close / (len(ordered) - 1)


def _activity(ordered: list[_Swap]) -> tuple[float, float]:
    if len(ordered) < 2:
        return 0.0, 0.0
    if all(swap.time is not None for swap in ordered):
        first = ordered[0].time
        last = ordered[-1].time
        assert first is not None and last is not None
        hours = (last - first).total_seconds() / 3600
        gaps = []
        for left, right in zip(ordered, ordered[1:], strict=False):
            assert left.time is not None and right.time is not None
            gaps.append((right.time - left.time).total_seconds() / 60)
        return hours, max(gaps)
    hours = (ordered[-1].block - ordered[0].block) / _BLOCKS_PER_MINUTE / 60
    gaps = [
        (right.block - left.block) / _BLOCKS_PER_MINUTE
        for left, right in zip(ordered, ordered[1:], strict=False)
    ]
    return hours, max(gaps) if gaps else 0.0


"""Wallet-level features and the holder table.

The feature columns are the contract the later models train on. They are
computed for real from swaps and transfers (this is not an empty stub) so
the batch command can already write a training frame. The holder table is
what the Wallets and Holders source boxes read. It is data, not a wash
score: loading a token builds it, and nothing is shown until a graph asks.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean, pstdev

import pandas as pd

from washdetect.detector import Detection, SwapEvent, TransferEvent

FEATURE_COLUMNS = [
    "wallet",
    "n_swaps",
    "n_buys",
    "n_sells",
    "buy_amount",
    "sell_amount",
    "net_position_change",
    "round_trip_count",
    "trade_interval_cv",
    "median_inter_swap_blocks",
    "n_pools",
    "counterparty_degree",
    "funding_source",
    "funding_source_degree",
    "wash_volume_usd",
    "volume_usd",
    "heuristic_label",
    "heuristic_confidence",
]

WALLET_COLUMNS = [
    "wallet",
    "balance",
    "held_days",
    "held_days_is_lower_bound",
    "acquired_before_window",
    "ever_sold",
    "days_since_sell",
    "swap_count",
    "buy_volume_usd",
    "sell_volume_usd",
]


def _cv(gaps: list[float]) -> float | None:
    if len(gaps) < 2:
        return None
    avg = mean(gaps)
    if avg <= 0:
        return None
    return pstdev(gaps) / avg


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def build_feature_frame(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    detection: Detection,
    *,
    excluded: set[str] | None = None,
) -> pd.DataFrame:
    """One row per wallet that swapped, with the columns the models will use."""
    excluded = {addr.lower() for addr in (excluded or set())}
    by_wallet: dict[str, list[SwapEvent]] = defaultdict(list)
    for swap in swaps:
        by_wallet[swap.wallet.lower()].append(swap)

    counterparties: dict[str, set[str]] = defaultdict(set)
    first_funder: dict[str, str] = {}
    ordered = sorted(transfers, key=lambda item: (item.block, item.log_index))
    for transfer in ordered:
        src, dst = transfer.src.lower(), transfer.dst.lower()
        if src in excluded or dst in excluded or src == dst:
            continue
        counterparties[src].add(dst)
        counterparties[dst].add(src)
        if dst not in first_funder:
            first_funder[dst] = src
    funder_degree: dict[str, int] = defaultdict(int)
    for funder in first_funder.values():
        funder_degree[funder] += 1

    wash_by_wallet = {row.wallet: row for row in detection.wallets}
    rows: list[dict[str, object]] = []
    for wallet, wallet_swaps in by_wallet.items():
        wallet_swaps.sort(key=lambda item: (item.block, item.log_index))
        gaps = [
            float(b.block - a.block)
            for a, b in zip(wallet_swaps, wallet_swaps[1:], strict=False)
            if b.block >= a.block
        ]
        buy_amount = sum(s.amount for s in wallet_swaps if s.side == "buy")
        sell_amount = sum(s.amount for s in wallet_swaps if s.side == "sell")
        denom = max(buy_amount, sell_amount)
        net = abs(buy_amount - sell_amount) / denom if denom else 0.0
        wash = wash_by_wallet.get(wallet)
        funder = first_funder.get(wallet, "")
        rows.append(
            {
                "wallet": wallet,
                "n_swaps": len(wallet_swaps),
                "n_buys": sum(1 for s in wallet_swaps if s.side == "buy"),
                "n_sells": sum(1 for s in wallet_swaps if s.side == "sell"),
                "buy_amount": buy_amount,
                "sell_amount": sell_amount,
                "net_position_change": net,
                "round_trip_count": wash.round_trip_count if wash else 0,
                "trade_interval_cv": _cv(gaps),
                "median_inter_swap_blocks": _median(gaps),
                "n_pools": len({s.pool for s in wallet_swaps if s.pool}),
                "counterparty_degree": len(counterparties.get(wallet, set())),
                "funding_source": funder,
                "funding_source_degree": funder_degree.get(funder, 0) if funder else 0,
                "wash_volume_usd": wash.wash_volume_usd if wash else 0.0,
                "volume_usd": sum(s.usd for s in wallet_swaps),
                "heuristic_label": 1 if wash and wash.confidence >= 0.5 else 0,
                "heuristic_confidence": wash.confidence if wash else 0.0,
            }
        )
    frame = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
    return frame.sort_values(["heuristic_label", "wash_volume_usd"], ascending=[False, False])


def build_wallet_table(
    swaps: pd.DataFrame,
    transfers: pd.DataFrame,
    balances: dict[str, float] | None,
    *,
    end_ts: datetime,
    window_start_ts: datetime,
) -> pd.DataFrame:
    """Holder and wallet rows for the loaded window.

    `held_days` is days since the first inbound transfer *inside the loaded
    range*. It is a lower bound (`held_days_is_lower_bound`) because the
    cache is not the token's full history. `acquired_before_window` is true
    when a wallet still has a balance but received nothing in the range, so
    `held_days` is left empty rather than invented. `ever_sold` / 
    `days_since_sell` look only at sell swaps in the range.
    """
    balances = {k.lower(): float(v) for k, v in (balances or {}).items()}
    wallets: set[str] = set()

    swap_rows = swaps if swaps is not None else pd.DataFrame()
    transfer_rows = transfers if transfers is not None else pd.DataFrame()

    buys: dict[str, float] = defaultdict(float)
    sells: dict[str, float] = defaultdict(float)
    swap_count: dict[str, int] = defaultdict(int)
    last_sell: dict[str, datetime] = {}
    ever_sold: set[str] = set()

    if not swap_rows.empty:
        for row in swap_rows.itertuples(index=False):
            wallet = str(getattr(row, "trader", "") or "").lower()
            if not wallet:
                continue
            wallets.add(wallet)
            swap_count[wallet] += 1
            usd = float(getattr(row, "usd_value", 0.0) or 0.0)
            side = getattr(row, "side", "")
            if side == "buy":
                buys[wallet] += usd
            elif side == "sell":
                sells[wallet] += usd
                ever_sold.add(wallet)
                ts = getattr(row, "block_timestamp", None)
                if isinstance(ts, datetime) and (wallet not in last_sell or ts > last_sell[wallet]):
                    last_sell[wallet] = ts

    inbound: dict[str, float] = defaultdict(float)
    first_in: dict[str, datetime] = {}
    if not transfer_rows.empty:
        for row in transfer_rows.itertuples(index=False):
            src = str(getattr(row, "from_address", "") or "").lower()
            dst = str(getattr(row, "to_address", "") or "").lower()
            amount = float(getattr(row, "amount", 0.0) or 0.0)
            ts = getattr(row, "block_timestamp", None)
            if src:
                wallets.add(src)
            if dst:
                wallets.add(dst)
                inbound[dst] += amount
                if isinstance(ts, datetime) and (dst not in first_in or ts < first_in[dst]):
                    first_in[dst] = ts

    end_naive = end_ts.replace(tzinfo=None) if end_ts.tzinfo else end_ts
    rows: list[dict[str, object]] = []
    for wallet in wallets:
        balance = balances.get(wallet)
        got = inbound.get(wallet, 0.0)
        acquired_before = balance is not None and balance > 0 and got <= 0
        held_days = None
        lower_bound = False
        if balance is not None and balance > 0 and wallet in first_in and not acquired_before:
            delta = end_naive - first_in[wallet].replace(tzinfo=None)
            held_days = delta.total_seconds() / 86400.0
            # The first inbound we can see is the start of the cache, not
            # necessarily the true acquisition.
            lower_bound = first_in[wallet] <= window_start_ts.replace(tzinfo=None) + _one_hour()
        days_since = None
        if wallet in last_sell:
            days_since = (end_naive - last_sell[wallet].replace(tzinfo=None)).total_seconds() / 86400.0
        rows.append(
            {
                "wallet": wallet,
                "balance": balance,
                "held_days": held_days,
                "held_days_is_lower_bound": lower_bound,
                "acquired_before_window": acquired_before,
                "ever_sold": wallet in ever_sold,
                "days_since_sell": days_since,
                "swap_count": int(swap_count.get(wallet, 0)),
                "buy_volume_usd": float(buys.get(wallet, 0.0)),
                "sell_volume_usd": float(sells.get(wallet, 0.0)),
            }
        )
    frame = pd.DataFrame(rows, columns=WALLET_COLUMNS)
    if frame.empty:
        return frame
    return frame.sort_values(["balance", "wallet"], ascending=[False, True], na_position="last")


def _one_hour():
    from datetime import timedelta

    return timedelta(hours=1)

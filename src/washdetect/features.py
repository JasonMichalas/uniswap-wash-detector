"""Wallet-level features and the holder table.

The feature columns are the contract the later models train on. They are
computed for real from swaps and transfers (this is not an empty stub) so
the batch command can already write a training frame. The holder table is
what the Wallets and Holders source boxes read. It is data, not a wash
score: loading a token builds it, and nothing is shown until a graph asks.
"""

from __future__ import annotations

import math
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
    # Behavioural flow stats. These are not the detector's decision.
    "buy_fraction",
    "log_buy_amount",
    "log_sell_amount",
    "log_volume_usd",
    "burst_fraction",
    "size_cv",
    "n_in_counterparties",
    "n_out_counterparties",
    "in_transfer_ratio",
    "out_transfer_ratio",
    "median_blocks_buy_to_out",
    "both_sides",
]

# What XGBoost and Isolation Forest actually see. The detector's own
# outputs are absent on purpose: round_trip_count, wash_volume_usd,
# heuristic_label, and heuristic_confidence would let the model recite
# the rule. funding_source is a raw address, not a number.
MODEL_FEATURE_COLUMNS = [
    "n_swaps",
    "n_buys",
    "n_sells",
    "buy_fraction",
    "log_buy_amount",
    "log_sell_amount",
    "log_volume_usd",
    "net_position_change",
    "trade_interval_cv",
    "median_inter_swap_blocks",
    "burst_fraction",
    "size_cv",
    "n_pools",
    "counterparty_degree",
    "funding_source_degree",
    "n_in_counterparties",
    "n_out_counterparties",
    "in_transfer_ratio",
    "out_transfer_ratio",
    "median_blocks_buy_to_out",
    "both_sides",
]

RULE_OUTPUT_COLUMNS = (
    "round_trip_count",
    "wash_volume_usd",
    "heuristic_label",
    "heuristic_confidence",
)

# One row per swap. Wallet columns are context, still without rule outputs.
TRADE_FEATURE_COLUMNS = [
    "log_usd",
    "log_amount",
    "side_buy",
    "blocks_since_prev",
    "amount_vs_wallet",
    "wallet_n_swaps",
    "wallet_buy_fraction",
    "wallet_net_position_change",
    "wallet_burst_fraction",
    "wallet_median_inter_swap_blocks",
    "wallet_size_cv",
    "wallet_n_pools",
    "wallet_counterparty_degree",
    "wallet_funding_source_degree",
    "wallet_in_transfer_ratio",
    "wallet_out_transfer_ratio",
    "wallet_both_sides",
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


def _log1p(value: float) -> float:
    return math.log1p(max(0.0, float(value)))


def _cap_ratio(value: float, cap: float = 5.0) -> float:
    if value < 0:
        return 0.0
    return min(cap, float(value))


def model_matrix(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Numeric model input. Missing values become 0. Rule columns are refused."""
    banned = set(RULE_OUTPUT_COLUMNS) & set(columns)
    if banned:
        raise ValueError(f"model features must not include rule outputs: {sorted(banned)}")
    matrix = frame.reindex(columns=columns)
    for column in columns:
        matrix[column] = pd.to_numeric(matrix[column], errors="coerce")
    return matrix.fillna(0.0).astype(float)


def build_trade_frame(
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    *,
    excluded: set[str] | None = None,
    detection: Detection | None = None,
) -> pd.DataFrame:
    """One row per swap. Wallet context is attached; the rule's decision is not.

    ``detection`` is optional and only used to store ``heuristic_label`` on
    the side, for the training label. It is not one of TRADE_FEATURE_COLUMNS.
    """
    wallet_frame = build_feature_frame(
        swaps,
        transfers,
        detection or Detection(cycles=[], wallets=[], flagged={}),
        excluded=excluded,
    )
    by_wallet = wallet_frame.set_index("wallet") if not wallet_frame.empty else None
    flagged = detection.flagged if detection is not None else {}
    columns = [
        "wallet",
        "tx_hash",
        "log_index",
        "block_number",
        "side",
        "usd",
        "heuristic_label",
        *TRADE_FEATURE_COLUMNS,
    ]
    ordered = sorted(swaps, key=lambda item: (item.wallet, item.block, item.log_index, item.tx_hash))
    last_block: dict[str, int] = {}
    rows: list[dict[str, object]] = []
    for swap in ordered:
        wallet = swap.wallet.lower()
        prev = last_block.get(wallet)
        gap = float(swap.block - prev) if prev is not None and swap.block >= prev else 0.0
        last_block[wallet] = swap.block
        context = by_wallet.loc[wallet] if by_wallet is not None and wallet in by_wallet.index else None
        denom = 0.0
        if context is not None:
            denom = max(float(context["buy_amount"]), float(context["sell_amount"]))
        rows.append(
            {
                "wallet": wallet,
                "tx_hash": swap.tx_hash.lower(),
                "log_index": int(swap.log_index),
                "block_number": int(swap.block),
                "side": swap.side,
                "usd": float(swap.usd or 0.0),
                "heuristic_label": 1 if (swap.tx_hash.lower(), int(swap.log_index)) in flagged else 0,
                "log_usd": _log1p(swap.usd or 0.0),
                "log_amount": _log1p(swap.amount),
                "side_buy": 1.0 if swap.side == "buy" else 0.0,
                "blocks_since_prev": gap,
                "amount_vs_wallet": (float(swap.amount) / denom) if denom else 0.0,
                "wallet_n_swaps": float(context["n_swaps"]) if context is not None else 0.0,
                "wallet_buy_fraction": float(context["buy_fraction"]) if context is not None else 0.0,
                "wallet_net_position_change": float(context["net_position_change"]) if context is not None else 0.0,
                "wallet_burst_fraction": float(context["burst_fraction"] or 0.0) if context is not None else 0.0,
                "wallet_median_inter_swap_blocks": float(context["median_inter_swap_blocks"] or 0.0)
                if context is not None
                else 0.0,
                "wallet_size_cv": float(context["size_cv"] or 0.0) if context is not None else 0.0,
                "wallet_n_pools": float(context["n_pools"]) if context is not None else 0.0,
                "wallet_counterparty_degree": float(context["counterparty_degree"]) if context is not None else 0.0,
                "wallet_funding_source_degree": float(context["funding_source_degree"]) if context is not None else 0.0,
                "wallet_in_transfer_ratio": float(context["in_transfer_ratio"]) if context is not None else 0.0,
                "wallet_out_transfer_ratio": float(context["out_transfer_ratio"]) if context is not None else 0.0,
                "wallet_both_sides": float(context["both_sides"]) if context is not None else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns)


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
    inbound: dict[str, set[str]] = defaultdict(set)
    outbound: dict[str, set[str]] = defaultdict(set)
    in_amount: dict[str, float] = defaultdict(float)
    out_amount: dict[str, float] = defaultdict(float)
    out_edges: dict[str, list[tuple[int, float]]] = defaultdict(list)
    first_funder: dict[str, str] = {}
    ordered = sorted(transfers, key=lambda item: (item.block, item.log_index))
    for transfer in ordered:
        src, dst = transfer.src.lower(), transfer.dst.lower()
        if src in excluded or dst in excluded or src == dst:
            continue
        counterparties[src].add(dst)
        counterparties[dst].add(src)
        outbound[src].add(dst)
        inbound[dst].add(src)
        out_amount[src] += transfer.amount
        in_amount[dst] += transfer.amount
        out_edges[src].append((transfer.block, transfer.amount))
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
        n_buys = sum(1 for s in wallet_swaps if s.side == "buy")
        n_sells = sum(1 for s in wallet_swaps if s.side == "sell")
        volume_usd = sum(s.usd for s in wallet_swaps)
        sizes = [s.usd for s in wallet_swaps if s.usd > 0]
        short_gaps = [gap for gap in gaps if gap <= 200]
        buy_to_out: list[float] = []
        outs = out_edges.get(wallet, [])
        for buy in wallet_swaps:
            if buy.side != "buy":
                continue
            later = [block - buy.block for block, _amount in outs if block >= buy.block]
            if later:
                buy_to_out.append(float(min(later)))
        rows.append(
            {
                "wallet": wallet,
                "n_swaps": len(wallet_swaps),
                "n_buys": n_buys,
                "n_sells": n_sells,
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
                "volume_usd": volume_usd,
                "heuristic_label": 1 if wash and wash.confidence >= 0.5 else 0,
                "heuristic_confidence": wash.confidence if wash else 0.0,
                "buy_fraction": n_buys / len(wallet_swaps) if wallet_swaps else 0.0,
                "log_buy_amount": _log1p(buy_amount),
                "log_sell_amount": _log1p(sell_amount),
                "log_volume_usd": _log1p(volume_usd),
                "burst_fraction": (len(short_gaps) / len(gaps)) if gaps else 0.0,
                "size_cv": _cv(sizes),
                "n_in_counterparties": len(inbound.get(wallet, set())),
                "n_out_counterparties": len(outbound.get(wallet, set())),
                "in_transfer_ratio": _cap_ratio(in_amount.get(wallet, 0.0) / sell_amount) if sell_amount else 0.0,
                "out_transfer_ratio": _cap_ratio(out_amount.get(wallet, 0.0) / buy_amount) if buy_amount else 0.0,
                "median_blocks_buy_to_out": _median(buy_to_out),
                "both_sides": 1 if n_buys and n_sells else 0,
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

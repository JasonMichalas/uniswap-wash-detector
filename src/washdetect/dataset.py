from __future__ import annotations

import zlib
from dataclasses import dataclass

import pandas as pd

from washdetect.config import HeuristicConfig
from washdetect.detector import Detection, SwapEvent, TransferEvent, detect
from washdetect.features import build_feature_frame, build_trade_frame
from washdetect.synthetic import Injection, inject_wash_groups


@dataclass
class TokenTables:
    token: str
    symbol: str
    wallets: pd.DataFrame
    trades: pd.DataFrame
    groups: pd.DataFrame
    real_swaps: list[SwapEvent]
    real_transfers: list[TransferEvent]
    real_detection: Detection
    n_real_swaps: int
    n_real_transfers: int
    seconds: float = 0.0
    start_block: int = 0
    end_block: int = 0


def events_from_frames(
    swaps: pd.DataFrame | None,
    transfers: pd.DataFrame | None,
) -> tuple[list[SwapEvent], list[TransferEvent]]:
    swap_events: list[SwapEvent] = []
    if swaps is not None and not swaps.empty:
        for row in swaps.itertuples(index=False):
            wallet = str(getattr(row, "trader", "") or "")
            if not wallet or getattr(row, "side", "") not in {"buy", "sell"}:
                continue
            amount = float(getattr(row, "amount_token", 0) or 0)
            if amount <= 0:
                continue
            swap_events.append(
                SwapEvent(
                    wallet=wallet,
                    block=int(row.block_number),
                    side=str(row.side),
                    amount=amount,
                    usd=float(getattr(row, "usd_value", 0) or 0),
                    tx_hash=str(row.tx_hash),
                    log_index=int(row.log_index),
                    pool=str(getattr(row, "pool_address", "") or ""),
                )
            )
    transfer_events: list[TransferEvent] = []
    if transfers is not None and not transfers.empty:
        for row in transfers.itertuples(index=False):
            src = str(getattr(row, "from_address", "") or "")
            dst = str(getattr(row, "to_address", "") or "")
            amount = float(getattr(row, "amount", 0) or 0)
            if not src or not dst or amount <= 0:
                continue
            transfer_events.append(
                TransferEvent(
                    src=src,
                    dst=dst,
                    block=int(row.block_number),
                    amount=amount,
                    tx_hash=str(row.tx_hash),
                    log_index=int(row.log_index),
                )
            )
    return swap_events, transfer_events


def assemble_from_events(
    token: str,
    symbol: str,
    swaps: list[SwapEvent],
    transfers: list[TransferEvent],
    *,
    seed: int | None = None,
    excluded: set[str] | None = None,
    seconds: float = 0.0,
    start_block: int = 0,
    end_block: int = 0,
) -> TokenTables:
    """The label is planted-or-rule. It is not a model feature."""
    excluded = excluded or set()
    real = detect(swaps, transfers, excluded=excluded)
    blocks = [swap.block for swap in swaps] or [start_block or 1]
    lo = min(blocks)
    hi = max(max(blocks), lo + 20_000)
    planted = inject_wash_groups(
        swaps,
        transfers,
        seed=seed if seed is not None else zlib.crc32(token.lower().encode()) & 0xFFFFFFFF,
        block_lo=lo,
        block_hi=hi,
    )
    augmented = detect(planted.swaps, planted.transfers, excluded=excluded)
    wide = detect(
        planted.swaps,
        planted.transfers,
        excluded=excluded,
        config=HeuristicConfig(max_block_gap=8_000, max_group_size=32),
    )
    wallets = build_feature_frame(planted.swaps, planted.transfers, augmented, excluded=excluded)
    trades = build_trade_frame(planted.swaps, planted.transfers, excluded=excluded, detection=augmented)
    wallets = _attach_wallet_labels(wallets, planted, token, symbol)
    trades = _attach_trade_labels(trades, planted, token, symbol)
    groups = _group_frame(planted, token, symbol, augmented, wide)
    return TokenTables(
        token=token.lower(),
        symbol=symbol,
        wallets=wallets,
        trades=trades,
        groups=groups,
        real_swaps=swaps,
        real_transfers=transfers,
        real_detection=real,
        n_real_swaps=len(swaps),
        n_real_transfers=len(transfers),
        seconds=seconds,
        start_block=start_block or lo,
        end_block=end_block or hi,
    )


def _attach_wallet_labels(
    frame: pd.DataFrame,
    planted: Injection,
    token: str,
    symbol: str,
) -> pd.DataFrame:
    out = frame.copy()
    injected = planted.wallets
    group_of = {}
    for group in planted.groups:
        for wallet in group.wallets:
            group_of[wallet] = group
    out["token"] = token.lower()
    out["symbol"] = symbol
    out["injected"] = out["wallet"].map(lambda wallet: int(wallet in injected))
    out["pattern_type"] = out["wallet"].map(lambda wallet: group_of[wallet].pattern_type if wallet in group_of else "")
    out["n_buyers"] = out["wallet"].map(lambda wallet: group_of[wallet].n_buyers if wallet in group_of else 0)
    out["n_sellers"] = out["wallet"].map(lambda wallet: group_of[wallet].n_sellers if wallet in group_of else 0)
    out["n_wallets"] = out["wallet"].map(lambda wallet: group_of[wallet].n_wallets if wallet in group_of else 0)
    out["label"] = ((out["heuristic_label"].astype(int) == 1) | (out["injected"] == 1)).astype(int)
    return out


def _attach_trade_labels(
    frame: pd.DataFrame,
    planted: Injection,
    token: str,
    symbol: str,
) -> pd.DataFrame:
    out = frame.copy()
    group_of = {}
    for index, group in enumerate(planted.groups):
        for key in group.swap_ids:
            group_of[key] = (index, group)
    patterns, buyers, sellers, sizes, ids, injected, rules = [], [], [], [], [], [], []
    for row in out.itertuples(index=False):
        key = (str(row.tx_hash), int(row.log_index))
        found = group_of.get(key)
        if found is None:
            patterns.append("")
            buyers.append(0)
            sellers.append(0)
            sizes.append(0)
            ids.append(-1)
            injected.append(0)
            rules.append("")
        else:
            gid, group = found
            patterns.append(group.pattern_type)
            buyers.append(group.n_buyers)
            sellers.append(group.n_sellers)
            sizes.append(group.n_wallets)
            ids.append(gid)
            injected.append(1)
            rules.append(group.rule)
    out["token"] = token.lower()
    out["symbol"] = symbol
    out["injected"] = injected
    out["pattern_type"] = patterns
    out["n_buyers"] = buyers
    out["n_sellers"] = sellers
    out["n_wallets"] = sizes
    out["group_id"] = ids
    out["rule"] = rules
    out["label"] = ((out["heuristic_label"].astype(int) == 1) | (out["injected"] == 1)).astype(int)
    return out


def _rule_caught(found: Detection, key: tuple[str, int], rule: str) -> bool:
    if key not in found.flagged:
        return False
    if not rule:
        return True
    return rule in found.reasons.get(key, {}).get("rules", "").split(",")


def _group_frame(
    planted: Injection,
    token: str,
    symbol: str,
    found: Detection,
    wide: Detection,
) -> pd.DataFrame:
    rows = []
    for index, group in enumerate(planted.groups):
        source = wide if group.wide else found
        hit = 0
        if group.swap_ids and all(_rule_caught(source, key, group.rule) for key in group.swap_ids):
            hit = 1
        rows.append(
            {
                "token": token.lower(),
                "symbol": symbol,
                "group_id": index,
                "pattern_type": group.pattern_type,
                "rule": group.rule,
                "wide": int(group.wide),
                "n_buyers": group.n_buyers,
                "n_sellers": group.n_sellers,
                "n_wallets": group.n_wallets,
                "n_swaps": len(group.swap_ids),
                "injected": 1,
                "rule_hit": hit,
            }
        )
    return pd.DataFrame(rows)

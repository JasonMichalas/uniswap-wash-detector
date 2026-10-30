"""Wallet context the rules never read: lifecycle position, LP overlap, shared gas fingerprints, rhythm, fee burn."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

CONTEXT_FEATURES = [
    "first_trade_rel",
    "early_volume_share",
    "active_days",
    "is_lp",
    "log_gas_twins",
    "gas_twin_frac",
    "burstiness",
    "sleepiness_h",
    "tx_per_active_block",
    "daily_usd_cv",
    "fee_burn",
    "loss_to_burn",
]
EARLY = pd.Timedelta(days=2)
MAX_FEE_PPM = 100_000  # v4 dynamic-fee flags and junk read above this


def _providers(liquidity: pd.DataFrame | None) -> set[str]:
    if liquidity is None or liquidity.empty or "provider" not in liquidity.columns:
        return set()
    return set(liquidity["provider"].dropna().astype(str).str.lower())


def _first_funders(transfers: pd.DataFrame | None, pools: set[str]) -> dict[str, str]:
    if transfers is None or transfers.empty:
        return {}
    t = transfers[["from_address", "to_address", "block_number", "log_index"]].copy()
    t["from_address"] = t["from_address"].astype(str).str.lower()
    t["to_address"] = t["to_address"].astype(str).str.lower()
    t = t[~t["from_address"].isin(pools)].sort_values(["block_number", "log_index"])
    return t.drop_duplicates("to_address").set_index("to_address")["from_address"].to_dict()


def _twins(txs: pd.DataFrame) -> pd.DataFrame:
    """Wallets sharing an exact (gas limit, tip) pair. Pairs used by many wallets are wallet defaults, not a script."""
    limit = pd.to_numeric(txs["gas_limit"], errors="coerce")
    tip = pd.to_numeric(txs["priority_fee"], errors="coerce").round(6)
    tx = txs.assign(fp=limit.astype(str) + "|" + tip.astype(str))[limit.notna() & tip.notna()]
    n_wallets = txs["trader"].nunique()
    users = tx.groupby("fp")["trader"].nunique()
    default = users > max(20, 0.05 * n_wallets)
    tx = tx.assign(others=tx["fp"].map(users) - 1)
    tx.loc[tx["fp"].map(default), "others"] = 0
    out = tx.groupby("trader")["others"].agg(log_gas_twins="max", gas_twin_frac=lambda o: float((o > 0).mean()))
    out["log_gas_twins"] = np.log1p(out["log_gas_twins"])
    return out


def _sleepiness(ts: pd.Series) -> float:
    """Mean over two-day bins of the longest gap in hours (Niedermayer et al. 2024)."""
    if len(ts) < 3:
        return np.nan
    bins = ((ts - ts.iloc[0]) // pd.Timedelta(days=2)).to_numpy()
    sec = (ts - ts.iloc[0]).dt.total_seconds().to_numpy()
    gaps = [np.diff(sec[bins == b]).max() for b in np.unique(bins) if (bins == b).sum() >= 2]
    return float(np.mean(gaps)) / 3600 if gaps else np.nan


def context_features(
    swaps: pd.DataFrame,
    transfers: pd.DataFrame | None,
    pools: set[str],
    liquidity: pd.DataFrame | None = None,
    pool_fees: dict[str, float] | None = None,
    eth_usd: float = np.nan,
) -> pd.DataFrame:
    """`swaps` is BehaviourFeatures.swaps: one row per trader swap with ts, usd, amount, fee_eth and tx metadata."""
    if swaps.empty:
        return pd.DataFrame(columns=["wallet", *CONTEXT_FEATURES])
    start, end = swaps["ts"].min(), swaps["ts"].max()
    span = max((end - start).total_seconds(), 1.0)
    priced = swaps.sort_values("block_number")
    unit = (priced["usd"] / priced["amount"].where(priced["amount"] > 0)).dropna()
    price = float(unit.iloc[-1]) if len(unit) else 0.0
    fee_ppm = swaps["pool_address"].str.lower().map(pool_fees or {}).astype(float)
    swaps = swaps.assign(pool_fee_usd=swaps["usd"] * fee_ppm.where(fee_ppm <= MAX_FEE_PPM) / 1e6)
    providers = _providers(liquidity)
    funders = _first_funders(transfers, pools)
    twins = _twins(swaps.drop_duplicates("tx_hash"))
    rows = []
    for wallet, g in swaps.groupby("trader"):
        g = g.sort_values(["block_number", "log_index"])
        txs = g.drop_duplicates("tx_hash")
        volume = float(g["usd"].sum())
        days = g.groupby(g["ts"].dt.floor("D"))["usd"].sum()
        sec = (txs["ts"] - start).dt.total_seconds().to_numpy()
        gaps = np.diff(sec)
        mu, sd = (gaps.mean(), gaps.std()) if len(gaps) >= 2 else (np.nan, np.nan)
        gas_usd = float(txs["fee_eth"].sum()) * eth_usd
        pool_fee = float(g["pool_fee_usd"].sum()) if g["pool_fee_usd"].notna().any() else np.nan
        burn = gas_usd + (pool_fee if pool_fee == pool_fee else 0.0)
        signed = np.where(g["side"] == "sell", 1.0, -1.0) * g["usd"]
        held = (np.where(g["side"] == "buy", 1.0, -1.0) * g["amount"]).sum()
        gross = float(signed.sum() + held * price)
        rows.append(
            {
                "wallet": wallet,
                "first_trade_rel": (g["ts"].iloc[0] - start).total_seconds() / span,
                "early_volume_share": float(g.loc[g["ts"] < start + EARLY, "usd"].sum() / volume) if volume > 0 else np.nan,
                "active_days": len(days),
                "is_lp": float(wallet in providers or funders.get(wallet) in providers),
                "burstiness": float((sd - mu) / (sd + mu)) if mu == mu and sd + mu > 0 else np.nan,
                "sleepiness_h": _sleepiness(txs["ts"]),
                "tx_per_active_block": len(txs) / txs["block_number"].nunique(),
                "daily_usd_cv": float(days.std(ddof=0) / days.mean()) if len(days) >= 2 and days.mean() > 0 else np.nan,
                "fee_burn": burn / volume if volume > 0 and burn == burn and burn > 0 else np.nan,
                "loss_to_burn": max(-5.0, min(5.0, -gross / burn)) if burn == burn and burn > 0 else np.nan,
            }
        )
    out = pd.DataFrame(rows).set_index("wallet").join(twins).reset_index()
    out["active_days"] = out["active_days"].map(math.log1p)
    return out[["wallet", *CONTEXT_FEATURES]]

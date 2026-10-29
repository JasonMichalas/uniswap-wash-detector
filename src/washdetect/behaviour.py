"""Behaviour features that the wash rules never read: timing, sizes, gas, wallet age, fees, diversity."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from washdetect.config import AGGREGATORS, ROUTERS, WETH

WALLET_BEHAVIOUR = [
    "log_n_trades",
    "hour_entropy",
    "active_hours",
    "gap_cv",
    "log_gap_median",
    "gap_regularity",
    "round_amount_frac",
    "round_usd_frac",
    "repeat_size_frac",
    "benford_mad",
    "log_mean_usd",
    "usd_cv",
    "gas_rel",
    "log_tip",
    "gas_limit_variety",
    "top_of_block_frac",
    "log_nonce",
    "log_nonce_rate",
    "token_tx_share",
    "fee_to_volume",
    "pnl_to_volume",
    "n_pools",
    "n_targets",
    "own_contract_frac",
    "log_peers",
]

TRADE_BEHAVIOUR = [
    "t_log_usd",
    "t_round_amount",
    "t_round_usd",
    "t_gas_rel",
    "t_log_tip",
    "t_top_of_block",
    "t_hour_sin",
    "t_hour_cos",
    *WALLET_BEHAVIOUR,
]

META = ("tx_to", "nonce", "tx_index", "gas_price", "priority_fee", "gas_limit")
BENFORD = np.log10(1 + 1 / np.arange(1, 10))
KNOWN_TARGETS = ROUTERS | AGGREGATORS


def is_round(values: pd.Series, digits: int = 2) -> pd.Series:
    """True when the value has at most `digits` significant figures, within 0.1%."""
    x = values.astype(float).abs()
    safe = x.where(x > 0, np.nan)
    scale = 10 ** (np.floor(np.log10(safe)) - (digits - 1))
    return ((safe / scale).round() * scale - safe).abs() <= 0.001 * safe


def first_digit(values: pd.Series) -> pd.Series:
    x = values.astype(float).abs()
    x = x[x > 0]
    return (x / 10 ** np.floor(np.log10(x))).astype(int).clip(1, 9)


def benford_mad(values: pd.Series) -> float:
    digits = first_digit(values)
    if len(digits) < 5:
        return np.nan
    seen = np.bincount(digits, minlength=10)[1:] / len(digits)
    return float(np.abs(seen - BENFORD).mean())


def _entropy(hours: pd.Series) -> float:
    if len(hours) < 3:
        return np.nan
    p = hours.value_counts(normalize=True).to_numpy()
    return float(-(p * np.log(p)).sum() / math.log(24))


class BehaviourFeatures:
    """Builds one row per trader (tx.from) and one row per swap from cached swaps, transfers and tx metadata."""

    def __init__(self, swaps: pd.DataFrame, transfers: pd.DataFrame | None, pools: set[str] | None = None) -> None:
        self.pools = {p.lower() for p in (pools or set())}
        self.swaps = self._prepare(swaps)
        self.transfers = transfers if transfers is not None else pd.DataFrame()

    def _prepare(self, swaps: pd.DataFrame) -> pd.DataFrame:
        frame = swaps[swaps["trader"].notna() & swaps["side"].isin(["buy", "sell"])].copy()
        for column in META:
            if column not in frame.columns:
                frame[column] = np.nan
        frame["trader"] = frame["trader"].str.lower()
        frame["usd"] = pd.to_numeric(frame["usd_value"], errors="coerce").fillna(0.0)
        frame["amount"] = pd.to_numeric(frame["amount_token"], errors="coerce").fillna(0.0)
        frame["ts"] = pd.to_datetime(frame["block_timestamp"])
        frame["hour"] = frame["ts"].dt.hour
        frame["round_amount"] = is_round(frame["amount"]).astype(float)
        frame["round_usd"] = is_round(frame["usd"]).astype(float)
        gas = pd.to_numeric(frame["gas_price"], errors="coerce")
        by_block = frame.assign(g=gas).drop_duplicates("tx_hash").sort_values("block_number")
        local = by_block.set_index("tx_hash")["g"].rolling(51, center=True, min_periods=1).median()
        frame["gas_rel"] = gas / frame["tx_hash"].map(local)
        frame["tip"] = np.log1p(pd.to_numeric(frame["priority_fee"], errors="coerce"))
        frame["top"] = (pd.to_numeric(frame["tx_index"], errors="coerce") <= 2).astype(float)
        frame.loc[frame["tx_index"].isna(), "top"] = np.nan
        frame["fee_eth"] = gas * pd.to_numeric(frame["gas_limit"], errors="coerce") / 1e9
        return frame

    def eth_usd(self) -> float:
        weth = self.swaps[(self.swaps["quote_token"].str.lower() == WETH) & (self.swaps["amount_quote"] > 0)]
        prices = weth["usd"] / weth["amount_quote"]
        prices = prices[prices > 0]
        return float(prices.median()) if len(prices) else np.nan

    def peers(self) -> dict[str, int]:
        if self.transfers.empty:
            return {}
        t = self.transfers[["from_address", "to_address"]].astype(str).apply(lambda c: c.str.lower())
        t = t[~t["from_address"].isin(self.pools) & ~t["to_address"].isin(self.pools)]
        both = pd.concat([t.rename(columns={"from_address": "a", "to_address": "b"}),
                          t.rename(columns={"to_address": "a", "from_address": "b"})])
        return both.groupby("a")["b"].nunique().to_dict()

    def wallets(self) -> pd.DataFrame:
        eth_usd = self.eth_usd()
        peers = self.peers()
        last_price = self.swaps.sort_values("block_number").pipe(
            lambda f: (f["usd"] / f["amount"].where(f["amount"] > 0)).dropna()
        )
        price = float(last_price.iloc[-1]) if len(last_price) else 0.0
        rows = []
        for wallet, g in self.swaps.groupby("trader"):
            rows.append(self._wallet_row(wallet, g, eth_usd, price, peers.get(wallet, 0)))
        return pd.DataFrame(rows, columns=["wallet", *WALLET_BEHAVIOUR])

    def _wallet_row(self, wallet: str, g: pd.DataFrame, eth_usd: float, price: float, peers: int) -> dict:
        g = g.sort_values(["block_number", "log_index"])
        txs = g.drop_duplicates("tx_hash")
        gaps = txs["ts"].diff().dt.total_seconds().dropna()
        gaps = gaps[gaps > 0]
        med = float(gaps.median()) if len(gaps) else np.nan
        sizes = g["amount"]
        repeat = [((sizes[g["side"] == side] - a).abs() <= 0.005 * a).sum() > 1 for side, a in zip(g["side"], sizes, strict=True)]
        volume = float(g["usd"].sum())
        signed = np.where(g["side"] == "sell", 1.0, -1.0)
        tokens = np.where(g["side"] == "buy", 1.0, -1.0) * g["amount"]
        fees = float(txs["fee_eth"].sum()) * eth_usd if eth_usd == eth_usd else np.nan
        pnl = float((signed * g["usd"]).sum() + tokens.sum() * price) - (fees if fees == fees else 0.0)
        nonces = pd.to_numeric(txs["nonce"], errors="coerce").dropna()
        days = max((g["ts"].max() - g["ts"].min()).total_seconds() / 86400, 1.0)
        span = nonces.max() - nonces.min() + 1 if len(nonces) else np.nan
        targets = txs["tx_to"].dropna().str.lower()
        own = ~targets.isin(KNOWN_TARGETS) & ~targets.isin(self.pools)
        limits = pd.to_numeric(txs["gas_limit"], errors="coerce").dropna()
        mean_usd = float(g["usd"].mean())
        return {
            "wallet": wallet,
            "log_n_trades": math.log1p(len(g)),
            "hour_entropy": _entropy(g["hour"]),
            "active_hours": g["hour"].nunique() / 24,
            "gap_cv": float(gaps.std(ddof=0) / gaps.mean()) if len(gaps) >= 2 else np.nan,
            "log_gap_median": math.log1p(med) if med == med else np.nan,
            "gap_regularity": float(((gaps - med).abs() <= 0.1 * med).mean()) if len(gaps) >= 2 else np.nan,
            "round_amount_frac": float(g["round_amount"].mean()),
            "round_usd_frac": float(g["round_usd"].mean()),
            "repeat_size_frac": float(np.mean(repeat)),
            "benford_mad": benford_mad(g["usd"]),
            "log_mean_usd": math.log1p(max(mean_usd, 0.0)),
            "usd_cv": float(g["usd"].std(ddof=0) / mean_usd) if len(g) >= 2 and mean_usd > 0 else np.nan,
            "gas_rel": float(txs["gas_rel"].median()),
            "log_tip": float(txs["tip"].median()),
            "gas_limit_variety": limits.nunique() / len(limits) if len(limits) >= 2 else np.nan,
            "top_of_block_frac": float(txs["top"].mean()),
            "log_nonce": math.log1p(nonces.min()) if len(nonces) else np.nan,
            "log_nonce_rate": math.log1p(span / days) if span == span else np.nan,
            "token_tx_share": min(1.0, len(txs) / span) if span == span and span > 0 else np.nan,
            "fee_to_volume": fees / volume if volume > 0 and fees == fees else np.nan,
            "pnl_to_volume": max(-1.0, min(1.0, pnl / volume)) if volume > 0 else np.nan,
            "n_pools": g["pool_address"].nunique(),
            "n_targets": targets.nunique(),
            "own_contract_frac": float(own.mean()) if len(own) else np.nan,
            "log_peers": math.log1p(peers),
        }

    def trades(self, wallets: pd.DataFrame | None = None) -> pd.DataFrame:
        wallets = self.wallets() if wallets is None else wallets
        s = self.swaps
        out = pd.DataFrame(
            {
                "wallet": s["trader"],
                "tx_hash": s["tx_hash"].str.lower(),
                "log_index": s["log_index"].astype(int),
                "block_number": s["block_number"].astype(int),
                "usd": s["usd"],
                "t_log_usd": np.log1p(s["usd"].clip(lower=0)),
                "t_round_amount": s["round_amount"],
                "t_round_usd": s["round_usd"],
                "t_gas_rel": s["gas_rel"],
                "t_log_tip": s["tip"],
                "t_top_of_block": s["top"],
                "t_hour_sin": np.sin(2 * np.pi * s["hour"] / 24),
                "t_hour_cos": np.cos(2 * np.pi * s["hour"] / 24),
            }
        )
        return out.merge(wallets, on="wallet", how="left").reset_index(drop=True)


def with_tx_meta(swaps: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    if meta is None or meta.empty:
        return swaps
    meta = meta.drop_duplicates("tx_hash")
    return swaps.drop(columns=[c for c in META if c in swaps.columns]).merge(meta, on="tx_hash", how="left")

"""Attach model columns to a frame the query engine already filtered.

Features are computed from the full loaded window, then joined back onto
the rows the graph kept. The rule's decision is not an input.
"""

from __future__ import annotations

import pandas as pd

from washdetect.dataset import events_from_frames
from washdetect.detector import Detection
from washdetect.features import build_feature_frame, build_trade_frame
from washdetect.models import ModelBundle, ModelNotTrained, WashModel


class ScoreCache:
    def __init__(self) -> None:
        self.bundle: ModelBundle | None = None
        self.trades: pd.DataFrame | None = None
        self.wallets: pd.DataFrame | None = None

    def ensure(
        self,
        swaps: pd.DataFrame | None,
        transfers: pd.DataFrame | None,
        excluded: set[str],
        model_dir: str | None = None,
    ) -> ModelBundle:
        if self.bundle is not None and self.trades is not None and self.wallets is not None:
            return self.bundle
        try:
            model = WashModel.load(model_dir)
        except ModelNotTrained as exc:
            raise ModelNotTrained(str(exc)) from exc
        self.bundle = model.bundle
        swap_events, transfer_events = events_from_frames(swaps, transfers)
        empty = Detection(cycles=[], wallets=[], flagged={})
        self.wallets = build_feature_frame(swap_events, transfer_events, empty, excluded=excluded)
        self.trades = build_trade_frame(swap_events, transfer_events, excluded=excluded, detection=empty)
        if not self.wallets.empty:
            self.wallets = model.score(self.wallets, kind="wallet")
        if not self.trades.empty:
            self.trades = model.score(self.trades, kind="trade")
        return self.bundle


def _trade_lookup(trades: pd.DataFrame) -> dict[tuple[str, int], tuple[float, float]]:
    lookup: dict[tuple[str, int], tuple[float, float]] = {}
    if trades is None or trades.empty:
        return lookup
    for row in trades.itertuples(index=False):
        lookup[(str(row.tx_hash).lower(), int(row.log_index))] = (
            float(row.ml_score),
            float(row.anomaly_score),
        )
    return lookup


def _wallet_lookup(wallets: pd.DataFrame) -> dict[str, tuple[float, float]]:
    lookup: dict[str, tuple[float, float]] = {}
    if wallets is None or wallets.empty:
        return lookup
    for row in wallets.itertuples(index=False):
        lookup[str(row.wallet).lower()] = (float(row.ml_score), float(row.anomaly_score))
    return lookup


def score_rows(frame: pd.DataFrame, cache: ScoreCache, mode: str) -> pd.DataFrame:
    """Add ml_score and/or anomaly_score. Swap rows also gain ml_weighted_usd."""
    out = frame.copy()
    trade_keys = _trade_lookup(cache.trades if cache.trades is not None else pd.DataFrame())
    wallet_keys = _wallet_lookup(cache.wallets if cache.wallets is not None else pd.DataFrame())
    ml_values: list[float] = []
    anomaly_values: list[float] = []
    is_swap = "tx_hash" in out.columns and "log_index" in out.columns and "trader" in out.columns
    if is_swap:
        for row in out.itertuples(index=False):
            key = (str(row.tx_hash).lower(), int(row.log_index))
            ml_score, anomaly = trade_keys.get(key, (0.0, 0.0))
            ml_values.append(ml_score)
            anomaly_values.append(anomaly)
    elif "wallet" in out.columns:
        for row in out.itertuples(index=False):
            ml_score, anomaly = wallet_keys.get(str(row.wallet).lower(), (0.0, 0.0))
            ml_values.append(ml_score)
            anomaly_values.append(anomaly)
    else:
        raise ValueError("ML boxes need swaps (trader, tx_hash) or a wallet column.")
    if mode in {"ml", "both"}:
        out["ml_score"] = ml_values
        if "usd_value" in out.columns:
            weighted = []
            for score, usd in zip(ml_values, out["usd_value"].tolist(), strict=True):
                try:
                    amount = float(usd)
                except (TypeError, ValueError):
                    amount = 0.0
                if amount != amount or amount < 0:  # NaN
                    amount = 0.0
                weighted.append(score * amount)
            out["ml_weighted_usd"] = weighted
    if mode in {"anomaly", "both"}:
        out["anomaly_score"] = anomaly_values
    return out


def explain_wallets(wallets: list[str], cache: ScoreCache, *, limit: int = 25, k: int = 5) -> tuple[pd.DataFrame, bool]:
    """Top SHAP reasons. Returns the table and whether the wallet list was truncated."""
    if cache.bundle is None or cache.wallets is None:
        raise ModelNotTrained("Explain needs a loaded model.")
    chosen = []
    seen: set[str] = set()
    for wallet in wallets:
        key = str(wallet).lower()
        if key and key not in seen:
            seen.add(key)
            chosen.append(key)
    truncated = len(chosen) > limit
    chosen = chosen[:limit]
    features = cache.wallets[cache.wallets["wallet"].isin(chosen)]
    if features.empty:
        return (
            pd.DataFrame(columns=["wallet", "rank", "feature", "feature_value", "shap_value", "direction"]),
            truncated,
        )
    reasons = WashModel(cache.bundle).explain(features, kind="wallet", k=k)
    order = {wallet: index for index, wallet in enumerate(chosen)}
    reasons["_order"] = reasons["wallet"].map(lambda wallet: order.get(str(wallet), 10**6))
    reasons = reasons.sort_values(["_order", "rank"]).drop(columns=["_order"])
    return reasons.reset_index(drop=True), truncated

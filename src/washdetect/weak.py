"""Weak labels: each rule votes, and a Dawid-Skene label model learns how far to trust each vote."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from washdetect.config import HeuristicConfig, hop_addresses
from washdetect.detector import WashDetector
from washdetect.events import SwapEvent, TransferEvent
from washdetect.trades import TradeResolver

ABSTAIN = -1
VOTERS = ["R1", "R2", "R3", "R4", "R5", "R6", "NM", "B25", "one_sided", "single", "dropped", "slow"]


def quick_flips(swaps: list[SwapEvent], blocks: int = 25, usd_gap: float = 0.01, repeats: int = 3) -> set[tuple[str, int]]:
    """Chainalysis 2025: a buy and a sell within 25 blocks, USD within 1%, at least three times per address."""
    by_wallet: dict[str, list[SwapEvent]] = defaultdict(list)
    for swap in swaps:
        by_wallet[swap.wallet].append(swap)
    flagged: set[tuple[str, int]] = set()
    for rows in by_wallet.values():
        rows.sort(key=lambda s: (s.block, s.log_index))
        used: set[int] = set()
        pairs = []
        for i, a in enumerate(rows):
            if i in used or a.usd <= 0:
                continue
            for j in range(i + 1, len(rows)):
                b = rows[j]
                if b.block - a.block > blocks:
                    break
                if j in used or b.side == a.side or b.usd <= 0:
                    continue
                if abs(a.usd - b.usd) < usd_gap * max(a.usd, b.usd):
                    used.update((i, j))
                    pairs.append((a, b))
                    break
        if len(pairs) >= repeats:
            flagged.update((s.tx_hash, s.log_index) for pair in pairs for s in pair)
    return flagged


class Votes:
    """One vote per swap from each rule (1 wash, 0 not wash, -1 abstain), then rolled up per trader."""

    def __init__(self, swaps: list[SwapEvent], transfers: list[TransferEvent], pools: set[str], config: HeuristicConfig | None = None) -> None:
        self.config = config or HeuristicConfig(net_ratio=0.05, max_block_gap=7200, max_group_size=24)
        self.swaps = [s for s in swaps if s.wallet and s.side in {"buy", "sell"} and s.amount > 0]
        self.transfers = transfers
        self.pools = {p.lower() for p in pools}
        self.detection = WashDetector(config=self.config).find_groups(self.swaps, transfers, self.pools)

    def trade_votes(self) -> pd.DataFrame:
        flagged = self.detection.reasons
        nm = {k for c in self.detection.cycles if c.rules == "R1" and len(c.swap_ids) >= 3 for k in c.swap_ids}
        fast = quick_flips(self.swaps)
        hops = self.pools | hop_addresses()
        kept = {(s.tx_hash, s.log_index) for s in TradeResolver(self.transfers, hops, self.config.net_ratio).resolve(list(self.swaps))}
        sides: dict[str, set[str]] = defaultdict(set)
        count: dict[str, int] = defaultdict(int)
        for s in self.swaps:
            sides[s.wallet].add(s.side)
            count[s.wallet] += 1
        slow = self._slow_wallets()
        rows = []
        for s in self.swaps:
            key = (s.tx_hash, s.log_index)
            rules = set(flagged.get(key, {}).get("rules", "").split(","))
            row = {"wallet": s.wallet, "tx_hash": s.tx_hash, "log_index": s.log_index}
            for rule in ("R1", "R2", "R3", "R4", "R5", "R6"):
                row[rule] = 1 if rule in rules else ABSTAIN
            row["NM"] = 1 if key in nm else ABSTAIN
            row["B25"] = 1 if key in fast else ABSTAIN
            row["one_sided"] = 0 if len(sides[s.wallet]) == 1 else ABSTAIN
            row["single"] = 0 if count[s.wallet] == 1 else ABSTAIN
            row["dropped"] = 0 if key not in kept else ABSTAIN
            row["slow"] = 0 if s.wallet in slow else ABSTAIN
            rows.append(row)
        return pd.DataFrame(rows, columns=["wallet", "tx_hash", "log_index", *VOTERS])

    def _slow_wallets(self) -> set[str]:
        """Both sides, but every buy and sell are more than a day apart."""
        by_wallet: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        for s in self.swaps:
            by_wallet[s.wallet][s.side].append(s.block)
        slow = set()
        for wallet, sides in by_wallet.items():
            if sides["buy"] and sides["sell"]:
                gap = min(abs(b - a) for b in sides["buy"] for a in sides["sell"])
                if gap > 7200:
                    slow.add(wallet)
        return slow

    @staticmethod
    def wallet_votes(trades: pd.DataFrame) -> pd.DataFrame:
        """A wallet gets a rule's positive vote if any of its swaps does; negative votes are wallet-wide already."""
        grouped = trades.groupby("wallet")[VOTERS].max().reset_index()
        return grouped


class LabelModel:
    """Binary Dawid-Skene with abstains: each voter has its own chance of saying 1, 0 or nothing under each class."""

    def __init__(self, iterations: int = 100, smoothing: float = 1.0) -> None:
        self.iterations = iterations
        self.smoothing = smoothing
        self.prior = 0.5
        self.table: np.ndarray | None = None  # voters x class x outcome(1, 0, abstain)

    @staticmethod
    def _outcomes(votes: np.ndarray) -> np.ndarray:
        return np.where(votes == 1, 0, np.where(votes == 0, 1, 2))

    def fit(self, votes: pd.DataFrame | np.ndarray) -> LabelModel:
        v = self._outcomes(np.asarray(votes))
        pos = (v == 0).any(axis=1)
        neg = (v == 1).any(axis=1)
        q = np.where(pos & ~neg, 0.9, np.where(pos, 0.5, 0.1))
        for _ in range(self.iterations):
            self._m_step(v, q)
            new = self._e_step(v)
            if np.abs(new - q).max() < 1e-6:
                q = new
                break
            q = new
        return self

    def _m_step(self, v: np.ndarray, q: np.ndarray) -> None:
        self.prior = float(np.clip(q.mean(), 0.01, 0.99))
        table = np.zeros((v.shape[1], 2, 3))
        for k in range(3):
            hit = (v == k).astype(float)
            table[:, 1, k] = q @ hit + self.smoothing
            table[:, 0, k] = (1 - q) @ hit + self.smoothing
        self.table = table / table.sum(axis=2, keepdims=True)

    def _e_step(self, v: np.ndarray) -> np.ndarray:
        cols = np.arange(v.shape[1])
        log1 = np.log(self.prior) + np.log(self.table[cols, 1, v]).sum(axis=1)
        log0 = np.log(1 - self.prior) + np.log(self.table[cols, 0, v]).sum(axis=1)
        return 1 / (1 + np.exp(log0 - log1))

    def predict_proba(self, votes: pd.DataFrame | np.ndarray) -> np.ndarray:
        return self._e_step(self._outcomes(np.asarray(votes)))

    def weights(self, names: list[str]) -> pd.DataFrame:
        """How likely each voter is to say wash under each class."""
        return pd.DataFrame(
            {"voter": names, "p_vote1_if_wash": self.table[:, 1, 0], "p_vote1_if_not": self.table[:, 0, 0],
             "p_vote0_if_wash": self.table[:, 1, 1], "p_vote0_if_not": self.table[:, 0, 1]}
        )

"""Who funded each trader with ETH, and where its ETH went, from Blockscout's keyless API.

Lookups are cached in sqlite, shared across threads, and rate limited. A failed or slow lookup
leaves the feature missing, which the model handles.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from washdetect.config import CEX_HOT, hop_addresses

BLOCKSCOUT = "https://eth.blockscout.com"
CACHE = Path(os.environ.get("WASHDETECT_BLOCKSCOUT_CACHE", Path.home() / ".cache" / "washdetect" / "blockscout.sqlite"))
HUB_TXS = 2_000  # an address with more transactions than this is an exchange or service, not a controller
BUDGET = float(os.environ.get("WASHDETECT_FUNDING_SECONDS", "10"))
FUNDING_FEATURES = [
    "log_funder_fanout",
    "funder_is_hub",
    "log_same_tx_funded",
    "funder_is_lp",
    "log_fund_to_trade_h",
    "log_exit_fanin",
    "log_since_pool_h",
]


class Blockscout:
    def __init__(self, cache: Path = CACHE, per_second: float = 2.8, timeout: float = 10.0) -> None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS hits (key TEXT PRIMARY KEY, body TEXT)")
        self.lock = threading.Lock()
        self.gap = 1.0 / per_second
        self.next_slot = 0.0
        self.http = httpx.Client(base_url=BLOCKSCOUT, timeout=timeout)

    def cached(self, key: str) -> object | None:
        with self.lock:
            row = self.db.execute("SELECT body FROM hits WHERE key = ?", [key]).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, path: str, params: dict | None = None) -> object | None:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        hit = self.cached(key)
        if hit is not None:
            return hit
        for attempt in range(4):
            with self.lock:
                now = time.monotonic()
                slot = max(now, self.next_slot)
                self.next_slot = slot + self.gap
            time.sleep(max(0.0, slot - now))
            try:
                response = self.http.get(path, params=params)
            except httpx.HTTPError:
                time.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(2**attempt)
                continue
            if response.status_code != 200:
                return None
            body = response.json()
            with self.lock:
                self.db.execute("INSERT OR REPLACE INTO hits VALUES (?, ?)", [key, json.dumps(body)])
                self.db.commit()
            return body
        return None

    def items(self, address: str, kind: str, **params: str) -> list[dict] | None:
        body = self.get(f"/api/v2/addresses/{address}/{kind}", params)
        return list(body.get("items") or []) if isinstance(body, dict) else None

    def first_funding(self, address: str) -> dict | None:
        """Earliest inbound ETH. When the first transaction is outbound, gas came internally (a disperse contract)."""
        oldest = self.items(address, "transactions", sort="block_number", order="asc")
        if oldest is None:
            return None
        inbound = [t for t in oldest if _hash(t.get("to")) == address and int(t.get("value") or 0) > 0]
        if not oldest or _hash(oldest[0].get("from")) == address:
            internal = self.items(address, "internal-transactions", filter="to")
            if internal is None:
                return None
            inbound += [t for t in internal if int(t.get("value") or 0) > 0]
        skip = hop_addresses() - set(CEX_HOT)  # a router refund is not funding
        inbound = [t for t in inbound if _hash(t.get("from")) not in skip]
        if not inbound:
            return {}
        first = min(inbound, key=lambda t: t["timestamp"])
        tx = first.get("transaction_hash") or first.get("hash")
        return {"funder": _hash(first.get("from")), "tx": str(tx).lower(), "ts": int(pd.Timestamp(first["timestamp"]).timestamp())}

    def exit(self, address: str) -> str | None:
        """The address that got this wallet's most recent outbound ETH, skipping routers and known hubs."""
        latest = self.items(address, "transactions", filter="from")
        if latest is None:
            return None
        skip = hop_addresses() - set(CEX_HOT)
        for t in latest:
            to = _hash(t.get("to"))
            if int(t.get("value") or 0) > 0 and to and to not in skip:
                return to
        return ""

    def tx_count(self, address: str) -> int | None:
        body = self.get(f"/api/v2/addresses/{address}/counters", {})
        try:
            return int(body["transactions_count"]) if body else None
        except (KeyError, TypeError, ValueError):
            return None

    def created_at(self, address: str) -> int | None:
        info = self.get(f"/api/v2/addresses/{address}", {})
        tx = info.get("creation_transaction_hash") if isinstance(info, dict) else None
        detail = self.get(f"/api/v2/transactions/{tx}", {}) if tx else None
        stamp = detail.get("timestamp") if isinstance(detail, dict) else None
        return int(pd.Timestamp(stamp).timestamp()) if stamp else None

    def wallet(self, address: str) -> dict:
        funding = self.first_funding(address)
        return {"exit": self.exit(address), **({"funder": None} if funding is None else funding)}


def _hash(side: object) -> str:
    return str(side.get("hash") or "").lower() if isinstance(side, dict) else ""


_POOL: ThreadPoolExecutor | None = None


def lookup(wallets: list[str], pools: list[str], budget: float | None = None, client: Blockscout | None = None) -> tuple[dict, dict]:
    """Looks wallets and pools up in parallel. With a budget, returns what finished in time and lets the rest fill the cache.

    Transaction counts are fetched only for funders and exits shared by two or more traders, to spot exchanges.
    """
    global _POOL
    client = client or Blockscout()
    _POOL = _POOL or ThreadPoolExecutor(4, thread_name_prefix="blockscout")
    deadline = None if budget is None else time.monotonic() + budget
    jobs = {_POOL.submit(client.wallet, w): w for w in wallets}
    pool_jobs = {_POOL.submit(client.created_at, p): p for p in pools}
    found = _collect(jobs, deadline)
    created = _collect(pool_jobs, deadline)
    shared = pd.Series([info.get(role) for info in found.values() for role in ("funder", "exit") if info.get(role)])
    shared = [a for a, n in shared.value_counts().items() if n >= 2]
    counts = _collect({_POOL.submit(client.tx_count, a): a for a in shared}, deadline)
    for info in found.values():
        for role in ("funder", "exit"):
            if info.get(role) in counts:
                info[f"{role}_txs"] = counts[info[role]]
            elif info.get(role):
                info[f"{role}_txs"] = 0
    return found, created


def _collect(jobs: dict, deadline: float | None) -> dict:
    timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
    done, _ = wait(jobs, timeout=timeout)
    return {jobs[j]: j.result() for j in done if j.exception() is None}


def funding_features(
    swaps: pd.DataFrame,
    providers: set[str],
    wallets: dict[str, dict],
    pool_created: dict[str, int | None],
) -> pd.DataFrame:
    """One row per trader. Fan-out and fan-in count other traders of this token sharing the funder or the exit."""
    first = swaps.sort_values("block_number").groupby("trader").agg(ts=("ts", "first"), pool=("pool_address", "first"))
    first["ts"] = first["ts"].map(lambda t: t.timestamp())
    rows = []
    for wallet, info in wallets.items():
        rows.append({"wallet": wallet, **{k: info.get(k) for k in ("funder", "tx", "ts", "exit", "funder_txs", "exit_txs")}})
    frame = pd.DataFrame(rows, columns=["wallet", "funder", "tx", "ts", "exit", "funder_txs", "exit_txs"])
    frame = frame.rename(columns={"ts": "funded_ts"}).set_index("wallet").join(first, how="right")
    hub = (pd.to_numeric(frame["funder_txs"], errors="coerce") > HUB_TXS) | frame["funder"].isin(CEX_HOT)
    exit_hub = (pd.to_numeric(frame["exit_txs"], errors="coerce") > HUB_TXS) | frame["exit"].isin(CEX_HOT)
    funder = frame["funder"].where(frame["funder"].astype(bool) & ~hub)
    exits = frame["exit"].where(frame["exit"].astype(bool) & ~exit_hub)
    known = frame["funder"].notna()
    out = pd.DataFrame(index=frame.index)
    out["log_funder_fanout"] = np.log1p(funder.map(funder.value_counts()).fillna(1) - 1).where(known)
    out["funder_is_hub"] = hub.astype(float).where(frame["funder_txs"].notna())
    out["log_same_tx_funded"] = np.log1p(frame["tx"].map(frame["tx"].value_counts()).fillna(1) - 1).where(known)
    out["funder_is_lp"] = frame["funder"].isin(providers).astype(float).where(known)
    hours = (frame["ts"] - pd.to_numeric(frame["funded_ts"], errors="coerce")) / 3600
    out["log_fund_to_trade_h"] = np.log1p(hours.where(hours >= 0))
    out["log_exit_fanin"] = np.log1p(exits.map(exits.value_counts()).fillna(1) - 1).where(frame["exit"].notna())
    created = pd.to_numeric(frame["pool"].str.lower().map(pool_created), errors="coerce")
    since = (frame["ts"] - created) / 3600
    out["log_since_pool_h"] = np.log1p(since.clip(lower=0))
    out.index.name = "wallet"
    return out.reset_index()[["wallet", *FUNDING_FEATURES]]


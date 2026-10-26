"""Read a loaded window out of DuckDB for the query engine.

Running a graph uses this snapshot and then a throwaway connection. It does
not write the cache. If the window was never loaded, the caller gets a
sentence explaining that, not a partial answer.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from washdetect.features import build_wallet_table
from washdetect.store import Store


def window_ready(store: Store, token: str, start: int, end: int) -> str | None:
    pools = store.pools_for(token)
    if not pools:
        return "No pools are cached for this token. Load it before running a graph."
    if not store.range_covered(token, "transfers", start, end):
        return "Transfers for this window are not fully cached. Load the token for this range first."
    for pool in pools:
        dataset = f"swaps:{pool['pool_address']}"
        if not store.range_covered(token, dataset, start, end):
            return (
                f"Swaps for pool {pool['pool_address']} are not fully cached. "
                "Load the token for this range first."
            )
    return None


def read_window(
    store: Store,
    token: str,
    start: int,
    end: int,
    start_ts: datetime,
    end_ts: datetime,
) -> dict[str, object]:
    swaps = store.read_swaps(token, start, end)
    transfers = store.read_transfers(token, start, end)
    balances = store.latest_balances(token, end)
    wallets = build_wallet_table(
        swaps, transfers, balances, end_ts=end_ts, window_start_ts=start_ts
    )
    pools = pd.DataFrame(store.pools_for(token))
    addresses = set(pools["pool_address"].astype(str)) if not pools.empty else set()
    return {
        "swaps": swaps,
        "transfers": transfers,
        "wallets": wallets,
        "pools": pools,
        "pool_addresses": addresses,
    }

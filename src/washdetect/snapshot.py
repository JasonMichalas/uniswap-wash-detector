"""Read a loaded window out of DuckDB for the query engine.

Running a graph uses this snapshot and then a throwaway connection. It does
not write the cache. If the window was never loaded, the caller gets a
sentence explaining that, not a partial answer.
"""

from __future__ import annotations

from datetime import datetime

from washdetect.store import Store
from washdetect.token import TokenData


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
    return TokenData.load(store, token, start, end, start_ts, end_ts).frames()

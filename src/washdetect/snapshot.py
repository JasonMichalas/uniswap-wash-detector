from __future__ import annotations

from washdetect.store import Store, pool_log_gaps


def window_ready(store: Store, token: str, start: int, end: int) -> str | None:
    """A sentence when the window is missing, never a partial frame."""
    pools = store.pools_for(token)
    if not pools:
        return "No pools are cached for this token. Load it before running a graph."
    if not store.range_covered(token, "transfers", start, end):
        return "Transfers for this window are not fully cached. Load the token for this range first."
    have = store.coverage_by_dataset(token)
    for pool in pools:
        if pool_log_gaps(have, str(pool["pool_address"]), start, end):
            return (
                f"Swaps for pool {pool['pool_address']} are not fully cached. "
                "Load the token for this range first."
            )
    return None

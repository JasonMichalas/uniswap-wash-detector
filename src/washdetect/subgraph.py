from __future__ import annotations

import os
from typing import Any

import httpx

from washdetect.config import GRAPH_GATEWAY, UNISWAP_V2_SUBGRAPH, UNISWAP_V3_SUBGRAPH


class SubgraphError(Exception):
    pass


def api_key() -> str | None:
    key = os.environ.get("GRAPH_API_KEY", "").strip()
    return key or None


V3_QUERY = """
query Swaps($pools: [String!], $ts: Int!, $first: Int!) {
  swaps(
    first: $first
    orderBy: timestamp
    orderDirection: asc
    where: { pool_in: $pools, timestamp_gte: $ts }
  ) {
    id
    timestamp
    sender
    recipient
    origin
    amount0
    amount1
    amountUSD
    logIndex
    transaction { id blockNumber }
    pool {
      id
      feeTier
      token0 { id symbol decimals }
      token1 { id symbol decimals }
    }
  }
}
"""

V2_QUERY = """
query Swaps($pairs: [String!], $ts: Int!, $first: Int!) {
  swaps(
    first: $first
    orderBy: timestamp
    orderDirection: asc
    where: { pair_in: $pairs, timestamp_gte: $ts }
  ) {
    id
    timestamp
    sender
    to
    amount0In
    amount0Out
    amount1In
    amount1Out
    amountUSD
    logIndex
    transaction { id blockNumber }
    pair {
      id
      token0 { id symbol decimals }
      token1 { id symbol decimals }
    }
  }
}
"""


def _post(url: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    try:
        response = httpx.post(
            url, json={"query": query, "variables": variables}, timeout=40.0
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SubgraphError(str(exc)) from exc
    if body.get("errors"):
        raise SubgraphError(str(body["errors"][:1]))
    return body.get("data") or {}


def _paged(url: str, query: str, var_key: str, addresses: list[str], start_ts: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = start_ts
    seen: set[str] = set()
    for _page in range(40):
        data = _post(url, query, {var_key: addresses, "ts": cursor, "first": 1000})
        batch = data.get("swaps") or []
        if not batch:
            break
        fresh = 0
        last_ts = cursor
        for row in batch:
            rid = str(row.get("id"))
            last_ts = int(row.get("timestamp") or last_ts)
            if rid in seen:
                continue
            seen.add(rid)
            rows.append(row)
            fresh += 1
        if len(batch) < 1000:
            break
        if fresh == 0:
            break
        # The next page restarts at the last timestamp; `seen` drops that overlap.
        cursor = last_ts
    return rows


def fetch_swaps(
    *,
    v2_pools: list[str],
    v3_pools: list[str],
    start_ts: int,
    key: str | None = None,
) -> list[dict[str, Any]]:
    key = key if key is not None else api_key()
    if not key:
        raise SubgraphError("GRAPH_API_KEY is not set")
    rows: list[dict[str, Any]] = []
    if v3_pools:
        url = f"{GRAPH_GATEWAY}/{key}/subgraphs/id/{UNISWAP_V3_SUBGRAPH}"
        rows.extend(_paged(url, V3_QUERY, "pools", v2_lower(v3_pools), start_ts))
    if v2_pools:
        url = f"{GRAPH_GATEWAY}/{key}/subgraphs/id/{UNISWAP_V2_SUBGRAPH}"
        rows.extend(_paged(url, V2_QUERY, "pairs", v2_lower(v2_pools), start_ts))
    return rows


def v2_lower(addresses: list[str]) -> list[str]:
    return [a.lower() for a in addresses]


def map_subgraph_swap(row: dict[str, Any], token: str) -> dict[str, Any] | None:
    """v3 amounts are signed human units (positive means the token entered the pool)."""
    token = token.lower()
    if "pool" in row:
        pool = row["pool"]
        protocol = "v3"
        fee = int(pool.get("feeTier") or 0)
        amount0 = float(row.get("amount0") or 0)
        amount1 = float(row.get("amount1") or 0)
        recipient = (row.get("recipient") or "").lower()
    elif "pair" in row:
        pool = row["pair"]
        protocol = "v2"
        fee = 3000
        a0_in = float(row.get("amount0In") or 0)
        a0_out = float(row.get("amount0Out") or 0)
        a1_in = float(row.get("amount1In") or 0)
        a1_out = float(row.get("amount1Out") or 0)
        amount0 = a0_in - a0_out
        amount1 = a1_in - a1_out
        recipient = (row.get("to") or "").lower()
    else:
        return None

    token0 = (pool.get("token0") or {}).get("id", "").lower()
    token1 = (pool.get("token1") or {}).get("id", "").lower()
    if token == token0:
        raw, quote, quote_amount = amount0, token1, amount1
    elif token == token1:
        raw, quote, quote_amount = amount1, token0, amount0
    else:
        return None
    if raw == 0:
        return None
    tx = row.get("transaction") or {}
    tx_hash = str(tx.get("id") or "")
    if not tx_hash:
        return None
    block = tx.get("blockNumber")
    if block is None:
        return None
    try:
        log_index = int(row.get("logIndex") or 0)
    except (TypeError, ValueError):
        log_index = 0
    usd = row.get("amountUSD")
    return {
        "tx_hash": tx_hash.lower(),
        "log_index": log_index,
        "block_number": int(block),
        "timestamp": int(row.get("timestamp") or 0),
        "pool_address": str(pool.get("id") or "").lower(),
        "protocol": protocol,
        "fee": fee,
        "sender": (row.get("sender") or "").lower(),
        "recipient": recipient,
        "trader": (row.get("origin") or "").lower() or None,
        "side": "sell" if raw > 0 else "buy",
        "amount_token": abs(raw),
        "amount_quote": abs(float(quote_amount)),
        "quote_token": quote,
        "usd_value": float(usd) if usd not in (None, "") else None,
        "token0_symbol": (pool.get("token0") or {}).get("symbol") or "",
        "token1_symbol": (pool.get("token1") or {}).get("symbol") or "",
        "token0_decimals": int((pool.get("token0") or {}).get("decimals") or 18),
        "token1_decimals": int((pool.get("token1") or {}).get("decimals") or 18),
    }

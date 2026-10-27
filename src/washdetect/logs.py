from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from washdetect.abi import (
    BURN_V2,
    BURN_V3,
    MINT_V2,
    MINT_V3,
    SWAP_V2,
    SWAP_V3,
    classify_leg,
    decode_v2_mint_amounts,
    decode_v2_swap_amounts,
    decode_v3_burn_amounts,
    decode_v3_mint_amounts,
    decode_v3_swap_amounts,
    topic_address,
)
from washdetect.discover import PoolMeta
from washdetect.rpc import RpcClient
from washdetect.store import Store
from washdetect.window import _naive


def _hex_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value), 16)


def _log_ts(log: dict[str, Any]) -> int | None:
    raw = log.get("blockTimestamp")
    if raw is None:
        return None
    try:
        return _hex_int(raw)
    except (TypeError, ValueError):
        return None


def fetch_timestamps(
    client: RpcClient, store: Store, numbers: list[int], head: int, head_ts: datetime
) -> dict[int, datetime]:
    unique = sorted(set(numbers))
    known = store.known_blocks(unique)
    missing = [n for n in unique if n not in known]
    if missing:
        calls = [("eth_getBlockByNumber", [hex(n), False]) for n in missing]
        results = client.batch(calls, batch_size=25)
        rows: list[tuple[int, datetime]] = []
        for number, result in zip(missing, results, strict=False):
            if not isinstance(result, dict) or "timestamp" not in result:
                continue
            ts = datetime.fromtimestamp(int(result["timestamp"], 16), UTC)
            ts = _naive(ts)
            known[number] = ts
            rows.append((number, ts))
        store.remember_blocks(rows)
    for number in unique:
        if number not in known:
            known[number] = head_ts - timedelta(seconds=12 * (head - number))
    return known


def _swap_tuple(
    token: str,
    tx_hash: str,
    log_index: int,
    block: int,
    ts: datetime,
    pool: str,
    protocol: str,
    sender: str,
    recipient: str,
    trader: str | None,
    leg: dict[str, Any],
    usd: float | None,
) -> tuple[Any, ...]:
    return (
        token,
        tx_hash,
        log_index,
        block,
        ts,
        pool,
        protocol,
        sender,
        recipient,
        trader,
        leg["side"],
        leg["amount_token"],
        leg["amount_quote"],
        leg["quote_token"],
        usd,
        leg["amount0_raw"],
        leg["amount1_raw"],
    )


def decode_pool_log(
    log: dict[str, Any], pool: PoolMeta, token: str
) -> tuple[str, dict[str, Any]] | None:
    topics = [str(t).lower() for t in (log.get("topics") or [])]
    if not topics:
        return None
    kind_topic = topics[0]
    data = str(log.get("data") or "0x")
    try:
        if pool.protocol == "v2" and kind_topic == SWAP_V2:
            a0_in, a1_in, a0_out, a1_out = decode_v2_swap_amounts(data)
            leg = classify_leg(
                token, pool.token0, pool.token1, pool.token0_decimals, pool.token1_decimals,
                a0_in - a0_out, a1_in - a1_out,
            )
            if not leg:
                return None
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            recipient = topic_address(topics[2]) if len(topics) > 2 else ""
            return "swap", {**leg, "sender": sender, "recipient": recipient}
        if pool.protocol == "v3" and kind_topic == SWAP_V3:
            amount0, amount1 = decode_v3_swap_amounts(data)
            leg = classify_leg(
                token, pool.token0, pool.token1, pool.token0_decimals, pool.token1_decimals,
                amount0, amount1,
            )
            if not leg:
                return None
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            recipient = topic_address(topics[2]) if len(topics) > 2 else ""
            return "swap", {**leg, "sender": sender, "recipient": recipient}
        if pool.protocol == "v2" and kind_topic == MINT_V2:
            a0, a1 = decode_v2_mint_amounts(data)
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            return "liq", {"kind": "mint", "sender": sender, "amount0_raw": str(a0), "amount1_raw": str(a1)}
        if pool.protocol == "v2" and kind_topic == BURN_V2:
            a0, a1 = decode_v2_mint_amounts(data)
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            return "liq", {"kind": "burn", "sender": sender, "amount0_raw": str(a0), "amount1_raw": str(a1)}
        if pool.protocol == "v3" and kind_topic == MINT_V3:
            a0, a1 = decode_v3_mint_amounts(data)
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            return "liq", {"kind": "mint", "sender": sender, "amount0_raw": str(a0), "amount1_raw": str(a1)}
        if pool.protocol == "v3" and kind_topic == BURN_V3:
            a0, a1 = decode_v3_burn_amounts(data)
            sender = topic_address(topics[1]) if len(topics) > 1 else ""
            return "liq", {"kind": "burn", "sender": sender, "amount0_raw": str(a0), "amount1_raw": str(a1)}
    except Exception:
        return None
    return None


def _pool_topics(protocol: str) -> list[Any]:
    if protocol == "v2":
        return [[SWAP_V2, MINT_V2, BURN_V2]]
    return [[SWAP_V3, MINT_V3, BURN_V3]]

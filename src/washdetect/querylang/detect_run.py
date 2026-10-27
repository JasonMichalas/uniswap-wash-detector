"""Turn a swaps view and a transfers view into flagged streams."""

from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd

from washdetect.config import HeuristicConfig
from washdetect.detector import Detection, SwapEvent, TransferEvent, WashDetector
from washdetect.graph.catalog import field_map
from washdetect.querylang.ast import Detect
from washdetect.querylang.errors import QueryError
from washdetect.querylang.results import _Bundle, _Stream
from washdetect.querylang.sqlutil import _run


def _detector_config(expr: Detect) -> HeuristicConfig:
    kwargs: dict[str, float | int] = {}
    if expr.net_ratio is not None:
        if not 0 < float(expr.net_ratio) <= 0.5:
            raise QueryError("detect_cycles tolerance must be greater than 0 and at most 0.5.")
        kwargs["net_ratio"] = float(expr.net_ratio)
    if expr.max_block_gap is not None:
        gap = int(expr.max_block_gap)
        if not 1 <= gap <= 50_000:
            raise QueryError("detect_cycles window must be a whole number of blocks from 1 to 50000.")
        kwargs["max_block_gap"] = gap
    if expr.max_group_size is not None:
        size = int(expr.max_group_size)
        if not 2 <= size <= 64:
            raise QueryError("detect_cycles max group size must be a whole number from 2 to 64.")
        kwargs["max_group_size"] = size
    return HeuristicConfig(**kwargs)


def _run_detect(
    con: duckdb.DuckDBPyConnection,
    swaps_view: str,
    transfers_view: str,
    excluded: set[str],
    warnings: list[str],
    config: HeuristicConfig | None = None,
) -> Detection:
    swap_rows = con.execute(
        f"""
        SELECT trader, block_number, side, amount_token, usd_value, tx_hash, log_index, pool_address
        FROM {swaps_view}
        """
    ).fetchall()
    events: list[SwapEvent] = []
    dropped = 0
    for trader, block, side, amount, usd, tx_hash, log_index, pool in swap_rows:
        if not trader:
            dropped += 1
            continue
        events.append(
            SwapEvent(
                wallet=str(trader),
                block=int(block),
                side=str(side),
                amount=float(amount or 0),
                usd=float(usd or 0),
                tx_hash=str(tx_hash),
                log_index=int(log_index),
                pool=str(pool or ""),
            )
        )
    if dropped:
        warnings.append(
            f"{dropped} swaps had no resolved trader (tx.from) and were ignored by the detector."
        )
    transfer_rows = con.execute(
        f"""
        SELECT from_address, to_address, block_number, amount, tx_hash, log_index
        FROM {transfers_view}
        """
    ).fetchall()
    transfers = [
        TransferEvent(
            src=str(src),
            dst=str(dst),
            block=int(block),
            amount=float(amount or 0),
            tx_hash=str(tx_hash),
            log_index=int(log_index),
        )
        for src, dst, block, amount, tx_hash, log_index in transfer_rows
        if src and dst
    ]
    return WashDetector(config=config).find_groups(events, transfers, excluded)


def _register_detection(
    con: duckdb.DuckDBPyConnection,
    swaps_view: str,
    detection: Detection,
    fresh: Any,
) -> _Bundle:
    flags = pd.DataFrame(
        [
            {"tx_hash": tx, "log_index": int(log_index), "is_wash": True, "confidence": conf, "cycle_id": cid}
            for (tx, log_index), (conf, cid) in detection.flagged.items()
        ],
        columns=["tx_hash", "log_index", "is_wash", "confidence", "cycle_id"],
    )
    wallets = pd.DataFrame(
        [
            {
                "wallet": row.wallet,
                "confidence": row.confidence,
                "wash_volume_usd": row.wash_volume_usd,
                "round_trip_count": row.round_trip_count,
                "ring_count": row.ring_count,
                "swap_count": row.swap_count,
                "net_ratio": row.net_ratio,
                "volume_usd": row.volume_usd,
            }
            for row in detection.wallets
        ],
        columns=list(field_map("wash_wallets")),
    )
    cycles = pd.DataFrame(
        [
            {
                "cycle_id": row.cycle_id,
                "size": row.size,
                "kind": row.kind,
                "pattern_type": row.pattern_type,
                "n_buyers": row.n_buyers,
                "n_sellers": row.n_sellers,
                "n_wallets": row.n_wallets,
                "wallets": ",".join(row.wallets),
                "volume_usd": row.volume_usd,
                "confidence": row.confidence,
                "start_block": row.start_block,
                "end_block": row.end_block,
            }
            for row in detection.cycles
        ],
        columns=list(field_map("cycles")),
    )
    con.register(f"flags_{id(detection)}", flags)
    con.register(f"wash_wallets_{id(detection)}", wallets)
    con.register(f"cycles_{id(detection)}", cycles)
    swap_view = fresh()
    wallet_view = fresh()
    cycle_view = fresh()
    # The flagged stream keeps every swap. is_wash is false when the heuristic
    # did not claim it, so a ratio of the filtered stream over this one is the
    # wash percentage, and a daily group still has the clean volume.
    _run(
        con,
        f"""
        CREATE VIEW {swap_view} AS
        SELECT
            s.block_number,
            s.block_timestamp,
            s.pool_address,
            s.protocol,
            s.trader,
            s.side,
            s.amount_token,
            s.usd_value,
            s.tx_hash,
            s.log_index,
            COALESCE(f.is_wash, FALSE) AS is_wash,
            COALESCE(f.confidence, 0) AS confidence,
            f.cycle_id AS cycle_id
        FROM {swaps_view} s
        LEFT JOIN flags_{id(detection)} f
          ON s.tx_hash = f.tx_hash AND s.log_index = f.log_index
        """,
    )
    _run(con, f"CREATE VIEW {wallet_view} AS SELECT * FROM wash_wallets_{id(detection)}")
    _run(con, f"CREATE VIEW {cycle_view} AS SELECT * FROM cycles_{id(detection)}")
    return _Bundle(
        swaps=_Stream(swap_view, "swaps_flagged", field_map("swaps_flagged")),
        wallets=_Stream(wallet_view, "wash_wallets", field_map("wash_wallets")),
        cycles=_Stream(cycle_view, "cycles", field_map("cycles")),
    )


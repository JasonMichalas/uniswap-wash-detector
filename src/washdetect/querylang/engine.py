"""Run a Program against in-memory frames.

The SQL is built by this module, never by pasting the user's text in. Field
names have to be on the whitelist for that stream, and string values are
quoted as SQL literals. The only statements issued are CREATE VIEW and a
final SELECT, on a throwaway DuckDB connection, so a query cannot touch the
cache file.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import duckdb
import pandas as pd

from washdetect.config import MAX_WINDOW_DAYS, ROUTERS, HeuristicConfig
from washdetect.detector import Detection, SwapEvent, TransferEvent, WashDetector
from washdetect.features import WALLET_COLUMNS, build_wallet_table
from washdetect.graph.catalog import TYPES, field_map
from washdetect.querylang.ast import (
    Aggregate,
    AnomalyScore,
    Assign,
    Condition,
    Detect,
    Explain,
    Filter,
    GroupBy,
    Limit,
    MlScore,
    Output,
    Program,
    Project,
    Ratio,
    Sort,
    Source,
    TimeWindow,
)
from washdetect.querylang.errors import QueryError
from washdetect.scoring import ScoreCache, explain_wallets, score_rows

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_MAX_ROWS = 1000
_NUMBER_OPS = {">", ">=", "<", "<=", "=", "!="}
_EQ_OPS = {"=", "!="}

_SWAP_COLUMNS = [
    "block_number",
    "block_timestamp",
    "pool_address",
    "protocol",
    "trader",
    "side",
    "amount_token",
    "amount_quote",
    "quote_token",
    "usd_value",
    "tx_hash",
    "log_index",
]
_TRANSFER_COLUMNS = [
    "block_number",
    "block_timestamp",
    "from_address",
    "to_address",
    "amount",
    "tx_hash",
    "log_index",
]
_POOL_COLUMNS = [
    "pool_address",
    "protocol",
    "token0",
    "token1",
    "token0_symbol",
    "token1_symbol",
    "fee",
]


@dataclass
class OutputResult:
    kind: str
    title: str
    format: str
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool = False
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "kind": self.kind,
            "title": self.title,
            "format": self.format,
            "columns": self.columns,
            "rows": self.rows,
            "truncated": self.truncated,
        }
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass
class ExecResult:
    query: str
    sql: str
    outputs: list[OutputResult]
    warnings: list[str] = field(default_factory=list)
    detection: Detection | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "sql": self.sql,
            "outputs": [item.as_dict() for item in self.outputs],
            "warnings": self.warnings,
        }


@dataclass
class _Stream:
    view: str
    type_name: str
    fields: dict[str, str]
    note: str | None = None


_SCOREABLE = {"swaps", "swaps_flagged", "wallets", "wash_wallets"}


@dataclass
class _Bundle:
    swaps: _Stream
    wallets: _Stream
    cycles: _Stream


def execute_frames(
    program: Program,
    *,
    swaps: pd.DataFrame | None = None,
    transfers: pd.DataFrame | None = None,
    wallets: pd.DataFrame | None = None,
    pools: pd.DataFrame | None = None,
    pool_addresses: set[str] | None = None,
    end_ts: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    model_dir: str | None = None,
) -> ExecResult:
    """Execute `program`. Frames are the loaded window, already filtered to it."""
    end_ts = end_ts or datetime(2026, 1, 1)
    window_start = window_start or end_ts
    window_end = window_end or end_ts
    swaps_df = _ensure(swaps, _SWAP_COLUMNS)
    transfers_df = _ensure(transfers, _TRANSFER_COLUMNS)
    if wallets is None:
        wallets_df = build_wallet_table(
            swaps_df, transfers_df, None, end_ts=end_ts, window_start_ts=window_start
        )
    else:
        wallets_df = _ensure(wallets, WALLET_COLUMNS)
    pools_df = _ensure(pools, _POOL_COLUMNS)
    for frame in (swaps_df, transfers_df):
        if "block_timestamp" in frame.columns:
            frame["block_timestamp"] = pd.to_datetime(frame["block_timestamp"])

    con = duckdb.connect()
    # Keep frames alive for the life of the connection. DuckDB may view them.
    con.register("swaps_df", swaps_df)
    con.register("transfers_df", transfers_df)
    con.register("wallets_df", wallets_df)
    con.register("pools_df", pools_df)
    con.execute("CREATE VIEW base_swaps AS SELECT * FROM swaps_df")
    con.execute("CREATE VIEW base_transfers AS SELECT * FROM transfers_df")
    con.execute("CREATE VIEW base_wallets AS SELECT * FROM wallets_df")
    con.execute(
        """
        CREATE VIEW base_holders AS
        SELECT * FROM base_wallets
        WHERE balance IS NOT NULL AND balance > 0
        """
    )
    con.execute(
        """
        CREATE VIEW base_pools AS
        SELECT
            p.pool_address,
            p.protocol,
            p.token0,
            p.token1,
            p.token0_symbol,
            p.token1_symbol,
            p.fee,
            COUNT(s.tx_hash) AS swap_count,
            COALESCE(SUM(s.usd_value), 0) AS volume_usd
        FROM pools_df p
        LEFT JOIN base_swaps s ON s.pool_address = p.pool_address
        GROUP BY 1, 2, 3, 4, 5, 6, 7
        """
    )

    excluded = {addr.lower() for addr in (pool_addresses or set())} | set(ROUTERS)
    warnings: list[str] = []
    if not swaps_df.empty and "usd_value" in swaps_df.columns:
        missing_price = int(swaps_df["usd_value"].isna().sum())
        if missing_price:
            warnings.append(
                f"{missing_price} swaps have no USD price and are left out of USD totals. "
                "Stablecoin-quoted pools price cleanly; other quotes need an ETH price."
            )
    env: dict[str, _Stream | _Bundle] = {}
    sql_lines: list[str] = []
    outputs: list[OutputResult] = []
    detection: Detection | None = None
    counter = 0
    # Registered frames have to outlive the views that read them.
    kept: list[pd.DataFrame] = []
    cache = ScoreCache()

    def fresh(name: str) -> str:
        nonlocal counter
        counter += 1
        view = f"v_{counter}_{_safe_name(name)}"
        return view

    for stmt in program.statements:
        if isinstance(stmt, Assign):
            if stmt.name in env:
                raise QueryError(f"The name {stmt.name} is assigned twice.")
            _safe_name(stmt.name)
            bound_name = stmt.name
            value, produced = _eval(
                stmt.expr,
                env=env,
                con=con,
                excluded=excluded,
                warnings=warnings,
                end_ts=end_ts,
                window_start=window_start,
                window_end=window_end,
                fresh=lambda name=bound_name: fresh(name),
                sql_lines=sql_lines,
                comment=stmt.to_text(),
                cache=cache,
                base_swaps=swaps_df,
                base_transfers=transfers_df,
                model_dir=model_dir,
                kept=kept,
            )
            env[stmt.name] = value
            if isinstance(produced, Detection):
                detection = produced
        elif isinstance(stmt, Output):
            stream = _need_stream(env, stmt.name)
            _check_output(stmt, stream)
            result = _read_output(con, stream, stmt)
            outputs.append(result)
            sql_lines.append(f"-- {stmt.to_text()}\nSELECT * FROM {stream.view}")
        else:
            raise QueryError("A program is a list of assignments and outputs.")

    if not outputs:
        raise QueryError("The query does not output anything. Add an output number, table, or chart.")
    return ExecResult(
        query=program.to_text(),
        sql="\n\n".join(sql_lines).strip() + "\n",
        outputs=outputs,
        warnings=warnings,
        detection=detection,
    )


def _eval(
    expr: Any,
    *,
    env: dict[str, _Stream | _Bundle],
    con: duckdb.DuckDBPyConnection,
    excluded: set[str],
    warnings: list[str],
    end_ts: datetime,
    window_start: datetime,
    window_end: datetime,
    fresh: Any,
    sql_lines: list[str],
    comment: str,
    cache: ScoreCache,
    base_swaps: pd.DataFrame,
    base_transfers: pd.DataFrame,
    model_dir: str | None,
    kept: list[pd.DataFrame],
) -> tuple[_Stream | _Bundle, Detection | None]:
    if isinstance(expr, Source):
        view = {
            "swaps": "base_swaps",
            "transfers": "base_transfers",
            "wallets": "base_wallets",
            "holders": "base_holders",
            "pools": "base_pools",
        }.get(expr.kind)
        if view is None:
            raise QueryError(f"Unknown source {expr.kind!r}.")
        type_name = "wallets" if expr.kind == "holders" else expr.kind
        sql_lines.append(f"-- {comment}\n-- reads {view}")
        return _Stream(view, type_name, field_map(type_name)), None

    if isinstance(expr, Detect):
        swaps_stream = _need_stream(env, expr.swaps)
        transfers_stream = _need_stream(env, expr.transfers)
        if swaps_stream.type_name != "swaps":
            raise QueryError(
                "detect_cycles wants a swaps stream that has not already been flagged. "
                "Connect the Swaps source, not the detector's own output."
            )
        if transfers_stream.type_name != "transfers":
            raise QueryError("detect_cycles wants a transfers stream as its second input.")
        detection = _run_detect(
            con,
            swaps_stream.view,
            transfers_stream.view,
            excluded,
            warnings,
            config=_detector_config(expr),
        )
        bundle = _register_detection(con, swaps_stream.view, detection, fresh)
        sql_lines.append(
            f"-- {comment}\n-- heuristic round-trip / cycle detector "
            f"(Gan et al. 2024 style) on {swaps_stream.view} and {transfers_stream.view}"
        )
        return bundle, detection

    if isinstance(expr, Project):
        bundle = env.get(expr.name)
        if not isinstance(bundle, _Bundle):
            raise QueryError(
                f"{expr.name} is not a detector result. "
                "project is only valid on detect_cycles(...)."
            )
        chosen = {"swaps": bundle.swaps, "wallets": bundle.wallets, "cycles": bundle.cycles}.get(
            expr.kind
        )
        if chosen is None:
            raise QueryError("project kind must be swaps, wallets, or cycles.")
        sql_lines.append(f"-- {comment}\n-- {chosen.view}")
        return chosen, None

    if isinstance(expr, Filter):
        stream = _need_stream(env, expr.name)
        where = " AND ".join(_compile_condition(cond, stream.fields) for cond in expr.conditions)
        view = fresh()
        sql = f"CREATE VIEW {view} AS SELECT * FROM {stream.view} WHERE {where}"
        _run(con, sql)
        sql_lines.append(f"-- {comment}\n{sql}")
        return _Stream(view, stream.type_name, dict(stream.fields)), None

    if isinstance(expr, TimeWindow):
        stream = _need_stream(env, expr.name)
        if stream.type_name not in {"swaps", "swaps_flagged", "transfers"}:
            raise QueryError(
                "A time window applies to swaps or transfers, which have block timestamps. "
                f"{expr.name} is {stream.type_name}."
            )
        start, finish = _window_bounds(expr, end_ts, window_start, window_end)
        view = fresh()
        sql = (
            f"CREATE VIEW {view} AS SELECT * FROM {stream.view} "
            f"WHERE block_timestamp >= TIMESTAMP '{start:%Y-%m-%d %H:%M:%S}' "
            f"AND block_timestamp <= TIMESTAMP '{finish:%Y-%m-%d %H:%M:%S}'"
        )
        _run(con, sql)
        sql_lines.append(f"-- {comment}\n{sql}")
        return _Stream(view, stream.type_name, dict(stream.fields)), None

    if isinstance(expr, GroupBy):
        stream = _need_stream(env, expr.name)
        view = fresh()
        sql, fields = _group_sql(stream, expr.key, view)
        _run(con, sql)
        sql_lines.append(f"-- {comment}\n{sql}")
        return _Stream(view, "grouped", fields), None

    if isinstance(expr, Aggregate):
        stream = _need_stream(env, expr.name)
        view = fresh()
        sql = _aggregate_sql(stream, expr, view)
        _run(con, sql)
        sql_lines.append(f"-- {comment}\n{sql}")
        return _Stream(view, "scalar", field_map("scalar")), None

    if isinstance(expr, Ratio):
        part = _need_stream(env, expr.part)
        whole = _need_stream(env, expr.whole)
        part_column = _numeric(expr.field, part.fields, expr.part)
        whole_name = expr.whole_field or expr.field
        whole_column = _numeric(whole_name, whole.fields, expr.whole)
        view = fresh()
        sql = (
            f"CREATE VIEW {view} AS SELECT CASE "
            f"WHEN (SELECT COALESCE(SUM({whole_column}), 0) FROM {whole.view}) = 0 THEN 0 "
            f"ELSE 100.0 * (SELECT COALESCE(SUM({part_column}), 0) FROM {part.view}) "
            f"/ (SELECT COALESCE(SUM({whole_column}), 0) FROM {whole.view}) END AS value"
        )
        _run(con, sql)
        sql_lines.append(f"-- {comment}\n{sql}")
        note = None
        if (
            expr.field == "ml_weighted_usd"
            and part.view == whole.view
            and "ml_score" in part.fields
            and "usd_value" in part.fields
        ):
            note = _bootstrap_note(con, part.view)
        return _Stream(view, "scalar", field_map("scalar"), note=note), None

    if isinstance(expr, Sort):
        stream = _need_stream(env, expr.name)
        if expr.field not in stream.fields:
            raise QueryError(
                f"Cannot sort {expr.name} by {expr.field!r}. "
                f"Fields: {', '.join(stream.fields)}."
            )
        direction = "ASC" if expr.direction == "asc" else "DESC"
        column = _ident(expr.field)
        view = fresh()
        sql = f"CREATE VIEW {view} AS SELECT * FROM {stream.view} ORDER BY {column} {direction} NULLS LAST"
        _run(con, sql)
        sql_lines.append(f"-- {comment}\n{sql}")
        return _Stream(view, stream.type_name, dict(stream.fields)), None

    if isinstance(expr, Limit):
        stream = _need_stream(env, expr.name)
        if stream.type_name == "scalar":
            raise QueryError("limit applies to a table, not a single number.")
        if not isinstance(expr.n, int) or not 1 <= expr.n <= _MAX_ROWS:
            raise QueryError(f"limit must be a whole number from 1 to {_MAX_ROWS}.")
        view = fresh()
        sql = f"CREATE VIEW {view} AS SELECT * FROM {stream.view} LIMIT {expr.n}"
        _run(con, sql)
        sql_lines.append(f"-- {comment}\n{sql}")
        return _Stream(view, stream.type_name, dict(stream.fields)), None

    if isinstance(expr, MlScore):
        return _score_stream(
            expr.name,
            "ml",
            env=env,
            con=con,
            excluded=excluded,
            fresh=fresh,
            sql_lines=sql_lines,
            comment=comment,
            cache=cache,
            base_swaps=base_swaps,
            base_transfers=base_transfers,
            model_dir=model_dir,
            kept=kept,
        ), None

    if isinstance(expr, AnomalyScore):
        return _score_stream(
            expr.name,
            "anomaly",
            env=env,
            con=con,
            excluded=excluded,
            fresh=fresh,
            sql_lines=sql_lines,
            comment=comment,
            cache=cache,
            base_swaps=base_swaps,
            base_transfers=base_transfers,
            model_dir=model_dir,
            kept=kept,
        ), None

    if isinstance(expr, Explain):
        return _explain_stream(
            expr.name,
            env=env,
            con=con,
            excluded=excluded,
            warnings=warnings,
            fresh=fresh,
            sql_lines=sql_lines,
            comment=comment,
            cache=cache,
            base_swaps=base_swaps,
            base_transfers=base_transfers,
            model_dir=model_dir,
            kept=kept,
        ), None
    raise QueryError(f"Cannot run expression {type(expr).__name__}.")


def _load_scores(
    cache: ScoreCache,
    base_swaps: pd.DataFrame,
    base_transfers: pd.DataFrame,
    excluded: set[str],
    model_dir: str | None,
) -> None:
    from washdetect.models import ModelNotTrained

    try:
        cache.ensure(base_swaps, base_transfers, excluded, model_dir)
    except ModelNotTrained as exc:
        raise QueryError(str(exc)) from exc


def _score_stream(
    name: str,
    mode: str,
    *,
    env: dict[str, _Stream | _Bundle],
    con: duckdb.DuckDBPyConnection,
    excluded: set[str],
    fresh: Any,
    sql_lines: list[str],
    comment: str,
    cache: ScoreCache,
    base_swaps: pd.DataFrame,
    base_transfers: pd.DataFrame,
    model_dir: str | None,
    kept: list[pd.DataFrame],
) -> _Stream:
    stream = _need_stream(env, name)
    if stream.type_name not in _SCOREABLE:
        raise QueryError(
            f"ML scores apply to swaps or wallets. {name} is {stream.type_name}."
        )
    _load_scores(cache, base_swaps, base_transfers, excluded, model_dir)
    frame = con.execute(f"SELECT * FROM {stream.view}").df()
    try:
        scored = score_rows(frame, cache, mode)
    except ValueError as exc:
        raise QueryError(str(exc)) from exc
    kept.append(scored)
    view = fresh()
    registered = f"scored_{view}"
    con.register(registered, scored)
    sql = f"CREATE VIEW {view} AS SELECT * FROM {registered}"
    _run(con, sql)
    sql_lines.append(f"-- {comment}\n{sql}")
    fields = dict(stream.fields)
    if mode == "ml":
        fields["ml_score"] = "number"
        if "usd_value" in scored.columns:
            fields["ml_weighted_usd"] = "number"
    else:
        fields["anomaly_score"] = "number"
    return _Stream(view, stream.type_name, fields)


def _explain_stream(
    name: str,
    *,
    env: dict[str, _Stream | _Bundle],
    con: duckdb.DuckDBPyConnection,
    excluded: set[str],
    warnings: list[str],
    fresh: Any,
    sql_lines: list[str],
    comment: str,
    cache: ScoreCache,
    base_swaps: pd.DataFrame,
    base_transfers: pd.DataFrame,
    model_dir: str | None,
    kept: list[pd.DataFrame],
) -> _Stream:
    stream = _need_stream(env, name)
    if stream.type_name not in {"wallets", "wash_wallets"}:
        raise QueryError(
            f"Explain reads wallets. {name} is {stream.type_name}. "
            "Connect a Wallets source or the detector's wallets output."
        )
    if "wallet" not in stream.fields:
        raise QueryError(f"{name} has no wallet column to explain.")
    _load_scores(cache, base_swaps, base_transfers, excluded, model_dir)
    frame = con.execute(f"SELECT wallet FROM {stream.view}").df()
    wallets = [str(wallet) for wallet in frame["wallet"].tolist() if wallet]
    from washdetect.models import ModelNotTrained

    try:
        reasons, truncated = explain_wallets(wallets, cache)
    except ModelNotTrained as exc:
        raise QueryError(str(exc)) from exc
    if truncated:
        warnings.append(
            "Explain is capped at 25 wallets. Sort and limit the stream to choose which ones."
        )
    explained = {str(wallet).lower() for wallet in reasons["wallet"].tolist()} if not reasons.empty else set()
    chosen: list[str] = []
    seen: set[str] = set()
    for wallet in wallets:
        key = wallet.lower()
        if key and key not in seen:
            seen.add(key)
            chosen.append(key)
    asked = set(chosen[:25])
    missing = len(asked - explained)
    if missing > 0:
        warnings.append(
            f"{missing} wallets have no swaps in this window, so there is nothing to explain."
        )
    kept.append(reasons)
    view = fresh()
    registered = f"explain_{view}"
    con.register(registered, reasons)
    sql = f"CREATE VIEW {view} AS SELECT * FROM {registered}"
    _run(con, sql)
    sql_lines.append(f"-- {comment}\n{sql}")
    return _Stream(view, "explanations", field_map("explanations"))


def _bootstrap_note(con: duckdb.DuckDBPyConnection, view: str) -> str:
    from washdetect.models import probability_weighted_wash_percent

    frame = con.execute(f"SELECT ml_score, usd_value FROM {view}").df()
    stats = probability_weighted_wash_percent(
        frame["usd_value"].tolist(),
        frame["ml_score"].tolist(),
        n_bootstrap=400,
        seed=0,
    )
    if int(stats["n"]) == 0:
        return "No priced swaps to resample, so the bootstrap interval is empty."
    return (
        f"95% bootstrap interval {float(stats['ci_low']):.2f}%–{float(stats['ci_high']):.2f}% "
        f"on {int(stats['n'])} priced swaps. "
        "The interval resamples this window; it is not a guarantee about other tokens."
    )


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


def _group_sql(stream: _Stream, key: str, view: str) -> tuple[str, dict[str, str]]:
    if key == "day":
        if "block_timestamp" not in _view_expectation(stream):
            raise QueryError(
                f"Group by day needs swaps or transfers. {stream.type_name} has no timestamp."
            )
        fields = {"day": "string", "count": "number", "sum_usd_value": "number"}
        wash = ""
        if "is_wash" in stream.fields and "usd_value" in stream.fields:
            wash = (
                ", COALESCE(SUM(CASE WHEN is_wash THEN usd_value ELSE 0 END), 0) AS sum_wash_usd"
            )
            fields["sum_wash_usd"] = "number"
        value = (
            "COALESCE(SUM(usd_value), 0) AS sum_usd_value"
            if "usd_value" in stream.fields
            else "0 AS sum_usd_value"
        )
        sql = (
            f"CREATE VIEW {view} AS SELECT "
            f"strftime(CAST(block_timestamp AS TIMESTAMP), '%Y-%m-%d') AS day, "
            f"COUNT(*) AS count, {value}{wash} "
            f"FROM {stream.view} GROUP BY 1 ORDER BY 1"
        )
        return sql, fields
    if key == "wallet":
        if "trader" in stream.fields:
            column = "trader"
        elif "wallet" in stream.fields:
            column = "wallet"
        else:
            raise QueryError(f"Cannot group {stream.type_name} by wallet.")
        fields = {"wallet": "string", "count": "number"}
        extra = ""
        if "usd_value" in stream.fields:
            extra = ", COALESCE(SUM(usd_value), 0) AS sum_usd_value"
            fields["sum_usd_value"] = "number"
        elif "wash_volume_usd" in stream.fields:
            extra = ", COALESCE(SUM(wash_volume_usd), 0) AS sum_usd_value"
            fields["sum_usd_value"] = "number"
        sql = (
            f"CREATE VIEW {view} AS SELECT {column} AS wallet, COUNT(*) AS count{extra} "
            f"FROM {stream.view} GROUP BY 1 ORDER BY count DESC"
        )
        return sql, fields
    if key == "pool":
        if "pool_address" not in stream.fields:
            raise QueryError(f"Cannot group {stream.type_name} by pool.")
        fields = {"pool_address": "string", "count": "number", "sum_usd_value": "number"}
        value = (
            "COALESCE(SUM(usd_value), 0)" if "usd_value" in stream.fields else "0"
        )
        sql = (
            f"CREATE VIEW {view} AS SELECT pool_address, COUNT(*) AS count, "
            f"{value} AS sum_usd_value FROM {stream.view} GROUP BY 1 ORDER BY sum_usd_value DESC"
        )
        return sql, fields
    raise QueryError("group by must be day, wallet, or pool.")


def _view_expectation(stream: _Stream) -> set[str]:
    if stream.type_name in {"swaps", "swaps_flagged", "transfers"}:
        return set(stream.fields) | {"block_timestamp"}
    return set(stream.fields)


def _aggregate_sql(stream: _Stream, expr: Aggregate, view: str) -> str:
    if expr.fn == "count":
        return f"CREATE VIEW {view} AS SELECT COUNT(*)::DOUBLE AS value FROM {stream.view}"
    column = _numeric(expr.field or "", stream.fields, expr.name)
    if expr.fn == "sum":
        return f"CREATE VIEW {view} AS SELECT COALESCE(SUM({column}), 0) AS value FROM {stream.view}"
    if expr.fn == "avg":
        return f"CREATE VIEW {view} AS SELECT COALESCE(AVG({column}), 0) AS value FROM {stream.view}"
    raise QueryError(f"Unknown aggregate {expr.fn!r}. Use count, sum, or avg.")


def _compile_condition(cond: Condition, fields: dict[str, str]) -> str:
    if cond.kind == "sold_in":
        if "days_since_sell" not in fields:
            raise QueryError(
                "sold_in() only applies to wallets or holders. "
                "It asks whether a wallet sold the token in the loaded window."
            )
        days = _duration_days(cond.amount or 0, cond.unit)
        if cond.negate:
            return f"(days_since_sell IS NULL OR days_since_sell > {sql_number(days)})"
        return f"(days_since_sell IS NOT NULL AND days_since_sell <= {sql_number(days)})"
    if cond.field not in fields:
        raise QueryError(
            f"Field {cond.field!r} is not on this stream. Available fields: {', '.join(fields)}."
        )
    kind = fields[cond.field]
    allowed = _NUMBER_OPS if kind == "number" else _EQ_OPS
    if cond.op not in allowed:
        raise QueryError(
            f"Cannot use {cond.op} on {cond.field} ({kind}). Use one of: {', '.join(sorted(allowed))}."
        )
    column = _ident(cond.field or "")
    return f"{column} {cond.op} {sql_literal(cond.value, kind)}"


def _window_bounds(
    expr: TimeWindow,
    end_ts: datetime,
    window_start: datetime,
    window_end: datetime,
) -> tuple[datetime, datetime]:
    if expr.days is not None:
        days = _duration_days(expr.days, expr.unit)
        if days <= 0 or days > MAX_WINDOW_DAYS:
            raise QueryError(f"A time window must be between 1 hour and {MAX_WINDOW_DAYS} days.")
        start = end_ts - timedelta(days=days)
        finish = end_ts
    else:
        try:
            start = datetime.fromisoformat(expr.start or "")
            finish = datetime.fromisoformat(expr.end or "")
        except ValueError as exc:
            raise QueryError("Time window dates must look like 2026-01-31.") from exc
        if len(expr.end or "") == 10:
            finish = finish + timedelta(days=1) - timedelta(seconds=1)
    if start.tzinfo or finish.tzinfo:
        start = start.replace(tzinfo=None)
        finish = finish.replace(tzinfo=None)
    slack = timedelta(hours=2)
    if start < window_start - slack or finish > window_end + slack:
        raise QueryError(
            "This time window sits outside the loaded range "
            f"({window_start:%Y-%m-%d} to {window_end:%Y-%m-%d}). "
            "Load a wider range, then run the graph again."
        )
    return start, finish


def _duration_days(amount: float, unit: str) -> float:
    if unit == "h":
        return float(amount) / 24.0
    if unit == "d":
        return float(amount)
    raise QueryError("Durations are days (d) or hours (h), for example 30d.")


def _check_output(stmt: Output, stream: _Stream) -> None:
    if stmt.kind == "number" and stream.type_name != "scalar":
        raise QueryError(
            f"A number card needs a single value (count, sum, avg, or percent-of). "
            f"{stmt.name} is a {stream.type_name} table."
        )
    if stmt.kind == "chart" and stream.type_name != "grouped":
        raise QueryError(
            "A chart reads a Group by box. Put Group by day in front of it to plot volume over time."
        )
    if stmt.kind == "table" and stream.type_name == "scalar":
        raise QueryError("That stream is a single number. Use a number card, not a table.")
    if stmt.kind not in {"number", "table", "chart"}:
        raise QueryError(f"Unknown output {stmt.kind!r}.")


def _read_output(con: duckdb.DuckDBPyConnection, stream: _Stream, stmt: Output) -> OutputResult:
    cursor = con.execute(f"SELECT * FROM {stream.view}")
    columns = [desc[0] for desc in cursor.description]
    raw = cursor.fetchall()
    truncated = len(raw) > _MAX_ROWS
    rows = [_json_row(columns, row) for row in raw[:_MAX_ROWS]]
    return OutputResult(
        kind=stmt.kind,
        title=stmt.title or stmt.kind,
        format=stmt.format or "number",
        columns=columns,
        rows=rows,
        truncated=truncated,
        note=stream.note,
    )


def _json_row(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in zip(columns, row, strict=False):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            out[name] = None
        elif isinstance(value, (datetime, pd.Timestamp)):
            out[name] = pd.Timestamp(value).isoformat()
        elif hasattr(value, "item"):
            out[name] = value.item()
        else:
            out[name] = value
    return out


def _need_stream(env: dict[str, _Stream | _Bundle], name: str) -> _Stream:
    value = env.get(name)
    if isinstance(value, _Bundle):
        raise QueryError(
            f"{name} is a detector result with three outputs. "
            "Project it first: `project {name} swaps`, `wallets`, or `cycles`."
        )
    if not isinstance(value, _Stream):
        raise QueryError(
            f"Unknown name {name!r}. Names are assigned with `name = ...` above the line that uses them."
        )
    return value


def _numeric(name: str, fields: dict[str, str], owner: str) -> str:
    if name not in fields:
        raise QueryError(
            f"Field {name!r} is not on {owner}. Available fields: {', '.join(fields)}."
        )
    if fields[name] != "number":
        raise QueryError(f"{name} is {fields[name]}, not a number, so it cannot be summed.")
    return _ident(name)


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise QueryError(f"Invalid field name {name!r}.")
    return name


def _safe_name(name: str) -> str:
    if not _IDENT.match(name):
        raise QueryError(
            f"Invalid name {name!r}. Use lowercase letters, digits, and underscores, starting with a letter."
        )
    return name


def sql_number(value: float) -> str:
    if not math.isfinite(value):
        raise QueryError("Numbers must be finite.")
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def sql_literal(value: object, kind: str) -> str:
    """A SQL literal we built. Strings are quoted; they are never spliced raw."""
    if kind == "bool":
        if value is True or value == "true":
            return "TRUE"
        if value is False or value == "false":
            return "FALSE"
        raise QueryError(f"Expected true or false, got {value!r}.")
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise QueryError(f"Expected a number, got {value!r}.")
        return sql_number(float(value))
    if kind == "string":
        text = str(value)
        if len(text) > 200:
            raise QueryError("Filter text is limited to 200 characters.")
        return "'" + text.replace("'", "''") + "'"
    raise QueryError(f"Cannot compare a {kind} value.")


def _ensure(frame: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = None
    return out


def _run(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    # Our SQL is a single statement. Reject anything that grew a second one
    # outside of a string literal — a cheap extra belt on top of the quoting.
    if _suspicious(sql):
        raise QueryError("Refusing to run a statement the compiler did not expect.")
    con.execute(sql)


def _suspicious(sql: str) -> bool:
    stripped = _strip_strings(sql).lower()
    banned = ("drop ", "delete ", "insert ", "update ", "alter ", "attach ", "copy ", "pragma ")
    return any(word in stripped for word in banned)


def _strip_strings(sql: str) -> str:
    """Hide string literals and line comments so a keyword scan sees code only.

    A wallet address is allowed to contain the letters "drop". Doubled quotes
    stay inside the literal, and `--` comments (the compiled query text) are
    not statements.
    """
    out: list[str] = []
    index = 0
    length = len(sql)
    while index < length:
        if sql[index] == "'":
            out.append("''")
            index += 1
            while index < length:
                if sql.startswith("''", index):
                    index += 2
                    continue
                if sql[index] == "'":
                    index += 1
                    break
                index += 1
            continue
        if sql.startswith("--", index):
            while index < length and sql[index] != "\n":
                index += 1
            continue
        out.append(sql[index])
        index += 1
    return "".join(out)


# Touch TYPES so a missing catalog entry fails at import of a stream builder.
_ = TYPES

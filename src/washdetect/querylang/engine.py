from __future__ import annotations

from datetime import datetime
from typing import Any

import duckdb
import pandas as pd

from washdetect.config import ROUTERS
from washdetect.detector import Detection
from washdetect.features import WALLET_COLUMNS, build_wallet_table
from washdetect.graph.catalog import TYPES, field_map
from washdetect.prices import pricing_note
from washdetect.querylang.ast import (
    Aggregate,
    AnomalyScore,
    Assign,
    Detect,
    Explain,
    Filter,
    GroupBy,
    Insight,
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
from washdetect.querylang.detect_run import _detector_config, _register_detection, _run_detect
from washdetect.querylang.errors import QueryError
from washdetect.querylang.parser import parse
from washdetect.querylang.results import ExecResult, OutputResult, _Bundle, _Stream
from washdetect.querylang.score_run import _bootstrap_note, _explain_stream, _score_stream
from washdetect.querylang.sqlbuild import (
    _aggregate_sql,
    _check_output,
    _compile_condition,
    _group_sql,
    _need_stream,
    _numeric,
    _read_output,
    _window_bounds,
)
from washdetect.querylang.sqlutil import (
    _MAX_ROWS,
    _POOL_COLUMNS,
    _SWAP_COLUMNS,
    _TRANSFER_COLUMNS,
    _ensure,
    _ident,
    _run,
    _safe_name,
    _strip_strings,
)
from washdetect.scoring import ScoreCache
from washdetect.signals import WindowSignals

# SQL is built here, never pasted from the query. Only CREATE VIEW and SELECT
# run, on a throwaway connection, so a query cannot touch the cache.
__all__ = ["ExecResult", "OutputResult", "QueryEngine", "_strip_strings", "execute_frames"]

_ = TYPES  # fail at import if a catalog stream is missing, not mid-query


def execute_frames(
    program: Program,
    *,
    swaps: pd.DataFrame | None = None,
    transfers: pd.DataFrame | None = None,
    wallets: pd.DataFrame | None = None,
    pools: pd.DataFrame | None = None,
    pool_addresses: set[str] | None = None,
    liquidity: pd.DataFrame | None = None,
    creator: str = "",
    creator_status: str = "",
    end_ts: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    model_dir: str | None = None,
) -> ExecResult:
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
    signals = WindowSignals(
        swaps_df,
        transfers_df,
        wallets_df,
        liquidity,
        pools=pool_addresses,
        creator=creator,
        creator_status=creator_status,
    )
    for frame in (swaps_df, transfers_df):
        if "block_timestamp" in frame.columns:
            frame["block_timestamp"] = pd.to_datetime(frame["block_timestamp"])

    con = duckdb.connect()
    # DuckDB may read these frames lazily, so they have to stay referenced.
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
          AND COALESCE(holder_kind, 'wallet') = 'wallet'
          AND lower(wallet) NOT IN (
            '0x0000000000000000000000000000000000000000',
            '0x000000000000000000000000000000000000dead'
          )
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
        sources: list[str | None] = []
        source_col = (
            swaps_df["price_source"].tolist()
            if "price_source" in swaps_df.columns
            else [None] * len(swaps_df)
        )
        for usd, source in zip(swaps_df["usd_value"].tolist(), source_col, strict=True):
            if usd is None or (isinstance(usd, float) and usd != usd):
                sources.append(None)
            else:
                sources.append(str(source) if isinstance(source, str) and source else "stable")
        note = pricing_note(len(sources), sources)
        if note:
            warnings.append(note)
    env: dict[str, _Stream | _Bundle] = {}
    sql_lines: list[str] = []
    outputs: list[OutputResult] = []
    detection: Detection | None = None
    counter = 0
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
                signals=signals,
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


class QueryEngine:

    def compile(self, text: str) -> Program:
        return parse(text)

    def run(
        self,
        program: Program,
        *,
        swaps: pd.DataFrame | None = None,
        transfers: pd.DataFrame | None = None,
        wallets: pd.DataFrame | None = None,
        pools: pd.DataFrame | None = None,
        pool_addresses: set[str] | None = None,
        liquidity: pd.DataFrame | None = None,
        creator: str = "",
        creator_status: str = "",
        end_ts: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        model_dir: str | None = None,
    ) -> ExecResult:
        return execute_frames(
            program,
            swaps=swaps,
            transfers=transfers,
            wallets=wallets,
            pools=pools,
            pool_addresses=pool_addresses,
            liquidity=liquidity,
            creator=creator,
            creator_status=creator_status,
            end_ts=end_ts,
            window_start=window_start,
            window_end=window_end,
            model_dir=model_dir,
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
    signals: WindowSignals,
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
                "find_wash_groups wants a swaps stream that has not already been flagged. "
                "Connect the Swaps source, not the detector's own output."
            )
        if transfers_stream.type_name != "transfers":
            raise QueryError("find_wash_groups wants a transfers stream as its second input.")
        detection = _run_detect(
            con,
            swaps_stream.view,
            transfers_stream.view,
            excluded,
            warnings,
            config=_detector_config(expr),
        )
        bundle = _register_detection(
            con,
            swaps_stream.view,
            detection,
            fresh,
            show_all=bool(getattr(expr, "show_all", False)),
            transfers_view=transfers_stream.view,
        )
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
                "project is only valid on find_wash_groups(...)."
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

    if isinstance(expr, Insight):
        settings: dict[str, object] = {}
        if expr.top_n is not None:
            settings["top_n"] = expr.top_n
        if expr.minutes is not None:
            settings["minutes"] = expr.minutes
        if expr.usd is not None:
            settings["usd"] = expr.usd
        if expr.percent is not None:
            settings["percent"] = expr.percent
        if expr.min_wallets is not None:
            settings["min_wallets"] = expr.min_wallets
        if expr.creator:
            settings["creator"] = expr.creator
        frame, note = signals.run(expr.kind, **settings)
        view = fresh()
        held = f"{view}_src"
        con.register(held, frame)
        kept.append(frame)
        sql = f"CREATE VIEW {view} AS SELECT * FROM {held}"
        _run(con, sql)
        sql_lines.append(f"-- {comment}\n{sql}")
        return _Stream(view, expr.kind, field_map(expr.kind), note=note), None

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

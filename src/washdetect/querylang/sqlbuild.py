from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import duckdb
import pandas as pd

from washdetect.config import MAX_WINDOW_DAYS
from washdetect.querylang.ast import Aggregate, Condition, Output, TimeWindow
from washdetect.querylang.errors import QueryError
from washdetect.querylang.results import OutputResult, _Bundle, _Stream
from washdetect.querylang.sqlutil import (
    _EQ_OPS,
    _MAX_ROWS,
    _NUMBER_OPS,
    _ident,
    sql_literal,
    sql_number,
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
        amount = ""
        if "amount_token" in stream.fields:
            amount = ", COALESCE(SUM(amount_token), 0) AS sum_amount_token"
            fields["sum_amount_token"] = "number"
        sql = (
            f"CREATE VIEW {view} AS SELECT "
            f"strftime(CAST(block_timestamp AS TIMESTAMP), '%Y-%m-%d') AS day, "
            f"COUNT(*) AS count, {value}{amount}{wash} "
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
        if "amount_token" in stream.fields:
            extra += ", COALESCE(SUM(amount_token), 0) AS sum_amount_token"
            fields["sum_amount_token"] = "number"
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
        amount = ""
        if "amount_token" in stream.fields:
            amount = ", COALESCE(SUM(amount_token), 0) AS sum_amount_token"
            fields["sum_amount_token"] = "number"
        sql = (
            f"CREATE VIEW {view} AS SELECT pool_address, COUNT(*) AS count, "
            f"{value} AS sum_usd_value{amount} FROM {stream.view} GROUP BY 1 ORDER BY sum_usd_value DESC"
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

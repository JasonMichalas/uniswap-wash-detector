from __future__ import annotations

import math
import re

import duckdb
import pandas as pd

from washdetect.querylang.errors import QueryError

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
    """Quoted here. Filter text is never spliced into the SQL raw."""
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
    # One statement. A second one that grew outside a string literal is refused.
    if _suspicious(sql):
        raise QueryError("Refusing to run a statement the compiler did not expect.")
    con.execute(sql)


def _suspicious(sql: str) -> bool:
    stripped = _strip_strings(sql).lower()
    banned = ("drop ", "delete ", "insert ", "update ", "alter ", "attach ", "copy ", "pragma ")
    return any(word in stripped for word in banned)


def _strip_strings(sql: str) -> str:
    """A wallet address may contain 'drop'. Literals and line comments are hidden from that scan."""
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

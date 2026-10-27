from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd

from washdetect.graph.catalog import field_map
from washdetect.querylang.errors import QueryError
from washdetect.querylang.results import _SCOREABLE, _Bundle, _Stream
from washdetect.querylang.sqlbuild import _need_stream
from washdetect.querylang.sqlutil import _run
from washdetect.scoring import ScoreCache, explain_wallets, score_rows


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

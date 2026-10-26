"""Parse query text into a Program.

Short queries desugar into the same assignments the node editor emits, so
there is one execution path. Parse errors name the line and offer an example.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from lark import Lark, Token, Tree, UnexpectedInput

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

_EXAMPLE = (
    "Examples: `holders where held_days > 90`, `wallets where not sold_in(30d)`, "
    "`wash_wallets where confidence > 0.8`, `cycles where size >= 3`."
)


@lru_cache(maxsize=1)
def _parser() -> Lark:
    try:
        grammar = files("washdetect.querylang").joinpath("grammar.lark").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, TypeError):
        grammar = Path(__file__).with_name("grammar.lark").read_text(encoding="utf-8")
    return Lark(
        grammar,
        parser="earley",
        lexer="dynamic",
        start="start",
        propagate_positions=True,
    )


def parse(text: str) -> Program:
    """Parse a short query or a program. Blank input is an error, not an empty plan."""
    if text is None or not str(text).strip():
        raise QueryError("The query is empty. " + _EXAMPLE)
    try:
        tree = _parser().parse(str(text))
    except UnexpectedInput as exc:
        context = " ".join(exc.get_context(str(text)).split())
        raise QueryError(
            f"Could not parse this query at line {exc.line}, column {exc.column}. "
            f"Near: {context!r}. {_EXAMPLE}"
        ) from exc
    try:
        return _build(tree)
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f"Could not understand this query. {_EXAMPLE}") from exc


def _build(tree: Tree) -> Program:
    if tree.data == "start":
        return _build(tree.children[0])
    if tree.data == "simple":
        return _desugar(*_simple(tree))
    if tree.data == "program":
        return Program([_statement(child) for child in tree.children])
    raise QueryError(f"Unexpected syntax node {tree.data}.")


def _statement(tree: Tree) -> Assign | Output:
    inner = tree.children[0]
    if inner.data == "assign":
        return Assign(str(inner.children[0]), _expr(inner.children[1].children[0]))
    if inner.data == "output":
        return _output(inner)
    raise QueryError(f"Unexpected statement {inner.data}.")


def _expr(tree: Tree):
    kind = tree.data
    if kind == "source":
        return Source(str(tree.children[0]))
    if kind == "detect":
        swaps = str(tree.children[0])
        transfers = str(tree.children[1])
        if len(tree.children) == 2:
            return Detect(swaps, transfers)
        if len(tree.children) != 5:
            raise QueryError(
                "detect_cycles takes swaps and transfers, optionally followed by "
                "tolerance, window blocks, and max group size."
            )
        tolerance = _number(tree.children[2])
        window = _int(tree.children[3], "detect_cycles window")
        group = _int(tree.children[4], "detect_cycles max group size")
        return Detect(swaps, transfers, net_ratio=float(tolerance), max_block_gap=window, max_group_size=group)
    if kind == "project":
        return Project(str(tree.children[0]), str(tree.children[1]))
    if kind == "filter":
        name = str(tree.children[0])
        conditions = [_condition(child) for child in tree.children[1:]]
        if not conditions:
            raise QueryError(f"filter {name} needs a where condition.")
        return Filter(name, conditions)
    if kind == "timewin":
        return _timewin(tree)
    if kind == "groupby":
        return GroupBy(str(tree.children[0]), str(tree.children[1]))
    if kind == "aggregate":
        return _aggregate(tree)
    if kind == "ratio":
        whole_field = str(tree.children[3]) if len(tree.children) > 3 else None
        return Ratio(str(tree.children[0]), str(tree.children[1]), str(tree.children[2]), whole_field)
    if kind == "sort":
        direction = str(tree.children[2]) if len(tree.children) > 2 else "desc"
        return Sort(str(tree.children[0]), str(tree.children[1]), direction)
    if kind == "limitstmt":
        return Limit(str(tree.children[0]), _int(tree.children[1], "limit"))
    if kind == "ml":
        return MlScore(str(tree.children[0]))
    if kind == "anomaly":
        return AnomalyScore(str(tree.children[0]))
    if kind == "explainstmt":
        return Explain(str(tree.children[0]))
    raise QueryError(f"Unknown expression {kind}.")


def _timewin(tree: Tree) -> TimeWindow:
    name = str(tree.children[0])
    rest = tree.children[1:]
    if len(rest) == 2 and isinstance(rest[0], Token) and rest[0].type == "NUMBER":
        return TimeWindow(name, days=_number(rest[0]), unit=str(rest[1]))
    if len(rest) == 2:
        return TimeWindow(name, start=_string(rest[0]), end=_string(rest[1]))
    raise QueryError(f"Could not read the time window on {name}.")


def _aggregate(tree: Tree) -> Aggregate:
    head = tree.children[0]
    fn = str(head)
    name = str(tree.children[1])
    if fn == "count":
        return Aggregate("count", name, None)
    if len(tree.children) < 3:
        raise QueryError(f"{fn} needs a field, for example `{fn} {name} of usd_value`.")
    return Aggregate(fn, name, str(tree.children[2]))


def _output(tree: Tree) -> Output:
    kind = str(tree.children[0])
    name = str(tree.children[1])
    title = None
    fmt = "number"
    for child in tree.children[2:]:
        if isinstance(child, Token) and child.type == "STRING":
            title = _string(child)
        elif isinstance(child, Token) and child.type == "OUT_FORMAT":
            fmt = str(child)
    return Output(kind, name, title, fmt)


def _condition(tree: Tree) -> Condition:
    if tree.children and isinstance(tree.children[0], Token) and tree.children[0].type == "NOT":
        sold = tree.children[1]
        amount, unit = _sold(sold)
        return Condition("sold_in", negate=True, amount=amount, unit=unit)
    if tree.children and isinstance(tree.children[0], Tree) and tree.children[0].data == "sold_call":
        amount, unit = _sold(tree.children[0])
        return Condition("sold_in", negate=False, amount=amount, unit=unit)
    field = str(tree.children[0])
    op = str(tree.children[1])
    value = _value(tree.children[2])
    return Condition("compare", field=field, op=op, value=value)


def _sold(tree: Tree) -> tuple[float, str]:
    return _number(tree.children[0]), str(tree.children[1])


def _value(tree: Tree) -> object:
    token = tree.children[0]
    if token.type == "NUMBER":
        return _number(token)
    if token.type == "STRING":
        return _string(token)
    if token.type == "BOOL":
        return str(token) == "true"
    raise QueryError(f"Unsupported value {token!r}.")


def _simple(tree: Tree) -> tuple[str, list[Condition], int | None]:
    head = str(tree.children[0])
    conditions: list[Condition] = []
    limit = None
    for child in tree.children[1:]:
        if not isinstance(child, Tree):
            continue
        if child.data == "where_clause":
            conditions = [_condition(cond) for cond in child.children]
        elif child.data == "limit_clause":
            limit = _int(child.children[0], "limit")
    return head, conditions, limit


def _desugar(head: str, conditions: list[Condition], limit: int | None) -> Program:
    if head == "ml_wash_percent":
        if conditions or limit is not None:
            raise QueryError(
                "ml_wash_percent stands on its own. Filter and sort in the node editor "
                "if you want a slice of the scored swaps."
            )
        return Program(
            [
                Assign("s", Source("swaps")),
                Assign("m", MlScore("s")),
                Assign("p", Ratio("m", "m", "ml_weighted_usd", "usd_value")),
                Output("number", "p", "ML wash volume", "percent"),
            ]
        )
    if head == "wash_percent":
        if conditions or limit is not None:
            raise QueryError(
                "wash_percent stands on its own. To slice the data, filter in front of a ratio "
                "in the node editor instead of adding where or limit."
            )
        return Program(
            [
                Assign("s", Source("swaps")),
                Assign("t", Source("transfers")),
                Assign("d", Detect("s", "t")),
                Assign("f", Project("d", "swaps")),
                Assign("w", Filter("f", [Condition("compare", field="is_wash", op="=", value=True)])),
                Assign("p", Ratio("w", "f", "usd_value")),
                Output("number", "p", "Wash volume", "percent"),
            ]
        )
    if head == "wash_wallets":
        return _chain(
            [
                Assign("s", Source("swaps")),
                Assign("t", Source("transfers")),
                Assign("d", Detect("s", "t")),
                Assign("w", Project("d", "wallets")),
            ],
            "w",
            conditions,
            limit,
            "Wash wallets",
        )
    if head == "top_holders":
        statements: list[Assign | Output] = [Assign("h", Source("holders"))]
        current = "h"
        if conditions:
            statements.append(Assign("f", Filter(current, conditions)))
            current = "f"
        statements.append(Assign("s", Sort(current, "balance", "desc")))
        statements.append(Assign("l", Limit("s", limit if limit is not None else 20)))
        statements.append(Output("table", "l", "Top holders", "number"))
        return Program(statements)
    if head == "cycles":
        return _chain(
            [
                Assign("s", Source("swaps")),
                Assign("t", Source("transfers")),
                Assign("d", Detect("s", "t")),
                Assign("c", Project("d", "cycles")),
            ],
            "c",
            conditions,
            limit,
            "Cycles",
        )
    if head in {"holders", "wallets", "swaps", "transfers", "pools"}:
        return _chain([Assign("x", Source(head))], "x", conditions, limit, head.replace("_", " "))
    raise QueryError(f"Unknown query {head!r}. {_EXAMPLE}")


def _chain(
    statements: list[Assign | Output],
    current: str,
    conditions: list[Condition],
    limit: int | None,
    title: str,
) -> Program:
    if conditions:
        statements.append(Assign("f", Filter(current, conditions)))
        current = "f"
    if limit is not None:
        statements.append(Assign("l", Limit(current, limit)))
        current = "l"
    statements.append(Output("table", current, title, "number"))
    return Program(statements)


def _number(token: Token) -> float:
    text = str(token)
    return float(text) if "." in text else int(text)


def _int(token: Token, what: str) -> int:
    number = _number(token)
    if isinstance(number, float) or number < 0:
        raise QueryError(f"{what} expects a whole number, not {token!r}.")
    return int(number)


def _string(token: Token) -> str:
    try:
        value = json.loads(str(token))
    except json.JSONDecodeError as exc:
        raise QueryError(f"Could not read the text {token!r}.") from exc
    if not isinstance(value, str):
        raise QueryError(f"Expected text, got {token!r}.")
    return value

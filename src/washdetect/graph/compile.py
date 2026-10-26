"""Lower a validated graph to a query-language program.

Each box becomes one or more assignments. The detector's three sockets
become `project <id> swaps|wallets|cycles`, which is what the text under
the graph shows. Invalid graphs raise GraphCompileError carrying the same
messages as `validate_graph`.
"""

from __future__ import annotations

import re
from typing import Any

from washdetect.graph.catalog import NODE_BY_TYPE
from washdetect.graph.validate import GraphErrorItem, validate_graph
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

_IDENT = re.compile(r"[^a-z0-9_]+")


class GraphCompileError(QueryError):
    def __init__(self, errors: list[GraphErrorItem]) -> None:
        self.errors = errors
        super().__init__("; ".join(item.message for item in errors))


def compile_graph(graph: dict[str, Any]) -> tuple[Program, str]:
    errors = validate_graph(graph)
    if errors:
        raise GraphCompileError(errors)
    program = _lower(graph)
    return program, program.to_text()


def _lower(graph: dict[str, Any]) -> Program:
    nodes = {node["id"]: node for node in graph["nodes"]}
    incoming: dict[tuple[str, str], Any] = {}
    for edge in graph["edges"]:
        incoming[(edge["target"], edge.get("targetHandle") or "in")] = edge

    order = _topo(nodes, graph["edges"])
    used: set[str] = set()
    names: dict[tuple[str, str], str] = {}
    statements: list[Assign | Output] = []

    def uname(node_id: str, suffix: str = "") -> str:
        base = _IDENT.sub("_", node_id.lower()).strip("_")
        if not base or base[0].isdigit():
            base = "n_" + base
        name = "n_" + base
        if suffix:
            name += "_" + suffix
        candidate = name[:48]
        original = candidate
        i = 2
        while candidate in used:
            candidate = f"{original[:40]}_{i}"
            i += 1
        used.add(candidate)
        return candidate

    def upstream(node_id: str, handle: str) -> str:
        edge = incoming[(node_id, handle)]
        return names[(edge["source"], edge.get("sourceHandle") or "out")]

    for node_id in order:
        node = nodes[node_id]
        ntype = node["type"]
        data = node.get("data") or {}
        if ntype == "source":
            name = uname(node_id)
            statements.append(Assign(name, Source(str(data["source"]))))
            names[(node_id, "out")] = name
        elif ntype == "detect_cycles":
            name = uname(node_id)
            statements.append(
                Assign(
                    name,
                    Detect(
                        upstream(node_id, "in_swaps"),
                        upstream(node_id, "in_transfers"),
                        net_ratio=_optional_float(data.get("tolerance")),
                        max_block_gap=_optional_int(data.get("window_blocks")),
                        max_group_size=_optional_int(data.get("max_group_size")),
                    ),
                )
            )
            for kind, handle in (("swaps", "out_swaps"), ("wallets", "out_wallets"), ("cycles", "out_cycles")):
                projected = uname(node_id, kind)
                statements.append(Assign(projected, Project(name, kind)))
                names[(node_id, handle)] = projected
        elif ntype == "condition":
            name = uname(node_id)
            statements.append(Assign(name, Filter(upstream(node_id, "in"), [_condition(data)])))
            names[(node_id, "out")] = name
        elif ntype == "time_window":
            name = uname(node_id)
            statements.append(
                Assign(name, TimeWindow(upstream(node_id, "in"), days=float(data["days"]), unit="d"))
            )
            names[(node_id, "out")] = name
        elif ntype == "group_by":
            name = uname(node_id)
            statements.append(Assign(name, GroupBy(upstream(node_id, "in"), str(data["by"]))))
            names[(node_id, "out")] = name
        elif ntype == "aggregate":
            name = uname(node_id)
            fn = str(data.get("fn") or "count")
            field = None if fn == "count" else str(data.get("field") or "")
            statements.append(Assign(name, Aggregate(fn, upstream(node_id, "in"), field)))
            names[(node_id, "out")] = name
        elif ntype == "ratio":
            name = uname(node_id)
            over = data.get("over")
            whole_field = str(over) if isinstance(over, str) and over.strip() else None
            statements.append(
                Assign(
                    name,
                    Ratio(
                        upstream(node_id, "in_part"),
                        upstream(node_id, "in_whole"),
                        str(data["field"]),
                        whole_field,
                    ),
                )
            )
            names[(node_id, "out")] = name
        elif ntype == "sort":
            name = uname(node_id)
            statements.append(
                Assign(
                    name,
                    Sort(
                        upstream(node_id, "in"),
                        str(data["field"]),
                        str(data.get("direction") or "desc"),
                    ),
                )
            )
            names[(node_id, "out")] = name
        elif ntype == "limit":
            name = uname(node_id)
            statements.append(Assign(name, Limit(upstream(node_id, "in"), int(data["n"]))))
            names[(node_id, "out")] = name
        elif ntype == "ml_score":
            name = uname(node_id)
            statements.append(Assign(name, MlScore(upstream(node_id, "in"))))
            names[(node_id, "out")] = name
        elif ntype == "anomaly_score":
            name = uname(node_id)
            statements.append(Assign(name, AnomalyScore(upstream(node_id, "in"))))
            names[(node_id, "out")] = name
        elif ntype == "explain":
            name = uname(node_id)
            statements.append(Assign(name, Explain(upstream(node_id, "in"))))
            names[(node_id, "out")] = name
        elif ntype in {"output_number", "output_table", "output_chart"}:
            kind = {"output_number": "number", "output_table": "table", "output_chart": "chart"}[ntype]
            fmt = str(data.get("format") or "number")
            title = data.get("title")
            if not isinstance(title, str) or not title.strip():
                title = NODE_BY_TYPE[ntype]["label"]
            statements.append(Output(kind, upstream(node_id, "in"), title.strip(), fmt))
        else:
            raise GraphCompileError([GraphErrorItem(f"Cannot lower box type {ntype}.", node_id)])
    return Program(statements)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _condition(data: dict[str, Any]) -> Condition:
    if data.get("mode") == "sold_in":
        return Condition(
            "sold_in",
            negate=bool(data.get("negate")),
            amount=float(data.get("days") or 0),
            unit="d",
        )
    value = data.get("value")
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        value = value.lower() == "true"
    return Condition("compare", field=str(data.get("field")), op=str(data.get("op")), value=value)


def _topo(nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    indeg = dict.fromkeys(nodes, 0)
    graph: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        src, dst = edge["source"], edge["target"]
        graph[src].append(dst)
        indeg[dst] += 1
    queue = [node_id for node_id, degree in indeg.items() if degree == 0]
    order: list[str] = []
    while queue:
        node_id = queue.pop(0)
        order.append(node_id)
        for nxt in graph[node_id]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return order

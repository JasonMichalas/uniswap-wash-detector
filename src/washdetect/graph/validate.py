from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from washdetect.graph.catalog import NODE_BY_TYPE, SOURCE_TYPES, can_connect, field_map

_MAX_NODES = 80
_GROUP_KEYS = {
    "swaps": {"day", "wallet", "pool"},
    "swaps_flagged": {"day", "wallet", "pool"},
    "transfers": {"day"},
    "wash_wallets": {"wallet"},
    "wallets": set(),
    "cycles": set(),
    "pools": {"pool"},
    "grouped": set(),
}


@dataclass
class GraphErrorItem:
    message: str
    node_id: str | None = None
    edge_id: str | None = None
    code: str = "invalid"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "code": self.code,
        }


def validate_graph(graph: dict[str, Any]) -> list[GraphErrorItem]:
    """A loop here is a wiring error. Wash cycles come from the detector, not from a box feeding itself."""
    errors: list[GraphErrorItem] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return [GraphErrorItem("A graph needs a nodes list and an edges list.", code="shape")]
    if len(nodes) > _MAX_NODES:
        errors.append(GraphErrorItem(f"Graphs are limited to {_MAX_NODES} boxes.", code="size"))

    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            errors.append(GraphErrorItem("A box is missing an id.", code="shape"))
            continue
        if node_id in by_id:
            errors.append(GraphErrorItem(f"Two boxes share the id {node_id!r}.", node_id, code="shape"))
            continue
        if node.get("type") not in NODE_BY_TYPE:
            errors.append(
                GraphErrorItem(
                    f"Unknown box type {node.get('type')!r}.",
                    node_id,
                    code="type",
                )
            )
            continue
        by_id[node_id] = node

    incoming: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        edge_id = str(edge.get("id") or "")
        src = str(edge.get("source") or "")
        dst = str(edge.get("target") or "")
        if src not in by_id or dst not in by_id:
            errors.append(
                GraphErrorItem(
                    "A wire points at a box that is not on the canvas.",
                    edge_id=edge_id or None,
                    code="edge",
                )
            )
            continue
        if src == dst:
            errors.append(
                GraphErrorItem(
                    "A box cannot connect to itself.",
                    node_id=src,
                    edge_id=edge_id or None,
                    code="cycle",
                )
            )
            continue
        src_handle = edge.get("sourceHandle") or "out"
        dst_handle = edge.get("targetHandle") or "in"
        src_def = NODE_BY_TYPE[by_id[src]["type"]]
        dst_def = NODE_BY_TYPE[by_id[dst]["type"]]
        src_spec = _handle(src_def.get("outputs") or [], src_handle)
        dst_spec = _handle(dst_def.get("inputs") or [], dst_handle)
        if src_spec is None:
            errors.append(
                GraphErrorItem(
                    f"{_label(by_id[src])} has no output socket {src_handle!r}.",
                    src,
                    edge_id or None,
                    "socket",
                )
            )
            continue
        if dst_spec is None:
            errors.append(
                GraphErrorItem(
                    f"{_label(by_id[dst])} has no input socket {dst_handle!r}.",
                    dst,
                    edge_id or None,
                    "socket",
                )
            )
            continue
        incoming[(dst, dst_handle)].append(edge)
        outgoing[src].append(edge)

    if _has_cycle(by_id, edges):
        errors.append(
            GraphErrorItem(
                "This graph has a loop. Data-flow graphs have to be acyclic. "
                "Wash-trading cycles are found by the Find wash groups box, not by wiring a box back into itself.",
                code="cycle",
            )
        )
        return errors

    order = _topo(by_id, edges)
    out_types: dict[tuple[str, str], str] = {}
    out_fields: dict[tuple[str, str], dict[str, str]] = {}

    for node_id in order:
        node = by_id[node_id]
        spec = NODE_BY_TYPE[node["type"]]
        data = node.get("data") or {}
        label = _label(node)

        for inp in spec.get("inputs") or []:
            wires = incoming.get((node_id, inp["id"]), [])
            if len(wires) == 0:
                errors.append(
                    GraphErrorItem(
                        f"{label} is missing a wire into '{inp['label']}'.",
                        node_id,
                        code="missing",
                    )
                )
            elif len(wires) > 1:
                errors.append(
                    GraphErrorItem(
                        f"{label} has {len(wires)} wires into '{inp['label']}'. Use one.",
                        node_id,
                        code="missing",
                    )
                )
            else:
                wire = wires[0]
                src_id = wire["source"]
                src_handle = wire.get("sourceHandle") or "out"
                src_type = out_types.get((src_id, src_handle))
                if src_type is None:
                    continue
                if not can_connect(src_type, inp["type"]):
                    errors.append(
                        GraphErrorItem(
                            f"Cannot connect {_label(by_id[src_id])} ({src_type}) to "
                            f"{label} input '{inp['label']}' ({_friendly_input(inp['type'])}).",
                            node_id,
                            wire.get("id"),
                            "type",
                        )
                    )

        input_type = _single_input_type(node, incoming, out_types)
        input_fields = _single_input_fields(node, incoming, out_fields)
        produced = _produce(node, spec, data, input_type, input_fields, errors)
        for handle, (type_name, fields) in produced.items():
            out_types[(node_id, handle)] = type_name
            out_fields[(node_id, handle)] = fields

        if spec.get("outputs") and outgoing.get(node_id):
            pass
        if node["type"].startswith("output") and outgoing.get(node_id):
            errors.append(
                GraphErrorItem(
                    f"{label} is an output and cannot feed another box.",
                    node_id,
                    code="edge",
                )
            )

    output_nodes = [node for node in by_id.values() if str(node["type"]).startswith("output")]
    if by_id and not output_nodes:
        errors.append(
            GraphErrorItem(
                "Add a Number, Table, or Chart box so the graph has somewhere to show its result.",
                code="missing",
            )
        )
    return errors


def _produce(
    node: dict[str, Any],
    spec: dict[str, Any],
    data: dict[str, Any],
    input_type: str | None,
    input_fields: dict[str, str] | None,
    errors: list[GraphErrorItem],
) -> dict[str, tuple[str, dict[str, str]]]:
    node_id = node["id"]
    ntype = node["type"]
    label = _label(node)
    if ntype == "source":
        source = data.get("source")
        if source not in SOURCE_TYPES:
            errors.append(
                GraphErrorItem(
                    f"{label} needs a table: swaps, transfers, wallets, holders, or pools.",
                    node_id,
                    code="param",
                )
            )
            return {}
        type_name = SOURCE_TYPES[source]
        return {"out": (type_name, field_map(type_name))}

    if ntype == "detect_cycles":
        _check_detector_params(data, node_id, label, errors)
        return {
            "out_swaps": ("swaps_flagged", field_map("swaps_flagged")),
            "out_wallets": ("wash_wallets", field_map("wash_wallets")),
            "out_cycles": ("cycles", field_map("cycles")),
        }

    if ntype in {"condition", "time_window", "sort", "limit", "ml_score", "anomaly_score"}:
        if input_type is None or input_fields is None:
            return {}
        fields = dict(input_fields)
        if ntype == "ml_score":
            fields["ml_score"] = "number"
            if "usd_value" in fields:
                fields["ml_weighted_usd"] = "number"
        if ntype == "anomaly_score":
            fields["anomaly_score"] = "number"
        _check_params(ntype, data, input_type, fields, node_id, label, errors)
        return {"out": (input_type, fields)}

    if ntype == "explain":
        return {"out": ("explanations", field_map("explanations"))}

    if ntype == "group_by":
        if input_type is None:
            return {}
        key = str(data.get("by") or "")
        allowed = _GROUP_KEYS.get(input_type, set())
        if key not in allowed:
            errors.append(
                GraphErrorItem(
                    f"{label} cannot group {input_type} by {key or '(nothing)'}. "
                    f"Try one of: {', '.join(sorted(allowed)) or 'a swaps or transfers stream'}.",
                    node_id,
                    code="param",
                )
            )
            return {}
        fields: dict[str, str] = {"count": "number"}
        if key == "day":
            fields = {"day": "string", "count": "number", "sum_usd_value": "number"}
            if input_fields and "is_wash" in input_fields:
                fields["sum_wash_usd"] = "number"
        elif key == "wallet":
            fields = {"wallet": "string", "count": "number", "sum_usd_value": "number"}
        elif key == "pool":
            fields = {"pool_address": "string", "count": "number", "sum_usd_value": "number"}
        return {"out": ("grouped", fields)}

    if ntype == "aggregate":
        if input_fields is None:
            return {}
        fn = str(data.get("fn") or "count")
        if fn not in {"count", "sum", "avg"}:
            errors.append(GraphErrorItem(f"{label} function must be count, sum, or avg.", node_id, code="param"))
            return {}
        if fn != "count":
            field = str(data.get("field") or "")
            if field not in input_fields:
                errors.append(
                    GraphErrorItem(
                        f"{label} cannot {fn} {field or '(no field)'}. "
                        f"Fields: {', '.join(input_fields)}.",
                        node_id,
                        code="param",
                    )
                )
                return {}
            if input_fields[field] != "number":
                errors.append(
                    GraphErrorItem(f"{field} is not a number, so it cannot be {fn}med.", node_id, code="param")
                )
                return {}
        return {"out": ("scalar", field_map("scalar"))}

    if ntype == "ratio":
        _check_ratio(node, errors)
        return {"out": ("scalar", field_map("scalar"))}

    if ntype == "output_number":
        title = data.get("title")
        if title is not None and not isinstance(title, str):
            errors.append(GraphErrorItem(f"{label} title must be text.", node_id, code="param"))
        fmt = data.get("format") or "number"
        if fmt not in {"number", "percent", "integer"}:
            errors.append(GraphErrorItem(f"{label} format must be number, percent, or integer.", node_id, code="param"))
        return {}
    if ntype in {"output_table", "output_chart"}:
        return {}
    return {}


def _check_detector_params(data: dict[str, Any], node_id: str, label: str, errors: list[GraphErrorItem]) -> None:
    tolerance = data.get("tolerance")
    if tolerance not in (None, ""):
        if not _positive_number(tolerance) or float(tolerance) > 0.5:
            errors.append(
                GraphErrorItem(
                    f"{label} tolerance must be greater than 0 and at most 0.5.",
                    node_id,
                    code="param",
                )
            )
    window = data.get("window_blocks")
    if window not in (None, ""):
        if isinstance(window, bool) or not isinstance(window, (int, float)) or int(window) != window or not 1 <= int(window) <= 120_000:
            errors.append(
                GraphErrorItem(
                    f"{label} window must be a whole number of blocks from 1 to 120000.",
                    node_id,
                    code="param",
                )
            )
    rules = data.get("rules")
    if rules not in (None, ""):
        names = [part.strip() for part in str(rules).split(",") if part.strip()]
        if not names or any(name not in {"R1", "R2", "R3", "R4", "R5", "R6"} for name in names):
            errors.append(
                GraphErrorItem(
                    f"{label} kinds of wash trading must be chosen from R1 through R6.",
                    node_id,
                    code="param",
                )
            )
    size = data.get("max_group_size")
    if size not in (None, ""):
        if isinstance(size, bool) or not isinstance(size, (int, float)) or int(size) != size or not 2 <= int(size) <= 64:
            errors.append(
                GraphErrorItem(
                    f"{label} max group size must be a whole number from 2 to 64.",
                    node_id,
                    code="param",
                )
            )


def _check_params(
    ntype: str,
    data: dict[str, Any],
    input_type: str,
    fields: dict[str, str],
    node_id: str,
    label: str,
    errors: list[GraphErrorItem],
) -> None:
    if ntype == "condition":
        mode = data.get("mode") or "compare"
        if mode == "sold_in":
            if "days_since_sell" not in fields:
                errors.append(
                    GraphErrorItem(
                        f"{label} uses sold_in(), which only applies to wallets or holders.",
                        node_id,
                        code="param",
                    )
                )
            days = data.get("days")
            if not _positive_number(days) or float(days) > 365:
                errors.append(
                    GraphErrorItem(
                        f"{label} needs a sold_in window from 1 to 365 days.",
                        node_id,
                        code="param",
                    )
                )
            return
        field = str(data.get("field") or "")
        op = str(data.get("op") or "")
        if field not in fields:
            errors.append(
                GraphErrorItem(
                    f"{label} filters on {field or '(no field)'}, which {input_type} does not have. "
                    f"Available fields: {', '.join(fields)}.",
                    node_id,
                    code="param",
                )
            )
            return
        kind = fields[field]
        allowed = {">", ">=", "<", "<=", "=", "!="} if kind == "number" else {"=", "!="}
        if op not in allowed:
            errors.append(
                GraphErrorItem(
                    f"{label} cannot use {op or '(no operator)'} on {field} ({kind}).",
                    node_id,
                    code="param",
                )
            )
        if "value" not in data:
            errors.append(GraphErrorItem(f"{label} needs a value to compare against.", node_id, code="param"))
            return
        if not _value_ok(kind, data.get("value")):
            errors.append(
                GraphErrorItem(
                    f"{label} compares {field} ({kind}) to {data.get('value')!r}, which is the wrong kind of value.",
                    node_id,
                    code="param",
                )
            )
    elif ntype == "time_window":
        days = data.get("days")
        if not _positive_number(days) or float(days) > 90:
            errors.append(
                GraphErrorItem(
                    f"{label} needs a length from 1 to 90 days, inside the loaded window.",
                    node_id,
                    code="param",
                )
            )
    elif ntype == "sort":
        field = str(data.get("field") or "")
        if field not in fields:
            errors.append(
                GraphErrorItem(
                    f"{label} sorts by {field or '(no field)'}, which is not on this stream. "
                    f"Fields: {', '.join(fields)}.",
                    node_id,
                    code="param",
                )
            )
        direction = data.get("direction") or "desc"
        if direction not in {"asc", "desc"}:
            errors.append(GraphErrorItem(f"{label} direction must be asc or desc.", node_id, code="param"))
    elif ntype == "limit":
        n = data.get("n")
        if isinstance(n, bool) or not isinstance(n, (int, float)) or int(n) != n or not 1 <= int(n) <= 1000:
            errors.append(
                GraphErrorItem(f"{label} needs a whole number N from 1 to 1000.", node_id, code="param")
            )


def _check_ratio(node: dict[str, Any], errors: list[GraphErrorItem]) -> None:
    field = str((node.get("data") or {}).get("field") or "")
    if not field:
        errors.append(
            GraphErrorItem(
                f"{_label(node)} needs the numeric field to divide, usually usd_value.",
                node["id"],
                code="param",
            )
        )


def _single_input_type(
    node: dict[str, Any],
    incoming: dict[tuple[str, str], list[dict[str, Any]]],
    out_types: dict[tuple[str, str], str],
) -> str | None:
    spec = NODE_BY_TYPE[node["type"]]
    inputs = spec.get("inputs") or []
    if len(inputs) != 1:
        return None
    wires = incoming.get((node["id"], inputs[0]["id"]), [])
    if len(wires) != 1:
        return None
    wire = wires[0]
    return out_types.get((wire["source"], wire.get("sourceHandle") or "out"))


def _single_input_fields(
    node: dict[str, Any],
    incoming: dict[tuple[str, str], list[dict[str, Any]]],
    out_fields: dict[tuple[str, str], dict[str, str]],
) -> dict[str, str] | None:
    spec = NODE_BY_TYPE[node["type"]]
    inputs = spec.get("inputs") or []
    if len(inputs) != 1:
        return None
    wires = incoming.get((node["id"], inputs[0]["id"]), [])
    if len(wires) != 1:
        return None
    wire = wires[0]
    return out_fields.get((wire["source"], wire.get("sourceHandle") or "out"))


def _value_ok(kind: str, value: Any) -> bool:
    if kind == "bool":
        return value in {True, False, "true", "false"}
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    return False


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) > 0


def _friendly_input(type_name: str) -> str:
    return {
        "rows": "any table",
        "timed": "swaps or transfers",
        "walletish": "wallets",
        "scoreable": "swaps or wallets",
        "scalar": "a single number",
        "swaps": "swaps",
    }.get(type_name, type_name)


def _handle(handles: list[dict[str, Any]], handle_id: str) -> dict[str, Any] | None:
    for handle in handles:
        if handle["id"] == handle_id:
            return handle
    return None


def _label(node: dict[str, Any]) -> str:
    data = node.get("data") or {}
    if node.get("type") == "source" and data.get("source"):
        return f"Source ({data['source']})"
    title = data.get("title")
    base = NODE_BY_TYPE.get(node.get("type"), {}).get("label", node.get("type"))
    if isinstance(title, str) and title.strip():
        return f"{base} '{title.strip()}'"
    return str(base)


def _has_cycle(by_id: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> bool:
    graph: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for edge in edges:
        src, dst = edge.get("source"), edge.get("target")
        if src in graph and dst in graph and src != dst:
            graph[src].append(dst)
    color = dict.fromkeys(graph, 0)
    def visit(node_id: str) -> bool:
        color[node_id] = 1
        for nxt in graph[node_id]:
            if color[nxt] == 1 or (color[nxt] == 0 and visit(nxt)):
                return True
        color[node_id] = 2
        return False
    return any(color[node_id] == 0 and visit(node_id) for node_id in graph)


def _topo(by_id: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    indeg = dict.fromkeys(by_id, 0)
    graph: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for edge in edges:
        src, dst = edge.get("source"), edge.get("target")
        if src in by_id and dst in by_id and src != dst:
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
    for node_id in by_id:
        if node_id not in order:
            order.append(node_id)
    return order

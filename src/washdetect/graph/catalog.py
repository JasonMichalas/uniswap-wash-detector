"""Box types, socket types, and the columns each stream is allowed to use.

The editor asks the API for this catalog, so the Python file is the one
place a student adds a field or a box. Sockets only connect when
`can_connect` says so. `rows`, `timed`, and `walletish` are input-only
wildcards; they are never the type of an output.
"""

from __future__ import annotations

from typing import Any

# kind is "number", "string", or "bool". Order is the order shown in the UI.
TYPES: dict[str, list[tuple[str, str]]] = {
    "swaps": [
        ("block_number", "number"),
        ("trader", "string"),
        ("side", "string"),
        ("pool_address", "string"),
        ("protocol", "string"),
        ("usd_value", "number"),
        ("amount_token", "number"),
        ("tx_hash", "string"),
    ],
    "transfers": [
        ("block_number", "number"),
        ("from_address", "string"),
        ("to_address", "string"),
        ("amount", "number"),
        ("tx_hash", "string"),
    ],
    "wallets": [
        ("wallet", "string"),
        ("balance", "number"),
        ("held_days", "number"),
        ("held_days_is_lower_bound", "bool"),
        ("acquired_before_window", "bool"),
        ("ever_sold", "bool"),
        ("days_since_sell", "number"),
        ("swap_count", "number"),
        ("buy_volume_usd", "number"),
        ("sell_volume_usd", "number"),
    ],
    "wash_wallets": [
        ("wallet", "string"),
        ("confidence", "number"),
        ("wash_volume_usd", "number"),
        ("round_trip_count", "number"),
        ("ring_count", "number"),
        ("swap_count", "number"),
        ("net_ratio", "number"),
        ("volume_usd", "number"),
    ],
    "cycles": [
        ("cycle_id", "string"),
        ("size", "number"),
        ("kind", "string"),
        ("pattern_type", "string"),
        ("n_buyers", "number"),
        ("n_sellers", "number"),
        ("n_wallets", "number"),
        ("wallets", "string"),
        ("volume_usd", "number"),
        ("confidence", "number"),
        ("start_block", "number"),
        ("end_block", "number"),
    ],
    "pools": [
        ("pool_address", "string"),
        ("protocol", "string"),
        ("token0_symbol", "string"),
        ("token1_symbol", "string"),
        ("fee", "number"),
        ("swap_count", "number"),
        ("volume_usd", "number"),
    ],
    "scalar": [("value", "number")],
    "grouped": [
        ("day", "string"),
        ("wallet", "string"),
        ("pool_address", "string"),
        ("count", "number"),
        ("sum_usd_value", "number"),
        ("sum_wash_usd", "number"),
    ],
}

# swaps_flagged is a swaps stream plus the detector's columns. It is not a
# different family: a filter that accepts swaps also accepts it, except the
# detector input itself, which wants unmarked swaps (see can_connect).
TYPES["swaps_flagged"] = TYPES["swaps"] + [
    ("is_wash", "bool"),
    ("confidence", "number"),
    ("cycle_id", "string"),
]

ROW_TYPES = (
    "swaps",
    "swaps_flagged",
    "transfers",
    "wallets",
    "wash_wallets",
    "cycles",
    "pools",
    "grouped",
)

SOURCE_TYPES = {
    "swaps": "swaps",
    "transfers": "transfers",
    "wallets": "wallets",
    "holders": "wallets",
    "pools": "pools",
}


def field_map(type_name: str) -> dict[str, str]:
    try:
        return dict(TYPES[type_name])
    except KeyError as exc:
        raise KeyError(f"unknown stream type {type_name}") from exc


def can_connect(src: str, dst: str) -> bool:
    """Whether an output socket of type `src` may feed an input of type `dst`."""
    if dst == "rows":
        return src in ROW_TYPES
    if dst == "timed":
        return src in {"swaps", "swaps_flagged", "transfers"}
    if dst == "walletish":
        return src in {"wallets", "wash_wallets"}
    if dst == "swaps":
        # The detector wants the raw swap stream, not one it has already flagged.
        return src == "swaps"
    return src == dst


NODE_DEFS: list[dict[str, Any]] = [
    {
        "type": "source",
        "label": "Source",
        "category": "source",
        "description": "A table loaded for this token. Holders are wallets with a positive balance.",
        "inputs": [],
        "outputs": [{"id": "out", "label": "out", "type": "dynamic", "typeFrom": "source"}],
        "params": [
            {
                "name": "source",
                "label": "Table",
                "kind": "enum",
                "options": ["swaps", "transfers", "wallets", "holders", "pools"],
            }
        ],
    },
    {
        "type": "condition",
        "label": "Condition",
        "category": "filter",
        "description": "Keep rows that match a comparison, or wallets that did or did not sell recently.",
        "inputs": [{"id": "in", "label": "in", "type": "rows"}],
        "outputs": [{"id": "out", "label": "out", "type": "same"}],
        "params": [
            {"name": "mode", "label": "Mode", "kind": "enum", "options": ["compare", "sold_in"]},
            {"name": "field", "label": "Field", "kind": "field"},
            {
                "name": "op",
                "label": "Operator",
                "kind": "enum",
                "options": [">", ">=", "<", "<=", "=", "!="],
            },
            {"name": "value", "label": "Value", "kind": "value"},
            {"name": "days", "label": "Days", "kind": "number"},
            {"name": "negate", "label": "Has not sold", "kind": "bool"},
        ],
    },
    {
        "type": "time_window",
        "label": "Time window",
        "category": "filter",
        "description": "Keep swaps or transfers in a shorter slice of the loaded range.",
        "inputs": [{"id": "in", "label": "in", "type": "timed"}],
        "outputs": [{"id": "out", "label": "out", "type": "same"}],
        "params": [
            {"name": "days", "label": "Last N days of the loaded window", "kind": "number"},
        ],
    },
    {
        "type": "detect_cycles",
        "label": "Cycle detector",
        "category": "detector",
        "description": (
            "Flags round-trips and connected flow groups whose pooled buys and sells "
            "cancel: one-to-many, many-to-one, many-to-many, and rings. "
            "Filter the cycles output on pattern_type, n_buyers, n_sellers, or n_wallets. "
            "Tolerance is the allowed |buys-sells|/max gap, the window is in blocks, "
            "and max group size caps how many wallets one group may contain."
        ),
        "params": [
            {"name": "tolerance", "label": "Amount tolerance (0–0.5)", "kind": "number"},
            {"name": "window_blocks", "label": "Time window (blocks)", "kind": "number"},
            {"name": "max_group_size", "label": "Max wallets in a group", "kind": "number"},
        ],
        "inputs": [
            {"id": "in_swaps", "label": "swaps", "type": "swaps"},
            {"id": "in_transfers", "label": "transfers", "type": "transfers"},
        ],
        "outputs": [
            {"id": "out_swaps", "label": "flagged swaps", "type": "swaps_flagged"},
            {"id": "out_wallets", "label": "wallets", "type": "wash_wallets"},
            {"id": "out_cycles", "label": "cycles", "type": "cycles"},
        ],
    },
    {
        "type": "ml_score",
        "label": "ML wash score",
        "category": "detector",
        "description": (
            "Milestone 2. Will attach an XGBoost probability to each wallet. "
            "The box validates today and refuses to run until a model is trained."
        ),
        "inputs": [{"id": "in", "label": "wallets", "type": "walletish"}],
        "outputs": [{"id": "out", "label": "scored", "type": "same"}],
        "params": [],
    },
    {
        "type": "group_by",
        "label": "Group by",
        "category": "aggregate",
        "description": "Collapse rows by day, wallet, or pool and sum USD volume.",
        "inputs": [{"id": "in", "label": "in", "type": "rows"}],
        "outputs": [{"id": "out", "label": "groups", "type": "grouped"}],
        "params": [
            {"name": "by", "label": "Group by", "kind": "enum", "options": ["day", "wallet", "pool"]}
        ],
    },
    {
        "type": "aggregate",
        "label": "Aggregate",
        "category": "aggregate",
        "description": "Reduce a stream to one number: a count, a sum, or an average.",
        "inputs": [{"id": "in", "label": "in", "type": "rows"}],
        "outputs": [{"id": "out", "label": "value", "type": "scalar"}],
        "params": [
            {"name": "fn", "label": "Function", "kind": "enum", "options": ["count", "sum", "avg"]},
            {"name": "field", "label": "Field", "kind": "field"},
        ],
    },
    {
        "type": "ratio",
        "label": "Percent of",
        "category": "aggregate",
        "description": "100 × sum(part) / sum(whole) for one numeric field. This is how wash % is built.",
        "inputs": [
            {"id": "in_part", "label": "part", "type": "rows"},
            {"id": "in_whole", "label": "whole", "type": "rows"},
        ],
        "outputs": [{"id": "out", "label": "percent", "type": "scalar"}],
        "params": [{"name": "field", "label": "Field", "kind": "field"}],
    },
    {
        "type": "sort",
        "label": "Sort",
        "category": "arrange",
        "description": "Order rows by a field.",
        "inputs": [{"id": "in", "label": "in", "type": "rows"}],
        "outputs": [{"id": "out", "label": "out", "type": "same"}],
        "params": [
            {"name": "field", "label": "Field", "kind": "field"},
            {"name": "direction", "label": "Direction", "kind": "enum", "options": ["desc", "asc"]},
        ],
    },
    {
        "type": "limit",
        "label": "Limit",
        "category": "arrange",
        "description": "Keep the first N rows. Put Sort in front of this to get a top-N.",
        "inputs": [{"id": "in", "label": "in", "type": "rows"}],
        "outputs": [{"id": "out", "label": "out", "type": "same"}],
        "params": [{"name": "n", "label": "N", "kind": "number"}],
    },
    {
        "type": "output_number",
        "label": "Number",
        "category": "output",
        "description": "Show one number. Use format percent when the input is a Percent of box.",
        "inputs": [{"id": "in", "label": "value", "type": "scalar"}],
        "outputs": [],
        "params": [
            {"name": "title", "label": "Title", "kind": "text"},
            {
                "name": "format",
                "label": "Format",
                "kind": "enum",
                "options": ["number", "percent", "integer"],
            },
        ],
    },
    {
        "type": "output_table",
        "label": "Table",
        "category": "output",
        "description": "Show the rows.",
        "inputs": [{"id": "in", "label": "rows", "type": "rows"}],
        "outputs": [],
        "params": [{"name": "title", "label": "Title", "kind": "text"}],
    },
    {
        "type": "output_chart",
        "label": "Chart",
        "category": "output",
        "description": "Plot a Group by day stream. If the swaps were flagged, wash volume is a second series.",
        "inputs": [{"id": "in", "label": "groups", "type": "grouped"}],
        "outputs": [],
        "params": [{"name": "title", "label": "Title", "kind": "text"}],
    },
]

PALETTE: list[dict[str, Any]] = [
    {
        "node_type": "source",
        "label": "Swaps",
        "category": "source",
        "blurb": "Buys and sells in this token's pools.",
        "data": {"source": "swaps"},
    },
    {
        "node_type": "source",
        "label": "Transfers",
        "category": "source",
        "blurb": "ERC-20 transfers of this token.",
        "data": {"source": "transfers"},
    },
    {
        "node_type": "source",
        "label": "Wallets",
        "category": "source",
        "blurb": "Every wallet seen in the loaded window.",
        "data": {"source": "wallets"},
    },
    {
        "node_type": "source",
        "label": "Holders",
        "category": "source",
        "blurb": "Wallets with a positive balance at the window end.",
        "data": {"source": "holders"},
    },
    {
        "node_type": "source",
        "label": "Pools",
        "category": "source",
        "blurb": "Uniswap v2 and v3 pools quoted in WETH or a stablecoin.",
        "data": {"source": "pools"},
    },
    {
        "node_type": "condition",
        "label": "Condition",
        "category": "filter",
        "blurb": "Keep rows matching a field, or sold_in(Nd).",
        "data": {"mode": "compare", "field": "", "op": ">", "value": 0, "days": 30, "negate": False},
    },
    {
        "node_type": "time_window",
        "label": "Time window",
        "category": "filter",
        "blurb": "Last N days inside the loaded range.",
        "data": {"days": 7},
    },
    {
        "node_type": "detect_cycles",
        "label": "Cycle detector",
        "category": "detector",
        "blurb": "Round-trips, rings, and M:N flow groups.",
        "data": {"tolerance": 0.05, "window_blocks": 200, "max_group_size": 16},
    },
    {
        "node_type": "ml_score",
        "label": "ML wash score",
        "category": "detector",
        "blurb": "Not trained yet. Reserved for XGBoost.",
        "data": {},
    },
    {
        "node_type": "group_by",
        "label": "Group by",
        "category": "aggregate",
        "blurb": "By day, wallet, or pool.",
        "data": {"by": "day"},
    },
    {
        "node_type": "aggregate",
        "label": "Aggregate",
        "category": "aggregate",
        "blurb": "count, sum, or avg.",
        "data": {"fn": "count", "field": "usd_value"},
    },
    {
        "node_type": "ratio",
        "label": "Percent of",
        "category": "aggregate",
        "blurb": "Part ÷ whole, as a percentage.",
        "data": {"field": "usd_value"},
    },
    {
        "node_type": "sort",
        "label": "Sort",
        "category": "arrange",
        "blurb": "Order by a field.",
        "data": {"field": "", "direction": "desc"},
    },
    {
        "node_type": "limit",
        "label": "Limit",
        "category": "arrange",
        "blurb": "Keep the first N rows.",
        "data": {"n": 20},
    },
    {
        "node_type": "output_number",
        "label": "Number",
        "category": "output",
        "blurb": "A single figure.",
        "data": {"title": "Result", "format": "number"},
    },
    {
        "node_type": "output_table",
        "label": "Table",
        "category": "output",
        "blurb": "The rows.",
        "data": {"title": "Results"},
    },
    {
        "node_type": "output_chart",
        "label": "Chart",
        "category": "output",
        "blurb": "Daily volume bars.",
        "data": {"title": "Volume"},
    },
]

NODE_BY_TYPE = {node["type"]: node for node in NODE_DEFS}


def catalog_payload() -> dict[str, Any]:
    types_out = {
        name: [{"name": field, "kind": kind} for field, kind in fields]
        for name, fields in TYPES.items()
    }
    return {
        "palette": PALETTE,
        "nodes": NODE_DEFS,
        "types": types_out,
        "row_types": list(ROW_TYPES),
        "source_types": SOURCE_TYPES,
        "connect": {
            "rows": list(ROW_TYPES),
            "timed": ["swaps", "swaps_flagged", "transfers"],
            "walletish": ["wallets", "wash_wallets"],
            "swaps_exact": ["swaps"],
        },
    }

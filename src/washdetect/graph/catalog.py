from __future__ import annotations

from typing import Any

TYPES: dict[str, list[tuple[str, str]]] = {
    "swaps": [
        ("block_number", "number"),
        ("trader", "string"),
        ("side", "string"),
        ("pool_address", "string"),
        ("protocol", "string"),
        ("usd_value", "number"),
        ("amount_token", "number"),
        ("price_source", "string"),
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
        ("holder_kind", "string"),
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
        ("rules", "string"),
        ("why", "string"),
        ("path", "string"),
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
    "explanations": [
        ("wallet", "string"),
        ("rank", "number"),
        ("feature", "string"),
        ("feature_value", "number"),
        ("shap_value", "number"),
        ("direction", "string"),
    ],
    "scalar": [("value", "number")],
    "grouped": [
        ("day", "string"),
        ("wallet", "string"),
        ("pool_address", "string"),
        ("count", "number"),
        ("sum_usd_value", "number"),
        ("sum_amount_token", "number"),
        ("sum_wash_usd", "number"),
    ],
}

TYPES["swaps_flagged"] = TYPES["swaps"] + [
    ("is_wash", "bool"),
    ("confidence", "number"),
    ("cycle_id", "string"),
    ("rules", "string"),
    ("why", "string"),
    ("path", "string"),
]

INSIGHT_KINDS = (
    "concentration",
    "pnl",
    "sandwich",
    "insiders",
    "fake_holders",
    "liquidity_pull",
    "whales",
    "pressure",
    "bots",
    "wash_over_time",
)

# A chart can read a Group by, or one of the questions that already is a time series.
CHART_STREAMS = ("grouped", "pressure", "wash_over_time")

TYPES["concentration"] = [
    ("wallet", "string"),
    ("balance", "number"),
    ("share_percent", "number"),
]
TYPES["pnl"] = [
    ("wallet", "string"),
    ("side", "string"),
    ("realised_usd", "number"),
    ("unrealised_usd", "number"),
    ("total_usd", "number"),
    ("cost_status", "string"),
    ("why", "string"),
    ("tokens_bought", "number"),
    ("tokens_sold", "number"),
    ("balance", "number"),
]
TYPES["sandwich"] = [
    ("attacker", "string"),
    ("victim", "string"),
    ("pool_address", "string"),
    ("direction", "string"),
    ("profit_usd", "number"),
    ("block_number", "number"),
    ("buy_tx", "string"),
    ("victim_tx", "string"),
    ("sell_tx", "string"),
]
TYPES["insiders"] = [
    ("wallet", "string"),
    ("received_amount", "number"),
    ("sold_usd", "number"),
    ("creator", "string"),
    ("why", "string"),
]
TYPES["fake_holders"] = [
    ("source", "string"),
    ("wallet_count", "number"),
    ("holdings", "number"),
    ("span_minutes", "number"),
    ("first_block", "number"),
    ("last_block", "number"),
]
TYPES["liquidity_pull"] = [
    ("pool_address", "string"),
    ("removed_percent", "number"),
    ("removed_usd", "number"),
    ("span_minutes", "number"),
    ("remover", "string"),
    ("original_adder", "string"),
    ("same_wallet", "bool"),
    ("block_timestamp", "string"),
]
TYPES["whales"] = [
    ("from_address", "string"),
    ("to_address", "string"),
    ("amount_token", "number"),
    ("amount_usd", "number"),
    ("supply_percent", "number"),
    ("tag", "string"),
    ("tx_hash", "string"),
    ("block_timestamp", "string"),
]
TYPES["pressure"] = [
    ("day", "string"),
    ("buy_usd", "number"),
    ("sell_usd", "number"),
    ("net_usd", "number"),
    ("price_usd", "number"),
]
TYPES["bots"] = [
    ("wallet", "string"),
    ("label", "string"),
    ("reason", "string"),
    ("trade_count", "number"),
    ("back_to_back_percent", "number"),
    ("active_hours", "number"),
]
TYPES["wash_over_time"] = [
    ("day", "string"),
    ("volume_usd", "number"),
    ("wash_usd", "number"),
    ("wash_percent", "number"),
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
    "explanations",
    *INSIGHT_KINDS,
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
    if dst == "rows":
        return src in ROW_TYPES
    if dst == "grouped":
        return src in CHART_STREAMS
    if dst == "timed":
        return src in {"swaps", "swaps_flagged", "transfers"}
    if dst == "walletish":
        return src in {"wallets", "wash_wallets"}
    if dst == "scoreable":
        return src in {"swaps", "swaps_flagged", "wallets", "wash_wallets"}
    if dst == "swaps":
        return src == "swaps"  # detector input must be unmarked; flagged swaps are a different socket
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
        "label": "Find wash groups",
        "category": "detector",
        "description": (
            "Finds wash groups and writes a plain-English Why and a path on each flagged swap. "
            "Kinds: same wallet buys and sells; tokens passed along then sold; wallets funded "
            "or paid together; a circle; many trades with nothing left; buy and sell in one "
            "transaction. How close the amounts must be, the longest time between buy and sell, "
            "and the most wallets in a chain are settings on the box."
        ),
        "params": [
            {"name": "tolerance", "label": "How close buys and sells must be", "kind": "number"},
            {"name": "window_blocks", "label": "Longest time between buy and sell", "kind": "number"},
            {"name": "max_group_size", "label": "Most wallets in a chain", "kind": "number"},
            {"name": "rules", "label": "Which kinds of wash trading", "kind": "text"},
            {"name": "show_all", "label": "Show all wallets", "kind": "bool"},
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
            "Attaches a calibrated XGBoost wash probability (ml_score). "
            "On swaps it also adds ml_weighted_usd = probability × usd_value. "
            "A Percent of box divides that by usd_value to get the probability-weighted wash percent."
        ),
        "inputs": [{"id": "in", "label": "swaps or wallets", "type": "scoreable"}],
        "outputs": [{"id": "out", "label": "scored", "type": "same"}],
        "params": [],
    },
    {
        "type": "anomaly_score",
        "label": "Anomaly score",
        "category": "detector",
        "description": (
            "Attaches an Isolation Forest score in [0, 1]. Higher means the wallet or swap "
            "looks less like the bulk of the training tokens. This is a rank, not a probability."
        ),
        "inputs": [{"id": "in", "label": "swaps or wallets", "type": "scoreable"}],
        "outputs": [{"id": "out", "label": "scored", "type": "same"}],
        "params": [],
    },
    {
        "type": "explain",
        "label": "Explain",
        "category": "detector",
        "description": (
            "Top TreeSHAP reasons for up to 25 wallets. Sort and limit first to explain "
            "the highest-scoring wallets. Reasons describe the raw XGBoost margin."
        ),
        "inputs": [{"id": "in", "label": "wallets", "type": "walletish"}],
        "outputs": [{"id": "out", "label": "reasons", "type": "explanations"}],
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
        "description": (
            "100 × sum(part) / sum(whole). Leave Divide by blank to use the same field on both. "
            "For the ML wash percent, divide ml_weighted_usd by usd_value on the scored swaps."
        ),
        "inputs": [
            {"id": "in_part", "label": "part", "type": "rows"},
            {"id": "in_whole", "label": "whole", "type": "rows"},
        ],
        "outputs": [{"id": "out", "label": "percent", "type": "scalar"}],
        "params": [
            {"name": "field", "label": "Field", "kind": "field"},
            {"name": "over", "label": "Divide by (blank = same field)", "kind": "field"},
        ],
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
        "label": "Find wash groups",
        "category": "detector",
        "blurb": "Round-trips, rings, and M:N flow groups.",
        "data": {
            "tolerance": 0.05,
            "window_blocks": 7200,
            "max_group_size": 24,
            "rules": "R1,R2,R3,R4,R5,R6",
        },
    },
    {
        "node_type": "ml_score",
        "label": "ML wash score",
        "category": "detector",
        "blurb": "Calibrated XGBoost probability.",
        "data": {},
    },
    {
        "node_type": "anomaly_score",
        "label": "Anomaly score",
        "category": "detector",
        "blurb": "Isolation Forest rank in [0, 1].",
        "data": {},
    },
    {
        "node_type": "explain",
        "label": "Explain",
        "category": "detector",
        "blurb": "Top SHAP reasons for the selected wallets.",
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

def _question(
    kind: str,
    label: str,
    blurb: str,
    description: str,
    params: list[dict[str, Any]],
    defaults: dict[str, Any],
) -> None:
    NODE_DEFS.append(
        {
            "type": kind,
            "label": label,
            "category": "insight",
            "description": description,
            "inputs": [],
            "outputs": [{"id": "out", "label": "out", "type": kind}],
            "params": params,
        }
    )
    PALETTE.append(
        {
            "node_type": kind,
            "label": label,
            "category": "insight",
            "blurb": blurb,
            "data": defaults,
        }
    )


_NUMBER = "number"
_TEXT = "text"

_question(
    "concentration",
    "Holder concentration",
    "Share of the balances we could read held by the top wallets, plus a Gini score.",
    "How concentrated ownership is, using balances read at the end of the window.",
    [{"name": "top_n", "label": "How many holders", "kind": _NUMBER}],
    {"top_n": 10},
)
_question(
    "pnl",
    "Profit and loss",
    "Who made or lost money, with an unknown cost when the buy was before the window.",
    "Realised and unrealised profit for wallets that traded in the loaded window.",
    [{"name": "top_n", "label": "How many wallets", "kind": _NUMBER}],
    {"top_n": 10},
)
_question(
    "sandwich",
    "Sandwich attacks",
    "A wallet that trades just before and just after someone else, in one block and one pool.",
    "Same-block sandwiches and the attacker's profit.",
    [],
    {},
)
_question(
    "insiders",
    "Insiders",
    "Wallets that received the token from the creator and then sold.",
    "Creator gifts that were sold. Paste a creator if this window cannot find one.",
    [{"name": "creator", "label": "Creator address", "kind": _TEXT}],
    {"creator": ""},
)
_question(
    "fake_holders",
    "Fake holders",
    "Wallets whose first tokens came from the same wallet at about the same time.",
    "Shared-funding clusters. This is not a wash result.",
    [
        {"name": "minutes", "label": "Within minutes", "kind": _NUMBER},
        {"name": "min_wallets", "label": "Minimum wallets", "kind": _NUMBER},
    ],
    {"minutes": 60, "min_wallets": 5},
)
_question(
    "liquidity_pull",
    "Liquidity pulls",
    "A pool that lost a large share of its added liquidity in a short time.",
    "Fast liquidity removals, flagged when the remover is the original adder.",
    [
        {"name": "minutes", "label": "Within minutes", "kind": _NUMBER},
        {"name": "percent", "label": "Percent removed", "kind": _NUMBER},
    ],
    {"minutes": 60, "percent": 50},
)
_question(
    "whales",
    "Whale moves",
    "Large transfers, tagged when they go to or come from an exchange.",
    "Transfers above a dollar size or a share of the balances we could read.",
    [
        {"name": "usd", "label": "Dollars", "kind": _NUMBER},
        {"name": "percent", "label": "Percent of supply", "kind": _NUMBER},
    ],
    {"usd": 100000, "percent": 1},
)
_question(
    "pressure",
    "Buy vs sell pressure",
    "Hourly dollars bought minus dollars sold, next to the price.",
    "Buy and sell pressure by hour.",
    [],
    {},
)
_question(
    "bots",
    "Bots vs humans",
    "Wallets that trade like bots, and the busiest wallets that do not.",
    "A label and the reason, from trade rhythm and known MEV wallets.",
    [{"name": "top_n", "label": "How many people", "kind": _NUMBER}],
    {"top_n": 10},
)
_question(
    "wash_over_time",
    "Wash share over time",
    "Each day's flagged wash dollars divided by that day's total dollars.",
    "Wash as a share of volume, one row per day.",
    [],
    {},
)

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
            "chart": list(CHART_STREAMS),
            "timed": ["swaps", "swaps_flagged", "transfers"],
            "walletish": ["wallets", "wash_wallets"],
            "scoreable": ["swaps", "swaps_flagged", "wallets", "wash_wallets"],
            "swaps_exact": ["swaps"],
        },
    }

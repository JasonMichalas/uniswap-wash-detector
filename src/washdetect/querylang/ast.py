from __future__ import annotations

import json
from dataclasses import dataclass, field


def render_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return json.dumps(str(value))


@dataclass
class Condition:
    kind: str
    negate: bool = False
    field: str | None = None
    op: str | None = None
    value: object = None
    amount: float | None = None
    unit: str = "d"

    def to_text(self) -> str:
        if self.kind == "sold_in":
            amount = self.amount if self.amount is not None else 0
            shown = int(amount) if float(amount).is_integer() else amount
            text = f"sold_in({shown}{self.unit})"
            return f"not {text}" if self.negate else text
        return f"{self.field} {self.op} {render_value(self.value)}"


@dataclass
class Source:
    kind: str

    def to_text(self) -> str:
        return f"source {self.kind}"


@dataclass
class Detect:
    swaps: str
    transfers: str
    net_ratio: float | None = None
    max_block_gap: int | None = None
    max_group_size: int | None = None
    rules: str | None = None
    show_all: bool = False

    def to_text(self) -> str:
        head = f"find_wash_groups({self.swaps}, {self.transfers}"
        if self.net_ratio is None and self.max_block_gap is None and self.max_group_size is None:
            return head + ")"
        tolerance = 0.05 if self.net_ratio is None else self.net_ratio
        window = 200 if self.max_block_gap is None else int(self.max_block_gap)
        group = 16 if self.max_group_size is None else int(self.max_group_size)
        return f"{head}, {render_value(tolerance)}, {window}, {group})"


@dataclass
class Project:
    name: str
    kind: str

    def to_text(self) -> str:
        return f"project {self.name} {self.kind}"


@dataclass
class Filter:
    name: str
    conditions: list[Condition]

    def to_text(self) -> str:
        body = " and ".join(cond.to_text() for cond in self.conditions)
        return f"filter {self.name} where {body}"


@dataclass
class TimeWindow:
    name: str
    days: float | None = None
    unit: str = "d"
    start: str | None = None
    end: str | None = None

    def to_text(self) -> str:
        if self.days is not None:
            shown = int(self.days) if float(self.days).is_integer() else self.days
            return f"window {self.name} last {shown}{self.unit}"
        return f'window {self.name} from {json.dumps(self.start)} to {json.dumps(self.end)}'


@dataclass
class GroupBy:
    name: str
    key: str

    def to_text(self) -> str:
        return f"group {self.name} by {self.key}"


@dataclass
class Aggregate:
    fn: str
    name: str
    field: str | None = None

    def to_text(self) -> str:
        if self.fn == "count":
            return f"count {self.name}"
        return f"{self.fn} {self.name} of {self.field}"


@dataclass
class Ratio:
    part: str
    whole: str
    field: str
    whole_field: str | None = None

    def to_text(self) -> str:
        text = f"ratio {self.part} {self.whole} of {self.field}"
        if self.whole_field and self.whole_field != self.field:
            text += f" over {self.whole_field}"
        return text


@dataclass
class Sort:
    name: str
    field: str
    direction: str = "desc"

    def to_text(self) -> str:
        return f"sort {self.name} by {self.field} {self.direction}"


@dataclass
class Limit:
    name: str
    n: int

    def to_text(self) -> str:
        return f"limit {self.name} {self.n}"


@dataclass
class MlScore:
    name: str

    def to_text(self) -> str:
        return f"ml_score {self.name}"


@dataclass
class AnomalyScore:
    name: str

    def to_text(self) -> str:
        return f"anomaly_score {self.name}"


@dataclass
class Explain:
    name: str

    def to_text(self) -> str:
        return f"explain {self.name}"


Expr = (
    Source
    | Detect
    | Project
    | Filter
    | TimeWindow
    | GroupBy
    | Aggregate
    | Ratio
    | Sort
    | Limit
    | MlScore
    | AnomalyScore
    | Explain
)


@dataclass
class Assign:
    name: str
    expr: Expr

    def to_text(self) -> str:
        return f"{self.name} = {self.expr.to_text()}"


@dataclass
class Output:
    kind: str
    name: str
    title: str | None = None
    format: str = "number"

    def to_text(self) -> str:
        title = f" as {json.dumps(self.title)}" if self.title else ""
        fmt = f" format {self.format}" if self.format else ""
        return f"output {self.kind} {self.name}{title}{fmt}"


@dataclass
class Program:
    statements: list[Assign | Output] = field(default_factory=list)

    def to_text(self) -> str:
        return "\n".join(stmt.to_text() for stmt in self.statements) + "\n"

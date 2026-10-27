from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from washdetect.detector import Detection


@dataclass
class OutputResult:
    kind: str
    title: str
    format: str
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool = False
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "kind": self.kind,
            "title": self.title,
            "format": self.format,
            "columns": self.columns,
            "rows": self.rows,
            "truncated": self.truncated,
        }
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass
class ExecResult:
    query: str
    sql: str
    outputs: list[OutputResult]
    warnings: list[str] = field(default_factory=list)
    detection: Detection | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "sql": self.sql,
            "outputs": [item.as_dict() for item in self.outputs],
            "warnings": self.warnings,
        }


@dataclass
class _Stream:
    view: str
    type_name: str
    fields: dict[str, str]
    note: str | None = None


_SCOREABLE = {"swaps", "swaps_flagged", "wallets", "wash_wallets"}


@dataclass
class _Bundle:
    swaps: _Stream
    wallets: _Stream
    cycles: _Stream

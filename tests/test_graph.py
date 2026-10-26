import json
from pathlib import Path

import pytest

from washdetect.graph.compile import GraphCompileError, compile_graph
from washdetect.graph.validate import validate_graph
from washdetect.querylang.parser import parse

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = sorted((ROOT / "examples" / "graphs").glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda path: path.stem)
def test_templates_validate_and_compile(path: Path):
    graph = _load(path)
    assert validate_graph(graph) == []
    _program, text = compile_graph(graph)
    # The text under the graph is real query language, not a private dialect.
    again = parse(text)
    assert again.to_text() == text
    assert "output " in text


def test_ml_wash_percent_template_divides_weighted_usd():
    graph = _load(ROOT / "examples" / "graphs" / "11-ml-wash-percent.json")
    text = compile_graph(graph)[1]
    assert "ml_score " in text
    assert "ml_weighted_usd over usd_value" in text
    assert "format percent" in text


def test_transfers_cannot_feed_the_ml_score():
    graph = {
        "nodes": [
            {"id": "t", "type": "source", "data": {"source": "transfers"}},
            {"id": "m", "type": "ml_score", "data": {}},
            {"id": "o", "type": "output_table", "data": {"title": "nope"}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "m", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "e2", "source": "m", "target": "o", "sourceHandle": "out", "targetHandle": "in"},
        ],
    }
    errors = validate_graph(graph)
    assert any("Cannot connect" in error.message for error in errors)


def test_wash_percent_template_uses_general_boxes():
    graph = _load(ROOT / "examples" / "graphs" / "01-wash-percent.json")
    text = compile_graph(graph)[1]
    assert "detect_cycles" in text
    assert "ratio " in text
    assert "is_wash = true" in text
    assert "format percent" in text
    # The answer is assembled. There is no wash_percent box in the graph.
    assert all(node["type"] != "wash_percent" for node in graph["nodes"])


def test_incompatible_sockets_are_named():
    graph = {
        "nodes": [
            {"id": "h", "type": "source", "data": {"source": "holders"}},
            {"id": "d", "type": "detect_cycles", "data": {}},
            {"id": "t", "type": "source", "data": {"source": "transfers"}},
            {"id": "o", "type": "output_table", "data": {"title": "x"}},
        ],
        "edges": [
            {"id": "e1", "source": "h", "target": "d", "sourceHandle": "out", "targetHandle": "in_swaps"},
            {"id": "e2", "source": "t", "target": "d", "sourceHandle": "out", "targetHandle": "in_transfers"},
            {"id": "e3", "source": "d", "target": "o", "sourceHandle": "out_cycles", "targetHandle": "in"},
        ],
    }
    errors = validate_graph(graph)
    assert any("Cannot connect" in error.message and "holders" in error.message for error in errors)
    with pytest.raises(GraphCompileError):
        compile_graph(graph)


def test_graph_loops_are_rejected():
    graph = {
        "nodes": [
            {"id": "a", "type": "condition", "data": {"mode": "compare", "field": "balance", "op": ">", "value": 0}},
            {"id": "b", "type": "sort", "data": {"field": "balance", "direction": "desc"}},
        ],
        "edges": [
            {"id": "e1", "source": "a", "target": "b", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "e2", "source": "b", "target": "a", "sourceHandle": "out", "targetHandle": "in"},
        ],
    }
    errors = validate_graph(graph)
    assert any(error.code == "cycle" for error in errors)
    assert any("Cycle detector" in error.message for error in errors)


def test_bad_field_names_the_available_columns():
    graph = {
        "nodes": [
            {"id": "h", "type": "source", "data": {"source": "holders"}},
            {
                "id": "f",
                "type": "condition",
                "data": {"mode": "compare", "field": "confidence", "op": ">", "value": 0.8},
            },
            {"id": "o", "type": "output_table", "data": {"title": "nope"}},
        ],
        "edges": [
            {"id": "e1", "source": "h", "target": "f", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "e2", "source": "f", "target": "o", "sourceHandle": "out", "targetHandle": "in"},
        ],
    }
    errors = validate_graph(graph)
    assert any("confidence" in error.message and "balance" in error.message for error in errors)


def test_sold_in_is_rejected_on_swaps():
    graph = {
        "nodes": [
            {"id": "s", "type": "source", "data": {"source": "swaps"}},
            {"id": "f", "type": "condition", "data": {"mode": "sold_in", "days": 30, "negate": True}},
            {"id": "o", "type": "output_table", "data": {"title": "nope"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "f", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "e2", "source": "f", "target": "o", "sourceHandle": "out", "targetHandle": "in"},
        ],
    }
    errors = validate_graph(graph)
    assert any("sold_in" in error.message for error in errors)

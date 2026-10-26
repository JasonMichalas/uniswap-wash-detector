"""Train/test is by token, and planted shapes on the held-out token are scored."""

from washdetect.dataset import assemble_from_events
from washdetect.detector import SwapEvent
from washdetect.features import RULE_OUTPUT_COLUMNS
from washdetect.train import split_token_ids, train_tables


def _token(index: int) -> object:
    swaps = []
    for k in range(20):
        swaps.append(
            SwapEvent(f"0xorg{index}_{k}", 1_000 + k * 30, "buy", 5 + k, 5 + k, f"0xorgtx{index}_{k}", 0)
        )
    swaps.append(SwapEvent(f"0xrt{index}", 800, "buy", 12, 12, f"0xrtb{index}", 0))
    swaps.append(SwapEvent(f"0xrt{index}", 900, "sell", 12, 12, f"0xrts{index}", 0))
    return assemble_from_events(f"0x{index:040x}", f"T{index}", swaps, [], seed=100 + index)


def test_split_keeps_tokens_disjoint():
    split = split_token_ids([f"0x{i:040x}" for i in range(8)], seed=1)
    assert set(split["train"]).isdisjoint(split["test"])
    assert set(split["train"]).isdisjoint(split["calib"])
    assert set(split["calib"]).isdisjoint(split["test"])
    assert split["train"] and split["test"]


def test_training_reports_planted_recall_by_pattern(tmp_path):
    tables = [_token(i) for i in range(4)]
    _bundle, report = train_tables(
        tables,
        seed=0,
        model_dir=tmp_path / "model",
        report_path=tmp_path / "evaluation.md",
        figure_dir=tmp_path / "figures",
        window_note="Window: synthetic fixture, four tokens, no chain fetch.",
    )
    text = (tmp_path / "evaluation.md").read_text(encoding="utf-8")
    assert "Headline results" in text
    assert "many_to_one" in text
    assert "n_buyers" in text
    assert (tmp_path / "figures" / "pr_curve.png").is_file()
    assert (tmp_path / "figures" / "recall_by_pattern.png").is_file()
    assert set(RULE_OUTPUT_COLUMNS).isdisjoint(_bundle.trade_xgb.columns)
    # The planted-swap recall line is the first data row of the headline table.
    assert "Swap model" in text

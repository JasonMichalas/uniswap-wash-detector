from __future__ import annotations

from pathlib import Path

import pandas as pd

from washdetect.models import probability_weighted_wash_percent


def binary_metrics(labels, scores, *, threshold: float = 0.5) -> dict[str, float]:
    import numpy as np
    from sklearn.metrics import average_precision_score

    y = np.asarray(list(labels), dtype=int)
    p = np.asarray(list(scores), dtype=float)
    if len(y) == 0:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "pr_auc": float("nan"), "tp": 0, "fp": 0, "fn": 0, "n": 0, "n_pos": 0}
    pred = p >= threshold
    tp = int(np.sum(pred & (y == 1)))
    fp = int(np.sum(pred & (y == 0)))
    fn = int(np.sum(~pred & (y == 1)))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    if y.sum() == 0 or y.sum() == len(y):
        pr_auc = float("nan")
    else:
        pr_auc = float(average_precision_score(y, p))
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "pr_auc": pr_auc,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "n": int(len(y)),
        "n_pos": int(y.sum()),
    }


def rule_metric_table(frame: pd.DataFrame, *, score: str = "ml_score") -> pd.DataFrame:
    """Precision, recall, and F1 per planted rule on held-out swaps.

    Other rules' plants are left out of the negatives, so an R2 plant is not a
    false positive for R1. Real swaps are the negatives.
    """
    columns = ["rule", "n", "precision", "recall", "f1"]
    if frame.empty or "rule" not in frame.columns or "injected" not in frame.columns:
        return pd.DataFrame(columns=columns)
    real = frame[frame["injected"] == 0]
    planted = frame[frame["injected"] == 1]
    rows = []
    for rule, part in planted.groupby("rule", dropna=False):
        if not str(rule):
            continue
        scored = pd.concat([part, real], ignore_index=True)
        metrics = binary_metrics(scored["injected"], scored[score])
        rows.append(
            {
                "rule": rule,
                "n": int(metrics["n_pos"]),
                "precision": metrics["precision"],
                "recall": metrics["recall"],
                "f1": metrics["f1"],
            }
        )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows).sort_values("rule").reset_index(drop=True)


def recall_table(frame: pd.DataFrame, keys: list[str], *, score: str = "ml_score") -> pd.DataFrame:
    planted = frame[frame["injected"] == 1]
    if planted.empty:
        return pd.DataFrame(columns=[*keys, "n", "recall"])
    rows = []
    for values, part in planted.groupby(keys, dropna=False):
        if not isinstance(values, tuple):
            values = (values,)
        caught = float((part[score] >= 0.5).mean()) if len(part) else 0.0
        row = dict(zip(keys, values, strict=True))
        row["n"] = int(len(part))
        row["recall"] = caught
        rows.append(row)
    out = pd.DataFrame(rows)
    return out.sort_values(keys).reset_index(drop=True)


def group_recall(trades: pd.DataFrame, *, score: str = "ml_score") -> pd.DataFrame:
    planted = trades[trades["injected"] == 1]
    if planted.empty:
        return pd.DataFrame(columns=["pattern_type", "n_buyers", "n_sellers", "n_wallets", "n_groups", "recall"])
    keys = ["token", "group_id", "pattern_type", "n_buyers", "n_sellers", "n_wallets"]
    grouped = (
        planted.groupby(keys, dropna=False)[score]
        .apply(lambda series: float((series >= 0.5).mean()))
        .reset_index(name="swap_recall")
    )
    grouped["caught"] = (grouped["swap_recall"] >= 0.5).astype(int)
    rows = []
    for values, part in grouped.groupby(["pattern_type", "n_buyers", "n_sellers", "n_wallets"]):
        rows.append(
            {
                "pattern_type": values[0],
                "n_buyers": int(values[1]),
                "n_sellers": int(values[2]),
                "n_wallets": int(values[3]),
                "n_groups": int(len(part)),
                "recall": float(part["caught"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["pattern_type", "n_buyers", "n_sellers"]).reset_index(drop=True)


def write_plots(
    trades: pd.DataFrame,
    pattern_recall: pd.DataFrame,
    comparison: pd.DataFrame,
    directory: Path,
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from sklearn.metrics import precision_recall_curve

    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    planted_task = trades.copy()
    y = planted_task["injected"].astype(int).to_numpy()
    p = planted_task["ml_score"].astype(float).to_numpy()
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    if y.sum() and y.sum() < len(y):
        precision, recall, _ = precision_recall_curve(y, p)
        ax.plot(recall, precision, color="#0f6e56", lw=2)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.02)
    ax.set_xlabel("Recall on planted swaps")
    ax.set_ylabel("Precision")
    ax.set_title("Test tokens: planted wash vs everything else")
    fig.tight_layout()
    path = directory / "pr_curve.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    written.append(path)

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    if not pattern_recall.empty:
        order = pattern_recall.sort_values("recall")
        ax.barh(order["pattern_type"].astype(str), order["recall"], color="#1d4e89")
        ax.set_xlim(0, 1)
    ax.set_xlabel("Swap recall at probability 0.5")
    ax.set_title("Planted-swap recall by pattern")
    fig.tight_layout()
    path = directory / "recall_by_pattern.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    written.append(path)

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    if not comparison.empty:
        labels = comparison["symbol"].astype(str).tolist()
        x = np.arange(len(labels))
        width = 0.38
        ax.bar(x - width / 2, comparison["rule_wash_pct"], width, label="Rule", color="#8a8f98")
        ax.bar(x + width / 2, comparison["ml_wash_pct"], width, label="ML", color="#0f6e56")
        ax.set_xticks(x, labels, rotation=45, ha="right")
        ax.legend(frameon=False)
    ax.set_ylabel("Wash volume %")
    ax.set_title("Rule vs probability-weighted ML wash %")
    fig.tight_layout()
    path = directory / "wash_percent_comparison.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    written.append(path)
    return written


def _pct(value: float) -> str:
    if value != value:  # NaN
        return "n/a"
    return f"{100 * value:.1f}%"


def _num(value: float, digits: int = 1) -> str:
    if value != value:
        return "n/a"
    return f"{value:.{digits}f}"


def render_report(
    *,
    headline: dict[str, float],
    wallet_headline: dict[str, float],
    agreement: dict[str, float],
    pattern_recall: pd.DataFrame,
    pair_recall: pd.DataFrame,
    group_level: pd.DataFrame,
    rule_model: pd.DataFrame | None = None,
    rule_detector: pd.DataFrame | None = None,
    comparison: pd.DataFrame,
    split: dict[str, list[str]],
    tokens: pd.DataFrame,
    feature_note: str,
    window_note: str,
) -> str:
    """Planted-task precision counts real wash as a false positive; rule agreement is separate."""
    lines = [
        "# Evaluation: wash models",
        "",
        "Milestone 2 of the Uniswap Wash Detector (ECM3401, University of Exeter). "
        "There is no public ground truth, so the supervised numbers below are recall "
        "and precision on wash groups planted into real pool history, with train and "
        "test split by token.",
        "",
        "## Dataset",
        "",
        window_note,
        "",
        "| Symbol | Token | Real swaps | Real transfers | Fetch s | Split |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in tokens.itertuples(index=False):
        lines.append(
            f"| {row.symbol} | `{row.token}` | {int(row.n_real_swaps)} | {int(row.n_real_transfers)} | "
            f"{float(row.seconds):.1f} | {row.split} |"
        )
    lines += [
        "",
        f"Train tokens: {len(split['train'])}. Calibration tokens: {len(split['calib'])}. "
        f"Test tokens: {len(split['test'])}.",
        "",
        "Planted groups (kept separate from the chain cache, addresses start with `0x57`) "
        "cover every rule: a same-wallet round trip (R1), a partial lot and fan-in / fan-out / "
        "many-to-many flows (R2), a 16-wallet chain and a 21-wallet chain, a fan-in whose sell "
        "is 3,000 blocks later, a shared funder (R3), rings (R4), a wallet that churns flat (R5), "
        "and a buy and a sell in one transaction (R6). A negative decoy, two strangers buying 2 "
        "and selling 2 about 40 blocks apart, is inserted and labelled not-wash. "
        "Group sizes run from 1 to 21 wallets.",
        "",
        "## Features",
        "",
        feature_note,
        "",
        "## Headline results (test tokens, planted swaps)",
        "",
        "Positive class: a swap we inserted. Threshold 0.5 on the calibrated probability. "
        "PR-AUC is average precision over that ranking.",
        "",
        "| | Precision | Recall | F1 | PR-AUC | Support |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Swap model | {_pct(headline['precision'])} | {_pct(headline['recall'])} | {_pct(headline['f1'])} | "
        f"{_num(headline['pr_auc'], 3)} | {int(headline['n_pos'])} / {int(headline['n'])} |",
        f"| Wallet model | {_pct(wallet_headline['precision'])} | {_pct(wallet_headline['recall'])} | "
        f"{_pct(wallet_headline['f1'])} | {_num(wallet_headline['pr_auc'], 3)} | "
        f"{int(wallet_headline['n_pos'])} / {int(wallet_headline['n'])} |",
        "",
        "A real wash swap that the model scores above 0.5 is a false positive on this "
        "planted-only task. Agreement with the rule, on real (non-planted) test swaps, "
        "is the right place to read that:",
        "",
        f"Rule agreement on real test swaps: precision {_pct(agreement['precision'])}, "
        f"recall {_pct(agreement['recall'])}, F1 {_pct(agreement['f1'])} "
        f"(the rule's hard label is the reference).",
        "",
        "![Precision-recall on planted test swaps](figures/pr_curve.png)",
        "",
        "## Recall by pattern",
        "",
        "Swap-level recall: fraction of planted swaps of that pattern with probability ≥ 0.5.",
        "",
        _md_table(pattern_recall, percent={"recall"}),
        "",
        "![Recall by pattern](figures/recall_by_pattern.png)",
        "",
        "## Recall by rule",
        "",
        "Model precision, recall, and F1 are on planted test swaps of that rule. "
        "Other rules' plants are left out of that rule's negatives. Real test swaps are the negatives. "
        "Detector recall is the fraction of planted groups on the test tokens whose swaps "
        "the rule labelled with that rule id. A 21-wallet chain and the 3,000-block fan-in are "
        "scored with a 8,000-block gap and a 32-wallet cap. The other plants use the 200-block, "
        "16-wallet default.",
        "",
        "### Model, planted test swaps",
        "",
        _md_table(
            rule_model if rule_model is not None else pd.DataFrame(),
            percent={"precision", "recall", "f1"},
        ),
        "",
        "### Detector, planted groups on test tokens",
        "",
        _md_table(rule_detector if rule_detector is not None else pd.DataFrame(), percent={"recall"}),
        "",
        "The previous published run was 22 tokens, blocks 26,264,761–26,279,564 "
        "(2026-10-24 20:00 through 2026-10-26 21:30 UTC): 20,758 real swaps and 145,308 transfers. "
        "Swap model precision 91.9%, recall 100%, F1 95.8%, PR-AUC 1.000 (784 / 5219). "
        "Wallet model precision 93.7%, recall 100%, F1 96.7%, PR-AUC 1.000 (714 / 2003). "
        "Rule agreement on real test swaps precision 95.7%, recall 29.2%, F1 44.7%. "
        "Model recall by planted rule was 100% for R1 (14), R2 (658), R3 (14), R4 (56), R5 (28), and R6 (14). "
        "Detector group recall was 100% for R1 (22), R2 (374), R3 (22), R4 (88), R5 (22), and R6 (22). "
        "That run did not report precision or F1 per rule.",
        "",
        "## Recall by buyers × sellers",
        "",
        "Same swap-level recall, split by the planted group's `n_buyers` and `n_sellers`.",
        "",
        _md_table(pair_recall, percent={"recall"}),
        "",
        "Group-level recall: a planted group counts as caught when at least half of its "
        "swaps score ≥ 0.5.",
        "",
        _md_table(group_level, percent={"recall"}),
        "",
        "## Wash percent: rule vs model",
        "",
        "Rule % is the Gan-style hard label on the real swaps only (plants removed). "
        "ML % is `100 * sum(p_i v_i) / sum(v_i)` on those same swaps, with a percentile "
        "bootstrap 95% interval. Test-token rows are the ones the model did not train on.",
        "",
        _comparison_table(comparison),
        "",
        "![Rule vs ML wash percent](figures/wash_percent_comparison.png)",
        "",
        "## Limitations",
        "",
        "- These precision, recall, and F1 figures are optimistic. The positive class is "
        "synthetic wash planted by the same rules the detector implements, on tokens the "
        "model did not train on. A high score means the model recognised that generator, "
        "not that the same score would hold for wash that was never planted.",
        "- The plants are drawn from one generator. High recall means the model recognised "
        "that generator on a new token, not that it has seen every real wash ring.",
        "- Real wash in a test token lowers planted-task precision when the model is right "
        "about it. Read the rule-agreement line next to the headline.",
        "- The rule and the model share the same block window and tolerance family. "
        "They can agree because the behaviour is obvious (a buyer who forwards the tokens), "
        "not because the model was shown the rule's output column.",
        "- USD uses one end-of-window ETH price for WETH legs. The public-RPC window "
        "is not the token's life. `held_days` and quiet wallets outside the window are invisible.",
        "- Isolation Forest is fit on non-planted training rows. It is an anomaly rank, "
        "not a calibrated probability.",
        "- SHAP explains the raw XGBoost margin. Platt scaling is monotone, so the sign "
        "of a reason is the same after calibration, but the probability itself is not a SHAP unit.",
        "",
    ]
    return "\n".join(lines)


def _md_table(frame: pd.DataFrame, *, percent: set[str]) -> str:
    if frame is None or frame.empty:
        return "_No rows._"
    columns = list(frame.columns)
    header = "| " + " | ".join(columns) + " |"
    align = "| " + " | ".join("---:" if col in percent or col.startswith("n_") or col in {"n", "recall"} else "---" for col in columns) + " |"
    body = []
    for row in frame.itertuples(index=False):
        cells = []
        for col, value in zip(columns, row, strict=True):
            if col in percent:
                cells.append(_pct(float(value)))
            elif isinstance(value, float):
                cells.append(_num(value, 3))
            else:
                cells.append(str(value))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, align, *body])


def _comparison_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No tokens._"
    lines = [
        "| Symbol | Split | Rule % | ML % | ML 95% CI | Real swaps |",
        "|---|---|---:|---:|---|---:|",
    ]
    for row in frame.itertuples(index=False):
        lines.append(
            f"| {row.symbol} | {row.split} | {_num(row.rule_wash_pct)} | {_num(row.ml_wash_pct)} | "
            f"[{_num(row.ml_ci_low)}, {_num(row.ml_ci_high)}] | {int(row.n_real_swaps)} |"
        )
    return "\n".join(lines)


def wash_percent_row(symbol: str, split: str, usd, rule_flags, probabilities) -> dict:
    rule = probability_weighted_wash_percent(usd, rule_flags, n_bootstrap=400, seed=1)
    ml = probability_weighted_wash_percent(usd, probabilities, n_bootstrap=400, seed=2)
    return {
        "symbol": symbol,
        "split": split,
        "rule_wash_pct": rule["estimate"],
        "ml_wash_pct": ml["estimate"],
        "ml_ci_low": ml["ci_low"],
        "ml_ci_high": ml["ci_high"],
        "n_real_swaps": rule["n"],
    }

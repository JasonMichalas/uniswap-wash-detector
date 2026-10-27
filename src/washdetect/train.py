from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

from washdetect.dataset import TokenTables
from washdetect.evaluate import (
    binary_metrics,
    group_recall,
    recall_table,
    render_report,
    wash_percent_row,
    write_plots,
)
from washdetect.features import (
    MODEL_FEATURE_COLUMNS,
    RULE_OUTPUT_COLUMNS,
    TRADE_FEATURE_COLUMNS,
    build_trade_frame,
)
from washdetect.models import ModelBundle, WashModel

MAX_NEGATIVE_WALLETS = 1_500
MAX_NEGATIVE_TRADES = 2_500

FEATURE_NOTE = (
    "XGBoost and Isolation Forest see behavioural columns only: swap counts, "
    "log amounts, net position over the whole window, inter-swap timing and "
    "burstiness, pool count, counterparty degree, shared-funder degree, and "
    "the fraction of bought tokens sent onward (or sold tokens that arrived "
    "by transfer). They do not see `round_trip_count`, `wash_volume_usd`, "
    "`heuristic_label`, `heuristic_confidence`, `pattern_type`, or the "
    "group's buyer and seller counts. Those are labels or rule outputs. "
    f"Wallet columns: {', '.join(MODEL_FEATURE_COLUMNS)}. "
    f"Swap columns add the swap's own size and the same wallet context. "
    "A planted row's label is 1 even when the rule misses it; a real row's "
    "label is the rule's weak label."
)


def split_token_ids(token_ids: list[str], seed: int = 0) -> dict[str, list[str]]:
    ordered = sorted({token.lower() for token in token_ids})
    if len(ordered) < 3:
        raise ValueError(
            "Need at least 3 tokens so the test token is not also a training token."
        )
    rng = random.Random(seed)
    rng.shuffle(ordered)
    n_test = max(1, round(len(ordered) * 0.30))
    n_test = min(n_test, len(ordered) - 2)
    test = sorted(ordered[:n_test])
    rest = ordered[n_test:]
    calib: list[str] = []
    if len(rest) >= 3:
        calib = [rest[0]]
        train = sorted(rest[1:])
    else:
        train = sorted(rest)
    return {"train": train, "calib": calib, "test": test}


def _downsample(frame: pd.DataFrame, cap: int, seed: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    positive = frame[frame["label"] == 1]
    negative = frame[frame["label"] == 0]
    if len(negative) > cap:
        negative = negative.sample(cap, random_state=seed)
    return pd.concat([positive, negative], ignore_index=True)


def _detector_rule_table(tables: list[TokenTables]) -> pd.DataFrame:
    frames = [table.groups for table in tables if not table.groups.empty and "rule" in table.groups.columns]
    if not frames:
        return pd.DataFrame(columns=["rule", "n", "recall"])
    groups = pd.concat(frames, ignore_index=True)
    groups = groups[groups["rule"].astype(str) != ""]
    if groups.empty:
        return pd.DataFrame(columns=["rule", "n", "recall"])
    rows = []
    for rule, part in groups.groupby("rule"):
        rows.append({"rule": rule, "n": int(len(part)), "recall": float(part["rule_hit"].mean())})
    return pd.DataFrame(rows).sort_values("rule").reset_index(drop=True)


def _concat(tables: list[TokenTables], attr: str) -> pd.DataFrame:
    frames = [getattr(table, attr) for table in tables if not getattr(table, attr).empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def train_tables(
    tables: list[TokenTables],
    *,
    seed: int = 0,
    model_dir: str | Path | None = None,
    report_path: str | Path | None = None,
    figure_dir: str | Path | None = None,
    window_note: str = "",
) -> tuple[ModelBundle, str]:
    """Does not touch the network. Train, calibration, and test are disjoint tokens."""
    by_id = {table.token.lower(): table for table in tables}
    split = split_token_ids(list(by_id), seed)
    train = [by_id[token] for token in split["train"]]
    calib = [by_id[token] for token in split["calib"]]
    test = [by_id[token] for token in split["test"]]

    wallet_fit = _downsample(_concat(train, "wallets"), MAX_NEGATIVE_WALLETS * max(1, len(train)), seed)
    trade_fit = _downsample(_concat(train, "trades"), MAX_NEGATIVE_TRADES * max(1, len(train)), seed + 1)
    wallet_fit = _downsample(wallet_fit, MAX_NEGATIVE_WALLETS * max(1, len(train)), seed)
    trade_fit = _downsample(trade_fit, MAX_NEGATIVE_TRADES * max(1, len(train)), seed + 1)

    wallet_normal = _concat(train, "wallets")
    wallet_normal = wallet_normal[wallet_normal["injected"] == 0]
    trade_normal = _concat(train, "trades")
    trade_normal = trade_normal[trade_normal["injected"] == 0]
    if len(wallet_normal) < 8:
        wallet_normal = wallet_fit
    if len(trade_normal) < 8:
        trade_normal = trade_fit
    model = WashModel()
    model.train(
        wallet_fit,
        trade_fit,
        wallet_normal,
        trade_normal,
        wallet_calib=_concat(calib, "wallets") if calib else None,
        trade_calib=_concat(calib, "trades") if calib else None,
    )
    bundle = model.bundle

    test_trades = model.score(_concat(test, "trades"), kind="trade")
    test_wallets = model.score(_concat(test, "wallets"), kind="wallet")

    headline = binary_metrics(test_trades["injected"], test_trades["ml_score"])
    wallet_headline = binary_metrics(test_wallets["injected"], test_wallets["ml_score"])
    real_trades = test_trades[test_trades["injected"] == 0]
    agreement = binary_metrics(real_trades["heuristic_label"], real_trades["ml_score"]) if not real_trades.empty else binary_metrics([], [])

    pattern_recall = recall_table(test_trades, ["pattern_type"])
    pair_recall = recall_table(test_trades, ["n_buyers", "n_sellers"])
    rule_model = recall_table(test_trades, ["rule"]) if "rule" in test_trades.columns else pd.DataFrame()
    groups = group_recall(test_trades)
    rule_detector = _detector_rule_table(tables)

    role = {}
    for name, ids in split.items():
        for token in ids:
            role[token] = name
    comparison_rows = []
    summary_rows = []
    for table in tables:
        which = role[table.token]
        summary_rows.append(
            {
                "symbol": table.symbol,
                "token": table.token,
                "n_real_swaps": table.n_real_swaps,
                "n_real_transfers": table.n_real_transfers,
                "seconds": table.seconds,
                "split": which,
            }
        )
        comparison_rows.append(_real_wash_row(table, which, bundle))
    comparison = pd.DataFrame(comparison_rows)
    summary = pd.DataFrame(summary_rows).sort_values(["split", "symbol"])

    banned = set(RULE_OUTPUT_COLUMNS) & set(MODEL_FEATURE_COLUMNS + TRADE_FEATURE_COLUMNS)
    if banned:
        raise RuntimeError(f"rule outputs leaked into model features: {sorted(banned)}")

    report = render_report(
        headline=headline,
        wallet_headline=wallet_headline,
        agreement=agreement,
        pattern_recall=pattern_recall,
        pair_recall=pair_recall,
        group_level=groups,
        rule_model=rule_model,
        rule_detector=rule_detector,
        comparison=comparison,
        split=split,
        tokens=summary,
        feature_note=FEATURE_NOTE,
        window_note=window_note or "Window: see the token table.",
    )

    if figure_dir is not None:
        write_plots(test_trades, pattern_recall, comparison, Path(figure_dir))
    if report_path is not None:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
    bundle.manifest = {
        "seed": seed,
        "split": split,
        "swap_pr_auc": headline["pr_auc"],
        "swap_recall": headline["recall"],
        "swap_precision": headline["precision"],
        "swap_f1": headline["f1"],
    }
    if model_dir is not None:
        bundle.save(model_dir)
    return bundle, report


def _real_wash_row(table: TokenTables, split: str, bundle: ModelBundle) -> dict:
    trades = build_trade_frame(table.real_swaps, table.real_transfers, detection=table.real_detection)
    if trades.empty:
        return {
            "symbol": table.symbol,
            "split": split,
            "rule_wash_pct": 0.0,
            "ml_wash_pct": 0.0,
            "ml_ci_low": 0.0,
            "ml_ci_high": 0.0,
            "n_real_swaps": 0,
        }
    probabilities = bundle.trade_xgb.predict_proba(trades).tolist()
    return wash_percent_row(
        table.symbol,
        split,
        trades["usd"].tolist(),
        trades["heuristic_label"].tolist(),
        probabilities,
    )

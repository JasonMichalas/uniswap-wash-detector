"""Feature ablation: base, +batch1, +batch2 on the same token split and settings, scored on held-out tokens.

usage: ablate.py HANDCHECK_DIR NEXF_DB TRAIN_TOKENS TRAIN_DB_DIR OLD_DIR OUT_DIR
Saves each model in OUT_DIR/<set>, the results in OUT_DIR/ablation.json, and mean |SHAP| in OUT_DIR/shap.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import real_eval

from washdetect.models import WashModel
from washdetect.weakmodel import WALLET_SETS, WeakWashModel, frames_from_db, split_tokens


def mean_shap(booster, frame: pd.DataFrame) -> pd.Series:
    from xgboost import DMatrix

    contrib = booster.model.get_booster().predict(DMatrix(booster.matrix(frame)), pred_contribs=True)[:, :-1]
    return pd.Series(np.abs(contrib).mean(axis=0), index=booster.columns).sort_values(ascending=False)


def main(hc: str, nexf_db: str, train_list: str, train_dir: str, old_dir: str, out_dir: str) -> None:
    out = Path(out_dir)
    lines = [line.split() for line in open(train_list) if line.strip()]
    for name, token in lines:
        real_eval.FRAMES[name] = frames_from_db(name, f"{train_dir}/{name}.duckdb", token.lower())
    train, calib = split_tokens([n for n, _ in lines])
    old = WashModel.load(old_dir)
    results, shap = {}, {}
    for level in WALLET_SETS:
        model = WeakWashModel(level)
        model.train([real_eval.FRAMES[n] for n in train], [real_eval.FRAMES[n] for n in calib])
        model.save(out / level)
        results[level] = real_eval.evaluate(model, old, hc, nexf_db, train_list, train_dir)
        train_wallets = pd.concat([real_eval.FRAMES[n].wallets for n in train], ignore_index=True)
        train_trades = pd.concat([real_eval.FRAMES[n].trades for n in train], ignore_index=True)
        shap[level] = {
            "wallet": mean_shap(model.wallet, train_wallets).round(4).to_dict(),
            "trade": mean_shap(model.trade, train_trades).round(4).to_dict(),
        }
        print(level, "done", flush=True)
    (out / "ablation.json").write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    (out / "shap.json").write_text(json.dumps(shap, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:])

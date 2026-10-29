"""Held-out evaluation on real data: NexFundAI CLS wallets and the hand-checked cases.

usage: real_eval.py HANDCHECK_DIR NEXF_DB TRAIN_TOKENS TRAIN_DB_DIR WEAK_DIR OLD_DIR [OUT_JSON]
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import average_precision_score

from washdetect.baselines import size_tests, zero_net_sets
from washdetect.config import hop_addresses
from washdetect.dataset import events_from_frames
from washdetect.events import Detection
from washdetect.features import build_feature_frame, build_trade_frame
from washdetect.models import WashModel
from washdetect.store import Store
from washdetect.weak import RULES, VOTERS
from washdetect.weakmodel import TokenFrames, WeakWashModel, frames_from_db

HERE = Path(__file__).resolve().parent
NEXF = "0x16ca471ae755f8a2cd4ec315a4a7439dcfebe54c"
NEXF_POOL = "0x19e7fe8cc2e813589ce70cf48207e0d50a911d7a"
HANDCHECK = {
    "mog": "0xaaee1a9723aadb7afa2810263653a34ba2c21c7a",
    "neiro": "0x812ba41e071c7b7fa4ebcfb62df5f45f6fa853ee",
    "pepe": "0x6982508145454ce325ddbe47a25d4ec3d2311933",
    "rlusd": "0x8292bb45bf1ee4d140127049757c2e0ff06317ed",
    "spx": "0xe0f63a424a4439cbe457d80e4f4b51ad25b2c56c",
    "turbo": "0xa35923162c49cf95e6bf26623385eb431ad920d3",
}
METHODS = ["rules", "label_model", "b25", "vw", "old_xgb", "weak_xgb"]
POS_USER = {"volume_bot", "mm_bot", "retail_roundtrip", "active_trader"}
B = 1000
CUTS: dict[str, float] = {}  # weak-model cut-offs picked on the calibration tokens
FRAMES: dict[str, TokenFrames] = {}  # built once, reused when several models are compared
CACHED_ONLY = False  # funding from the Blockscout cache only; unlooked-up wallets stay missing


class Token:
    """Every method's score for each swap and wallet of one token."""

    def __init__(self, name: str, db: str, token: str, weak: WeakWashModel, old: WashModel | None) -> None:
        store = Store(db)
        lo, hi = store.con.execute("SELECT min(block_number), max(block_number) FROM swaps WHERE token = ?", [token]).fetchone()
        self.pools = {str(p["pool_address"]).lower() for p in store.pools_for(token)}
        self.swaps = store.read_swaps(token, lo, hi)
        self.transfers = store.read_transfers(token, lo, hi)
        if name not in FRAMES:
            FRAMES[name] = frames_from_db(name, db, token, cached_only=CACHED_ONLY)
        frames = FRAMES[name]
        frames.trades = frames.trades.copy()
        events, transfer_events = events_from_frames(self.swaps, self.transfers)
        for e in events:
            e.wallet, e.tx_hash, e.pool = e.wallet.lower(), e.tx_hash.lower(), e.pool.lower()
        for t in transfer_events:
            t.src, t.dst, t.tx_hash = t.src.lower(), t.dst.lower(), t.tx_hash.lower()
        self.events, self.transfer_events = events, transfer_events
        trades = frames.trades
        keys = list(zip(trades.tx_hash, trades.log_index, strict=False))
        vw = zero_net_sets(events, transfer_events, self.pools)
        trades["rules"] = (trades[RULES] == 1).any(axis=1).astype(float)
        trades["label_model"] = weak.trade_labels.predict_proba(trades[VOTERS])
        trades["b25"] = (trades["B25"] == 1).astype(float)
        trades["vw"] = [float(k in vw) for k in keys]
        trades["weak_xgb"] = weak.trade.proba(trades)
        trades["old_xgb"] = self._old(old, keys) if old else np.nan
        self.trades = trades
        wallets = trades.groupby("wallet")[METHODS].max()
        w = frames.wallets.set_index("wallet")
        wallets["label_model"] = weak.wallet_labels.predict_proba(w.loc[wallets.index, VOTERS])
        wallets["weak_xgb"] = weak.wallet.proba(w.loc[wallets.index])
        if old:
            wallets["old_xgb_wallet"] = self._old_wallets(old).reindex(wallets.index)
        self.wallets = wallets

    def _old(self, old: WashModel, keys: list) -> np.ndarray:
        excluded = self.pools | hop_addresses()
        frame = build_trade_frame(self.events, self.transfer_events, excluded=excluded, detection=Detection(cycles=[], wallets=[], flagged={}))
        scored = old.score(frame, kind="trade")
        lookup = {(str(a).lower(), int(b)): float(s) for a, b, s in zip(scored.tx_hash, scored.log_index, scored.ml_score, strict=False)}
        return np.array([lookup.get(k, np.nan) for k in keys])

    def _old_wallets(self, old: WashModel) -> pd.Series:
        excluded = self.pools | hop_addresses()
        frame = build_feature_frame(self.events, self.transfer_events, Detection(cycles=[], wallets=[], flagged={}), excluded=excluded)
        scored = old.score(frame, kind="wallet")
        return pd.Series(scored.ml_score.to_numpy(), index=scored.wallet.str.lower())

    def wash_share(self, column: str, cut: float = 0.5) -> float:
        usd = self.trades["usd"].fillna(0)
        return float((usd * (self.trades[column] >= cut)).sum() / max(usd.sum(), 1e-9))


def metrics(y: np.ndarray, s: np.ndarray, cut: float = 0.5) -> dict[str, float]:
    s = np.nan_to_num(s, nan=0.0)
    hit = s >= cut
    tp = float((hit & y).sum())
    out = {
        "precision": tp / hit.sum() if hit.sum() else np.nan,
        "recall": tp / y.sum() if y.sum() else np.nan,
    }
    out["f1"] = 2 * out["precision"] * out["recall"] / (out["precision"] + out["recall"]) if tp else 0.0
    out["ap"] = float(average_precision_score(y, s)) if 0 < y.sum() < len(y) else np.nan
    return out


def boot(y: np.ndarray, s: np.ndarray, groups: np.ndarray | None = None, cut: float = 0.5, seed: int = 0) -> dict[str, list[float]]:
    """Point estimate and 95% percentile interval; resamples whole groups (wallets) when given."""
    rng = np.random.default_rng(seed)
    point = metrics(y, s, cut)
    groups = np.arange(len(y)) if groups is None else groups
    ids = np.unique(groups)
    members = {g: np.flatnonzero(groups == g) for g in ids}
    draws = []
    for _ in range(B):
        idx = np.concatenate([members[g] for g in rng.choice(ids, len(ids))])
        draws.append(metrics(y[idx], s[idx], cut))
    table = pd.DataFrame(draws)
    return {k: [round(point[k], 3), *np.round(np.nanpercentile(table[k], [2.5, 97.5]), 3).tolist()] for k in point}


def scored(y: np.ndarray, frame: pd.DataFrame, columns: list[str], cuts: dict[str, float], groups: np.ndarray | None = None) -> dict:
    out = {}
    for m in columns:
        out[m] = boot(y, frame[m].to_numpy(), groups, cuts.get(m, 0.5))
        if m in cuts:
            out[f"{m}@0.5"] = boot(y, frame[m].to_numpy(), groups)
    return out


def nexf_eval(tok: Token, cls: set[str]) -> dict:
    gt_tx = {t.tx_hash for t in tok.transfer_events if (t.src == NEXF_POOL and t.dst in cls) or (t.dst == NEXF_POOL and t.src in cls)}
    trades, wallets = tok.trades, tok.wallets
    yt = trades.tx_hash.isin(gt_tx).to_numpy()
    yw = wallets.index.isin(cls)
    cols = [*METHODS, "old_xgb_wallet"]
    return {
        "wallets": int(len(yw)), "wallet_pos": int(yw.sum()), "trades": int(len(yt)), "trade_pos": int(yt.sum()),
        "wallet": scored(yw, wallets, [m for m in cols if m in wallets], {"weak_xgb": CUTS["wallet"]}),
        "trade": scored(yt, trades, METHODS, {"weak_xgb": CUTS["trade"]}, trades.wallet.to_numpy()),
    }


def handcheck_eval(tokens: dict[str, Token], hc: Path) -> dict:
    sys.path.insert(0, str(hc))
    from labels import L

    cases = {r["case"]: r for r in csv.DictReader(open(hc / "cases.csv"))}
    by_group = {r["group_id"]: k for k, r in cases.items() if k.startswith("G")}
    rows = []
    for g in json.load(open(hc / "sample_groups.json")):
        case = by_group.get(g["id"])
        if not case:
            continue
        tok = tokens[cases[case]["token"].lower()]
        keys = {(a.lower(), int(b)) for a, b in g["swaps"]}
        t = tok.trades[[k in keys for k in zip(tok.trades.tx_hash, tok.trades.log_index, strict=False)]]
        if t.empty:
            t = tok.trades[tok.trades.wallet.isin([w.lower() for w in g["wallets"]])]
        wallet_scores = tok.wallets.reindex([w.lower() for w in g["wallets"]])
        rows.append({"case": case, **t[METHODS].max().to_dict(), "weak_xgb_wallet": wallet_scores["weak_xgb"].max(), "old_xgb_wallet": wallet_scores.get("old_xgb_wallet", pd.Series([np.nan])).max()})
    for c in json.load(open(hc / "sample_controls.json")):
        tok = tokens[c["tok"]]
        w = c["wallet"].lower()
        t = tok.trades[tok.trades.wallet == w]
        rows.append({"case": c["case"], **t[METHODS].max().to_dict(), "weak_xgb_wallet": tok.wallets.weak_xgb.get(w, np.nan), "old_xgb_wallet": tok.wallets.get("old_xgb_wallet", pd.Series(dtype=float)).get(w, np.nan)})
    frame = pd.DataFrame(rows).set_index("case")
    label = {
        "strict": lambda c: L[c][0] in ("Clear wash", "Likely wash"),
        "user": lambda c: L[c][0] in ("Clear wash", "Likely wash") or (L[c][1] in POS_USER and L[c][0] != "Not wash") or (L[c][1] == "mm_bot" and c.startswith("G")),
    }
    out = {"cases": int(len(frame))}
    for name, fn in label.items():
        y = np.array([fn(c) for c in frame.index])
        cuts = {"weak_xgb": CUTS["trade"], "weak_xgb_wallet": CUTS["wallet"]}
        out[name] = {"pos": int(y.sum()), **scored(y, frame, list(frame.columns), cuts)}
    return out


def token_tests(tokens: dict[str, Token], held_out: list[str]) -> dict:
    rows = []
    for name, tok in tokens.items():
        tests = size_tests(tok.swaps["amount_token"].astype(float).tolist())
        rows.append({"token": name, "held_out": name in held_out, "rules_pct": tok.wash_share("rules"), "weak_pct": tok.wash_share("weak_xgb", CUTS["trade"]), **tests})
    frame = pd.DataFrame(rows)
    rng = np.random.default_rng(0)
    corr = {}
    for pct, sub in (("rules_pct", frame), ("weak_pct", frame[frame.held_out])):
        for test in ("benford_mad", "round_ratio", "tail_alpha"):
            d = sub[[pct, test]].dropna()
            rho = spearmanr(d[pct], d[test]).statistic
            draws = [spearmanr(*d.iloc[rng.integers(0, len(d), len(d))].T.values).statistic for _ in range(B)]
            corr[f"{pct}~{test}"] = [round(float(rho), 3), *np.round(np.nanpercentile(draws, [2.5, 97.5]), 3).tolist(), len(d)]
    return {"tokens": frame.round(4).to_dict(orient="records"), "spearman": corr}


def main(hc: str, nexf_db: str, train_list: str, train_dir: str, weak_dir: str, old_dir: str, out: str = "real_eval.json") -> None:
    result = evaluate(WeakWashModel.load(weak_dir), WashModel.load(old_dir), hc, nexf_db, train_list, train_dir)
    Path(out).write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")
    print(json.dumps(result, indent=1, default=float))


def evaluate(weak: WeakWashModel, old: WashModel | None, hc: str, nexf_db: str, train_list: str, train_dir: str) -> dict:
    CUTS.update(wallet=weak.wallet.threshold, trade=weak.trade.threshold)
    tokens = {n: Token(n, f"{hc}/{n}.duckdb", a, weak, old) for n, a in HANDCHECK.items()}
    tokens["nexf"] = Token("nexf", nexf_db, NEXF, weak, old)
    for line in open(train_list):
        n, a = line.split()
        tokens[n] = Token(n, f"{train_dir}/{n}.duckdb", a.lower(), weak, None)
    cls = {r["address"].lower() for r in csv.DictReader(open(HERE / "nexf_cls_wallets.csv"))}
    return {
        "model": weak.manifest,
        "nexf": nexf_eval(tokens["nexf"], cls),
        "handcheck": handcheck_eval(tokens, Path(hc)),
        "token_tests": token_tests(tokens, [*HANDCHECK, "nexf"]),
    }


if __name__ == "__main__":
    main(*sys.argv[1:])

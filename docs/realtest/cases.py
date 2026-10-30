"""Blind test on confirmed wash cases: the shipped rules and model, run with app defaults, scored afterwards.

usage: cases.py CASE=DB [CASE=DB ...]   (cases and wallets from confirmed_cases.csv)
A case swap is a swap in a transaction that moves the token between the pool and a case wallet,
so attribution does not depend on the detector's trader resolution. Precision is a lower bound:
other wash in the same window counts against it. Active precision counts only swaps between the
first and last case swap, the period the filings describe.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from washdetect.config import HeuristicConfig
from washdetect.dataset import events_from_frames
from washdetect.detector import WashDetector
from washdetect.store import Store
from washdetect.weakmodel import WEAK_DIR, WeakWashModel, frames_from_db

HERE = Path(__file__).resolve().parent
TOKEN = {"cls_nexf": "0x16ca471ae755f8a2cd4ec315a4a7439dcfebe54c", "zm_nexf": "0x16ca471ae755f8a2cd4ec315a4a7439dcfebe54c"}
APP = HeuristicConfig(net_ratio=0.05, max_block_gap=7200, max_group_size=24)


def case_swaps(swaps, transfers, pools, wallets):
    """(tx, log_index) -> case wallet, from pool<->wallet token transfers."""
    tx_wallet = {}
    for t in transfers.itertuples():
        a, b = str(t.from_address).lower(), str(t.to_address).lower()
        if a in pools and b in wallets:
            tx_wallet[str(t.tx_hash).lower()] = b
        elif b in pools and a in wallets:
            tx_wallet[str(t.tx_hash).lower()] = a
    return {(str(s.tx_hash).lower(), int(s.log_index)): tx_wallet[str(s.tx_hash).lower()] for s in swaps.itertuples() if str(s.tx_hash).lower() in tx_wallet}


def score(name, flagged, truth, n_swaps, wallets, active):
    tp = {k for k in flagged if k in truth}
    live = {k for k in flagged if k in active}
    return {
        "case": name,
        "wallets": len(wallets),
        "wallets_trading": len(set(truth.values())),
        "wallets_found": len({truth[k] for k in tp}),
        "case_trades": len(truth),
        "trades_caught": len(tp),
        "flagged": len(flagged),
        "precision": round(len(tp) / len(flagged), 3) if flagged else None,
        "recall": round(len(tp) / len(truth), 3) if truth else None,
        "window_swaps": n_swaps,
        "active_swaps": len(active),
        "active_flagged": len(live),
        "active_precision": round(len(tp) / len(live), 3) if live else None,
    }


def run(name: str, db: str, model: WeakWashModel) -> dict:
    wallets = {r["address"] for r in csv.DictReader(open(HERE / "confirmed_cases.csv")) if r["case"] == name}
    token = TOKEN[name]
    store = Store(db)
    lo, hi = store.con.execute("SELECT min(block_number), max(block_number) FROM swaps WHERE token = ?", [token]).fetchone()
    pools = {str(p["pool_address"]).lower() for p in store.pools_for(token)}
    swaps, transfers = store.read_swaps(token, lo, hi), store.read_transfers(token, lo, hi)
    truth = case_swaps(swaps, transfers, pools, wallets)
    block = {(str(s.tx_hash).lower(), int(s.log_index)): int(s.block_number) for s in swaps.itertuples()}
    first, last = min(block[k] for k in truth), max(block[k] for k in truth)
    active = {k for k, b in block.items() if first <= b <= last}
    events, transfer_events = events_from_frames(swaps, transfers)
    det = WashDetector(config=APP).find_groups(events, transfer_events, pools)
    rules = {(str(a).lower(), int(b)) for a, b in det.flagged}
    trades = frames_from_db(name, db, token, cached_only=True).trades
    cut = model.manifest["trade_threshold"]
    keys = zip(trades["tx_hash"].str.lower(), trades["log_index"].astype(int), strict=False)
    ml = {k for k, p in zip(keys, model.trade.proba(trades), strict=False) if p >= cut}
    return {"rules": score(name, rules, truth, len(swaps), wallets, active), "model": score(name, ml, truth, len(swaps), wallets, active)}


if __name__ == "__main__":
    model = WeakWashModel.load(WEAK_DIR)
    out = {}
    for arg in sys.argv[1:]:
        name, db = arg.split("=", 1)
        out[name] = run(name, db, model)
        print(json.dumps(out[name]), flush=True)
    (HERE / "cases.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

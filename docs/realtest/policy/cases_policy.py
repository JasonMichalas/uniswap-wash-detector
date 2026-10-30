import csv
import json
import pickle
import sys

sys.path.insert(0, "/workspace/wd-speed/docs/realtest")
from cases import APP, TOKEN, case_swaps
from train_pol import X

from washdetect.dataset import events_from_frames
from washdetect.detector import WashDetector
from washdetect.store import Store
from washdetect.weakmodel import frames_from_db

P = pickle.load(open("policy_model.pkl", "rb"))
out = {}
for arg in sys.argv[1:]:
    name, db = arg.split("=")
    wallets = {
        r["address"]
        for r in csv.DictReader(open("/workspace/wd-speed/docs/realtest/confirmed_cases.csv"))
        if r["case"] == name
    }
    st = Store(db)
    tok = TOKEN[name]
    lo, hi = st.con.execute(
        "select min(block_number), max(block_number) from swaps where token=?", [tok]
    ).fetchone()
    pools = {str(p["pool_address"]).lower() for p in st.pools_for(tok)}
    sw, tr = st.read_swaps(tok, lo, hi), st.read_transfers(tok, lo, hi)
    truth = case_swaps(sw, tr, pools, wallets)
    block = {(s.tx_hash.lower(), int(s.log_index)): int(s.block_number) for s in sw.itertuples()}
    b0, b1 = min(block[k] for k in truth), max(block[k] for k in truth)
    active = {k for k, b in block.items() if b0 <= b <= b1}
    ev, te = events_from_frames(sw, tr)
    rules = {
        (str(a).lower(), int(b))
        for a, b in WashDetector(config=APP).find_groups(ev, te, pools).flagged
    }
    t = frames_from_db(name, db, tok, cached_only=True).trades
    keys = list(zip(t.tx_hash.str.lower(), t.log_index.astype(int), strict=False))
    p = P["model"].predict_proba(X(t))[:, 1]
    for label, cut in [("policy P>=0.95", P["p2_p95"]), ("policy P>=0.99", P["p2_p99"])]:
        pol = {k for k, q in zip(keys, p, strict=False) if q >= cut}
        for m, fl in [(label, pol), ("rules or " + label, pol | rules)]:
            tp = fl & set(truth)
            live = fl & active
            out.setdefault(name, {})[m] = dict(
                wallets_found=len({truth[k] for k in tp}),
                wallets=len(set(truth.values())),
                trades_caught=len(tp),
                case_trades=len(truth),
                flagged=len(fl),
                precision=round(len(tp) / max(len(fl), 1), 3),
                active_flagged=len(live),
                active_precision=round(len(tp) / max(len(live), 1), 3),
                recall=round(len(tp) / len(truth), 3),
            )
            print(name, m, out[name][m], flush=True)
json.dump(out, open("cases_policy.json", "w"), indent=1)

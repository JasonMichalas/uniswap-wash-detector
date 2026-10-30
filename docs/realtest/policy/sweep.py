import itertools
import json
import os
import pickle
import sys

from washdetect.config import HeuristicConfig
from washdetect.dataset import events_from_frames
from washdetect.detector import WashDetector
from washdetect.store import Store

ORC = json.load(open("oracle.json"))
S = json.load(open("split.json"))


def load(n):
    p = f"ev_{n}.pkl"
    if os.path.exists(p):
        return pickle.load(open(p, "rb"))
    st = Store(f"/workspace/p4/new/{n}.duckdb")
    token = st.con.execute("select token from swaps limit 1").fetchone()[0]
    lo, hi = st.con.execute("select min(block_number), max(block_number) from swaps").fetchone()
    pools = {str(p["pool_address"]).lower() for p in st.pools_for(token)}
    ev, te = events_from_frames(st.read_swaps(token, lo, hi), st.read_transfers(token, lo, hi))
    pickle.dump((ev, te, pools), open(p, "wb"))
    return ev, te, pools


def run(toks, **kw):
    per = {}
    for n in toks:
        ev, te, pools = load(n)
        det = WashDetector(config=HeuristicConfig(**kw)).find_groups(ev, te, pools)
        lab = ORC[n]["swaps"]
        for (h, i), rec in det.reasons.items():
            k = f"{h.lower()}:{i}"
            if (h, i) not in det.flagged:
                continue
            for r in rec["rules"].split(",") if rec["rules"] else ["?"]:
                per.setdefault(r, [0, 0])[lab.get(k, (0, 0))[0] == 1] += 1
        tot = per.setdefault("ALL", [0, 0])
        for h, i in det.flagged:
            tot[lab.get(f"{h.lower()}:{i}", (0, 0))[0] == 1] += 1
    pos = sum(v[0] for n in toks for v in ORC[n]["swaps"].values())
    return {
        r: dict(flagged=a + b, precision=round(b / (a + b), 3), recall=round(b / pos, 3))
        for r, (a, b) in per.items()
    }


if __name__ == "__main__":
    part = sys.argv[1]
    base = dict(net_ratio=0.05, max_block_gap=7200, max_group_size=24)
    res = {"app": run(S[part], **base)}
    print("app", res["app"], flush=True)
    for nr, gap, ms in itertools.product([0.01, 0.05, 0.15], [300, 1800, 7200], [10, 40]):
        kw = dict(base, net_ratio=nr, max_block_gap=gap, max_self_swaps=ms)
        r = run(S[part], **kw)
        res[f"nr={nr} gap={gap} self={ms}"] = r
        print(
            nr,
            gap,
            ms,
            r["ALL"],
            {k: v["precision"] for k, v in r.items() if k != "ALL"},
            flush=True,
        )
    json.dump(res, open(f"sweep_{part}.json", "w"), indent=1)

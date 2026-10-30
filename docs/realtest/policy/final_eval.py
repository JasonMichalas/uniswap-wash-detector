import json
import pickle
import sys

import numpy as np
import pandas as pd
from evalb import F
from train_pol import S, X, frame, shipped

P = pickle.load(open("policy_model.pkl", "rb"))


def scores(part):
    t = pd.concat([frame(n) for n in S[part]])
    t["rules"] = (
        [
            k in R
            for k, R in zip(
                t.key,
                [{f"{h.lower()}:{i}" for h, i in F[n]["flagged_swaps"]} for n in t.token],
                strict=False,
            )
        ]
        if False
        else False
    )
    rules = {n: {f"{h.lower()}:{i}" for h, i in F[n]["flagged_swaps"]} for n in S[part]}
    t["rules"] = [k in rules[n] for k, n in zip(t.key, t.token, strict=False)]
    t["p0"] = shipped.trade.proba(t)
    t["p2"] = P["model"].predict_proba(X(t))[:, 1]
    return t


def methods(t):
    return {
        "rules (app defaults)": t.rules,
        "model, shipped cut": t.p0 >= shipped.manifest["trade_threshold"],
        "model, best-F1 cut on dev": t.p0 >= P["shipped_dev_cut"],
        "model, P>=0.95 cut on dev": t.p0 >= P["p0_p95"],
        "policy-trained, best-F1 cut": t.p2 >= P["cut"],
        "policy-trained, P>=0.95 cut": t.p2 >= P["p2_p95"],
        "policy-trained, P>=0.99 cut": t.p2 >= P["p2_p99"],
        "rules or policy-trained P>=0.99": t.rules | (t.p2 >= P["p2_p99"]),
        "flag everything": pd.Series(True, index=t.index),
    }


def pr(y, f):
    tp = (y & f).sum()
    return tp / max(f.sum(), 1), tp / max(y.sum(), 1)


def ci(t, f, B=1000):
    y = t.y.astype(bool).to_numpy()
    f = f.to_numpy()
    g = t.key.str.split(":").str[0].to_numpy()
    u, inv = np.unique(g, return_inverse=True)
    rng = np.random.default_rng(0)
    ps, rs = [], []
    tp = np.bincount(inv, y & f)
    fl = np.bincount(inv, f)
    po = np.bincount(inv, y)
    for _ in range(B):
        w = np.bincount(rng.integers(0, len(u), len(u)), minlength=len(u))
        ps.append((w * tp).sum() / max((w * fl).sum(), 1))
        rs.append((w * tp).sum() / max((w * po).sum(), 1))
    return np.percentile(ps, [2.5, 97.5]), np.percentile(rs, [2.5, 97.5])


if __name__ == "__main__":
    part = sys.argv[1]
    t = scores(part).reset_index(drop=True)
    y = t.y.astype(bool)
    out = {}
    print(part, "swaps", len(t), "wash", int(y.sum()), "tokens", S[part])
    for name, f in methods(t).items():
        p, r = pr(y, f)
        (pl, ph), (rl, rh) = ci(t, f)
        macro = np.nanmean(
            [pr(y[t.token == n], f[t.token == n])[0] for n in S[part] if f[t.token == n].sum()]
        )
        out[name] = dict(
            flagged=int(f.sum()),
            precision=round(p, 3),
            p_ci=[round(pl, 3), round(ph, 3)],
            recall=round(r, 3),
            r_ci=[round(rl, 3), round(rh, 3)],
            macro_precision=round(float(macro), 3),
        )
        print(
            f"{name:26s} {int(f.sum()):5d} P {p:.3f} [{pl:.3f},{ph:.3f}] R {r:.3f} [{rl:.3f},{rh:.3f}] macroP {macro:.3f}"
        )
    json.dump(out, open(f"final_{part}.json", "w"), indent=1)

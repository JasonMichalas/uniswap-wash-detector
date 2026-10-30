"""Policy-supervised trade model: trained on oracle labels of the old tokens, cut chosen on dev, test untouched."""

import json
import pickle

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve
from xgboost import XGBClassifier

from washdetect.weakmodel import PARAMS, WEAK_DIR, WeakWashModel

OLD = "amp andy ape bitcoin elon floki gato hex ldo npc wojak woo mog neiro pepe rlusd spx turbo".split()
S = json.load(open("split.json"))
ORC = {**json.load(open("oracle_train.json")), **json.load(open("oracle.json"))}
shipped = WeakWashModel.load(WEAK_DIR)
cols = shipped.trade.columns


def frame(n):
    t = pd.read_pickle(f"tr_{n}.pkl")
    k = t.tx_hash.str.lower() + ":" + t.log_index.astype(int).astype(str)
    t["y"] = [ORC[n]["swaps"].get(x, (0, 0))[0] for x in k]
    t["token"] = n
    t["key"] = k
    return t


def X(t):
    return np.clip(
        np.nan_to_num(
            t.reindex(columns=cols).apply(pd.to_numeric, errors="coerce").to_numpy(float),
            nan=np.nan,
        ),
        -1e9,
        1e9,
    )


def fit(toks):
    t = pd.concat([frame(n) for n in toks])
    w = 1.0 / t.groupby("token").y.transform("size")
    m = XGBClassifier(**PARAMS)
    m.fit(X(t), t.y, sample_weight=w * len(toks))
    return m


def best_cut(y, p):
    pr, rc, c = precision_recall_curve(y, p)
    f1 = 2 * pr[:-1] * rc[:-1] / np.maximum(pr[:-1] + rc[:-1], 1e-12)
    return float(c[f1.argmax()])


if __name__ == "__main__":
    m = fit(OLD)
    dev = pd.concat([frame(n) for n in S["dev"]])
    p2, p0 = m.predict_proba(X(dev))[:, 1], shipped.trade.proba(dev)
    cut2, cut1 = best_cut(dev.y, p2), best_cut(dev.y, p0)
    print(
        "dev AP policy",
        round(average_precision_score(dev.y, p2), 3),
        "shipped",
        round(average_precision_score(dev.y, p0), 3),
        "base",
        round(dev.y.mean(), 3),
    )
    print("cuts", cut1, cut2, "shipped cut", shipped.manifest["trade_threshold"])
    pickle.dump(dict(model=m, cut=cut2, shipped_dev_cut=cut1), open("policy_model.pkl", "wb"))
    pd.DataFrame(dict(key=dev.key, token=dev.token, y=dev.y, p_policy=p2, p_shipped=p0)).to_pickle(
        "dev_scores.pkl"
    )

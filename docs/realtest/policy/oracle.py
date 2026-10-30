"""Policy labels from raw token flows; shares no code with the detector.

Per swap transaction, each address's net token change says whether it bought or sold,
so labels do not depend on who the pipeline names as trader.
"""

import json
import sys

import duckdb
import pandas as pd

from washdetect.config import AGGREGATORS, PROTOCOLS, ROUTERS

DAY, UNWIND, SERVICE_FANIN, LINK_MIN = 86400, 0.5, 5, 0.1
ZERO = "0x" + "0" * 40
PM = "0x000000000004444c5dc75cb358380d2e3de08a90"

DBS = {
    **{
        n: f"/workspace/p4/new/{n}.duckdb"
        for n in "kendu apu peipei maga bobo shib aave ondo".split()
    },
    **{
        n: f"/workspace/p2/train/{n}.duckdb"
        for n in "amp andy ape bitcoin elon floki gato hex ldo npc wojak woo".split()
    },
    **{n: f"/workspace/handcheck/{n}.duckdb" for n in "mog neiro pepe rlusd spx turbo".split()},
}


def facts(name):
    c = duckdb.connect(DBS[name], read_only=True)
    pools = {r[0].lower() for r in c.execute("select pool_address from pools").fetchall()} | {
        r[0].lower() for r in c.execute("select distinct pool_address from swaps").fetchall()
    }
    tr = c.execute(
        "select tx_hash, epoch(block_timestamp) t, lower(from_address) f, lower(to_address) d, amount from transfers"
    ).df()
    pools.add(PM)
    swap_tx = {r[0] for r in c.execute("select distinct tx_hash from swaps").fetchall()}
    s = tr[tr.tx_hash.isin(swap_tx)]
    d = pd.concat([s.assign(a=s.d, q=s.amount), s.assign(a=s.f, q=-s.amount)])
    net = d.groupby(["tx_hash", "a"]).agg(q=("q", "sum"), t=("t", "first")).reset_index()
    gross = d.assign(q=d.q.abs()).groupby(["tx_hash", "a"]).q.sum().reset_index(name="g")
    net = net.merge(gross, on=["tx_hash", "a"])
    net = net[~net.a.isin(pools) & (net.a != ZERO) & (net.q.abs() > 1e-6 * net.g)]
    acts = {a: g for a, g in net.groupby("a")}
    public = {x.lower() for x in ROUTERS | AGGREGATORS | PROTOCOLS}
    frm_pool = s[s.f.isin(pools)][["tx_hash", "d"]].rename(columns={"d": "a"})
    to_pool = s[s.d.isin(pools)][["tx_hash", "f"]].rename(columns={"f": "a"})
    churn = frm_pool.merge(to_pool).drop_duplicates()
    churn = churn[~churn.a.isin(public)]
    churn_tx = churn.groupby("a").tx_hash.apply(set).to_dict()
    ns = tr[
        ~tr.tx_hash.isin(swap_tx)
        & (tr.f != tr.d)
        & (tr.f != ZERO)
        & ~tr.f.isin(pools)
        & ~tr.d.isin(pools)
    ]
    fanin = ns.groupby("d").f.nunique()
    service = set(fanin[fanin >= SERVICE_FANIN].index)
    vol = {a: g.q.abs().sum() for a, g in acts.items()}
    nb = {}
    for f, t, q in zip(ns.f, ns.d, ns.amount, strict=False):
        if (
            f in acts
            and t in acts
            and f not in service
            and t not in service
            and q >= LINK_MIN * min(vol[f], vol[t])
        ):
            nb.setdefault(f, set()).add(t)
            nb.setdefault(t, set()).add(f)

    def roundtrip(g):
        b, s_ = g[g.q > 0], g[g.q < 0]
        if b.empty or s_.empty:
            return None
        gap = min(abs(x - y) for x in b.t for y in s_.t)
        bq, sq = b.q.sum(), -s_.q.sum()
        return gap, min(bq, sq) / max(bq, sq), len(set(b.tx_hash) & set(s_.tx_hash)) > 0

    out = {}
    for a in set(churn_tx) - set(acts):
        out[a] = dict(
            label=1,
            literal=1,
            why="atomic churn",
            n=len(churn_tx[a]),
            buys=0,
            sells=0,
            unwind=None,
            gap_s=None,
            link="",
            service=False,
        )
    for a, g in acts.items():
        rt = roundtrip(g)
        lit = rt is not None and rt[0] <= DAY
        unw = lit and rt[1] >= UNWIND
        grp = ""
        for v in nb.get(a, ()):
            rt2 = roundtrip(pd.concat([g, acts[v]]))
            opposite = ((g.q > 0).any() and (acts[v].q < 0).any()) or (
                (g.q < 0).any() and (acts[v].q > 0).any()
            )
            if opposite and rt2 and rt2[0] <= DAY and rt2[1] >= UNWIND:
                grp = v
                break
        if a in churn_tx:
            unw = True
        why = (
            ("atomic churn" if rt is None or rt[2] else "own round trip")
            if unw
            else (
                "linked group" if grp else ("partial" if lit else ("slow" if rt else "one-sided"))
            )
        )
        out[a] = dict(
            label=int(unw or bool(grp)),
            literal=int(lit or bool(grp)),
            why=why,
            n=len(g),
            buys=int((g.q > 0).sum()),
            sells=int((g.q < 0).sum()),
            unwind=None if rt is None else round(rt[1], 3),
            gap_s=None if rt is None else rt[0],
            link=grp,
            service=a in service,
        )
    sw = c.execute(
        "select lower(s.trader) w, s.tx_hash, lower(m.tx_to) txto from swaps s left join tx_meta m using(tx_hash)"
    ).df()
    traders = {}
    for w, g in sw.groupby("w"):
        own = [out[a] for a in {w, *g.txto.dropna()} if a in out]
        hit = [o for o in own if o["label"]]
        traders[w] = dict(
            label=int(bool(hit)),
            literal=int(any(o["literal"] for o in own)),
            why=(hit or own or [dict(why="no net flow")])[0]["why"],
            n=len(g),
        )
    sw2 = c.execute("select tx_hash, log_index from swaps").df()
    lab = {}
    principal = net.loc[net.q.abs().groupby(net.tx_hash).idxmax()].set_index("tx_hash").a.to_dict()
    churn_by_tx = {}
    for a, t in churn_tx.items():
        for h in t:
            churn_by_tx.setdefault(h, set()).add(a)
    lab = {}
    for h in set(principal) | set(churn_by_tx):
        ws = [out[a] for a in ({principal[h]} if h in principal else churn_by_tx[h]) if a in out]
        lab[h] = (int(any(o["label"] for o in ws)), int(any(o["literal"] for o in ws)))
    swaps = {
        f"{h.lower()}:{int(i)}": lab.get(h, (0, 0))
        for h, i in zip(sw2.tx_hash, sw2.log_index, strict=False)
    }
    return dict(actors=out, traders=traders, swaps=swaps)


if __name__ == "__main__":
    res = {n: facts(n) for n in sys.argv[2:]}
    json.dump(res, open(sys.argv[1], "w"))
    for n, d in res.items():
        df = pd.DataFrame(d["traders"]).T
        print(n, len(df), df.label.sum(), df.literal.sum(), df.why.value_counts().to_dict())

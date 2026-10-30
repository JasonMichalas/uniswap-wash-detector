import json,sys
from collections import defaultdict,Counter
WETH="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
def sh(x): return x[:8] if x else "-"
def feats(r,case):
    pools=set(r["pools"]); actors=set(r["members"])|set(r.get("beneficiaries",[]))
    rows=[];buy=sell=0.0
    for t in sorted(r["txs"],key=lambda t:(t["block"],t["idx"])):
        own={t["frm"]} | ({t["to"]} if t["to"] not in pools else set())
        d=sum(v for k,v in t["delta"].items() if k in own)
        nsw=len(t["swap_events"]); npool=len({p for _,p in t["swap_events"]})
        w=sum(v for k,v in t["weth"].items() if k in actors)
        if d>0: buy+=d
        elif d<0: sell+=-d
        rows.append(dict(b=t["block"],i=t["idx"],frm=t["frm"],to=t["to"],d=d,nsw=nsw,npool=npool,weth=w,eth=t["eth_value"],h=t["hash"],gross=sum(abs(v) for k,v in t["delta"].items() if k in actors)))
    froms=Counter(x["frm"] for x in rows)
    info=r["info"]
    funders={m:info.get(m,{}).get("funded_by","") for m in r["members"]}
    fc=Counter(v for v in funders.values() if v)
    return rows,buy,sell,froms,funders,fc
def show(r,case):
    rows,buy,sell,froms,funders,fc=feats(r,case)
    print(f"=== {r['case']} {r['tok']} rule={case.get('rules','CTRL')} conf={case.get('conf','-')} wallets={len(r['members'])} usd={case.get('usd',0):.0f}")
    if 'why' in case: print("  claim:",case['why'][:160])
    for m in r["members"][:8]+r.get("beneficiaries",[])[:4]:
        i=r["info"].get(m,{}); f=r["flows"].get(m,{})
        fl=" ".join(f"{k}={v:.4g}" for k,v in f.items() if k!="n_transfers")
        print(f"  {'M' if m in r['members'] else 'B'} {m} c={i.get('contract')} n={i.get('nonce')} lab={i.get('label','')[:40]!r} fund={sh(i.get('funded_by',''))} | {fl} nT={f.get('n_transfers')}")
    for x in rows[:14]:
        lab=r["info"].get(x["to"],{}).get("label","")[:28]
        print(f"   b{x['b']} i{x['i']} from={sh(x['frm'])} to={sh(x['to'])}({lab}) actorΔ={x['d']:+.6g} gross={x['gross']:.4g} swaps={x['nsw']}/{x['npool']}p wethΔ={x['weth']:+.4g} eth={x['eth']:.3g}")
    if len(rows)>14: print(f"   ... {len(rows)-14} more")
    nr=abs(buy-sell)/max(buy,sell,1e-18)
    print(f"  actor buy={buy:.6g} sell={sell:.6g} net_ratio={nr:.3f} senders={dict(Counter({sh(k):v for k,v in froms.items()}))} internal_transfers={len(r['internal'])} sandwich={[(b,i1-i0,len([x for x in s if x not in froms])) for b,i0,i1,s in r['sandwich']]}")
    if fc and max(fc.values())>1: print("  shared funder:",fc.most_common(2))
    if r["internal"]:
        for h,li,b,s,d,v in r["internal"][:5]: print(f"   xfer b{b} {sh(s)}->{sh(d)} {v:.4g}raw tx {h[:12]}")
    if r["top_others"]: print("  top other counterparties:",[(sh(x),f"{v:.4g}",r['info'].get(x,{}).get('label','')[:25]) for x,v in r["top_others"]])
if __name__=="__main__":
    ev=json.load(open(sys.argv[1])); cases={c["case"]:c for c in json.load(open(sys.argv[2]))}
    ids=sys.argv[3].split(",") if len(sys.argv)>3 else sorted(ev)
    for k in ids:
        if "error" in ev[k]: print(k,"ERROR",ev[k]["error"]);continue
        show(ev[k],cases[k])

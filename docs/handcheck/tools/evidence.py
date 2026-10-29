"""Independent evidence from raw RPC (tx, receipts, Transfer logs) plus Etherscan labels."""
import json, re, sys, time, random, threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import httpx
from washdetect.rpc import RpcClient

T="0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SWAP_TOPICS={"0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822":"v2","0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67":"v3","0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f":"v4"}
WETH="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"; PM="0x000000000004444c5dc75cb358380d2e3de08a90"; ZERO="0x"+"0"*40
_base=RpcClient(timeout=15)
_tl=threading.local()
class _R:
    def __getattr__(self,k):
        if not hasattr(_tl,"c"): _tl.c=_base.spawn(cursor=random.randrange(7))
        return getattr(_tl.c,k)
rpc=_R()
def a(t): return "0x"+t[-40:]
def pad(x): return "0x"+"0"*24+x[2:]
DEC={}
def call(m,p):
    for i in range(4):
        try: return rpc.call(m,p)
        except Exception as e: err=e; time.sleep(1)
    raise err
def decimals(tok):
    if tok not in DEC: DEC[tok]=int(call("eth_call",[{"to":tok,"data":"0x313ce567"},"latest"]),16)
    return DEC[tok]

ES={}; eslock=threading.Lock()
UA={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
def etherscan(addr):
    if addr in ES: return ES[addr]
    out={"label":"","funded_by":""}
    for i in range(3):
        try:
            r=httpx.get(f"https://etherscan.io/address/{addr}",headers=UA,timeout=20)
            h=r.text
            m=re.search(r"<title>\s*(.*?)\s*\|\s*(Address|Contract)",h,re.S)
            if m:
                lab=m.group(1).strip()
                out["label"]="" if lab.lower().startswith(("address","contract")) else lab
                out["kind_title"]=m.group(2)
            i2=h.find("Funded By")
            if i2>0:
                f=re.findall(r"/address/(0x[0-9a-fA-F]{40})",h[i2:i2+2000])
                if f: out["funded_by"]=f[0].lower()
            nt=re.search(r'Public Name Tag.{0,400}?>([^<]{2,60})</span>',h,re.S)
            if m: break
        except Exception: time.sleep(2)
    time.sleep(0.3)
    with eslock: ES[addr]=out
    return out

def tx_detail(h, tok, dec, pools):
    tx=call("eth_getTransactionByHash",[h]); rc=call("eth_getTransactionReceipt",[h])
    delta=defaultdict(float); weth=defaultdict(float); swaps=[]; moves=[]
    for lg in rc["logs"]:
        ad=lg["address"].lower(); t0=lg["topics"][0] if lg["topics"] else ""
        if t0==T and len(lg["topics"])==3:
            s,d=a(lg["topics"][1]),a(lg["topics"][2]); v=int(lg["data"],16) if lg["data"]!="0x" else 0
            if ad==tok:
                v/=10**dec; delta[s]-=v; delta[d]+=v; moves.append((s,d,v))
            elif ad==WETH:
                v/=1e18; weth[s]-=v; weth[d]+=v
        if t0 in SWAP_TOPICS:
            swaps.append((SWAP_TOPICS[t0], ad if t0!=list(SWAP_TOPICS)[2] else lg["topics"][1]))
    return dict(hash=h,block=int(tx["blockNumber"],16),idx=int(tx["transactionIndex"],16),frm=tx["from"].lower(),to=(tx["to"] or "").lower(),
                eth_value=int(tx["value"],16)/1e18,status=int(rc["status"],16),delta=dict(delta),weth=dict(weth),moves=moves,swap_events=swaps,
                gas_eth=int(rc["gasUsed"],16)*int(rc.get("effectiveGasPrice","0x0"),16)/1e18)

def raw_transfers(tok, wallets, lo, hi):
    ws=[pad(w) for w in wallets]
    out=[]
    for topics in ([T,ws],[T,None,ws]):
        logs=rpc.get_logs(address=tok,topics=topics,start_block=lo,end_block=hi,init_span=5000,workers=3)
        for lg in logs:
            out.append((lg["transactionHash"].lower(),int(lg["logIndex"],16),int(lg["blockNumber"],16),a(lg["topics"][1]),a(lg["topics"][2]),int(lg["data"],16)))
    return sorted(set(out),key=lambda x:(x[2],x[1]))

TOKS={}
def tokinfo(t):
    if t not in TOKS:
        d=json.load(open(f"{t}_det.json")); TOKS[t]=d
    return TOKS[t]

def build(case):
    d=tokinfo(case["tok"]); tok=d["token"]; dec=decimals(tok)
    pools=set(p.lower() for p in d["pools"])|{PM}
    if "wallets" in case:
        members=[w.lower() for w in case["wallets"]]; txs=sorted({s[0].lower() for s in case["swaps"]})
        lo,hi=case["start"],case["end"]
    else:
        import pandas as pd
        sw=pd.read_pickle(f"{case['tok']}_swaps.pkl"); sw=sw[sw.trader.str.lower()==case["wallet"]]
        members=[case["wallet"]]; txs=sorted(set(sw.tx_hash.str.lower())); lo,hi=int(sw.block_number.min()),int(sw.block_number.max())
    raw=raw_transfers(tok,members,d["start_block"],d["end_block"])
    mset=set(members)
    internal=[r for r in raw if r[3] in mset and r[4] in mset]
    if not txs or case.get("rules")=="R4":
        txs=sorted(set(txs)|{r[0] for r in internal if lo<=r[2]<=hi})
    txs=txs[:40]
    det=[tx_detail(h,tok,dec,pools) for h in txs]
    ben=sorted({x for t in det for x,v in t["delta"].items() if abs(v)>1e-12 and x not in pools and x not in mset and x!=ZERO})
    ben_raw=raw_transfers(tok,ben[:8],d["start_block"],d["end_block"]) if ben else []
    raw=sorted(set(raw)|set(ben_raw),key=lambda x:(x[2],x[1]))
    actors=members+ben[:8]; aset=set(actors)
    # sandwich check per block where an actor both buys and sells
    byblock=defaultdict(list)
    for t in det: byblock[t["block"]].append(t)
    sandwich=[]
    for b,ts in byblock.items():
        if len(ts)<2: continue
        ts=sorted(ts,key=lambda t:t["idx"]); i0,i1=ts[0]["idx"],ts[-1]["idx"]
        if i1-i0<=1: sandwich.append((b,i0,i1,[]));continue
        logs=rpc.get_logs(address=tok,topics=[T],start_block=b,end_block=b,init_span=1)
        mids={lg["transactionHash"].lower() for lg in logs if i0<int(lg["transactionIndex"],16)<i1}
        senders=[]
        for h in list(mids)[:6]:
            tx=call("eth_getTransactionByHash",[h]); senders.append(tx["from"].lower())
        sandwich.append((b,i0,i1,senders))
    info={}
    for w in members[:12]+ben[:8]:
        code=call("eth_getCode",[w,"latest"]); nonce=int(call("eth_getTransactionCount",[w,"latest"]),16)
        info[w]=dict(contract=len(code)>2,nonce=nonce,**etherscan(w))
    tos={t["to"] for t in det}|{t["frm"] for t in det}
    for x in tos:
        if x not in info: info[x]=dict(**etherscan(x))
    # window flows per member
    flows={}
    for w in actors:
        f=defaultdict(float)
        for h,li,b,s,dd,v in raw:
            v=v/10**dec
            if dd==w: f["in_"+("member" if s in aset else "pool" if s in pools else "mint" if s==ZERO else "other")]+=v
            if s==w: f["out_"+("member" if dd in aset else "pool" if dd in pools else "burn" if dd==ZERO else "other")]+=v
        f["net"]=sum(v for k,v in f.items() if k.startswith("in_"))-sum(v for k,v in f.items() if k.startswith("out_"))
        f["n_transfers"]=sum(1 for r in raw if w in (r[3],r[4]))
        flows[w]=dict(f)
    others=defaultdict(float)
    for h,li,b,s,dd,v in raw:
        for x in (s,dd):
            if x not in aset and x not in pools: others[x]+=v/10**dec
    top_others=sorted(others.items(),key=lambda kv:-kv[1])[:4]
    for x,_ in top_others:
        if x not in info and x!=ZERO: info[x]=dict(**etherscan(x))
    return dict(case=case["case"],tok=case["tok"],token=tok,members=members,beneficiaries=ben,txs=det,internal=internal,flows=flows,sandwich=sandwich,info=info,top_others=top_others,pools=sorted(pools))

if __name__=="__main__":
    which=sys.argv[1]; cases=json.load(open(which))
    if len(sys.argv)>2: cases=[c for c in cases if c["case"] in sys.argv[2].split(",")]
    res={}
    def go(c):
        for k in range(3):
            try: return c["case"],build(c)
            except Exception as e: err=repr(e); time.sleep(3)
        return c["case"],{"error":err}
    with ThreadPoolExecutor(5) as ex:
        for cid,r in ex.map(go,cases):
            res[cid]=r; print(cid, "ERR "+r["error"][:150] if "error" in r else f"{len(r['txs'])} txs", flush=True)
    out=which.replace("sample_","evidence_")
    old={}
    try: old=json.load(open(out))
    except Exception: pass
    old.update(res); json.dump(old,open(out,"w"),default=str)

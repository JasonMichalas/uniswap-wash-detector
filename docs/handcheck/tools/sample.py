import json, random, pandas as pd
random.seed(82)
toks="pepe rlusd spx mog neiro turbo".split()
allc=[]
for t in toks:
    d=json.load(open(f"{t}_det.json"))
    for c in d['cycles']: c['tok']=t; allc.append(c)
def band(c):
    x=c['conf']; return 'high' if x>=0.95 else 'mid' if x>=0.8 else 'low'
for c in allc: c['band']=band(c)
quota={'R1':{'high':12,'mid':9,'low':5},'R2':{'high':5,'mid':5,'low':4},'R3':{'low':2},'R4':{'high':8,'low':8},'R5':{'low':5},'R6':{'low':17}}
pick=[]
for r,bq in quota.items():
    for b,n in bq.items():
        pool=[c for c in allc if c['rules']==r and c['band']==b]
        # round-robin across tokens for spread
        random.shuffle(pool); bytok={}
        for c in pool: bytok.setdefault(c['tok'],[]).append(c)
        got=[]
        while len(got)<n and any(bytok.values()):
            for t in list(bytok):
                if bytok[t] and len(got)<n: got.append(bytok[t].pop())
        if len(got)<n: print("short",r,b,len(got),n)
        pick+=got
print(len(pick))
for i,c in enumerate(pick): c['case']=f"G{i+1:02d}"
json.dump(pick,open("sample_groups.json","w"))
# controls
ctrl=[]
for t in toks:
    d=json.load(open(f"{t}_det.json")); sw=pd.read_pickle(f"{t}_swaps.pkl")
    sw['trader']=sw.trader.str.lower()
    g=sw.groupby('trader').side.agg(lambda s:(s=='buy').sum()*1000+(s=='sell').sum())
    two=[w for w,v in g.items() if v//1000>=1 and v%1000>=1 and w not in set(d['flagged_wallets'])]
    print(t,"two-sided unflagged",len(two),"flagged wallets",len(d['flagged_wallets']),"traders",len(g))
    random.shuffle(two)
    for w in two[:4 if t!='turbo' else 0]: ctrl.append(dict(tok=t,wallet=w))
ctrl=ctrl[:20]
for i,c in enumerate(ctrl): c['case']=f"C{i+1:02d}"
print(len(ctrl)); json.dump(ctrl,open("sample_controls.json","w"))

import json, csv, collections, sys
sys.path.insert(0,'.')
from labels import L
sg=json.load(open('sample_groups.json')); sc=json.load(open('sample_controls.json'))
eg=json.load(open('evidence_groups.json')); ec=json.load(open('evidence_controls.json'))
rows=[]
def links(ev, n=4):
    hs=[]
    for t in ev.get('txs',[]):
        if t['hash'] not in hs: hs.append(t['hash'])
    return " ".join(f"https://etherscan.io/tx/{h}" for h in hs[:n])
for s in sg:
    c=s['case']; lab,cause,evid=L[c]; ev=eg[c]
    rows.append(dict(case=c,token=s['tok'].upper() if s['tok']!='neiro' else 'Neiro',group_id=s['id'],rule=(s['rules'] if isinstance(s['rules'],str) else '+'.join(s['rules'])),confidence=s['conf'],band=s['band'],
      wallets=" ".join(s['wallets']),usd=round(s['usd'] or 0),label=lab,cause=cause,evidence=evid,links=links(ev)))
ctl=sc if isinstance(sc,list) else list(sc.values())
for s in ctl:
    c=s['case']; lab,cause,evid=L[c]; ev=ec[c]
    w=s.get('wallets') or [s.get('wallet')]
    rows.append(dict(case=c,token=s['tok'].upper() if s['tok']!='neiro' else 'Neiro',group_id="control",rule="CTRL",confidence="",band="",
      wallets=" ".join(w),usd="",label=lab,cause=cause,evidence=evid,links=links(ev)))
with open('cases.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
# stats
G=[r for r in rows if r['rule']!='CTRL']
def stat(rs):
    n=len(rs); cl=sum(r['label']=='Clear wash' for r in rs); li=sum(r['label']=='Likely wash' for r in rs)
    un=sum(r['label']=='Unclear' for r in rs); nw=n-cl-li-un
    return n,cl,li,un,nw
print("overall",stat(G))
by=collections.defaultdict(list)
for r in G: by[r['rule'].split('+')[0]].append(r)
for k in sorted(by): print(k,stat(by[k]))
byb=collections.defaultdict(list)
for r in G: byb[r['band']].append(r)
for k in byb: print(k,stat(byb[k]))
print("ctrl",stat([r for r in rows if r['rule']=='CTRL']))
print(collections.Counter(r['cause'] for r in G if r['label']=='Not wash').most_common())
print(collections.Counter(r['rule'] for r in G))

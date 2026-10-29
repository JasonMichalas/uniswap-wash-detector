import json, sys
from datetime import datetime
from washdetect.pipeline import load_token
from washdetect.store import Store
from washdetect.token import TokenData
from washdetect.config import HeuristicConfig
from washdetect.detector import WashDetector, SwapEvent, TransferEvent

TOK = {"pepe":("0x6982508145454Ce325dDbE47a25d4ec3d2311933","2026-10-26","2026-10-26"),
"rlusd":("0x8292Bb45bf1Ee4d140127049757C2E0fF06317eD","2026-10-25","2026-10-27"),
"spx":("0xE0f63A424a4439cBE457D80E4f4b51aD25b2c56C","2026-10-26","2026-10-27"),
"mog":("0xaaeE1A9723aaDB7afA2810263653A34bA2C21C7a","2026-10-26","2026-10-27"),
"neiro":("0x812Ba41e071C7b7fA4EBcFB62dF5F45f6fA853Ee","2026-10-25","2026-10-27"),
"turbo":("0xA35923162C49cF95e6BF26623385eb431ad920D3","2026-10-25","2026-10-27")}
name=sys.argv[1]; addr,s,e=TOK[name]; db=f"/workspace/handcheck/{name}.duckdb"
rep=load_token(addr,start=s,end=e,db_path=db,progress=lambda m:None)
st=Store(db)
td=TokenData.load(st,rep.token,rep.start_block,rep.end_block,datetime.fromisoformat(rep.start_time),datetime.fromisoformat(rep.end_time))
sw=td.swaps; tr=td.transfers
ev=[SwapEvent(wallet=str(r.trader or ""),block=int(r.block_number),side=str(r.side),amount=float(r.amount_token or 0),usd=float(r.usd_value or 0),tx_hash=str(r.tx_hash),log_index=int(r.log_index),pool=str(r.pool_address or "")) for r in sw.itertuples() if r.trader]
te=[TransferEvent(src=str(r.from_address),dst=str(r.to_address),block=int(r.block_number),amount=float(r.amount or 0),tx_hash=str(r.tx_hash),log_index=int(r.log_index)) for r in tr.itertuples() if r.from_address and r.to_address]
det=WashDetector(config=HeuristicConfig(net_ratio=0.05,max_block_gap=7200,max_group_size=24)).find_groups(ev,te,td.pool_addresses)
cyc=[]
for c in det.cycles:
    cyc.append(dict(id=c.cycle_id,kind=c.kind,rules=c.rules,conf=c.confidence,wallets=list(c.wallets),usd=c.volume_usd,start=c.start_block,end=c.end_block,swaps=[list(x) for x in c.swap_ids],why=c.why,pattern=c.pattern_type))
flagged_wallets={s.wallet.lower() for s in ev if (s.tx_hash.lower(),s.log_index) in det.flagged}
out=dict(token=rep.token,symbol=rep.symbol,start_block=rep.start_block,end_block=rep.end_block,n_swaps=len(ev),n_transfers=len(te),wash_pct=det.wash_pct,pools=sorted(td.pool_addresses),cycles=cyc,flagged_wallets=sorted(flagged_wallets))
sw.to_pickle(f"/workspace/handcheck/{name}_swaps.pkl"); tr.to_pickle(f"/workspace/handcheck/{name}_transfers.pkl")
json.dump(out,open(f"/workspace/handcheck/{name}_det.json","w"),default=str)
from collections import Counter
print(name,rep.symbol,len(ev),"swaps",len(te),"transfers",len(cyc),"groups",Counter(c['rules'] for c in cyc),f"wash {det.wash_pct:.1f}%")

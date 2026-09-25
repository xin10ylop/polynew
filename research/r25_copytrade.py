"""Out-of-sample copy-trading of consistently profitable taker wallets, strict fills.
Select wallets on data before CUT; follow their trades after CUT: we see their trade at api_ts (+react),
then buy the same side at the first print with api_ts >= seen+1 within 3s at price <= their price + slip."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.fees import taker_fee_per_share as fee
dur=sys.argv[1]
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['condition_id','ts','side_up','ask','side_won','size','wallet','tau','start_ts'])
CUT=tr.ts.max()-18*86400
tr['pnl']=tr.side_won-tr.ask-fee(tr.ask)
tr['usd']=tr.pnl*tr['size']; tr['cost']=tr.ask*tr['size']; tr['day']=tr.ts//86400
A=tr[tr.ts<CUT]; B=tr[tr.ts>=CUT].sort_values(['condition_id','ts'])
g=A.groupby('wallet')
w=pd.DataFrame(dict(n=g.size(),mk=g.condition_id.nunique(),vol=g.cost.sum(),pnl=g.usd.sum(),ppsh=g.pnl.mean()))
dp=A.groupby(['wallet','day']).usd.sum(); w['posday']=dp.groupby('wallet').apply(lambda x:(x>0).mean()); w['nd']=dp.groupby('wallet').size()
w['roi']=w.pnl/w.vol
for sel_name,sel in [('roi>5%,posday>=.6',(w.n>=200)&(w.mk>=50)&(w.roi>0.05)&(w.posday>=0.6)&(w.nd>=7)),
                     ('roi>2%,big',(w.n>=500)&(w.mk>=100)&(w.roi>0.02)&(w.nd>=10)),
                     ('top20 pnl',w.index.isin(w[(w.mk>=30)].sort_values('pnl').tail(20).index))]:
    S=set(w[sel].index)
    L=B[B.wallet.isin(S)]
    # their own out-of-sample performance
    own=L.pnl.mean() if len(L) else np.nan
    arrays={c:(x.ts.values,x.side_up.values.astype(bool),x.ask.values) for c,x in B.groupby('condition_id')}
    for react in [1,3]:
        out=[]
        last={}
        for r in L.itertuples(index=False):
            key=(r.condition_id,r.side_up)
            if key in last and r.ts-last[key]<30: continue   # one copy per market-side per 30s
            ts,su,ask=arrays[r.condition_id]; ii=np.flatnonzero(su==bool(r.side_up))
            seen=r.ts+react
            lo=np.searchsorted(ts[ii],seen+1,side='left'); hi=np.searchsorted(ts[ii],seen+4,side='right')
            cand=ask[ii][lo:hi]; ok=cand<=r.ask+0.02
            if not ok.any(): continue
            p=cand[np.argmax(ok)]
            out.append((r.condition_id,r.tau,p,r.side_won,r.side_won-p-fee(p))); last[key]=r.ts
        O=pd.DataFrame(out,columns=['cid','tau','px','won','pnl'])
        print(f'{dur} [{sel_name}] wallets={len(S)} their OOS trades={len(L)} their OOS pnl/sh={own:+.4f} | copy react={react}s: n={len(O)} mk={O.cid.nunique() if len(O) else 0} pnl/sh={O.pnl.mean() if len(O) else 0:+.4f} se={O.pnl.std()/np.sqrt(max(len(O),1)) if len(O) else 0:.4f}')

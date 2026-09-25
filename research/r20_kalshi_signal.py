"""Polymarket-only taker using Kalshi's (leading) price as the fair-value signal. Strict fills.
Signal at true time t: Kalshi mid proxy = last Kalshi trade yes price (exact timestamps) at <= t - kl (our Kalshi data latency).
Poly reference ask = last Poly print for that side with api_ts <= t (known). Execution: first Poly print for that side with
api_ts in [t+D, t+D+1] at price <= limit (D>=3 means true match time >= ~t+0..t+2)."""
import sys, glob; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.fees import taker_fee_per_share
k=pd.read_parquet('data/kalshi_15m.parquet'); pm15=pd.read_parquet('data/markets_15m.parquet').dropna(subset=['up_won'])
tr=pd.read_parquet('data/tr_eval_15m.parquet',columns=['condition_id','ts','side_up','ask'])
files=glob.glob('data/kalshi_trades/*.parquet')
data=[]
for f in files:
    K=pd.read_parquet(f).sort_values('t'); tk=K.ticker.iloc[0]
    if K.t.max()<1e8: K['t']=K.t*1000.0
    kk=k[k.ticker==tk]; o=int(kk.open_ts.iloc[0]); pr=pm15[pm15.start_ts==o]
    if pr.empty: continue
    P=tr[tr.condition_id==pr.condition_id.iloc[0]].sort_values('ts')
    if len(P)<20: continue
    # Kalshi mid proxy: EWMA-free: median of last 5 trade prices
    ky=pd.Series(K.yes.values).rolling(5,min_periods=1).median().values
    data.append((o,int(pr.up_won.iloc[0]),K.t.values,ky,P.ts.values,P.side_up.values.astype(bool),P.ask.values))
print('windows',len(data))
for kl in [0.5,1.0]:
  for D in [3,4]:
    for th in [0.02,0.04,0.06]:
        out=[]
        for o,y,kt,ky,pts,psu,pask in data:
            used=0; last_t=-1e9
            for t in range(o+10,o+890):
                if used>=3: break
                if t-last_t<10: continue
                j=np.searchsorted(kt,t-kl,side='right')-1
                if j<0 or (t-kl)-kt[j]>5: continue
                qk=ky[j]
                if not (0.05<qk<0.95): continue
                for side in (True,False):
                    q=qk if side else 1-qk
                    ii=np.flatnonzero(psu==side)
                    jj=np.searchsorted(pts[ii],t,side='right')-1
                    if jj<0 or t-pts[ii][jj]>15: continue
                    ref=pask[ii][jj]
                    if q-ref-taker_fee_per_share(ref)<=th: continue
                    lo=np.searchsorted(pts[ii],t+D,side='left'); hi=np.searchsorted(pts[ii],t+D+1,side='right')
                    cand=pask[ii][lo:hi]; ok=q-cand-taker_fee_per_share(cand)>th/2
                    if not ok.any(): continue
                    p=cand[np.argmax(ok)]; won=y if side else 1-y
                    out.append((o,t-o,side,p,q,won,won-p-taker_fee_per_share(p))); used+=1; last_t=t; break
        O=pd.DataFrame(out,columns=['o','el','side','px','q','won','pnl'])
        if len(O)==0: print(kl,D,th,'none'); continue
        print(f'kalshi_lat={kl}s D={D} th={th}: n={len(O)} windows={O.o.nunique()} px={O.px.mean():.3f} q={O.q.mean():.3f} win={O.won.mean():.3f} pnl/sh={O.pnl.mean():+.4f} se={O.pnl.std()/np.sqrt(len(O)):.4f}',flush=True)

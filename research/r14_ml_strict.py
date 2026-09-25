"""Strict taker test of the ML fair value on the held-out period (decision points from ml_test)."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.fees import taker_fee_per_share
dur=sys.argv[1]; col=sys.argv[2] if len(sys.argv)>2 else 'p_resid'
T=pd.read_parquet(f'data/ml_test_{dur}.parquet')
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['condition_id','ts','side_up','ask'])
tr=tr[tr.condition_id.isin(T.cid.unique())].sort_values(['condition_id','ts'])
G={c:(g.ts.values,g.side_up.values.astype(bool),g.ask.values) for c,g in tr.groupby('condition_id')}
for delay in [3,4]:
  for th in [0.02,0.04,0.07]:
    out=[]
    for c,g in T.groupby('cid'):
        ts,su,ask=G[c]; used=0
        for r in g.itertuples(index=False):
            if used>=3: break
            for side in (True,False):
                q=r[T.columns.get_loc(col)] if side else 1-r[T.columns.get_loc(col)]
                ii=np.flatnonzero(su==side)
                j=np.searchsorted(ts[ii],r.t,side='right')-1
                if j<0 or r.t-ts[ii][j]>15: continue
                ref=ask[ii][j]
                if not (0.03<=ref<=0.97) or q-ref-taker_fee_per_share(ref)<=th: continue
                lo=np.searchsorted(ts[ii],r.t+delay,side='left'); hi=np.searchsorted(ts[ii],r.t+delay+1,side='right')
                cand=ask[ii][lo:hi]
                ok=q-cand-taker_fee_per_share(cand)>th/2
                if not ok.any(): continue
                p=cand[np.argmax(ok)]; won=r.y if side else 1-r.y
                out.append((c,r.tau,p,q,won,won-p-taker_fee_per_share(p))); used+=1; break
    O=pd.DataFrame(out,columns=['cid','tau','px','q','won','pnl'])
    if len(O)==0: print(delay,th,'none'); continue
    O['tb']=pd.cut(O.tau,[0,20,60,120,300,600,900])
    print(f'{dur} {col} delay={delay} th={th}: n={len(O)} mk={O.cid.nunique()} px={O.px.mean():.3f} win={O.won.mean():.3f} pnl/sh={O.pnl.mean():+.4f} se={O.pnl.std()/np.sqrt(len(O)):.4f}')
    print('   ', O.groupby('tb',observed=True).pnl.agg(['size','mean']).round(4).T.to_string().replace('\n','\n    '))

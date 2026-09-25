import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.model import Series1s, fair_up
from pm.fees import taker_fee_per_share
dur=sys.argv[1]
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet')
S=Series1s()
D=int((tr.end_ts-tr.start_ts).iloc[0])
bins=[0,20,60,90,120,180,300,450,600,900][: (10 if D==900 else 7)]
if D==300: bins=[0,20,60,90,120,180,240,300]
tr['tb']=pd.cut(tr.tau,bins)
for lat in [0,1,2,3,5]:
    t=tr.ts.values-(lat-1)
    q,z=fair_up(S,tr.start_ts.values,tr.end_ts.values,tr.price_to_beat.values,t,S.sigma_at(t,900),0.54,df_t=5)
    qs=np.where(tr.side_up==1,q,1-q)
    edge=qs-tr.ask-tr.fee
    sel=edge>0.05
    g=tr[sel].groupby('tb',observed=True)
    print(f'lat={lat}', ' '.join(f'{str(k)}:{v.pnl.mean():+.3f}({len(v)})' for k,v in g))

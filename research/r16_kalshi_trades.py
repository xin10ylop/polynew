import sys; sys.path.insert(0,'.')
import os, pandas as pd, numpy as np, time
from concurrent.futures import ThreadPoolExecutor
from pm.kalshi import trades
k=pd.read_parquet('data/kalshi_15m.parquet')
k=k[k.open_ts>=k.open_ts.max()-14*86400]
k=k.sample(min(300,len(k)),random_state=1).sort_values('open_ts')
os.makedirs('data/kalshi_trades',exist_ok=True)
def one(tk):
    f=f'data/kalshi_trades/{tk}.parquet'
    if os.path.exists(f): return 0
    t=trades(tk)
    if not t: return 0
    d=pd.DataFrame(t)
    out=pd.DataFrame({'ticker':tk,'t':pd.to_datetime(d.created_time,format='ISO8601',utc=True).map(lambda x:x.timestamp()).values,'yes':d.yes_price_dollars.astype(float),
        'n':d.count_fp.astype(float),'taker_yes':(d.taker_side=='yes').astype(np.int8)})
    out.to_parquet(f); return len(out)
t0=time.time(); tot=0
with ThreadPoolExecutor(3) as ex:
    for i,n in enumerate(ex.map(one,k.ticker)):
        tot+=n
        if i%20==0: print(i,tot,round(time.time()-t0),flush=True)
print('done',tot)

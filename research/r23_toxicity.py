"""Where do makers lose? Maker PnL per fill vs BTC (futures) move just before the true match time."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.model import Series1s
from pm.fees import taker_fee_per_share
dur=sys.argv[1]
FU=Series1s('data/btcusdt_fut_1s.parquet')
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['ts','side_up','ask','side_won','size','tau','q_side'])
tm=(tr.ts.values-2).astype(np.int64)     # approx match second
i=FU.idx(tm)
lc=np.log(FU.c)
sgn=np.where(tr.side_up.values==1,1,-1)   # taker's side direction
sig=FU.sigma_at(tm,900)
res={}
for a,b,name in [(-3,-1,'r[-3,-1]'),(-1,0,'r[-1,0]'),(0,1,'r[0,+1]'),(-10,-3,'r[-10,-3]')]:
    r=(lc[i+b]-lc[i+a])/(sig*np.sqrt(b-a))*sgn   # z-score of move toward taker's side
    res[name]=r
mk=(tr.ask.values-tr.side_won.values)+0.2*taker_fee_per_share(tr.ask.values)
w=tr['size'].values
df=pd.DataFrame(res); df['mk']=mk; df['w']=w
for name in res:
    df['b']=pd.cut(df[name],[-99,-2,-1,-0.3,0.3,1,2,99])
    g=df.groupby('b',observed=True).apply(lambda x: pd.Series(dict(share=x.w.sum()/w.sum(),maker_pnl=np.average(x.mk,weights=x.w))))
    print(f'{dur} maker pnl/share by taker-favourable BTC move z {name}:'); print(g.round(4).T.to_string()); print()
quiet=(np.abs(res['r[-3,-1]'])<0.3)&(np.abs(res['r[-10,-3]'])<1)
print('maker pnl/share when BTC quiet in the prior 10s:',round(np.average(mk[quiet],weights=w[quiet]),4),' share of volume',round(w[quiet].sum()/w.sum(),3))
print('maker pnl/share otherwise:',round(np.average(mk[~quiet],weights=w[~quiet]),4))

"""Who makes money as a taker? Per-wallet PnL and behavior profile."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
dur=sys.argv[1]
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet')
tr['usd_pnl']=tr.pnl*tr['size']
tr['cost']=tr.ask*tr['size']
g=tr.groupby('wallet')
w=pd.DataFrame(dict(n=g.size(), mk=g.condition_id.nunique(), vol=g.cost.sum(), pnl=g.usd_pnl.sum(),
   avg_px=g.ask.mean(), med_tau=g.tau.median(), avg_edge=g.edge.mean(), frac_buy=g.taker_buy.mean(),
   days=g.ts.agg(lambda x:(x.max()-x.min())/86400)))
w['roi']=w.pnl/w.vol
w=w.sort_values('pnl',ascending=False)
print('wallets',len(w),'total taker pnl',w.pnl.sum().round(0),'total vol',w.vol.sum().round(0))
pd.set_option('display.width',250)
print(w.head(25).round(3).to_string())
print(w.tail(10).round(3).to_string())
w.to_parquet(f'data/wallets_{dur}.parquet')
# consistency: daily pnl of top wallets
tr['day']=pd.to_datetime(tr.ts,unit='s').dt.date
top=w[(w.n>200)].sort_values('pnl',ascending=False).head(10).index
for wal in top:
    d=tr[tr.wallet==wal].groupby('day').usd_pnl.sum()
    print(wal[:10], 'days',len(d),'pos days',(d>0).sum(),'sharpe/day',round(d.mean()/d.std(),2) if d.std()>0 else None)

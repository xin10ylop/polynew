"""Profile consistently profitable taker wallets: what do they know that the model doesn't?"""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.model import Series1s
dur=sys.argv[1]
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet')
S=Series1s()
tr['usd_pnl']=tr.pnl*tr['size']; tr['cost']=tr.ask*tr['size']
tr['day']=tr.ts//86400
g=tr.groupby('wallet')
w=pd.DataFrame(dict(n=g.size(), mk=g.condition_id.nunique(), vol=g.cost.sum(), pnl=g.usd_pnl.sum()))
dp=tr.groupby(['wallet','day']).usd_pnl.sum().reset_index()
pdr=dp.groupby('wallet').usd_pnl.agg(lambda x:(x>0).mean()); nd=dp.groupby('wallet').size()
w['posday']=pdr; w['ndays']=nd; w['roi']=w.pnl/w.vol
good=w[(w.n>=300)&(w.mk>=80)&(w.roi>0.03)&(w.posday>=0.6)&(w.ndays>=10)].sort_values('pnl',ascending=False)
bad=w[(w.n>=300)&(w.mk>=80)&(w.roi<-0.03)].sort_values('pnl')
print('consistent winners',len(good)); print(good.round(3).head(30).to_string())
# features at trade time
i=S.idx(tr.ts.values)-1
lc=np.log(S.c)
for L in [5,15,30,60,120,300]:
    tr[f'r{L}']=(lc[i]-lc[i-L])*1e4
sgn=np.where(tr.side_up==1,1,-1)
for L in [5,15,30,60,120,300]:
    tr[f'sr{L}']=tr[f'r{L}']*sgn   # recent move in direction of the side bought (positive = momentum)
cols=['ask','tau','q_side','edge','sr5','sr15','sr30','sr60','sr120','sr300','side_won','pnl']
def prof(ws,name):
    x=tr[tr.wallet.isin(ws)]
    print(f'\n--- {name}: n={len(x)} ---')
    print(x[cols].describe(percentiles=[.1,.5,.9]).round(3).to_string())
prof(good.index,'winners'); prof(bad.index,'losers'); prof(w.index,'all')
good.to_csv('data/good_wallets_%s.csv'%dur)

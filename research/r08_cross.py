"""5m vs 15m relative value in the shared final 5 minutes.

In [S+600, S+900) the 15m market (strike K15) and 5m market #3 (strike K5) settle on the same F.
Given the market's P5, back out the market-implied mean of F using the model's sd, then compute the
implied fair P15 = Phi((mu - K15)/sd). Compare with 15m prints. (And vice versa.)
Timing: both tapes share the same api-timestamp delay, so relative comparison at equal api_ts is fair.
"""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from scipy.stats import t as student
from pm.model import Series1s, twap_dist
from pm.data import load_markets
from pm.fees import taker_fee_per_share
S=Series1s('data/btcusdt_1s.parquet')
m15=load_markets('15m'); m5=load_markets('5m')
m5i=m5.set_index('start_ts')
t15=pd.read_parquet('data/tr_eval_15m.parquet',columns=['condition_id','ts','side_up','ask','up_price','start_ts'])
t5=pd.read_parquet('data/tr_eval_5m.parquet',columns=['condition_id','ts','side_up','ask','up_price','start_ts'])
t5=t5[t5.start_ts.isin(m15.start_ts+600)]
g15={c:g.sort_values('ts') for c,g in t15.groupby('condition_id')}
g5={c:g.sort_values('ts') for c,g in t5.groupby('condition_id')}
DF=5
rows=[]
for r in m15.itertuples(index=False):
    s3=r.start_ts+600
    if s3 not in m5i.index or r.condition_id not in g15: continue
    r5=m5i.loc[s3]
    if r5.condition_id not in g5: continue
    if r.start_ts-4000<S.t0 or r.end_ts+5>S.t1: continue
    A=g15[r.condition_id]; B=g5[r5.condition_id]
    K15=r.price_to_beat; K5=r5.price_to_beat
    # model sd of F (in Chainlink price units, basis-adjusted) at each second
    grid=np.arange(s3+2, r.end_ts-2)
    sig=S.sigma_at(grid,900)
    mean_b,sd_b=twap_dist(S,np.full(len(grid),r.start_ts),np.full(len(grid),r.end_ts),grid,sig)
    # convert binance-scale sd to chainlink scale ~ same relative
    rel_sd=np.sqrt((sd_b/mean_b)**2+(0.54e-4)**2)
    # last print (up price) per grid second in each market (known: api_ts <= t)
    ia=np.searchsorted(A.ts.values,grid,side='right')-1
    ib=np.searchsorted(B.ts.values,grid,side='right')-1
    ok=(ia>=0)&(ib>=0)
    ia=np.clip(ia,0,None); ib=np.clip(ib,0,None)
    fresh=ok&(grid-A.ts.values[ia]<=3)&(grid-B.ts.values[ib]<=3)
    p15=A.up_price.values[ia]; p5=B.up_price.values[ib]
    for k in np.flatnonzero(fresh):
        rows.append((r.condition_id,r5.condition_id,grid[k],r.end_ts-grid[k],K15,K5,p15[k],p5[k],rel_sd[k],r.up_won,r5.up_won))
X=pd.DataFrame(rows,columns=['c15','c5','t','tau','K15','K5','p15','p5','rsd','y15','y5'])
# implied mean from p5 then fair p15 (student-t, df=5 consistent with model)
def inv(p): return student.ppf(np.clip(p,0.005,0.995),DF)
mu5=np.log(X.K5)+inv(X.p5)*X.rsd          # log-mean implied by 5m market
X['f15']=student.cdf((mu5-np.log(X.K15))/X.rsd,DF)
mu15=np.log(X.K15)+inv(X.p15)*X.rsd
X['f5']=student.cdf((mu15-np.log(X.K5))/X.rsd,DF)
X['d15']=X.f15-X.p15    # >0: 15m Up cheap relative to 5m
X['d5']=X.f5-X.p5
X['gap_bp']=np.log(X.K5/X.K15)*1e4
X.to_parquet('data/cross_5m15m.parquet')
print('rows',len(X),'markets',X.c15.nunique())
print(X[['p15','p5','d15','d5','gap_bp','tau']].describe().round(4).to_string())
# does the 5m-implied fair value predict the 15m outcome better than the 15m price?
for name,p in [('p15',X.p15),('f15',X.f15),('avg',(X.p15+X.f15)/2)]:
    pp=np.clip(p,0.005,0.995); y=X.y15
    print(name,'LL',round(-(y*np.log(pp)+(1-y)*np.log(1-pp)).mean(),4))
for name,p in [('p5',X.p5),('f5',X.f5),('avg',(X.p5+X.f5)/2)]:
    pp=np.clip(p,0.005,0.995); y=X.y5
    print(name,'LL',round(-(y*np.log(pp)+(1-y)*np.log(1-pp)).mean(),4))

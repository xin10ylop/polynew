"""Month-level view of the final variant (b3_g300, 10-share clips) on the recent regime (Sep 10-23):
per-day stats, regime-gate effect on the real window sequence, Monte Carlo of a 30-day month
(random recent day per day, 288 resampled windows, -$50/day halt, minus ~$70 AWS), and size scaling on matched days."""
import pandas as pd, numpy as np
fs=['adapt_5m_train','adapt_5m_test18','adapt_5m_test21','adapt_5m_test23','adapt_5m_b3_early']
O=pd.concat([pd.read_parquet(f'data/{f}.parquet') for f in fs]); O['tot']=O.pnl+O.reb
O['day']=pd.to_datetime(O.st,unit='s').dt.strftime('%m-%d')
O=O[O.strat=='b3_g300'].drop_duplicates(['cid','lat']).sort_values('st')
recent=['09-10','09-11','09-15','09-16','09-18','09-21','09-23']
print("per day (recent regime), windows / $ per window / shares per window / worst window")
for lat in (20,50):
    R=O[(O.lat==lat)&O.day.isin(recent)]
    g=R.groupby('day').agg(n=('tot','size'),usd=('tot','mean'),sh=('sh','mean'),worst=('tot','min'),best=('tot','max'))
    print(lat,'ms\n',g.round(2).to_string())
    print(' all:', len(R),'windows  mean $/w',round(R.tot.mean(),3),' sd',round(R.tot.std(),2),' max sh/window',R.sh.max(), ' p99 sh',R.sh.quantile(.99))
# regime gate on real sequences (lag 2, K 12), per day contiguous
def gate_eval(R,K=12,lag=2):
    tot=[];gated=[]
    for d,D in R.groupby('day'):
        x=D.sort_values('st').tot.values
        for i in range(len(x)):
            hist=x[max(0,i-lag-K+1):max(0,i-lag+1)]
            on = len(hist)>=K and hist.mean()>0
            tot.append(x[i]); gated.append(x[i] if on else 0.0)
    return np.sum(tot), np.sum(gated), np.mean([g!=0 for g in gated])
for lat in (20,50):
    R=O[(O.lat==lat)&O.day.isin(recent)]
    print(f'gate {lat}ms: ungated {gate_eval(R)[0]:+.1f}  gated {gate_eval(R)[1]:+.1f}  frac windows traded {gate_eval(R)[2]:.2f}')
def gate_seq(x,K,lag=2):
    return sum(x[i] for i in range(len(x)) if i-lag-K+1>=0 and x[i-lag-K+1:i-lag+1].mean()>0)
# Monte Carlo month: pick a random recent day for each of 30 days, resample 288 windows from it in random order,
# apply -$50/day halt; subtract costs.
rng=np.random.default_rng(7)
AWS=70.0
def month(R, days=30, W=288, halt=50.0, scale=1.0):
    byday={d:D.tot.values*scale for d,D in R.groupby('day')}
    keys=list(byday)
    tot=0.0; path=[0.0]; daily=[]
    for _ in range(days):
        x=rng.choice(byday[keys[rng.integers(len(keys))]], W, replace=True)
        c=0.0
        for v in x:
            c+=v; path.append(path[-1]+v)
            if c<=-halt*scale: break
        daily.append(c); tot+=c
    p=np.array(path); dd=np.max(np.maximum.accumulate(p)-p)
    return tot, dd, np.array(daily)
for lat in (20,50):
    R=O[(O.lat==lat)&O.day.isin(recent)]
    res=[month(R) for _ in range(2000)]
    T=np.array([r[0] for r in res])-AWS; DD=np.array([r[1] for r in res]); D=np.concatenate([r[2] for r in res])
    print(f'\nMONTH {lat}ms (recent regime, 10-share clips, -$50/day halt, minus ${AWS:.0f} AWS): '
          f'median {np.median(T):+.0f}  p10 {np.percentile(T,10):+.0f}  p90 {np.percentile(T,90):+.0f}  P(loss) {np.mean(T<0):.1%}')
    print(f'   losing days {np.mean(D<0):.0%}   worst day p1 {np.percentile(D,1):+.0f}   max drawdown median {np.median(DD):.0f} p95 {np.percentile(DD,95):.0f}')
# gate with history carried across days, several K
for lat in (20,50):
    x=O[(O.lat==lat)&O.day.isin(recent)].tot.values
    print(f'{lat}ms gate, continuous history: ungated {x.sum():+.0f}', ' '.join(f'K{K} {gate_seq(x,K):+.0f}' for K in (12,36,72,144)))
# size scaling on matched days
A=pd.concat([pd.read_parquet(f'data/{f}.parquet') for f in ['adapt_5m_size_train','adapt_5m_test18','adapt_5m_test23']])
A['tot']=A.pnl+A.reb; A['day']=pd.to_datetime(A.st,unit='s').dt.strftime('%m-%d')
for days in (['09-10','09-11','09-15','09-16'],['09-18','09-23']):
    for lat in (20,50):
        S=A[(A.lat==lat)&A.day.isin(days)&A.strat.isin(['b3_g300','b3_g300_sz50','b3_g300_sz100'])]
        g=S.groupby('strat').agg(n=('tot','size'),usd=('tot','mean'),sd=('tot','std'),sh=('sh','mean'),worst=('tot','min'))
        g['t']=g.usd/(g.sd/np.sqrt(g.n)); g['c_per_sh']=100*g.usd/g.sh
        print(days,lat,'ms'); print(g.round(2).to_string())

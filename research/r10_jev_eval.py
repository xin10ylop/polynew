"""Honest Jev evaluation on historical decision points (walk-forward irrelevant: Jev is not fit on our data).

For sampled (market, t) we compute numeric features in code, bucket them semantically for Jev,
and compare Jev's P(up) with the quant TWAP model and the market price (last print known at t)."""
import sys; sys.path.insert(0,'.')
import asyncio, json, time
import numpy as np, pandas as pd
from pm.model import Series1s, fair_up
from pm.data import load_markets
from pm.jev import Jev, make_state, bucket

dur=sys.argv[1]; N=int(sys.argv[2]); seed=int(sys.argv[3]) if len(sys.argv)>3 else 0
S=Series1s('data/btcusdt_1s.parquet')
m=load_markets(dur); m=m[(m.start_ts-4000>S.t0)&(m.end_ts+5<S.t1)]
m=m[m.start_ts>=m.start_ts.max()-14*86400]
rng=np.random.default_rng(seed)
D=int(m.end_ts.iloc[0]-m.start_ts.iloc[0])
pick=m.sample(N,random_state=seed,replace=N>len(m)).reset_index(drop=True)
t=(pick.start_ts+rng.integers(5,D-3,len(pick))).values
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['condition_id','ts','up_price'])
tr=tr[tr.condition_id.isin(pick.condition_id)].sort_values('ts')
g={c:x for c,x in tr.groupby('condition_id')}
mp=np.full(len(pick),np.nan)
for i,(c,tt) in enumerate(zip(pick.condition_id,t)):
    x=g.get(c)
    if x is None: continue
    j=np.searchsorted(x.ts.values,tt+1,side='right')-1   # api_ts<=t+1  => matched before t
    if j>=0 and tt+1-x.ts.values[j]<=20: mp[i]=x.up_price.values[j]
sig=S.sigma_at(t,900)
q,z=fair_up(S,pick.start_ts.values,pick.end_ts.values,pick.price_to_beat.values,t,sig,0.54,df_t=5)
lc=np.log(S.c); i=S.idx(t)-1
r30=(lc[i]-lc[i-30])/(sig*np.sqrt(30)); r300=(lc[i]-lc[i-300])/(sig*np.sqrt(300))
v=S.v; tb=S.tbv; cv=np.concatenate([[0],np.cumsum(v)]); ctb=np.concatenate([[0],np.cumsum(tb)])
V60=cv[i+1]-cv[i-59]; B60=ctb[i+1]-ctb[i-59]; flow=np.where(V60>0,(2*B60-V60)/np.maximum(V60,1e-9),0)
vs=np.sqrt(S.ewma_var(60)[i]); vl=np.sqrt(S.ewma_var(3600)[i]); vr=vs/np.maximum(vl,1e-12)
tau=pick.end_ts.values-t
locked=np.clip((60-tau)/60,0,1)
df=pd.DataFrame(dict(cid=pick.condition_id,t=t,tau=tau,z=z,q=q,mp=mp,r30=r30,r300=r300,flow=flow,vr=vr,locked=locked,y=pick.up_won.values))
def mbucket(p):
    return bucket(p,[0.1,0.3,0.45,0.55,0.7,0.9],["very unlikely (under 10%)","unlikely (10-30%)","leaning no (30-45%)",
                   "coin flip (45-55%)","leaning yes (55-70%)","likely (70-90%)","very likely (over 90%)"])
Q_BLIND={"up":{"type":"noul","instructions":"Will this market settle Up?",
     "criteria":{"true":"The end-of-window average finishes at or above the strike","false":"The end-of-window average finishes below the strike"}},
     "regime":{"type":"choice","instructions":"Which regime best describes current BTC price action?",
     "criteria":{"trend":"Directional move likely to continue","chop":"Noisy mean-reverting action","shock":"Sudden abnormal move, unstable"}}}
Q_MKT={"up_mkt":{"type":"noul","instructions":"Will this market settle Up?",
     "criteria":{"true":"The end-of-window average finishes at or above the strike","false":"The end-of-window average finishes below the strike"}},
     "mispriced":{"type":"choice","instructions":"Compared with the evidence, is the crowd's current Up price too low, fair, or too high?",
     "criteria":{"too_low":"Up is underpriced by the crowd","fair":"The crowd price is about right","too_high":"Up is overpriced by the crowd"}}}
async def main():
    out=[None]*len(df)
    async with Jev(concurrency=10) as j:
        async def one(k):
            r=df.iloc[k]
            feat=dict(z=r.z,tau=r.tau,locked_frac=r.locked,r30_sig=r.r30,r300_sig=r.r300,flow60=r.flow,vol_ratio=r.vr)
            st=make_state(feat)
            try:
                a=await j.ask(st,Q_BLIND)
                res={'jev':a['up']['noul'],'reg_trend':a['regime']['probabilities'].get('trend'),'reg_chop':a['regime']['probabilities'].get('chop'),'reg_shock':a['regime']['probabilities'].get('shock')}
                if r.mp==r.mp:
                    st2=dict(st); st2['crowd_price_for_up']=mbucket(r.mp)
                    b=await j.ask(st2,Q_MKT)
                    res.update({'jev_mkt':b['up_mkt']['noul'],'mis_low':b['mispriced']['probabilities'].get('too_low'),'mis_high':b['mispriced']['probabilities'].get('too_high')})
                out[k]=res
            except Exception as ex:
                out[k]={'err':str(ex)[:100]}
        t0=time.time()
        await asyncio.gather(*[one(k) for k in range(len(df))])
        print('calls',j.calls,'tokens',j.input_tokens,'cost$',round(j.input_tokens*0.042e-6,4),'p50 lat',np.median(j.lat),'secs',time.time()-t0)
    return out
res=asyncio.run(main())
R=pd.concat([df,pd.DataFrame(res)],axis=1)
R.to_parquet(f'data/jev_eval_{dur}_{seed}.parquet')
def ll(p,y):
    p=np.clip(p,0.01,0.99); return -(y*np.log(p)+(1-y)*np.log(1-p)).mean()
def br(p,y): return ((p-y)**2).mean()
V=R.dropna(subset=['jev','mp','jev_mkt'])
print('n',len(R),'with market',len(V))
for c in ['q','mp','jev','jev_mkt']:
    print(f'{c:8s} LL={ll(V[c],V.y):.4f} Brier={br(V[c],V.y):.4f}')

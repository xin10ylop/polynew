"""Feature panel for ML fair value: decision points every `step` s per market, all info causal.

Market info known at true time t: prints with api_ts <= t (api_ts lags match time by >= ~1.1s)."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.model import Series1s, fair_up
from pm.data import load_markets
dur=sys.argv[1]; step=int(sys.argv[2]) if len(sys.argv)>2 else 5
SP=Series1s('data/btcusdt_1s.parquet'); FU=Series1s('data/btcusdt_fut_1s.parquet')
m=load_markets(dur); m=m[(m.start_ts-4000>SP.t0)&(m.end_ts+5<SP.t1)].reset_index(drop=True)
D=int(m.end_ts.iloc[0]-m.start_ts.iloc[0])
offs=np.arange(step,D-1,step)
M=len(m); K=len(offs)
s=np.repeat(m.start_ts.values,K); e=s+D; t=s+np.tile(offs,M)
F=pd.DataFrame({'cid':np.repeat(m.condition_id.values,K),'s':s,'t':t,'tau':e-t,'el':t-s,'y':np.repeat(m.up_won.values,K)})
ptb=np.repeat(m.price_to_beat.values,K)
for name,S,bsd in [('sp',SP,0.54),('fu',FU,0.7)]:
    sig=S.sigma_at(t,900)
    q,z=fair_up(S,s,e,ptb,t,sig,bsd,df_t=5)
    F[f'q_{name}']=q; F[f'z_{name}']=np.clip(z,-20,20)
    lc=np.log(S.c); i=S.idx(t)-1
    for L in [1,3,5,10,30,60,120,300]:
        F[f'r{L}_{name}']=(lc[i]-lc[i-L])/(sig*np.sqrt(L))
    F[f'volr_{name}']=np.sqrt(S.ewma_var(60)[i]/np.maximum(S.ewma_var(3600)[i],1e-18))
    F[f'sig_{name}']=sig*1e4
    cv=np.concatenate([[0],np.cumsum(S.v)]); cb=np.concatenate([[0],np.cumsum(S.tbv)])
    for L in [5,30,120]:
        V=cv[i+1]-cv[i+1-L]; B=cb[i+1]-cb[i+1-L]
        F[f'flow{L}_{name}']=np.where(V>0,(2*B-V)/np.maximum(V,1e-9),0)
        F[f'vol{L}_{name}']=np.log1p(V)
i=SP.idx(t)-1
F['lead5']=F.r5_fu*FU.sigma_at(t,900)-F.r5_sp*SP.sigma_at(t,900)   # futures moved more than spot (last 5s)
F['hour']=(t//3600)%24; F['dow']=((t//86400)+4)%7
# ---- Polymarket tape features (known at t: api_ts <= t)
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['condition_id','ts','up_price','side_up','size','ask'])
tr=tr.sort_values(['condition_id','ts'])
grp={c:(g.ts.values,g.up_price.values,g.side_up.values,g['size'].values) for c,g in tr.groupby('condition_id')}
mp=np.full(len(F),np.nan); age=np.full(len(F),np.nan); mp30=np.full(len(F),np.nan); n30=np.zeros(len(F)); fl30=np.zeros(len(F))
cids=F.cid.values; tt=F.t.values
start=0
for c in m.condition_id.values:
    sl=slice(start,start+K); start+=K
    if c not in grp: continue
    ts,up,su,sz=grp[c]
    tq=tt[sl]
    j=np.searchsorted(ts,tq,side='right')-1
    ok=j>=0
    jj=np.clip(j,0,None)
    mp[sl]=np.where(ok,up[jj],np.nan); age[sl]=np.where(ok,tq-ts[jj],np.nan)
    j30=np.searchsorted(ts,tq-30,side='right')-1
    mp30[sl]=np.where(j30>=0,up[np.clip(j30,0,None)],np.nan)
    a=np.searchsorted(ts,tq-30,side='right'); n30[sl]=j+1-a
    cs=np.concatenate([[0],np.cumsum(np.where(su==1,sz,-sz))])
    fl30[sl]=cs[j+1]-cs[a]
F['mp']=mp; F['mp_age']=age; F['mp_chg30']=mp-mp30; F['n30']=n30; F['pflow30']=np.sign(fl30)*np.log1p(np.abs(fl30))
F.to_parquet(f'data/feat_{dur}.parquet')
print(F.shape, F.mp.notna().mean())

"""Replay model-filtered maker quoting on the recorded live books with queue-aware fills."""
import sys; sys.path.insert(0,'.')
import requests, numpy as np, pandas as pd
from pm.livebook import load_rec, load_deltas
from pm.livefair import LiveFair
from pm.makersim import MakerSim, replay_events, TICK
from pm.fees import taker_fee_per_share

R=load_rec(); D=load_deltas()
LF=LiveFair(R['cl'],R['cb'])
cl_t0=R['cl'].ts.min()//1000; cl_t1=R['cl'].ts.max()//1000
slugs=sorted(set(D.slug))
ev=requests.get('https://gamma-api.polymarket.com/events',params=[('slug',s) for s in slugs]).json()
res={}
for e in ev:
    meta=e.get('eventMetadata') or {}
    if 'finalPrice' in meta and 'priceToBeat' in meta:
        res[e['slug']]=(1 if meta['finalPrice']>=meta['priceToBeat'] else 0, meta['priceToBeat'])
book_span=D.groupby('slug').ts.agg(['min','max'])
done=[]
for s in slugs:
    st=int(s.rsplit('-',1)[1]); dur=300 if '-5m-' in s else 900; en=st+dur
    if s not in res: continue
    if book_span.loc[s,'min']/1000>st+5 or book_span.loc[s,'max']/1000<en-5: continue
    if st-70<cl_t0 or en>cl_t1: continue
    done.append((s,st,en))
print('fully recorded & resolved markets:',len(done),[d[0] for d in done])
thetas=[float(x) for x in (sys.argv[1] if len(sys.argv)>1 else '0.01,0.02,0.03').split(',')]
lat=int(sys.argv[2]) if len(sys.argv)>2 else 150
rows=[]
for s,st,en in done:
    y,K=res[s]
    evs=replay_events(s,D,R['books'],R['trades'])
    for th in thetas:
        def policy(t,book,pos,st=st,en=en,K=K,th=th):
            if t/1000>en-3: return {}
            q=LF.fair(t,st,en,K=K)
            if q!=q: return {}
            out={}
            bb=book.best_bid(); ba=book.best_ask()
            if bb is None or ba is None: return {}
            pu=np.floor((q-th)/TICK+1e-9)*TICK
            pu=min(pu,bb)                          # join best bid at most (never improve)
            if pu>=0.02: out['up']=(round(pu,2),10.0)
            pdn=np.floor(((1-q)-th)/TICK+1e-9)*TICK
            pdn=min(pdn,round(1-ba,2))             # Down best bid = 1 - Up best ask
            if pdn>=0.02: out['dn']=(round(pdn,2),10.0)
            return out
        sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=200)
        f=sim.run(evs,policy,st*1000,en*1000,max_pos=100)
        if len(f):
            f['won']=np.where(f.side_up,y,1-y)
            f['pnl']=(f.won-f.px)*f.sz
            f['reb']=0.2*taker_fee_per_share(f.px)*f.sz
            up=f[f.side_up].sz.sum(); dn=f[~f.side_up].sz.sum()
            rows.append((s,th,len(f),f.sz.sum(),up,dn,(f.px*f.sz).sum(),f.pnl.sum(),f.reb.sum()))
        else:
            rows.append((s,th,0,0,0,0,0,0,0))
O=pd.DataFrame(rows,columns=['slug','theta','nfills','shares','up_sh','dn_sh','cost','pnl','rebate'])
pd.set_option('display.width',200)
print(O.round(2).to_string())
print(O.groupby('theta')[['nfills','shares','cost','pnl','rebate']].sum().round(2))

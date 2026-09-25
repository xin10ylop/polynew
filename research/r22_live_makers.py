"""Maker strategies on recorded live books with queue-aware fills (pm.makersim).
M1: model-filtered two-sided quotes (LiveFair: Chainlink + Coinbase lead), join best bid at most.
M2: pair accumulation (gabagool): bid both sides with bid_up + bid_dn <= 1 - margin; keep completing pairs.
M3: naive join best bid on both sides (baseline)."""
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
res={}
for i in range(0,len(slugs),20):
    ev=requests.get('https://gamma-api.polymarket.com/events',params=[('slug',s) for s in slugs[i:i+20]]).json()
    for e in ev:
        meta=e.get('eventMetadata') or {}
        if 'finalPrice' in meta and 'priceToBeat' in meta:
            res[e['slug']]=(1 if meta['finalPrice']>=meta['priceToBeat'] else 0, meta['priceToBeat'])
span=D.groupby('slug').ts.agg(['min','max'])
done=[]
for s in slugs:
    st=int(s.rsplit('-',1)[1]); dur=300 if '-5m-' in s else 900; en=st+dur
    if s not in res or span.loc[s,'min']/1000>st+5 or span.loc[s,'max']/1000<en-5: continue
    if st-70<cl_t0 or en>cl_t1: continue
    done.append((s,st,en))
print('complete markets',len(done),' 5m:',sum('-5m-' in d[0] for d in done),' 15m:',sum('-15m-' in d[0] for d in done))
lat=int(sys.argv[1]) if len(sys.argv)>1 else 200
SZ=10.0
def pol_M1(th):
    def f(t,book,pos,st,en,K):
        if t/1000>en-5: return {}
        q=LF.fair(t,st,en,K=K)
        bb=book.best_bid(); ba=book.best_ask()
        if q!=q or bb is None or ba is None: return {}
        out={}
        pu=min(np.floor((q-th)/TICK+1e-9)*TICK,bb)
        if pu>=0.03: out['up']=(round(pu,2),SZ)
        pdn=min(np.floor(((1-q)-th)/TICK+1e-9)*TICK,round(1-ba,2))
        if pdn>=0.03: out['dn']=(round(pdn,2),SZ)
        return out
    return f
def pol_M2(margin):
    def f(t,book,pos,st,en,K):
        if t/1000>en-5: return {}
        bb=book.best_bid(); ba=book.best_ask()
        if bb is None or ba is None: return {}
        bu=bb; bd=round(1-ba,2)          # best bids on each side
        # shade the side we already hold more of, so pair cost <= 1-margin
        imb=pos[True]-pos[False]
        tot=1-margin
        if bu+bd>tot:
            ex=round(bu+bd-tot,2)
            if imb>0: bu=round(bu-ex,2)
            elif imb<0: bd=round(bd-ex,2)
            else: bu=round(bu-np.ceil(ex/2*100)/100,2); bd=round(tot-bu,2) if bu+bd>tot else bd
        out={}
        if bu>=0.03 and pos[True]-pos[False]<50: out['up']=(bu,SZ)
        if bd>=0.03 and pos[False]-pos[True]<50: out['dn']=(bd,SZ)
        return out
    return f
def pol_M3():
    def f(t,book,pos,st,en,K):
        if t/1000>en-5: return {}
        bb=book.best_bid(); ba=book.best_ask()
        if bb is None or ba is None: return {}
        return {'up':(bb,SZ),'dn':(round(1-ba,2),SZ)}
    return f
strategies={'M1_th0.01':pol_M1(0.01),'M1_th0.03':pol_M1(0.03),'M2_m0.02':pol_M2(0.02),'M2_m0.04':pol_M2(0.04),'M3_naive':pol_M3()}
rows=[]
for s,st,en in done:
    y,K=res[s]; evs=replay_events(s,D,R['books'],R['trades'])
    for name,p in strategies.items():
        sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=250)
        f=sim.run(evs,lambda t,b,pos,p=p: p(t,b,pos,st,en,K),st*1000,en*1000,max_pos=150)
        if len(f)==0: rows.append((s,name,0,0,0,0,0,0)); continue
        f['won']=np.where(f.side_up,y,1-y)
        up=f[f.side_up].sz.sum(); dn=f[~f.side_up].sz.sum()
        cost=(f.px*f.sz).sum(); payout=(f.won*f.sz).sum(); reb=(0.2*taker_fee_per_share(f.px)*f.sz).sum()
        rows.append((s,name,len(f),up,dn,cost,payout-cost,reb))
O=pd.DataFrame(rows,columns=['slug','strat','nfills','up_sh','dn_sh','cost','pnl','rebate'])
O['dur']=np.where(O.slug.str.contains('-5m-'),'5m','15m')
O.to_parquet('data/live_makers.parquet')
g=O.groupby(['dur','strat']).agg(markets=('slug','nunique'),fills=('nfills','sum'),shares=('up_sh','sum'),dn=('dn_sh','sum'),cost=('cost','sum'),pnl=('pnl','sum'),reb=('rebate','sum'),
    pnl_sd=('pnl','std'))
g['pnl_per_share']=(g.pnl)/(g.shares+g.dn)
g['t_stat']=g.pnl/(g.pnl_sd*np.sqrt(g.markets))
print(g.round(3).to_string())

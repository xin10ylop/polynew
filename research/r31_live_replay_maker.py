"""Out-of-sample: replay the fast-maker strategies on TODAY's own live recording (recorder data, server timestamps)."""
import sys; sys.path.insert(0,'.')
import requests, numpy as np, pandas as pd
from pm.livebook import load_rec, load_deltas
from pm.makersim import MakerSim, replay_events, TICK
from pm.fees import taker_fee_per_share
LATS=[int(x) for x in (sys.argv[1] if len(sys.argv)>1 else '20,50,150').split(',')]
R=load_rec(); D=load_deltas()
slugs=sorted(set(D.slug)); res={}
for i in range(0,len(slugs),20):
    for e in requests.get('https://gamma-api.polymarket.com/events',params=[('slug',s) for s in slugs[i:i+20]]).json():
        meta=e.get('eventMetadata') or {}
        if 'finalPrice' in meta and 'priceToBeat' in meta: res[e['slug']]=1 if meta['finalPrice']>=meta['priceToBeat'] else 0
span=D.groupby('slug').ts.agg(['min','max'])
done=[]
for s in slugs:
    st=int(s.rsplit('-',1)[1]); en=st+(300 if '-5m-' in s else 900)
    if s in res and span.loc[s,'min']/1000<=st-30 and span.loc[s,'max']/1000>=en-5: done.append((s,st,en))
print('complete live markets',len(done),' 5m',sum('-5m-' in d[0] for d in done))
SZ=10.0
rows=[]
for s,st,en in done:
    evs=replay_events(s,D,R['books'],R['trades']); y=res[s]
    for lat in LATS:
        for name,back in (('join',0),('back1',1),('back2',2)):
            def pol(t,book,pos,back=back):
                if t/1000>en-5 or t/1000<st: return {}
                bb=book.best_bid(); ba=book.best_ask()
                if bb is None or ba is None: return {}
                pu=round(bb-back*TICK,2); pdn=round(1-ba-back*TICK,2); imb=pos[True]-pos[False]; o={}
                if pu>=0.03 and imb<30: o['up']=(pu,SZ)
                if pdn>=0.03 and -imb<30: o['dn']=(pdn,SZ)
                return o
            sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=50)
            f=sim.run(evs,pol,st*1000,en*1000,max_pos=10**9)
            if len(f)==0: rows.append((s,lat,name,0,0,0,sim.n_place,sim.n_cancel)); continue
            won=np.where(f.side_up,y,1-y)
            rows.append((s,lat,name,f.sz.sum(),((won-f.px)*f.sz).sum(),(0.2*taker_fee_per_share(f.px)*f.sz).sum(),sim.n_place,sim.n_cancel))
O=pd.DataFrame(rows,columns=['slug','lat','strat','sh','pnl','reb','npl','ncn'])
O['dur']=np.where(O.slug.str.contains('-5m-'),'5m','15m')
O.to_parquet('data/live_replay_maker.parquet')
g=O.groupby(['dur','lat','strat']).agg(mk=('slug','nunique'),sh=('sh','sum'),pnl=('pnl','sum'),reb=('reb','sum'),sd=('pnl','std'),npl=('npl','mean'),ncn=('ncn','mean'))
g['per_sh']=(g.pnl+g.reb)/g.sh; g['t']=(g.pnl+g.reb)/(g.sd*np.sqrt(g.mk)); g['usd_mk']=(g.pnl+g.reb)/g.mk
print(g.round(4).to_string())

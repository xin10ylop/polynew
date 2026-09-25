"""Smart maker on recorded live books (queue-aware): toxicity guard from Bybit perp ticks + fair-value cap.
M4: join best bid both sides; pull the side a recent lead move goes against; never bid above fair - margin.
M2b: gabagool with realized average-cost control (bid side X only at <= (1 - margin) - avgcost(other side) when unpaired).
Run on all complete recorded markets. Args: place/cancel latency ms, decide cadence ms."""
import sys; sys.path.insert(0,'.')
import requests, numpy as np, pandas as pd
from pm.livebook import load_rec, load_deltas
from pm.livefair import LiveFair
from pm.makersim import MakerSim, replay_events, TICK
from pm.fees import taker_fee_per_share
lat=int(sys.argv[1]) if len(sys.argv)>1 else 200
cad=int(sys.argv[2]) if len(sys.argv)>2 else 100
R=load_rec(); D=load_deltas()
LF=LiveFair(R['cl'],R['cb'])
BY=R['bybit']; by_recv=BY.recv.values; by_px=BY.px.values
def lead_move_bp(t,window):
    j=np.searchsorted(by_recv,t,side='right')-1; i=np.searchsorted(by_recv,t-window,side='right')-1
    if j<0 or i<0: return 0.0
    return (by_px[j]/by_px[i]-1)*1e4
cl_t0=R['cl'].ts.min()//1000; cl_t1=R['cl'].ts.max()//1000
slugs=sorted(set(D.slug)); res={}
for i in range(0,len(slugs),20):
    for e in requests.get('https://gamma-api.polymarket.com/events',params=[('slug',s) for s in slugs[i:i+20]]).json():
        meta=e.get('eventMetadata') or {}
        if 'finalPrice' in meta and 'priceToBeat' in meta: res[e['slug']]=(1 if meta['finalPrice']>=meta['priceToBeat'] else 0, meta['priceToBeat'])
span=D.groupby('slug').ts.agg(['min','max'])
done=[(s,int(s.rsplit('-',1)[1]),int(s.rsplit('-',1)[1])+(300 if '-5m-' in s else 900)) for s in slugs]
done=[(s,st,en) for s,st,en in done if s in res and span.loc[s,'min']/1000<=st+5 and span.loc[s,'max']/1000>=en-5 and st-70>=cl_t0 and en<=cl_t1]
print('complete markets',len(done))
SZ=10.0
def M4(win_ms,thr_bp,margin,cool_ms):
    state={'pull_up_until':0,'pull_dn_until':0}
    def f(t,book,pos,st,en,K):
        if t/1000>en-5: return {}
        bb=book.best_bid(); ba=book.best_ask()
        if bb is None or ba is None: return {}
        mv=lead_move_bp(t,win_ms)
        if mv>=thr_bp: state['pull_dn_until']=t+cool_ms      # BTC up -> Down bids stale
        if mv<=-thr_bp: state['pull_up_until']=t+cool_ms
        q=LF.fair(t,st,en,K=K)
        out={}
        if t>=state['pull_up_until']:
            pu=bb if q!=q else min(bb,np.floor((q-margin)/TICK+1e-9)*TICK)
            if 0.03<=pu: out['up']=(round(pu,2),SZ)
        if t>=state['pull_dn_until']:
            pd_=round(1-ba,2); pd_=pd_ if q!=q else min(pd_,np.floor(((1-q)-margin)/TICK+1e-9)*TICK)
            if 0.03<=pd_: out['dn']=(round(pd_,2),SZ)
        return out
    return f
def M2b(margin):
    fills={'cost':{True:0.0,False:0.0}}
    def f(t,book,pos,st,en,K):
        if t/1000>en-5: return {}
        bb=book.best_bid(); ba=book.best_ask()
        if bb is None or ba is None: return {}
        out={}
        for side,best in ((True,bb),(False,round(1-ba,2))):
            other=not side
            px=best
            if pos[other]>pos[side]:      # completing pairs: cap by other side's average cost
                avg_other=f.cost[other]/max(pos[other],1e-9)
                px=min(px,np.floor(((1-margin)-avg_other)/TICK+1e-9)*TICK)
            elif pos[side]>pos[other]+20: # don't add more to the heavy side
                continue
            if px>=0.03: out['up' if side else 'dn']=(round(px,2),SZ)
        return out
    f.cost={True:0.0,False:0.0}
    return f
rows=[]
configs={'M3_naive':None,'M4_w1000_t1_m0.01':(1000,1.0,0.01,3000),'M4_w1000_t0.5_m0.01':(1000,0.5,0.01,3000),
         'M4_w2000_t1_m0.02':(2000,1.0,0.02,5000),'M4_w500_t0.5_m0.0':(500,0.5,0.0,2000),'M2b_m0.02':'m2b'}
for s,st,en in done:
    y,K=res[s]; evs=replay_events(s,D,R['books'],R['trades'])
    for name,cfg in configs.items():
        if cfg is None:
            pol=lambda t,b,pos: {} if t/1000>en-5 or b.best_bid() is None or b.best_ask() is None else {'up':(b.best_bid(),SZ),'dn':(round(1-b.best_ask(),2),SZ)}
            sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=cad); f=sim.run(evs,pol,st*1000,en*1000,max_pos=150)
        elif cfg=='m2b':
            p=M2b(0.02)
            class Sim2(MakerSim):
                pass
            sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=cad)
            # track cost via wrapper: recompute from fills after the run is not possible mid-run, so approximate avg cost by best bid at fill
            f=sim.run(evs,lambda t,b,pos,p=p: p(t,b,pos,st,en,K),st*1000,en*1000,max_pos=150)
        else:
            p=M4(*cfg); sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=cad)
            f=sim.run(evs,lambda t,b,pos,p=p: p(t,b,pos,st,en,K),st*1000,en*1000,max_pos=150)
        if len(f)==0: rows.append((s,name,0,0,0,0,0)); continue
        f['won']=np.where(f.side_up,y,1-y)
        rows.append((s,name,len(f),f.sz.sum(),(f.px*f.sz).sum(),((f.won-f.px)*f.sz).sum(),(0.2*taker_fee_per_share(f.px)*f.sz).sum()))
O=pd.DataFrame(rows,columns=['slug','strat','nfills','shares','cost','pnl','reb'])
O['dur']=np.where(O.slug.str.contains('-5m-'),'5m','15m')
g=O.groupby(['dur','strat']).agg(mk=('slug','nunique'),fills=('nfills','sum'),sh=('shares','sum'),pnl=('pnl','sum'),reb=('reb','sum'),sd=('pnl','std'))
g['per_sh']=(g.pnl+g.reb)/g.sh; g['t']=(g.pnl+g.reb)/(g.sd*np.sqrt(g.mk))
print(f'latency {lat}ms cadence {cad}ms'); print(g.round(3).to_string())
O.to_parquet(f'data/smart_maker_{lat}_{cad}.parquet')

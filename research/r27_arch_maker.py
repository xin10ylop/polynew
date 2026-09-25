"""Historical maker strategies on archived real order books (PendulumFlow), queue-aware (pm.makersim).
Per complete market (5m/15m) in the extracted hours. Fair value: TWAP model on Binance spot (data up to prior second).
Toxicity guard: Binance futures move over the last W completed seconds (in bp)."""
import sys, glob, json; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.makersim import MakerSim, TICK
from pm.model import Series1s, fair_up
from pm.fees import taker_fee_per_share
lat=int(sys.argv[1]) if len(sys.argv)>1 else 200
dur_f=sys.argv[2] if len(sys.argv)>2 else 'both'
SP=Series1s('data/btcusdt_1s.parquet'); FU=Series1s('data/btcusdt_fut_1s.parquet')
lfu=np.log(FU.c)
m5=pd.read_parquet('data/markets_5m.parquet'); m15=pd.read_parquet('data/markets_15m.parquet')
M=pd.concat([m5,m15]).dropna(subset=['up_won','price_to_beat']).set_index('condition_id')
files=sorted(glob.glob('data/arch/*.parquet'))
hours=sorted(int(pd.Timestamp(f.split('/')[-1][:13],tz='UTC').timestamp()) for f in files)
import json, os
qc=json.load(open('data/arch/_qc.json')) if os.path.exists('data/arch/_qc.json') else {}
hs={h for h in hours if qc.get(f'{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}',0)>=30_000_000}
print('clean hours',len(hs),'of',len(hours))
def load_block(cid):
    r=M.loc[cid]; st,en=int(r.start_ts),int(r.end_ts)
    need=range((st-600)//3600*3600,(en+5)//3600*3600+1,3600)
    if not all(h in hs for h in need): return None
    parts=[pd.read_parquet(f'data/arch/{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}.parquet',filters=[('cid','==',cid)]) for h in need]
    return pd.concat(parts,ignore_index=True)
def events_for(cid,df,up_tok):
    ev=[]
    b=df[df.ev==1]
    for ts,bk in zip(b.ts.values,b.book.values):
        d=json.loads(bk); ev.append((int(ts),0,({round(p,4):s for p,s in d['b']},{round(p,4):s for p,s in d['a']})))
    pcg=df[df.ev==0]
    for ts,side,p,sz in zip(pcg.ts.values,pcg.side.values,pcg.price.values,pcg['size'].values):
        ev.append((int(ts),1,(side=='BUY',round(float(p),4),float(sz))))
    t=df[df.ev==2]
    for ts,a,side,p,sz in zip(t.ts.values,t.asset.values,t.side.values,t.price.values,t['size'].values):
        up=(a==up_tok)
        if up and side=='SELL': ev.append((int(ts),2,(True,round(p,4),sz)))
        elif (not up) and side=='BUY': ev.append((int(ts),2,(True,round(1-p,4),sz)))
        elif up and side=='BUY': ev.append((int(ts),2,(False,round(p,4),sz)))
        else: ev.append((int(ts),2,(False,round(1-p,4),sz)))
    ev.sort(key=lambda x:(x[0],x[1]))
    return ev
SZ=10.0
def make_policy(kind,st,en,K,params):
    state={'pu':0,'pd':0}
    def fair(tms):
        t=int(tms//1000)   # model uses closes up to t-1
        q,_=fair_up(SP,np.array([st]),np.array([en]),np.array([K]),np.array([t]),SP.sigma_at(np.array([t]),900),0.54,df_t=5)
        return float(q[0])
    def f(t,book,pos):
        if t/1000>en-5 or t/1000<st: return {}
        bb=book.best_bid(); ba=book.best_ask()
        if bb is None or ba is None: return {}
        bd=round(1-ba,2)
        if kind=='naive': return {'up':(bb,SZ),'dn':(bd,SZ)}
        q=fair(t)
        if kind=='fair':
            m=params['m']; out={}
            pu=min(bb,np.floor((q-m)/TICK+1e-9)*TICK); pdn=min(bd,np.floor(((1-q)-m)/TICK+1e-9)*TICK)
            if pu>=0.03: out['up']=(round(pu,2),SZ)
            if pdn>=0.03: out['dn']=(round(pdn,2),SZ)
            return out
        if kind=='guard':
            W,thr,cool,m=params['W'],params['thr'],params['cool'],params['m']
            s=int(t//1000)-1; i=FU.idx(s)
            mv=(lfu[i]-lfu[i-W])*1e4
            if mv>=thr: state['pd']=t+cool
            if mv<=-thr: state['pu']=t+cool
            out={}
            if t>=state['pu']:
                pu=min(bb,np.floor((q-m)/TICK+1e-9)*TICK)
                if pu>=0.03: out['up']=(round(pu,2),SZ)
            if t>=state['pd']:
                pdn=min(bd,np.floor(((1-q)-m)/TICK+1e-9)*TICK)
                if pdn>=0.03: out['dn']=(round(pdn,2),SZ)
            return out
    return f
CONFIGS={'naive':('naive',{}),'fair_m0.00':('fair',{'m':0.0}),'fair_m0.02':('fair',{'m':0.02}),
         'guard_W2_t1.0_m0.00':('guard',{'W':2,'thr':1.0,'cool':3000,'m':0.0}),
         'guard_W2_t0.5_m0.00':('guard',{'W':2,'thr':0.5,'cool':3000,'m':0.0}),
         'guard_W3_t1.0_m0.01':('guard',{'W':3,'thr':1.0,'cool':5000,'m':0.01})}
cids=[c for c in M.index if M.loc[c].start_ts>=min(hours)+600 and M.loc[c].end_ts<=max(hours)+3600-5]
if dur_f!='both': cids=[c for c in cids if M.loc[c].dur==dur_f]
print('candidate markets',len(cids),flush=True)
rows=[]
for k,cid in enumerate(cids):
    r=M.loc[cid]; df=load_block(cid)
    if df is None or len(df)==0 or (df.ev==1).sum()==0: continue
    if r.start_ts-4000<SP.t0 or r.end_ts+5>SP.t1: continue
    ev=events_for(cid,df,r.up_token)
    for name,(kind,params) in CONFIGS.items():
        sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=250)
        f=sim.run(ev,make_policy(kind,int(r.start_ts),int(r.end_ts),float(r.price_to_beat),params),int(r.start_ts)*1000,int(r.end_ts)*1000,max_pos=150)
        y=int(r.up_won)
        if len(f)==0: rows.append((cid,r.dur,name,0,0,0,0,0)); continue
        won=np.where(f.side_up,y,1-y)
        up=f[f.side_up].sz.sum(); dn=f[~f.side_up].sz.sum()
        rows.append((cid,r.dur,name,len(f),f.sz.sum(),((won-f.px)*f.sz).sum(),(0.2*taker_fee_per_share(f.px)*f.sz).sum(),min(up,dn)))
    if k%20==0:
        O=pd.DataFrame(rows,columns=['cid','dur','strat','nf','sh','pnl','reb','pairs'])
        print(k, O.groupby(['dur','strat']).apply(lambda g: f"mk={g.cid.nunique()} sh={g.sh.sum():.0f} pnl/sh={(g.pnl.sum()+g.reb.sum())/max(g.sh.sum(),1):+.4f}").to_string(),flush=True)
O=pd.DataFrame(rows,columns=['cid','dur','strat','nf','sh','pnl','reb','pairs'])
O.to_parquet(f'data/arch_maker_{lat}.parquet')
g=O.groupby(['dur','strat']).agg(mk=('cid','nunique'),sh=('sh','sum'),pnl=('pnl','sum'),reb=('reb','sum'),sd=('pnl','std'))
g['per_sh']=(g.pnl+g.reb)/g.sh; g['t']=(g.pnl+g.reb)/(g.sd*np.sqrt(g.mk))
print(f'FINAL latency {lat}ms'); print(g.round(4).to_string())

"""Archive maker backtest with millisecond lead-exchange guard. Args: latency_ms dur [max_markets]"""
import sys, glob, json, os; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.makersim import MakerSim, TICK
from pm.model import Series1s, fair_up
from pm.fees import taker_fee_per_share
lat=int(sys.argv[1]); dur_f=sys.argv[2]; maxm=int(sys.argv[3]) if len(sys.argv)>3 else 10**9
SP=Series1s('data/btcusdt_1s.parquet')
m5=pd.read_parquet('data/markets_5m.parquet'); m15=pd.read_parquet('data/markets_15m.parquet')
M=pd.concat([m5,m15]).dropna(subset=['up_won','price_to_beat']).set_index('condition_id')
qc=json.load(open('data/arch/_qc.json'))
files=sorted(glob.glob('data/arch/20*.parquet'))
hours=sorted(int(pd.Timestamp(f.split('/')[-1][:13],tz='UTC').timestamp()) for f in files)
hs={h for h in hours if qc.get(f'{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}',0)>=30_000_000}
FUT={}
def fut_for(day):
    if day not in FUT:
        p=f'data/fut_ms/{day}.parquet'
        FUT[day]=pd.read_parquet(p) if os.path.exists(p) else None
    return FUT[day]
def load_block(cid):
    r=M.loc[cid]; st,en=int(r.start_ts),int(r.end_ts)
    need=range((st-600)//3600*3600,(en+5)//3600*3600+1,3600)
    if not all(h in hs for h in need): return None
    return pd.concat([pd.read_parquet(f'data/arch/{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}.parquet',filters=[('cid','==',cid)]) for h in need],ignore_index=True)
def events_for(df,up_tok):
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
def make_policy(st,en,K,cfg,lead_ms,lead_px):
    state={'pu':0,'pd':0}; qcache={}
    def fair(tms):
        s=int(tms//1000)
        if s not in qcache:
            q,_=fair_up(SP,np.array([st]),np.array([en]),np.array([K]),np.array([s]),SP.sigma_at(np.array([s]),900),0.54,df_t=5)
            qcache[s]=float(q[0])
        return qcache[s]
    def lead_move(t,W):
        j=np.searchsorted(lead_ms,t,side='right')-1; i=np.searchsorted(lead_ms,t-W,side='right')-1
        if i<0 or j<0: return 0.0
        return (lead_px[j]/lead_px[i]-1)*1e4
    def f(t,book,pos):
        if t/1000>en-5 or t/1000<st: return {}
        bb=book.best_bid(); ba=book.best_ask()
        if bb is None or ba is None: return {}
        bd=round(1-ba,2)
        back=cfg.get('back',0)*TICK
        pu=round(bb-back,2); pdn=round(bd-back,2)
        if cfg.get('fair') is not None:
            q=fair(t); m=cfg['fair']
            pu=min(pu,np.floor((q-m)/TICK+1e-9)*TICK); pdn=min(pdn,np.floor(((1-q)-m)/TICK+1e-9)*TICK)
        if cfg.get('W'):
            mv=lead_move(t,cfg['W'])
            if mv>=cfg['thr']: state['pd']=t+cfg['cool']
            if mv<=-cfg['thr']: state['pu']=t+cfg['cool']
        out={}
        imb=pos[True]-pos[False]; MI=cfg.get('maxi',30)
        if t>=state['pu'] and pu>=0.03 and imb<MI: out['up']=(round(pu,2),SZ)
        if t>=state['pd'] and pdn>=0.03 and -imb<MI: out['dn']=(round(pdn,2),SZ)
        return out
    return f
CFG={'naive':{},'naive_back1':{'back':1},
     'guard_W500_t0.5':{'W':500,'thr':0.5,'cool':2000},'guard_W1000_t0.7':{'W':1000,'thr':0.7,'cool':2000},
     'guard_W500_t0.5_fair0':{'W':500,'thr':0.5,'cool':2000,'fair':0.0},'guard_W500_t0.5_back1':{'W':500,'thr':0.5,'cool':2000,'back':1}}
cids=[c for c in M.index if M.loc[c].dur==dur_f]
cids=[c for c in cids if all(h in hs for h in range((int(M.loc[c].start_ts)-600)//3600*3600,(int(M.loc[c].end_ts)+5)//3600*3600+1,3600))][:maxm]
print('markets',len(cids),flush=True)
rows=[]
for k,cid in enumerate(cids):
    r=M.loc[cid]; st,en=int(r.start_ts),int(r.end_ts)
    fu=fut_for(pd.Timestamp(st,unit='s').strftime('%Y-%m-%d'))
    if fu is None: continue
    df=load_block(cid)
    if df is None or (df.ev==1).sum()==0: continue
    ev=events_for(df,r.up_token)
    sel=(fu.ms.values>=(st-30)*1000)&(fu.ms.values<=(en+5)*1000)
    lms=fu.ms.values[sel]; lpx=fu.px.values[sel]
    for name,cfg in CFG.items():
        sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=50)
        f=sim.run(ev,make_policy(st,en,float(r.price_to_beat),cfg,lms,lpx),st*1000,en*1000,max_pos=10**9)
        y=int(r.up_won)
        if len(f)==0: rows.append((cid,name,0,0,0,0,0,0)); continue
        won=np.where(f.side_up,y,1-y)
        up=f[f.side_up].sz.sum(); dn=f[~f.side_up].sz.sum()
        rows.append((cid,name,len(f),f.sz.sum(),((won-f.px)*f.sz).sum(),(0.2*taker_fee_per_share(f.px)*f.sz).sum(),min(up,dn),abs(up-dn)))
    if k%25==0: print(k,flush=True)
O=pd.DataFrame(rows,columns=['cid','strat','nf','sh','pnl','reb','pairs','unpaired'])
O.to_parquet(f'data/arch_maker_ms_{dur_f}_{lat}.parquet')
g=O.groupby('strat').agg(mk=('cid','nunique'),sh=('sh','sum'),pnl=('pnl','sum'),reb=('reb','sum'),sd=('pnl','std'),pairs=('pairs','mean'),unp=('unpaired','mean'))
g['per_sh']=(g.pnl+g.reb)/g.sh; g['t']=(g.pnl+g.reb)/(g.sd*np.sqrt(g.mk)); g['sh_per_mk']=g.sh/g.mk
print(f'FINAL {dur_f} latency {lat}ms'); print(g.round(4).to_string())

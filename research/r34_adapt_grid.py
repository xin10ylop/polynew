"""Regime-adaptation maker grid (recent regime). Extends r30 with: pull-both guard, fair-value cap (TWAP model),
calm-only quoting (BTC futures move over last 60s), time-in-window limits. Args: dur lats workers ; env START_MIN/START_MAX/TAG/CFGSET"""
import sys, glob, json, os; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from multiprocessing import Pool
dur_f=sys.argv[1]; LATS=[int(x) for x in sys.argv[2].split(',')]; NW=int(sys.argv[3]) if len(sys.argv)>3 else 3
G3={'back':2,'W':300,'thr':0.3,'cool':1500}
CFGSETS={
 'adapt':{
  'b2_g300':dict(G3),
  'b2_g300_both':dict(G3,both=1),
  'b2_g200_t0.2':{'back':2,'W':200,'thr':0.2,'cool':2000},
  'b2_g200_t0.2_both':{'back':2,'W':200,'thr':0.2,'cool':2000,'both':1},
  'b3_g300':{'back':3,'W':300,'thr':0.3,'cool':1500},
  'b2_g300_fair1':dict(G3,fair=0.01),
  'b2_g300_calm3':dict(G3,calm=3.0),
  'b2_g300_t180':dict(G3,tmax=180),
  'b2_g300_skip60':dict(G3,tmin=60),
 },
 'size':{
  'b3_g300':{'back':3,'W':300,'thr':0.3,'cool':1500},
  'b3_g300_sz50':{'back':3,'W':300,'thr':0.3,'cool':1500,'sz':50.0},
  'b3_g300_sz100':{'back':3,'W':300,'thr':0.3,'cool':1500,'sz':100.0},
  'b4_g300':{'back':4,'W':300,'thr':0.3,'cool':1500},
 },
 'test2':{
  'b3_g300':{'back':3,'W':300,'thr':0.3,'cool':1500},
  'b2_g300_fair1':{'back':2,'W':300,'thr':0.3,'cool':1500,'fair':0.01},
  'b3_g300_fair1':{'back':3,'W':300,'thr':0.3,'cool':1500,'fair':0.01},
  'b3_g300_sz100':{'back':3,'W':300,'thr':0.3,'cool':1500,'sz':100.0},
 },
 'final_b3':{
  'b3_g300':{'back':3,'W':300,'thr':0.3,'cool':1500},
  'b2_g300':{'back':2,'W':300,'thr':0.3,'cool':1500},
 },
 'ldelay':{
  'b3_g300_ld0':{'back':3,'W':300,'thr':0.3,'cool':1500,'ld':0},
  'b3_g300_ld50':{'back':3,'W':300,'thr':0.3,'cool':1500,'ld':50},
  'b3_g300_ld100':{'back':3,'W':300,'thr':0.3,'cool':1500,'ld':100},
  'b3_g300_ld150':{'back':3,'W':300,'thr':0.3,'cool':1500,'ld':150},
  'b3_noguard':{'back':3,'W':300,'thr':999,'cool':1500},
 },
 'lsrc':{
  'bn_ld0':{'back':3,'W':300,'thr':0.3,'cool':1500,'ld':0},
  'bn_ld230':{'back':3,'W':300,'thr':0.3,'cool':1500,'ld':230},
  'cb_ld0':{'back':3,'W':300,'thr':0.3,'cool':1500,'src':'cb','cld':0},
  'cb_ld50':{'back':3,'W':300,'thr':0.3,'cool':1500,'src':'cb','cld':50},
  'both_200_50':{'back':3,'W':300,'thr':0.3,'cool':1500,'src':'both','ld':200,'cld':50},
  'cb_ld50_t02':{'back':3,'W':300,'thr':0.2,'cool':1500,'src':'cb','cld':50},
 },
 'test':{
  'b3_g300':{'back':3,'W':300,'thr':0.3,'cool':1500},
  'b2_g300_fair1':{'back':2,'W':300,'thr':0.3,'cool':1500,'fair':0.01},
  'b3_g300_fair1':{'back':3,'W':300,'thr':0.3,'cool':1500,'fair':0.01},
 }}
CFG=CFGSETS[os.environ.get('CFGSET','adapt')]
def setup():
    global M,hs,SP
    from pm.model import Series1s
    m5=pd.read_parquet('data/markets_5m.parquet'); m15=pd.read_parquet('data/markets_15m.parquet')
    M=pd.concat([m5,m15]).dropna(subset=['up_won','price_to_beat']).set_index('condition_id')
    qc=json.load(open('data/arch/_qc.json'))
    files=sorted(glob.glob('data/arch/20*.parquet'))
    hours=sorted(int(pd.Timestamp(f.split('/')[-1][:13],tz='UTC').timestamp()) for f in files)
    hs={h for h in hours if qc.get(f'{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}',0)>=30_000_000}
    SP=Series1s('data/btcusdt_1s.parquet')
def work(cid):
    from pm.makersim import MakerSim, TICK
    from pm.fees import taker_fee_per_share
    from pm.model import fair_up
    r=M.loc[cid]; st,en=int(r.start_ts),int(r.end_ts)
    need=range((st-600)//3600*3600,(en+5)//3600*3600+1,3600)
    if not all(h in hs for h in need): return []
    p=f'data/fut_ms/{pd.Timestamp(st,unit="s"):%Y-%m-%d}.parquet'
    if not os.path.exists(p): return []
    fu=pd.read_parquet(p); fu=fu[(fu.ms>=(st-90)*1000)&(fu.ms<=(en+5)*1000)]
    lms,lpx=fu.ms.values,fu.px.values
    pc=f'data/cb_ms/{pd.Timestamp(st,unit="s"):%Y-%m-%d}.parquet'
    if any(c.get('src','bn')!='bn' for c in CFG.values()):
        if not os.path.exists(pc): return []
        cb=pd.read_parquet(pc); cb=cb[(cb.ms>=(st-90)*1000)&(cb.ms<=(en+5)*1000)]
        cms,cpx=cb.ms.values,cb.px.values
    else:
        cms=cpx=np.array([])
    df=pd.concat([pd.read_parquet(f'data/arch/{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}.parquet',filters=[('cid','==',cid)]) for h in need],ignore_index=True)
    if (df.ev==1).sum()==0: return []
    ev=[]
    b=df[df.ev==1]
    for ts,bk in zip(b.ts.values,b.book.values):
        d=json.loads(bk); ev.append((int(ts),0,({round(p,4):s for p,s in d['b']},{round(p,4):s for p,s in d['a']})))
    pcg=df[df.ev==0]
    for ts,side,pp,sz in zip(pcg.ts.values,pcg.side.values,pcg.price.values,pcg['size'].values):
        ev.append((int(ts),1,(side=='BUY',round(float(pp),4),float(sz))))
    t=df[df.ev==2]
    for ts,a,side,pp,sz in zip(t.ts.values,t.asset.values,t.side.values,t.price.values,t['size'].values):
        up=(a==r.up_token)
        if up and side=='SELL': ev.append((int(ts),2,(True,round(pp,4),sz)))
        elif (not up) and side=='BUY': ev.append((int(ts),2,(True,round(1-pp,4),sz)))
        elif up and side=='BUY': ev.append((int(ts),2,(False,round(pp,4),sz)))
        else: ev.append((int(ts),2,(False,round(1-pp,4),sz)))
    ev.sort(key=lambda x:(x[0],x[1]))
    K=float(r.price_to_beat); y=int(r.up_won)
    fair_ok = st-4000>SP.t0 and en+5<SP.t1
    qcache={}
    def fair(tt):
        s=int(tt//1000)
        if s not in qcache:
            q,_=fair_up(SP,np.array([st]),np.array([en]),np.array([K]),np.array([s]),SP.sigma_at(np.array([s]),900),0.54,df_t=5)
            qcache[s]=float(q[0])
        return qcache[s]
    def lead_mv(tt,W,ms=lms,px=lpx):
        j=np.searchsorted(ms,tt,side='right')-1; i=np.searchsorted(ms,tt-W,side='right')-1
        return (px[j]/px[i]-1)*1e4 if (i>=0 and j>=0) else 0.0
    def guard_mv(tt,cfg):
        # BTC move seen by the bot at tt: each source is only visible after its transport delay
        src=cfg.get('src','bn'); mvs=[]
        if src in ('bn','both'): mvs.append(lead_mv(tt-cfg.get('ld',0),cfg['W']))
        if src in ('cb','both'): mvs.append(lead_mv(tt-cfg.get('cld',0),cfg['W'],cms,cpx))
        return max(mvs,key=abs)  # the larger move decides
    out=[]
    for lat in LATS:
        for name,cfg in CFG.items():
            if cfg.get('fair') is not None and not fair_ok: continue
            state={'pu':0,'pd':0}
            def pol(tt,book,pos,cfg=cfg,state=state):
                el=tt/1000-st
                if tt/1000>en-5 or el<0: return {}
                if cfg.get('tmax') is not None and el>cfg['tmax']: return {}
                if cfg.get('tmin') is not None and el<cfg['tmin']: return {}
                bb=book.best_bid(); ba=book.best_ask()
                if bb is None or ba is None: return {}
                back=cfg['back']*TICK; pu=round(bb-back,2); pdn=round(1-ba-back,2)
                if cfg.get('calm') is not None and abs(lead_mv(tt,60000))>cfg['calm']: return {}
                mv=guard_mv(tt,cfg)  # ld/cld: Binance/Coinbase feed transport delay (ms)
                if abs(mv)>=cfg['thr']:
                    if cfg.get('both'):
                        state['pu']=state['pd']=tt+cfg['cool']
                    elif mv>0: state['pd']=tt+cfg['cool']
                    else: state['pu']=tt+cfg['cool']
                if cfg.get('fair') is not None:
                    q=fair(tt); m=cfg['fair']
                    pu=min(pu,np.floor((q-m)/TICK+1e-9)*TICK); pdn=min(pdn,np.floor(((1-q)-m)/TICK+1e-9)*TICK)
                imb=pos[True]-pos[False]; o={}; sz=cfg.get('sz',10.0); mi=cfg.get('maxi',3*sz)
                if tt>=state['pu'] and pu>=0.03 and imb<mi: o['up']=(round(pu,2),sz)
                if tt>=state['pd'] and pdn>=0.03 and -imb<mi: o['dn']=(round(pdn,2),sz)
                return o
            sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=50)
            f=sim.run(ev,pol,st*1000,en*1000,max_pos=10**9)
            if len(f)==0: out.append((cid,st,lat,name,0,0.0,0.0,0.0)); continue
            won=np.where(f.side_up.astype(bool),y,1-y)
            out.append((cid,st,lat,name,len(f),float(f.sz.sum()),float(((won-f.px)*f.sz).sum()),float((0.2*taker_fee_per_share(f.px)*f.sz).sum())))
    return out
if __name__=='__main__':
    setup()
    smin=int(os.environ.get('START_MIN','0')); smax=int(os.environ.get('START_MAX','9999999999'))
    cids=[c for c in M.index if M.loc[c].dur==dur_f and smin<=int(M.loc[c].start_ts)<=smax and all(h in hs for h in range((int(M.loc[c].start_ts)-600)//3600*3600,(int(M.loc[c].end_ts)+5)//3600*3600+1,3600))]
    print('markets',len(cids),flush=True)
    rows=[]
    with Pool(NW,initializer=setup) as pool:
        for k,res in enumerate(pool.imap_unordered(work,cids,chunksize=2)):
            rows+=res
            if k%50==0: print(k,flush=True)
    O=pd.DataFrame(rows,columns=['cid','st','lat','strat','nf','sh','pnl','reb'])
    O.to_parquet(f'data/adapt_{dur_f}{os.environ.get("TAG","")}.parquet')
    O['tot']=O.pnl+O.reb; O['block']=pd.to_datetime(O.st,unit='s').dt.strftime('%m-%d')
    g=O.groupby(['lat','strat']).agg(mk=('cid','nunique'),sh=('sh','sum'),tot=('tot','sum'),sd=('tot','std'))
    g['per_sh']=g.tot/g.sh; g['usd_mk']=g.tot/g.mk; g['t']=g.usd_mk/(g.sd/np.sqrt(g.mk))
    print(g.round(4).to_string())
    b=O.groupby(['lat','strat','block']).agg(tot=('tot','sum'),sh=('sh','sum')); b['per_sh']=b.tot/b.sh
    print(b.per_sh.unstack('block').round(4).to_string())

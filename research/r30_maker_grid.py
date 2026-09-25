"""Decisive maker grid on all clean archived hours (parallel). Per-market results with block/date for consistency.
Args: dur  latencies(csv)  [workers]"""
import sys, glob, json, os; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from multiprocessing import Pool
dur_f=sys.argv[1]; LATS=[int(x) for x in sys.argv[2].split(',')]; NW=int(sys.argv[3]) if len(sys.argv)>3 else 3
CFGSETS={'default':{'join':{'back':0},'back1':{'back':1},'back2':{'back':2},'back1_guard':{'back':1,'W':500,'thr':0.5,'cool':2000}},
 'final':{'join':{'back':0},'back1':{'back':1},'back2':{'back':2},'back1_g500':{'back':1,'W':500,'thr':0.5,'cool':2000},
           'back2_g300':{'back':2,'W':300,'thr':0.3,'cool':1500},'back2_sz25':{'back':2,'sz':25.0},'back2_sz50':{'back':2,'sz':50.0}},
 'final2':{'back2':{'back':2},'back1_g500':{'back':1,'W':500,'thr':0.5,'cool':2000},'back2_g300':{'back':2,'W':300,'thr':0.3,'cool':1500},'back2_sz25':{'back':2,'sz':25.0}},
 'queue':{'back2':{'back':2},'back2_qm1.5':{'back':2,'qm':1.5},'back2_qm2':{'back':2,'qm':2.0},'back1_g500':{'back':1,'W':500,'thr':0.5,'cool':2000},
           'back1_g500_qm1.5':{'back':1,'W':500,'thr':0.5,'cool':2000,'qm':1.5}},
 'guard150':{'join':{'back':0},'back2':{'back':2},'join_g300_t0.3':{'back':0,'W':300,'thr':0.3,'cool':1500},'join_g500_t0.5':{'back':0,'W':500,'thr':0.5,'cool':2000},
             'back1_g300_t0.3':{'back':1,'W':300,'thr':0.3,'cool':1500},'back1_g500_t0.5':{'back':1,'W':500,'thr':0.5,'cool':2000},'back2_g300_t0.3':{'back':2,'W':300,'thr':0.3,'cool':1500}}}
CFG=CFGSETS[os.environ.get('CFGSET','default')]
def setup():
    global M,hs,SZ
    m5=pd.read_parquet('data/markets_5m.parquet'); m15=pd.read_parquet('data/markets_15m.parquet')
    M=pd.concat([m5,m15]).dropna(subset=['up_won','price_to_beat']).set_index('condition_id')
    qc=json.load(open('data/arch/_qc.json'))
    files=sorted(glob.glob('data/arch/20*.parquet'))
    hours=sorted(int(pd.Timestamp(f.split('/')[-1][:13],tz='UTC').timestamp()) for f in files)
    hs={h for h in hours if qc.get(f'{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}',0)>=30_000_000}
    SZ=10.0
def work(cid):
    from pm.makersim import MakerSim, TICK
    from pm.fees import taker_fee_per_share
    r=M.loc[cid]; st,en=int(r.start_ts),int(r.end_ts)
    need=range((st-600)//3600*3600,(en+5)//3600*3600+1,3600)
    if not all(h in hs for h in need): return []
    df=pd.concat([pd.read_parquet(f'data/arch/{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}.parquet',filters=[('cid','==',cid)]) for h in need],ignore_index=True)
    if (df.ev==1).sum()==0: return []
    ev=[]
    b=df[df.ev==1]
    for ts,bk in zip(b.ts.values,b.book.values):
        d=json.loads(bk); ev.append((int(ts),0,({round(p,4):s for p,s in d['b']},{round(p,4):s for p,s in d['a']})))
    pcg=df[df.ev==0]
    for ts,side,p,sz in zip(pcg.ts.values,pcg.side.values,pcg.price.values,pcg['size'].values):
        ev.append((int(ts),1,(side=='BUY',round(float(p),4),float(sz))))
    t=df[df.ev==2]
    for ts,a,side,p,sz in zip(t.ts.values,t.asset.values,t.side.values,t.price.values,t['size'].values):
        up=(a==r.up_token)
        if up and side=='SELL': ev.append((int(ts),2,(True,round(p,4),sz)))
        elif (not up) and side=='BUY': ev.append((int(ts),2,(True,round(1-p,4),sz)))
        elif up and side=='BUY': ev.append((int(ts),2,(False,round(p,4),sz)))
        else: ev.append((int(ts),2,(False,round(1-p,4),sz)))
    ev.sort(key=lambda x:(x[0],x[1]))
    fu=None
    p=f'data/fut_ms/{pd.Timestamp(st,unit="s"):%Y-%m-%d}.parquet'
    if os.path.exists(p):
        fu=pd.read_parquet(p); fu=fu[(fu.ms>=(st-30)*1000)&(fu.ms<=(en+5)*1000)]
        lms,lpx=fu.ms.values,fu.px.values
    y=int(r.up_won); out=[]
    for lat in LATS:
        for name,cfg in CFG.items():
            if cfg.get('W') and fu is None: continue
            state={'pu':0,'pd':0}
            def pol(tt,book,pos,cfg=cfg,state=state):
                if tt/1000>en-5 or tt/1000<st: return {}
                bb=book.best_bid(); ba=book.best_ask()
                if bb is None or ba is None: return {}
                back=cfg['back']*TICK; pu=round(bb-back,2); pdn=round(1-ba-back,2)
                if cfg.get('W'):
                    j=np.searchsorted(lms,tt,side='right')-1; i=np.searchsorted(lms,tt-cfg['W'],side='right')-1
                    if i>=0 and j>=0:
                        mv=(lpx[j]/lpx[i]-1)*1e4
                        if mv>=cfg['thr']: state['pd']=tt+cfg['cool']
                        if mv<=-cfg['thr']: state['pu']=tt+cfg['cool']
                imb=pos[True]-pos[False]; o={}; sz=cfg.get('sz',SZ); mi=cfg.get('maxi',3*sz)
                if tt>=state['pu'] and pu>=0.03 and imb<mi: o['up']=(pu,sz)
                if tt>=state['pd'] and pdn>=0.03 and -imb<mi: o['dn']=(pdn,sz)
                return o
            sim=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=50,queue_mult=cfg.get('qm',1.0))
            f=sim.run(ev,pol,st*1000,en*1000,max_pos=10**9)
            if len(f)==0:
                out.append((cid,r.dur,st,lat,name,0,0.0,0.0,0.0,sim.n_place,sim.n_cancel)); continue
            won=np.where(f.side_up,y,1-y)
            out.append((cid,r.dur,st,lat,name,len(f),float(f.sz.sum()),float(((won-f.px)*f.sz).sum()),float((0.2*taker_fee_per_share(f.px)*f.sz).sum()),sim.n_place,sim.n_cancel))
    return out
if __name__=='__main__':
    setup()
    cids=[c for c in M.index if M.loc[c].dur==dur_f and all(h in hs for h in range((int(M.loc[c].start_ts)-600)//3600*3600,(int(M.loc[c].end_ts)+5)//3600*3600+1,3600))]
    smin=int(os.environ.get('START_MIN','0')); smax=int(os.environ.get('START_MAX','9999999999'))
    cids=[c for c in cids if smin<=int(M.loc[c].start_ts)<=smax]
    print('markets',len(cids),flush=True)
    rows=[]
    with Pool(NW,initializer=setup) as pool:
        for k,res in enumerate(pool.imap_unordered(work,cids,chunksize=2)):
            rows+=res
            if k%50==0: print(k,flush=True)
    O=pd.DataFrame(rows,columns=['cid','dur','st','lat','strat','nf','sh','pnl','reb','n_place','n_cancel'])
    O.to_parquet(f'data/grid_{dur_f}{os.environ.get("TAG","")}.parquet')
    O['block']=pd.to_datetime(O.st,unit='s').dt.strftime('%m-%d')
    D=int(300 if dur_f=='5m' else 900)
    g=O.groupby(['lat','strat']).agg(mk=('cid','nunique'),sh=('sh','sum'),pnl=('pnl','sum'),reb=('reb','sum'),sd=('pnl','std'),pl=('n_place','mean'),cn=('n_cancel','mean'))
    g['per_sh']=(g.pnl+g.reb)/g.sh; g['t']=(g.pnl+g.reb)/(g.sd*np.sqrt(g.mk)); g['usd_per_mk']=(g.pnl+g.reb)/g.mk; g['orders_per_s']=(g.pl+g.cn)/D
    print(g.round(4).to_string())
    b=O.assign(tot=O.pnl+O.reb).groupby(['lat','strat','block']).agg(tot=('tot','sum'),sh=('sh','sum'))
    b['per_sh']=b.tot/b.sh
    print(b.per_sh.unstack('block').round(4).to_string())

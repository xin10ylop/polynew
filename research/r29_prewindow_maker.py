"""Pre-window market making on archived real books (queue-aware). Quote only in [start-W, start-2s].
Variants: join best bid both sides; fixed pair prices (e.g. 0.49/0.49); improve-by-one-tick when spread>1c.
Inventory held to resolution. Args: latency_ms dur"""
import sys, glob, json; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.makersim import MakerSim, TICK
from pm.fees import taker_fee_per_share
lat=int(sys.argv[1]); dur_f=sys.argv[2]
m5=pd.read_parquet('data/markets_5m.parquet'); m15=pd.read_parquet('data/markets_15m.parquet')
M=pd.concat([m5,m15]).dropna(subset=['up_won','price_to_beat']).set_index('condition_id')
qc=json.load(open('data/arch/_qc.json'))
files=sorted(glob.glob('data/arch/20*.parquet'))
hours=sorted(int(pd.Timestamp(f.split('/')[-1][:13],tz='UTC').timestamp()) for f in files)
hs={h for h in hours if qc.get(f'{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}',0)>=30_000_000}
WMAX=900
def load_block(cid):
    r=M.loc[cid]; st=int(r.start_ts)
    need=range((st-WMAX-60)//3600*3600,(st+5)//3600*3600+1,3600)
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
def pol(st,cfg):
    W=cfg['W']
    def f(t,book,pos):
        if t<(st-W)*1000 or t>(st-2)*1000: return {}
        bb=book.best_bid(); ba=book.best_ask()
        if bb is None or ba is None: return {}
        bd=round(1-ba,2)
        if cfg['mode']=='join': pu,pdn=bb,bd
        elif cfg['mode']=='fixed': pu,pdn=min(bb,cfg['p']),min(bd,cfg['p'])
        elif cfg['mode']=='improve':   # step inside the spread if it is > 1 tick
            pu=round(bb+TICK,2) if ba-bb>TICK+1e-9 else bb
            bd2=round(1-ba,2); pdn=round(bd2+TICK,2) if ba-bb>2*TICK+1e-9 else bd2
        pu=min(pu,cfg.get('cap',0.99)); pdn=min(pdn,cfg.get('cap',0.99))
        imb=pos[True]-pos[False]; out={}
        if imb<cfg.get('maxi',50) and pu>=0.05: out['up']=(round(pu,2),SZ)
        if -imb<cfg.get('maxi',50) and pdn>=0.05: out['dn']=(round(pdn,2),SZ)
        return out
    return f
CFG={'join_W300':{'mode':'join','W':300},'join_W900':{'mode':'join','W':900},'join_W300_cap0.50':{'mode':'join','W':300,'cap':0.50},
     'fixed0.49_W900':{'mode':'fixed','p':0.49,'W':900},'fixed0.48_W900':{'mode':'fixed','p':0.48,'W':900},'improve_W300':{'mode':'improve','W':300,'cap':0.50}}
cids=[c for c in M.index if M.loc[c].dur==dur_f]
cids=[c for c in cids if all(h in hs for h in range((int(M.loc[c].start_ts)-WMAX-60)//3600*3600,(int(M.loc[c].start_ts)+5)//3600*3600+1,3600))]
print('markets',len(cids),flush=True)
rows=[]
for k,cid in enumerate(cids):
    r=M.loc[cid]; st=int(r.start_ts); df=load_block(cid)
    if df is None or (df.ev==1).sum()==0: continue
    ev=[e for e in events_for(df,r.up_token) if e[0]<=(st+2)*1000]
    y=int(r.up_won)
    for name,cfg in CFG.items():
        f=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=250).run(ev,pol(st,cfg),(st-cfg['W'])*1000,(st-2)*1000,max_pos=10**9)
        if len(f)==0: rows.append((cid,name,0,0,0,0,0,0)); continue
        won=np.where(f.side_up,y,1-y); up=f[f.side_up].sz.sum(); dn=f[~f.side_up].sz.sum()
        rows.append((cid,name,len(f),f.sz.sum(),((won-f.px)*f.sz).sum(),(0.2*taker_fee_per_share(f.px)*f.sz).sum(),min(up,dn),(f.px*f.sz).sum()/f.sz.sum()))
    if k%25==0: print(k,flush=True)
O=pd.DataFrame(rows,columns=['cid','strat','nf','sh','pnl','reb','pairs','avgpx'])
O.to_parquet(f'data/prewin_{dur_f}_{lat}.parquet')
g=O.groupby('strat').agg(mk=('cid','nunique'),fill_mk=('nf',lambda x:(x>0).sum()),sh=('sh','sum'),pnl=('pnl','sum'),reb=('reb','sum'),sd=('pnl','std'),avgpx=('avgpx','mean'),pairs=('pairs','mean'))
g['per_sh']=(g.pnl+g.reb)/g.sh; g['t']=(g.pnl+g.reb)/(g.sd*np.sqrt(g.mk)); g['sh_per_mk']=g.sh/g.mk
print(f'FINAL prewindow {dur_f} latency {lat}ms'); print(g.round(4).to_string())

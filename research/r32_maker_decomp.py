"""Decompose deep-maker fills: markouts, price bands, paired vs unpaired PnL. Args: back latency nmarkets"""
import sys, glob, json; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.makersim import MakerSim, TICK
back=int(sys.argv[1]); lat=int(sys.argv[2]); NM=int(sys.argv[3])
m5=pd.read_parquet('data/markets_5m.parquet').dropna(subset=['up_won']).set_index('condition_id')
qc=json.load(open('data/arch/_qc.json'))
hours=sorted(int(pd.Timestamp(f.split('/')[-1][:13],tz='UTC').timestamp()) for f in glob.glob('data/arch/20*.parquet'))
hs={h for h in hours if qc.get(f'{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}',0)>=30_000_000}
cids=[c for c in m5.index if all(h in hs for h in range((int(m5.loc[c].start_ts)-600)//3600*3600,(int(m5.loc[c].end_ts)+5)//3600*3600+1,3600))]
rng=np.random.default_rng(0); cids=list(rng.choice(cids,min(NM,len(cids)),replace=False))
fills=[]; mk=[]
for cid in cids:
    r=m5.loc[cid]; st,en=int(r.start_ts),int(r.end_ts)
    need=range((st-600)//3600*3600,(en+5)//3600*3600+1,3600)
    df=pd.concat([pd.read_parquet(f'data/arch/{pd.Timestamp(h,unit="s"):%Y-%m-%dT%H}.parquet',filters=[('cid','==',cid)]) for h in need],ignore_index=True)
    if (df.ev==1).sum()==0: continue
    ev=[]
    for ts,bk in zip(df[df.ev==1].ts.values,df[df.ev==1].book.values):
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
    # mid series (Up) from book grid for markouts: use best_bid/best_ask columns of price_change rows
    pc=df[(df.ev==0)&df.bb.notna()&df.ba.notna()&(df.asset==r.up_token)][['ts','bb','ba']].sort_values('ts')
    mts=pc.ts.values; mid=((pc.bb+pc.ba)/2).values
    def mid_at(tq):
        j=np.searchsorted(mts,tq,side='right')-1
        return mid[j] if j>=0 else np.nan
    def pol(tt,book,pos):
        if tt/1000>en-5 or tt/1000<st: return {}
        bb=book.best_bid(); ba=book.best_ask()
        if bb is None or ba is None: return {}
        pu=round(bb-back*TICK,2); pdn=round(1-ba-back*TICK,2); imb=pos[True]-pos[False]; o={}
        if pu>=0.03 and imb<30: o['up']=(pu,10.0)
        if pdn>=0.03 and -imb<30: o['dn']=(pdn,10.0)
        return o
    f=MakerSim(place_lat=lat,cancel_lat=lat,decide_every=50).run(ev,pol,st*1000,en*1000,max_pos=10**9)
    y=int(r.up_won)
    if len(f)==0: continue
    f['side_up']=f.side_up.astype(bool)
    for x in f.itertuples(index=False):
        # value of our side in Up-mid terms
        m0=mid_at(x.t); m1=mid_at(x.t+1000); m5_=mid_at(x.t+5000); m30=mid_at(x.t+30000)
        sgn=1 if x.side_up else -1
        val=lambda m: (m if x.side_up else 1-m)
        fills.append((cid,x.t,x.side_up,x.px,x.sz,val(m0),val(m1),val(m5_),val(m30),y if x.side_up else 1-y,(x.t/1000-st)))
    up=f[f.side_up]; dn=f[~f.side_up]
    mk.append((cid,up.sz.sum(),dn.sz.sum(),(up.px*up.sz).sum(),(dn.px*dn.sz).sum(),y))
F=pd.DataFrame(fills,columns=['cid','t','up','px','sz','v0','v1','v5','v30','won','el'])
K=pd.DataFrame(mk,columns=['cid','up_sh','dn_sh','up_cost','dn_cost','y'])
w=F.sz
print(f'back{back} @{lat}ms: markets={F.cid.nunique()} fills={len(F)} shares={w.sum():.0f}')
for h in ['v0','v1','v5','v30','won']:
    d=(F[h]-F.px)
    print(f'  markout vs fill price at {h:>4}: {np.average(d.fillna(0),weights=w)*100:+.2f}c/share')
F['band']=pd.cut(F.px,[0,.1,.3,.5,.7,.9,.97,1])
print(F.groupby('band',observed=True).apply(lambda g: pd.Series(dict(sh=g.sz.sum(),pnl_c=np.average(g.won-g.px,weights=g.sz)*100,mk5_c=np.average((g.v5-g.px).fillna(0),weights=g.sz)*100))).round(2).to_string())
# paired vs unpaired decomposition per market
K['pairs']=np.minimum(K.up_sh,K.dn_sh)
K['avg_up']=K.up_cost/K.up_sh.replace(0,np.nan); K['avg_dn']=K.dn_cost/K.dn_sh.replace(0,np.nan)
K['pair_pnl']=K.pairs*(1-K.avg_up.fillna(0)-K.avg_dn.fillna(0))
K['un_up']=K.up_sh-K.pairs; K['un_dn']=K.dn_sh-K.pairs
K['unp_pnl']=K.un_up*(K.y-K.avg_up.fillna(0))+K.un_dn*((1-K.y)-K.avg_dn.fillna(0))
print(f'  paired shares/mkt {K.pairs.mean():.0f}  avg pair cost {((K.up_cost+K.dn_cost)/(K.up_sh+K.dn_sh)*2).mean():.4f}')
print(f'  PnL from locked pairs: ${K.pair_pnl.sum():.1f}   PnL from unpaired inventory: ${K.unp_pnl.sum():.1f}   total ${K.pair_pnl.sum()+K.unp_pnl.sum():.1f}')
print(f'  unpaired PnL sd per market ${K.unp_pnl.std():.2f}; pair PnL sd per market ${K.pair_pnl.std():.2f}')
F['eb']=pd.cut(F.el,[0,30,60,120,180,240,300])
print(F.groupby('eb',observed=True).apply(lambda g: pd.Series(dict(sh=g.sz.sum(),pnl_c=np.average(g.won-g.px,weights=g.sz)*100))).round(2).T.to_string())

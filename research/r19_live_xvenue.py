"""Live synchronized cross-venue check: Kalshi KXBTC15M book (REST poll) vs Polymarket 15m book (WS)."""
import sys, glob, gzip, json; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.livebook import load_rec, load_deltas, build_book_grid
from pm.fees import taker_fee_per_share
rows=[]
for f in sorted(glob.glob('data/live/kalshi_*.jsonl.gz')):
    try:
        for line in gzip.open(f,'rt'):
            try: d=json.loads(line)
            except Exception: break
            ob=d['ob'] or {}
            yb=ob.get('yes_dollars') or []; nb=ob.get('no_dollars') or []
            if not yb or not nb: continue
            by=max(yb,key=lambda x:float(x[0])); bn=max(nb,key=lambda x:float(x[0]))
            rows.append((d['r0'],d['r1'],d['open'],float(by[0]),float(by[1]),float(bn[0]),float(bn[1])))
    except (EOFError,OSError): pass
K=pd.DataFrame(rows,columns=['r0','r1','open','ybid','ybid_sz','nbid','nbid_sz'])
K['yask']=1-K.nbid; K['nask']=1-K.ybid; K['yask_sz']=K.nbid_sz; K['nask_sz']=K.ybid_sz
K['tq']=(K.r0+K.r1)//2      # best guess of snapshot time (midpoint of request)
print('kalshi polls',len(K),'windows',K.open.nunique())
R=load_rec(); D=load_deltas()
out=[]
for o,g in K.groupby('open'):
    slug=f'btc-updown-15m-{o}'
    B=build_book_grid(slug,D,R['books'],step=100)
    if B is None or len(B)==0: print('no poly book',slug); continue
    B=B.dropna(subset=['a1','b1'])
    j=np.searchsorted(B.t.values,g.tq.values,side='right')-1
    ok=j>=0; g=g[ok]; j=j[ok]
    pu_ask=B.a1.values[j]; pu_sz=B.as1.values[j]; pd_ask=1-B.b1.values[j]; pd_sz=B.bs1.values[j]
    c1=g.nask.values+pu_ask; f1=taker_fee_per_share(g.nask.values)+taker_fee_per_share(pu_ask)
    c2=g.yask.values+pd_ask; f2=taker_fee_per_share(g.yask.values)+taker_fee_per_share(pd_ask)
    for i in range(len(g)):
        out.append((slug,g.tq.values[i]/1000-o,g.yask.values[i],g.ybid.values[i],pu_ask[i],1-pd_ask[i],c1[i],f1[i],min(g.nask_sz.values[i],pu_sz[i]),c2[i],f2[i],min(g.yask_sz.values[i],pd_sz[i])))
X=pd.DataFrame(out,columns=['slug','el','k_yask','k_ybid','p_uask','p_ubid','c1','f1','sz1','c2','f2','sz2'])
X['net1']=1-X.c1-X.f1; X['net2']=1-X.c2-X.f2
X['kmid']=(X.k_yask+X.k_ybid)/2; X['pmid']=(X.p_uask+X.p_ubid)/2
print('samples',len(X))
print('mid gap (kalshi-poly) quantiles',np.percentile(X.kmid-X.pmid,[1,5,25,50,75,95,99]).round(3))
for th in [0,0.01,0.02]:
    print(f'net>{th}: pair1 (K_no+P_up) frac={(X.net1>th).mean():.4f}  pair2 (K_yes+P_dn) frac={(X.net2>th).mean():.4f}')
print('max net1',X.net1.max().round(4),'max net2',X.net2.max().round(4))
print(X.sort_values('net1',ascending=False).head(8).round(3).to_string())
X.to_parquet('data/live_xvenue.parquet')

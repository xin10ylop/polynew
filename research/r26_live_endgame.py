"""Endgame on live books: in the final 20s, compare Chainlink-TWAP-based P(up) with executable asks."""
import sys; sys.path.insert(0,'.')
import requests, numpy as np, pandas as pd
from pm.livebook import load_rec, load_deltas, build_book_grid
from pm.livefair import LiveFair
from pm.fees import taker_fee_per_share as fee
R=load_rec(); D=load_deltas(); LF=LiveFair(R['cl'],R['cb'])
cl_t0=R['cl'].ts.min()//1000; cl_t1=R['cl'].ts.max()//1000
slugs=sorted(set(D.slug)); res={}
for i in range(0,len(slugs),20):
    for e in requests.get('https://gamma-api.polymarket.com/events',params=[('slug',s) for s in slugs[i:i+20]]).json():
        meta=e.get('eventMetadata') or {}
        if 'finalPrice' in meta and 'priceToBeat' in meta: res[e['slug']]=(1 if meta['finalPrice']>=meta['priceToBeat'] else 0, meta['priceToBeat'], meta['finalPrice'])
rows=[]
for s in slugs:
    st=int(s.rsplit('-',1)[1]); en=st+(300 if '-5m-' in s else 900)
    if s not in res or st-70<cl_t0 or en+5>cl_t1: continue
    y,K,F=res[s]
    B=build_book_grid(s,D,R['books'],step=100)
    if B is None: continue
    B=B[(B.t>=(en-25)*1000)&(B.t<=(en+3)*1000)]
    for r in B.iloc[::5].itertuples(index=False):   # every 500ms
        q=LF.fair(r.t,st,en,K=K)
        rows.append((s,(en*1000-r.t)/1000,q,r.a1 if r.a1==r.a1 else 1.0,r.as1,1-r.b1 if r.b1==r.b1 else 1.0,r.bs1,y,np.log(F/K)*1e4))
X=pd.DataFrame(rows,columns=['slug','tau','q','up_ask','up_ask_sz','dn_ask','dn_ask_sz','y','move_bp'])
print('markets',X.slug.nunique(),'samples',len(X))
for side,ask,sz,won,qq in [('Up','up_ask','up_ask_sz','y','q')]:
    pass
X['q_up']=X.q; X['edge_up']=X.q-X.up_ask-fee(X.up_ask); X['edge_dn']=(1-X.q)-X.dn_ask-fee(X.dn_ask)
for th in [0.005,0.01,0.02]:
    a=X[X.edge_up>th]; b=X[X.edge_dn>th]
    pa=(a.y-a.up_ask-fee(a.up_ask)); pb=((1-b.y)-b.dn_ask-fee(b.dn_ask))
    print(f'edge>{th}: buyUp n={len(a)} mk={a.slug.nunique()} realized={pa.mean() if len(a) else 0:+.4f} | buyDn n={len(b)} mk={b.slug.nunique()} realized={pb.mean() if len(b) else 0:+.4f}  avg ask sz {a.up_ask_sz.mean() if len(a) else 0:.0f}/{b.dn_ask_sz.mean() if len(b) else 0:.0f}')
print(X[(X.edge_up>0.01)|(X.edge_dn>0.01)].sort_values('tau').head(30).round(4).to_string())

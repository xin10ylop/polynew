"""Kalshi KXBTC15M vs Polymarket 15m on the same windows: price gaps, lead-lag, arb frequency.
Polymarket api timestamps are shifted by -2.2s to approximate match time (measured earlier)."""
import sys, glob; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.fees import taker_fee_per_share
k=pd.read_parquet('data/kalshi_15m.parquet')
pm15=pd.read_parquet('data/markets_15m.parquet').dropna(subset=['up_won'])
tr=pd.read_parquet('data/tr_eval_15m.parquet',columns=['condition_id','ts','up_price','side_up','ask','start_ts'])
files=glob.glob('data/kalshi_trades/*.parquet')
rows=[]; lag_rows=[]
for f in files:
    K=pd.read_parquet(f); tk=K.ticker.iloc[0]
    if K.t.max()<1e8: K['t']=K.t*1000.0
    kk=k[k.ticker==tk]
    if kk.empty: continue
    o=int(kk.open_ts.iloc[0]); pmr=pm15[pm15.start_ts==o]
    if pmr.empty: continue
    cid=pmr.condition_id.iloc[0]; y=int(pmr.up_won.iloc[0]); ky=1 if kk.result.iloc[0]=='yes' else 0
    P=tr[tr.condition_id==cid].copy(); P['tm']=P.ts-2.2
    K=K.sort_values('t'); P=P.sort_values('tm')
    if len(P)<10 or len(K)<10: continue
    grid=np.arange(o+5,o+895)
    # last trade prices known at each second
    ik=np.searchsorted(K.t.values,grid,side='right')-1; ip=np.searchsorted(P.tm.values,grid,side='right')-1
    ok=(ik>=0)&(ip>=0)
    ik=np.clip(ik,0,None); ip=np.clip(ip,0,None)
    kage=grid-K.t.values[ik]; page=grid-P.tm.values[ip]
    kp=K.yes.values[ik]; pp=P.up_price.values[ip]
    fresh=ok&(kage<3)&(page<3)
    for g,a,b in zip(grid[fresh],kp[fresh],pp[fresh]):
        rows.append((tk,o,g-o,a,b,y,ky))
    # executable evidence: Kalshi taker-yes prints = yes ask; taker-no prints => yes bid (no ask = 1-yes)
    # Poly: side_up==1 prints = Up ask; side_up==0 prints = Down ask
    Kyes_ask=K[K.taker_yes==1]; Kno_ask=K[K.taker_yes==0]
    Pup=P[P.side_up==1]; Pdn=P[P.side_up==0]
    for (A,B,colA,colB,name) in [(Kno_ask,Pup,'yes','ask','K_no+P_up'),(Kyes_ask,Pdn,'yes','ask','K_yes+P_dn')]:
        # within the same second bucket, cheapest combination
        a=A.assign(sec=np.floor(A.t).astype(int)).groupby('sec')[colA].agg('max' if name=='K_no+P_up' else 'min')
        b=B.assign(sec=np.floor(B.tm).astype(int)).groupby('sec')[colB].min()
        j=pd.concat([a,b],axis=1,keys=['ka','pb']).dropna()
        if name=='K_no+P_up':
            kno=1-j.ka   # no price paid = 1 - yes print (taker bought no at 1-yes)
            cost=kno+j.pb; fee=taker_fee_per_share(kno)+taker_fee_per_share(j.pb)
        else:
            cost=j.ka+j.pb; fee=taker_fee_per_share(j.ka)+taker_fee_per_share(j.pb)
        for s_,c_,f_ in zip(j.index,cost,fee):
            lag_rows.append((tk,o,name,s_-o,c_,f_,y,ky))
X=pd.DataFrame(rows,columns=['tk','o','el','k','p','y','ky'])
A=pd.DataFrame(lag_rows,columns=['tk','o','pair','el','cost','fee','y','ky'])
print('windows',X.tk.nunique(),'rows',len(X))
X['gap']=X.k-X.p
print('gap (kalshi yes - poly up) quantiles:',np.percentile(X.gap,[1,5,25,50,75,95,99]).round(3))
print('|gap|>0.05 frac',(X.gap.abs()>0.05).mean().round(4),' >0.10',(X.gap.abs()>0.10).mean().round(4))
# lead-lag: correlation of 1s changes with shifts
Xs=X.sort_values(['tk','el'])
res={}
for L in range(-6,7):
    cs=[]
    for tk,g in Xs.groupby('tk'):
        dk=g.k.diff().values; dp=g.p.diff().values
        if L>=0: a,b=dk[1:len(dk)-L] if L>0 else dk[1:], dp[1+L:]
        else: a,b=dk[1-L:], dp[1:len(dp)+L]
        n=min(len(a),len(b)); cs.append(np.corrcoef(np.nan_to_num(a[:n]),np.nan_to_num(b[:n]))[0,1])
    res[L]=np.nanmean(cs)
print('corr(dKalshi_t, dPoly_t+L):',{k:round(v,3) for k,v in res.items()})
A['net']=1-A.cost-A.fee
print('\nSame-second executable pair evidence:')
for name,g in A.groupby('pair'):
    print(name,'n sec',len(g),' net>0 frac',(g.net>0).mean().round(4),' net>0.01',(g.net>0.01).mean().round(4),' net>0.03',(g.net>0.03).mean().round(4),' best net',g.net.max().round(3))
pos=A[A.net>0.01]
print('arb-seconds windows',pos.tk.nunique(),' el dist',np.percentile(pos.el,[10,50,90]) if len(pos) else '')
A.to_parquet('data/kalshi_pairs.parquet'); X.to_parquet('data/kalshi_gap.parquet')

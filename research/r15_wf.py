"""Multi-fold walk-forward for the ML residual model + strict execution, with feature ablations."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd, lightgbm as lgb
from pm.fees import taker_fee_per_share
dur=sys.argv[1]; variant=sys.argv[2]
F=pd.read_parquet(f'data/feat_{dur}.parquet')
F=F[F.mp.notna()&(F.mp_age<=30)].copy()
def logit(p): p=np.clip(p,0.005,0.995); return np.log(p/(1-p))
F['lmp']=logit(F.mp); F['lq_sp']=logit(F.q_sp); F['lq_fu']=logit(F.q_fu)
feats=[c for c in F.columns if c not in ('cid','s','t','y')]
if variant=='nocal': feats=[f for f in feats if f not in ('hour','dow')]
if variant=='nocal_novol': feats=[f for f in feats if f not in ('hour','dow') and not f.startswith('sig_') and not f.startswith('vol')]
params=dict(objective='binary',learning_rate=0.03,num_leaves=31,min_data_in_leaf=500,feature_fraction=0.7,bagging_fraction=0.7,bagging_freq=1,lambda_l2=10,verbose=-1)
tr_=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['condition_id','ts','side_up','ask']).sort_values(['condition_id','ts'])
G={c:(g.ts.values,g.side_up.values.astype(bool),g.ask.values) for c,g in tr_.groupby('condition_id')}
t_end=F.s.max()+1; fold=7*86400
folds=[(t_end-k*fold, t_end-(k-1)*fold) for k in (4,3,2,1)]
allO=[]
for a,b in folds:
    tr=F[F.s<a-3600]; te=F[(F.s>=a)&(F.s<b)].copy()
    bst=lgb.train(params,lgb.Dataset(tr[feats],tr.y,init_score=tr.lmp),num_boost_round=300)
    te['p']=1/(1+np.exp(-(te.lmp+bst.predict(te[feats],raw_score=True))))
    def ll(p,y): p=np.clip(p,1e-4,1-1e-4); return -(y*np.log(p)+(1-y)*np.log(1-p)).mean()
    out=[]
    for c,g in te.groupby('cid'):
        ts,su,ask=G[c]; used=0
        for r in g.itertuples(index=False):
            if used>=3: break
            for side in (True,False):
                q=r.p if side else 1-r.p
                ii=np.flatnonzero(su==side)
                j=np.searchsorted(ts[ii],r.t,side='right')-1
                if j<0 or r.t-ts[ii][j]>15: continue
                ref=ask[ii][j]
                if not (0.03<=ref<=0.97) or q-ref-taker_fee_per_share(ref)<=0.04: continue
                lo=np.searchsorted(ts[ii],r.t+4,side='left'); hi=np.searchsorted(ts[ii],r.t+5,side='right')
                cand=ask[ii][lo:hi]; ok=q-cand-taker_fee_per_share(cand)>0.02
                if not ok.any(): continue
                p=cand[np.argmax(ok)]; won=r.y if side else 1-r.y
                out.append((c,r.s,r.t,r.tau,side,p,q,won,won-p-taker_fee_per_share(p))); used+=1; break
    O=pd.DataFrame(out,columns=['cid','s','t','tau','side_up','px','q','won','pnl']); O['fold']=a
    allO.append(O)
    btc_ret=te.groupby('cid').y.first().mean()
    print(f'{dur} {variant} fold {pd.Timestamp(a,unit="s").date()}: LLmkt={ll(te.mp,te.y):.4f} LLml={ll(te.p,te.y):.4f} up_rate={btc_ret:.3f} | fills={len(O)} pnl/sh={O.pnl.mean():+.4f} se={O.pnl.std()/np.sqrt(max(len(O),1)):.4f} upfrac={O.side_up.mean():.2f} pnl_up={O[O.side_up].pnl.mean():+.4f} pnl_dn={O[~O.side_up].pnl.mean():+.4f}',flush=True)
A=pd.concat(allO); A.to_parquet(f'data/wf_{dur}_{variant}.parquet')
print(f'{dur} {variant} ALL: n={len(A)} pnl/sh={A.pnl.mean():+.4f} se={A.pnl.std()/np.sqrt(len(A)):.4f}')

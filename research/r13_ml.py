"""Walk-forward LightGBM fair value vs market price. Train on earlier data, test on the final 10 days."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd, lightgbm as lgb
dur=sys.argv[1]
F=pd.read_parquet(f'data/feat_{dur}.parquet')
F=F[F.mp.notna()&(F.mp_age<=30)].copy()
def logit(p): p=np.clip(p,0.005,0.995); return np.log(p/(1-p))
F['lmp']=logit(F.mp); F['lq_sp']=logit(F.q_sp); F['lq_fu']=logit(F.q_fu)
feats=[c for c in F.columns if c not in ('cid','s','t','y')]
cut=F.s.max()-10*86400
tr=F[F.s<cut]; te=F[F.s>=cut]
print('train',len(tr),'test',len(te),'test markets',te.cid.nunique())
def ll(p,y): p=np.clip(p,1e-4,1-1e-4); return -(y*np.log(p)+(1-y)*np.log(1-p)).mean()
params=dict(objective='binary',learning_rate=0.03,num_leaves=31,min_data_in_leaf=500,feature_fraction=0.7,bagging_fraction=0.7,bagging_freq=1,lambda_l2=10,verbose=-1)
# (a) full model; (b) residual model on top of market logit (init_score)
res={}
for name,use in [('full',feats),('nomarket',[f for f in feats if f not in ('mp','lmp','mp_age','mp_chg30','n30','pflow30')])]:
    dtr=lgb.Dataset(tr[use],tr.y); 
    b=lgb.train(params,dtr,num_boost_round=600)
    res[name]=b.predict(te[use])
dtr=lgb.Dataset(tr[feats],tr.y,init_score=tr.lmp); 
b=lgb.train(params,dtr,num_boost_round=300)
res['resid']=1/(1+np.exp(-(te.lmp+b.predict(te[feats],raw_score=True))))
imp=pd.Series(b.feature_importance('gain'),index=feats).sort_values(ascending=False)
print('residual-model top features:',imp.head(12).round(0).to_dict())
y=te.y.values
te=te.assign(**{f'p_{k}':v for k,v in res.items()})
bins=[0,20,60,120,180,240,300,450,600,900]
te['tb']=pd.cut(te.tau,bins)
rows=[]
for bb,g in te.groupby('tb',observed=True):
    rows.append(dict(tau=str(bb),n=len(g),mkt=ll(g.mp,g.y),q_sp=ll(g.q_sp,g.y),q_fu=ll(g.q_fu,g.y),nomkt=ll(g.p_nomarket,g.y),full=ll(g.p_full,g.y),resid=ll(g.p_resid,g.y)))
print(pd.DataFrame(rows).round(4).to_string())
print('ALL', {k:round(ll(te[c],y),4) for k,c in [('mkt','mp'),('q_sp','q_sp'),('nomkt','p_nomarket'),('full','p_full'),('resid','p_resid')]})
te[['cid','s','t','tau','y','mp','q_sp','p_full','p_resid','p_nomarket']].to_parquet(f'data/ml_test_{dur}.parquet')

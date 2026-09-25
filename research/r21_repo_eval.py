"""Evaluate replicated repo bots (from r17 Jev answers) with dry-run fills vs strict fills."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.fees import taker_fee_per_share as fee
D=pd.read_parquet('data/repo_jev_5m.parquet').sort_values(['cid','t'])
tr=pd.read_parquet('data/tr_eval_5m.parquet',columns=['condition_id','ts','side_up','ask'])
tr=tr[tr.condition_id.isin(D.cid.unique())].sort_values(['condition_id','ts'])
G={c:(g.ts.values,g.side_up.values.astype(bool),g.ask.values) for c,g in tr.groupby('condition_id')}
def strict_fill(c,t,side_up,limit,Dl=4):
    ts,su,ask=G[c]; ii=np.flatnonzero(su==side_up)
    lo=np.searchsorted(ts[ii],t+Dl,side='left'); hi=np.searchsorted(ts[ii],t+Dl+1,side='right')
    cand=ask[ii][lo:hi]; ok=cand<=limit+1e-9
    return cand[np.argmax(ok)] if ok.any() else None
def evaluate(name, decide, limit_fn=lambda ref,q: ref):
    dry=[]; strict=[]
    for c,g in D.groupby('cid'):
        for r in g.itertuples(index=False):
            d=decide(r)
            if d is None: continue
            side_up,q=d; ref=r.askU if side_up else r.askD
            won=r.y if side_up else 1-r.y
            dry.append(won-ref-fee(ref))
            p=strict_fill(c,r.t,side_up,limit_fn(ref,q))
            if p is not None: strict.append(won-p-fee(p))
            break   # one entry per window (all bots here)
    dry=np.array(dry); strict=np.array(strict)
    se=lambda x: x.std()/np.sqrt(max(len(x),1))
    print(f'{name:38s} entries={len(dry):4d} dry-run pnl/sh={dry.mean() if len(dry) else 0:+.4f}±{se(dry) if len(dry) else 0:.4f} | strict fills={len(strict):4d} ({len(strict)/max(len(dry),1):.0%}) pnl/sh={strict.mean() if len(strict) else 0:+.4f}±{se(strict) if len(strict) else 0:.4f}')
def jevagent(r):  # exact jevAgentDev policy
    if r.tau<90 or r.A_conf is None or not (r.A_conf>0.90): return None
    side_up=r.A_side=='UP'; ask=r.askU if side_up else r.askD
    if ask is None or ask!=ask or ask>0.70: return None
    pw=r.A_pu if side_up else 1-r.A_pu
    return (side_up,pw) if pw>=ask+0.10 else None
for thr in [0.9,0.8,0.7,0.6]:
    def pol(r,thr=thr):
        if r.tau<90 or not (r.A_conf>thr): return None
        side_up=r.A_side=='UP'; ask=r.askU if side_up else r.askD
        if ask is None or ask!=ask or ask>0.70: return None
        pw=r.A_pu if side_up else 1-r.A_pu
        return (side_up,pw) if pw>=ask+0.10 else None
    evaluate(f'jevAgentDev rule conf>{thr}',pol)
for th in [0.03,0.06,0.10]:
    def flow(r,th=th):
        for side_up in (True,False):
            ask=r.askU if side_up else r.askD
            if ask is None or ask!=ask or not (0.05<ask<0.95): continue
            q=r.B_pu if side_up else 1-r.B_pu
            if q-ask-fee(ask)>th: return (side_up,q)
        return None
    evaluate(f'jev-trader flow state, edge>{th}',flow)
def vote(r):
    up=sum([r.v_dir=='up',r.v_regime=='trend_up',r.v_toxic=='buyers',r.v_entry=='up',r.v_press=='up'])
    dn=sum([r.v_dir=='down',r.v_regime=='trend_down',r.v_toxic=='sellers',r.v_entry=='down',r.v_press=='down'])
    if r.v_dir=='flat': return None
    for side_up,n in ((True,up),(False,dn)):
        ask=r.askU if side_up else r.askD
        if n>=3 and ask is not None and ask==ask and ask<=0.8: return (side_up,None)
    return None
evaluate('5-output vote (>=3 agree, ask<=0.8)',vote)
D['A_p_up']=D.A_pu; y=D.y.values
def ll(p): p=np.clip(p,0.01,0.99); return -(y*np.log(p)+(1-y)*np.log(1-p)).mean()
mp=np.where(D.askU.notna()&D.askD.notna(),(D.askU+1-D.askD)/2,np.nan)
ok=~np.isnan(mp)
print('calibration LL on all decision points: market mid', round(-(y[ok]*np.log(np.clip(mp[ok],.01,.99))+(1-y[ok])*np.log(1-np.clip(mp[ok],.01,.99))).mean(),4), ' Jev A', round(ll(D.A_pu.values),4), ' Jev B(flow)', round(ll(D.B_pu.values),4))

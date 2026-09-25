"""Maker-side economics from the taker tape: every taker fill has a maker counterparty.
maker pnl/share = ask - side_won (maker sold the side the taker bought at `ask`) + rebate (20% of taker fee)."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.fees import taker_fee_per_share
dur=sys.argv[1]
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['condition_id','ts','tau','ask','side_won','size','q_side','wallet','start_ts','end_ts'])
tr['fee']=taker_fee_per_share(tr.ask)
tr['mk_pnl']=tr.ask-tr.side_won
tr['mk_pnl_r']=tr.mk_pnl+0.2*tr.fee
tr['tk_pnl']=-tr.mk_pnl-tr.fee
w=tr['size']
print(f'{dur}: taker trades {len(tr)}, shares {w.sum():.0f}, notional {(tr.ask*w).sum():.0f}')
print(f' maker pnl/share {np.average(tr.mk_pnl,weights=w):+.4f}  with rebate {np.average(tr.mk_pnl_r,weights=w):+.4f}  taker pnl/share after fee {np.average(tr.tk_pnl,weights=w):+.4f}')
D=int((tr.end_ts-tr.start_ts).iloc[0])
tr['tb']=pd.cut(tr.tau,[0,10,30,60,90,120,180,240,300,450,600,750,900][:(13 if D==900 else 9)])
tr['pb']=pd.cut(tr.ask,[0,.05,.1,.2,.3,.4,.5,.6,.7,.8,.9,.95,1])
def agg(g):
    ww=g['size']
    return pd.Series(dict(sh=ww.sum(), mk=np.average(g.mk_pnl,weights=ww), mk_r=np.average(g.mk_pnl_r,weights=ww), tk=np.average(g.tk_pnl,weights=ww)))
print(tr.groupby('tb',observed=True).apply(agg).round(4).to_string())
print(tr.groupby('pb',observed=True).apply(agg).round(4).to_string())
# trade size buckets: are big takers informed?
tr['sb']=pd.cut(tr['size'],[0,5,20,100,500,1e9])
print(tr.groupby('sb',observed=True).apply(agg).round(4).to_string())

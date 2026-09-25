"""Model-filtered passive quoting evaluated on the tape.
Each taker trade (true match time ~ api_ts - 2s) has a maker who sold `side` at `ask`, i.e. bought the
complement at 1-ask. If WE had been that maker with a quote set from info up to t_match - stale,
our PnL = comp_won - (1-ask) + rebate. We only 'quote' when the model says our buy is >= theta cheap."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.model import Series1s, fair_up
from pm.fees import taker_fee_per_share
dur=sys.argv[1]
S=Series1s('data/btcusdt_1s.parquet')
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['condition_id','ts','side_up','ask','side_won','size','start_ts','end_ts','price_to_beat','tau'])
tr['tm']=tr.ts-2
tr=tr[(tr.tm>tr.start_ts+1)&(tr.tm<tr.end_ts-1)]
comp_up=1-tr.side_up.values          # our side (complement of taker's side) is Up?
buy_px=1-tr.ask.values               # our purchase price
won=1-tr.side_won.values
reb=0.2*taker_fee_per_share(tr.ask.values)
D=int((tr.end_ts-tr.start_ts).iloc[0])
bins=[0,10,30,60,90,120,180,240,300,450,600,750,900][:(13 if D==900 else 9)]
tb=pd.cut(tr.tau.values+2,bins)
for stale in [1,2,4]:
    t=tr.tm.values-stale+1   # model uses data up to t-1 = tm-stale
    q,_=fair_up(S,tr.start_ts.values,tr.end_ts.values,tr.price_to_beat.values,t,S.sigma_at(t,900),0.54,df_t=5)
    qo=np.where(comp_up==1,q,1-q)
    edge=qo-buy_px
    pnl=won-buy_px+reb
    for th in [0.0,0.02,0.05]:
        sel=(edge>=th)&(buy_px>0.03)&(buy_px<0.97)
        w=tr['size'].values[sel]
        print(f'{dur} stale={stale}s theta={th}: fills={sel.sum()} shares={w.sum():.0f} pnl/sh={np.average(pnl[sel],weights=w):+.4f} (unweighted {pnl[sel].mean():+.4f})  all-maker={np.average(pnl,weights=tr["size"].values):+.4f}')
    sel=(edge>=0.02)&(buy_px>0.03)&(buy_px<0.97)
    d=pd.DataFrame({'tb':tb[sel],'pnl':pnl[sel],'w':tr['size'].values[sel]})
    print(d.groupby('tb',observed=True).apply(lambda g: pd.Series(dict(n=len(g),pnl=np.average(g.pnl,weights=g.w)))).round(4).T.to_string())

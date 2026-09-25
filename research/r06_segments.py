"""Strict backtest restricted to window segments (start / middle / final minute)."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from pm.model import Series1s, fair_up
from pm.data import load_markets
from pm.backtest import market_arrays, run, summarize
dur=sys.argv[1]; src=sys.argv[2]
S=Series1s(f'data/{src}.parquet')
m=load_markets(dur); m=m[(m.start_ts-4000>S.t0)&(m.end_ts+5<S.t1)]
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['condition_id','ts','side_up','ask'])
arrays=market_arrays(tr)
bsd=0.54 if src=='btcusdt_1s' else 0.7
def qfun(row,grid):
    q,_=fair_up(S,np.full(len(grid),row.start_ts),np.full(len(grid),row.end_ts),np.full(len(grid),row.price_to_beat),grid,S.sigma_at(grid,900),bsd,df_t=5)
    return q
D=int(m.end_ts.iloc[0]-m.start_ts.iloc[0])
segs={'first60':(1,60),'first150':(1,150),'mid':(150,D-90),'last90':(D-90,D-3),'last60':(D-60,D-3)}
for name,(a,b) in segs.items():
    for delay in [3,4]:
        for theta in [0.05,0.1]:
            f=run(m,arrays,qfun,delay=delay,win=1,theta=theta,theta_exec=theta/2,t_from=a,t_to=b,max_fills=2,cooldown=5)
            s=summarize(f)
            if isinstance(s,str): print(dur,src,name,delay,theta,s); continue
            print(f'{dur} {src} {name:>8} delay={delay} th={theta}: n={s.n:.0f} px={s.px:.3f} win={s.win:.3f} pnl/sh={s.pnl:+.4f} se={s.se:.4f} t={s.pnl/s.se:+.1f}', flush=True)

import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd, time
from pm.model import Series1s, fair_up
from pm.data import load_markets
from pm.backtest import market_arrays, run, summarize
dur=sys.argv[1]; src=sys.argv[2] if len(sys.argv)>2 else 'btcusdt_1s'
S=Series1s(f'data/{src}.parquet')
m=load_markets(dur); m=m[(m.start_ts-4000>S.t0)&(m.end_ts+5<S.t1)]
tr=pd.read_parquet(f'data/tr_eval_{dur}.parquet',columns=['condition_id','ts','side_up','ask'])
arrays=market_arrays(tr)
def qfun(row,grid):
    q,_=fair_up(S,np.full(len(grid),row.start_ts),np.full(len(grid),row.end_ts),np.full(len(grid),row.price_to_beat),grid,S.sigma_at(grid,900),0.54,df_t=5)
    return q
D=int(m.end_ts.iloc[0]-m.start_ts.iloc[0])
for delay in [int(x) for x in (sys.argv[3] if len(sys.argv)>3 else "4,5").split(",")]:
    for theta in [0.03,0.06,0.10]:
        t0=time.time()
        f=run(m,arrays,qfun,delay=delay,win=1,theta=theta,theta_exec=theta/2)
        f['tb']=pd.cut(f.tau,[0,10,30,60,90,120,180,240,300,450,600,750,900])
        s=summarize(f)
        print(f'--- {dur} src={src} delay={delay} theta={theta}: n={s.n:.0f} mk={s.mk:.0f} px={s.px:.3f} win={s.win:.3f} pnl/sh={s.pnl:+.4f} se={s.se:.4f} tot={s.tot:.1f} ({time.time()-t0:.0f}s)')
        print(summarize(f,'tb')[['n','px','win','pnl','se']].round(4).to_string())
        f.drop(columns=['tb']).to_parquet(f'data/bt_{dur}_{src}_d{delay}_th{theta}.parquet')

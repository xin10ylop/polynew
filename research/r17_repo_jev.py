"""Faithful replication of Jev bots from the user's videos, on historical 5m windows, with strict fills.

A) jevAgentDev/jev-polymarket-trading: FactsForJev state + DIRECTION_Q choice; ENTER once if conf>0.90,
   ask<=0.70, P(win)>=ask+0.10, >=90s left; FOK at best ask; hold to resolution.
B) jarrodwatts/jev-trader adapted: returnsBps 1/5/20/100, recentMids, taker CVD (Binance) -> choice up/down.
C) 5-output vote (learnwithmeai style): direction/regime/toxic/entry/pressure, trade if >=3 agree.
All three answered in ONE Jev call per decision point (fan-out)."""
import sys; sys.path.insert(0,'.')
import asyncio, json, time
import numpy as np, pandas as pd
from pm.model import Series1s, fair_up
from pm.data import load_markets
from pm.jev import Jev
from pm.fees import taker_fee_per_share
NM=int(sys.argv[1]) if len(sys.argv)>1 else 300
S=Series1s('data/btcusdt_1s.parquet')
m=load_markets('5m'); m=m[(m.start_ts-90000>S.t0)&(m.end_ts+5<S.t1)]
m=m[m.start_ts>=m.start_ts.max()-14*86400].sample(NM,random_state=7).sort_values('start_ts').reset_index(drop=True)
tr=pd.read_parquet('data/tr_eval_5m.parquet',columns=['condition_id','ts','side_up','ask'])
tr=tr[tr.condition_id.isin(m.condition_id)].sort_values(['condition_id','ts'])
G={c:(g.ts.values,g.side_up.values.astype(bool),g.ask.values) for c,g in tr.groupby('condition_id')}
DIRECTION_Q=" ".join(["This is a Polymarket BTC Up/Down 5-minute contract.",
 "Resolution is Chainlink BTC/USD TWAP vs Price to Beat (window open), not Binance spot and not share odds.",
 "UP wins if close TWAP >= open reference. DOWN otherwise. Winning shares pay $1, losers $0.",
 "Return calibrated P(UP) and P(DOWN) for THIS window using seconds remaining and btc.moveVsWindowOpenPct.",
 "If session.position.kind is open, judge whether THAT side still resolves winner.",
 "Market mids are trader opinions, not the oracle. Prefer BTC path vs open over 24h change.",
 "Treat UP and DOWN symmetrically."])
QA={"direction":{"type":"choice","instructions":DIRECTION_Q,"criteria":{"UP":"Bitcoin finishes UP vs the window open","DOWN":"Bitcoin finishes DOWN vs the window open"}}}
QB={"flow_dir":{"type":"choice","instructions":{"question":"Will this 5-minute BTC Up/Down market settle Up or Down?",
   "goal":"Settlement compares the 60-second average BTC price at window end with the 60-second average at window start (`strike`).",
   "inputs":"Taker flow is the strongest signal: `trades.cvdBtc` (taker buys minus taker sells), `trades.lastSide`. `returnsBps` and `recentMids` show the path. `vsStrikeBps` is the current price relative to the strike and `secondsRemaining` the time left."},
   "criteria":{"up":"Settles Up: end average at or above the strike","down":"Settles Down: end average below the strike"}}}
QC={"v_dir":{"type":"choice","instructions":"Direction of BTC over the rest of this window","criteria":{"up":"Rising","down":"Falling","flat":"No clear direction"}},
    "v_regime":{"type":"choice","instructions":"Market regime","criteria":{"trend_up":"Trending up","trend_down":"Trending down","range":"Range-bound / choppy"}},
    "v_toxic":{"type":"choice","instructions":"Is taker flow one-sided (toxic) and in which direction?","criteria":{"buyers":"Aggressive buyers dominate","sellers":"Aggressive sellers dominate","balanced":"Balanced flow"}},
    "v_entry":{"type":"choice","instructions":"Which side offers the better entry right now given price vs strike and time left?","criteria":{"up":"Buying Up is the better entry","down":"Buying Down is the better entry","none":"Neither"}},
    "v_press":{"type":"choice","instructions":"Where is short-term price pressure pointing?","criteria":{"up":"Upward pressure","down":"Downward pressure","none":"No pressure"}}}
QUESTIONS={**QA,**QB,**QC}
lc=np.log(S.c); cv=np.concatenate([[0],np.cumsum(S.v)]); cb=np.concatenate([[0],np.cumsum(S.tbv)])
def quotes(c,t):
    ts,su,ask=G.get(c,(np.array([]),np.array([],bool),np.array([])))
    q={}
    for side,name in ((True,'up'),(False,'down')):
        ii=np.flatnonzero(su==side); j=np.searchsorted(ts[ii],t,side='right')-1
        q[name]=float(ask[ii][j]) if j>=0 and t-ts[ii][j]<=20 else None
    return q
def facts(r,t):
    i=S.idx(t)-1; last=float(S.c[i]); wopen=float(S.c[S.idx(r.start_ts)-1])
    d24=S.c[i-86400:i+1]
    q=quotes(r.condition_id,t)
    def qs(a,other): 
        if a is None or other is None: return {"bestAsk":a,"bestBid":None,"mid":a}
        bid=round(1-other,3); return {"bestAsk":a,"bestBid":bid,"mid":round((a+bid)/2,3)}
    A={"market":{"slug":r.slug,"question":f"Bitcoin Up or Down 5m","endsAt":pd.Timestamp(r.end_ts,unit='s').isoformat(),"volume24hUsd":1.2e7,
        "up":qs(q['up'],q['down']),"down":qs(q['down'],q['up'])},
       "btc":{"last":round(last,2),"change24hPct":round((last/d24[0]-1)*100,3),"high24h":round(float(d24.max()),2),"low24h":round(float(d24.min()),2),
        "volume24hQuote":round(float(S.v[i-86400:i+1].sum()*last),0),"moveVsWindowOpenPct":round((last/wopen-1)*100,4),"windowOpen":round(wopen,2)},
       "session":{"secondsRemaining":int(r.end_ts-t),"windowLengthSec":300,"position":{"kind":"flat"}}}
    Kb=S.twap(np.array([r.start_ts]))[0]
    B={"market":"BTC Up/Down 5m","strike":round(Kb,2),"mid":round(last,2),"vsStrikeBps":round((last/Kb-1)*1e4,2),"secondsRemaining":int(r.end_ts-t),
       "returnsBps":{f"last{L}":round((lc[i]-lc[i-L])*1e4,2) for L in (1,5,20,100)},
       "recentMids":" ".join(f"{x:.1f}" for x in S.c[i-60:i+1:5]),
       "trades":{"buyBtc":round(float(cb[i+1]-cb[i-59]),3),"sellBtc":round(float((cv[i+1]-cv[i-59])-(cb[i+1]-cb[i-59])),3),
                 "cvdBtc":round(float(2*(cb[i+1]-cb[i-59])-(cv[i+1]-cv[i-59])),3),"lastSide":"buy" if S.tbv[i]>=S.v[i]-S.tbv[i] else "sell"}}
    return {"facts":A,"flow_state":B}, q
async def main():
    rows=[]
    async with Jev(concurrency=16) as j:
        async def run_market(r):
            for off in range(5,215,5):
                t=r.start_ts+off
                st,q=facts(r,t)
                try: a=await j.ask(st,QUESTIONS)
                except Exception as ex: rows.append(dict(cid=r.condition_id,t=t,err=str(ex)[:80])); continue
                d=a['direction']; f=a['flow_dir']
                votes={k:a[k]['choice'] for k in QC}
                rows.append(dict(cid=r.condition_id,t=t,tau=r.end_ts-t,y=r.up_won,askU=q['up'],askD=q['down'],
                    A_side=d['choice'],A_conf=d['confidence'],A_pu=d['probabilities'].get('UP'),
                    B_pu=f['probabilities'].get('up'),B_conf=f['confidence'],**votes))
        t0=time.time()
        await asyncio.gather(*[run_market(r) for r in m.itertuples(index=False)])
        print('calls',j.calls,'tokens',j.input_tokens,'cost$',round(j.input_tokens*0.042e-6,3),'p50 lat',round(float(np.median(j.lat)),3),'wall',round(time.time()-t0))
    D=pd.DataFrame(rows); D.to_parquet('data/repo_jev_5m.parquet'); print(len(D), D.get('err',pd.Series()).notna().sum())
asyncio.run(main())

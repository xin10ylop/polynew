"""Jev as a regime gate for the deep maker vs the causal rolling-PnL gate.
Per 5m window (grid results, back2@20ms), build a bucketed state from info known at window start:
BTC realized vol & trend (Binance spot 1s up to start), previous-window volumes, shadow maker PnL of previous windows.
Ask Jev: will passive two-sided market making be profitable in this window? Compare AUC / gated PnL."""
import sys, asyncio, json; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
from pm.model import Series1s
from pm.jev import Jev, bucket
G=pd.concat([pd.read_parquet(f) for f in ['data/grid_5m.parquet','data/grid_5m_sep02.parquet','data/grid_5m_sep06.parquet']],ignore_index=True).drop_duplicates(['cid','lat','strat'])
x=G[(G.lat==20)&(G.strat=='back2')].sort_values('st').copy()
x['tot']=x.pnl+x.reb; x['blk']=(x.st.diff()>3600).cumsum()
x['r12']=x.groupby('blk').tot.transform(lambda s: s.shift(1).rolling(12,min_periods=12).mean())
x['r3']=x.groupby('blk').tot.transform(lambda s: s.shift(1).rolling(3,min_periods=3).mean())
m5=pd.read_parquet('data/markets_5m.parquet').set_index('condition_id')
x['vol_prev']=x.groupby('blk').cid.transform(lambda s: pd.Series(m5.loc[s,'volume'].values,index=s.index).shift(1))
S=Series1s('data/btcusdt_1s.parquet'); lc=np.log(S.c)
i=S.idx(x.st.values)-1
x['rv5']=np.sqrt(S.ewma_var(120)[i])*1e4*np.sqrt(300)      # 5m-scaled short vol (bp)
x['rv60']=np.sqrt(S.ewma_var(1800)[i])*1e4*np.sqrt(300)
x['tr15']=(lc[i]-lc[i-900])*1e4; x['tr60']=(lc[i]-lc[i-3600])*1e4
x=x.dropna(subset=['r12','r3','vol_prev']).reset_index(drop=True)
x['good']=(x.tot>0).astype(int)
def state(r):
    return {"setting":"Passive two-sided quoting (resting bids 2 ticks behind the best bid on both Up and Down) in a 5-minute Bitcoin Up/Down prediction market. Profit comes from large market orders that sweep the book and then revert; losses come from sustained one-directional moves that keep filling one side.",
            "btc_volatility_now": bucket(r.rv5/max(r.rv60,1e-9),[0.7,1.2,1.8],["calmer than usual","normal","elevated","spiking"]),
            "btc_trend_last_15m": bucket(r.tr15,[-15,-5,5,15],["strong selloff","drifting down","flat","drifting up","strong rally"]),
            "btc_trend_last_hour": bucket(r.tr60,[-30,-10,10,30],["strong selloff","drifting down","flat","drifting up","strong rally"]),
            "market_activity_last_window": bucket(r.vol_prev,[30000,50000,80000],["quiet","normal","busy","very busy"]),
            "strategy_result_last_3_windows": bucket(r.r3,[-5,-1,1,5],["losing clearly","losing slightly","flat","winning slightly","winning clearly"]),
            "strategy_result_last_hour": bucket(r.r12,[-3,-0.5,0.5,3],["losing clearly","losing slightly","flat","winning slightly","winning clearly"])}
Q={"profitable":{"type":"noul","instructions":"Will this passive market-making strategy make money in the next 5-minute window?",
    "criteria":{"true":"Net profitable over the window","false":"Net loss over the window"}},
   "regime":{"type":"choice","instructions":"Which market regime is most likely over the next 5 minutes?",
    "criteria":{"mean_reverting":"Choppy, sweeps revert: good for passive quoting","trending":"Sustained one-way move: bad for passive quoting","volatile_shock":"Sudden large moves: dangerous for passive quoting"}}}
async def main():
    out=[None]*len(x)
    async with Jev(concurrency=12) as j:
        async def one(k):
            try:
                a=await j.ask(state(x.iloc[k]),Q)
                out[k]=(a['profitable']['noul'],a['regime']['probabilities'].get('mean_reverting',np.nan))
            except Exception as ex:
                out[k]=(np.nan,np.nan)
        await asyncio.gather(*[one(k) for k in range(len(x))])
        print('jev calls',j.calls,'cost$',round(j.input_tokens*0.042e-6,4))
    return out
res=asyncio.run(main())
x['jev_p']=[r[0] for r in res]; x['jev_mr']=[r[1] for r in res]
x.to_parquet('data/jev_gate.parquet')
v=x.dropna(subset=['jev_p'])
for c in ['r12','r3','jev_p','jev_mr','rv5','vol_prev']:
    print(f'AUC({c} -> window profitable) = {roc_auc_score(v.good,v[c]):.3f}')
print(f'ungated $/mkt {v.tot.mean():+.2f} (n={len(v)})')
for name,mask in [('rolling r12>0',v.r12>0),('jev_p>median',v.jev_p>v.jev_p.median()),('jev_mr>median',v.jev_mr>v.jev_mr.median()),
                  ('r12>0 & jev_p>median',(v.r12>0)&(v.jev_p>v.jev_p.median())),('r12>0 & jev_mr>median',(v.r12>0)&(v.jev_mr>v.jev_mr.median()))]:
    print(f'  gate {name:24s}: traded {mask.sum():4d} $/mkt {v[mask].tot.mean():+.2f}  skipped $/mkt {v[~mask].tot.mean():+.2f}')

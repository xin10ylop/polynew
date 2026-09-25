"""Compare market trade prices with the TWAP model, bucketed by time-to-expiry.

For every taker trade we know an executable price for one side. If we had bought that side at
that price (as a taker, paying fee), what is the realized PnL, split by model edge?"""
import sys
sys.path.insert(0, ".")
import numpy as np
import pandas as pd
from pm.data import load_markets, load_trades
from pm.model import Series1s, fair_up
from pm.fees import taker_fee_per_share

dur = sys.argv[1] if len(sys.argv) > 1 else "15m"
lat = int(sys.argv[2]) if len(sys.argv) > 2 else 1   # decision uses data up to ts-lat
S = Series1s()
m = load_markets(dur)
tr = load_trades(dur, m)
tr = tr[(tr.ts > tr.start_ts) & (tr.ts < tr.end_ts)]
tr = tr[(tr.start_ts - 4000 > S.t0) & (tr.end_ts + 5 < S.t1)]
t = tr.ts.values - (lat - 1)
sig = S.sigma_at(t, 900)
q_up, z = fair_up(S, tr.start_ts.values, tr.end_ts.values, tr.price_to_beat.values, t, sig, 0.54, df_t=5)
tr["q_up"] = q_up
tr["q_side"] = np.where(tr.side_up == 1, q_up, 1 - q_up)
tr["fee"] = taker_fee_per_share(tr.ask.values)
tr["edge"] = tr.q_side - tr.ask - tr.fee
tr["pnl"] = tr.side_won - tr.ask - tr.fee
tr.to_parquet(f"data/tr_eval_{dur}.parquet")

D = int((tr.end_ts - tr.start_ts).iloc[0])
bins = [0, 10, 20, 30, 45, 60, 90, 120, 180, 240, 300, 450, 600, 750, 900][: (15 if D == 900 else 11)]
tr["tb"] = pd.cut(tr.tau, bins)

def summarize(g):
    return pd.Series(dict(n=len(g), mk=g.condition_id.nunique(), avg_px=g.ask.mean(),
                          pnl_per_sh=g.pnl.mean(), win=g.side_won.mean(), q=g.q_side.mean(),
                          usd=(g.pnl * g["size"]).sum()))

print("=== market calibration by tau: log loss of market up_price vs model ===")
for b, g in tr.groupby("tb", observed=True):
    y = g.up_won.values
    pm_ = np.clip(g.up_price.values, 0.005, 0.995); pq = np.clip(g.q_up.values, 0.005, 0.995)
    llm = -(y*np.log(pm_)+(1-y)*np.log(1-pm_)).mean(); llq = -(y*np.log(pq)+(1-y)*np.log(1-pq)).mean()
    print(f"{str(b):>12} n={len(g):>8} LL_market={llm:.4f} LL_model={llq:.4f}")

for th in [0.0, 0.02, 0.05, 0.1, 0.2]:
    sel = tr[tr.edge > th]
    print(f"\n=== taker buys where model edge > {th} (all trades n={len(sel)}) ===")
    print(sel.groupby("tb", observed=True).apply(summarize).round(4).to_string())

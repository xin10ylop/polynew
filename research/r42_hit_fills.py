"""Exact backtest fill list of the adopted weekly 'hit' rule (NO only, >= 2 days left, $250 clips, $1,000 per strike,
decision on mid + 2c, execution assumed at the mid 5 min later + 2c + taker fee). Writes data/slow/hit_fills.parquet
for the execution audit (r43)."""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from pm.fees import taker_fee_per_share as fee  # noqa: E402

S = pd.read_parquet("data/slow/hit_weekly_samples.parquet").sort_values("t")
E = pd.read_parquet("data/slow/events_hit_weekly.parquet").reset_index(drop=True)
S["days_left"] = (E.loc[S.m, "end"].values - S.t.values) / 86400
S["cid"] = E.loc[S.m, "cid"].values
S["q_text"] = E.loc[S.m, "q"].values
en = (1 - S.qt1440) - ((1 - S.mid) + 0.02) - fee(1 - S.mid)
T = S[(en > 0.10) & (S.days_left >= 2)]
pos, rows = {}, []
for r in T.itertuples():
    usd = min(250.0, 1000.0 - pos.get(r.m, 0.0))
    if usd <= 1:
        continue
    pos[r.m] = pos.get(r.m, 0.0) + usd
    px = (1 - r.mid_e) + 0.02
    rows.append(dict(event=r.event, m=r.m, cid=r.cid, q=r.q_text, t=int(r.t), split=r.split, mid_yes=r.mid,
                     mid_yes_e=r.mid_e, px_assumed=px, usd=usd, shares=usd / px, win=1 - r.y,
                     pnl=usd / px * ((1 - r.y) - px - fee(px)), model_no=1 - r.qt1440))
F = pd.DataFrame(rows)
F.to_parquet("data/slow/hit_fills.parquet")
print(f"fills {len(F)}, markets {F.cid.nunique()}, weeks {F.event.nunique()}, $ {F.usd.sum():,.0f}, PnL ${F.pnl.sum():+,.0f}")
print("assumed NO price distribution:", F.px_assumed.describe(percentiles=[.05, .25, .5, .75, .95]).round(3).to_dict())

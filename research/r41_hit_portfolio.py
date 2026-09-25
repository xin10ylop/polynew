"""Portfolio simulation of the weekly 'hit' strategy (from r40 samples; model qt1440 chosen on the train half).

Every 6 h, for each unresolved strike where the model edge after costs exceeds THR, buy up to CLIP dollars more of
the favoured side (usually NO), capped at MAXPOS dollars per strike. Execution at the mid 5 min later + half spread
+ SLIP (extra slippage for size) + taker fee. Positions are held to resolution (capital returns at the hit or at
week end). Reports weekly PnL, peak capital, and train/test split.

  python research/r41_hit_portfolio.py [thr=0.10] [half_spread_c=1.0] [slip_c=1.0] [clip=250] [maxpos=1000]"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from pm.fees import taker_fee_per_share  # noqa: E402

a = sys.argv[1:]
THR = float(a[0]) if len(a) > 0 else 0.10
HS = float(a[1]) / 100 if len(a) > 1 else 0.01
SLIP = float(a[2]) / 100 if len(a) > 2 else 0.01
CLIP = float(a[3]) if len(a) > 3 else 250.0
MAXPOS = float(a[4]) if len(a) > 4 else 1000.0
S = pd.read_parquet("data/slow/hit_weekly_samples.parquet").sort_values("t")
q = S.qt1440.values
cost_y = S.mid_e + HS + SLIP
cost_n = (1 - S.mid_e) + HS + SLIP
ey = q - (S.mid + HS + SLIP) - taker_fee_per_share(S.mid)  # decision on the signal-time mid
en = (1 - q) - ((1 - S.mid) + HS + SLIP) - taker_fee_per_share(1 - S.mid)
S["side"] = np.where(ey > THR, "YES", np.where(en > THR, "NO", ""))
S["px"] = np.where(S.side == "YES", cost_y, cost_n)
S["fee"] = taker_fee_per_share(S.px)
S["win"] = np.where(S.side == "YES", S.y, 1 - S.y)
T = S[S.side != ""].copy()
pos = {}
fills = []
for r in T.itertuples():
    have = pos.get(r.m, 0.0)
    usd = min(CLIP, MAXPOS - have)
    if usd <= 1:
        continue
    pos[r.m] = have + usd
    sh = usd / r.px
    fills.append((r.event, r.split, r.t, r.t + r.hold_d * 86400, usd, sh * (r.win - r.px - r.fee), r.side))
F = pd.DataFrame(fills, columns=["event", "split", "t_in", "t_out", "usd", "pnl", "side"])
ev_time = F.groupby("event").t_in.min()
W = F.groupby("event").agg(split=("split", "first"), usd=("usd", "sum"), pnl=("pnl", "sum"), n=("pnl", "size"))
W = W.loc[ev_time.sort_values().index]
# capital in use over time
grid = np.arange(F.t_in.min(), F.t_out.max(), 3600)
cap = np.array([F.usd[(F.t_in <= g) & (F.t_out > g)].sum() for g in grid])
print(f"thr {THR}, half spread {100 * HS:.1f}c, slippage {100 * SLIP:.1f}c, clip ${CLIP:.0f}, max ${MAXPOS:.0f}/strike")
for sp in ("train", "test"):
    w = W[W.split == sp]
    print(f"{sp:5s}: weeks {len(w)}, traded ${w.usd.sum():,.0f}, PnL ${w.pnl.sum():+,.0f} "
          f"({100 * w.pnl.sum() / w.usd.sum():+.1f}% of traded), per week ${w.pnl.mean():+,.0f} "
          f"(t {w.pnl.mean() / (w.pnl.std() / np.sqrt(len(w))):+.2f}), losing weeks {int((w.pnl < 0).sum())}, "
          f"worst week ${w.pnl.min():+,.0f}, best ${w.pnl.max():+,.0f}")
print(f"capital in use: mean ${cap.mean():,.0f}, 95th pct ${np.percentile(cap, 95):,.0f}, max ${cap.max():,.0f}")
print(f"NO share of dollars: {F.usd[F.side == 'NO'].sum() / F.usd.sum():.0%}")
print("PnL per week, chronological:", " ".join(f"{p:+.0f}" for p in W.pnl))

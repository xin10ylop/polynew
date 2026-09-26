"""Fill audit on REAL archived order books (PendulumFlow L2) for weekly 'hit' NO signals (Aug 18 - Sep 18 2026).

For each signal, the first full NO-token book snapshot at or after the signal time (<= 30 min later) is walked to
buy a $250 clip, and compared with the backtest's assumed price (1 - mid 5 min later) + 2c. The bot's slippage guard
(average price <= book mid + 3c) is applied.

  python research/r44_hit_l2_audit.py"""
import glob
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from pm.fees import taker_fee_per_share as fee  # noqa: E402

C = pd.read_parquet("data/slow/hit_signals_recent.parquet")
E = pd.read_parquet("data/slow/events_hit_weekly.parquet").reset_index(drop=True)
C["tok_yes"] = E.loc[C.m, "tok_yes"].values
files = {f.split("/")[-1][:13]: f for f in glob.glob("data/arch_hit/*.parquet")}
cache = {}
rows = []
for r in C.itertuples():
    key = pd.Timestamp(r.t, unit="s").strftime("%Y-%m-%dT%H")
    if key not in files:
        continue
    if key not in cache:
        cache[key] = pd.read_parquet(files[key])
    d = cache[key]
    d = d[(d.cid == r.cid) & (d.ev == 1) & (d.asset != str(r.tok_yes))]  # NO-token snapshots
    d = d[(d.ts >= r.t * 1000) & (d.ts <= (r.t + 1800) * 1000)].sort_values("ts")
    base = dict(event=r.event, cid=r.cid, t=r.t, mid_yes_sig=r.mid, mid_yes_e=r.mid_e, y=r.y)
    if d.empty:
        rows.append({**base, "status": "no snapshot"})
        continue
    bk = json.loads(d.book.iloc[0])
    asks = sorted((p, s) for p, s in bk["a"])
    bids = sorted(((p, s) for p, s in bk["b"]), reverse=True)
    if not asks or not bids:
        rows.append({**base, "status": "one-sided book"})
        continue
    mid_no = (asks[0][0] + bids[0][0]) / 2
    spent, sh = 0.0, 0.0
    for p, s in asks:
        take = min(s, (250 - spent) / p)
        spent += take * p
        sh += take
        if spent >= 249.99:
            break
    vwap = spent / sh
    depth3 = sum(p * s for p, s in asks if p <= mid_no + 0.03)
    status = "filled" if spent >= 249.99 and vwap <= mid_no + 0.03 else ("guard" if spent >= 249.99 else "thin")
    rows.append({**base, "delay_min": (d.ts.iloc[0] / 1000 - r.t) / 60, "best_ask": asks[0][0], "best_bid": bids[0][0],
                 "spread": asks[0][0] - bids[0][0], "mid_no": mid_no, "vwap": vwap, "depth3": depth3, "status": status})
A = pd.DataFrame(rows)
A["px_assumed"] = (1 - A.mid_yes_e) + 0.02
print(f"signals in archived hours: {len(A)} | status: {A.status.value_counts().to_dict()}")
B = A[A.status.isin(["filled", "guard", "thin"])]
print(f"book snapshot delay after signal (min): median {B.delay_min.median():.1f}")
print(f"NO-token spread (c): median {100 * B.spread.median():.1f}, 90th pct {100 * B.spread.quantile(.9):.1f}")
print(f"NO ask depth within mid+3c ($): median {B.depth3.median():,.0f}, 10th pct {B.depth3.quantile(.1):,.0f}")
print(f"real mid_NO vs backtest (1 - mid_yes): median diff {100 * (B.mid_no - (1 - B.mid_yes_e)).median():+.2f}c")
print(f"real $250 VWAP minus backtest assumed price (c): median {100 * (B.vwap - B.px_assumed).median():+.2f}, "
      f"mean {100 * (B.vwap - B.px_assumed).mean():+.2f}, 90th pct {100 * (B.vwap - B.px_assumed).quantile(.9):+.2f}")
Fd = B[B.status == "filled"].copy()
Fd["pnl_real"] = 250 / Fd.vwap * ((1 - Fd.y) - Fd.vwap - fee(Fd.vwap))
Fd["pnl_bt"] = 250 / Fd.px_assumed * ((1 - Fd.y) - Fd.px_assumed - fee(Fd.px_assumed))
print(f"filled clips {len(Fd)}: PnL on real book ${Fd.pnl_real.sum():+,.0f} vs backtest-assumed ${Fd.pnl_bt.sum():+,.0f} "
      f"({100 * Fd.pnl_real.sum() / (250 * max(len(Fd), 1)):+.1f}% of traded on real book)")
pd.set_option("display.width", 220)
cols = ["event", "delay_min", "best_bid", "best_ask", "vwap", "px_assumed", "depth3", "y", "status"]
print(A[cols].assign(event=A.event.str[27:]).round(3).to_string())
A.to_parquet("data/slow/hit_l2_audit.parquet")

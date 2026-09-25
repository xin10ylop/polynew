"""Slow-strategy scan: is the market price at a fixed time-to-expiry calibrated against the outcome?

For every window and sample time tau (seconds before the end), the market price is the median Up-equivalent
trade price in the 5 s before (trade timestamps corrected for the ~2 s data-API lag). Bucketing by the favorite's
price p_fav = max(p, 1-p) (and separately the raw Up price), we compare the favorite's realized win rate with p_fav.
Net edge for a slow taker buying the favorite = win rate - p_fav - half spread (0.5c) - taker fee.
Standard errors are per window (one sample per window per tau), so correlated trades inside a window don't inflate t.

  python research/r36_calibration.py 5m|15m"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from pm.fees import taker_fee_per_share  # noqa: E402

dur = sys.argv[1]
LAG = 2  # data-API timestamp lag (s)
TAUS = [270, 240, 180, 120, 90, 60, 45, 30, 20, 10] if dur == "5m" else [840, 720, 600, 480, 360, 240, 120, 60, 30, 10]
tr = pd.read_parquet(f"data/tr_eval_{dur}.parquet", columns=["condition_id", "ts", "up_price", "end_ts", "up_won", "start_ts"])
tr["t"] = tr.ts - LAG
tr = tr.sort_values(["condition_id", "t"])
rows = []
for cid, g in tr.groupby("condition_id", sort=False):
    t = g.t.values
    p = g.up_price.values
    end = int(g.end_ts.iat[0])
    y = int(g.up_won.iat[0])
    st = int(g.start_ts.iat[0])
    for tau in TAUS:
        a, b = np.searchsorted(t, end - tau - 5, "left"), np.searchsorted(t, end - tau, "right")
        if b - a < 1:
            continue
        rows.append((cid, st, tau, float(np.median(p[a:b])), y))
S = pd.DataFrame(rows, columns=["cid", "st", "tau", "p", "y"])
S["month"] = pd.to_datetime(S.st, unit="s").dt.strftime("%m")
S["fav_up"] = S.p >= 0.5
S["pf"] = np.where(S.fav_up, S.p, 1 - S.p)
S["won"] = np.where(S.fav_up, S.y, 1 - S.y)
S["net"] = S.won - S.pf - 0.005 - taker_fee_per_share(S.pf)
bins = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.93, 0.96, 0.98, 1.0001]
S["bk"] = pd.cut(S.pf, bins, right=False)
print(f"{dur}: {S.cid.nunique()} windows, {len(S)} samples")


def table(D):
    g = D.groupby(["tau", "bk"], observed=True).agg(n=("won", "size"), pf=("pf", "mean"), win=("won", "mean"),
                                                     net=("net", "mean"), sd=("net", "std"))
    g["dev_c"] = 100 * (g.win - g.pf)
    g["net_c"] = 100 * g.net
    g["t"] = g.net / (g.sd / np.sqrt(g.n))
    return g[["n", "pf", "win", "dev_c", "net_c", "t"]]


pd.set_option("display.width", 200)
T = table(S)
print(T[T.n >= 50].round(3).to_string())
print("\nby month (only cells with |t| >= 2 overall):")
hot = T[(T.n >= 50) & (T.t.abs() >= 2)].index
for mo, D in S.groupby("month"):
    M = table(D)
    print(mo, M.loc[M.index.intersection(hot)][["n", "dev_c", "net_c", "t"]].round(2).to_string())
S.drop(columns=["bk"]).to_parquet(f"data/calib_{dur}.parquet")

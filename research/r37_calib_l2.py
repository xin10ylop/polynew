"""Check the r36 calibration pattern on EXECUTABLE prices: archived L2 best bid/ask (PendulumFlow, QC'd hours).

At end - tau (+ reaction delay d), buy the favorite at its best ask (Up ask = ba; Down ask = 1 - bb), hold to
settlement. net = won - ask - taker fee. One sample per window per (tau, d); t-stats are per window.

  python research/r37_calib_l2.py 5m|15m"""
import glob
import json
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, ".")
from pm.fees import taker_fee_per_share  # noqa: E402

dur = sys.argv[1]
TAUS = [120, 90, 60, 45, 30, 20, 10] if dur == "5m" else [480, 360, 240, 120, 60, 30, 10]
DELAYS = [0, 1000, 3000]
M = pd.read_parquet(f"data/markets_{dur}.parquet").dropna(subset=["up_won"]).set_index("condition_id")
qc = json.load(open("data/arch/_qc.json"))
files = [f for f in sorted(glob.glob("data/arch/20*.parquet")) if qc.get(f.split("/")[-1][:13], 0) >= 30_000_000]
parts = []
for f in files:
    t = pq.read_table(f, columns=["ev", "ts", "cid", "bb", "ba"])
    d = t.to_pandas()
    d = d[(d.ev == 0) & d.cid.isin(M.index)][["ts", "cid", "bb", "ba"]].sort_values("ts")
    d["bk"] = d.ts // 250  # keep the last book state per market per 250 ms
    parts.append(d.drop_duplicates(["cid", "bk"], keep="last").drop(columns="bk"))
    del t, d
E = pd.concat(parts).dropna().sort_values(["cid", "ts"])
covered = {f.split("/")[-1][:13] for f in files}
rows = []
for cid, g in E.groupby("cid", sort=False):
    r = M.loc[cid]
    st, en = int(r.start_ts), int(r.end_ts)
    hrs = {pd.Timestamp(h, unit="s").strftime("%Y-%m-%dT%H") for h in range(st // 3600 * 3600, en // 3600 * 3600 + 1, 3600)}
    if not hrs <= covered:
        continue
    ts, bb, ba = g.ts.values, g.bb.values, g.ba.values
    y = int(r.up_won)
    for tau in TAUS:
        for dl in DELAYS:
            t0 = (en - tau) * 1000 + dl
            i = np.searchsorted(ts, t0, "right") - 1
            if i < 0 or t0 - ts[i] > 30_000:
                continue
            b, a = float(bb[i]), float(ba[i])
            if not (0 < b < a < 1):
                continue
            mid = (a + b) / 2
            fav_up = mid >= 0.5
            ask = a if fav_up else round(1 - b, 4)
            won = y if fav_up else 1 - y
            rows.append((cid, st, tau, dl, mid if fav_up else 1 - mid, ask, a - b, won))
S = pd.DataFrame(rows, columns=["cid", "st", "tau", "dl", "pf", "ask", "spr", "won"])
S["net"] = S.won - S.ask - taker_fee_per_share(S.ask)
S["day"] = pd.to_datetime(S.st, unit="s").dt.strftime("%m-%d")
S["bk"] = pd.cut(S.pf, [0.5, 0.7, 0.8, 0.85, 0.9, 0.93, 0.96, 0.98, 1.0001], right=False)
print(f"{dur}: {S.cid.nunique()} windows with L2 coverage over {S.day.nunique()} days")
pd.set_option("display.width", 200)
g = S.groupby(["dl", "tau", "bk"], observed=True).agg(n=("net", "size"), pf=("pf", "mean"), ask=("ask", "mean"),
                                                        win=("won", "mean"), net=("net", "mean"), sd=("net", "std"))
g["net_c"] = 100 * g.net
g["t"] = g.net / (g.sd / np.sqrt(g.n))
print(g[g.n >= 30][["n", "pf", "ask", "win", "net_c", "t"]].round(3).to_string())
S.drop(columns=["bk"]).to_parquet(f"data/calib_l2_{dur}.parquet")

"""Execution audit of the weekly 'hit' NO strategy against the REAL trade tape (data-api).

For each NO signal (model edge > 10c after mid + 2c + fee, >= 2 days left), a fill is only credited if other traders
actually bought NO (or sold YES, the same thing) at a price <= LIMIT = (1 - mid) + 3c within W minutes after the
signal. We pay the volume-weighted price THEY paid, and take at most the dollar volume THEY took (capped by the clip).
Signals whose midpoint is the 0.500 placeholder of a book that has never quoted are dropped.
Portfolio: $250 clips, $1,000 per strike, one clip per 6 h sample. PnL held to resolution, taker fee included.

  python research/r43_hit_tape_audit.py [window_min=30] [limit_c=3]"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from pm.fees import taker_fee_per_share as fee  # noqa: E402

W = int(sys.argv[1]) * 60 if len(sys.argv) > 1 else 1800
LIM = float(sys.argv[2]) / 100 if len(sys.argv) > 2 else 0.03
S = pd.read_parquet("data/slow/hit_weekly_samples.parquet").sort_values("t")
E = pd.read_parquet("data/slow/events_hit_weekly.parquet").reset_index(drop=True)
P = pd.read_parquet("data/slow/px_hit_weekly.parquet")
T = pd.read_parquet("data/slow/tape_hit.parquet")
S["cid"] = E.loc[S.m, "cid"].values
S["days_left"] = (E.loc[S.m, "end"].values - S.t.values) / 86400
en = (1 - S.qt1440) - ((1 - S.mid) + 0.02) - fee(1 - S.mid)
C = S[(en > 0.10) & (S.days_left >= 2)].copy()

# placeholder detection: has the price history ever left 0.500 before the signal?
first_real = P[P.p.round(4) != 0.5].groupby("m").t.min()
C["book_live"] = C.t.values >= C.m.map(first_real).fillna(np.inf).values
C["placeholder"] = (C.mid.round(4) == 0.5) & ~C.book_live
# NO-equivalent taker buys: bought No at p, or sold Yes at p (== buying No at 1 - p)
T["no_px"] = np.where(T.outcome.str.lower() == "no", T.price, 1 - T.price)
T["no_buy"] = ((T.outcome.str.lower() == "no") & (T.side == "BUY")) | ((T.outcome.str.lower() == "yes") & (T.side == "SELL"))
T["usd"] = T.size * T.no_px
NB = T[T.no_buy].sort_values(["cid", "ts"])
by_cid = {c: (g.ts.values, g.no_px.values, g.usd.values) for c, g in NB.groupby("cid")}

rows = []
for r in C.itertuples():
    lim = (1 - r.mid) + LIM
    ts, px, usd = by_cid.get(r.cid, (np.array([]), np.array([]), np.array([])))
    a, b = np.searchsorted(ts, r.t, "left"), np.searchsorted(ts, r.t + W, "right")
    ok = px[a:b] <= lim + 1e-9
    vol = usd[a:b][ok].sum()
    vwap = (px[a:b][ok] * usd[a:b][ok]).sum() / vol if vol > 0 else np.nan
    rows.append((vol, vwap, (b - a)))
C[["tape_vol", "tape_vwap", "tape_n"]] = rows
print(f"NO signals {len(C)}; placeholder-mid signals dropped {int(C.placeholder.sum())}")
C = C[~C.placeholder]
print(f"signals with tape NO volume <= limit within {W // 60} min: {np.mean(C.tape_vol > 0):.0%} "
      f"(>= $250: {np.mean(C.tape_vol >= 250):.0%}); median tape price minus (1-mid): "
      f"{100 * np.nanmedian(C.tape_vwap - (1 - C.mid)):+.2f}c")

pos, fills = {}, []
for r in C.itertuples():
    if not r.tape_vol > 0:
        continue
    usd = min(250.0, 1000.0 - pos.get(r.m, 0.0), r.tape_vol)
    if usd < 5:
        continue
    pos[r.m] = pos.get(r.m, 0.0) + usd
    px = r.tape_vwap
    fills.append((r.event, r.split, r.t, usd, usd / px * ((1 - r.y) - px - fee(px))))
F = pd.DataFrame(fills, columns=["event", "split", "t", "usd", "pnl"])
Wk = F.groupby("event").agg(split=("split", "first"), t=("t", "min"), usd=("usd", "sum"), pnl=("pnl", "sum")).sort_values("t")
print(f"\nTAPE-PROVEN portfolio (window {W // 60} min, limit mid+{100 * LIM:.0f}c): fills {len(F)}, traded ${F.usd.sum():,.0f}")
for sp in ("train", "test"):
    w = Wk[Wk.split == sp]
    print(f"{sp:5s}: weeks {len(w)}, PnL ${w.pnl.sum():+,.0f} ({100 * w.pnl.sum() / w.usd.sum():+.1f}% of traded), "
          f"per week ${w.pnl.mean():+,.0f} (t {w.pnl.mean() / (w.pnl.std() / np.sqrt(len(w))):+.2f}), "
          f"losing weeks {int((w.pnl < 0).sum())}, worst ${w.pnl.min():+,.0f}")
Wk["end"] = pd.to_datetime(Wk.index.map(E.groupby("event").end.max()), unit="s")
for mo in sorted(Wk.end.dt.strftime("%Y-%m").unique())[-6:]:
    w = Wk[Wk.end.dt.strftime("%Y-%m") == mo]
    print(f"  {mo}: weeks {len(w)}, traded ${w.usd.sum():,.0f}, PnL ${w.pnl.sum():+,.0f} ({' '.join(f'{p:+.0f}' for p in w.pnl)})")
C.to_parquet("data/slow/hit_signals_audited.parquet")

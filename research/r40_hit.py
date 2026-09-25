"""Weekly/monthly 'Will Bitcoin reach/dip to $X' barrier markets vs a reflection-principle volatility model.

YES iff any Binance BTCUSDT 1m candle from market creation to period end has High >= X (reach) or Low <= X (dip).
At sample times every 6 h (only while the barrier has not been hit), model q = P(hit before end)
= min(1, 2 * P(Z > b / (sigma sqrt(tau)))), b = |ln(X/S)|, with Normal or t(4) tails and EWMA 1m vol.
Decision on the market mid at t; execution at the mid >= 5 min later + half spread + taker fee; hold to resolution
(capital is released at the hit or at period end). Clustered by event; train/test by date.

  python research/r40_hit.py hit_weekly|hit_monthly [entry_delay_s=300]"""
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, ".")
from pm.fees import taker_fee_per_share  # noqa: E402

name = sys.argv[1]
DELAY = int(sys.argv[2]) if len(sys.argv) > 2 else 300  # entry delay after the signal (s)
E = pd.read_parquet(f"data/slow/events_{name}.parquet").reset_index(drop=True)
P = pd.read_parquet(f"data/slow/px_{name}.parquet").sort_values(["m", "t"])
K = pd.read_parquet("data/klines_1m.parquet")
kms, kh, kl, kc = K.ms.values, K.h.values, K.l.values, K.c.values
x = E.q.str.extract(r"\$([\d,.]+)\s*([kK])?")
E["X"] = x[0].str.replace(",", "").astype(float) * np.where(x[1].notna(), 1000, 1)
E["up"] = E.q.str.contains(r"reach|above|hit \$", case=False) & ~E.q.str.contains("dip", case=False)
E = E[(E.start * 1000 >= kms[0] + 7 * 86400_000) & (E.end * 1000 <= kms[-1]) & E.yes_final.notna()].copy()
r = np.diff(np.log(kc), prepend=np.log(kc[0]))
r[np.abs(r) > 0.05] = 0.0
VAR = {hl: pd.Series(r ** 2).ewm(alpha=1 - 0.5 ** (1 / hl), adjust=False).mean().values for hl in (1440, 4320)}

rows, bad = [], 0
for m, g in P.groupby("m", sort=False):
    if m not in E.index:
        continue
    e = E.loc[m]
    a, b = np.searchsorted(kms, int(e.start) * 1000), np.searchsorted(kms, int(e.end) * 1000)
    ext = np.maximum.accumulate(kh[a:b]) if e.up else np.minimum.accumulate(kl[a:b])
    hit_idx = np.argmax(ext >= e.X) if e.up else np.argmax(ext <= e.X)
    hit = bool(ext[hit_idx] >= e.X) if e.up else bool(ext[hit_idx] <= e.X)
    if hit != (e.yes_final > 0.5):
        bad += 1
        continue
    t_hit = kms[a + hit_idx] / 1000 if hit else float(e.end)
    tt, pp = g.t.values, g.p.values
    for t0 in range(int(e.start) + 3600, int(e.end) - 3600, 6 * 3600):
        if t0 >= t_hit:
            break
        j = np.searchsorted(tt, t0, "right") - 1
        k = np.searchsorted(tt, t0 + DELAY, "left")
        if j < 0 or t0 - tt[j] > 900 or k >= len(tt) or tt[k] > t0 + DELAY + 600 or tt[k] >= t_hit:
            continue
        i = np.searchsorted(kms, t0 * 1000 - 60_000, "right") - 1
        s = kc[i]
        tau = (e.end - t0) / 60
        bb = abs(np.log(e.X / s))
        qs = []
        for hl in (1440, 4320):
            z = bb / np.sqrt(VAR[hl][i] * tau)
            qs += [min(1.0, 2 * stats.norm.sf(z)), min(1.0, 2 * stats.t.sf(z * np.sqrt(2), 4))]
        rows.append([m, e.event, t0, float(pp[j]), float(pp[k]), float(hit), (t_hit - t0) / 86400, s, e.X, e.up] + qs)
S = pd.DataFrame(rows, columns=["m", "event", "t", "mid", "mid_e", "y", "hold_d", "s", "X", "up",
                                "qn1440", "qt1440", "qn4320", "qt4320"])
S = S[(S.mid > 0.003) & (S.mid < 0.997)]
evs = S.groupby("event").t.min().sort_values()
cut = evs.iloc[len(evs) // 2]
S["split"] = np.where(S.event.map(evs) < cut, "train", "test")
print(f"{name}: markets {len(E)}, outcome mismatches dropped {bad}, samples {len(S)}, events {S.event.nunique()}")


def ll(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))


qcols = ["qn1440", "qt1440", "qn4320", "qt4320"]
for sp in ("train", "test"):
    D = S[S.split == sp]
    print(sp, "log-loss: market", round(ll(D.mid, D.y), 4), {c: round(ll(D[c], D.y), 4) for c in qcols})
D = S[S.split == "train"]
best = min(qcols, key=lambda c: ll(D[c], D.y))
print("best on train:", best)


def hs(p):
    return np.where((p > 0.05) & (p < 0.95), 0.005, 0.001)


print("\nbucket view (all): market mid vs realized hit rate")
S["bk"] = pd.cut(S.mid, [0, 0.02, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.98, 1])
print(S.groupby("bk", observed=True).agg(n=("y", "size"), mid=("mid", "mean"), hit=("y", "mean"),
                                         model=(best, "mean")).round(4).to_string())
for thr in (0.02, 0.05, 0.10):
    for sp in ("train", "test"):
        D = S[S.split == sp]
        q = D[best].values
        ey = q - D.mid - hs(D.mid) - taker_fee_per_share(D.mid)
        en = (1 - q) - (1 - D.mid) - hs(D.mid) - taker_fee_per_share(1 - D.mid)
        ay, an = D.mid_e + hs(D.mid_e), (1 - D.mid_e) + hs(D.mid_e)
        by, bn = ey > thr, (en > thr) & ~(ey > thr)
        pnl = np.where(by, D.y - ay - taker_fee_per_share(ay), np.where(bn, (1 - D.y) - an - taker_fee_per_share(an), np.nan))
        cost = np.where(by, ay, an)
        T = pd.DataFrame({"event": D.event.values, "pnl": pnl, "cost": cost, "hold": D.hold_d.values,
                          "side": np.where(by, "YES", "NO")}).dropna()
        if len(T) < 5:
            continue
        ev = T.groupby("event").pnl.sum()
        roc = T.pnl.sum() / (T.cost * T.hold).sum()  # profit per $ per day of capital tied up
        print(f"thr {thr:.2f} {sp:5s}: n {len(T):4d} ({(T.side == 'NO').mean():.0%} NO) events {ev.size:3d} | "
              f"{100 * T.pnl.mean():+6.2f}c/sh | event-t {ev.mean() / (ev.std() / np.sqrt(ev.size)):+5.2f} | "
              f"return per $ per day {100 * roc:+.3f}%")
S.drop(columns=["bk"]).to_parquet(f"data/slow/{name}_samples{'' if DELAY == 300 else f'_d{DELAY}'}.parquet")

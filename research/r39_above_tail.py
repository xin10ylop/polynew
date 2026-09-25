"""Strict near-expiry test of the daily 'Bitcoin above $K at noon ET' ladders.

Signal at t0 = end - x minutes: model q (t4, EWMA vol, Binance closes strictly before t0) vs market mid at t0
(1-minute CLOB midpoint, at most 2 min old). Execution: the market mid at the first point >= t0 + DELAY s
(<= t0 + DELAY + 240 s), plus half spread plus taker fee. Hold to resolution. Day-clustered t; train/test by date.

  python research/r39_above_tail.py [delay_s=60] [half_spread_c=0.5]"""
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, ".")
from pm.fees import taker_fee_per_share  # noqa: E402

DELAY = int(sys.argv[1]) if len(sys.argv) > 1 else 60
HS = float(sys.argv[2]) / 100 if len(sys.argv) > 2 else 0.005
E = pd.read_parquet("data/slow/events_above_daily.parquet").reset_index(drop=True)
P = pd.read_parquet("data/slow/px1m_above_daily.parquet").sort_values(["m", "t"])
K = pd.read_parquet("data/klines_1m.parquet")
kms, kc = K.ms.values, K.c.values
E["K"] = E.q.str.extract(r"\$([\d,]+(?:\.\d+)?)")[0].str.replace(",", "").astype(float)
E = E[(E.end * 1000 >= kms[0] + 30 * 86400_000) & (E.end * 1000 <= kms[-1])].copy()
i_end = np.searchsorted(kms, E.end.values.astype(np.int64) * 1000)
E["S_T"] = np.where((i_end < len(kms)) & (kms[np.minimum(i_end, len(kms) - 1)] == E.end.values * 1000),
                    kc[np.minimum(i_end, len(kms) - 1)], np.nan)
E["y"] = (E.S_T > E.K).astype(float)
E = E[E.S_T.notna() & E.yes_final.notna() & (E.y == (E.yes_final > 0.5))].copy()
E["day"] = pd.to_datetime(E.end, unit="s").dt.strftime("%Y-%m-%d")

r = np.diff(np.log(kc), prepend=np.log(kc[0]))
r[np.abs(r) > 0.05] = 0.0
VAR = {hl: pd.Series(r ** 2).ewm(alpha=1 - 0.5 ** (1 / hl), adjust=False).mean().values for hl in (60, 360, 4320)}


def q_model(t0, T, strike, hl):
    i = np.searchsorted(kms, t0 * 1000 - 60_000, "right") - 1  # candle closed before t0
    sd = np.sqrt(VAR[hl][i] * (T + 60 - t0) / 60)
    return float(stats.t.sf(np.log(strike / kc[i]) / sd * np.sqrt(2), 4)), float(kc[i])


XS = [180, 120, 90, 60, 45, 30, 20, 10, 5]
rows = []
for m, g in P.groupby("m", sort=False):
    if m not in E.index:
        continue
    e = E.loc[m]
    tt, pp = g.t.values, g.p.values
    for x in XS:
        t0 = int(e.end - 60 * x)
        j = np.searchsorted(tt, t0, "right") - 1
        k = np.searchsorted(tt, t0 + DELAY, "left")
        if j < 0 or t0 - tt[j] > 120 or k >= len(tt) or tt[k] > t0 + DELAY + 240 or tt[k] >= e.end:
            continue
        row = [m, e.day, x, float(pp[j]), float(pp[k]), e.y]
        for hl in (60, 360, 4320):
            q, s0 = q_model(t0, int(e.end), e.K, hl)
            row.append(q)
        rows.append(row + [s0, e.K])
S = pd.DataFrame(rows, columns=["m", "day", "x", "mid", "mid_e", "y", "q60", "q360", "q4320", "s0", "K"])
days = sorted(S.day.unique())
cut = days[len(days) // 2]
S["split"] = np.where(S.day < cut, "train", "test")
print(f"delay {DELAY}s, half spread {100 * HS:.1f}c: samples {len(S)}, days {len(days)}, cut {cut}")


def ll(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))


print("\nlog-loss by minutes-to-expiry (all days): market mid at t0 vs models")
print(S.groupby("x").apply(lambda d: pd.Series({"n": len(d), "mkt": ll(d.mid, d.y), "mkt_entry": ll(d.mid_e, d.y),
                                                 "q60": ll(d.q60, d.y), "q360": ll(d.q360, d.y),
                                                 "q4320": ll(d.q4320, d.y)})).round(4).to_string())


def hs(p):
    return np.where((p > 0.05) & (p < 0.95), HS, 0.001)


def run(D, qc, thr):
    q = D[qc].values
    ey = q - D.mid - hs(D.mid) - taker_fee_per_share(D.mid)  # decision on the signal-time mid
    en = (1 - q) - (1 - D.mid) - hs(D.mid) - taker_fee_per_share(1 - D.mid)
    ay = D.mid_e + hs(D.mid_e)  # execution at the later mid
    an = (1 - D.mid_e) + hs(D.mid_e)
    by, bn = ey > thr, (en > thr) & ~(ey > thr)
    pnl = np.where(by, D.y - ay - taker_fee_per_share(ay), np.where(bn, (1 - D.y) - an - taker_fee_per_share(an), np.nan))
    return pd.DataFrame({"day": D.day.values, "x": D.x.values, "pnl": pnl, "px": np.where(by, ay, an)}).dropna()


for qc in ("q60", "q360", "q4320"):
    print(f"\n=== model {qc}")
    for thr in (0.02, 0.05, 0.10):
        for split in ("train", "test"):
            T = run(S[S.split == split], qc, thr)
            if len(T) < 5:
                continue
            dd = T.groupby("day").pnl.sum()
            print(f"thr {thr:.2f} {split:5s}: n {len(T):4d} days {dd.size:3d} | {100 * T.pnl.mean():+6.2f}c/sh | "
                  f"t {dd.mean() / (dd.std() / np.sqrt(dd.size)):+5.2f} | by min-to-expiry: "
                  + " ".join(f"{x}:{100 * g.pnl.mean():+.1f}({len(g)})" for x, g in T.groupby("x")))
S.to_parquet(f"data/slow/above_tail_d{DELAY}.parquet")

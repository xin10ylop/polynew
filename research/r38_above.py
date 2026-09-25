"""Daily 'Bitcoin above $K at noon ET' strike ladders (Polymarket series 45) vs a volatility model.

Resolution: Binance BTCUSDT 1-minute candle opening at noon ET (= market endDate); YES iff its close > K.
Market price: CLOB midpoint history (5-min points). Executable price = mid + half spread (0.5c for 5-95c, else 0.1c)
plus the taker fee. Model: P(S_T > K) from the last completed Binance 1m close and an EWMA realized-vol estimate,
with Normal or Student-t(4) (variance-matched) log returns.

Step 1 validates the outcome reconstruction. Step 2 compares log-loss (market vs model) by horizon.
Step 3 is a trading backtest: at fixed horizons, buy YES or NO when model edge > threshold. PnL is held to
resolution. Standard errors are clustered by event day (all strikes share one BTC path). Train = first half of days,
test = second half.

  python research/r38_above.py"""
import re
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, ".")
from pm.fees import taker_fee_per_share  # noqa: E402

E = pd.read_parquet("data/slow/events_above_daily.parquet").reset_index(drop=True)
P = pd.read_parquet("data/slow/px_above_daily.parquet")
K = pd.read_parquet("data/klines_1m.parquet")
kms, kc = K.ms.values, K.c.values
E["K"] = E.q.str.extract(r"\$([\d,]+(?:\.\d+)?)")[0].str.replace(",", "").astype(float)
E = E[(E.end * 1000 >= kms[0] + 30 * 86400_000) & (E.end * 1000 <= kms[-1])].copy()


# --- step 1: outcome reconstruction ---------------------------------------------------------------
def close_at_open(ms):
    i = np.searchsorted(kms, ms)
    return kc[i] if i < len(kms) and kms[i] == ms else np.nan


E["S_T"] = [close_at_open(int(e) * 1000) for e in E.end]
E["y"] = (E.S_T > E.K).astype(float)
E["y_mkt"] = (E.yes_final > 0.5).astype(float)
ok = E.S_T.notna() & E.yes_final.notna()
print(f"markets {len(E)}, reconstructable {ok.sum()}, outcome match {np.mean(E.y[ok] == E.y_mkt[ok]):.4f}")
E = E[ok & (E.y == E.y_mkt)].copy()
E["day"] = pd.to_datetime(E.end, unit="s").dt.strftime("%Y-%m-%d")

# --- realized vol (EWMA of 1m log-return variance), causal -------------------------------------------
r = np.diff(np.log(kc), prepend=np.log(kc[0]))
r[np.abs(r) > 0.05] = 0.0  # data glitches
var_by_hl = {}
for hl_min in (360, 1440, 4320):
    a = 1 - 0.5 ** (1 / hl_min)
    v = pd.Series(r ** 2).ewm(alpha=a, adjust=False).mean().values
    var_by_hl[hl_min] = v


def model_q(t_s, T_s, strike, hl, dist):
    i = np.searchsorted(kms, t_s * 1000 - 60_000, "right") - 1  # last candle fully closed before t
    s = kc[i]
    tau_min = (T_s + 60 - t_s) / 60
    sd = np.sqrt(var_by_hl[hl][i] * tau_min)
    z = np.log(strike / s) / sd
    if dist == "t4":
        return stats.t.sf(z * np.sqrt(2), 4)  # t(4) scaled to unit variance: var = 4/2 = 2
    return stats.norm.sf(z)


# --- samples at fixed horizons ---------------------------------------------------------------------
HORIZONS_H = [48, 24, 12, 6, 3, 1, 0.5]
P = P.sort_values(["m", "t"])
rows = []
for m, g in P.groupby("m", sort=False):
    if m not in E.index:
        continue
    e = E.loc[m]
    tt, pp = g.t.values, g.p.values
    for h in HORIZONS_H:
        t0 = int(e.end - h * 3600)
        j = np.searchsorted(tt, t0, "right") - 1
        if j < 0 or t0 - tt[j] > 900:
            continue
        rows.append((m, e.day, h, t0, float(pp[j]), e.y, e.K, int(e.end)))
S = pd.DataFrame(rows, columns=["m", "day", "h", "t", "mid", "y", "K", "T"])
S = S[(S.mid > 0.002) & (S.mid < 0.998)]
for hl in (360, 1440, 4320):
    for dist in ("norm", "t4"):
        S[f"q_{dist}_{hl}"] = [model_q(t, T, k, hl, dist) for t, T, k in zip(S.t, S["T"], S.K)]
days = sorted(S.day.unique())
cut = days[len(days) // 2]
S["split"] = np.where(S.day < cut, "train", "test")
print(f"samples {len(S)} over {len(days)} days; train < {cut} <= test")


def ll(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))


qcols = [c for c in S.columns if c.startswith("q_")]
print("\nlog-loss by horizon (train): market vs models")
tr = S[S.split == "train"]
print(tr.groupby("h").apply(lambda d: pd.Series({"n": len(d), "market": ll(d.mid, d.y),
                                                  **{c: ll(d[c], d.y) for c in qcols}})).round(4).to_string())
best = min(qcols, key=lambda c: ll(tr[c], tr.y))
print("best model on train:", best)


# --- step 3: trading backtest ---------------------------------------------------------------------
def half_spread(p):
    return np.where((p > 0.05) & (p < 0.95), 0.005, 0.001)


def trades(D, q, thr):
    ask_y = D.mid + half_spread(D.mid)
    ask_n = (1 - D.mid) + half_spread(D.mid)
    ey = q - ask_y - taker_fee_per_share(ask_y)
    en = (1 - q) - ask_n - taker_fee_per_share(ask_n)
    buy_y = ey > thr
    buy_n = (en > thr) & ~buy_y
    pnl = np.where(buy_y, D.y - ask_y - taker_fee_per_share(ask_y),
                   np.where(buy_n, (1 - D.y) - ask_n - taker_fee_per_share(ask_n), np.nan))
    cost = np.where(buy_y, ask_y, np.where(buy_n, ask_n, np.nan))
    return pd.DataFrame({"day": D.day.values, "h": D.h.values, "pnl": pnl, "cost": cost}).dropna()


print(f"\ntrading with {best}: PnL per share (c), day-clustered t")
for thr in (0.02, 0.04, 0.06, 0.10):
    for split in ("train", "test"):
        D = S[S.split == split]
        T = trades(D, D[best], thr)
        if len(T) == 0:
            continue
        dd = T.groupby("day").pnl.sum()
        print(f"thr {thr:.2f} {split:5s}: trades {len(T):5d} on {dd.size:3d} days | {100 * T.pnl.mean():+6.2f}c/share"
              f" | $/day at 100 sh/trade {100 * dd.mean():+7.2f} (t {dd.mean() / (dd.std() / np.sqrt(dd.size)):+.2f})"
              f" | by horizon: " + " ".join(f"{h}h:{100 * g.pnl.mean():+.1f}" for h, g in T.groupby("h")))
S.to_parquet("data/slow/above_samples.parquet")

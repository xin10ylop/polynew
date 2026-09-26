"""The paper bot's model must reproduce the backtest's model (research/r40_hit.py, qt1440) on historical samples.

  python tests/test_hitbot_parity.py"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
import bot.hitbot as hb  # noqa: E402

K = pd.read_parquet("data/klines_1m.parquet")
S = pd.read_parquet("data/slow/hit_weekly_samples.parquet").sample(300, random_state=7)
E = pd.read_parquet("data/slow/events_hit_weekly.parquet").reset_index(drop=True)
kms = K.ms.values
arr = K[["ms", "h", "l", "c"]].values


def fake_klines(start_ms):
    i = np.searchsorted(kms, start_ms)
    j = np.searchsorted(kms, hb.now_s() * 1000)
    return [(int(r[0]), r[1], r[2], r[3], int(r[0]) + 59_999) for r in arr[i:j]]


hb.klines = fake_klines
errs = []
for r in S.itertuples():
    hb.now_s = lambda t=r.t: float(t)
    btc = hb.BTC(int(r.t * 1000) - 8 * 86400_000)
    q = btc.q_touch(r.X, float(E.loc[r.m, "end"]))
    errs.append(abs(q - r.qt1440))
errs = np.array(errs)
print(f"samples {len(errs)}: |bot q - backtest q| median {np.median(errs):.4f}, 95th pct {np.percentile(errs, 95):.4f}, "
      f"max {errs.max():.4f}")
assert np.percentile(errs, 95) < 0.02, "bot model deviates from the backtest model"
print("OK")

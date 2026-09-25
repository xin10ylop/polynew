"""TWAP-aware fair-value model for Polymarket BTC up/down markets.

Settlement: Up iff TWAP60(end) >= TWAP60(start), both from Chainlink BTC/USD.
We observe Binance BTCUSDT 1s closes. Chainlink ~= Binance * exp(basis) with a slowly
varying basis; the basis at window start is known exactly from price_to_beat, and it
drifts only ~0.5bp (1 sd) over a 5-15 minute window.

At decision time t (unix seconds) we know Binance closes for seconds <= t-1.
F = mean of closes over seconds [e-60, e-1]. Known part is summed exactly, the unknown
part is modelled as driftless Brownian motion from the last known close, giving a
Gaussian approximation for F.
"""
import numpy as np
import pandas as pd
from scipy.special import ndtr


class Series1s:
    def __init__(self, path="data/btcusdt_1s.parquet", df=None):
        df = pd.read_parquet(path) if df is None else df
        self.t0 = int(df.ts.iloc[0])
        self.t1 = int(df.ts.iloc[-1])
        self.c = df.c.values.astype(np.float64)
        self.cs = np.concatenate([[0.0], np.cumsum(self.c)])
        self.lr = np.concatenate([[0.0], np.diff(np.log(self.c))])
        self.v = df.v.values.astype(np.float64)
        self.tbv = df.tbv.values.astype(np.float64)
        self._ewma = {}

    def idx(self, ts):
        return np.asarray(ts, dtype=np.int64) - self.t0

    def close(self, ts):
        return self.c[self.idx(ts)]

    def sum_closes(self, a, b):
        """Sum of closes over seconds [a, b) (vectorized)."""
        return self.cs[self.idx(b)] - self.cs[self.idx(a)]

    def twap(self, T, L=60):
        return self.sum_closes(np.asarray(T) - L, T) / L

    def ewma_var(self, halflife):
        """Per-second variance estimate known at the end of each second (causal)."""
        if halflife not in self._ewma:
            a = 1 - 0.5 ** (1.0 / halflife)
            r2 = self.lr ** 2
            # pandas ewm is fast enough for 4M points
            v = pd.Series(r2).ewm(alpha=a, adjust=False).mean().values
            self._ewma[halflife] = v
        return self._ewma[halflife]

    def sigma_at(self, t, halflife=600):
        """sigma per sqrt(second) using info up to second t-1."""
        return np.sqrt(self.ewma_var(halflife)[self.idx(t) - 1])


def twap_dist(S, s, e, t, sigma, L=60):
    """Mean and sd of the settlement TWAP (Binance terms) conditional on data up to t-1.

    s, e, t are arrays of unix seconds; sigma per sqrt(second)."""
    t = np.asarray(t, dtype=np.int64)
    e = np.asarray(e, dtype=np.int64)
    last = t - 1                         # last known second
    w0 = e - L                           # first second in TWAP window
    known_end = np.clip(last + 1, w0, e)  # known seconds are [w0, known_end)
    k = known_end - w0
    Sk = np.where(k > 0, S.sum_closes(w0, np.maximum(known_end, w0)), 0.0)
    cL = S.close(last)
    n = L - k                            # unknown seconds count
    d0 = np.maximum(w0, last + 1) - last  # lag of first unknown second (>=1)
    varsum = n.astype(float) ** 2 * (d0 - 1) + n * (n + 1) * (2 * n + 1) / 6.0
    mean = (Sk + n * cL) / L
    sd = cL * sigma * np.sqrt(np.maximum(varsum, 0)) / L
    return mean, sd


def fair_up(S, s, e, ptb, t, sigma, basis_sd_bp=0.5, L=60, df_t=None):
    """P(Up) given Binance data up to t-1.

    ptb: Chainlink price to beat. Basis at start is b_s = ln(ptb / binance_twap(s)).
    Up iff F_binance*exp(b_e) >= ptb  <=>  F_binance >= ptb*exp(-b_e) ~= binance_twap(s)*exp(-(b_e-b_s)).
    """
    s = np.asarray(s, dtype=np.int64)
    Kb = S.twap(s, L)  # strike in Binance terms (basis cancels)
    mean, sd = twap_dist(S, s, e, t, sigma, L)
    frac = np.clip((np.asarray(t) - s) / np.maximum(np.asarray(e) - s, 1), 0, 1)
    bsd = Kb * basis_sd_bp * 1e-4 * np.ones_like(frac)
    tot = np.sqrt(sd ** 2 + bsd ** 2)
    z = (mean - Kb) / np.maximum(tot, 1e-9)
    if df_t is None:
        return ndtr(z), z
    from scipy.stats import t as student
    return student.cdf(z, df_t), z

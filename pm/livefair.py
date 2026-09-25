"""Live fair value from Chainlink (settlement source, ~1.4s delayed) + a leading exchange (Coinbase).

At wall time T (ms): known Chainlink obs are those with recv <= T. Estimate the current Chainlink-equivalent
price by applying the Coinbase return since the last Chainlink observation timestamp.
Strike = mean of Chainlink 1s obs over [s-60, s); settlement = mean over [e-60, e).
"""
import numpy as np
from scipy.stats import t as student


class LiveFair:
    def __init__(self, cl, lead, df_t=5):
        # cl: DataFrame ts(ms obs), px, recv ; lead: DataFrame ts, px, recv
        self.cl_ts = cl.ts.values // 1000        # observation second
        self.cl_px = cl.px.values
        self.cl_recv = cl.recv.values
        self.ld_recv = lead.recv.values
        self.ld_px = lead.px.values
        self.ld_ts = lead.ts.values
        self.df_t = df_t
        lr = np.diff(np.log(self.cl_px), prepend=np.log(self.cl_px[0]))
        dt = np.diff(self.cl_ts, prepend=self.cl_ts[0] - 1).clip(1, None)
        self.r2 = lr ** 2 / dt

    def strike(self, s):
        m = (self.cl_ts >= s - 60) & (self.cl_ts < s)
        return self.cl_px[m].mean() if m.sum() >= 50 else np.nan

    def sigma(self, T, halflife=600):
        k = np.searchsorted(self.cl_recv, T, side="right")
        if k < 30:
            return np.nan
        w = 0.5 ** (np.arange(k)[::-1] / halflife)
        return np.sqrt(np.sum(w * self.r2[:k]) / np.sum(w))

    def fair(self, T, s, e, K=None, sig=None):
        """P(Up) at wall time T ms for window [s, e) seconds."""
        k = np.searchsorted(self.cl_recv, T, side="right")  # obs known
        if k == 0:
            return np.nan
        last_s = self.cl_ts[k - 1]
        last_px = self.cl_px[k - 1]
        # lead adjustment: coinbase px now vs at last_s
        j_now = np.searchsorted(self.ld_recv, T, side="right") - 1
        j_then = np.searchsorted(self.ld_ts, last_s * 1000 + 999, side="right") - 1
        now_px = last_px
        if j_now >= 0 and j_then >= 0:
            now_px = last_px * self.ld_px[j_now] / self.ld_px[j_then]
        K = self.strike(s) if K is None else K
        sig = self.sigma(T) if sig is None else sig
        if not (K == K and sig == sig):
            return np.nan
        now_s = T / 1000.0
        w0 = e - 60
        known = (self.cl_ts[:k] >= w0) & (self.cl_ts[:k] < e)
        sk = self.cl_px[:k][known].sum()
        nk = known.sum()
        n = 60 - nk
        # unknown seconds: from max(w0, last_s+1) .. e-1 ; the stretch last_s..now is partly known via lead
        first_unknown = max(w0, last_s + 1)
        d0 = max(first_unknown - now_s, 0.0) + 1.0
        # variance of sum of n future points starting d0 seconds ahead
        varsum = n * n * (d0 - 1) + n * (n + 1) * (2 * n + 1) / 6.0 if n > 0 else 0.0
        mean = (sk + n * now_px) / 60.0
        sd = now_px * sig * np.sqrt(max(varsum, 0)) / 60.0
        sd = np.sqrt(sd ** 2 + (now_px * 0.3e-4) ** 2)  # small residual (lead/Chainlink mismatch)
        z = (mean - K) / sd
        return float(student.cdf(z, self.df_t))

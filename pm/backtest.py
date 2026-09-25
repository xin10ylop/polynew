"""Strict event-driven taker backtest on the historical trade tape.

Decision at second t uses only BTC data up to t-1 and prints up to t-1.
Execution: FOK-style. Our order (limit L = q - fee - theta_exec) is assumed to arrive at t+delay.
We fill only if a real print for the same side at price <= L occurs in [t+delay, t+delay+win].
Fill price = that print's price (a taker really paid it, so liquidity existed then).
"""
import numpy as np
import pandas as pd

from pm.fees import taker_fee_per_share


def market_arrays(tr):
    """Group trade tape by market -> dict cid -> (ts, side_up, ask) sorted by ts."""
    tr = tr.sort_values(["condition_id", "ts"])
    out = {}
    for cid, g in tr.groupby("condition_id", sort=False):
        out[cid] = (g.ts.values, g.side_up.values.astype(bool), g.ask.values)
    return out


def run(markets, arrays, qfun, delay=1, win=1, theta=0.05, theta_exec=0.02, max_fills=3,
        cooldown=10, t_from=None, t_to=None, px_min=0.02, px_max=0.98, one_side=True):
    """markets: DataFrame with condition_id,start_ts,end_ts,up_won.
    qfun(market_rows, t_grid) -> q_up array shaped like t_grid (data up to t-1).
    t_from/t_to: restrict decisions to seconds-since-start range."""
    fills = []
    for row in markets.itertuples(index=False):
        if row.condition_id not in arrays:
            continue
        ts, sup, ask = arrays[row.condition_id]
        s, e = row.start_ts, row.end_ts
        a = s + (t_from if t_from is not None else 1)
        b = e - (e - s - t_to if t_to is not None else 1)
        grid = np.arange(a, b)
        if len(grid) == 0:
            continue
        q_up = qfun(row, grid)
        nf = 0
        last_fill_t = -10**9
        held_side = None
        # reference prices: last print per side known at t-1
        for side in (True, False):
            pass
        idx_up = np.flatnonzero(sup)
        idx_dn = np.flatnonzero(~sup)
        for k, t in enumerate(grid):
            if nf >= max_fills:
                break
            if t - last_fill_t < cooldown:
                continue
            for side in (True, False):
                if one_side and held_side is not None and held_side != side:
                    continue
                ii = idx_up if side else idx_dn
                # last print of this side strictly before t (known)
                j = np.searchsorted(ts[ii], t, side="left") - 1
                if j < 0 or t - ts[ii][j] > 15:
                    continue
                ref = ask[ii][j]
                q = q_up[k] if side else 1 - q_up[k]
                if not (px_min <= ref <= px_max):
                    continue
                if q - ref - taker_fee_per_share(ref) <= theta:
                    continue
                # execution window
                lo = np.searchsorted(ts[ii], t + delay, side="left")
                hi = np.searchsorted(ts[ii], t + delay + win, side="right")
                if hi <= lo:
                    continue
                cand = ask[ii][lo:hi]
                # limit price: need q - p - fee(p) > theta_exec
                ok = q - cand - taker_fee_per_share(cand) > theta_exec
                if not ok.any():
                    continue
                p = cand[np.argmax(ok)]
                won = row.up_won if side else 1 - row.up_won
                fee = taker_fee_per_share(p)
                fills.append((row.condition_id, t, int(t - s), int(e - t), side, ref, p, q, fee, won, won - p - fee))
                nf += 1
                last_fill_t = t
                held_side = side
                break
    return pd.DataFrame(fills, columns=["cid", "t", "el", "tau", "side_up", "ref", "px", "q", "fee", "won", "pnl"])


def summarize(f, by=None):
    if len(f) == 0:
        return "no fills"
    def agg(g):
        return pd.Series(dict(n=len(g), mk=g.cid.nunique(), px=g.px.mean(), q=g.q.mean(), win=g.won.mean(),
                              pnl=g.pnl.mean(), se=g.pnl.std() / np.sqrt(len(g)), tot=g.pnl.sum()))
    if by is None:
        return agg(f)
    return f.groupby(by, observed=True).apply(agg)

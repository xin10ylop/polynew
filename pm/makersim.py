"""Queue-aware maker simulator on recorded Polymarket books (fill-realistic).

Unified book convention (Up token): bids = Up bids, asks = Up asks.
 - Our BUY Up at L rests on the Up-bid level L.
 - Our BUY Down at M rests on the Up-ask level 1-M (mirror).
Trade events (last_trade_price, reported once on the taker's asset) hitting levels:
 - (Up, SELL, p)   -> Up-bid level p        | (Down, BUY, x) -> Up-bid level 1-x
 - (Up, BUY, x)    -> Up-ask level x        | (Down, SELL, p) -> Up-ask level 1-p
Queue: on placement, queue_ahead = displayed size at the level at the time the order becomes live.
It decreases with volume traded at that level and is capped by the displayed size (cancels ahead).
A trade through a better-than-our level price exhausts our level -> full fill.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TICK = 0.01


@dataclass
class Order:
    side_up: bool          # True: buying Up (rests on Up-bid level); False: buying Down (rests on Up-ask level)
    level: float           # unified Up-book level price
    size: float
    live_at: int           # ms when it becomes live on the book
    cancel_at: int = 10**15
    q_ahead: float = -1.0  # set when it becomes live
    filled: float = 0.0


@dataclass
class Book:
    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None


def replay_events(slug, deltas, books, trades):
    """Chronological event list for one market: (ts_ms, kind, payload)."""
    ev = []
    for (sl, ts, r, b, a) in books:
        if sl == slug:
            ev.append((ts, 0, ({float(p): float(s) for p, s in b}, {float(p): float(s) for p, s in a})))
    d = deltas[deltas.slug == slug]
    for ts, side, p, sz in zip(d.ts.values, d.side.values, d.price.values, d["size"].values):
        ev.append((int(ts), 1, (side == "B", round(float(p), 4), float(sz))))
    t = trades[trades.slug == slug]
    for ts, out, side, p, sz in zip(t.ts.values, t.out.values, t.side.values, t.p.values, t.sz.values):
        # map to unified level + which book side is consumed
        if out == "Up" and side == "SELL":
            ev.append((int(ts), 2, (True, round(p, 4), sz)))
        elif out == "Down" and side == "BUY":
            ev.append((int(ts), 2, (True, round(1 - p, 4), sz)))
        elif out == "Up" and side == "BUY":
            ev.append((int(ts), 2, (False, round(p, 4), sz)))
        elif out == "Down" and side == "SELL":
            ev.append((int(ts), 2, (False, round(1 - p, 4), sz)))
    ev.sort(key=lambda x: (x[0], x[1]))
    return ev


class MakerSim:
    """Drive with a quoting policy: policy(t_ms, book) -> dict(up=(price,size)|None, dn=(price,size)|None)
    where price is the price WE pay for that side (Down price M -> Up-ask level 1-M)."""

    def __init__(self, place_lat=150, cancel_lat=150, decide_every=200, queue_mult=1.0):
        self.queue_mult = queue_mult
        self.n_place = 0
        self.n_cancel = 0
        self.place_lat = place_lat
        self.cancel_lat = cancel_lat
        self.decide_every = decide_every

    def run(self, events, policy, t_start, t_end, max_pos=200.0):
        book = Book()
        orders = []
        fills = []  # (t, side_up, price_paid, size)
        pos = {True: 0.0, False: 0.0}
        next_dec = None
        have_book = False
        for ts, kind, pl in events:
            # decisions on a fixed cadence
            if have_book:
                if next_dec is None:
                    next_dec = max(t_start, ts)
                while next_dec <= ts and next_dec < t_end:
                    self._decide(next_dec, book, orders, policy, pos, max_pos)
                    next_dec += self.decide_every
            # activate orders that became live (queue snapshot at live time)
            for o in orders:
                if o.q_ahead < 0 and ts >= o.live_at and o.cancel_at > o.live_at:
                    lvl = book.bids if o.side_up else book.asks
                    o.q_ahead = lvl.get(o.level, 0.0) * self.queue_mult
            if kind == 0:
                book.bids, book.asks = dict(pl[0]), dict(pl[1])
                have_book = True
            elif kind == 1:
                is_bid, p, sz = pl
                lv = book.bids if is_bid else book.asks
                if sz <= 0:
                    lv.pop(p, None)
                else:
                    lv[p] = sz
                # cancels ahead: queue can't exceed displayed size
                for o in orders:
                    if o.q_ahead >= 0 and o.side_up == is_bid and o.level == p and ts < o.cancel_at:
                        o.q_ahead = min(o.q_ahead, max(sz, 0.0) * self.queue_mult)
            elif kind == 2:
                on_bid, p, sz = pl
                for o in orders:
                    if o.q_ahead < 0 or ts >= o.cancel_at or o.side_up != on_bid:
                        continue
                    rem = o.size - o.filled
                    if rem <= 1e-9:
                        continue
                    through = (p < o.level - 1e-9) if on_bid else (p > o.level + 1e-9)
                    if through:
                        f = rem
                    elif abs(p - o.level) < 1e-9:
                        f = min(rem, max(0.0, sz - o.q_ahead))
                        o.q_ahead = max(0.0, o.q_ahead - sz)
                    else:
                        continue
                    if f > 1e-9:
                        o.filled += f
                        paid = o.level if o.side_up else round(1 - o.level, 4)
                        fills.append((ts, o.side_up, paid, f))
                        pos[o.side_up] += f
            orders = [o for o in orders if ts < o.cancel_at and o.filled < o.size - 1e-9]
            if ts >= t_end + 5000:
                break
        return pd.DataFrame(fills, columns=["t", "side_up", "px", "sz"])

    def _decide(self, t, book, orders, policy, pos, max_pos):
        want = policy(t, book, pos)
        for key, side_up in (("up", True), ("dn", False)):
            w = want.get(key)
            cur = [o for o in orders if o.side_up == side_up and o.cancel_at > t]
            if w is None or pos[side_up] >= max_pos:
                for o in cur:
                    if o.cancel_at > t + self.cancel_lat:
                        self.n_cancel += 1
                    o.cancel_at = min(o.cancel_at, t + self.cancel_lat)
                continue
            price, size = w
            level = round(price, 4) if side_up else round(1 - price, 4)
            # post-only: must not cross
            if side_up and book.best_ask() is not None and level >= book.best_ask() - 1e-9:
                level = round(book.best_ask() - TICK, 4)
            if (not side_up) and book.best_bid() is not None and level <= book.best_bid() + 1e-9:
                level = round(book.best_bid() + TICK, 4)
            keep = [o for o in cur if abs(o.level - level) < 1e-9]
            for o in cur:
                if o not in keep:
                    if o.cancel_at > t + self.cancel_lat:
                        self.n_cancel += 1
                    o.cancel_at = min(o.cancel_at, t + self.cancel_lat)
            if not keep and 0.0 < level < 1.0:
                self.n_place += 1
                orders.append(Order(side_up, level, size, live_at=t + self.place_lat))

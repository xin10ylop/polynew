"""Paper executor: queue-aware simulated fills with virtual latency, driven by live exchange events.
Same fill logic as pm/makersim.py (join back of queue, advance by trades at our level, cap by displayed size,
full fill on trade-through)."""
from dataclasses import dataclass


@dataclass
class POrder:
    side_up: bool
    level: float          # unified Up-book level (Down bid at M rests on Up-ask level 1-M)
    size: float
    live_at: int
    cancel_at: int = 10**15
    q_ahead: float = -1.0
    filled: float = 0.0


class PaperExec:
    def __init__(self, latency_ms, on_fill):
        self.lat = latency_ms
        self.orders = {}      # market -> list[POrder]
        self.on_fill = on_fill

    def working(self, market, side_up, now_ms):
        return [o for o in self.orders.get(market, []) if o.side_up == side_up and o.cancel_at > now_ms
                and o.filled < o.size - 1e-9]

    def place(self, market, side_up, price, size, now_ms):
        level = round(price, 4) if side_up else round(1 - price, 4)
        self.orders.setdefault(market, []).append(POrder(side_up, level, size, live_at=now_ms + self.lat))

    def cancel(self, market, order, now_ms):
        order.cancel_at = min(order.cancel_at, now_ms + self.lat)

    def cancel_all(self, market, now_ms):
        for o in self.orders.get(market, []):
            o.cancel_at = min(o.cancel_at, now_ms + self.lat)

    def on_book(self, market, book, ts):
        for o in self.orders.get(market, []):
            if o.q_ahead < 0 and ts >= o.live_at and o.cancel_at > o.live_at:
                o.q_ahead = book.level(o.side_up, o.level)

    def on_delta(self, market, is_bid, price, size, ts):
        p = round(price, 4)
        for o in self.orders.get(market, []):
            if o.q_ahead >= 0 and o.side_up == is_bid and o.level == p and ts < o.cancel_at:
                o.q_ahead = min(o.q_ahead, max(size, 0.0))

    def on_trade(self, market, on_bid, price, size, ts):
        """on_bid: trade consumed Up-bid levels (Up SELL or Down BUY); else Up-ask levels."""
        for o in self.orders.get(market, []):
            if o.q_ahead < 0 or ts >= o.cancel_at or o.side_up != on_bid:
                continue
            rem = o.size - o.filled
            if rem <= 1e-9:
                continue
            through = (price < o.level - 1e-9) if on_bid else (price > o.level + 1e-9)
            if through:
                f = rem
            elif abs(price - o.level) < 1e-9:
                f = min(rem, max(0.0, size - o.q_ahead))
                o.q_ahead = max(0.0, o.q_ahead - size)
            else:
                continue
            if f > 1e-9:
                o.filled += f
                paid = o.level if o.side_up else round(1 - o.level, 4)
                self.on_fill(market, o.side_up, paid, f, ts)
        self.orders[market] = [o for o in self.orders.get(market, []) if ts < o.cancel_at and o.filled < o.size - 1e-9]

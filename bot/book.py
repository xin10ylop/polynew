"""Order book for one binary market kept in Up-token terms (Down book is the mirror)."""


class UpBook:
    def __init__(self):
        self.bids = {}
        self.asks = {}
        self.ts = 0
        self.ready = False

    def snapshot(self, bids, asks, ts):
        self.bids = {round(float(p), 4): float(s) for p, s in bids}
        self.asks = {round(float(p), 4): float(s) for p, s in asks}
        self.ts = ts
        self.ready = True

    def delta(self, is_bid, price, size, ts):
        book = self.bids if is_bid else self.asks
        p = round(float(price), 4)
        if float(size) <= 0:
            book.pop(p, None)
        else:
            book[p] = float(size)
        self.ts = max(self.ts, ts)

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None

    def level(self, is_bid, price):
        return (self.bids if is_bid else self.asks).get(round(price, 4), 0.0)

"""Reconstruct recorded Polymarket books and live BTC feeds for fill-accurate simulation.

Book: only the Up token is stored; Down book is the mirror (Down ask = 1 - Up bid).
All times in ms. Book sampled on a fixed grid (default 100ms) using server timestamps.
"""
import glob
import gzip
import json

import numpy as np
import pandas as pd

LIVE = "data/live"


def _iter_jsonl(f):
    try:
        with gzip.open(f, "rt") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    return
    except (EOFError, OSError):
        return


def load_rec(files=None):
    files = files or sorted(glob.glob(f"{LIVE}/rec_*_*.jsonl.gz"))
    books, trades, cl, bybit, cb, meta = [], [], [], [], [], {}
    for f in files:
        for d in _iter_jsonl(f):
            s, m, r = d["s"], d["m"], d["r"]
            if s == "book":
                books.append((m["slug"], int(m["ts"]), r, m["bids"], m["asks"]))
            elif s == "trade":
                trades.append((m["slug"], m["out"], float(m["p"]), float(m["sz"]), m["side"], int(m["ts"]), r))
            elif s == "meta":
                meta[m["slug"]] = m
            elif s == "rtds":
                mm = json.loads(m) if isinstance(m, str) else m
                p = mm.get("payload", {})
                if mm.get("topic") == "crypto_prices_chainlink" and "value" in p:
                    cl.append((int(p["timestamp"]), float(p["value"]), r))
                elif "data" in p:  # initial backfill batch
                    for x in p["data"]:
                        cl.append((int(x["timestamp"]), float(x["value"]), r))
            elif s == "bybit":
                mm = json.loads(m)
                if mm.get("topic", "").startswith("publicTrade"):
                    for x in mm.get("data", []):
                        bybit.append((int(x["T"]), float(x["p"]), float(x["v"]), 1 if x["S"] == "Buy" else -1, r))
            elif s == "cb":
                mm = json.loads(m)
                if mm.get("type") == "ticker":
                    t = pd.Timestamp(mm["time"]).value // 10**6
                    cb.append((t, float(mm["price"]), float(mm.get("best_bid") or 0), float(mm.get("best_ask") or 0), r))
    out = dict(
        books=books,
        trades=pd.DataFrame(trades, columns=["slug", "out", "p", "sz", "side", "ts", "recv"]),
        cl=pd.DataFrame(cl, columns=["ts", "px", "recv"]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True),
        bybit=pd.DataFrame(bybit, columns=["ts", "px", "sz", "sgn", "recv"]).sort_values("ts").reset_index(drop=True),
        cb=pd.DataFrame(cb, columns=["ts", "px", "bid", "ask", "recv"]).sort_values("ts").reset_index(drop=True),
        meta=meta,
    )
    return out


def load_deltas(files=None):
    files = files or sorted(glob.glob(f"{LIVE}/book_*_*.csv.gz"))
    parts = []
    for f in files:
        try:
            df = pd.read_csv(f, header=None, names=["recv", "ts", "slug", "side", "price", "size", "bb", "ba"],
                             on_bad_lines="skip", engine="c")
        except (EOFError, OSError, pd.errors.ParserError):
            # truncated gzip tail of a live file: read what we can
            import io
            raw = b""
            try:
                with gzip.open(f, "rb") as fh:
                    while True:
                        chunk = fh.read(1 << 20)
                        if not chunk:
                            break
                        raw += chunk
            except (EOFError, OSError):
                pass
            raw = raw[: raw.rfind(b"\n") + 1]
            df = pd.read_csv(io.BytesIO(raw), header=None,
                             names=["recv", "ts", "slug", "side", "price", "size", "bb", "ba"], on_bad_lines="skip")
        parts.append(df)
    df = pd.concat(parts, ignore_index=True)
    df = df.dropna(subset=["ts", "price", "size"])
    df["ts"] = df.ts.astype(np.int64)
    return df


def build_book_grid(slug, deltas, books, step=100, depth=5):
    """Replay snapshots + deltas for one slug; sample top-of-book on a server-time grid.

    Returns DataFrame: t, then bid_px_i/bid_sz_i, ask_px_i/ask_sz_i for i in 1..depth (Up token)."""
    ev = []
    for (sl, ts, r, bids, asks) in books:
        if sl == slug:
            ev.append((ts, 0, bids, asks))
    d = deltas[deltas.slug == slug]
    for ts, side, p, sz in zip(d.ts.values, d.side.values, d.price.values, d["size"].values):
        ev.append((int(ts), 1, side, (float(p), float(sz))))
    if not ev:
        return None
    ev.sort(key=lambda x: (x[0], x[1]))
    bids, asks = {}, {}
    t0 = ev[0][0] // step * step + step
    rows = []
    grid_t = t0
    i = 0
    n = len(ev)
    started = False
    while i < n:
        ts = ev[i][0]
        while grid_t <= ts and started:
            rows.append(_snap(grid_t, bids, asks, depth))
            grid_t += step
        if not started:
            grid_t = ts // step * step + step
        kind = ev[i][1]
        if kind == 0:
            bids = {float(p): float(s) for p, s in ev[i][2]}
            asks = {float(p): float(s) for p, s in ev[i][3]}
            started = True
        elif started:
            side, (p, sz) = ev[i][2], ev[i][3]
            book = bids if side == "B" else asks
            if sz <= 0:
                book.pop(p, None)
            else:
                book[p] = sz
        i += 1
    rows.append(_snap(grid_t, bids, asks, depth))
    cols = ["t"] + [f"b{i}" for i in range(1, depth + 1)] + [f"bs{i}" for i in range(1, depth + 1)] + \
           [f"a{i}" for i in range(1, depth + 1)] + [f"as{i}" for i in range(1, depth + 1)]
    return pd.DataFrame(rows, columns=cols)


def _snap(t, bids, asks, depth):
    bp = sorted(bids, reverse=True)[:depth]
    ap = sorted(asks)[:depth]
    bp += [np.nan] * (depth - len(bp))
    ap += [np.nan] * (depth - len(ap))
    return [t] + bp + [bids.get(p, 0.0) if p == p else 0.0 for p in bp] + ap + [asks.get(p, 0.0) if p == p else 0.0 for p in ap]


def side_asks(row, side_up, depth=5):
    """Executable asks (price, size) for buying Up (from Up asks) or Down (mirror of Up bids)."""
    if side_up:
        return [(row[f"a{i}"], row[f"as{i}"]) for i in range(1, depth + 1) if row[f"a{i}"] == row[f"a{i}"]]
    return [(round(1 - row[f"b{i}"], 4), row[f"bs{i}"]) for i in range(1, depth + 1) if row[f"b{i}"] == row[f"b{i}"]]


def fill_fok(asks, limit, qty):
    """Walk asks <= limit for qty shares. Returns (filled_qty, avg_px)."""
    got, cost = 0.0, 0.0
    for p, s in asks:
        if p > limit + 1e-9 or got >= qty:
            break
        take = min(s, qty - got)
        got += take
        cost += take * p
    return got, (cost / got if got > 0 else np.nan)

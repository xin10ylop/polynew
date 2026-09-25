"""Coinbase BTC-USD trades (microsecond timestamps) for the windows in a backtest output -> data/cb_ms/<day>.parquet
(ms float, px, qty). Uses the public Advanced Trade market ticker endpoint (start/end in seconds, max 1000 trades).

  python -m pm.cb_ms data/adapt_5m_ldelay.parquet"""
import concurrent.futures as cf
import datetime as dt
import os
import sys
import time

import pandas as pd
import requests

URL = "https://api.coinbase.com/api/v3/brokerage/market/products/BTC-USD/ticker"
S = requests.Session()


def _get(a, b):
    for k in range(8):
        try:
            r = S.get(URL, params={"limit": 1000, "start": a, "end": b}, timeout=30)
            if r.status_code == 429:
                time.sleep(1 + k)
                continue
            r.raise_for_status()
            return r.json().get("trades") or []
        except requests.RequestException:
            time.sleep(1 + k)
    raise RuntimeError(f"coinbase fetch failed {a}-{b}")


def fetch(a, b):
    t = _get(a, b)
    if len(t) >= 1000 and b - a > 1:
        m = (a + b) // 2
        return fetch(a, m) + fetch(m, b)
    return [(dt.datetime.fromisoformat(x["time"].replace("Z", "+00:00")).timestamp() * 1000, float(x["price"]),
             float(x["size"])) for x in t]


def main(path):
    o = pd.read_parquet(path)
    sts = sorted(set(int(s) for s in o.st))
    iv = []
    for s in sts:
        a, b = s - 90, s + 305
        if iv and a <= iv[-1][1]:
            iv[-1][1] = max(iv[-1][1], b)
        else:
            iv.append([a, b])
    chunks = [(a, min(a + 30, b)) for a0, b in iv for a in range(a0, b, 30)]
    print("intervals", len(iv), "chunks", len(chunks), flush=True)
    rows = []
    with cf.ThreadPoolExecutor(6) as ex:
        for i, r in enumerate(ex.map(lambda c: fetch(*c), chunks)):
            rows += r
            if i % 500 == 0:
                print(i, len(rows), flush=True)
    df = pd.DataFrame(rows, columns=["ms", "px", "qty"]).drop_duplicates().sort_values("ms")
    df["day"] = pd.to_datetime(df.ms, unit="ms").dt.strftime("%Y-%m-%d")
    os.makedirs("data/cb_ms", exist_ok=True)
    for d, g in df.groupby("day"):
        g[["ms", "px", "qty"]].reset_index(drop=True).to_parquet(f"data/cb_ms/{d}.parquet")
        print(d, len(g), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])

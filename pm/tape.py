"""Polymarket data-api trade tape for a list of condition ids -> data/slow/tape_<name>.parquet
(cid, ts, price, size, side, outcome). data-api timestamps lag the match by ~2 s (irrelevant at minute scale).

  python -m pm.tape hit data/slow/hit_fills.parquet"""
import concurrent.futures as cf
import sys
import time

import pandas as pd
import requests

URL = "https://data-api.polymarket.com/trades"
S = requests.Session()


def fetch(cid):
    out, off = [], 0
    while off <= 50_000:
        for k in range(5):
            try:
                r = S.get(URL, params={"market": cid, "limit": 500, "offset": off}, timeout=30)
                if r.status_code == 429:
                    time.sleep(2 + 2 * k)
                    continue
                d = r.json() if r.status_code == 200 else []
                break
            except (requests.RequestException, ValueError):
                time.sleep(1 + k)
                d = []
        if not d:
            break
        out += [(cid, int(t["timestamp"]), float(t["price"]), float(t["size"]), t.get("side"), t.get("outcome"))
                for t in d]
        if len(d) < 500:
            break
        off += 500
    return out


def main(name, path):
    cids = pd.read_parquet(path).cid.unique()
    rows = []
    with cf.ThreadPoolExecutor(6) as ex:
        for i, r in enumerate(ex.map(fetch, cids)):
            rows += r
            if i % 100 == 0:
                print(i, len(rows), flush=True)
    T = pd.DataFrame(rows, columns=["cid", "ts", "price", "size", "side", "outcome"]).drop_duplicates()
    T.to_parquet(f"data/slow/tape_{name}.parquet")
    print("trades", len(T), "markets", T.cid.nunique())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

"""Poll Kalshi KXBTC15M order books (current + next window) ~2x/s; write gzip JSONL."""
import gzip
import json
import os
import time

import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
OUT = "data/live"


def ticker_for(open_ts):
    # KXBTC15M-26SEP251015-15 : close time in US/Eastern, suffix = close minute
    import datetime as dt
    from zoneinfo import ZoneInfo
    close = dt.datetime.fromtimestamp(open_ts + 900, tz=ZoneInfo("America/New_York"))
    return f"KXBTC15M-{close:%y%b%d%H%M}".upper() + f"-{close:%M}"


def main():
    os.makedirs(OUT, exist_ok=True)
    s = requests.Session()
    f = None
    hour = None
    while True:
        now = time.time()
        h = int(now // 3600)
        if h != hour:
            if f:
                f.close()
            f = gzip.open(f"{OUT}/kalshi_{h}_{os.getpid()}.jsonl.gz", "at", compresslevel=3)
            hour = h
        cur = int(now // 900 * 900)
        tk = ticker_for(cur)
        t0 = time.time()
        try:
            r = s.get(f"{API}/markets/{tk}/orderbook", params={"depth": 10}, timeout=5)
            t1 = time.time()
            if r.status_code == 200:
                f.write(json.dumps({"r0": int(t0 * 1000), "r1": int(t1 * 1000), "tk": tk, "open": cur,
                                    "ob": r.json().get("orderbook_fp") or r.json().get("orderbook")}) + "\n")
            elif r.status_code == 429:
                time.sleep(1)
        except Exception as ex:  # noqa: BLE001
            print("err", ex, flush=True)
        f.flush()
        time.sleep(max(0.0, 0.5 - (time.time() - t0)))


if __name__ == "__main__":
    main()

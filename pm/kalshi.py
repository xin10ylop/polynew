"""Kalshi KXBTC15M public market data (no auth needed for REST market data)."""
import sys
import time

import pandas as pd
import requests

API = "https://api.elections.kalshi.com/trade-api/v2"


def get(path, params, tries=6):
    for a in range(tries):
        r = requests.get(API + path, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(1 + a)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("kalshi rate limited")


def settled_markets(series="KXBTC15M", min_close=None):
    out, cursor = [], None
    while True:
        p = dict(series_ticker=series, status="settled", limit=1000)
        if cursor:
            p["cursor"] = cursor
        if min_close:
            p["min_close_ts"] = min_close
        d = get("/markets", p)
        out += d["markets"]
        cursor = d.get("cursor")
        if not cursor or not d["markets"]:
            break
    df = pd.DataFrame([dict(ticker=m["ticker"], open_ts=int(pd.Timestamp(m["open_time"]).timestamp()),
                            close_ts=int(pd.Timestamp(m["close_time"]).timestamp()), result=m.get("result"),
                            strike=m.get("floor_strike"), final=m.get("expiration_value"),
                            volume=float(m.get("volume_fp") or 0)) for m in out])
    return df.sort_values("open_ts").reset_index(drop=True)


def trades(ticker, min_ts=None, max_ts=None):
    out, cursor = [], None
    while True:
        p = dict(ticker=ticker, limit=1000)
        if cursor:
            p["cursor"] = cursor
        if min_ts:
            p["min_ts"] = min_ts
        if max_ts:
            p["max_ts"] = max_ts
        d = get("/markets/trades", p)
        out += d.get("trades", [])
        cursor = d.get("cursor")
        if not cursor or not d.get("trades"):
            break
    return out


if __name__ == "__main__":
    since = int(pd.Timestamp(sys.argv[1], tz="UTC").timestamp())
    df = settled_markets(min_close=since)
    df.to_parquet("data/kalshi_15m.parquet")
    print(len(df), df.head(2).to_string(), df.tail(2).to_string())

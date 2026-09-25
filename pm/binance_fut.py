"""Binance USD-M futures BTCUSDT aggTrades -> 1s bars (last, vwap, buy/sell volume)."""
import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip"


def fetch_day(day, sym="BTCUSDT"):
    for attempt in range(4):
        try:
            r = requests.get(URL.format(sym=sym, day=day), timeout=300)
            r.raise_for_status()
            break
        except Exception:  # noqa: BLE001
            if attempt == 3:
                raise
    z = zipfile.ZipFile(io.BytesIO(r.content))
    f = z.open(z.namelist()[0])
    head = f.read(200).decode()
    f = z.open(z.namelist()[0])
    has_header = head.startswith("agg")
    df = pd.read_csv(f, header=0 if has_header else None,
                     names=["id", "price", "qty", "first", "last", "time", "buyer_maker"],
                     usecols=[1, 2, 5, 6])
    t = df.time.values.astype(np.int64)
    t = np.where(t > 10**14, t // 1000, t)  # to ms
    sec = t // 1000
    px = df.price.values.astype(np.float64)
    q = df.qty.values.astype(np.float64)
    bm = df.buyer_maker.astype(str).str.lower().eq("true").values
    g = pd.DataFrame({"sec": sec, "px": px, "q": q, "pq": px * q, "bq": np.where(bm, 0, q), "sq": np.where(bm, q, 0), "ms": t})
    agg = g.groupby("sec").agg(last=("px", "last"), vol=("q", "sum"), pq=("pq", "sum"), bq=("bq", "sum"), sq=("sq", "sum"), n=("px", "size"))
    agg["vwap"] = agg.pq / agg.vol
    return agg.drop(columns="pq")


def build(start, end, sym="BTCUSDT"):
    days = [d.strftime("%Y-%m-%d") for d in pd.date_range(start, end, freq="D")]
    with ThreadPoolExecutor(4) as ex:
        parts = list(ex.map(lambda d: fetch_day(d, sym), days))
    agg = pd.concat(parts).sort_index()
    agg = agg[~agg.index.duplicated()]
    full = np.arange(agg.index.min(), agg.index.max() + 1)
    agg = agg.reindex(full)
    agg["last"] = agg["last"].ffill()
    agg["vwap"] = agg["vwap"].fillna(agg["last"])
    for c in ["vol", "bq", "sq", "n"]:
        agg[c] = agg[c].fillna(0)
    out = pd.DataFrame({"ts": agg.index.values.astype(np.int64), "o": agg["last"].values, "h": agg["last"].values,
                        "l": agg["last"].values, "c": agg["last"].values, "v": agg.vol.values.astype(np.float32),
                        "n": agg.n.values.astype(np.int32), "tbv": agg.bq.values.astype(np.float32),
                        "vwap": agg.vwap.values})
    return out


if __name__ == "__main__":
    df = build(sys.argv[1], sys.argv[2])
    df.to_parquet("data/btcusdt_fut_1s.parquet")
    print(len(df), df.ts.min(), df.ts.max())

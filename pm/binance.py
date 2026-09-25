"""Download Binance BTCUSDT 1-second klines from data.binance.vision daily archives."""
import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

URL = "https://data.binance.vision/data/spot/daily/klines/{sym}/1s/{sym}-1s-{day}.zip"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qvol", "trades", "tb_base", "tb_quote", "ign"]


def fetch_day(day, sym="BTCUSDT"):
    r = requests.get(URL.format(sym=sym, day=day), timeout=120)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    df = pd.read_csv(z.open(z.namelist()[0]), header=None, names=COLS)
    t = df.open_time.astype(np.int64)
    # archives switched to microseconds in 2025
    t = np.where(t > 10**14, t // 1_000_000, t // 1000)
    out = pd.DataFrame({
        "ts": t.astype(np.int64),
        "o": df.open.astype(np.float64), "h": df.high.astype(np.float64),
        "l": df.low.astype(np.float64), "c": df.close.astype(np.float64),
        "v": df.volume.astype(np.float32), "n": df.trades.astype(np.int32),
        "tbv": df.tb_base.astype(np.float32),
    })
    return out


def fetch_range(start, end, sym="BTCUSDT"):
    days = [d.strftime("%Y-%m-%d") for d in pd.date_range(start, end, freq="D")]
    with ThreadPoolExecutor(6) as ex:
        parts = list(ex.map(lambda d: fetch_day(d, sym), days))
    df = pd.concat(parts).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    return df


if __name__ == "__main__":
    start, end = sys.argv[1], sys.argv[2]
    sym = sys.argv[3] if len(sys.argv) > 3 else "BTCUSDT"
    df = fetch_range(start, end, sym)
    out = f"data/{sym.lower()}_1s.parquet"
    df.to_parquet(out)
    print(len(df), df.ts.min(), df.ts.max(), "->", out)
    full = np.arange(df.ts.min(), df.ts.max() + 1)
    print("missing seconds:", len(full) - len(df))

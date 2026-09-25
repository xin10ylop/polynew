"""Binance BTCUSDT spot 1-minute klines (the resolution source of the slower Polymarket Bitcoin markets).

  python -m pm.klines 2025-06 2026-09   -> data/klines_1m.parquet (ms open time, o, h, l, c, v)"""
import io
import sys
import zipfile

import numpy as np
import pandas as pd
import requests

M = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{m}.zip"
D = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{d}.zip"


def grab(url):
    r = requests.get(url, timeout=120)
    if r.status_code != 200:
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    df = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[0, 1, 2, 3, 4, 5])
    df = df[pd.to_numeric(df[0], errors="coerce").notna()].astype(float)
    t = df[0].values.astype(np.int64)
    t = np.where(t > 10**14, t // 1000, t)  # 2025+ files are in microseconds
    return pd.DataFrame({"ms": t, "o": df[1].values, "h": df[2].values, "l": df[3].values, "c": df[4].values,
                         "v": df[5].values})


def main(a, b):
    parts = []
    for m in pd.period_range(a, b, freq="M"):
        x = grab(M.format(m=str(m)))
        if x is None:  # current month: daily files
            for d in pd.date_range(m.start_time, m.end_time, freq="D"):
                y = grab(D.format(d=d.strftime("%Y-%m-%d")))
                if y is not None:
                    parts.append(y)
        else:
            parts.append(x)
        print(m, sum(len(p) for p in parts), flush=True)
    K = pd.concat(parts).drop_duplicates("ms").sort_values("ms").reset_index(drop=True)
    K.to_parquet("data/klines_1m.parquet")
    print(len(K), pd.to_datetime(K.ms.min(), unit="ms"), pd.to_datetime(K.ms.max(), unit="ms"))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

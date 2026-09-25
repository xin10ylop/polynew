"""Binance USD-M BTCUSDT aggTrades at millisecond resolution for selected days -> data/fut_ms/<day>.parquet (ms, px, qty, buyer_maker)."""
import io, os, sys, zipfile
import numpy as np, pandas as pd, requests
URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{day}.zip"
os.makedirs("data/fut_ms", exist_ok=True)
for day in sys.argv[1:]:
    out = f"data/fut_ms/{day}.parquet"
    if os.path.exists(out):
        continue
    r = requests.get(URL.format(day=day), timeout=300); r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content)); f = z.open(z.namelist()[0])
    head = f.read(100).decode(); f = z.open(z.namelist()[0])
    df = pd.read_csv(f, header=0 if head.startswith("agg") else None, names=["id", "price", "qty", "first", "last", "time", "bm"], usecols=[1, 2, 5, 6])
    t = df.time.values.astype(np.int64); t = np.where(t > 10**14, t // 1000, t)
    pd.DataFrame({"ms": t, "px": df.price.values.astype(np.float64), "qty": df.qty.values.astype(np.float32),
                  "bm": df.bm.astype(str).str.lower().eq("true").values}).to_parquet(out)
    print(day, len(df), flush=True)

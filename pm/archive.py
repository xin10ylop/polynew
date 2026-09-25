"""Extract BTC up/down (5m/15m) order-book events from the free PendulumFlow Polymarket archive (v3).

Hourly parquet files are sorted by event_type then market, so we fetch only row groups whose market range
covers our condition ids (HTTP range reads) and filter in Arrow. Output per hour: data/arch/<hour>.parquet
columns: ev, ts(ms server), tr(us received), cid, asset, price, size, side, bb, ba, book(json for snapshots)."""
import json
import os
import sys
import time

import fsspec
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

BASE = "https://archive.pendulumflow.com/v3/{d}/{h:02d}/{d}T{h:02d}.parquet"
EVENTS = ("price_change", "book", "last_trade_price", "tick_size_change")
COLS = ["event_type", "timestamp_received", "timestamp", "market", "asset_id", "price", "size", "side",
        "best_bid", "best_ask", "bids", "asks"]


def targets(hour_ts):
    m5 = pd.read_parquet("data/markets_5m.parquet")
    m15 = pd.read_parquet("data/markets_15m.parquet")
    a = m5[(m5.start_ts >= hour_ts - 300) & (m5.start_ts < hour_ts + 3600 + 600)]
    b = m15[(m15.start_ts >= hour_ts - 900) & (m15.start_ts < hour_ts + 3600 + 900)]
    return {bytes.fromhex(c[2:]): c for c in pd.concat([a, b]).condition_id}


def up_tokens():
    m5 = pd.read_parquet("data/markets_5m.parquet")
    m15 = pd.read_parquet("data/markets_15m.parquet")
    return set(m5.up_token.dropna()) | set(m15.up_token.dropna())


def extract_hour(hour_ts, out_dir="data/arch"):
    os.makedirs(out_dir, exist_ok=True)
    d = pd.Timestamp(hour_ts, unit="s", tz="UTC")
    out = f"{out_dir}/{d:%Y-%m-%dT%H}.parquet"
    if os.path.exists(out):
        return out, 0
    url = BASE.format(d=f"{d:%Y-%m-%d}", h=d.hour)
    tg = targets(hour_ts)
    tgt_arr = pa.array(list(tg.keys()), type=pa.binary())
    fs = fsspec.filesystem("http")
    parts = []
    with fs.open(url, "rb", block_size=2 ** 22) as f:
        pf = pq.ParquetFile(f)
        md = pf.metadata
        cols = [md.schema.column(i).path for i in range(md.num_columns)]
        ie, im = cols.index("event_type"), cols.index("market")
        sel = []
        for g in range(md.num_row_groups):
            rg = md.row_group(g)
            se, sm = rg.column(ie).statistics, rg.column(im).statistics
            if se.min not in EVENTS:
                continue
            if any(sm.min <= c <= sm.max for c in tg):
                sel.append(g)
        for g in sel:
            t = pf.read_row_group(g, columns=COLS)
            t = t.filter(pc.is_in(t.column("market"), value_set=tgt_arr))
            if t.num_rows == 0:
                continue
            ev = t.column("event_type").to_pylist()
            df = pd.DataFrame({
                "ev": pd.Categorical(ev, categories=list(EVENTS)).codes.astype(np.int8),
                "ts": pc.cast(t.column("timestamp"), pa.int64()).to_numpy(zero_copy_only=False),
                "tr": pc.cast(t.column("timestamp_received"), pa.int64()).to_numpy(zero_copy_only=False),
                "cid": ["0x" + x.hex() for x in t.column("market").to_pylist()],
                "asset": [str(int.from_bytes(x, "big")) if isinstance(x, bytes) else str(x) for x in t.column("asset_id").to_pylist()],
                "price": pc.cast(t.column("price"), pa.float64()).to_numpy(zero_copy_only=False),
                "size": pc.cast(t.column("size"), pa.float64()).to_numpy(zero_copy_only=False),
                "side": t.column("side").to_pylist(),
                "bb": pc.cast(t.column("best_bid"), pa.float64()).to_numpy(zero_copy_only=False),
                "ba": pc.cast(t.column("best_ask"), pa.float64()).to_numpy(zero_copy_only=False),
            })
            isbook = df.ev.values == 1
            if isbook.any():
                bids = t.column("bids").to_pylist()
                asks = t.column("asks").to_pylist()
                df["book"] = [json.dumps({"b": [[float(x["price"]), float(x["size"])] for x in bb],
                                          "a": [[float(x["price"]), float(x["size"])] for x in aa]}) if k else None
                              for k, bb, aa in zip(isbook, bids, asks)]
            else:
                df["book"] = None
            parts.append(df)
    res = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(res):
        ups = up_tokens()
        keep = (res.ev.values == 2) | res.asset.isin(ups).values   # trades: both tokens; book/deltas: Up only
        res = res[keep].reset_index(drop=True)
    res.to_parquet(out)
    return out, len(res)


if __name__ == "__main__":
    hours = [int(pd.Timestamp(h, tz="UTC").timestamp()) for h in sys.argv[1:]]
    for h in hours:
        t = time.time()
        try:
            out, n = extract_hour(h)
            print(pd.Timestamp(h, unit="s"), n, "rows", round(time.time() - t), "s", flush=True)
        except Exception as ex:  # noqa: BLE001
            print(pd.Timestamp(h, unit="s"), "ERROR", repr(ex)[:200], flush=True)

"""Extract full L2 events (both tokens) for chosen condition ids from the PendulumFlow archive.

  python -m pm.archive_cids data/slow/hit_signals_audited.parquet data/arch_hit
For every signal hour that the archive has, keeps book snapshots, price changes and trades of that hour's signal
markets. Output: <out_dir>/<YYYY-MM-DDTHH>.parquet (same columns as pm.archive)."""
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

from pm.archive import BASE, COLS, EVENTS


def extract(hour_ts, cids, out_dir):
    d = pd.Timestamp(hour_ts, unit="s", tz="UTC")
    out = f"{out_dir}/{d:%Y-%m-%dT%H}.parquet"
    if os.path.exists(out):
        return 0
    tg = {bytes.fromhex(c[2:]): c for c in cids}
    tgt_arr = pa.array(list(tg.keys()), type=pa.binary())
    fs = fsspec.filesystem("http")
    parts = []
    with fs.open(BASE.format(d=f"{d:%Y-%m-%d}", h=d.hour), "rb", block_size=2 ** 22) as f:
        pf = pq.ParquetFile(f)
        md = pf.metadata
        cols = [md.schema.column(i).path for i in range(md.num_columns)]
        ie, im = cols.index("event_type"), cols.index("market")
        for g in range(md.num_row_groups):
            rg = md.row_group(g)
            se, sm = rg.column(ie).statistics, rg.column(im).statistics
            if se.min not in EVENTS or not any(sm.min <= c <= sm.max for c in tg):
                continue
            t = pf.read_row_group(g, columns=COLS)
            t = t.filter(pc.is_in(t.column("market"), value_set=tgt_arr))
            if t.num_rows == 0:
                continue
            df = pd.DataFrame({
                "ev": pd.Categorical(t.column("event_type").to_pylist(), categories=list(EVENTS)).codes.astype(np.int8),
                "ts": pc.cast(t.column("timestamp"), pa.int64()).to_numpy(zero_copy_only=False),
                "cid": ["0x" + x.hex() for x in t.column("market").to_pylist()],
                "asset": [str(int.from_bytes(x, "big")) if isinstance(x, bytes) else str(x) for x in t.column("asset_id").to_pylist()],
                "price": pc.cast(t.column("price"), pa.float64()).to_numpy(zero_copy_only=False),
                "size": pc.cast(t.column("size"), pa.float64()).to_numpy(zero_copy_only=False),
                "side": t.column("side").to_pylist(),
                "bb": pc.cast(t.column("best_bid"), pa.float64()).to_numpy(zero_copy_only=False),
                "ba": pc.cast(t.column("best_ask"), pa.float64()).to_numpy(zero_copy_only=False),
            })
            isbook = df.ev.values == 1
            bids, asks = t.column("bids").to_pylist(), t.column("asks").to_pylist()
            df["book"] = [json.dumps({"b": [[float(x["price"]), float(x["size"])] for x in bb],
                                      "a": [[float(x["price"]), float(x["size"])] for x in aa]}) if k else None
                          for k, bb, aa in zip(isbook, bids, asks)]
            parts.append(df)
    res = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    os.makedirs(out_dir, exist_ok=True)
    res.to_parquet(out)
    return len(res)


def main(sig_path, out_dir):
    C = pd.read_parquet(sig_path)
    C["hour"] = C.t // 3600 * 3600
    for h, g in C.groupby("hour"):
        t0 = time.time()
        try:
            n = extract(int(h), list(g.cid.unique()), out_dir)
            print(pd.Timestamp(h, unit="s"), "rows", n, round(time.time() - t0), "s", flush=True)
        except Exception as ex:  # noqa: BLE001 - hour not in archive
            print(pd.Timestamp(h, unit="s"), "skip", repr(ex)[:80], flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

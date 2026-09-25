"""1-minute midpoint history for the final hours of each market of a slow series (for near-expiry tests).

  python -m pm.slow_tail above_daily 3   -> data/slow/px1m_<series>.parquet (m, t, p) for [end - 3h, end + 60s]"""
import concurrent.futures as cf
import sys

import numpy as np
import pandas as pd

from pm.slowmkts import history


def main(name, hours):
    E = pd.read_parquet(f"data/slow/events_{name}.parquet")
    mi, tt, pp = [], [], []
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(history, r.tok_yes, int(r.end - hours * 3600), int(r.end) + 60, 1): k
                for k, r in enumerate(E.itertuples())}
        for i, f in enumerate(cf.as_completed(futs)):
            t, p = f.result()
            mi.append(np.full(len(t), futs[f], dtype=np.int32))
            tt.append(t)
            pp.append(p)
            if i % 1000 == 0:
                print(i, flush=True)
    pd.DataFrame({"m": np.concatenate(mi), "t": np.concatenate(tt), "p": np.concatenate(pp)}).to_parquet(
        f"data/slow/px1m_{name}.parquet")
    print("points", sum(len(x) for x in tt))


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]))

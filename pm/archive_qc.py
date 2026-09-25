"""Record archive hour row counts (manifest) so backtests can skip partial-capture hours."""
import json
import os
import sys

import pandas as pd
import requests

OUT = "data/arch/_qc.json"


def check(hours):
    qc = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for f in hours:
        if f in qc:
            continue
        d = pd.Timestamp(f)
        try:
            m = requests.get(f"https://archive.pendulumflow.com/v3/{d:%Y-%m-%d}/{d:%H}/manifest.json", timeout=30).json()
            qc[f] = int(m.get("row_count", 0))
        except Exception:  # noqa: BLE001
            qc[f] = -1
    json.dump(qc, open(OUT, "w"))
    return qc


if __name__ == "__main__":
    import glob
    hours = [p.split("/")[-1][:13] for p in glob.glob("data/arch/20*.parquet")]
    qc = check(hours)
    bad = {k: v for k, v in qc.items() if v < 30_000_000}
    print(len(qc), "hours checked; partial:", bad)

"""Collect closed events of the slower Polymarket Bitcoin series (outcomes + minute price history per market).

  python -m pm.slowmkts            -> data/slow/events_<series>.parquet, data/slow/px_<series>.parquet

Series: daily 'above' strike ladder (45), weekly 'hit' (10151), monthly 'hit' (10016), daily up/down (41),
4h up/down (10331), hourly up/down (10114). Prices come from CLOB /prices-history at 1-minute fidelity."""
import concurrent.futures as cf
import json
import os
import time

import numpy as np
import pandas as pd
import requests

GAMMA = "https://gamma-api.polymarket.com/events"
HIST = "https://clob.polymarket.com/prices-history"
SERIES = {"above_daily": 45, "hit_weekly": 10151, "hit_monthly": 10016, "updown_daily": 41, "updown_4h": 10331,
          "updown_1h": 10114}
S = requests.Session()


def get(url, params):
    for k in range(6):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + 2 * k)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(1 + k)
    return None


def events(series_id):
    out, off = [], 0
    while True:
        page = get(GAMMA, {"series_id": series_id, "closed": "true", "limit": 100, "offset": off})
        if not page:
            break
        out += page
        off += len(page)
        if len(page) < 100:
            break
    return out


def history(tok, a, b, fid):
    ts, ps = [], []
    for s in range(a, b, 6 * 86400):  # chunked: long ranges get downsampled otherwise
        h = get(HIST, {"market": tok, "startTs": s, "endTs": min(b, s + 6 * 86400), "fidelity": fid})
        for p in (h or {}).get("history", []):
            ts.append(p["t"])
            ps.append(p["p"])
    return np.array(ts, dtype=np.int64), np.array(ps, dtype=np.float32)


def main():
    os.makedirs("data/slow", exist_ok=True)
    for name, sid in SERIES.items():
        evs = events(sid)
        rows = []
        for e in evs:
            for m in e.get("markets", []):
                try:
                    toks = json.loads(m["clobTokenIds"])
                    prices = json.loads(m.get("outcomePrices") or "[]")
                except (KeyError, ValueError):
                    continue
                if not (m.get("endDate") or e.get("endDate")):
                    continue
                rows.append(dict(event=e["slug"], cid=m["conditionId"], q=m["question"], tok_yes=toks[0],
                                 start=pd.Timestamp(m.get("startDate") or e.get("startDate")).timestamp(),
                                 end=pd.Timestamp(m.get("endDate") or e.get("endDate")).timestamp(), yes_final=float(prices[0]) if prices else None,
                                 volume=float(m.get("volume") or 0), desc=m.get("description", "")[:600]))
        E = pd.DataFrame(rows)
        E.to_parquet(f"data/slow/events_{name}.parquet")
        print(name, "events", len(evs), "markets", len(E), flush=True)
        fid = 1 if name in ("updown_1h", "updown_4h") else 5  # minutes; slow markets don't need 1-minute points
        mi, tt, pp = [], [], []
        with cf.ThreadPoolExecutor(6) as ex:
            futs = {ex.submit(history, r.tok_yes, int(r.start), int(r.end) + 60, fid): k for k, r in enumerate(E.itertuples())}
            for i, f in enumerate(cf.as_completed(futs)):
                t, p = f.result()
                mi.append(np.full(len(t), futs[f], dtype=np.int32))
                tt.append(t)
                pp.append(p)
                if i % 500 == 0:
                    print(name, i, sum(len(x) for x in tt), flush=True)
        pd.DataFrame({"m": np.concatenate(mi), "t": np.concatenate(tt), "p": np.concatenate(pp)}).to_parquet(
            f"data/slow/px_{name}.parquet")  # m = row index into events_<name>.parquet
        print(name, "price points", sum(len(x) for x in tt), flush=True)


if __name__ == "__main__":
    main()

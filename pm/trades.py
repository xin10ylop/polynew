"""Download taker trade tapes for BTC up/down markets from the Polymarket data-api."""
import asyncio
import os
import sys
import time

import aiohttp
import numpy as np
import pandas as pd

API = "https://data-api.polymarket.com/trades"


async def fetch_market(session, cid, sem):
    async with sem:
        for attempt in range(7):
            try:
                params = dict(market=cid, limit=10000, offset=0, takerOnly="true")
                async with session.get(API, params=params, timeout=aiohttp.ClientTimeout(total=120)) as r:
                    if r.status in (429, 500, 502, 503, 504):
                        await asyncio.sleep(min(60, 2 ** attempt))
                        continue
                    r.raise_for_status()
                    d = await r.json()
                    return cid, d
            except Exception as ex:  # noqa: BLE001
                if attempt == 6:
                    print("failed", cid, ex, flush=True)
                    return cid, None
                await asyncio.sleep(min(60, 2 ** attempt))
    return cid, None


def compact(cid, d):
    if not d:
        return None
    df = pd.DataFrame(d)
    return pd.DataFrame({
        "condition_id": cid,
        "ts": df["timestamp"].astype(np.int64),
        "price": df["price"].astype(np.float64),
        "size": df["size"].astype(np.float64),
        "taker_buy": (df["side"] == "BUY").astype(np.int8),
        "outcome_idx": df["outcomeIndex"].astype(np.int8),
        "wallet": df["proxyWallet"].astype(str),
    })


async def run(dur, since, until, shard_size=400, conc=8):
    m = pd.read_parquet(f"data/markets_{dur}.parquet")
    m = m[(m.start_ts >= since) & (m.start_ts < until) & m.closed].sort_values("start_ts", ascending=False)
    os.makedirs(f"data/trades_{dur}", exist_ok=True)
    done = set()
    for f in os.listdir(f"data/trades_{dur}"):
        if f.endswith(".parquet"):
            done |= set(pd.read_parquet(f"data/trades_{dur}/{f}", columns=["condition_id"]).condition_id.unique())
    todo = [c for c in m.condition_id if c not in done]
    print(dur, "markets", len(m), "todo", len(todo), flush=True)
    sem = asyncio.Semaphore(conc)
    t0 = time.time()
    async with aiohttp.ClientSession(trust_env=True) as session:
        for s in range(0, len(todo), shard_size):
            chunk = todo[s:s + shard_size]
            res = await asyncio.gather(*[fetch_market(session, c, sem) for c in chunk])
            parts = [compact(c, d) for c, d in res if d]
            n_cap = sum(1 for _, d in res if d and len(d) >= 10000)
            if parts:
                df = pd.concat(parts, ignore_index=True)
                df.to_parquet(f"data/trades_{dur}/shard_{int(time.time()*1000)}.parquet")
            print(f"{dur} {s+len(chunk)}/{len(todo)} rows={sum(len(p) for p in parts)} capped={n_cap} "
                  f"elapsed={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    dur = sys.argv[1]
    since = int(pd.Timestamp(sys.argv[2], tz="UTC").timestamp())
    until = int(pd.Timestamp(sys.argv[3], tz="UTC").timestamp()) if len(sys.argv) > 3 else int(time.time())
    asyncio.run(run(dur, since, until))

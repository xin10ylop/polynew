"""Enumerate BTC up/down markets from the Gamma API by deterministic slug."""
import asyncio
import json
import time

import aiohttp
import pandas as pd

GAMMA = "https://gamma-api.polymarket.com/events"
DUR = {"5m": 300, "15m": 900, "4h": 14400}


def slug_for(dur, ts):
    return f"btc-updown-{dur}-{ts}"


def _parse_event(e, dur):
    m = e["markets"][0]
    meta = e.get("eventMetadata") or {}
    toks = json.loads(m.get("clobTokenIds") or "[]")
    outs = json.loads(m.get("outcomes") or "[]")
    prices = json.loads(m.get("outcomePrices") or "[]")
    start_ts = int(e["slug"].rsplit("-", 1)[1])
    ptb, fin = meta.get("priceToBeat"), meta.get("finalPrice")
    up_idx = outs.index("Up") if "Up" in outs else 0
    res = None
    if m.get("closed") and prices:
        try:
            res = 1 if float(prices[up_idx]) > 0.5 else 0
        except ValueError:
            res = None
    fs = m.get("feeSchedule") or {}
    cfg = m.get("cryptoMarketConfig") or {}
    return dict(
        slug=e["slug"], dur=dur, start_ts=start_ts, end_ts=start_ts + DUR[dur],
        condition_id=m.get("conditionId"),
        up_token=toks[up_idx] if toks else None,
        down_token=toks[1 - up_idx] if len(toks) > 1 else None,
        price_to_beat=ptb, final_price=fin, up_won=res,
        volume=float(m.get("volume") or 0), closed=bool(m.get("closed")),
        fee_rate=fs.get("rate"), fee_exp=fs.get("exponent"), rebate=fs.get("rebateRate"),
        twap=cfg.get("twapEnabled"), twap_lookback=cfg.get("twapLookbackSeconds"),
        config_id=m.get("cryptoMarketConfigId"),
    )


async def _fetch_batch(session, slugs, dur, sem):
    params = [("slug", s) for s in slugs]
    async with sem:
        for attempt in range(6):
            try:
                async with session.get(GAMMA, params=params, timeout=aiohttp.ClientTimeout(total=60)) as r:
                    if r.status == 429:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    r.raise_for_status()
                    data = await r.json()
                    return [_parse_event(e, dur) for e in data if e.get("markets")]
            except Exception as ex:  # noqa: BLE001
                if attempt == 5:
                    print("batch failed", slugs[0], ex)
                    return []
                await asyncio.sleep(2 ** attempt)
    return []


async def enumerate_markets(dur, start_ts, end_ts, batch=20, conc=10):
    step = DUR[dur]
    t0 = start_ts // step * step
    slugs = [slug_for(dur, t) for t in range(t0, end_ts, step)]
    sem = asyncio.Semaphore(conc)
    async with aiohttp.ClientSession(trust_env=True) as session:
        tasks = [_fetch_batch(session, slugs[i:i + batch], dur, sem) for i in range(0, len(slugs), batch)]
        out = []
        for i, fut in enumerate(asyncio.as_completed(tasks)):
            out.extend(await fut)
    df = pd.DataFrame(out).sort_values("start_ts").reset_index(drop=True)
    return df


if __name__ == "__main__":
    import sys
    dur = sys.argv[1]
    start = int(pd.Timestamp(sys.argv[2], tz="UTC").timestamp())
    end = int(pd.Timestamp(sys.argv[3], tz="UTC").timestamp()) if len(sys.argv) > 3 else int(time.time())
    df = asyncio.run(enumerate_markets(dur, start, end))
    out = f"data/markets_{dur}.parquet"
    df.to_parquet(out)
    print(dur, len(df), "markets ->", out)
    print(df.tail(3).T)

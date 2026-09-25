"""Measure the latencies that decide whether the deep-maker edge exists on THIS host.

  python -m bot.latency_probe            # feed lag + REST round trip (no keys needed)
  PROBE_ORDERS=1 python -m bot.latency_probe   # also post+cancel 5 tiny post-only orders far from the market (needs keys)

Rule of thumb from the backtests: event->order-at-engine must be <= ~30-50 ms. From a generic cloud box we
measured 300-480 ms REST and multi-second feed lags (strategy loses). Target host: AWS eu-west-1 (Dublin) -- London/UK IPs are close-only on the Polymarket API."""
import asyncio
import json
import os
import statistics
import time

import aiohttp
import websockets

WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA = "https://gamma-api.polymarket.com/events"


async def current_market(session):
    st = int(time.time() // 300 * 300)
    async with session.get(GAMMA, params={"slug": f"btc-updown-5m-{st}"}) as r:
        ev = (await r.json())[0]
    m = ev["markets"][0]
    return json.loads(m["clobTokenIds"]), m["conditionId"]


async def feed_lag(tokens, seconds=30):
    lags = []
    async with websockets.connect(WS_MARKET, max_queue=None) as ws:
        await ws.send(json.dumps({"assets_ids": tokens, "type": "market"}))
        t_end = time.time() + seconds
        while time.time() < t_end:
            try:
                raw = await asyncio.wait_for(ws.recv(), 5)
            except asyncio.TimeoutError:
                continue
            now = time.time() * 1000
            msg = json.loads(raw)
            for it in (msg if isinstance(msg, list) else [msg]):
                if "timestamp" in it:
                    lags.append(now - int(it["timestamp"]))
    # raw lag includes clock offset; the spread (p90 - min) is the queueing/network part
    lags.sort()
    return dict(n=len(lags), min=lags[0], p50=statistics.median(lags), p90=lags[int(0.9 * len(lags))],
                p99=lags[int(0.99 * len(lags))], jitter_p90_minus_min=lags[int(0.9 * len(lags))] - lags[0])


async def rest_rtt(session, n=20):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        async with session.get("https://clob.polymarket.com/time") as r:
            await r.text()
        out.append((time.perf_counter() - t) * 1000)
    return dict(p50=statistics.median(out), min=min(out), max=max(out))


def order_rtt(tokens, n=5):
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
    c = ClobClient("https://clob.polymarket.com", key=os.environ["POLY_PRIVATE_KEY"], chain_id=137,
                   signature_type=int(os.environ.get("POLY_SIG_TYPE", "1")), funder=os.environ["POLY_FUNDER"])
    c.set_api_creds(c.create_or_derive_api_creds())
    res = []
    for _ in range(n):
        args = OrderArgs(token_id=tokens[0], price=0.01, size=5, side="BUY")  # far below market, post-only
        t0 = time.perf_counter()
        signed = c.create_order(args, PartialCreateOrderOptions(tick_size="0.01", neg_risk=False))
        t1 = time.perf_counter()
        resp = c.post_order(signed, OrderType.GTC, post_only=True)
        t2 = time.perf_counter()
        oid = resp.get("orderID")
        if oid:
            c.cancel(oid)
        t3 = time.perf_counter()
        res.append(dict(sign_ms=(t1 - t0) * 1000, post_ms=(t2 - t1) * 1000, cancel_ms=(t3 - t2) * 1000))
        time.sleep(0.5)
    return res


async def main():
    async with aiohttp.ClientSession(trust_env=True) as s:
        tokens, cid = await current_market(s)
        print("REST /time round trip ms:", await rest_rtt(s))
    print("feed timestamps (local - exchange) ms over 30s:", await feed_lag(tokens))
    if os.environ.get("PROBE_ORDERS") == "1":
        for r in order_rtt(tokens):
            print("order:", {k: round(v, 1) for k, v in r.items()})
    print("\nTarget: REST p50 <= ~20 ms, feed jitter p90 <= ~30 ms, post_ms <= ~30 ms. Otherwise do not run live.")


if __name__ == "__main__":
    asyncio.run(main())

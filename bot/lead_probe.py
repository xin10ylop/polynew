"""Measure how late BTC futures trades reach this host (the bot's BTC guard depends on it).

  python -m bot.lead_probe

For 30 s it prints the receive time minus the exchange trade time for Bybit (BTCUSDT perp, the bot's feed) and
Binance USD-M (BTCUSDT perp, the backtest's feed). It relies on the host clock; AWS instances sync to Amazon Time
Sync, which is accurate to well under 1 ms. The backtest assumed 0 ms here."""
import asyncio
import json
import statistics
import time

import websockets

BYBIT = "wss://stream.bybit.com/v5/public/linear"
BINANCE = "wss://fstream.binance.com/ws/btcusdt@aggTrade"


async def bybit(lags, seconds):
    async with websockets.connect(BYBIT, max_queue=None, open_timeout=10) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": ["publicTrade.BTCUSDT"]}))
        end = time.time() + seconds
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), 5)
            except asyncio.TimeoutError:
                continue
            now = time.time() * 1000
            for tr in json.loads(raw).get("data") or []:
                lags.append(now - int(tr["T"]))


async def binance(lags, seconds):
    async with websockets.connect(BINANCE, max_queue=None, open_timeout=10) as ws:
        end = time.time() + seconds
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), 5)
            except asyncio.TimeoutError:
                continue
            now = time.time() * 1000
            lags.append(now - int(json.loads(raw)["T"]))


def show(name, lags):
    if not lags:
        print(f"{name}: no trades received (blocked or no connection)")
        return
    lags.sort()
    q = lambda f: lags[min(len(lags) - 1, int(f * len(lags)))]  # noqa: E731
    print(f"{name}: n={len(lags)}  min={lags[0]:.0f} ms  p50={statistics.median(lags):.0f} ms  "
          f"p90={q(0.9):.0f} ms  p99={q(0.99):.0f} ms")


async def main(seconds=30):
    by, bn = [], []
    res = await asyncio.gather(asyncio.wait_for(bybit(by, seconds), seconds + 15),
                               asyncio.wait_for(binance(bn, seconds), seconds + 15), return_exceptions=True)
    for name, r in zip(("bybit", "binance"), res):
        if isinstance(r, Exception):
            print(f"{name} error: {r!r}"[:160])
    show("Bybit BTCUSDT perp  (bot guard feed)", by)
    show("Binance BTCUSDT perp (backtest feed)", bn)


if __name__ == "__main__":
    asyncio.run(main())

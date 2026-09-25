"""Measure how late BTC trades from each exchange reach this host (the bot's BTC guard depends on it).

  python -m bot.lead_probe [seconds]

For each venue it prints the receive time minus the exchange trade time, over `seconds` (default 30), all venues
at once. It relies on the host clock; AWS instances sync to Amazon Time Sync, which is accurate to well under 1 ms.
The backtest originally assumed 0 ms here."""
import asyncio
import datetime as dt
import json
import statistics
import sys
import time

import websockets


def _iso_ms(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000


def _bybit(m):
    return [int(t["T"]) for t in m.get("data") or []] if m.get("topic", "").startswith("publicTrade") else []


def _binance(m):
    return [int(m["T"])] if "T" in m else []


def _coinbase(m):
    return [_iso_ms(m["time"])] if m.get("type") == "match" else []


def _kraken(m):
    return [_iso_ms(t["timestamp"]) for t in m.get("data") or []] if m.get("channel") == "trade" else []


def _bitstamp(m):
    return [int(m["data"]["microtimestamp"]) / 1000] if m.get("event") == "trade" else []


def _okx(m):
    return [int(t["ts"]) for t in m.get("data") or []] if "data" in m else []


def _deribit(m):
    d = (m.get("params") or {}).get("data")
    return [int(t["timestamp"]) for t in d] if isinstance(d, list) else []


def _hyperliquid(m):
    return [int(t["time"]) for t in m.get("data") or []] if m.get("channel") == "trades" else []


VENUES = [
    ("Bybit BTCUSDT perp (Singapore; bot feed)", "wss://stream.bybit.com/v5/public/linear",
     {"op": "subscribe", "args": ["publicTrade.BTCUSDT"]}, _bybit),
    ("Binance BTCUSDT perp (Tokyo)", "wss://fstream.binance.com/ws/btcusdt@trade", None, _binance),
    ("Binance BTCUSDT perp alt URL", "wss://fstream.binance.com/market/ws/btcusdt@trade", None, _binance),
    ("Binance BTCUSDT spot (Tokyo)", "wss://stream.binance.com:9443/ws/btcusdt@trade", None, _binance),
    ("Coinbase BTC-USD spot (US East)", "wss://ws-feed.exchange.coinbase.com",
     {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["matches"]}, _coinbase),
    ("Kraken BTC/USD spot", "wss://ws.kraken.com/v2",
     {"method": "subscribe", "params": {"channel": "trade", "symbol": ["BTC/USD"]}}, _kraken),
    ("Bitstamp BTC/USD spot", "wss://ws.bitstamp.net",
     {"event": "bts:subscribe", "data": {"channel": "live_trades_btcusd"}}, _bitstamp),
    ("OKX BTC-USDT perp", "wss://ws.okx.com:8443/ws/v5/public",
     {"op": "subscribe", "args": [{"channel": "trades", "instId": "BTC-USDT-SWAP"}]}, _okx),
    ("Deribit BTC-PERPETUAL (London; 100ms batches)", "wss://www.deribit.com/ws/api/v2",
     {"jsonrpc": "2.0", "id": 1, "method": "public/subscribe", "params": {"channels": ["trades.BTC-PERPETUAL.100ms"]}},
     _deribit),
    ("Hyperliquid BTC perp", "wss://api.hyperliquid.xyz/ws",
     {"method": "subscribe", "subscription": {"type": "trades", "coin": "BTC"}}, _hyperliquid),
]


async def run(url, sub, extract, lags, seconds):
    async with websockets.connect(url, max_queue=None, open_timeout=10, max_size=None) as ws:
        if sub:
            await ws.send(json.dumps(sub))
        end = time.time() + seconds
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), 5)
            except asyncio.TimeoutError:
                continue
            now = time.time() * 1000
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            for m in msg if isinstance(msg, list) else [msg]:
                if isinstance(m, dict):
                    try:
                        lags.extend(now - t for t in extract(m))
                    except (KeyError, TypeError, ValueError):
                        pass


async def main(seconds=30):
    lags = [[] for _ in VENUES]
    res = await asyncio.gather(*[asyncio.wait_for(run(u, s, f, lags[i], seconds), seconds + 15)
                                 for i, (_, u, s, f) in enumerate(VENUES)], return_exceptions=True)
    print(f"{'venue':48s} {'n':>6s} {'min':>6s} {'p50':>6s} {'p90':>6s}   (ms late)")
    for (name, *_), lg, r in zip(VENUES, lags, res):
        if not lg:
            why = f"{r!r}"[:70] if isinstance(r, Exception) else "no trades"
            print(f"{name:48s}  -- {why}")
            continue
        lg.sort()
        print(f"{name:48s} {len(lg):6d} {lg[0]:6.0f} {statistics.median(lg):6.0f} {lg[int(0.9 * len(lg))]:6.0f}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 30))

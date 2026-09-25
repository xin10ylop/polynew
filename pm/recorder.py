"""Live recorder: Polymarket CLOB books for BTC up/down 5m/15m markets + BTC price feeds.

Writes gzipped JSONL, one line per message: {"r": recv_ms, "s": source, "m": raw}.
Rotates hourly. Run: python -m pm.recorder
"""
import asyncio
import gzip
import json
import os
import time

import aiohttp
import websockets

OUT = "data/live"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA = "https://gamma-api.polymarket.com/events"
DURS = {"5m": 300, "15m": 900}


class Sink:
    def __init__(self):
        os.makedirs(OUT, exist_ok=True)
        self.hour = None
        self.f = None
        self.n = 0

    def write(self, src, msg):
        now = time.time()
        h = int(now // 3600)
        if h != self.hour:
            if self.f:
                self.f.close()
            self.hour = h
            self.f = gzip.open(f"{OUT}/rec_{h}_{os.getpid()}.jsonl.gz", "at", compresslevel=3)
        self.f.write(json.dumps({"r": int(now * 1000), "s": src, "m": msg}, separators=(",", ":")) + "\n")
        self.n += 1
        if self.n % 2000 == 0:
            self.f.flush()


sink = Sink()
UP_TOKENS = {}  # token -> slug (only Up tokens; Down book mirrors Up book)
ALL_TOKENS = {}


class CompactSink:
    """Hourly gzip CSV of book deltas: recv_ms,server_ms,slug,side(B/S),price,size,best_bid,best_ask"""
    def __init__(self):
        self.hour = None
        self.f = None

    def w(self, line):
        now = time.time()
        h = int(now // 3600)
        if h != self.hour:
            if self.f:
                self.f.close()
            self.hour = h
            self.f = gzip.open(f"{OUT}/book_{h}_{os.getpid()}.csv.gz", "at", compresslevel=1)
        self.f.write(line)

    def flush(self):
        if self.f:
            self.f.flush()


csink = CompactSink()


def handle_clob(raw):
    r = int(time.time() * 1000)
    try:
        m = json.loads(raw)
    except Exception:  # noqa: BLE001
        return
    for it in (m if isinstance(m, list) else [m]):
        et = it.get("event_type")
        if et == "price_change":
            ts = it.get("timestamp", "")
            for pc in it.get("price_changes", []):
                slug = UP_TOKENS.get(pc.get("asset_id"))
                if slug:
                    csink.w(f"{r},{ts},{slug},{pc['side'][0]},{pc['price']},{pc['size']},"
                            f"{pc.get('best_bid', '')},{pc.get('best_ask', '')}\n")
        elif et == "book":
            if it.get("asset_id") in UP_TOKENS:
                sink.write("book", {"slug": UP_TOKENS[it["asset_id"]], "ts": it.get("timestamp"),
                                    "bids": [(x["price"], x["size"]) for x in it.get("bids", [])],
                                    "asks": [(x["price"], x["size"]) for x in it.get("asks", [])]})
        elif et == "last_trade_price":
            a = it.get("asset_id")
            slug, out = ALL_TOKENS.get(a, ("?", "?"))
            sink.write("trade", {"slug": slug, "out": out, "p": it.get("price"), "sz": it.get("size"),
                                 "side": it.get("side"), "ts": it.get("timestamp")})
        else:
            sink.write("clob", it)
market_meta = {}  # slug -> dict


async def fetch_meta(session, slugs):
    need = [s for s in slugs if s not in market_meta]
    if not need:
        return
    params = [("slug", s) for s in need]
    try:
        async with session.get(GAMMA, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json()
        for e in data:
            m = e["markets"][0]
            toks = json.loads(m["clobTokenIds"])
            outs = json.loads(m["outcomes"])
            up = outs.index("Up")
            meta = dict(slug=e["slug"], cid=m["conditionId"], up=toks[up], down=toks[1 - up],
                        start=int(e["slug"].rsplit("-", 1)[1]), meta=e.get("eventMetadata"))
            market_meta[e["slug"]] = meta
            UP_TOKENS[meta["up"]] = e["slug"]
            ALL_TOKENS[meta["up"]] = (e["slug"], "Up")
            ALL_TOKENS[meta["down"]] = (e["slug"], "Down")
            sink.write("meta", meta)
    except Exception as ex:  # noqa: BLE001
        print("meta error", ex, flush=True)


def wanted_slugs(now):
    out = []
    for d, step in DURS.items():
        cur = int(now // step * step)
        for k in (0, 1):  # current and next window
            out.append(f"btc-updown-{d}-{cur + k * step}")
    return out


async def clob_conn(assets, stop_evt):
    """One websocket per market (2 tokens). Unbounded queue so the server never sees a slow consumer."""
    while not stop_evt.is_set():
        try:
            async with websockets.connect(CLOB_WS, open_timeout=15, ping_interval=None, max_size=None,
                                          max_queue=None) as ws:
                await ws.send(json.dumps({"assets_ids": assets, "type": "market"}))

                async def pinger():
                    while True:
                        await asyncio.sleep(9)
                        await ws.send("PING")

                async def stopper():
                    await stop_evt.wait()
                    await ws.close()

                pt = asyncio.create_task(pinger())
                st = asyncio.create_task(stopper())
                try:
                    async for m in ws:
                        if m != "PONG":
                            handle_clob(m)
                finally:
                    pt.cancel()
                    st.cancel()
        except Exception as ex:  # noqa: BLE001
            print("clob error", repr(ex)[:200], flush=True)
            await asyncio.sleep(1)


async def clob_manager():
    running = {}  # slug -> stop event
    async with aiohttp.ClientSession(trust_env=True) as session:
        while True:
            slugs = wanted_slugs(time.time())
            await fetch_meta(session, slugs)
            for s in slugs:
                if s in market_meta and s not in running:
                    ev = asyncio.Event()
                    running[s] = ev
                    asyncio.create_task(clob_conn([market_meta[s]["up"], market_meta[s]["down"]], ev))
                    sink.write("subs", {"slug": s})
            for s in list(running):
                if s not in slugs:
                    # keep a few seconds past window end to catch the final prints
                    end = market_meta[s]["start"] + DURS[s.split("-")[2]]
                    if time.time() > end + 20:
                        running.pop(s).set()
            await asyncio.sleep(2)


async def simple_feed(name, url, sub=None, ping=None, ping_every=15):
    while True:
        try:
            async with websockets.connect(url, open_timeout=15, ping_interval=20, max_size=None) as ws:
                if sub:
                    for s in (sub if isinstance(sub, list) else [sub]):
                        await ws.send(s)
                last = time.time()
                while True:
                    try:
                        m = await asyncio.wait_for(ws.recv(), 5)
                        if m and m not in ("PONG", "pong"):
                            sink.write(name, m)
                    except asyncio.TimeoutError:
                        pass
                    if ping and time.time() - last > ping_every:
                        await ws.send(ping)
                        last = time.time()
        except Exception as ex:  # noqa: BLE001
            print(name, "error", repr(ex)[:200], flush=True)
            await asyncio.sleep(2)


async def main():
    rtds_sub = json.dumps({"action": "subscribe", "subscriptions": [
        {"topic": "crypto_prices_chainlink", "type": "*", "filters": "{\"symbol\":\"btc/usd\"}"},
        {"topic": "crypto_prices", "type": "update", "filters": "btcusdt"},
    ]})
    tasks = [
        clob_manager(),
        simple_feed("rtds", "wss://ws-live-data.polymarket.com", rtds_sub, ping="PING", ping_every=5),
        simple_feed("cb", "wss://ws-feed.exchange.coinbase.com",
                    json.dumps({"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]})),
        simple_feed("bybit", "wss://stream.bybit.com/v5/public/linear",
                    json.dumps({"op": "subscribe", "args": ["publicTrade.BTCUSDT", "orderbook.1.BTCUSDT"]}),
                    ping=json.dumps({"op": "ping"}), ping_every=15),
        simple_feed("kraken", "wss://ws.kraken.com/v2",
                    json.dumps({"method": "subscribe", "params": {"channel": "trade", "symbol": ["BTC/USD"]}})),
        simple_feed("hl", "wss://api.hyperliquid.xyz/ws",
                    json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": "BTC"}}),
                    ping=json.dumps({"method": "ping"}), ping_every=30),
    ]

    async def stats():
        while True:
            await asyncio.sleep(60)
            print(time.strftime("%H:%M:%S"), "msgs", sink.n, flush=True)
            sink.f and sink.f.flush()
            csink.flush()

    await asyncio.gather(stats(), *tasks)


if __name__ == "__main__":
    asyncio.run(main())

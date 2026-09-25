"""Event loop for the deep-maker bot.  python -m bot.run   (BOT_MODE=paper|live)

Paper mode: real-time market data, simulated queue-aware fills with virtual latency (BOT_PAPER_LAT_MS).
Live mode: post-only GTC orders via py-clob-client. Deploy near AWS eu-west-2; the backtested edge needs
<= ~50ms book-event-to-order latency (see REPORT.md). Create a file named KILL to cancel everything and stop."""
import asyncio
import json
import os
import time

import aiohttp
import websockets

from bot.book import UpBook
from bot.config import Config
from bot.strategy import desired_quotes

WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_USER = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
GAMMA = "https://gamma-api.polymarket.com/events"
DUR_S = {"5m": 300, "15m": 900}


class Market:
    def __init__(self, slug, cid, up, dn, start, end):
        self.slug, self.cid, self.up, self.dn, self.start, self.end = slug, cid, up, dn, start, end
        self.book = UpBook()
        self.inv = {True: 0.0, False: 0.0}
        self.cost = {True: 0.0, False: 0.0}
        self.last_replace = {True: 0, False: 0}
        self.stop = asyncio.Event()
        self.closed = False
        self.last_rx = 0.0          # local receive time of the last market message


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.markets = {}
        self.realized = 0.0
        self.halted = False
        self.lead = []            # (local_ms, price) BTC lead ticks (Bybit perp)
        self.pull_until = {True: 0, False: 0}   # side -> local ms until which that side is pulled
        self.clock_off = None     # estimate of (server ms - local ms), min over recent messages (least delayed)
        os.makedirs(cfg.log_dir, exist_ok=True)
        self.log_f = open(f"{cfg.log_dir}/bot_{int(time.time())}.jsonl", "a")
        if cfg.mode == "live":
            from bot.live import LiveExec
            self.ex = LiveExec(cfg)
        else:
            from bot.paper import PaperExec
            self.ex = PaperExec(cfg.paper_latency_ms, self.on_fill)

    def log(self, kind, **kw):
        self.log_f.write(json.dumps({"t": int(time.time() * 1000), "k": kind, **kw}) + "\n")
        self.log_f.flush()

    # ---------------- fills / pnl
    def on_fill(self, cid, side_up, price, size, ts):
        m = self.markets.get(cid)
        if m is None:
            return
        m.inv[side_up] += size
        m.cost[side_up] += price * size
        self.log("fill", slug=m.slug, up=side_up, px=price, sz=size, ts=ts, inv_up=m.inv[True], inv_dn=m.inv[False])

    async def settle(self, session, m):
        for _ in range(60):
            async with session.get(GAMMA, params={"slug": m.slug}) as r:
                ev = (await r.json() or [{}])[0]
            meta = ev.get("eventMetadata") or {}
            if "finalPrice" in meta and "priceToBeat" in meta:
                up_won = meta["finalPrice"] >= meta["priceToBeat"]
                payout = m.inv[True] * up_won + m.inv[False] * (not up_won)
                pnl = payout - m.cost[True] - m.cost[False]
                self.realized += pnl
                self.log("settle", slug=m.slug, up_won=up_won, inv_up=m.inv[True], inv_dn=m.inv[False],
                         cost=m.cost[True] + m.cost[False], pnl=pnl, realized=self.realized)
                print(f"[settle] {m.slug} up_won={up_won} up={m.inv[True]:.0f} dn={m.inv[False]:.0f} "
                      f"pnl={pnl:+.2f} total={self.realized:+.2f}", flush=True)
                if self.realized <= -self.cfg.daily_loss_limit:
                    self.halted = True
                    print("[halt] daily loss limit reached", flush=True)
                return
            await asyncio.sleep(10)

    # ---------------- quoting
    async def requote(self, m, now_ms):
        if self.halted or os.path.exists(self.cfg.kill_file):
            await self.cancel_market(m, now_ms)
            return
        stale = (time.time() - m.last_rx) * 1000 > self.cfg.stale_ms
        now_local = time.time() * 1000
        over_cap = (m.cost[True] + m.cost[False]) >= self.cfg.max_usd_per_market
        want = {} if (stale or over_cap) else desired_quotes(m.book, m.inv[True], m.inv[False], now_ms / 1000,
                                                              m.start, m.end, self.cfg)
        for key, side_up in (("up", True), ("dn", False)):
            w = want.get(key)
            if self.cfg.guard_on and now_local < self.pull_until[side_up]:
                w = None
            cur = self.ex.working(m.cid, side_up, now_ms)
            cur_px = None
            if cur:
                o = cur[0]
                cur_px = o["price"] if isinstance(o, dict) else (o.level if side_up else round(1 - o.level, 4))
            if w is None:
                if cur:
                    await self._cancel(m, side_up, cur, now_ms)
                continue
            if cur_px is not None and abs(cur_px - w[0]) < 1e-9:
                continue
            if now_ms - m.last_replace[side_up] < self.cfg.min_replace_ms:
                continue
            m.last_replace[side_up] = now_ms
            if cur:
                await self._cancel(m, side_up, cur, now_ms)
            await self._place(m, side_up, w[0], w[1], now_ms)

    async def _place(self, m, side_up, price, size, now_ms):
        if self.cfg.mode == "live":
            asyncio.create_task(self.ex.place(m.cid, m.up if side_up else m.dn, side_up, price, size))
        else:
            self.ex.place(m.cid, side_up, price, size, now_ms)

    async def _cancel(self, m, side_up, cur, now_ms):
        if self.cfg.mode == "live":
            asyncio.create_task(self.ex.cancel(m.cid, side_up))
        else:
            for o in cur:
                self.ex.cancel(m.cid, o, now_ms)

    async def cancel_market(self, m, now_ms):
        if self.cfg.mode == "live":
            for s in (True, False):
                await self.ex.cancel(m.cid, s)
        else:
            self.ex.cancel_all(m.cid, now_ms)

    # ---------------- market data
    async def market_ws(self, m):
        while not m.stop.is_set():
            try:
                async with websockets.connect(WS_MARKET, max_queue=None, ping_interval=None, open_timeout=15) as ws:
                    await ws.send(json.dumps({"assets_ids": [m.up, m.dn], "type": "market"}))

                    async def pinger():
                        while True:
                            await asyncio.sleep(9)
                            await ws.send("PING")
                    pt = asyncio.create_task(pinger())
                    try:
                        async for raw in ws:
                            if raw == "PONG":
                                continue
                            await self.handle(m, raw)
                            if m.stop.is_set():
                                break
                    finally:
                        pt.cancel()
            except Exception as ex:  # noqa: BLE001
                print("ws error", m.slug, repr(ex)[:120], flush=True)
                await asyncio.sleep(0.5)

    def server_now(self):
        return int(time.time() * 1000 + (self.clock_off or 0))

    async def handle(self, m, raw):
        m.last_rx = time.time()
        msg = json.loads(raw)
        for it in (msg if isinstance(msg, list) else [msg]):
            et = it.get("event_type")
            ts = int(it.get("timestamp") or time.time() * 1000)
            off = ts - m.last_rx * 1000
            # off = true_offset - feed_delay, so the least-delayed message gives the largest off; slowly decaying max
            self.clock_off = off if self.clock_off is None else max(self.clock_off - 0.5, off)
            if et == "book" and it.get("asset_id") == m.up:
                m.book.snapshot([(x["price"], x["size"]) for x in it["bids"]], [(x["price"], x["size"]) for x in it["asks"]], ts)
                if self.cfg.mode == "paper":
                    self.ex.on_book(m.cid, m.book, ts)
            elif et == "price_change":
                for pc in it.get("price_changes", []):
                    if pc.get("asset_id") != m.up:
                        continue
                    is_bid = pc["side"] == "BUY"
                    m.book.delta(is_bid, pc["price"], pc["size"], ts)
                    if self.cfg.mode == "paper":
                        self.ex.on_book(m.cid, m.book, ts)
                        self.ex.on_delta(m.cid, is_bid, float(pc["price"]), float(pc["size"]), ts)
            elif et == "last_trade_price" and self.cfg.mode == "paper":
                p, sz, up = float(it["price"]), float(it["size"]), it.get("asset_id") == m.up
                side = it.get("side")
                if up and side == "SELL":
                    self.ex.on_trade(m.cid, True, round(p, 4), sz, ts)
                elif (not up) and side == "BUY":
                    self.ex.on_trade(m.cid, True, round(1 - p, 4), sz, ts)
                elif up and side == "BUY":
                    self.ex.on_trade(m.cid, False, round(p, 4), sz, ts)
                else:
                    self.ex.on_trade(m.cid, False, round(1 - p, 4), sz, ts)
        await self.requote(m, int(time.time() * 1000) if self.cfg.mode == "live" else self.server_now())

    async def lead_ws(self):
        """BTC lead feed (Bybit linear perp trades). A fast move pulls the side it makes stale:
        BTC up -> Down bids are stale; BTC down -> Up bids are stale."""
        url = "wss://stream.bybit.com/v5/public/linear"
        while True:
            try:
                async with websockets.connect(url, max_queue=None, open_timeout=15, ping_interval=20) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": ["publicTrade.BTCUSDT"]}))
                    async for raw in ws:
                        d = json.loads(raw)
                        if not str(d.get("topic", "")).startswith("publicTrade"):
                            continue
                        now = time.time() * 1000
                        for x in d.get("data", []):
                            self.lead.append((now, float(x["p"])))
                        W = self.cfg.guard_window_ms
                        while self.lead and self.lead[0][0] < now - W:
                            self.lead.pop(0)
                        if len(self.lead) >= 2:
                            mv = (self.lead[-1][1] / self.lead[0][1] - 1) * 1e4
                            side = False if mv >= self.cfg.guard_bp else True if mv <= -self.cfg.guard_bp else None
                            if side is not None and self.pull_until[side] < now:
                                self.pull_until[side] = now + self.cfg.guard_cool_ms
                                self.log("guard", mv_bp=round(mv, 2), pull="dn" if side is False else "up")
                                for m in list(self.markets.values()):
                                    if not m.closed:
                                        await self.requote(m, int(now) if self.cfg.mode == "live" else self.server_now())
            except Exception as ex:  # noqa: BLE001
                print("lead ws error", repr(ex)[:120], flush=True)
                await asyncio.sleep(1)

    async def user_ws(self):
        """Live fills from the authenticated user channel."""
        creds = self.ex.creds
        while True:
            try:
                async with websockets.connect(WS_USER, max_queue=None, open_timeout=15) as ws:
                    await ws.send(json.dumps({"auth": {"apiKey": creds.api_key, "secret": creds.api_secret,
                                                       "passphrase": creds.api_passphrase},
                                              "markets": [m.cid for m in self.markets.values()], "type": "user"}))
                    async for raw in ws:
                        if raw == "PONG":
                            continue
                        for it in (json.loads(raw) if raw.startswith("[") else [json.loads(raw)]):
                            if it.get("event_type") != "trade":
                                continue
                            for mo in it.get("maker_orders", []):
                                for (cid, side_up), o in list(self.ex.open.items()):
                                    if o["order_id"] == mo.get("order_id"):
                                        self.on_fill(cid, side_up, float(mo["price"]), float(mo["matched_amount"]),
                                                     int(time.time() * 1000))
            except Exception as ex:  # noqa: BLE001
                print("user ws error", repr(ex)[:120], flush=True)
                await asyncio.sleep(1)

    # ---------------- market lifecycle
    async def discover(self, session):
        now = time.time()
        for d in self.cfg.durations:
            step = DUR_S[d]
            for k in (0, 1):
                st = int(now // step * step) + k * step
                slug = f"btc-updown-{d}-{st}"
                if any(m.slug == slug for m in self.markets.values()):
                    continue
                async with session.get(GAMMA, params={"slug": slug}) as r:
                    evs = await r.json()
                if not evs:
                    continue
                mk = evs[0]["markets"][0]
                toks = json.loads(mk["clobTokenIds"])
                outs = json.loads(mk["outcomes"])
                up = outs.index("Up")
                m = Market(slug, mk["conditionId"], toks[up], toks[1 - up], st, st + step)
                self.markets[m.cid] = m
                asyncio.create_task(self.market_ws(m))
                print(f"[market] {slug}", flush=True)

    async def lifecycle(self):
        async with aiohttp.ClientSession(trust_env=True) as session:
            while True:
                await self.discover(session)
                now = time.time()
                for cid, m in list(self.markets.items()):
                    sn = self.server_now() if self.cfg.mode == "paper" else int(now * 1000)
                    if not m.closed and m.last_rx and (now - m.last_rx) * 1000 > self.cfg.stale_ms:
                        await self.cancel_market(m, sn)
                    if not m.closed and now > m.end - self.cfg.stop_before_end_s:
                        await self.cancel_market(m, sn)
                    if not m.closed and now > m.end + 2:
                        m.closed = True
                        m.stop.set()
                        asyncio.create_task(self.settle(session, m))
                    if m.closed and now > m.end + 900:
                        self.markets.pop(cid, None)
                if int(now) % 30 == 0:
                    for m in self.markets.values():
                        if m.closed:
                            continue
                        wu = self.ex.working(m.cid, True, int(now * 1000)) if self.cfg.mode == "paper" else []
                        wd = self.ex.working(m.cid, False, int(now * 1000)) if self.cfg.mode == "paper" else []
                        print(f"[status] {m.slug} book_ready={m.book.ready} bb={m.book.best_bid()} ba={m.book.best_ask()} "
                              f"book_age_ms={int(now*1000)-m.book.ts} orders_up={[round(o.level,2) for o in wu]} "
                              f"orders_dn={[round(1-o.level,2) for o in wd]} inv=({m.inv[True]:.0f},{m.inv[False]:.0f})", flush=True)
                await asyncio.sleep(1)

    async def main(self):
        tasks = [self.lifecycle()]
        if self.cfg.guard_on:
            tasks.append(self.lead_ws())
        if self.cfg.mode == "live":
            tasks.append(self.user_ws())
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    cfg = Config()
    print(f"deep-maker bot mode={cfg.mode} back={cfg.back_ticks} size={cfg.size} lat(paper)={cfg.paper_latency_ms}ms", flush=True)
    asyncio.run(Bot(cfg).main())

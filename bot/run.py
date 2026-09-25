"""Deep-maker bot for Polymarket BTC Up/Down 5m.   python -m bot.run   (BOT_MODE=paper|live)

Every market is always traded by a SHADOW paper engine (queue-aware simulated fills, virtual latency).
The shadow's settled window PnL feeds an optional REGIME GATE: with BOT_USE_GATE=1, live quoting is allowed only
while the mean shadow PnL of the last `gate_k` settled windows is > 0. It is off by default: it helped the older
2-behind variant, but for the current 3-behind variant it cut PnL on Sep 10-23 (REPORT.md 4d). It is always
evaluated and logged.

Safety: stale/lagging feed guard, BTC lead-move guard (Bybit perp), inventory cap counting in-flight orders,
dollar cap per market, daily loss halt, KILL file. Deploy in AWS eu-west-1 (Dublin; UK IPs are close-only on the API): the edge needs
<= ~50 ms from book event to order at the matching engine (see REPORT.md)."""
import asyncio
import collections
import json
import os
import time

import aiohttp
import websockets

from bot.book import UpBook
from bot.config import Config
from bot.paper import PaperExec
from bot.strategy import desired_quotes

WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_USER = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
WS_LEAD = "wss://stream.bybit.com/v5/public/linear"
GAMMA = "https://gamma-api.polymarket.com/events"
DUR_S = {"5m": 300, "15m": 900}


class Account:
    def __init__(self):
        self.inv = {True: 0.0, False: 0.0}
        self.cost = {True: 0.0, False: 0.0}

    def fill(self, side_up, px, sz):
        self.inv[side_up] += sz
        self.cost[side_up] += px * sz

    def pnl(self, up_won):
        return self.inv[True] * up_won + self.inv[False] * (not up_won) - self.cost[True] - self.cost[False]


class Market:
    def __init__(self, slug, cid, up, dn, start, end):
        self.slug, self.cid, self.up, self.dn, self.start, self.end = slug, cid, up, dn, start, end
        self.book = UpBook()
        self.acct = {"shadow": Account(), "live": Account()}
        self.last_replace = collections.defaultdict(int)
        self.stop = asyncio.Event()
        self.closed = False
        self.last_rx = 0.0
        self.lag_ms = 0.0
        self.gate_at_open = None


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.markets = {}
        self.realized = {"shadow": 0.0, "live": 0.0, "shadow_gated": 0.0}
        self.shadow_hist = collections.deque(maxlen=cfg.gate_k)
        self.halted = False
        self.lead = collections.deque()
        self.pull_until = {True: 0.0, False: 0.0}
        self.clock_off = None
        os.makedirs(cfg.log_dir, exist_ok=True)
        self.log_f = open(f"{cfg.log_dir}/bot_{int(time.time())}.jsonl", "a")
        self.shadow = PaperExec(cfg.paper_latency_ms, lambda *a: self.on_fill("shadow", *a))
        self.live = None
        if cfg.mode == "live":
            from bot.live import LiveExec
            self.live = LiveExec(cfg)

    # ------------------------------------------------------------------ utils
    def log(self, kind, **kw):
        self.log_f.write(json.dumps({"t": int(time.time() * 1000), "k": kind, **kw}) + "\n")
        self.log_f.flush()

    def server_now(self):
        return int(time.time() * 1000 + (self.clock_off or 0))

    def gate_on(self):
        if len(self.shadow_hist) < self.cfg.gate_k:
            return bool(self.cfg.gate_warmup_allow)
        return sum(self.shadow_hist) / len(self.shadow_hist) > 0

    def on_fill(self, which, cid, side_up, price, size, ts):
        m = self.markets.get(cid)
        if m is None:
            return
        m.acct[which].fill(side_up, price, size)
        a = m.acct[which]
        self.log("fill", ex=which, slug=m.slug, up=side_up, px=price, sz=size, ts=ts,
                 inv_up=a.inv[True], inv_dn=a.inv[False])

    # ------------------------------------------------------------------ settlement
    async def settle(self, session, m):
        for _ in range(90):
            try:
                async with session.get(GAMMA, params={"slug": m.slug}) as r:
                    ev = (await r.json() or [{}])[0]
            except Exception:  # noqa: BLE001
                ev = {}
            meta = ev.get("eventMetadata") or {}
            if "finalPrice" in meta and "priceToBeat" in meta:
                up_won = meta["finalPrice"] >= meta["priceToBeat"]
                sp = m.acct["shadow"].pnl(up_won)
                lp = m.acct["live"].pnl(up_won)
                self.realized["shadow"] += sp
                self.realized["live"] += lp
                if m.gate_at_open:
                    self.realized["shadow_gated"] += sp
                self.shadow_hist.append(sp)
                self.log("settle", slug=m.slug, up_won=up_won, shadow_pnl=sp, live_pnl=lp, gate=m.gate_at_open,
                         shadow_inv=m.acct["shadow"].inv, realized=self.realized)
                s = m.acct["shadow"]
                print(f"[settle] {m.slug} up_won={up_won} shadow up={s.inv[True]:.0f} dn={s.inv[False]:.0f} "
                      f"pnl={sp:+.2f} | gate_at_open={m.gate_at_open} | totals shadow={self.realized['shadow']:+.2f} "
                      f"gated={self.realized['shadow_gated']:+.2f} live={self.realized['live']:+.2f}", flush=True)
                if self.realized["live"] <= -self.cfg.daily_loss_limit:
                    self.halted = True
                    print("[halt] live daily loss limit reached", flush=True)
                return
            await asyncio.sleep(10)

    # ------------------------------------------------------------------ quoting
    def _guards_ok(self, m):
        silent = (time.time() - m.last_rx) * 1000 > self.cfg.stale_ms
        lagging = m.lag_ms > self.cfg.max_feed_lag_ms
        return not (silent or lagging)

    async def requote(self, m):
        now_local = time.time() * 1000
        feed_ok = self._guards_ok(m)
        # shadow: identical logic, always on (it is the regime detector); uses exchange-time clock
        await self._requote_one(m, "shadow", self.server_now(), feed_ok, now_local)
        if self.live is not None:
            gate_ok = self.gate_on() or not self.cfg.use_gate
            allowed = feed_ok and gate_ok and not self.halted and not os.path.exists(self.cfg.kill_file)
            await self._requote_one(m, "live", int(now_local), allowed, now_local)

    async def _requote_one(self, m, which, now_ms, allowed, now_local):
        acct = m.acct[which]
        ex = self.shadow if which == "shadow" else self.live

        def working(side_up):
            return ex.working(m.cid, side_up, now_ms)

        def open_size(side_up):
            return sum(((o["size"] - o.get("filled", 0.0)) if isinstance(o, dict) else o.size - o.filled)
                       for o in working(side_up))

        over_cap = (acct.cost[True] + acct.cost[False]) >= self.cfg.max_usd_per_market
        want = {} if (not allowed or over_cap) else desired_quotes(
            m.book, acct.inv[True], acct.inv[False], now_ms / 1000 if which == "shadow" else time.time(),
            m.start, m.end, self.cfg, open_size(True), open_size(False))
        for key, side_up in (("up", True), ("dn", False)):
            w = want.get(key)
            if self.cfg.guard_on and now_local < self.pull_until[side_up]:
                w = None
            cur = working(side_up)
            cur_px = None
            if cur:
                o = cur[-1]
                cur_px = o["price"] if isinstance(o, dict) else (o.level if side_up else round(1 - o.level, 4))
            if w is None:
                if cur:
                    await self._cancel(ex, which, m, side_up, cur, now_ms)
                continue
            if cur_px is not None and abs(cur_px - w[0]) < 1e-9:
                continue
            if now_ms - m.last_replace[(which, side_up)] < self.cfg.min_replace_ms:
                continue
            m.last_replace[(which, side_up)] = now_ms
            if cur:
                await self._cancel(ex, which, m, side_up, cur, now_ms)
            if which == "shadow":
                ex.place(m.cid, side_up, w[0], w[1], now_ms)
            else:
                asyncio.create_task(ex.place(m.cid, m.up if side_up else m.dn, side_up, w[0], w[1]))

    async def _cancel(self, ex, which, m, side_up, cur, now_ms):
        if which == "shadow":
            for o in cur:
                ex.cancel(m.cid, o, now_ms)
        else:
            asyncio.create_task(ex.cancel(m.cid, side_up))

    async def cancel_market(self, m):
        self.shadow.cancel_all(m.cid, self.server_now())
        if self.live is not None:
            for s in (True, False):
                await self.live.cancel(m.cid, s)

    # ------------------------------------------------------------------ market data
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
                            if raw != "PONG":
                                await self.handle(m, raw)
                            if m.stop.is_set():
                                break
                    finally:
                        pt.cancel()
            except Exception as ex:  # noqa: BLE001
                print("ws error", m.slug, repr(ex)[:120], flush=True)
                await asyncio.sleep(0.5)

    async def handle(self, m, raw):
        m.last_rx = time.time()
        msg = json.loads(raw)
        for it in (msg if isinstance(msg, list) else [msg]):
            et = it.get("event_type")
            ts = int(it.get("timestamp") or time.time() * 1000)
            off = ts - m.last_rx * 1000
            # off = true_offset - feed_delay: least-delayed message gives the largest off (slowly decaying max)
            self.clock_off = off if self.clock_off is None else max(self.clock_off - 0.5, off)
            m.lag_ms = self.clock_off - off
            if et == "book" and it.get("asset_id") == m.up:
                m.book.snapshot([(x["price"], x["size"]) for x in it["bids"]],
                                [(x["price"], x["size"]) for x in it["asks"]], ts)
                self.shadow.on_book(m.cid, m.book, ts)
            elif et == "price_change":
                for pc in it.get("price_changes", []):
                    if pc.get("asset_id") != m.up:
                        continue
                    is_bid = pc["side"] == "BUY"
                    m.book.delta(is_bid, pc["price"], pc["size"], ts)
                    self.shadow.on_book(m.cid, m.book, ts)
                    self.shadow.on_delta(m.cid, is_bid, float(pc["price"]), float(pc["size"]), ts)
            elif et == "last_trade_price":
                p, sz, up, side = float(it["price"]), float(it["size"]), it.get("asset_id") == m.up, it.get("side")
                if up and side == "SELL":
                    self.shadow.on_trade(m.cid, True, round(p, 4), sz, ts)
                elif (not up) and side == "BUY":
                    self.shadow.on_trade(m.cid, True, round(1 - p, 4), sz, ts)
                elif up and side == "BUY":
                    self.shadow.on_trade(m.cid, False, round(p, 4), sz, ts)
                else:
                    self.shadow.on_trade(m.cid, False, round(1 - p, 4), sz, ts)
        await self.requote(m)

    async def lead_ws(self):
        """BTC lead guard: BTC up fast -> Down bids are stale (pull); BTC down fast -> pull Up bids."""
        while True:
            try:
                async with websockets.connect(WS_LEAD, max_queue=None, open_timeout=15, ping_interval=20) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": ["publicTrade.BTCUSDT"]}))
                    async for raw in ws:
                        d = json.loads(raw)
                        if not str(d.get("topic", "")).startswith("publicTrade"):
                            continue
                        now = time.time() * 1000
                        for x in d.get("data", []):
                            self.lead.append((now, float(x["p"])))
                        while self.lead and self.lead[0][0] < now - self.cfg.guard_window_ms:
                            self.lead.popleft()
                        if len(self.lead) >= 2:
                            mv = (self.lead[-1][1] / self.lead[0][1] - 1) * 1e4
                            side = False if mv >= self.cfg.guard_bp else True if mv <= -self.cfg.guard_bp else None
                            if side is not None and self.pull_until[side] < now:
                                self.pull_until[side] = now + self.cfg.guard_cool_ms
                                self.log("guard", mv_bp=round(mv, 2), pull="dn" if side is False else "up")
                                for m in list(self.markets.values()):
                                    if not m.closed:
                                        await self.requote(m)
            except Exception as ex:  # noqa: BLE001
                print("lead ws error", repr(ex)[:120], flush=True)
                await asyncio.sleep(1)

    async def user_ws(self):
        """Live fills from the authenticated user channel (maker side of trades)."""
        creds = self.live.creds
        while True:
            try:
                async with websockets.connect(WS_USER, max_queue=None, open_timeout=15) as ws:
                    await ws.send(json.dumps({"auth": {"apiKey": creds.api_key, "secret": creds.api_secret,
                                                       "passphrase": creds.api_passphrase},
                                              "markets": [m.cid for m in self.markets.values()], "type": "user"}))
                    async for raw in ws:
                        if raw == "PONG":
                            continue
                        msg = json.loads(raw)
                        for it in (msg if isinstance(msg, list) else [msg]):
                            if it.get("event_type") != "trade":
                                continue
                            for mo in it.get("maker_orders", []):
                                sz = float(mo.get("matched_amount") or 0)
                                key = self.live.on_fill(mo.get("order_id"), sz)
                                if key is not None:
                                    self.on_fill("live", key[0], key[1], float(mo["price"]), sz, int(time.time() * 1000))
            except Exception as ex:  # noqa: BLE001
                print("user ws error", repr(ex)[:120], flush=True)
                await asyncio.sleep(1)

    # ------------------------------------------------------------------ lifecycle
    async def discover(self, session):
        now = time.time()
        for d in self.cfg.durations:
            step = DUR_S[d]
            for k in (0, 1):
                st = int(now // step * step) + k * step
                slug = f"btc-updown-{d}-{st}"
                if any(mm.slug == slug for mm in self.markets.values()):
                    continue
                try:
                    async with session.get(GAMMA, params={"slug": slug}) as r:
                        evs = await r.json()
                except Exception:  # noqa: BLE001
                    continue
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
            last_status = 0
            while True:
                await self.discover(session)
                now = time.time()
                for cid, m in list(self.markets.items()):
                    if m.gate_at_open is None and now >= m.start:
                        m.gate_at_open = self.gate_on()
                    if not m.closed and (now > m.end - self.cfg.stop_before_end_s or
                                         (m.last_rx and (now - m.last_rx) * 1000 > self.cfg.stale_ms)):
                        await self.cancel_market(m)
                    if not m.closed and now > m.end + 2:
                        m.closed = True
                        m.stop.set()
                        asyncio.create_task(self.settle(session, m))
                    if m.closed and now > m.end + 900:
                        self.markets.pop(cid, None)
                if now - last_status >= 30:
                    last_status = now
                    for m in self.markets.values():
                        if m.closed:
                            continue
                        s = m.acct["shadow"]
                        print(f"[status] {m.slug} bb={m.book.best_bid()} ba={m.book.best_ask()} lag_ms={m.lag_ms:.0f} "
                              f"shadow_inv=({s.inv[True]:.0f},{s.inv[False]:.0f}) gate={self.gate_on()} "
                              f"hist={len(self.shadow_hist)}", flush=True)
                await asyncio.sleep(1)

    async def main(self):
        tasks = [self.lifecycle()]
        if self.cfg.guard_on:
            tasks.append(self.lead_ws())
        if self.live is not None:
            tasks.append(self.user_ws())
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    cfg = Config()
    print(f"deep-maker bot mode={cfg.mode} back={cfg.back_ticks} size={cfg.size} shadow_lat={cfg.paper_latency_ms}ms "
          f"gate_k={cfg.gate_k} use_gate={cfg.use_gate}", flush=True)
    asyncio.run(Bot(cfg).main())

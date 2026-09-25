"""Live executor via the official py-clob-client (post-only GTC limit BUY orders).

Requires env: POLY_PRIVATE_KEY, POLY_FUNDER (proxy wallet), POLY_SIG_TYPE. Run in AWS eu-west-1 (Dublin): the engine is in London, but UK IPs are close-only on the API.
Blocking HTTP calls run in a thread pool so the event loop keeps processing market data.

Correctness details handled here:
- place/cancel race: a cancel issued while a placement is still in flight waits for that placement, then cancels
  the resulting order id (no orphan orders).
- fills (from the user websocket, see run.py) reduce the tracked open size; fully filled orders are forgotten so
  the strategy can re-quote.
- tick size / neg-risk are passed explicitly to avoid two extra HTTP round trips per order.
"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


class LiveExec:
    def __init__(self, cfg):
        from py_clob_client.client import ClobClient  # lazy import: paper mode needs no keys
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        self.OrderArgs, self.OrderType, self.Opts = OrderArgs, OrderType, PartialCreateOrderOptions
        self.client = ClobClient(HOST, key=os.environ[cfg.pk_env], chain_id=CHAIN_ID,
                                 signature_type=int(os.environ.get(cfg.sig_type_env, "1")),
                                 funder=os.environ[cfg.funder_env])
        self.creds = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(self.creds)
        self.pool = ThreadPoolExecutor(8)
        self.open = {}      # (market, side_up) -> dict(order_id, price, size, filled)
        self.pending = {}   # (market, side_up) -> asyncio.Task of an in-flight placement
        self.errors = 0

    async def _run(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *a)

    def working(self, market, side_up, now_ms):
        key = (market, side_up)
        if key in self.pending and not self.pending[key].done():
            # treat an in-flight order as working at its intended price (prevents duplicate placements)
            return [self.pending[key].intent]
        o = self.open.get(key)
        return [o] if o else []

    async def place(self, market, token_id, side_up, price, size):
        key = (market, side_up)

        async def _do():
            tick = "0.01" if 0.04 <= price <= 0.96 else "0.001"
            args = self.OrderArgs(token_id=token_id, price=float(price), size=float(size), side="BUY")
            signed = await self._run(lambda: self.client.create_order(args, self.Opts(tick_size=tick, neg_risk=False)))
            resp = await self._run(lambda: self.client.post_order(signed, self.OrderType.GTC, post_only=True))
            oid = (resp or {}).get("orderID") or (resp or {}).get("orderId")
            if oid and (resp or {}).get("success", True):
                self.open[key] = dict(order_id=oid, price=price, size=size, filled=0.0)
            else:
                self.errors += 1
            return resp

        task = asyncio.create_task(_do())
        task.intent = dict(order_id=None, price=price, size=size, filled=0.0)
        self.pending[key] = task
        try:
            return await task
        except Exception as ex:  # noqa: BLE001
            self.errors += 1
            print("place error", repr(ex)[:160], flush=True)
        finally:
            if self.pending.get(key) is task:
                self.pending.pop(key, None)

    async def cancel(self, market, side_up):
        key = (market, side_up)
        t = self.pending.get(key)
        if t is not None and not t.done():
            try:
                await t
            except Exception:  # noqa: BLE001
                pass
        o = self.open.pop(key, None)
        if o and o.get("order_id"):
            try:
                await self._run(self.client.cancel, o["order_id"])
            except Exception as ex:  # noqa: BLE001
                self.errors += 1
                print("cancel error", repr(ex)[:160], flush=True)

    def on_fill(self, order_id, size):
        """Returns (market, side_up) of the filled order, or None."""
        for key, o in list(self.open.items()):
            if o["order_id"] == order_id:
                o["filled"] += size
                if o["filled"] >= o["size"] - 1e-9:
                    self.open.pop(key, None)
                return key
        return None

    async def cancel_all(self):
        self.open.clear()
        await self._run(self.client.cancel_all)

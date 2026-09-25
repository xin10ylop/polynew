"""Live executor via the official py-clob-client (post-only GTC limit BUY orders).

Requires env: POLY_PRIVATE_KEY, POLY_FUNDER (proxy wallet), POLY_SIG_TYPE. Run on a VPS close to AWS eu-west-2
(London). Blocking HTTP calls are pushed to a thread pool so the event loop keeps processing market data.
Fills are read from the authenticated user websocket (see run.py)."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


class LiveExec:
    def __init__(self, cfg):
        from py_clob_client.client import ClobClient  # imported lazily: paper mode needs no key
        from py_clob_client.clob_types import OrderArgs, OrderType
        self.OrderArgs, self.OrderType = OrderArgs, OrderType
        key = os.environ[cfg.pk_env]
        self.client = ClobClient(HOST, key=key, chain_id=CHAIN_ID, signature_type=int(os.environ.get(cfg.sig_type_env, "1")),
                                 funder=os.environ[cfg.funder_env])
        self.creds = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(self.creds)
        self.pool = ThreadPoolExecutor(8)
        self.open = {}   # (market, side_up) -> dict(order_id, price, size)

    async def _run(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *a)

    def working(self, market, side_up, now_ms):
        o = self.open.get((market, side_up))
        return [o] if o else []

    async def place(self, market, token_id, side_up, price, size):
        args = self.OrderArgs(token_id=token_id, price=float(price), size=float(size), side="BUY")
        signed = await self._run(self.client.create_order, args)
        resp = await self._run(lambda: self.client.post_order(signed, self.OrderType.GTC, post_only=True))
        oid = (resp or {}).get("orderID") or (resp or {}).get("orderId")
        if oid:
            self.open[(market, side_up)] = dict(order_id=oid, price=price, size=size)
        return resp

    async def cancel(self, market, side_up):
        o = self.open.pop((market, side_up), None)
        if o:
            await self._run(self.client.cancel, o["order_id"])

    async def cancel_all(self):
        self.open.clear()
        await self._run(self.client.cancel_all)

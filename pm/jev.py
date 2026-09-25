"""Minimal async client for TypeSafe Jev (System One) via OpenRouter or TypeSafe directly.

Env:
  OPENROUTER_API_KEY -> POST https://openrouter.ai/api/alpha/decisions (model "~typesafe/jev-latest")
  TYPESAFE_API_KEY   -> POST https://api.typesafe.ai/v1/systemone     (model "jev-latest")
Jev returns calibrated typed answers (choice / score / noul). Per TypeSafe's own guidance it is weak at
arithmetic, so all numeric work happens in code and Jev only sees pre-bucketed, semantic state.
"""
import asyncio
import json
import os
import time

import aiohttp


def _load_dotenv(path=".env"):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _endpoint():
    _load_dotenv()
    if os.environ.get("OPENROUTER_API_KEY"):
        return ("https://openrouter.ai/api/alpha/decisions", os.environ["OPENROUTER_API_KEY"],
                os.environ.get("JEV_MODEL", "~typesafe/jev-latest"))
    if os.environ.get("TYPESAFE_API_KEY"):
        base = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
        return (f"{base}/v1/systemone", os.environ["TYPESAFE_API_KEY"], os.environ.get("JEV_MODEL", "jev-latest"))
    return None


def available():
    return _endpoint() is not None


class Jev:
    def __init__(self, concurrency=8, timeout=10):
        ep = _endpoint()
        if ep is None:
            raise RuntimeError("set OPENROUTER_API_KEY or TYPESAFE_API_KEY")
        self.url, self.key, self.model = ep
        self.sem = asyncio.Semaphore(concurrency)
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.session = None
        self.calls = 0
        self.input_tokens = 0
        self.lat = []

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(trust_env=True, timeout=self.timeout,
                                             headers={"Authorization": f"Bearer {self.key}",
                                                      "Content-Type": "application/json"})
        return self

    async def __aexit__(self, *a):
        await self.session.close()

    async def ask(self, state, questions):
        body = {"model": self.model, "state": state, "questions": questions}
        async with self.sem:
            for attempt in range(5):
                t0 = time.time()
                async with self.session.post(self.url, data=json.dumps(body)) as r:
                    if r.status in (429, 529, 500, 502, 503):
                        await asyncio.sleep(0.5 * 2 ** attempt)
                        continue
                    txt = await r.text()
                    if r.status != 200:
                        raise RuntimeError(f"jev {r.status}: {txt[:300]}")
                    d = json.loads(txt)
                    self.calls += 1
                    self.lat.append(time.time() - t0)
                    self.input_tokens += d.get("usage", {}).get("input_tokens", 0)
                    return d["answers"]
        raise RuntimeError("jev: retries exhausted")


# ---- question battery for BTC up/down (semantic, pre-bucketed state; numbers stay in code) ----

def bucket(x, edges, labels):
    for e, l in zip(edges, labels):
        if x < e:
            return l
    return labels[-1]


def make_state(feat):
    """feat: dict of numeric features computed in code -> semantic state for Jev."""
    z = feat["z"]
    return {
        "market": "Bitcoin Up/Down binary option; settles Up if the 60-second average BTC price at window end "
                  "is at or above the 60-second average price at window start",
        "time_left": bucket(feat["tau"], [20, 60, 120, 240, 600], ["final seconds", "final minute", "1-2 minutes",
                                                                   "2-4 minutes", "4-10 minutes", "over 10 minutes"]),
        "price_vs_strike": bucket(z, [-2, -1, -0.3, 0.3, 1, 2], ["far below", "clearly below", "slightly below",
                                                                 "at the strike", "slightly above", "clearly above",
                                                                 "far above"]),
        "settlement_average_locked": bucket(feat.get("locked_frac", 0), [0.01, 0.34, 0.67], ["not started", "partly",
                                                                                           "mostly", "almost fully"]),
        "momentum_30s": bucket(feat["r30_sig"], [-1.5, -0.5, 0.5, 1.5], ["strongly falling", "falling", "flat",
                                                                        "rising", "strongly rising"]),
        "momentum_5m": bucket(feat["r300_sig"], [-1.5, -0.5, 0.5, 1.5], ["strongly falling", "falling", "flat",
                                                                        "rising", "strongly rising"]),
        "taker_flow_60s": bucket(feat["flow60"], [-0.3, -0.1, 0.1, 0.3], ["heavy selling", "net selling", "balanced",
                                                                         "net buying", "heavy buying"]),
        "volatility_regime": bucket(feat["vol_ratio"], [0.7, 1.3, 2.0], ["calm", "normal", "elevated", "extreme"]),
    }


QUESTIONS = {
    "up": {"type": "noul", "instructions": "Will this market settle Up?",
           "criteria": {"true": "The end-of-window average finishes at or above the strike",
                        "false": "The end-of-window average finishes below the strike"}},
    "regime": {"type": "choice", "instructions": "Which regime best describes current BTC price action?",
               "criteria": {"trend": "Directional move likely to continue", "chop": "Noisy mean-reverting action",
                            "shock": "Sudden abnormal move, unstable"}},
}

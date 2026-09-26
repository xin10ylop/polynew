"""Paper bot for the weekly 'What price will Bitcoin hit' strategy (REPORT.md section 4g). No keys, no money.

  python -m bot.hitbot              # run forever: a pass every 5 min; each strike acts at most once per 6 h
  python -m bot.hitbot --once       # a single pass
  python -m bot.hitbot --summary    # paper positions and P&L so far

Model and rule are the ones backtested in research/r40_hit.py and r41_hit_portfolio.py:
  q = P(touch before the end) = min(1, 2 * P(T4 > z * sqrt(2))),  z = |ln(X / S)| / sqrt(var_1m * minutes_left)
  var_1m = EWMA (1-day half-life) of Binance BTCUSDT 1-minute log-return^2; S = last closed 1-minute close.
  Decision: cost = order-book mid + 2c; buy NO if (1 - q) - cost - fee > 0.10, only with >= 2 days left in the week.
  (YES buys and weekend entries did not hold up out-of-sample; see REPORT.md 4g. HIT_SIDES=YES,NO re-enables YES.)
  Size: $250 per clip, at most $1,000 per strike, at most one clip per strike per 6 h (first after 1 h of market life).
Paper fills walk the real order book for the clip; a clip is skipped if its average price is more than 3c above the
mid. Positions are held to resolution and settled from the market's own outcome."""
import argparse
import json
import math
import os
import time
from datetime import datetime, timezone

import requests

GAMMA = "https://gamma-api.polymarket.com/events"
CLOB = "https://clob.polymarket.com"
KLINES = "https://data-api.binance.vision/api/v3/klines"  # Binance market data; not geo-restricted
SERIES_ID = 10151  # bitcoin-hit-price-weekly


def _env(name, default):
    return type(default)(os.environ.get(name, default))


CFG = dict(clip=_env("HIT_CLIP", 250.0), max_pos=_env("HIT_MAX", 1000.0), thr=_env("HIT_THR", 0.10),
           cost=_env("HIT_COST", 0.02), max_slip=_env("HIT_MAX_SLIP", 0.03), every_h=_env("HIT_EVERY_H", 6.0),
           min_age_h=_env("HIT_MIN_AGE_H", 1.0), min_days_left=_env("HIT_MIN_DAYS_LEFT", 2.0),
           sides=_env("HIT_SIDES", "NO"), log_dir=_env("HIT_LOG_DIR", "logs/hitbot"))
S = requests.Session()


# ----------------------------------------------------------------------------- helpers
def get(url, params=None, tries=5):
    for k in range(tries):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + 2 * k)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            time.sleep(1 + k)
    raise RuntimeError(f"GET failed: {url} {params}")


def fee(p):
    return 0.07 * p * (1 - p)  # Polymarket crypto taker fee per share


def t4_sf(x):
    """Survival function of Student-t with 4 degrees of freedom (closed form)."""
    t2 = x * x
    return 0.5 - 0.5 * x * (6 + t2) / (4 + t2) ** 1.5 if x >= 0 else 1 - t4_sf(-x)


def now_s():
    return time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------------- BTC data and model
def klines(start_ms):
    out, t = [], int(start_ms)
    while True:
        k = get(KLINES, {"symbol": "BTCUSDT", "interval": "1m", "startTime": t, "limit": 1000})
        if not k:
            break
        out += k
        if len(k) < 1000:
            break
        t = int(k[-1][0]) + 60_000
    return [(int(x[0]), float(x[2]), float(x[3]), float(x[4]), int(x[6])) for x in out]  # open, high, low, close, close_ms


class BTC:
    """Last 8+ days of closed 1-minute candles, EWMA variance (1-day half-life), running highs/lows."""

    def __init__(self, since_ms):
        now_ms = now_s() * 1000
        k = [c for c in klines(min(since_ms, now_ms - 8 * 86400_000)) if c[4] < now_ms]  # closed candles only
        self.k = k
        a = 1 - 0.5 ** (1 / 1440)
        var, prev = 0.0, None
        for c in k:
            if prev is not None:
                r = math.log(c[3] / prev)
                var = (1 - a) * var + a * (r * r if abs(r) <= 0.05 else 0.0)
            prev = c[3]
        self.var = var
        self.S = k[-1][3]
        self.t_last = k[-1][4] / 1000

    def extreme_since(self, start_s, up):
        xs = [c[1] if up else c[2] for c in self.k if c[0] >= start_s * 1000]
        return (max(xs) if up else min(xs)) if xs else None

    def q_touch(self, X, end_s):
        minutes = max(1.0, (end_s - now_s()) / 60)
        z = abs(math.log(X / self.S)) / math.sqrt(self.var * minutes)
        return min(1.0, 2 * t4_sf(z * math.sqrt(2)))


# ----------------------------------------------------------------------------- markets and books
def parse_strike(q):
    import re
    m = re.search(r"\$([\d,.]+)\s*([kK])?", q)
    x = float(m.group(1).replace(",", ""))
    return x * 1000 if m.group(2) else x


def active_markets():
    out = []
    for e in get(GAMMA, {"series_id": SERIES_ID, "closed": "false", "limit": 20}):
        for m in e.get("markets", []):
            if m.get("closed") or not m.get("acceptingOrders", True):
                continue
            yes, no = json.loads(m["clobTokenIds"])
            q = m["question"]
            out.append(dict(cid=m["conditionId"], q=q, X=parse_strike(q), up="dip" not in q.lower(),
                            start=datetime.fromisoformat((m.get("startDate") or e["startDate"]).replace("Z", "+00:00")).timestamp(),
                            end=datetime.fromisoformat((m.get("endDate") or e["endDate"]).replace("Z", "+00:00")).timestamp(),
                            yes=yes, no=no, event=e["slug"]))
    return out


def book(token):
    b = get(f"{CLOB}/book", {"token_id": token})
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
    return bids, asks


def vwap_buy(asks, usd):
    """Walk the asks to spend `usd`; returns (shares, average price) or (0, None) if the book is too thin."""
    sh, spent = 0.0, 0.0
    for p, s in asks:
        take = min(s, (usd - spent) / p)
        sh += take
        spent += take * p
        if spent >= usd - 1e-6:
            return sh, spent / sh
    return 0.0, None


def market_outcome(cid):
    m = get("https://gamma-api.polymarket.com/markets", {"condition_ids": cid})
    if not m or not m[0].get("closed"):
        return None
    p = json.loads(m[0].get("outcomePrices") or "[]")
    return float(p[0]) if p else None


# ----------------------------------------------------------------------------- state and logging
class State:
    def __init__(self, d):
        os.makedirs(d, exist_ok=True)
        self.path = os.path.join(d, "state.json")
        self.log_path = os.path.join(d, "events.jsonl")
        self.s = json.load(open(self.path)) if os.path.exists(self.path) else {"pos": {}, "settled": {}}

    def save(self):
        tmp = self.path + ".tmp"
        json.dump(self.s, open(tmp, "w"), indent=1)
        os.replace(tmp, self.path)

    def log(self, kind, **kw):
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"t": int(now_s()), "k": kind, **kw}) + "\n")


# ----------------------------------------------------------------------------- one pass
def run_once(st):
    mkts = active_markets()
    if mkts:
        btc = BTC(min(m["start"] for m in mkts) * 1000)
        print(f"[{iso(now_s())}] BTC {btc.S:,.0f}  1m vol {math.sqrt(btc.var) * 1e4:.2f}bp  markets {len(mkts)}", flush=True)
    for m in mkts:
        p = st.s["pos"].setdefault(m["cid"], {"q": m["q"], "event": m["event"], "end": m["end"], "usd": 0.0,
                                              "fees": 0.0, "sh_yes": 0.0, "sh_no": 0.0, "last": 0})
        age_h = (now_s() - m["start"]) / 3600
        if (age_h < CFG["min_age_h"] or now_s() - p["last"] < CFG["every_h"] * 3600
                or m["end"] - now_s() < CFG["min_days_left"] * 86400):
            continue
        ext = btc.extreme_since(m["start"], m["up"])
        if ext is not None and ((m["up"] and ext >= m["X"]) or (not m["up"] and ext <= m["X"])):
            continue  # already touched: the market resolves YES
        bids, asks = book(m["yes"])
        if not bids or not asks:
            continue
        mid = (bids[0][0] + asks[0][0]) / 2
        if not 0.003 < mid < 0.997:
            continue
        q = btc.q_touch(m["X"], m["end"])
        e_yes = q - (mid + CFG["cost"]) - fee(mid)
        e_no = (1 - q) - ((1 - mid) + CFG["cost"]) - fee(1 - mid)
        side = "YES" if e_yes > CFG["thr"] else ("NO" if e_no > CFG["thr"] else None)
        if side not in CFG["sides"].split(","):
            side = None
        st.log("decide", cid=m["cid"], q_mkt=m["q"], S=btc.S, X=m["X"], mid=mid, model=q, e_yes=e_yes, e_no=e_no,
               side=side, pos_usd=p["usd"])
        p["last"] = int(now_s())
        if side is None:
            continue
        usd = min(CFG["clip"], CFG["max_pos"] - p["usd"])
        if usd < 5:
            continue
        token_asks = asks if side == "YES" else book(m["no"])[1]
        sh, avg = vwap_buy(token_asks, usd)
        ref = mid if side == "YES" else 1 - mid
        if avg is None or avg > ref + CFG["max_slip"]:
            st.log("skip_slippage", cid=m["cid"], side=side, avg=avg, ref=ref)
            continue
        f = sh * fee(avg)
        p["usd"] += usd
        p["fees"] += f
        p["sh_yes" if side == "YES" else "sh_no"] += sh
        st.log("fill", cid=m["cid"], q_mkt=m["q"], side=side, usd=usd, shares=sh, avg=avg, fee=f, mid=mid, model=q)
        print(f"  PAPER BUY {side:3s} ${usd:.0f} @ {avg:.3f} ({sh:.0f} sh) | {m['q'][:60]} | model {q:.3f} mid {mid:.3f}",
              flush=True)
    # settle positions whose market is no longer active (touched early, or the week ended)
    active = {m["cid"] for m in mkts}
    for cid, p in list(st.s["pos"].items()):
        if cid in active:
            continue
        if p["usd"] <= 0:
            del st.s["pos"][cid]
            continue
        y = market_outcome(cid)
        if y is None:
            continue  # closed for trading but not yet resolved
        payout = p["sh_yes"] * y + p["sh_no"] * (1 - y)
        pnl = payout - p["usd"] - p["fees"]
        st.s["settled"][cid] = {**p, "yes_final": y, "pnl": pnl, "settled_at": int(now_s())}
        del st.s["pos"][cid]
        st.log("settle", cid=cid, q_mkt=p["q"], yes_final=y, pnl=pnl)
        print(f"  SETTLED {p['q'][:60]} -> {'YES' if y > 0.5 else 'NO'}  PnL {pnl:+.2f}", flush=True)
    st.save()


def summary(st):
    print(f"config: {CFG}")
    tot_open, mtm = 0.0, 0.0
    print("\nOPEN POSITIONS")
    for cid, p in st.s["pos"].items():
        if p["usd"] <= 0:
            continue
        try:
            m = get("https://gamma-api.polymarket.com/markets", {"condition_ids": cid})[0]
            yes = json.loads(m["clobTokenIds"])[0]
            bids, asks = book(yes)
            mid = (bids[0][0] + asks[0][0]) / 2 if bids and asks else float(json.loads(m["outcomePrices"])[0])
        except Exception:  # noqa: BLE001
            mid = None
        val = p["sh_yes"] * mid + p["sh_no"] * (1 - mid) if mid is not None else float("nan")
        tot_open += p["usd"] + p["fees"]
        mtm += val - p["usd"] - p["fees"]
        print(f"  {p['q'][:58]:58s} YES {p['sh_yes']:6.0f} NO {p['sh_no']:6.0f} cost ${p['usd'] + p['fees']:7.2f} "
              f"now {mid if mid is not None else float('nan'):.3f} m2m {val - p['usd'] - p['fees']:+8.2f}")
    print(f"  total cost ${tot_open:,.2f}, mark-to-market {mtm:+,.2f}")
    print("\nSETTLED BY WEEK")
    weeks = {}
    for p in st.s["settled"].values():
        w = weeks.setdefault(p["event"], [0.0, 0.0, 0])
        w[0] += p["usd"] + p["fees"]
        w[1] += p["pnl"]
        w[2] += 1
    for ev, (usd, pnl, n) in sorted(weeks.items()):
        print(f"  {ev:60s} strikes {n:2d} cost ${usd:8.2f} PnL {pnl:+9.2f}")
    print(f"  total settled PnL {sum(w[1] for w in weeks.values()):+,.2f}")
    print("\nbacktest reference at this sizing (NO only, >= 2 days left; Jul 2025 - Sep 2026, 60 weeks): "
          "about +$1,100 to +$1,400/week on average, 23% losing weeks, worst week about -$2,000.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    st = State(CFG["log_dir"])
    if a.summary:
        return summary(st)
    print(f"hitbot PAPER mode {CFG}", flush=True)
    while True:
        try:
            run_once(st)
        except Exception as ex:  # noqa: BLE001 - keep running; errors are logged
            st.log("error", err=repr(ex)[:300])
            print("error:", repr(ex)[:300], flush=True)
        if a.once:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()

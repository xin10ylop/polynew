"""Summarize the bot's logs (paper shadow and live):   python -m bot.summary   [log_dir]

Reads every logs/bot/bot_*.jsonl, including those from earlier runs and restarts.
Settled window PnL in the logs excludes the maker rebate. This script estimates the rebate
(20% of the taker fee on our fills) and adds it, so the totals compare with the backtest in REPORT.md."""
import collections
import glob
import json
import math
import sys
import time

from pm.fees import taker_fee_per_share

WINDOWS_PER_DAY = 288
BACKTEST = "backtest b3_g300 @10-share clips, Sep 10-23 (incl. rebate): +$0.46/window @20 ms, +$0.24/window @50 ms"


def load(log_dir):
    settles, fills, guards = {}, collections.defaultdict(list), 0
    for path in sorted(glob.glob(f"{log_dir}/bot_*.jsonl")):
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line cut off by a restart
                k = r.get("k")
                if k == "settle" and "shadow_pnl" in r:  # older log formats are skipped
                    settles[r["slug"]] = r
                elif k == "fill" and "ex" in r:
                    fills[(r["ex"], r["slug"])].append(r)
                elif k == "guard":
                    guards += 1
    return settles, fills, guards


def stats(xs):
    n = len(xs)
    if n == 0:
        return dict(n=0, sum=0.0, mean=0.0, sd=0.0, t=0.0)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return dict(n=n, sum=sum(xs), mean=m, sd=sd, t=m / sd * math.sqrt(n) if sd > 0 else 0.0)


def report(ex, settles, fills):
    pnl_key = f"{ex}_pnl"
    rows = []
    for slug, s in sorted(settles.items(), key=lambda kv: kv[1]["t"]):
        fs = fills.get((ex, slug), [])
        reb = sum(0.2 * taker_fee_per_share(f["px"]) * f["sz"] for f in fs)
        sh = {True: 0.0, False: 0.0}
        cost = {True: 0.0, False: 0.0}
        for f in fs:
            sh[f["up"]] += f["sz"]
            cost[f["up"]] += f["px"] * f["sz"]
        rows.append(dict(slug=slug, t=s["t"], gate=s.get("gate"), pnl=s[pnl_key], reb=reb, tot=s[pnl_key] + reb,
                         up=sh[True], dn=sh[False], cup=cost[True], cdn=cost[False]))
    traded = [r for r in rows if r["up"] + r["dn"] > 0]
    print(f"\n=== {ex.upper()} ===")
    if not rows:
        print("no settled windows yet")
        return
    span_h = (rows[-1]["t"] - rows[0]["t"]) / 3.6e6
    st = stats([r["tot"] for r in rows])
    shares = sum(r["up"] + r["dn"] for r in rows)
    print(f"settled windows: {len(rows)} over {span_h:.1f} h   (traded: {len(traded)})")
    print(f"PnL excl. rebate: {sum(r['pnl'] for r in rows):+.2f}   est. rebate: {sum(r['reb'] for r in rows):+.2f}")
    print(f"TOTAL incl. rebate: {st['sum']:+.2f}   per window: {st['mean']:+.3f} (sd {st['sd']:.2f}, t={st['t']:.2f})"
          f"   -> ~{st['mean'] * WINDOWS_PER_DAY:+.0f} $/day if it holds")
    if shares:
        print(f"shares filled: {shares:.0f} ({shares / len(rows):.1f}/window)   "
              f"PnL per share filled: {100 * st['sum'] / shares:+.2f} c")
    paired = [r for r in traded if r["up"] > 0 and r["dn"] > 0]
    if paired:
        pc = [r["cup"] / r["up"] + r["cdn"] / r["dn"] for r in paired]
        print(f"windows with both sides filled: {len(paired)}   avg Up+Down pair cost: {sum(pc) / len(pc):.3f}")
    unp = [abs(r["up"] - r["dn"]) for r in traded]
    if unp:
        print(f"unpaired shares at settlement: avg {sum(unp) / len(unp):.1f}/traded window, max {max(unp):.0f}")
    wins = sum(r["tot"] > 0 for r in traded)
    losses = sum(r["tot"] < 0 for r in traded)
    print(f"winning/losing traded windows: {wins}/{losses}   best {max(r['tot'] for r in rows):+.2f}"
          f"   worst {min(r['tot'] for r in rows):+.2f}")
    gated = [r for r in rows if r["gate"]]
    if ex == "shadow":
        g = stats([r["tot"] for r in gated])
        print(f"only windows where the regime gate was ON: {g['n']} windows, total {g['sum']:+.2f}, "
              f"per window {g['mean']:+.3f}")
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[time.strftime("%Y-%m-%d", time.gmtime(r["t"] / 1000))].append(r["tot"])
    print("per UTC day:  " + "   ".join(f"{d}: {sum(v):+.2f} ({len(v)}w)" for d, v in sorted(by_day.items())))
    print("last 12 windows: " + " ".join(f"{r['tot']:+.2f}" for r in rows[-12:]))


def main():
    log_dir = sys.argv[1] if len(sys.argv) > 1 else "logs/bot"
    settles, fills, guards = load(log_dir)
    print(BACKTEST)
    print(f"BTC lead-guard pulls: {guards}")
    report("shadow", settles, fills)
    if any(ex == "live" for ex, _ in fills) or any(s.get("live_pnl") for s in settles.values()):
        report("live", settles, fills)


if __name__ == "__main__":
    main()

# Deep-maker bot: Polymarket BTC Up/Down 5m

> **Read `REPORT.md` first.** The edge is real in backtests on archived real order books, but:
> - it needs **~20–50 ms** from a book event to the order reaching the matching engine;
> - it is **regime-dependent**: some days lose, hence the daily loss limit and a quick stop rule;
> - it has **not** yet been proven with real money.
>
> Start with paper mode on the real host, then trade tiny size.

## What it does

For the current and next 5-minute BTC window:

- It rests a **post-only bid 2 ticks behind the best bid on both sides** (Up and Down).
- It re-prices on every book change.
- **Fills** come mostly from large market orders that sweep several levels and then partly revert.
- **Profit** comes from locking Up+Down pairs for less than $1 (backtest average ≈ $0.974 at 20 ms), plus the 20% maker fee rebate.
- **Inventory** stays balanced: the cap on |Up − Down| counts resting orders as if they had already filled.
- Positions are held to resolution. A pair always pays exactly $1.

## Safety layers (all on by default)

| layer | what it does |
|---|---|
| Regime gate (optional, off by default) | A shadow paper engine trades every window. With `BOT_USE_GATE=1`, live quoting is on only while the shadow's mean PnL over the last 12 settled windows is > 0. It is off by default because it cut PnL for this variant on Sep 10–23 (REPORT.md §4d); it is still logged. |
| BTC lead guard | If Bybit BTC perp moves ≥ 0.5 bp within 500 ms, pull the side that the move makes stale for 2 s. |
| Feed-lag guard | Pull all quotes if market data arrives > 250 ms late (clock-corrected). |
| Stale-feed guard | Pull all quotes if a market's feed is silent for > 2 s. This covers frozen books and reconnects. |
| Inventory caps | Imbalance cap (in-flight orders included), plus a $150 cost cap per market. |
| Daily loss halt | Stop when live realized PnL ≤ −$50 (`BOT_DAILY_LOSS`). |
| Kill switch | `touch KILL` cancels everything and stops quoting. |

## Hosting (matters more than anything else)

- Polymarket's CLOB runs in AWS eu-west-2 (London), but **UK IP addresses are close-only on the Polymarket API** (no new orders). Run the bot in **eu-west-1 (Dublin)**, which is ~1–2 ms away and API-allowed. Step-by-step: `DEPLOY_AWS.md`.
- Measured on this project's Dublin instance (c7i-flex.large, 2026-09-25): REST round trip p50 22.7 ms (min 20.8), market feed jitter p90 27.7 ms (p99 ~59 ms above min). Both are inside the backtest's 20–50 ms band.
- From a generic cloud box, this project measured 300–480 ms and multi-second feed lags. At those latencies the strategy **loses** (−2¢/share at 100 ms).
- Confirm your jurisdiction is allowed to trade on Polymarket.

## Run

```bash
pip install -r requirements.txt py-clob-client
# paper (no keys needed): real-time data, simulated queue-aware fills
BOT_MODE=paper BOT_PAPER_LAT_MS=20 python -m bot.run
# live (tiny size first!)
export POLY_PRIVATE_KEY=...   POLY_FUNDER=<your Polymarket proxy wallet>   POLY_SIG_TYPE=1
BOT_MODE=live BOT_SIZE=5 BOT_MAX_IMB=15 BOT_DAILY_LOSS=20 python -m bot.run
```

Logs go to `logs/bot/*.jsonl`, with events `fill`, `guard`, `settle` (shadow and live PnL, and the gate state at window open).

Summary of all runs so far (PnL incl. estimated maker rebate, per-day totals, pair cost, gate-on windows), compared with the backtest:

```bash
python -m bot.summary
```

## Known limitations / TODO before scaling

- **Python.** The hot path is Python with py-clob-client over HTTP.
  - For the ~20 ms regime, pre-sign orders at the next few price levels, reuse HTTP connections, or port the hot path to Rust or Go.
  - Measure your real event-to-ack latency first. The backtest cliff is sharp: +1.4¢ at 20 ms, ≈0 at 50 ms, −2¢ at 100 ms per share.
- **Live fills.** Live fill handling parses the user-channel `trade` events (`maker_orders`). Verify the field names against your account's messages in paper-plus-live shadow before sizing up.
- **Redemption.** Resolved positions need redeeming, or merge pairs early to recycle capital. That is not automated here.
- **Rate limits.** The bot sends about 3–5 order+cancel requests per second per market. The per-signer limits are 60/s orders and 50/s cancels, sustained.

# Polymarket BTC Up/Down (5m / 15m): what works, what doesn't, and why

Research period: data from Aug 7 to Sep 25, 2026.
Every claim below survived realistic execution testing; everything that didn't is listed with the reason it failed.

**Testing standard.** A strategy counts only if it survives:
1. strict historical execution (fills only from real trades that happened *after* our order could have arrived, with corrected timestamps);
2. a replay on **real archived order books** with queue position, latency and depth;
3. per-date consistency plus a check on the most recent regime.

No real money was traded. That is the final step, and it needs the right host (see §6).

## TL;DR

1. **Taking liquidity doesn't work** for anyone slower than ~1 second.
   - That covers every model tried: the TWAP fair-value model, LightGBM, Jev, copy-trading, cross-market signals and the bots from the videos.
   - Polymarket's price is a better forecaster than any public-data model, and the fee (up to 1.75¢/share) eats the rest.
2. **Jev (TypeSafe System One) adds no edge here** in any of the three roles tested: direct forecaster, the video bots' designs, and regime gate.
   - It is fast (≈230 ms p50 via OpenRouter) and cheap.
   - But this is a numeric microstructure problem, and TypeSafe itself says Jev is weak at numbers. Its probabilities were badly overconfident.
3. **The edge that did exist: a *fast* "deep liquidity" maker.**
   - Rest bids 2 ticks behind the best bid on *both* Up and Down, re-price within ~20 ms, get filled by sweeping market orders that revert, and lock Up+Down pairs below $1.
   - On real archived order books, Aug 19 – Sep 6 (639 windows), this earned **+2.2¢/share (t 9.5) at 20 ms**.
   - **It has decayed:** Sep 10 – 21 (423 windows) came in at **−1.9¢/share (t −4.8)**, and Sep 25 at −2.4¢.
   - A BTC-move-guarded variant is still slightly positive recently (+1.7¢/share, t 1.5), which is **not significant** and is tiny in dollars.
   - The regime gate does not rescue the recent period.
4. **After adapting to the new regime, a small edge survives on unseen data.**
   - Quote **3 ticks** behind on both sides, with a BTC guard that pulls quotes on ≥0.3 bp moves within 300 ms.
   - Out-of-sample over Sep 18, 21 and 23 it made **+3.0¢/share at 20 ms** (t 1.7, bootstrap P(≤0) = 5%) and +1.6¢ at 50 ms. It was positive on all three days.
   - It is only about **$0.45 per window at 10-share clips (~$130/day)**. It is real but modest, and fragile to latency and regime (§4c).
   - Over a month (§4d), with all losing windows and days included, that is about **+$2.6k (50 ms) to +$3.9k (20 ms) at 10-share clips**, *if the Sep 10–23 regime persists*. The regime gate hurts this variant, so it is now off by default.
   - **Withdrawn (§4e):** those figures assumed the bot sees Binance BTC trades with 0 ms delay. Measured from Dublin, the delay is ~200 ms under load, and at that delay the strategy loses money.
5. **Deliverable:** `bot/` is a paper/live implementation of exactly the backtested logic, with the gate, BTC lead guard, feed-lag and stale-feed guards, and inventory, dollar and daily-loss caps.
   - It must run in **AWS eu-west-1 (Dublin)**, next to the London engine. UK IPs are close-only on Polymarket's API, so London itself can't place orders.
   - From this cloud container the feed arrived 0.3–10 s late, which is exactly the failure mode that kills makers.

## 1. Market mechanics (as of Sept 2026)

- **Settlement.** Up iff the Chainlink BTC/USD **60 s TWAP** at the end of the window is ≥ the 60 s TWAP at its start.
  - Applies to 5m (since Aug 14) and 15m (since Aug 7) windows.
  - `eventMetadata.priceToBeat` / `finalPrice` are published per event.
- **Fees.**
  - Taker: `0.07·p·(1−p)` per share.
  - Maker: 0, plus a rebate of 20% of the taker fees on your fills.
- **Taker speed bump.** Crypto taker orders are held 250 ms → **50 ms (Aug 17)** → **150 ms (Sep 4)** and cannot be cancelled while held. The engine is in AWS eu-west-2.
- **Rate limits (per signer, sustained):** 60 orders/s and 50 cancels/s. Minimum 5 shares; tick 0.01 (0.001 near 0/1).
- **Data trap.**
  - Data-API trade timestamps are **~2.2 s later than the real match** (range 1.1–3.1 s), measured against the live websocket.
  - A backtest that fills at a print "1 s after the signal" is really filling at a price from *before* the signal.
  - This single effect produced every early "edge" in this project.
- **Chainlink stream.** Polymarket RTDS streams Chainlink BTC/USD at 1/s with ~1.4 s delay. From it I reproduced the official strike to within ~0.3 bp.
- **Liquidity.** A 5m window trades ~$15–90k; a 15m window $20–450k. Top of book usually holds 5–400 shares with a 1-tick spread.

## 2. Data

| dataset | coverage |
|---|---|
| Gamma markets with exact TWAP strike/final | 12,251 × 5m, 4,756 × 15m |
| Polymarket taker trade tapes (with wallets) | 18.1M (5m) + 4.1M (15m) fills |
| Binance BTCUSDT spot 1 s; USD-M futures 1 s and ms | Aug 6 – Sep 24 |
| **Archived real L2 books** (PendulumFlow, free) | ~100 h in 9 blocks from Aug 19 to Sep 25: full book snapshots, every price change, every trade (ms) |
| Own live recording | Polymarket CLOB, Chainlink RTDS, Coinbase, Bybit, Kraken, Hyperliquid (Sep 25) |

The archive was validated against the official trade tape: 95–96% of trades are present, with no duplicates.

## 3. Everything that failed (and why)

| idea | best "paper" result | result under realistic fills | why it fails |
|---|---|---|---|
| TWAP fair-value model, taker | +2 to +8¢/sh | ≈0 at 4–5 s delay; + only at ~1 s | pure latency race; the market already prices the TWAP maths |
| LightGBM fair value (futures lead, flow, market price) | +3.8¢/sh, one 10-day window | +0.5 ± 0.55¢ over 4 folds (2 negative) | overfit a drift regime |
| Start-of-window offset / final-minute TWAP lock | positive at prints | ≈0; 0 mispriced moments on live books | priced in |
| 5m ↔ 15m shared-settlement relative value | +0.3–0.9¢ at prints | not executable | 15m is the better-informed book |
| Copy-trading profitable wallets (out-of-sample) | wallets keep +1.7¢ | copies −0.8 to −1.3¢ | their edge is timing |
| Kalshi price as signal (dropped: user can't use Kalshi) | — | −2 to −10¢ | noisy prints |
| Post-expiry leftovers | — | 42 of 12k windows | resolution anomalies |
| Hour-of-day pockets | — | don't persist out-of-sample | noise |
| Pre-window market making (5m) | makers +0.6–1.2¢ on average | −0.1 to −0.9¢ for a new order in the queue | new orders get filled only when the level is swept |
| Naive join-best-bid maker @150 ms | — | −0.98¢/sh (t −9.4) | picked off |

### Jev (TypeSafe) and the bots from the videos

- **Latency.** Measured ~230 ms p50 (200 ms min) via OpenRouter, even with 12 questions or a 300-number state in one call. Cost ≈ $0.00002–0.00007 per decision.
- **As a forecaster** (3,000 historical 5m points):
  - market log-loss **0.387**;
  - quant model 0.403;
  - Jev with the market price bucket 0.472;
  - Jev blind 0.517.
  - Stacked on market + model, Jev makes cross-validated log-loss worse (0.3871 → 0.3893).
- **Faithful replications** (300 real windows, 12,600 Jev calls, strict fills):
  - **jevAgentDev/jev-polymarket-trading** (conf > 0.9, ask ≤ 0.70, edge ≥ 0.10): only **27%** of its FOK orders would fill; −0.3¢ ± 5.8.
  - **jarrodwatts/jev-trader** state (CVD, returns, mids): +3.5¢ ± 4.1, not significant.
  - **5-output vote:** −2.8¢ ± 4.4.
  - Jev's raw probabilities were overconfident: log-loss 1.02–1.05 versus 0.57 for the market.
- **As a regime gate** for the maker: AUC 0.48, worse than random. It traded the worse half: +$4.01/window traded versus +$6.28 on windows it skipped.
- **The BTC/Alpaca "Jev loop"** (Lewis Jackson / Roan): the prompt is email-gated and has no public repo, so it was not tested. It is long-only BTC spot every 3–5 s, which Alpaca fees would eat.

**Verdict on "Jev + Opus".**
- **Opus 5.5** did the part that mattered: generating and killing hypotheses, building strict simulators, and catching data traps (timestamp lag, archive gaps, feed lag, paper-mode clock bias).
- **Jev** is the wrong tool for this numeric problem. Its speed is irrelevant without an information edge.
- The Jev client (`pm/jev.py`) and all Jev experiments are in the repo for re-testing on future Jev versions.

## 4. The edge that survives: fast "deep liquidity" market making (5m)

**Mechanism**
- Top-of-book is thin, so large market orders **sweep several levels at once** and then partially revert.
- A bot resting **2 ticks behind** the best bid on both outcomes gets filled mainly by those sweeps.
- Because it re-prices within ~20–50 ms, it steps away from *gradual* informed repricing. That drift is what killed the user's earlier bot: bids below the ask lost −4.6 to −7.4¢/share because they filled only when price drifted down to them.
- Every Up+Down pair pays exactly $1, and **the profit is the locked pair discount**.

**Where the profit comes from** (117 random windows, 2 behind @20 ms):
- Markout (value of what we bought minus fill price) is **+2.0¢/share at 1 s, 5 s and 30 s**, and **+1.4¢ at resolution**. There is no adverse drift after fills.
- **Locked pairs made +$385** (average pair cost **$0.974**). Unpaired leftover inventory made −$68, which is noise.

**Latency cliff** (same windows):

| reaction latency | markout @1 s | avg pair cost | PnL/share |
|---|---|---|---|
| **20 ms** | **+1.98¢** | **0.974** | **+1.39¢** |
| 50 ms | −0.21¢ | 0.992 | −0.04¢ |
| 100 ms | −2.35¢ | 0.997 | −2.04¢ |

**Robustness checks**
- **Doubling the queue ahead of every order** (to simulate faster competitors) barely changes results: +2.78 → +2.71¢ at 20 ms. Most fills are sweeps *through* the level.
- **Order traffic** is ~3–5 orders+cancels/s per market, far under the rate limits.
- **Latency:** Sep 6 (150 ms taker-delay era), with the BTC guard, still made **+1.3¢ at 100 ms** (2 behind + guard 300 ms/0.3 bp) and +1.9–2.1¢ at 50 ms.

**Per-date results so far** (2 behind @20 ms, PnL/share incl. rebate):

| block | 08-19 | 08-23 | 08-24 | 08-28 | 09-02 | 09-06 | 09-25 (today) |
|---|---|---|---|---|---|---|---|
| PnL/share | +2.6¢ | +2.4¢ | +2.2¢ | +0.4¢ | +1.9¢ | +2.8¢ | **−2.4¢** |

*(The full all-blocks grid, including Sep 10, 15–16 and 21, is in §4b when complete.)*

**Regime gate** (causal: trade a window only if the shadow sim's mean PnL over the previous 12 windows is > 0):

| strategy | ungated $/window | gated $/window | skipped windows $/window | Sep 25 ungated → gated |
|---|---|---|---|---|
| 2 behind @20 ms | +3.32 | **+5.07** | −1.88 | −$402 → **−$43** |
| 2 behind @50 ms | +0.45 | **+2.49** | −2.37 | −$892 → **$0** |

### 4b. Final all-blocks grid (921 + 141 windows, 11 date blocks)

PnL per share filled, including the maker rebate:

| strategy | latency | Aug 19 – Sep 6 | Sep 10 – Sep 21 | Sep 25 |
|---|---|---|---|---|
| 2 behind | 20 ms | **+2.22¢ (t 9.5)** | **−1.88¢ (t −4.8)** | −2.4¢ |
| 2 behind + BTC guard (300 ms / 0.3 bp) | 20 ms | +2.47¢ (t 6.9) | +1.73¢ (t 1.5) | n/a (no ms BTC data yet) |
| 1 behind + BTC guard (500 ms / 0.5 bp) | 20 ms | +1.57¢ (t 8.0) | −0.09¢ | — |
| 2 behind | 50 ms | +0.73¢ (t 3.5) | −2.42¢ (t −10.5) | −2.9¢ |
| 2 behind + BTC guard | 50 ms | +1.75¢ (t 4.6) | +0.02¢ | — |
| any variant | 100 ms | ≤ 0 | < 0 | < 0 |

Per-date, 2 behind @20 ms:

| Aug 19 | Aug 23 | Aug 24 | Aug 28 | Sep 2 | Sep 6 | Sep 10 | Sep 11 | Sep 15 | Sep 16 | Sep 21 | Sep 25 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| +2.6¢ | +2.4¢ | +2.2¢ | +0.7¢ | +1.9¢ | +2.8¢ | −0.5¢ | −4.6¢ | −2.6¢ | −1.7¢ | −2.4¢ | −2.4¢ |

- Adding a regime gate (lag 2, K 12) on Sep 10+ gives 2 behind @20 ms −0.57¢/share, and the guarded variant +0.98¢ (t 0.6). Not enough.
- **Interpretation.** The deep fills are now adversely selected, while the BTC guard still protects. So sweeps became *informed* after the taker delay rose to 150 ms (Sep 4). Plausibly, fast takers now sweep deeper to guarantee fills after the delay, and more fast makers compete for the reverting flow.

### 4c. Regime adaptation: train on Sep 10–16, test on unseen Sep 18 / 21 / 23

**Training.** 9 variants were tried on Sep 10–16 (282 windows). The deep fills that decayed can be protected in two ways: quote **deeper** (3 ticks behind), or cap bids at the TWAP fair value. Both need the BTC guard (300 ms / 0.3 bp). The best training results @20 ms:

| variant | ¢/share | $/window | t |
|---|---|---|---|
| 3 behind + guard | +4.4 | +0.47 | 1.9 |
| 2 behind + guard + fair cap | +4.1 | +0.56 | 1.7 |

Everything @50 ms was ≈ 0.

**Out-of-sample.** Three candidates were frozen before looking at the test days. Results on unseen Sep 18, 21 and 23 (396 windows):

| variant | latency | ¢/share | $/window (10-share clips) | t | P(mean ≤ 0), bootstrap | per day (18 / 21 / 23) |
|---|---|---|---|---|---|---|
| **3 behind + guard** | **20 ms** | **+3.0** | **+0.45** | 1.7 | **4.9%** | +0.2 / +8.6 / +1.3 ¢ |
| 3 behind + guard | 50 ms | +1.6 | +0.40 | 1.3 | 9.6% | 0.0 / +4.3 / +1.1 ¢ |
| 3 behind + guard + fair cap | 20 ms | +3.3 | +0.25 | 1.0 | — | +4.0 / +4.8 / +1.9 ¢ |
| 2 behind + guard + fair cap | 20 ms | +0.1 | +0.02 | 0.1 | — | — |
| 3 behind + guard, 100-share clips | 20 ms | +1.6 | +2.21 | 0.7 | — | — |

**Conclusion.**
- A small, **marginally significant** maker edge survives on recent unseen data. It is positive on every unseen day at both 20 and 50 ms.
- In dollars it is modest: about **$0.45 per 5-minute window at 10-share clips**, or roughly $130/day across all 288 windows.
- Size: the per-share edge in the simulator is about the same at 10, 50 and 100-share clips on matched days (§4d). Dollar PnL, variance and drawdowns all scale roughly with size. How real sweeps react to larger resting orders is not modelled, so scaling is unproven live.
- It is latency-bound (≤50 ms), and the market has already changed regime once in September.
- Sep 25 could not be tested: Binance ms BTC data is published the next day.

These are the defaults now in `bot/config.py` (`BOT_BACK_TICKS=3`, guard 300 ms / 0.3 bp, size 10).

**The final variant across every tested day** (3 behind + BTC guard, 10-share clips; per window = per 5-minute market):

| day | 20 ms ¢/share | 20 ms $/window | shares filled/window | 50 ms ¢/share | 50 ms $/window |
|---|---|---|---|---|---|
| Aug 19 | +2.1 | +1.71 | 81 | +2.2 | +1.54 |
| Aug 23 | +4.7 | +2.71 | 58 | +1.0 | +0.55 |
| Aug 24 | +7.4 | +2.84 | 38 | +6.5 | +2.02 |
| Aug 28 | +4.6 | +1.35 | 29 | +4.5 | +1.06 |
| Sep 02 | +4.1 | +2.46 | 60 | +3.6 | +1.98 |
| Sep 06 | +3.7 | +2.91 | 80 | +3.1 | +2.15 |
| Sep 10 | +4.8 | +0.62 | 13 | +1.5 | +0.42 |
| Sep 11 | +13.6 | +0.67 | 5 | +3.7 | +0.53 |
| Sep 15 | +8.9 | +0.54 | 6 | +4.8 | +0.46 |
| Sep 16 | +1.6 | +0.19 | 12 | −3.9 | −0.82 |
| Sep 18* | +0.2 | +0.03 | 17 | 0.0 | −0.01 |
| Sep 21* | +8.6 | +1.03 | 12 | +4.3 | +0.83 |
| Sep 23* | +1.3 | +0.22 | 17 | +1.1 | +0.30 |

(* = unseen test days)

| period | latency | ¢/share | $/window | t | ≈ $/day if all 288 windows traded |
|---|---|---|---|---|---|
| Aug 19 – Sep 6 | 20 ms | +3.8 | +2.25 | 6.8 | ~$650 |
| Sep 10 – Sep 23 | 20 ms | +3.5 | +0.46 | 2.5 | ~$130 |
| Aug 19 – Sep 6 | 50 ms | +3.1 | +1.58 | 5.2 | ~$455 |
| Sep 10 – Sep 23 | 50 ms | +1.0 | +0.24 | 1.1 | ~$70 |

**Reading.**
- At 20 ms the **per-share edge is stable** (~3.5–3.8¢) and positive on all 13 days.
- But the **fills collapsed about 4–5× after Sep 6**, from ~59 to ~13 shares per window, so dollars per window fell about 5×.
- At 50 ms the recent edge is not significant.
- It is regime-dependent in *size*, and at 50 ms also in *sign*.

**BTC 15m check.** The same frozen variants were run on 220 recent 15m windows (Sep 10–23).
- 3 behind + guard: +1.4¢/share but only **+$0.08 per window** (t 0.3, ~6 shares filled per window). The sign flips from day to day.
- Best variant, 3 behind + guard + fair cap @50 ms: +$0.23 per window (t 0.95).
- **No usable edge on 15m.** 15m takers are better informed (see §3.5), so 5m is the only venue with a maker edge.

### 4d. A month of the final variant: losses, bankroll, size, regime gate (`research/r35_month_sim.py`)

**Month simulation.**
- Setup: 3 behind + guard, 10-share clips.
  - Each of 30 days draws one real day from Sep 10–23.
  - Its 288 windows are resampled from that day's backtested windows (losing windows included, maker rebate included).
  - The −$50 daily halt is applied, and ~$70 of AWS cost is subtracted.

| latency | median month | 10th pct | 90th pct | losing days | worst day (1st pct) | max drawdown (median / 95th pct) |
|---|---|---|---|---|---|---|
| 20 ms | **+$3,860** | +$3,000 | +$4,720 | 19% | −$60 | $144 / $227 |
| 50 ms | **+$2,600** | +$1,750 | +$3,510 | 34% | −$65 | $201 / $338 |

- The simulation captures *bad luck within the current regime*, not a regime change. After Sep 6, dollar PnL per window fell about 5× within days.
- Nor does it capture the difference between simulated and real fills. Only a live test measures that.
- Plan for materially less than the table, and stop quickly if live results turn negative.

**Regime gate on this variant (real window order, lag 2).**
- The gate *cuts* PnL:
  - 20 ms: ungated +$310 on 678 windows, versus +$136 to +$188 gated for K = 12…144.
  - 50 ms: ungated +$164, versus −$48 to +$8 gated.
- It trades only ~30% of windows and mostly misses the good ones: losing windows cluster weakly, so a rolling-mean filter lags the regime.
- The bot therefore defaults to `BOT_USE_GATE=0`. The gate is still computed and logged.

**Size scaling on matched days** (the simulator fills the full size only when the queue ahead is consumed or the level trades through):

| days | latency | clip | $/window | ¢/share | t | worst window |
|---|---|---|---|---|---|---|
| Sep 10–16 | 20 ms | 10 | +0.47 | +4.4 | 1.9 | −$13 |
| Sep 10–16 | 20 ms | 50 | +2.07 | +4.2 | 1.8 | −$76 |
| Sep 10–16 | 20 ms | 100 | +4.01 | +4.4 | 1.8 | −$164 |
| Sep 10–16 | 50 ms | 10 / 50 / 100 | +0.02 / +0.05 / −0.30 | ≈ 0 | ≈ 0 | −$25 / −$133 / −$269 |
| Sep 18 + 23 | 20 ms | 10 / 100 | +0.13 / +2.21 | +0.8 / +1.6 | 0.4 / 0.7 | −$23 / −$233 |

- In the simulator, per-share edge does not shrink with size. (An earlier note said it "halves at 100 shares"; that compared different day sets.)
- Risk scales with size too. Bankroll needed, roughly two concurrent windows at the 99th percentile of inventory plus the 95th-percentile drawdown:
  - ~$600 at 10-share clips;
  - ~$2.5–3k at 50;
  - ~$5–6k at 100. Raise `BOT_MAX_USD` and `BOT_DAILY_LOSS` in proportion.
- Real capacity depends on how many sweeps reach 3 ticks deep and on how other bots react to larger resting orders. Neither is in the data, so size up only in steps validated live.

### 4e. Reality check: BTC feed delay from Dublin (Sep 25, after the paper test)

**Paper test.**
- The Dublin paper bot lost $39 over its first 39 windows (−$1.01 per window, 13.8 shares per window, Up+Down pair cost $1.22).
- That prompted a check of an assumption in §4c/§4d: the backtest's BTC guard read Binance trades at their *exchange* timestamp, i.e. with **0 ms** delay.

**Measured on the Dublin server** (`bot/lead_probe.py`, receive time minus trade time, 30 s while BTC was active):

| venue | p50 | p90 |
|---|---|---|
| Bybit perp (the bot's feed) | 234 ms | 400 ms |
| Binance perp / spot | 204 / 177 ms | 337 / 301 ms |
| OKX perp | 126 ms | 342 ms |
| Coinbase spot | 48 ms | 52 ms |
| Bitstamp / Kraken spot (thin) | 26 / 13 ms | 37 / 18 ms |

In a quiet moment Bybit showed 95 ms. Under activity, which is exactly when the guard matters, it is 230–400 ms.

**Backtest with realistic guard delays** (678 windows Sep 10–23, 3 behind, 10-share clips, $ per window incl. rebate; `ld` = Binance delay, `cld` = Coinbase delay, lat = order/cancel latency):

| guard | lat 20 | lat 30 | lat 50 |
|---|---|---|---|
| Binance 0 ms (original assumption) | +0.46 | +0.46 | +0.24 |
| Binance 100 ms | +0.37 | | +0.12 |
| Binance 150 ms | +0.38 | | −0.02 |
| **Binance 230 ms (≈ what the bot really has)** | | **−0.09** | **−0.73** |
| Coinbase 0 ms | | +0.07 | −0.44 |
| Coinbase 50 ms | | −0.02 | −0.47 |
| Binance 200 + Coinbase 50 | | +0.11 | −0.09 |
| no guard | −1.77 | | −3.92 |

**Conclusion.**
- The toxic sweeps are predicted by **Binance** moves specifically: Coinbase, even at zero delay, does not protect.
- From Dublin, Binance data arrives ~200 ms late under load. At that delay the deep maker **loses** (−$0.09 to −$0.73 per window). The §4c/§4d profit estimates assumed an impossible guard latency and are withdrawn.
- The paper loss is consistent with this.
- The BTC 5m/15m markets also carry **no liquidity-rewards pool** (CLOB `/rewards/markets/<cid>` is empty), so there is no quoting subsidy to lean on.

### 4f. Last rescue attempts for the fast maker, and a slow calibration scan (Sep 25)

**Polymarket book-momentum guard** (pull the side a ≥1–2 tick mid move makes stale; sees fast makers' repricing with ~10 ms feed delay). 678 windows Sep 10–23, $ per window:

| guard | lat 30 | lat 60 |
|---|---|---|
| Binance 230 ms (what the bot has) | −0.09 | −1.46 |
| book 1 tick / 300 ms | −0.50 | −0.74 |
| book 2 ticks / 300 ms | −0.71 | −1.43 |
| book 1 tick / 1 s | −0.41 | −0.64 |
| book 1 tick + Coinbase 50 ms | +0.15 (t 1.0) | +0.19 (t 0.9) |
| 4 behind + book 1 tick | −0.21 | −0.77 |

- No variant is significantly profitable at a latency this setup can reach.
- **The fast deep-maker is closed.**

**Slow calibration scan** (`r36`, `r37`): is the price at a fixed time before expiry calibrated against the outcome?
- **On trade prices** (11,763 windows):
  - 5m favorites at 80–93¢ with 20–60 s left seem to win 2–5¢ more often than priced.
- **On executable L2 asks** (1,372 archived windows, buying the favorite at its best ask, fee included):
  - The same cells give **+0.5¢, t 0.4**. The apparent edge was trade prices lagging the book during moves.
  - No price/time cell is significant after costs.
- The 5m book is calibrated for a slow taker.

## 4g. A slow edge: weekly "Will Bitcoin reach / dip to $X" markets (`r40`, `r41`)

**Market.**
- Series `bitcoin-hit-price-weekly` (Polymarket series 10151), about 15 strikes per week.
- YES if any Binance BTCUSDT 1-minute candle from market creation to Sunday 11:59 PM ET has High ≥ X (reach) or Low ≤ X (dip).
- About $200k traded per day. Taker fee 0.07·p(1−p).

**Model.**
- P(touch before the end) = min(1, 2·P(T₄ > b/(σ√τ))), where:
  - b = |ln(X/S)|;
  - T₄ is Student-t(4) scaled to unit variance;
  - σ = EWMA (1-day half-life) of Binance 1-minute return variance.
- It uses only candles closed before the decision.
- Tails and half-life were chosen on the first half of weeks (log-loss); the second half is out-of-sample.

**Rule.**
- Every 6 h, for each strike not yet hit: buy the side whose model value exceeds its cost by > 10¢. Cost = mid + half spread + slippage + taker fee.
- 85% of the dollars go to NO: the market **overprices touch probability**, most of all for strikes near 50¢ early in the week.
- Positions are held to resolution.

**Data.**
- 902 markets, 63 weeks (Jul 2025 – Sep 2026).
- CLOB midpoint history. The live check confirms it is the book midpoint.
- Outcomes rebuilt from Binance 1-minute candles; 99% match the market's own resolutions.

**Out-of-sample results** (second half: 31 weeks, per share, costs included):

| entry delay after signal | ¢/share | week-clustered t |
|---|---|---|
| 5 min | +18.4 | 5.0 |
| 30 min | +16.5 | 4.4 |
| 60 min | +12.5 | 3.5 |

**Robustness.**
- Positive in every quarter: 2025Q3 +16.1, Q4 +18.6, 2026Q1 +17.8, Q2 +20.9, Q3 +15.0¢/share.
- Positive in falling, flat and rising weeks. Strongly rising weeks (> +5%) are about flat.
- Dose-response: model edge 10–15¢ → +5.9¢ realised; 15–20¢ → +11.7¢; 20–30¢ → +25.6¢; > 30¢ → +35.6¢.
- The naive "always buy NO at mid-range" rule does **not** work; the model is needed.

**Executability.** Real NO buys in these markets (27 sampled markets, 2,256 trades, mid-range periods) paid:
- a median of +1.0¢ above (1 − mid);
- +2.0¢ at the 75th percentile;
- +3.6¢ at the 90th percentile.

The simulation charges 2.0¢ (and 3.5¢ in the harsh run). NO-buy volume in those periods was about $15k per market.

**Portfolio simulation** (`r41`: $250 clips every 6 h, max $1,000 per strike):
- **Out-of-sample:** +$1,157 per week (t 4.1), 9 losing weeks of 30, worst week −$1,957.
- **Capital in use:** $2.6k on average, $5.5k at the 95th percentile, $8k peak.
- **With 3.5¢ total costs:** +$1,105 per week.
- **With $500 clips / $3,000 per strike:** +$2,399 per week, $5.7k average capital, $22k peak.

**Refinement: NO only, ≥ 2 days left.** Chosen after the split, but it agrees in both halves and matches the mechanism.
- YES buys: train +20.4¢, test **−2.1¢**.
- Weekend entries: train +9.1¢, test −6.4¢. The model ignores quieter weekends.
- NO with ≥ 2 days left: train +17.5¢ (t 2.9), test **+23.5¢ (t 5.0)**.

Portfolio ($250 / $1,000, 2¢ costs):

| | per week | t | losing weeks | worst week |
|---|---|---|---|---|
| train | +$1,168 | 3.7 | 7 of 30 | |
| test | **+$1,399** | 5.4 | 7 of 30 | −$1,966 |
| test, harsh costs (3.5¢) | +$1,270 | 5.6 | | −$1,320 |

- Capital in use: $2.4k mean, $8k peak.
- Recent months: 2026-06 +$5,955; 07 +$5,789; 08 +$8,514; 09 (3 weeks to Sep 20) +$168.
- This is the rule in `bot/hitbot.py`.

**Caveats.**
- This is a behavioural mispricing in a retail market. It can shrink if others trade it.
- One bad week can cost about one to two good weeks.
- It needs real capital: about $5–8k to run the $250/$1,000 sizing.
- The monthly series (14 months) is not reliable and is not used.

## 5. Deliverable: `bot/`

- It uses exactly the backtested quoting logic and the same queue-aware fill model (paper mode).
- Live mode sends post-only GTC orders via the official `py-clob-client`.
- The regime gate runs off a continuous shadow simulation.
- Guards: BTC lead (Bybit perp), feed lag > 250 ms, silent feed > 2 s, imbalance cap counting in-flight orders, $ cap per window, daily loss halt, `KILL` file.
- Paper test in this container:
  - The software works end to end: discovery, websocket book, quoting, queue fills, settlement.
  - It showed feed lags of **2.5–10 s** here, so it is useless as a *strategy* test. The feed-lag guard correctly refuses to quote.
- See `bot/README.md` for deployment.

## 6. What must happen before real money

1. **Host in AWS eu-west-1 (Dublin; UK IPs are close-only on the API, see `DEPLOY_AWS.md`).** Measure the real event→order-ack latency. If it isn't under ~30–50 ms, don't run it.
2. Run **paper mode on that host** for several days, comparing shadow PnL with the archive backtest.
3. Go live with **5-share clips** and a $20 daily-loss limit. Compare live fills with the shadow fills of the same windows; they should match.
4. Only then size up. The edge is a liquidity premium with finite capacity, and other fast makers compete for it.

## 7. Repository map

- `pm/`: data pipeline, TWAP model, strict backtester, live recorder, live book reconstruction, queue-aware maker simulator (`makersim.py`), archive extractor (`archive.py`), Jev client (`jev.py`).
- `research/r01…r33`: every experiment in this report, numbered.
- `bot/`: the deployable deep-maker bot.

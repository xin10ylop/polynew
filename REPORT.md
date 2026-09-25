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
   - It is only about **$0.45 per window at 10-share clips (~$130/day)** and does not scale linearly. It is real but modest, and fragile to latency and regime (§4c).
5. **Deliverable:** `bot/` is a paper/live implementation of exactly the backtested logic, with the gate, BTC lead guard, feed-lag and stale-feed guards, and inventory, dollar and daily-loss caps.
   - It must run in or next to **AWS eu-west-2 (London)**.
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
- It **does not scale linearly**: per-share edge halves at 100-share clips out-of-sample.
- It is latency-bound (≤50 ms), and the market has already changed regime once in September.
- Sep 25 could not be tested: Binance ms BTC data is published the next day.

These are the defaults now in `bot/config.py` (`BOT_BACK_TICKS=3`, guard 300 ms / 0.3 bp, size 10).

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

1. **Host in AWS eu-west-2 (London).** Measure the real event→order-ack latency. If it isn't under ~30–50 ms, don't run it.
2. Run **paper mode on that host** for several days, comparing shadow PnL with the archive backtest.
3. Go live with **5-share clips**, a $20 daily-loss limit and the gate on. Compare live fills with the shadow fills of the same windows; they should match.
4. Only then size up. The edge is a liquidity premium with finite capacity, and other fast makers compete for it.

## 7. Repository map

- `pm/`: data pipeline, TWAP model, strict backtester, live recorder, live book reconstruction, queue-aware maker simulator (`makersim.py`), archive extractor (`archive.py`), Jev client (`jev.py`).
- `research/r01…r33`: every experiment in this report, numbered.
- `bot/`: the deployable deep-maker bot.

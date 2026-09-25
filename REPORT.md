# Polymarket BTC Up/Down (5m / 15m): research report (interim)

**Status:** in progress. The live recorders and the order-book archive extraction are still running.
This report lists every result so far, including the ones that failed and why.

**Testing standard.** A strategy counts only if it survives, in order:
1. strict historical execution (a real print that happened *after* our order could have arrived, using corrected timestamps);
2. a replay against real order books with queue position, latency and depth;
3. a small live trial.

Nothing in this report has passed stage 3 yet.

## 1. How these markets actually work (Sept 2026)

- **Settlement.** Up wins iff Chainlink BTC/USD **60-second TWAP** at window end is ≥ the 60s TWAP at window start.
  - This applies to both 5m and 15m markets (TWAP since Aug 7/14).
  - `eventMetadata.priceToBeat` and `finalPrice` are exposed for every closed event.
  - Sniping the last second is dead, because an average cannot be moved in an instant.
- **Fees (`crypto_fees_v2`).**
  - Takers pay `0.07·p·(1−p)` per share: 1.75¢ at p=0.5, 0.2¢ at p=0.97.
  - Makers pay 0 and receive a rebate of 20% of taker fees.
  - Tick is 0.01 (0.001 near the extremes); minimum order is 5 shares.
- **Timestamps.** The data-API trade timestamps are **~2.2s later than the real match time** (range 1.1–3.1s). This was measured against the live websocket.
  - Any backtest that fills at a print "1 second after the signal" is actually filling at a price from *before* the signal.
  - This single effect produced every early "edge" found in this project.
- **Live data sources.** Polymarket RTDS streams Chainlink BTC/USD at 1/s with ~1.4s delay.
  - Using that stream, I reproduce the official `priceToBeat` to within ~0.3bp.
  - A typical 5m move is ~5bp.
- **Liquidity.** On Polymarket, 5m windows trade about $15–50k and 15m windows $20–450k.
  - Kalshi's KXBTC15M (same 60s-average structure, same 15-minute boundaries, CF BRTI index) trades **2–2.8M contracts per window**.

## 2. Data built

| dataset | size |
|---|---|
| Gamma markets (exact TWAP strike/final) | 12,251 × 5m (since Aug 14), 4,756 × 15m (since Aug 7) |
| Polymarket taker trade tapes | 18.1M (5m) + 4.1M (15m) fills, with wallets |
| BTC 1s | Binance spot klines + Binance USD-M futures (from aggTrades), Aug 6 – Sep 24 |
| Kalshi KXBTC15M | 4,702 settled windows + trade tapes for sampled windows |
| Live recording (running) | Polymarket CLOB book deltas + trades, Chainlink RTDS, Coinbase, Bybit, Kraken, Hyperliquid, Kalshi books (0.5s) |
| Historical L2 (extracting) | PendulumFlow free archive: full book events for BTC up/down, 96 hours |

## 3. Results

### 3.1 TWAP fair-value model (Binance → Chainlink basis-corrected)

- The model is well calibrated.
  - For 5m windows it already carries information at window open (log-loss 0.676 vs 0.693 for a coin flip), because spot at the open differs from the TWAP strike.
- **But the market price is a better forecaster than the model at every point of the window** (5m log-loss: market 0.387, model 0.403).
- As a taker, the naive print-based backtest showed +2 to +8¢/share.
  - With corrected timestamps (execution print must come after order arrival), the edge **goes to about 0** at 4–5s delays.
  - It **stays positive only at ~1s real execution**. That is an HFT speed race.

### 3.2 ML fair value (LightGBM: futures lead, order flow, multi-horizon returns, market price and flow)

- One held-out 10-day window looked great: 15m +3.8¢/share, t≈4 under strict fills.
- **Four-fold weekly walk-forward:** +0.5¢ ± 0.55¢. Two folds lost money, and the model's log-loss was worse than the market's in 3 of 4 folds.
- **Verdict: not robust.** The single-window result was luck, with calendar features overfitting a drift regime.

### 3.3 Jev (TypeSafe System One) via OpenRouter

- **Latency:** measured ~230ms p50 (200ms min) with a warm connection. That holds even with 12 questions or a 300-number state in one call.
- **Cost:** ~$0.00002–0.00007 per decision.
- **Forecasting quality (3,000 historical 5m decision points):**

| predictor | log-loss | Brier |
|---|---|---|
| market price | **0.387** | **0.128** |
| quant TWAP model | 0.403 | 0.133 |
| Jev + market bucket | 0.472 | 0.154 |
| Jev blind | 0.517 | 0.171 |

- **Stacked on market + model,** Jev adds nothing. Cross-validated log-loss worsens from 0.3871 to 0.3893.
- **Faithful replications of the bots from the user's videos** (300 real windows, 12,600 Jev calls):

| bot | dry-run PnL/share | real fill rate | strict PnL/share |
|---|---|---|---|
| jevAgentDev/jev-polymarket-trading (conf>0.9, ask≤0.70, edge≥0.10, ≥90s) | +0.7¢ ± 3.0 | **27%** | −0.3¢ ± 5.8 |
| jarrodwatts/jev-trader state (CVD, returns, mids) | −0.5¢ ± 2.9 | 48% | +3.5¢ ± 4.1 (n.s.) |
| 5-output vote (learnwithmeai style) | −2.7¢ ± 2.9 | 42% | −2.8¢ ± 4.4 |

- Jev's raw probabilities are badly overconfident on this task: log-loss 1.02–1.05, versus 0.57 for the market on the same points.
- The Lewis Jackson BTC/Alpaca "Jev loop" prompt is behind an email form at part-timequant.com/jev. It has no public repo, so it was not tested.

### 3.4 Other angles that failed strict testing

| angle | result |
|---|---|
| Start-of-window TWAP offset | priced in (≈0 at realistic delays) |
| Final-60s "TWAP lock" | priced in, both historically and on live books (0 mispriced moments in 11 windows) |
| 5m ↔ 15m shared-settlement relative value | the 15m market is right when they disagree; trading 5m off 15m gives +0.3–0.9¢ at prints (not executable) |
| Copy-trading consistently profitable wallets (out-of-sample) | the wallets keep +1.7¢/share, but copies lose −0.8 to −1.3¢ (their edge is timing) |
| Kalshi price as a signal for Polymarket | −2 to −10¢/share under strict fills (Kalshi prints are noisy) |
| Post-expiry leftovers | only 42/12k windows, ~30 min after end (resolution anomalies) |
| Hour-of-day pockets | do not persist out-of-sample |

### 3.5 Where the money goes (every taker fill has a maker)

- **15m.** Takers are informed before fees (+0.55¢/share) but lose −0.53¢ after fees. Average makers lose −0.33¢ after rebate.
- **5m.** Takers lose −0.92¢/share after fees. Average makers make **+0.13¢**, including the rebate.
- **Maker losses are concentrated right after BTC moves.**
  - For 5m, fills after a taker-favourable move (z>1 in the prior 1–3s) cost makers −1 to −7¢/share.
  - Fills after quiet or unfavourable moves earn +0.4 to +8¢.
  - A maker that avoids the first kind would keep about 72% of volume at roughly +0.8¢/share, before queue effects.

### 3.6 Open candidates (being validated on real order books)

1. **Toxicity-guarded market making (5m)**, simulated with a queue-aware replay on real books:
   - join the best bid on both sides;
   - pull the side that a lead-exchange move goes against;
   - never bid above fair value.
2. **Kalshi ↔ Polymarket 15m cross-venue pairs.**
   - Outcomes agree in 98.6% of 4,697 windows. All 66 disagreements had a TWAP move under 1bp.
   - Kalshi leads Polymarket by 1–2s.
   - Live synchronized books show occasional persistent gaps of ~10¢.
   - Caveats: this needs accounts on both venues, and jurisdiction rules may forbid it.

## 4. Honest bottom line so far

Polymarket's BTC up/down prices are efficient for anyone who is not an HFT. Every taker strategy built on public information has failed realistic execution, and so has every Jev-driven bot. Whatever edge exists belongs to (a) sub-second market makers who manage adverse selection, and (b) cross-venue players. Both are being tested on real order books before any claim is made.

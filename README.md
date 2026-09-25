# polynew — Polymarket BTC Up/Down (5m / 15m) research

Research toolkit + findings for Polymarket's BTC Up/Down markets (Chainlink 60s-TWAP settlement,
crypto_fees_v2 taker fees), with TypeSafe **Jev** integration and a strict, fill-realistic testing stack.

Layout
- `pm/` data pipeline (Gamma markets, trade tapes, Binance spot/futures 1s), TWAP fair-value model,
  strict backtester, live recorder (Polymarket CLOB + Chainlink RTDS + Coinbase/Bybit/Kraken/Hyperliquid),
  live order-book reconstruction, queue-aware maker simulator, Kalshi KXBTC15M tools, Jev client.
- `research/` numbered experiments (r01..), each prints its own results.
- `scripts/` long-running recorders.
- `data/`, `logs/`, `.env` are git-ignored (put `OPENROUTER_API_KEY=` or `TYPESAFE_API_KEY=` in `.env`).

See `REPORT.md` for findings (work in progress).

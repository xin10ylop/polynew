"""Loading helpers: markets + trade tapes joined with model features."""
import glob

import numpy as np
import pandas as pd


def load_markets(dur):
    m = pd.read_parquet(f"data/markets_{dur}.parquet")
    return m.dropna(subset=["price_to_beat", "final_price", "up_won"]).reset_index(drop=True)


def load_trades(dur, markets=None):
    files = sorted(glob.glob(f"data/trades_{dur}/*.parquet"))
    tr = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    tr = tr.drop_duplicates()
    if markets is None:
        markets = load_markets(dur)
    tr = tr.merge(markets[["condition_id", "start_ts", "end_ts", "price_to_beat", "up_won"]], on="condition_id")
    # executable side observed: BUY X at p -> X available at p; SELL X at p -> complement available at 1-p
    x_is_up = tr.outcome_idx.values == 0
    buy = tr.taker_buy.values == 1
    tr["side_up"] = np.where(buy, x_is_up, ~x_is_up).astype(np.int8)  # side we could buy is Up?
    tr["ask"] = np.where(buy, tr.price.values, 1 - tr.price.values)    # price of that side
    tr["up_price"] = np.where(x_is_up, tr.price.values, 1 - tr.price.values)  # trade price in Up terms
    tr["side_won"] = np.where(tr.side_up == 1, tr.up_won, 1 - tr.up_won).astype(np.int8)
    tr["tau"] = tr.end_ts - tr.ts            # seconds to expiry
    tr["el"] = tr.ts - tr.start_ts           # seconds since window start
    tr["notional"] = tr.price * tr["size"]
    return tr

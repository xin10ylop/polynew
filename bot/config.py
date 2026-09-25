"""Bot configuration (env-overridable). Defaults are the backtested 'back2' variant."""
import os
from dataclasses import dataclass, field


def _f(name, default):
    return type(default)(os.environ.get(name, default))


@dataclass
class Config:
    mode: str = _f("BOT_MODE", "paper")            # paper | live
    durations: tuple = ("5m",)                     # backtested edge is on 5m
    back_ticks: int = _f("BOT_BACK_TICKS", 2)      # quote this many ticks behind the best bid on each side
    size: float = _f("BOT_SIZE", 10.0)             # shares per resting order
    max_imbalance: float = _f("BOT_MAX_IMB", 30.0) # |Up shares - Down shares| cap per market
    stop_before_end_s: float = _f("BOT_STOP_S", 5.0)
    start_after_open_s: float = _f("BOT_START_S", 0.0)
    min_price: float = 0.03
    min_replace_ms: int = _f("BOT_MIN_REPLACE_MS", 20)   # debounce cancel/replace per side
    max_markets: int = 2                           # current + next window
    daily_loss_limit: float = _f("BOT_DAILY_LOSS", 50.0)  # USD; bot halts when realized PnL <= -limit
    paper_latency_ms: int = _f("BOT_PAPER_LAT_MS", 50)   # virtual order latency for paper fills
    guard_window_ms: int = _f("BOT_GUARD_W_MS", 500)    # BTC lead-move guard window
    guard_bp: float = _f("BOT_GUARD_BP", 0.5)            # pull the stale side if BTC moves >= this many bp within window
    guard_cool_ms: int = _f("BOT_GUARD_COOL_MS", 2000)   # keep that side pulled this long
    guard_on: int = _f("BOT_GUARD", 1)
    max_feed_lag_ms: int = _f("BOT_MAX_LAG_MS", 250)   # pull quotes if market data arrives later than this
    stale_ms: int = _f("BOT_STALE_MS", 2000)       # no quotes if the market feed is silent this long (frozen/stale book)
    max_usd_per_market: float = _f("BOT_MAX_USD", 150.0)  # dollar cap on cost of inventory per market
    gate_k: int = _f("BOT_GATE_K", 12)             # regime gate: mean shadow PnL of last K settled windows must be > 0
    gate_warmup_allow: int = _f("BOT_GATE_WARMUP", 0)  # 1 = allow live quoting before K shadow windows exist
    kill_file: str = "KILL"                        # create this file to stop quoting and cancel all
    log_dir: str = "logs/bot"
    # live credentials (only used in live mode) -- read from environment / .env, never hard-code
    pk_env: str = "POLY_PRIVATE_KEY"
    funder_env: str = "POLY_FUNDER"                # proxy wallet address (Polymarket account)
    sig_type_env: str = "POLY_SIG_TYPE"            # 1 = email/magic proxy, 2 = browser wallet proxy, 0 = EOA
    extra: dict = field(default_factory=dict)

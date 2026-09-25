"""Polymarket fee model (crypto_fees_v2 schedule).

fee (pUSD) = shares * rate * (p * (1 - p)) ** exponent, charged to takers only.
Makers pay nothing and receive `rebate_rate` of the taker fees collected in the market.
"""

CRYPTO_RATE = 0.07
CRYPTO_EXPONENT = 1
CRYPTO_REBATE = 0.20


def taker_fee_per_share(p, rate=CRYPTO_RATE, exponent=CRYPTO_EXPONENT):
    return rate * (p * (1.0 - p)) ** exponent


def taker_cost_per_share(p, rate=CRYPTO_RATE, exponent=CRYPTO_EXPONENT):
    """All-in cost of buying one share at price p as a taker."""
    return p + taker_fee_per_share(p, rate, exponent)


def breakeven_prob(p, rate=CRYPTO_RATE, exponent=CRYPTO_EXPONENT):
    """Win probability needed for a taker buy at p to break even."""
    return taker_cost_per_share(p, rate, exponent)


def maker_rebate_per_share(p, rate=CRYPTO_RATE, rebate=CRYPTO_REBATE, exponent=CRYPTO_EXPONENT):
    """Approximate rebate a maker earns per share filled (pro-rata share of taker fee)."""
    return rebate * taker_fee_per_share(p, rate, exponent)

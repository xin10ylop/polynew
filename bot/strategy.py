"""Deep maker quoting rule (identical to research/r30 'backN'):
bid Up at (best Up bid - N ticks), bid Down at (best Down bid - N ticks) where best Down bid = 1 - best Up ask.
Never cross (post-only), respect inventory imbalance cap, quote only inside the trading window."""
TICK = 0.01


def desired_quotes(book, inv_up, inv_dn, now_s, start_s, end_s, cfg, open_up=0.0, open_dn=0.0):
    """open_up/open_dn: size of our resting (or in-flight) orders; counted as worst-case fills for the inventory cap."""
    if not book.ready or now_s < start_s + cfg.start_after_open_s or now_s > end_s - cfg.stop_before_end_s:
        return {}
    bb, ba = book.best_bid(), book.best_ask()
    if bb is None or ba is None:
        return {}
    back = cfg.back_ticks * TICK
    out = {}
    imb_up = (inv_up + open_up) - inv_dn     # worst case if every resting Up order fills
    imb_dn = (inv_dn + open_dn) - inv_up
    p_up = round(bb - back, 2)
    p_dn = round((1 - ba) - back, 2)
    if p_up >= cfg.min_price and imb_up + cfg.size <= cfg.max_imbalance:
        out["up"] = (p_up, cfg.size)
    if p_dn >= cfg.min_price and imb_dn + cfg.size <= cfg.max_imbalance:
        out["dn"] = (p_dn, cfg.size)
    return out

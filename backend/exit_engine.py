"""
Exit Engine — satellite-position exit discipline for SMSF.

Replaces position-level tight stops with:
  1. Catastrophe stop (−20% or 2.5×ATR, whichever is wider)
  2. Thesis stop (BROKEN verdict from sentinel)
  3. Time stop (day-63 or earnings-zone-adjusted)
  4. CGT deferral override (12-month discount within 30 days)
  5. Portfolio circuit breaker (imported from circuit_breaker.py)
"""

from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple


CATASTROPHE_STOP_PCT = -0.20
CATASTROPHE_ATR_MULTIPLE = 2.5
DEFAULT_HORIZON_DAYS = 63
CGT_HOLD_DAYS = 365
CGT_DEFERRAL_WINDOW_DAYS = 30
CGT_MIN_GAIN_FOR_DEFERRAL_PCT = 8.0


def compute_exit_plan(
    symbol: str,
    entry_date: date,
    entry_price: float,
    current_price: float,
    atr_20d_pct: float = 0.03,
    days_to_earnings: Optional[int] = None,
    sentinel_verdict: str = "INTACT",
    portfolio_breaker_level: str = "NORMAL",
    risk_params_override: Optional[Dict] = None,
) -> Dict:
    days_held = (date.today() - entry_date).days
    gain_pct = (current_price / entry_price - 1) * 100

    if risk_params_override:
        cat_stop_pct = risk_params_override.get("catastrophe_stop_pct", CATASTROPHE_STOP_PCT)
        cat_atr_mult = risk_params_override.get("catastrophe_atr_multiple", CATASTROPHE_ATR_MULTIPLE)
    else:
        cat_stop_pct = CATASTROPHE_STOP_PCT
        cat_atr_mult = CATASTROPHE_ATR_MULTIPLE

    # ── Catastrophe stop: −20% or 2.5×ATR, whichever is wider ──────────────
    atr_stop = -(cat_atr_mult * atr_20d_pct)
    active_stop = min(cat_stop_pct, atr_stop)
    cat_stop_price = round(entry_price * (1 + active_stop), 3)
    cat_stop_triggered = current_price <= cat_stop_price

    # ── Horizon stop: day-63, earnings-adjusted ─────────────────────────────
    horizon_hit = False
    if days_to_earnings is not None and 0 < days_to_earnings < 14:
        horizon_hit = days_held >= (DEFAULT_HORIZON_DAYS + days_to_earnings)
    else:
        horizon_hit = days_held >= DEFAULT_HORIZON_DAYS

    # ── CGT deferral — only if profitable, near 12 months, and no other exit ─
    cgt_defer = False
    if gain_pct >= CGT_MIN_GAIN_FOR_DEFERRAL_PCT:
        days_to_discount = max(0, CGT_HOLD_DAYS - days_held)
        if 0 < days_to_discount <= CGT_DEFERRAL_WINDOW_DAYS:
            cgt_defer = True

    # ── Exit priority ───────────────────────────────────────────────────────
    exit_reason = None
    exit_signal = "HOLD"

    if portfolio_breaker_level in ("ORANGE", "RED"):
        exit_reason = f"PORTFOLIO_BREAKER_{portfolio_breaker_level}"
        exit_signal = "FORCE_EXIT"
    elif sentinel_verdict == "BROKEN":
        exit_reason = "THESIS_BROKEN"
        exit_signal = "FORCE_EXIT"
    elif cat_stop_triggered:
        exit_reason = "CATASTROPHE_STOP"
        exit_signal = "EXIT"
    elif horizon_hit:
        if cgt_defer and sentinel_verdict == "INTACT":
            exit_reason = "CGT_DEFERRAL"
            exit_signal = "HOLD_EXTEND"
        else:
            exit_reason = "TIME_STOP"
            exit_signal = "EXIT"

    return {
        "symbol": symbol,
        "days_held": days_held,
        "gain_pct": round(gain_pct, 2),
        "active_stop_pct": round(active_stop, 4),
        "cat_stop_price": cat_stop_price,
        "cat_stop_triggered": cat_stop_triggered,
        "horizon_hit": horizon_hit,
        "cgt_defer": cgt_defer,
        "cgt_days_to_discount": max(0, CGT_HOLD_DAYS - days_held) if gain_pct >= CGT_MIN_GAIN_FOR_DEFERRAL_PCT else None,
        "exit_signal": exit_signal,
        "exit_reason": exit_reason,
        "sentinel_verdict": sentinel_verdict,
        "portfolio_breaker": portfolio_breaker_level,
    }

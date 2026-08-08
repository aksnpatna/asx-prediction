"""
Calendar Gate — Month + Day-of-Week scoring gate for SMSF.

Applies seasonal multipliers based on 55,000+ data-point analysis.
Overridable by WFO gate state (bear-market flush) and macro regime.
September has a nuance override — not a binary block.
"""

from datetime import date
from typing import Dict, Optional


MONTHLY_SIGNALS: Dict[int, Dict] = {
    1:  {"signal": "STRONG",   "multiplier": 1.10, "note": "New-year capital deployment"},
    2:  {"signal": "CAUTION",  "multiplier": 0.70, "note": "Earnings-season variance"},
    3:  {"signal": "AVOID",    "multiplier": 0.40, "note": "Worst month — avg -1.7%, 42% win"},
    4:  {"signal": "STRONG",   "multiplier": 1.25, "note": "Post-earnings clean window"},
    5:  {"signal": "NEUTRAL",  "multiplier": 0.90, "note": "Pre-EOFY fade risk"},
    6:  {"signal": "CAUTION",  "multiplier": 0.75, "note": "EOFY tax-loss selling"},
    7:  {"signal": "STRONG",   "multiplier": 1.30, "note": "Best month +4.5%, 62% win"},
    8:  {"signal": "STRONG",   "multiplier": 1.15, "note": "Full-year results positive bias"},
    9:  {"signal": "CAUTION",  "multiplier": 0.60, "note": "Sep median -1.3%, 42% win"},
    10: {"signal": "NEUTRAL",  "multiplier": 0.85, "note": "Re-entry after Sep softness"},
    11: {"signal": "STRONG",   "multiplier": 1.20, "note": "Year-end positioning — strong verified"},
    12: {"signal": "MODERATE",  "multiplier": 1.05, "note": "Christmas rally"},
}

DOW_TILT: Dict[int, float] = {0: 1.0, 1: 1.10, 2: 1.05, 3: 1.00, 4: 0.80}


def get_calendar_status(
    wfo_state: str = "GREEN",
    axjo_vs_sma200_pct: float = 0.0,
    vix: float = 18.0,
    copper_gold_4w_pct: float = 0.0,
    override_date: Optional[date] = None,
) -> Dict:
    today = override_date or date.today()
    month = today.month
    dow = today.weekday()

    # ── Bear-market override — seasonal signals suppressed entirely ─────────
    if wfo_state in ("RED", "RED_MANUAL_REVIEW") and axjo_vs_sma200_pct < -0.10 and vix > 30:
        return {
            "allow_new_entries": False,
            "max_new_positions": 0,
            "cash_floor_pct": 40,
            "stop_tighten_pct": 2.0,
            "score_multiplier": 0.0,
            "reason": "BEAR_MARKET_OVERRIDE: seasonal signals suppressed",
            "calendar_signal": "SUPPRESSED",
        }

    # ── Friday block — no new satellite entries ─────────────────────────────
    if dow == 4:
        return {
            "allow_new_entries": False,
            "score_multiplier": 0.0,
            "reason": "FRIDAY_BLOCK: no new entries before weekend",
            "calendar_signal": "FRIDAY",
        }

    cfg = MONTHLY_SIGNALS[month]
    multiplier = cfg["multiplier"] * DOW_TILT.get(dow, 1.0)

    # ── September nuance — override if macro is strongly positive ───────────
    if month == 9:
        if (wfo_state == "GREEN"
                and axjo_vs_sma200_pct > 0.05
                and vix < 18
                and copper_gold_4w_pct > -3):
            return {
                "allow_new_entries": True,
                "score_multiplier": 0.90,
                "max_new_positions": 8,
                "stop_tighten_pct": 0.5,
                "reason": "SEP_OVERRIDE: macro strong enough to lift restriction",
                "calendar_signal": "CAUTION_OVERRIDE",
            }
        return {
            "allow_new_entries": True,
            "score_multiplier": 0.60,
            "max_new_positions": 3,
            "stop_tighten_pct": 1.0,
            "reason": "SEP_CAUTION: default September restriction",
            "calendar_signal": "CAUTION",
        }

    # ── March restriction ───────────────────────────────────────────────────
    if month == 3:
        return {
            "allow_new_entries": True,
            "score_multiplier": 0.40,
            "max_new_positions": 2,
            "stop_tighten_pct": 1.5,
            "reason": "MARCH_AVOID: worst month historically",
            "calendar_signal": "AVOID",
        }

    return {
        "allow_new_entries": True,
        "score_multiplier": round(multiplier, 2),
        "max_new_positions": 20,
        "stop_tighten_pct": 0.0,
        "reason": f"{cfg['signal']}: {cfg['note']}",
        "calendar_signal": cfg["signal"],
    }

"""
SMSF Three-Tier Stock Universe Configuration.

Data-driven tiers, ranked by current 90-day dollar volume.
Core / Broad / Signal tiers govern scan depth and AI-debate eligibility.
"""

import json
import os
from datetime import date
from pathlib import Path
from typing import Dict, Optional

UNIVERSE_TIERS: Dict = {
    "core": {
        "description": "Active SMSF trading pool — full-feature scan + AI debate eligible",
        "min_dollar_vol": 1_000_000,    # >$1M/day
        "full_feature_scan": True,
        "ai_debate_eligible": True,
        "max_positions_for_tier": 8,
    },
    "broad": {
        "description": "Candidate generation — daily scan, top-% feed AI debate",
        "min_dollar_vol": 500_000,
        "full_feature_scan": True,
        "ai_debate_eligible": False,    # only if score hits top-10% or sector-rotation
        "max_positions_for_tier": 4,
    },
    "signal": {
        "description": "Signal detection only — lightweight scan for breakout screening",
        "min_dollar_vol": 100_000,
        "full_feature_scan": False,     # RSI + volume + momentum only
        "ai_debate_eligible": False,
        "max_positions_for_tier": 0,
    },
}

UNIVERSE_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "universe_ranked.json")
UNIVERSE_CACHE_TTL_HOURS = 168  # 1 week


def load_ranked_universe(force_refresh: bool = False) -> Dict[str, Dict]:
    if not force_refresh and os.path.exists(UNIVERSE_CACHE_PATH):
        try:
            mtime = os.path.getmtime(UNIVERSE_CACHE_PATH)
            age_hours = (date.today().timestamp() - mtime) / 3600
            if age_hours < UNIVERSE_CACHE_TTL_HOURS:
                with open(UNIVERSE_CACHE_PATH) as f:
                    return json.load(f)
        except Exception:
            pass
    return rank_universe_from_db()


def rank_universe_from_db() -> Dict[str, Dict]:
    from sqlalchemy import text
    from main import db_conn

    ranked = {}
    try:
        with db_conn() as conn:
            rows = conn.execute(text("""
                SELECT symbol,
                       AVG(close * volume) AS dollar_vol_90d,
                       COUNT(*) AS n_days,
                       MAX(trade_date) AS last_trade,
                       AVG(close) AS avg_price
                FROM eod_ohl_history
                WHERE market = 'AU'
                  AND close > 0
                  AND trade_date >= CURRENT_DATE - INTERVAL '120 days'
                GROUP BY symbol
                HAVING COUNT(*) >= 60
                ORDER BY dollar_vol_90d DESC
            """)).fetchall()

        for symbol, adv, n_days, last_trade, avg_price in rows:
            ranked[symbol] = {
                "symbol": symbol,
                "dollar_vol_90d": round(float(adv or 0), 0),
                "n_days": n_days,
                "last_trade": str(last_trade) if last_trade else None,
                "avg_price": round(float(avg_price or 0), 4),
            }

        try:
            os.makedirs(os.path.dirname(UNIVERSE_CACHE_PATH), exist_ok=True)
            with open(UNIVERSE_CACHE_PATH, "w") as f:
                json.dump(ranked, f, indent=2)
        except Exception:
            pass

        return ranked
    except Exception as e:
        try:
            if os.path.exists(UNIVERSE_CACHE_PATH):
                with open(UNIVERSE_CACHE_PATH) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}


def classify_symbol(symbol: str, ranked: Optional[Dict] = None) -> str:
    if ranked is None:
        ranked = load_ranked_universe()
    info = ranked.get(symbol.upper(), {})
    adv = info.get("dollar_vol_90d", 0)
    if adv >= UNIVERSE_TIERS["core"]["min_dollar_vol"]:
        return "core"
    if adv >= UNIVERSE_TIERS["broad"]["min_dollar_vol"]:
        return "broad"
    if adv >= UNIVERSE_TIERS["signal"]["min_dollar_vol"]:
        return "signal"
    return "skip"


def get_universe_symbols(tier: str = "core", ranked: Optional[Dict] = None) -> list:
    if ranked is None:
        ranked = load_ranked_universe()
    min_vol = UNIVERSE_TIERS.get(tier, {}).get("min_dollar_vol", 1_000_000)
    return [s for s, info in ranked.items() if info.get("dollar_vol_90d", 0) >= min_vol]


def tier_summary(ranked: Optional[Dict] = None) -> dict:
    if ranked is None:
        ranked = load_ranked_universe()
    counts = {"core": 0, "broad": 0, "signal": 0, "skip": 0}
    for s in ranked:
        counts[classify_symbol(s, ranked)] += 1
    return counts

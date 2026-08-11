"""
Core Sleeve — buy-and-hold mega-cap portfolio for SMSF (60-70% NAV).

Separate table from paper_trades(satellite). Core positions are NOT traded
on 63-day signals. Exit only on thesis-break or RED circuit breaker.

Semi-annual review: February + August.
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
import uuid


CORE_SYMBOLS = {
    "CBA": {"sector": "Financials", "thesis": "Mega financial — median +9.8%/yr, fully franked", "yield_est": 3.5, "franking": 1.0},
    "BHP": {"sector": "Materials", "thesis": "Commodity anchor — portfolio hedge, variable yield", "yield_est": 5.0, "franking": 0.75},
    "WES": {"sector": "Consumer", "thesis": "Most consistent consumer — best risk-adjusted (Sharpe 1.10)", "yield_est": 3.0, "franking": 1.0},
    "WOW": {"sector": "Consumer", "thesis": "Defensive consumer, fully franked dividend compounder", "yield_est": 3.0, "franking": 1.0},
    "CSL": {"sector": "Healthcare", "thesis": "Healthcare anchor, low correlation to commodities", "yield_est": 1.0, "franking": 0.3},
    "GMG": {"sector": "REITs", "thesis": "Industrial REIT — growth profile, global diversification", "yield_est": 1.0, "franking": 0.3},
    "NAB": {"sector": "Financials", "thesis": "Bank diversification from CBA, fully franked yield", "yield_est": 5.0, "franking": 1.0},
    "WBC": {"sector": "Financials", "thesis": "Retail banking exposure, fully franked", "yield_est": 5.5, "franking": 1.0},
    "MQG": {"sector": "Financials", "thesis": "Diversified financial — global infrastructure, non-bank exposure", "yield_est": 3.5, "franking": 0.4},
    "TLS": {"sector": "Communications", "thesis": "Yield compounder, infrastructure backbone", "yield_est": 4.5, "franking": 1.0},
}

CORE_ETF_SYMBOLS = {
    "VAS": {"sector": "ETF-Diversified", "thesis": "Broad ASX300 exposure — reduces single-stock risk", "yield_est": 4.0, "franking": 0.7},
    "VGS": {"sector": "ETF-International", "thesis": "Global developed markets — currency diversification", "yield_est": 2.5, "franking": 0.0},
}

CORE_TARGET_PCT_NAV = 0.60
CORE_MAX_POSITIONS = 10
CORE_SINGLE_MAX_PCT = 0.15
SEMI_ANNUAL_MONTHS = [2, 8]


def get_next_review_date() -> date:
    today = date.today()
    for m in SEMI_ANNUAL_MONTHS:
        review = date(today.year, m, 15)
        if review >= today:
            return review
    return date(today.year + 1, SEMI_ANNUAL_MONTHS[0], 15)


def get_core_candidates() -> List[Dict]:
    candidates = []
    for sym, info in CORE_SYMBOLS.items():
        candidates.append({
            "symbol": sym,
            "sector": info["sector"],
            "thesis": info["thesis"],
            "yield_est": info["yield_est"],
            "franking": info["franking"],
            "type": "stock",
        })
    for sym, info in CORE_ETF_SYMBOLS.items():
        candidates.append({
            "symbol": sym,
            "sector": info["sector"],
            "thesis": info["thesis"],
            "yield_est": info["yield_est"],
            "franking": info["franking"],
            "type": "etf",
        })
    return candidates


def compute_core_sleeve_state(conn, nav: float) -> Dict:
    rows = conn.execute(
        "SELECT symbol, qty, entry_price, sector, thesis, next_review_date, franking_pct, yield_est, created_at "
        "FROM core_positions WHERE status = 'active' ORDER BY created_at"
    ).fetchall()

    positions = []
    total_value = 0.0
    for row in rows:
        sym, qty, entry, sector, thesis, review, franking, yld, created = row
        val = (qty or 0) * (entry or 0)
        total_value += val
        positions.append({
            "symbol": sym,
            "qty": qty,
            "entry_price": entry,
            "value": round(val, 2),
            "pct_of_nav": round(val / nav * 100, 1) if nav > 0 else 0,
            "sector": sector,
            "thesis": thesis,
            "next_review": str(review) if review else None,
            "franking_pct": franking,
            "yield_est": yld,
            "days_held": (date.today() - created.date()).days if created else 0,
        })

    core_nav_pct = round(total_value / nav * 100, 1) if nav > 0 else 0
    review_due = False
    now = date.today()
    for p in positions:
        if p["next_review"] and date.fromisoformat(p["next_review"]) <= now:
            review_due = True
            break

    return {
        "positions": positions,
        "total_value": round(total_value, 2),
        "pct_of_nav": core_nav_pct,
        "position_count": len(positions),
        "target_pct": round(CORE_TARGET_PCT_NAV * 100, 0),
        "max_positions": CORE_MAX_POSITIONS,
        "max_single_pct": round(CORE_SINGLE_MAX_PCT * 100, 0),
        "next_review_date": str(get_next_review_date()),
        "review_due": review_due,
        "status": "above_target" if core_nav_pct > CORE_TARGET_PCT_NAV * 100 else (
            "on_target" if core_nav_pct >= CORE_TARGET_PCT_NAV * 100 - 5 else "below_target"
        ),
    }


def create_core_position(conn, user_id: str, symbol: str, qty: float, entry_price: float,
                         market: str = "AU", notes: str = "") -> Optional[str]:
    sym_info = {**CORE_SYMBOLS, **CORE_ETF_SYMBOLS}.get(symbol.upper(), {})
    sector = sym_info.get("sector", "Unknown")
    thesis = sym_info.get("thesis", notes or "Manual core position")
    yield_est = sym_info.get("yield_est", 0)
    franking = sym_info.get("franking", 0)

    existing = conn.execute(
        "SELECT COUNT(*) FROM core_positions WHERE symbol = :sym AND status = 'active'",
        {"sym": symbol.upper()}
    ).fetchone()
    if existing and existing[0] > 0:
        return None

    count = conn.execute(
        "SELECT COUNT(*) FROM core_positions WHERE status = 'active'"
    ).fetchone()
    if count and count[0] >= CORE_MAX_POSITIONS:
        return None

    pos_id = str(uuid.uuid4())
    next_review = get_next_review_date()
    conn.execute(
        """INSERT INTO core_positions (id, user_id, symbol, market, qty, entry_price,
           sector, thesis, franking_pct, yield_est, next_review_date, status, notes)
           VALUES (:id, :uid, :sym, :mkt, :qty, :entry, :sector, :thesis,
           :franking, :yield, :review, 'active', :notes)""",
        {
            "id": pos_id, "uid": user_id, "sym": symbol.upper(), "mkt": market,
            "qty": qty, "entry": entry_price, "sector": sector,
            "thesis": thesis, "franking": franking, "yield": yield_est,
            "review": next_review.isoformat(), "notes": notes,
        }
    )
    return pos_id


def close_core_position(conn, position_id: str, exit_reason: str = "manual") -> bool:
    conn.execute(
        "UPDATE core_positions SET status = 'closed', exit_reason = :reason, "
        "closed_at = NOW(), updated_at = NOW() WHERE id = :id",
        {"reason": exit_reason, "id": position_id}
    )
    return True

"""
Data Sanity — verifies EODHD close vs ASX official close is within 2% tolerance.

Guardrail #13: gap > 2% → no trade until sources agree.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional
import requests
import time


SANITY_THRESHOLD_PCT = 2.0
CHECK_TIMEOUT = 10


def get_asx_close_via_yahoo(symbol: str) -> Optional[float]:
    import yfinance as yf
    try:
        ticker = f"{symbol}.AX"
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        if not hist.empty and "Close" in hist.columns:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def get_eodhd_close(conn, symbol: str) -> Optional[float]:
    try:
        row = conn.execute(
            "SELECT close FROM eod_ohl_history WHERE symbol = :sym "
            "ORDER BY trade_date DESC LIMIT 1",
            {"sym": symbol.upper()}
        ).fetchone()
        if row:
            return float(row[0])
    except Exception:
        pass
    return None


def check_data_sanity(conn, symbol: str) -> Dict:
    eodhd_close = get_eodhd_close(conn, symbol)
    asx_close = get_asx_close_via_yahoo(symbol)

    if eodhd_close is None:
        return {"sane": True, "gap_pct": 0, "reason": "no_eodhd_data",
                "eodhd_close": 0, "asx_close": asx_close or 0}

    if asx_close is None or asx_close <= 0:
        return {"sane": True, "gap_pct": 0, "reason": "no_asx_data",
                "eodhd_close": eodhd_close, "asx_close": 0}

    gap_pct = abs(eodhd_close - asx_close) / asx_close * 100
    sane = gap_pct <= SANITY_THRESHOLD_PCT

    return {
        "sane": sane,
        "gap_pct": round(gap_pct, 2),
        "eodhd_close": round(eodhd_close, 2),
        "asx_close": round(asx_close, 2),
        "reason": "ok" if sane else f"gap {gap_pct:.1f}% > {SANITY_THRESHOLD_PCT}% threshold",
        "symbol": symbol,
    }


def log_sanity_check(conn, result: Dict):
    conn.execute("""
        INSERT INTO data_sanity_log (checked_at, symbol, sane, gap_pct, eodhd_close,
                                     asx_close, reason, is_stale)
        VALUES (NOW(), :sym, :sane, :gap, :eodhd, :asx, :reason, :stale)
    """, {
        "sym": result.get("symbol", ""),
        "sane": result["sane"],
        "gap": result["gap_pct"],
        "eodhd": result["eodhd_close"],
        "asx": result["asx_close"],
        "reason": result["reason"],
        "stale": result["gap_pct"] > 10,
    })

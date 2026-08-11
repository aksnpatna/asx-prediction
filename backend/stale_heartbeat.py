"""
Stale Pipeline Heartbeat — detects stale OHLC sessions in core universe.

Guardrail #18: if >5% of core-universe tickers show stale sessions
(unchanged close or zero volume) over the past 5 trading days → freeze new entries.
"""

from datetime import date, datetime, timedelta
from typing import Dict


STALE_THRESHOLD_PCT = 5.0
STALE_LOOKBACK_DAYS = 5


def check_stale_sessions(conn, core_symbols: list) -> Dict:
    if not core_symbols:
        return {"status": "no_universe", "stale_pct": 0, "freeze": False,
                "stale_count": 0, "total_checked": 0, "description": "No symbols provided"}

    placeholders = ",".join(f"'{s}'" for s in core_symbols)
    cutoff = date.today() - timedelta(days=STALE_LOOKBACK_DAYS)

    try:
        rows = conn.execute(
            f"SELECT symbol, trade_date, close, volume FROM eod_ohl_history "
            f"WHERE symbol IN ({placeholders}) AND trade_date >= :cutoff "
            f"ORDER BY symbol, trade_date",
            {"cutoff": cutoff}
        ).fetchall()
    except Exception:
        return {"status": "db_error", "stale_pct": 0, "freeze": False,
                "stale_count": 0, "total_checked": 0, "description": "Database error"}

    if not rows:
        return {"status": "no_data", "stale_pct": 100, "freeze": True,
                "stale_count": len(core_symbols), "total_checked": len(core_symbols),
                "description": f"No OHLC data for any of {len(core_symbols)} symbols"}

    from collections import defaultdict
    symbol_staleness = defaultdict(lambda: {"stale_days": 0, "total_days": 0})

    prev_symbol = None
    prev_close = None
    for row in rows:
        sym, td, close, vol = row
        if sym != prev_symbol:
            prev_close = None
            prev_symbol = sym
        symbol_staleness[sym]["total_days"] += 1
        if vol is None or vol == 0 or (prev_close is not None and abs((close or 0) - (prev_close or 0)) < 0.001):
            symbol_staleness[sym]["stale_days"] += 1
        prev_close = close

    stale_count = 0
    for sym, stats in symbol_staleness.items():
        if stats["total_days"] > 0 and stats["stale_days"] / stats["total_days"] > 0.5:
            stale_count += 1

    total_checked = len(symbol_staleness)
    stale_pct = round(stale_count / total_checked * 100, 1) if total_checked > 0 else 0
    freeze = stale_pct > STALE_THRESHOLD_PCT

    return {
        "status": "freeze" if freeze else ("warning" if stale_pct > 2 else "healthy"),
        "stale_pct": stale_pct,
        "freeze": freeze,
        "stale_count": stale_count,
        "total_checked": total_checked,
        "threshold_pct": STALE_THRESHOLD_PCT,
        "description": (
            f"{stale_count}/{total_checked} symbols stale ({stale_pct}%) — "
            f"{'FREEZE new entries' if freeze else 'Warning' if stale_pct > 2 else 'Healthy'}"
        ),
    }

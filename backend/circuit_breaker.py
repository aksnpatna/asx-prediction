"""
Portfolio Drawdown Circuit Breaker — capital protection for SMSF.

Three-level ladder:
  YELLOW (8% DD): suspend new entries, raise cash to 30%, tighten stops
  ORANGE (15% DD): partial liquidation, 60% cash floor, no new entries
  RED (25% DD): sell all satellite positions, 100% cash, 8-week cooling
"""

from typing import Dict


BREAKER_LEVELS = [
    {
        "name": "YELLOW",
        "threshold": 0.08,
        "allow_new_entries": False,
        "cash_floor_pct": 0.30,
        "stop_tighten_pct": 1.5,
        "sell_all": False,
        "description": "Portfolio drawdown >8% from peak. Suspend new satellite entries. Raise cash to 30%.",
    },
    {
        "name": "ORANGE",
        "threshold": 0.15,
        "allow_new_entries": False,
        "cash_floor_pct": 0.60,
        "stop_tighten_pct": 2.0,
        "sell_all": False,
        "description": "Drawdown >15%. Sell weakest 50% of satellite. 60% cash floor. No new entries.",
    },
    {
        "name": "RED",
        "threshold": 0.25,
        "allow_new_entries": False,
        "cash_floor_pct": 1.00,
        "stop_tighten_pct": 0.0,
        "sell_all": True,
        "description": "Drawdown >25%. Sell all satellite positions. 100% cash. 8-week re-entry cooling.",
    },
]

COOLDOWN_WEEKS = 8


class DrawdownCircuitBreaker:
    def __init__(self, peak_value: float = None):
        self.peak_value = peak_value

    def check(self, current_value: float, peak_value: float = None) -> Dict:
        if peak_value is not None:
            self.peak_value = peak_value
        if self.peak_value is None or self.peak_value <= 0:
            return {
                "level": "NORMAL",
                "drawdown_pct": 0.0,
                "peak_value": current_value,
                "current_value": current_value,
                "allow_new_entries": True,
                "cash_floor_pct": 0.15,
                "stop_tighten_pct": 0.0,
                "sell_all": False,
                "description": "Normal — full deployment.",
            }

        dd = (self.peak_value - current_value) / self.peak_value

        for level in reversed(BREAKER_LEVELS):
            if dd >= level["threshold"]:
                return {
                    "level": level["name"],
                    "drawdown_pct": round(dd * 100, 2),
                    "peak_value": round(self.peak_value, 2),
                    "current_value": round(current_value, 2),
                    **level,
                }

        return {
            "level": "NORMAL",
            "drawdown_pct": round(dd * 100, 2),
            "peak_value": round(self.peak_value, 2),
            "current_value": round(current_value, 2),
            "allow_new_entries": True,
            "cash_floor_pct": 0.15,
            "stop_tighten_pct": 0.0,
            "sell_all": False,
            "description": "Normal — full deployment.",
        }

    def update_peak(self, new_value: float):
        if self.peak_value is None or new_value > self.peak_value:
            self.peak_value = new_value

    def get_status_str(self, state: Dict) -> str:
        level = state["level"]
        dd = state["drawdown_pct"]
        emojis = {"NORMAL": "🟢", "YELLOW": "🟡", "ORANGE": "🟠", "RED": "🔴"}
        emoji = emojis.get(level, "⚪")
        return f"{emoji} <b>{level}</b>: {dd:.1f}% drawdown from ${state['peak_value']:,.0f} peak"


def persist_peak_value(portfolio_value: float, uid: str = None):
    try:
        from sqlalchemy import text
        from main import db_conn
        with db_conn() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE portfolio_peak_tracker ADD COLUMN IF NOT EXISTS user_id TEXT"))
                conn.commit()
            except Exception:
                pass
            conn.execute(text("""
                INSERT INTO portfolio_peak_tracker (recorded_at, peak_value, user_id)
                VALUES (CURRENT_TIMESTAMP, :val, :uid)
            """), {"val": portfolio_value, "uid": uid})
            conn.commit()
    except Exception:
        pass


def get_peak_value(uid: str = None, default: float = 0.0) -> float:
    """Latest recorded peak. Per-user when uid given (fresh users fall back to
    `default` — the global tracker holds legacy summed values that poison
    per-user breakers)."""
    try:
        from sqlalchemy import text
        from main import db_conn
        with db_conn() as conn:
            if uid:
                try:
                    row = conn.execute(text(
                        "SELECT peak_value FROM portfolio_peak_tracker "
                        "WHERE user_id = :uid ORDER BY recorded_at DESC LIMIT 1"),
                        {"uid": uid}).fetchone()
                    if row and row[0]:
                        return float(row[0])
                except Exception:
                    pass
                return float(default or 0.0)
            row = conn.execute(text(
                "SELECT peak_value FROM portfolio_peak_tracker "
                "ORDER BY recorded_at DESC LIMIT 1"
            )).fetchone()
            if row:
                return float(row[0])
    except Exception:
        pass
    return float(default or 0.0)

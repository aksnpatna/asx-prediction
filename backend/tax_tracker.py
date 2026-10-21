"""
Tax Tracker — CGT 12-month timer + franking credit calendar for SMSF.

Tracks days-held for every open position and alerts when approaching
the 12-month CGT discount window (10% effective rate vs 15%).
Also monitors ex-dividend dates for fully-franked stocks.
"""

from datetime import date, datetime, timedelta
from typing import Dict, Optional, List


CGT_HOLD_DAYS = 365
CGT_ALERT_WINDOW_DAYS = 30
CGT_EFFECTIVE_RATE_SHORT = 0.15
CGT_EFFECTIVE_RATE_LONG = 0.10
CGT_MIN_GAIN_FOR_ALERT_PCT = 5.0

FULLY_FRANKED_STOCKS = {
    "CBA", "NAB", "WBC", "ANZ", "MQG", "BOQ", "BEN",
    "BHP", "RIO", "FMG", "WES", "WOW", "COL", "TLS",
    "CSL", "GMG", "SUN", "IAG", "QBE", "ASX",
    "MIN", "S32", "STO", "WDS", "ORG", "APA",
}


class TaxTracker:
    def __init__(self):
        pass

    def get_cgt_status(
        self,
        symbol: str,
        entry_date: date,
        current_price: float,
        entry_price: float,
    ) -> Dict:
        days_held = (date.today() - entry_date).days
        days_to_discount = max(0, CGT_HOLD_DAYS - days_held)
        gain_pct = (current_price / entry_price - 1) * 100

        effective_cgt = (CGT_EFFECTIVE_RATE_LONG if days_held >= CGT_HOLD_DAYS
                         else CGT_EFFECTIVE_RATE_SHORT)

        alert = None
        should_defer = False

        if 0 < days_to_discount <= CGT_ALERT_WINDOW_DAYS and gain_pct >= CGT_MIN_GAIN_FOR_ALERT_PCT:
            alert = (
                f"CGT DISCOUNT in {days_to_discount} days on {symbol} "
                f"({gain_pct:+.1f}%) — hold for 10% effective rate"
            )
            should_defer = True
        elif days_held >= CGT_HOLD_DAYS and gain_pct >= CGT_MIN_GAIN_FOR_ALERT_PCT:
            alert = (
                f"CGT DISCOUNT ACTIVE on {symbol} "
                f"({gain_pct:+.1f}%) — selling at 10% effective rate"
            )

        return {
            "symbol": symbol,
            "days_held": days_held,
            "days_to_discount": days_to_discount,
            "effective_cgt": effective_cgt,
            "gain_pct": round(gain_pct, 2),
            "alert": alert,
            "should_defer_exit": should_defer,
        }

    def is_franked(self, symbol: str) -> bool:
        return symbol.upper() in FULLY_FRANKED_STOCKS

    def get_franking_credit_est(
        self, value: float, dividend_yield_pct: float = 4.5
    ) -> float:
        dividend_amount = value * (dividend_yield_pct / 100)
        franking_credit = dividend_amount * (0.30 / 0.70)
        return round(franking_credit, 2)

    def batch_status(self, positions: List[Dict]) -> List[Dict]:
        results = []
        for pos in positions:
            try:
                ed = (pos.get("entry_date") or pos.get("created_at") or "")
                if isinstance(ed, str):
                    ed = datetime.fromisoformat(ed.replace("Z", "")).date()
                elif isinstance(ed, datetime):
                    ed = ed.date()
                else:
                    ed = date.today()
            except Exception:
                ed = date.today()

            ep = float(pos.get("entry_price", 0))
            cp = float(pos.get("current_price", 0))
            status = self.get_cgt_status(pos.get("symbol", ""), ed, cp, ep)
            status["position_id"] = pos.get("id")
            results.append(status)
        return results


def get_upcoming_dividends(symbol: str, api_key: str = None) -> Optional[Dict]:
    if not api_key:
        import os
        api_key = os.getenv("EODHD_API_KEY", "")

    if api_key:
        try:
            import requests
            r = requests.get(
                f"https://eodhd.com/api/div/{symbol}.AU",
                params={"api_token": api_key, "fmt": "json",
                        "from": date.today().isoformat()},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    upcoming = []
                    for d in data:
                        try:
                            d_date = date.fromisoformat(d["date"][:10])
                            if d_date > date.today():
                                upcoming.append({
                                    "date": str(d_date),
                                    "days_to_ex_div": (d_date - date.today()).days,
                                    "value": d.get("value", 0),
                                })
                        except Exception:
                            continue
                    if upcoming:
                        next_div = upcoming[0]
                        return {
                            "symbol": symbol,
                            "ex_div_date": next_div["date"],
                            "days_to_ex_div": next_div["days_to_ex_div"],
                            "dividend_amount": next_div["value"],
                            "all_upcoming": upcoming[:3],
                        }
        except Exception:
            pass

    # Free yfinance fallback for ex-dividend date and dividend rate
    try:
        import yfinance as yf
        tk = yf.Ticker(f"{symbol.upper().replace('.AX', '')}.AX")
        info = tk.info or {}
        ex_ts = info.get("exDividendDate")
        div_rate = info.get("dividendRate") or info.get("lastDividendValue") or 0
        if ex_ts:
            from datetime import datetime as _dt
            d_date = _dt.utcfromtimestamp(int(ex_ts)).date() if isinstance(ex_ts, (int, float)) else date.fromisoformat(str(ex_ts)[:10])
            if d_date >= date.today():
                return {
                    "symbol": symbol,
                    "ex_div_date": str(d_date),
                    "days_to_ex_div": (d_date - date.today()).days,
                    "dividend_amount": round(float(div_rate) / 2.0, 4) if div_rate else 0,
                    "all_upcoming": [{"date": str(d_date), "days_to_ex_div": (d_date - date.today()).days, "value": round(float(div_rate) / 2.0, 4) if div_rate else 0}],
                }
    except Exception:
        pass
    return None


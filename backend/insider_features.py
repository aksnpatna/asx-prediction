"""Insider transaction features — directors/executives buying & selling their own stock.

Insider buying is one of the strongest known signals, especially on the
under-covered ASX (small/mid caps have real information asymmetry).

Requires EODHD Fundamentals feed ($59.99) — /api/insider-transactions endpoint.

Features:
- insider_net_ratio: (buys - sells) / (buys + sells) over trailing 90 days, -1..+1
- insider_buy_count: number of insider BUY transactions in trailing 90 days
- insider_sell_count: number of insider SELL transactions in trailing 90 days
- insider_net_value: net insider value (buy $ - sell $) in trailing 90 days
"""

import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EODHD_BASE = "https://eodhd.com/api"


def _api_key() -> str:
    return os.getenv("EODHD_API_KEY", "").strip()


def fetch_insider_transactions(symbol: str, limit: int = 100) -> list:
    """Fetch insider transactions for a symbol from EODHD.

    Returns list of dicts: {date, transactionType, shares, price, ...}
    """
    import requests
    key = _api_key()
    if not key:
        return []
    try:
        r = requests.get(
            f"{EODHD_BASE}/insider-transactions",
            params={"api_token": key, "symbol": f"{symbol}.AU", "limit": limit, "fmt": "json"},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data
        return []
    except Exception:
        return []


def compute_insider_features(transactions: list, as_of: date = None) -> dict:
    """Compute trailing-90-day insider features from a list of transactions.

    Returns dict: {insider_net_ratio, insider_buy_count, insider_sell_count,
                   insider_net_value}
    """
    import numpy as np
    if not transactions:
        return {
            "insider_net_ratio": 0.0,
            "insider_buy_count": 0.0,
            "insider_sell_count": 0.0,
            "insider_net_value": 0.0,
        }

    cutoff = (as_of or date.today()) - timedelta(days=90)

    buys = 0
    sells = 0
    buy_value = 0.0
    sell_value = 0.0

    for t in transactions:
        try:
            # Date field varies: "date" or "transactionDate" or "reportDate"
            d = t.get("date") or t.get("transactionDate") or t.get("reportDate")
            if d:
                d = str(d)[:10]
                tx_date = date.fromisoformat(d)
                if tx_date < cutoff:
                    continue

            ttype = (t.get("transactionType") or t.get("type") or "").strip().upper()
            shares = float(t.get("shares") or t.get("Shares") or 0)
            price = float(t.get("price") or t.get("Price") or 0)
            value = shares * price

            if "BUY" in ttype or "PURCHASE" in ttype or "ACQUIRE" in ttype:
                buys += 1
                buy_value += value
            elif "SELL" in ttype or "DISPOSE" in ttype:
                sells += 1
                sell_value += value
        except Exception:
            continue

    total = buys + sells
    net_ratio = (buys - sells) / total if total > 0 else 0.0
    net_value = buy_value - sell_value

    return {
        "insider_net_ratio": round(net_ratio, 4),
        "insider_buy_count": float(buys),
        "insider_sell_count": float(sells),
        "insider_net_value": round(net_value, 2),
    }


def load_insider_feature_map(symbols: list, db_conn=None) -> dict:
    """Fetch + compute insider features for symbols.

    Returns dict: symbol -> insider feature dict (static, point-in-time is
    approximated by using the latest available transactions).
    """
    result = {}
    for i, sym in enumerate(symbols):
        try:
            txns = fetch_insider_transactions(sym)
            result[sym] = compute_insider_features(txns)
        except Exception:
            result[sym] = {
                "insider_net_ratio": 0.0, "insider_buy_count": 0.0,
                "insider_sell_count": 0.0, "insider_net_value": 0.0,
            }
        if (i + 1) % 50 == 0:
            print(f"[Insider] Fetched {i+1}/{len(symbols)}", flush=True)
        time.sleep(0.15)
    return result

"""EODHD Fundamentals + Delisted Data Integration (requires $59.99 Fundamentals feed).

Two-step G1 survivorship-bias fix:
1. Fetch delisted ASX tickers via exchange-symbol-list/AU?delisted=1
2. Backfill their OHLC + fundamentals into our tables

Point-in-time fundamentals: fetch /api/fundamentals/{TICKER} for liquid symbols,
replacing the yfinance latest-snapshot proxy (removes look-ahead bias).

Run once after subscribing. Requires EODHD_API_KEY with Fundamentals access.
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EODHD_BASE = "https://eodhd.com/api"


def _api_key() -> str:
    return os.getenv("EODHD_API_KEY", "").strip()


def fetch_delisted_symbols() -> list:
    """Fetch all delisted ASX common stocks. Returns list of {code, name, exchange}."""
    key = _api_key()
    if not key:
        print("[EODHD-Fund] No EODHD_API_KEY. Aborting.")
        return []

    url = f"{EODHD_BASE}/exchange-symbol-list/AU"
    try:
        r = requests.get(
            url,
            params={"api_token": key, "delisted": 1, "type": "common_stock", "fmt": "json"},
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                print(f"[EODHD-Fund] Found {len(data)} delisted ASX common stocks.")
                return data
        print(f"[EODHD-Fund] Delisted fetch HTTP {r.status_code}: {r.text[:200]}")
        return []
    except Exception as e:
        print(f"[EODHD-Fund] Delisted fetch failed: {e}")
        return []


def store_delisted_tickers(symbols: list):
    """Store delisted tickers in delisted_tickers table."""
    from sqlalchemy import text
    from main import db_conn

    inserted = 0
    with db_conn() as conn:
        for s in symbols:
            code = s.get("Code") or s.get("code") or ""
            name = s.get("Name") or s.get("name") or ""
            if not code:
                continue
            try:
                conn.execute(text(
                    "INSERT INTO delisted_tickers (symbol, name, delisted_reason, source) "
                    "VALUES (:sym, :name, :reason, 'eodhd') "
                    "ON CONFLICT (symbol) DO UPDATE SET name=EXCLUDED.name"
                ), {"sym": code.split(".")[0], "name": name[:200], "reason": "delisted"})
                inserted += 1
            except Exception:
                pass
        conn.commit()
    print(f"[EODHD-Fund] Stored {inserted} delisted tickers.")
    return inserted


def backfill_delisted_ohlc(symbols: list, from_date: str = "2015-01-01") -> int:
    """Backfill OHLC history for delisted tickers into eod_ohl_history."""
    from sqlalchemy import text
    from main import db_conn

    key = _api_key()
    inserted = 0
    errors = 0
    for i, s in enumerate(symbols):
        code = (s.get("Code") or s.get("code") or "").split(".")[0]
        if not code:
            continue
        try:
            r = requests.get(
                f"{EODHD_BASE}/eod/{code}.AU",
                params={"api_token": key, "fmt": "json", "from": from_date},
                timeout=30,
            )
            if r.status_code != 200:
                errors += 1
                continue
            rows = r.json()
            if not isinstance(rows, list) or not rows:
                continue
            with db_conn() as conn:
                for row in rows:
                    try:
                        conn.execute(text(
                            "INSERT INTO eod_ohl_history (symbol, market, trade_date, open, high, low, close, volume, adjusted_close) "
                            "VALUES (:sym, 'AU', :d, :o, :h, :l, :c, :v, :ac) "
                            "ON CONFLICT (symbol, market, trade_date) DO NOTHING"
                        ), {
                            "sym": code, "d": row.get("date"),
                            "o": row.get("open"), "h": row.get("high"),
                            "l": row.get("low"), "c": row.get("close"),
                            "v": row.get("volume") or 0,
                            "ac": row.get("adjusted_close"),
                        })
                    except Exception:
                        pass
                conn.commit()
            inserted += len(rows)
        except Exception:
            errors += 1
        if (i + 1) % 100 == 0:
            print(f"[EODHD-Fund] Backfilled {i+1}/{len(symbols)} delisted ({inserted} rows, {errors} errors)")
        time.sleep(0.1)
    print(f"[EODHD-Fund] Delisted OHLC backfill done: {inserted} rows, {errors} errors.")
    return inserted


def fetch_fundamentals(symbols: list) -> int:
    """Fetch point-in-time fundamentals for symbols (replaces yfinance snapshot proxy)."""
    from sqlalchemy import text
    from main import db_conn

    key = _api_key()
    fetched = 0
    errors = 0
    for i, sym in enumerate(symbols):
        try:
            r = requests.get(
                f"{EODHD_BASE}/fundamentals/{sym}.AU",
                params={"api_token": key, "fmt": "json"},
                timeout=30,
            )
            if r.status_code != 200:
                errors += 1
                continue
            data = r.json()
            general = data.get("General", {})
            highlights = data.get("Highlights", {})
            valuation = data.get("Valuation", {})

            trailing_pe = highlights.get("PERatio") or valuation.get("TrailingPE")
            forward_pe = valuation.get("ForwardPE")
            market_cap = highlights.get("MarketCapitalization")
            div_yield = highlights.get("DividendYield")
            analyst_target = highlights.get("WallStreetTargetPrice")
            beta = data.get("Technicals", {}).get("Beta")

            with db_conn() as conn:
                conn.execute(text(
                    "INSERT INTO fundamental_snapshots "
                    "(symbol, market, snapshot_date, trailing_pe, forward_pe, market_cap, "
                    " dividend_yield, analyst_target_mean, beta) "
                    "VALUES (:sym, 'AU', :d, :pe, :fpe, :mc, :dy, :atm, :beta) "
                    "ON CONFLICT (symbol, snapshot_date) DO UPDATE SET "
                    "trailing_pe=EXCLUDED.trailing_pe, forward_pe=EXCLUDED.forward_pe, "
                    "market_cap=EXCLUDED.market_cap, dividend_yield=EXCLUDED.dividend_yield, "
                    "analyst_target_mean=EXCLUDED.analyst_target_mean, beta=EXCLUDED.beta"
                ), {
                    "sym": sym, "d": date.today(),
                    "pe": trailing_pe, "fpe": forward_pe, "mc": market_cap,
                    "dy": div_yield, "atm": analyst_target, "beta": beta,
                })
                conn.commit()
            fetched += 1
        except Exception:
            errors += 1
        if (i + 1) % 50 == 0:
            print(f"[EODHD-Fund] Fundamentals {i+1}/{len(symbols)} ({fetched} ok, {errors} errors)")
        time.sleep(0.15)
    print(f"[EODHD-Fund] Fundamentals done: {fetched} ok, {errors} errors.")
    return fetched


def run_g1_delisted_backfill() -> dict:
    """Full G1 survivorship-bias fix: delisted tickers + OHLC backfill."""
    symbols = fetch_delisted_symbols()
    if not symbols:
        return {"status": "error", "reason": "no_delisted_symbols"}

    stored = store_delisted_tickers(symbols)
    ohlc_rows = backfill_delisted_ohlc(symbols)

    return {
        "status": "ok",
        "delisted_symbols": len(symbols),
        "stored": stored,
        "ohlc_rows": ohlc_rows,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["delisted", "fundamentals", "all"], default="all")
    ap.add_argument("--symbols", nargs="*", default=None, help="specific symbols for fundamentals")
    args = ap.parse_args()

    if args.mode in ("delisted", "all"):
        print(json.dumps(run_g1_delisted_backfill(), indent=2, default=str))

    if args.mode in ("fundamentals", "all"):
        from config.universe import get_universe_symbols
        syms = args.symbols or (get_universe_symbols("core") + get_universe_symbols("broad"))
        fetch_fundamentals(syms)

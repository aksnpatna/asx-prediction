"""Alpha Vantage free-tier fundamental backfill for ASX tickers.

Rate limits:
  - 5 calls/minute (12s between requests)
  - 500 calls/day (stops and resumes next run)
  - Checkpoint file tracks progress per-symbol

API endpoints used (free tier):
  - OVERVIEW: company profile, PE, EPS, market cap, beta, sector
  - INCOME_STATEMENT: revenue, gross profit, net income, EBIT
  - BALANCE_SHEET: total assets, total liabilities, book value
  - CASH_FLOW: operating cash flow, capex, free cash flow

Schedule: Run as a daily cron job. Processes 500 tickers/day.
          Full 1,645-ticker backfill: ~3.3 days.
"""
import json
import os
import sys
import time as _time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
_STATE_FILE = Path(__file__).parent / "alphavantage_backfill_state.json"
_CALLS_PER_MINUTE = 5
_CALLS_PER_DAY = 500
_MIN_INTERVAL_SEC = 60.0 / _CALLS_PER_MINUTE

_last_call_time = 0.0
_daily_call_count = 0
_daily_date = date.today()


def _rate_limit():
    global _last_call_time, _daily_call_count, _daily_date
    today = date.today()
    if today != _daily_date:
        _daily_call_count = 0
        _daily_date = today

    if _daily_call_count >= _CALLS_PER_DAY:
        raise RuntimeError(f"Daily limit of {_CALLS_PER_DAY} calls reached. Resume tomorrow.")

    now = _time.time()
    elapsed = now - _last_call_time
    if elapsed < _MIN_INTERVAL_SEC:
        _time.sleep(_MIN_INTERVAL_SEC - elapsed)
    _last_call_time = _time.time()
    _daily_call_count += 1


def _load_state() -> dict:
    if _STATE_FILE.exists():
        with open(_STATE_FILE) as f:
            return json.load(f)
    return {"completed_symbols": {}, "last_run": None, "total_calls_today": 0}


def _save_state(state: dict):
    state["last_run"] = datetime.utcnow().isoformat()
    state["total_calls_today"] = _daily_call_count
    with _STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


def fetch_overview(symbol: str) -> Optional[dict]:
    """Fetch Alpha Vantage OVERVIEW endpoint for a ticker."""
    _rate_limit()
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "OVERVIEW",
        "symbol": f"{symbol}.AX",
        "apikey": _API_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "Symbol" not in data:
            return None
        return data
    except Exception as e:
        print(f"[AV] OVERVIEW {symbol} failed: {e}")
        return None


def fetch_income_statement(symbol: str) -> Optional[dict]:
    """Fetch annual income statement."""
    _rate_limit()
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "INCOME_STATEMENT",
        "symbol": f"{symbol}.AX",
        "apikey": _API_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        return data if "annualReports" in data else None
    except Exception as e:
        print(f"[AV] INCOME {symbol} failed: {e}")
        return None


def fetch_balance_sheet(symbol: str) -> Optional[dict]:
    """Fetch annual balance sheet."""
    _rate_limit()
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "BALANCE_SHEET",
        "symbol": f"{symbol}.AX",
        "apikey": _API_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        return data if "annualReports" in data else None
    except Exception as e:
        print(f"[AV] BALANCE {symbol} failed: {e}")
        return None


def fetch_cash_flow(symbol: str) -> Optional[dict]:
    """Fetch annual cash flow statement."""
    _rate_limit()
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "CASH_FLOW",
        "symbol": f"{symbol}.AX",
        "apikey": _API_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        return data if "annualReports" in data else None
    except Exception as e:
        print(f"[AV] CASHFLOW {symbol} failed: {e}")
        return None


def extract_fundamentals(symbol: str, overview: dict, income: dict, balance: dict, cashflow: dict) -> dict:
    """Extract normalized fundamental metrics across endpoints."""
    result = {
        "symbol": symbol,
        "fetched_at": datetime.utcnow().isoformat(),
        "pe_ratio": float(overview.get("PERatio", 0) or 0),
        "forward_pe": float(overview.get("ForwardPE", 0) or 0),
        "eps": float(overview.get("EPS", 0) or 0),
        "market_cap": float(overview.get("MarketCapitalization", 0) or 0),
        "beta": float(overview.get("Beta", 0) or 1.0),
        "dividend_yield": float(overview.get("DividendYield", 0) or 0) / 100,
        "book_value": float(overview.get("BookValue", 0) or 0),
        "return_on_equity": float(overview.get("ReturnOnEquityTTM", 0) or 0) / 100,
        "profit_margin": float(overview.get("ProfitMargin", 0) or 0),
        "revenue_ttm": float(overview.get("RevenueTTM", 0) or 0),
        "sector": overview.get("Sector", ""),
        "industry": overview.get("Industry", ""),
    }

    # Latest annual income statement
    if income and income.get("annualReports"):
        latest = income["annualReports"][0]
        result["net_income"] = float(latest.get("netIncome", 0) or 0)
        result["operating_income"] = float(latest.get("operatingIncome", 0) or 0)
        result["ebitda"] = float(latest.get("ebitda", 0) or 0)

    # Latest balance sheet
    if balance and balance.get("annualReports"):
        latest = balance["annualReports"][0]
        result["total_assets"] = float(latest.get("totalAssets", 0) or 0)
        result["total_liabilities"] = float(latest.get("totalLiabilities", 0) or 0)
        result["total_equity"] = float(latest.get("totalShareholderEquity", 0) or 0)
        result["debt_to_equity"] = result["total_liabilities"] / result["total_equity"] if result["total_equity"] else 0

    # Latest cash flow
    if cashflow and cashflow.get("annualReports"):
        latest = cashflow["annualReports"][0]
        result["operating_cf"] = float(latest.get("operatingCashflow", 0) or 0)
        result["capex"] = abs(float(latest.get("capitalExpenditures", 0) or 0))
        result["free_cash_flow"] = result["operating_cf"] - result["capex"]

    return result


def store_fundamentals(data: dict):
    """Store extracted fundamentals into alphavantage_fundamentals table."""
    from sqlalchemy import text
    from main import db_conn

    with db_conn() as conn:
        conn.execute(
            text("""
                INSERT INTO alphavantage_fundamentals
                (symbol, fetched_at, pe_ratio, forward_pe, eps, market_cap, beta,
                 dividend_yield, book_value, return_on_equity, profit_margin,
                 revenue_ttm, net_income, operating_income, ebitda,
                 total_assets, total_liabilities, total_equity, debt_to_equity,
                 operating_cf, capex, free_cash_flow, sector, industry)
                VALUES (:s,:fa,:pe,:fp,:eps,:mc,:b,:dy,:bv,:roe,:pm,:rev,
                        :ni,:oi,:eb,:ta,:tl,:te,:de,:ocf,:cx,:fcf,:sec,:ind)
                ON CONFLICT (symbol, fetched_at) DO UPDATE SET
                pe_ratio=EXCLUDED.pe_ratio, forward_pe=EXCLUDED.forward_pe,
                eps=EXCLUDED.eps, market_cap=EXCLUDED.market_cap,
                beta=EXCLUDED.beta, dividend_yield=EXCLUDED.dividend_yield,
                book_value=EXCLUDED.book_value,
                return_on_equity=EXCLUDED.return_on_equity,
                profit_margin=EXCLUDED.profit_margin,
                revenue_ttm=EXCLUDED.revenue_ttm,
                net_income=EXCLUDED.net_income,
                operating_income=EXCLUDED.operating_income,
                ebitda=EXCLUDED.ebitda,
                total_assets=EXCLUDED.total_assets,
                total_liabilities=EXCLUDED.total_liabilities,
                total_equity=EXCLUDED.total_equity,
                debt_to_equity=EXCLUDED.debt_to_equity,
                operating_cf=EXCLUDED.operating_cf,
                capex=EXCLUDED.capex,
                free_cash_flow=EXCLUDED.free_cash_flow,
                sector=EXCLUDED.sector, industry=EXCLUDED.industry
            """),
            {"s": data["symbol"], "fa": data["fetched_at"],
             "pe": data.get("pe_ratio"), "fp": data.get("forward_pe"),
             "eps": data.get("eps"), "mc": data.get("market_cap"),
             "b": data.get("beta"), "dy": data.get("dividend_yield"),
             "bv": data.get("book_value"), "roe": data.get("return_on_equity"),
             "pm": data.get("profit_margin"), "rev": data.get("revenue_ttm"),
             "ni": data.get("net_income"), "oi": data.get("operating_income"),
             "eb": data.get("ebitda"), "ta": data.get("total_assets"),
             "tl": data.get("total_liabilities"), "te": data.get("total_equity"),
             "de": data.get("debt_to_equity"), "ocf": data.get("operating_cf"),
             "cx": data.get("capex"), "fcf": data.get("free_cash_flow"),
             "sec": data.get("sector", ""), "ind": data.get("industry", "")},
        )
        conn.commit()


def create_table():
    """Create the alphavantage_fundamentals table if it doesn't exist."""
    from sqlalchemy import text
    from main import db_conn

    with db_conn() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alphavantage_fundamentals (
                symbol VARCHAR(10) NOT NULL,
                fetched_at TIMESTAMP NOT NULL,
                pe_ratio DOUBLE PRECISION,
                forward_pe DOUBLE PRECISION,
                eps DOUBLE PRECISION,
                market_cap DOUBLE PRECISION,
                beta DOUBLE PRECISION,
                dividend_yield DOUBLE PRECISION,
                book_value DOUBLE PRECISION,
                return_on_equity DOUBLE PRECISION,
                profit_margin DOUBLE PRECISION,
                revenue_ttm DOUBLE PRECISION,
                net_income DOUBLE PRECISION,
                operating_income DOUBLE PRECISION,
                ebitda DOUBLE PRECISION,
                total_assets DOUBLE PRECISION,
                total_liabilities DOUBLE PRECISION,
                total_equity DOUBLE PRECISION,
                debt_to_equity DOUBLE PRECISION,
                operating_cf DOUBLE PRECISION,
                capex DOUBLE PRECISION,
                free_cash_flow DOUBLE PRECISION,
                sector VARCHAR(100),
                industry VARCHAR(100),
                PRIMARY KEY (symbol, fetched_at)
            )
        """))
        conn.commit()


def backfill_batch(max_symbols: int = _CALLS_PER_DAY // 4) -> dict:
    """Backfill fundamental data for up to max_symbols (125 by default: 4 calls/ticker).

    Rate: 500 calls/day ÷ 4 calls/ticker = 125 tickers/day.
          1,645 tickers ÷ 125/day = ~13 days for full backfill.
    """
    if not _API_KEY:
        return {"error": "ALPHA_VANTAGE_API_KEY not set"}

    from sqlalchemy import text
    from main import db_conn
    from yfinance_service import YFinanceService

    create_table()

    state = _load_state()
    completed = state.get("completed_symbols", {})
    dead_set = YFinanceService.get_dead_set()

    with db_conn() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT symbol FROM eod_ohl_history WHERE market='AU' AND trade_date >= '2026-01-01' ORDER BY symbol")
        ).fetchall()

    all_symbols = [r[0] for r in rows if r[0] not in dead_set and r[0] not in completed]
    batch = all_symbols[:max_symbols]

    print(f"[AV] Backfill batch: {len(batch)} tickers (limit: {max_symbols}, daily calls left: {_CALLS_PER_DAY - _daily_call_count})")
    fetched = 0
    errors = 0

    for sym in batch:
        try:
            overview = fetch_overview(sym)
            if not overview:
                print(f"[AV] {sym}: no OVERVIEW data, skipping")
                continue

            income = fetch_income_statement(sym)
            balance = fetch_balance_sheet(sym)
            cashflow = fetch_cash_flow(sym)

            fundamentals = extract_fundamentals(sym, overview, income, balance, cashflow)
            fundamentals["symbol"] = sym
            fundamentals["fetched_at"] = datetime.utcnow().isoformat()

            store_fundamentals(fundamentals)
            completed[sym] = datetime.utcnow().isoformat()
            fetched += 1

            if fetched % 10 == 0:
                print(f"[AV] Progress: {fetched}/{len(batch)} fetched, {errors} errors")
                _save_state(state)

        except RuntimeError as e:
            if "Daily limit" in str(e):
                print(f"[AV] Daily limit reached after {fetched} tickers. Resuming tomorrow.")
                break
            errors += 1
            print(f"[AV] {sym} error: {e}")
        except Exception as e:
            errors += 1
            print(f"[AV] {sym} unexpected error: {e}")

    state["completed_symbols"] = completed
    _save_state(state)

    remaining = len(all_symbols) - len(completed)
    print(f"[AV] Batch complete: {fetched} fetched, {errors} errors, {len(completed)} total completed, {remaining} remaining")
    return {"fetched": fetched, "errors": errors, "completed": len(completed), "remaining": remaining}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["backfill", "status", "test"], default="status")
    ap.add_argument("--symbol", type=str, default="BHP")
    ap.add_argument("--max", type=int, default=_CALLS_PER_DAY // 4)
    args = ap.parse_args()

    if args.mode == "status":
        state = _load_state()
        completed = state.get("completed_symbols", {})
        print(f"Completed: {len(completed)} symbols")
        print(f"Last run: {state.get('last_run', 'never')}")
        print(f"Calls today: {state.get('total_calls_today', 0)}/{_CALLS_PER_DAY}")

    elif args.mode == "test":
        if not _API_KEY:
            print("ALPHA_VANTAGE_API_KEY not set. Please set the env variable.")
            sys.exit(1)
        overview = fetch_overview(args.symbol)
        if overview:
            print(json.dumps(dict(list(overview.items())[:20]), indent=2, default=str))
        else:
            print(f"No OVERVIEW data for {args.symbol}")

    elif args.mode == "backfill":
        r = backfill_batch(max_symbols=args.max)
        print(json.dumps(r, indent=2, default=str))

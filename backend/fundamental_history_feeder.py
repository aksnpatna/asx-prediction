"""Free fundamental data feeder — historical financial statements from yfinance.

Fetches 4 years of annual income statement, balance sheet, and cash flow
for each ASX ticker, deriving point-in-time fundamental ratios (P/E, ROE,
debt/equity, margins, FCF yield). Stores per-year snapshots for training
enrichment.

Runs monthly. Single-threaded, rate-limited to avoid Yahoo throttling.
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yfinance_service import YFinanceService


def _safe_float(v):
    try:
        if v is None or (isinstance(v, float) and (pd.isna(v))):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_historical_fundamentals(sym: str) -> list:
    """Fetch 4 years of annual financial statements, return per-year ratio dicts."""
    import yfinance as yf
    try:
        ticker = yf.Ticker(f"{sym}.AX")
        income = ticker.income_stmt
        balance = ticker.balance_sheet
        cashflow = ticker.cashflow
        info = ticker.info

        if income is None or income.empty:
            return []

        # Annual columns are Timestamps (year-end dates)
        years = []
        for col in income.columns:
            ts = pd.Timestamp(col)
            years.append(ts.year)

        # Current market cap + shares for P/E calc
        market_cap = _safe_float(info.get("marketCap")) if info else None
        shares = _safe_float(info.get("sharesOutstanding")) if info else None

        results = []
        for i, yr in enumerate(years):
            col = income.columns[i]
            # Income statement
            revenue = _safe_float(income.loc["Total Revenue", col]) if "Total Revenue" in income.index else None
            net_income = _safe_float(income.loc["Net Income", col]) if "Net Income" in income.index else None
            eps = _safe_float(income.loc["Diluted EPS", col]) if "Diluted EPS" in income.index else (
                _safe_float(income.loc["Basic EPS", col]) if "Basic EPS" in income.index else None)
            gross_profit = _safe_float(income.loc["Gross Profit", col]) if "Gross Profit" in income.index else None
            op_income = _safe_float(income.loc["Operating Income", col]) if "Operating Income" in income.index else None

            # Balance sheet (may have different column years)
            bs_col = None
            if balance is not None and not balance.empty:
                for bc in balance.columns:
                    if pd.Timestamp(bc).year == yr:
                        bs_col = bc
                        break
            total_assets = total_debt = equity = cash = None
            if bs_col is not None:
                total_assets = _safe_float(balance.loc["Total Assets", bs_col]) if "Total Assets" in balance.index else None
                total_debt = _safe_float(balance.loc["Total Debt", bs_col]) if "Total Debt" in balance.index else None
                equity = _safe_float(balance.loc["Stockholders Equity", bs_col]) if "Stockholders Equity" in balance.index else None
                cash = _safe_float(balance.loc["Cash And Cash Equivalents", bs_col]) if "Cash And Cash Equivalents" in balance.index else None

            # Cash flow
            cf_col = None
            if cashflow is not None and not cashflow.empty:
                for cc in cashflow.columns:
                    if pd.Timestamp(cc).year == yr:
                        cf_col = cc
                        break
            ocf = fcf = capex = None
            if cf_col is not None:
                ocf = _safe_float(cashflow.loc["Operating Cash Flow", cf_col]) if "Operating Cash Flow" in cashflow.index else None
                capex = _safe_float(cashflow.loc["Capital Expenditure", cf_col]) if "Capital Expenditure" in cashflow.index else None
                if ocf is not None and capex is not None:
                    fcf = ocf - abs(capex)

            # Derived ratios
            roe = (net_income / equity * 100) if (net_income and equity) else None
            debt_equity = (total_debt / equity) if (total_debt and equity) else None
            gross_margin = (gross_profit / revenue * 100) if (gross_profit and revenue) else None
            op_margin = (op_income / revenue * 100) if (op_income and revenue) else None

            if revenue or net_income or eps:
                results.append({
                    "symbol": sym,
                    "fiscal_year": yr,
                    "revenue": revenue,
                    "net_income": net_income,
                    "eps": eps,
                    "roe_pct": roe,
                    "debt_equity": debt_equity,
                    "gross_margin_pct": gross_margin,
                    "op_margin_pct": op_margin,
                    "ocf": ocf,
                    "fcf": fcf,
                    "total_assets": total_assets,
                    "total_debt": total_debt,
                    "equity": equity,
                    "cash": cash,
                })
        return results
    except Exception as e:
        return []


def fetch_historical_fundamentals(market: str = "AU", batch_size: int = 400) -> dict:
    """Fetch 4 years of historical financial statements for all liquid ASX tickers."""
    from sqlalchemy import text
    from main import db_conn, get_asx_universe

    full_universe = get_asx_universe()
    dead_set = YFinanceService.get_dead_set()
    all_symbols = [s for s in full_universe if len(s) <= 3 and s not in dead_set]

    total = len(all_symbols)
    print(f"[FundFeeder] Fetching historical fundamentals for {total} tickers...")

    total_years = 0
    total_errors = 0
    total_symbols_done = 0

    for i, sym in enumerate(all_symbols):
        try:
            rows = _extract_historical_fundamentals(sym)
            if not rows:
                total_errors += 1
            else:
                with db_conn() as conn:
                    for r in rows:
                        conn.execute(text("""
                            INSERT INTO fundamental_history (symbol, fiscal_year, revenue, net_income,
                                eps, roe_pct, debt_equity, gross_margin_pct, op_margin_pct,
                                ocf, fcf, total_assets, total_debt, equity, cash, fetched_at)
                            VALUES (:sym, :yr, :rev, :ni, :eps, :roe, :de, :gm, :om,
                                :ocf, :fcf, :ta, :td, :eq, :cash, NOW())
                            ON CONFLICT (symbol, fiscal_year) DO UPDATE SET
                                revenue=EXCLUDED.revenue, net_income=EXCLUDED.net_income,
                                eps=EXCLUDED.eps, roe_pct=EXCLUDED.roe_pct, debt_equity=EXCLUDED.debt_equity,
                                gross_margin_pct=EXCLUDED.gross_margin_pct, op_margin_pct=EXCLUDED.op_margin_pct,
                                ocf=EXCLUDED.ocf, fcf=EXCLUDED.fcf, fetched_at=NOW()
                        """), r)
                    conn.commit()
                total_years += len(rows)
                total_symbols_done += 1
        except Exception:
            total_errors += 1

        if (i + 1) % 50 == 0:
            print(f"[FundFeeder] Progress {i+1}/{total} — {total_symbols_done} symbols, {total_years} year-rows, {total_errors} errors")

        time.sleep(0.35)  # rate limit ~3/sec

    print(f"[FundFeeder] Done: {total_symbols_done} symbols, {total_years} year-rows, {total_errors} errors")
    return {"symbols": total_symbols_done, "year_rows": total_years, "errors": total_errors}


def create_history_table():
    """Create fundamental_history table if it doesn't exist."""
    from sqlalchemy import text
    from main import db_conn
    try:
        with db_conn() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS fundamental_history (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    fiscal_year INTEGER NOT NULL,
                    revenue DOUBLE PRECISION,
                    net_income DOUBLE PRECISION,
                    eps DOUBLE PRECISION,
                    roe_pct DOUBLE PRECISION,
                    debt_equity DOUBLE PRECISION,
                    gross_margin_pct DOUBLE PRECISION,
                    op_margin_pct DOUBLE PRECISION,
                    ocf DOUBLE PRECISION,
                    fcf DOUBLE PRECISION,
                    total_assets DOUBLE PRECISION,
                    total_debt DOUBLE PRECISION,
                    equity DOUBLE PRECISION,
                    cash DOUBLE PRECISION,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (symbol, fiscal_year)
                )
            """))
            conn.commit()
    except Exception as e:
        print(f"[FundFeeder] Table create failed: {e}")

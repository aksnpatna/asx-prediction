"""Dividend features — point-in-time from yfinance full dividend history.

ASX is a high-yield market (5-7% for banks/majors). Dividend yield, growth,
and ex-div timing are quality/value signals for the +8%/-8% path-aware strategy.

Features:
- div_yield_ttm: trailing 12-month dividend yield (%), point-in-time
- div_growth_yoy: year-over-year dividend growth (%)
- days_since_div: days since last dividend payment
"""

import json
import os
import sys
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def fetch_dividend_history(symbols: list, db_conn) -> dict:
    """Fetch full dividend history for symbols from yfinance.

    Returns dict: symbol -> pd.Series of dividends (date-indexed).
    Falls back to eod_ohl_history for symbols with no yfinance dividends.
    """
    import yfinance as yf
    result = {}
    for i, sym in enumerate(symbols):
        try:
            t = yf.Ticker(f"{sym}.AX")
            divs = t.dividends
            if divs is not None and len(divs) > 0:
                divs.index = pd.to_datetime(divs.index).tz_localize(None).normalize()
                divs = divs[~divs.index.duplicated(keep='last')].sort_index()
                result[sym] = divs.astype(float)
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            print(f"[Div] Fetched {i+1}/{len(symbols)} symbols ({len(result)} with dividends)", flush=True)
        time.sleep(0.25)
    return result


def _ttm_yield_series(dividends: pd.Series, close: pd.Series) -> pd.Series:
    """Compute trailing-12-month dividend yield (%) aligned to close price index."""
    # TTM dividend sum: sum of dividends in the trailing 365 days
    ttm_div = dividends.rolling("365D").sum()
    # Reindex TTM dividends to the close index (forward-fill)
    ttm_aligned = ttm_div.reindex(close.index, method="ffill").fillna(0.0)
    yield_ttm = (ttm_aligned / close.replace(0, np.nan)) * 100
    return yield_ttm.fillna(0.0)


def build_dividend_features(symbols: list, db_conn) -> dict:
    """Build point-in-time dividend features for symbols.

    Returns dict: symbol -> DataFrame indexed by date with columns:
    div_yield_ttm, div_growth_yoy, days_since_div
    """
    from eodhd_backfill import get_ohlc_for_symbol
    dividend_history = fetch_dividend_history(symbols, db_conn)
    result = {}

    for sym, divs in dividend_history.items():
        try:
            df = get_ohlc_for_symbol(sym)
            if df.empty:
                continue
            close = df["Close"].astype(float)

            # TTM yield
            yield_ttm = _ttm_yield_series(divs, close)

            # YoY dividend growth: (TTM now - TTM 1yr ago) / TTM 1yr ago
            ttm_div = divs.rolling("365D").sum()
            ttm_aligned = ttm_div.reindex(close.index, method="ffill").fillna(0.0)
            ttm_1yr_ago = ttm_aligned.shift(252).fillna(ttm_aligned)
            growth = (ttm_aligned - ttm_1yr_ago) / (ttm_1yr_ago.replace(0, np.nan)).fillna(1.0) * 100
            growth = growth.fillna(0.0)

            # Days since last dividend
            div_dates = pd.Series(divs.index, index=divs.index)
            last_div = div_dates.reindex(close.index, method="ffill")
            last_div_ns = pd.to_datetime(last_div).values.astype('datetime64[ns]')
            days_since = (close.index.values - last_div_ns) / np.timedelta64(1, 'D')
            days_since = pd.Series(days_since, index=close.index).fillna(365).astype(float)

            result[sym] = pd.DataFrame({
                "div_yield_ttm": yield_ttm.values,
                "div_growth_yoy": growth.values,
                "days_since_div": days_since.values,
            }, index=close.index)
        except Exception:
            continue

    print(f"[Div] Built dividend features for {len(result)} symbols", flush=True)
    return result


def load_dividend_feature_map(symbols: list, db_conn) -> dict:
    """Load a fast-lookup dict: symbol -> {date_str -> [yield_ttm, growth, days_since]}."""
    features = build_dividend_features(symbols, db_conn)
    fast_map = {}
    for sym, df in features.items():
        d = {}
        for idx, row in df.iterrows():
            d[idx.date().isoformat()] = (
                float(row["div_yield_ttm"]),
                float(row["div_growth_yoy"]),
                float(row["days_since_div"]),
            )
        fast_map[sym] = d
    return fast_map

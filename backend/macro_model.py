import yfinance as yf
import pandas as pd
import numpy as np
import os
import requests
import threading
import time as _time
from datetime import datetime, timedelta

_macro_cache = {}
_macro_cache_ts = 0.0
_macro_cache_lock = threading.Lock()

def get_macro_data():
    """Fetch recent macro indicators. Cached for 1 hour (thread-safe)."""
    global _macro_cache, _macro_cache_ts
    with _macro_cache_lock:
        if _macro_cache and (_time.time() - _macro_cache_ts) < 3600:
            return _macro_cache.copy()
    
    tickers = {
        "aud_usd": "AUDUSD=X",
        "gold": "GC=F",
        "copper": "HG=F",
        "crude_oil": "CL=F",
        "asx200": "^AXJO",
        "vix": "^VIX"
    }
    
    data = {}
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)
    
    # 1. Fetch yfinance market data
    for key, symbol in tickers.items():
        try:
            df = yf.download(symbol, start=start_date, end=end_date, progress=False)
            if not df.empty and not df['Close'].empty:
                val = df['Close'].iloc[-1]
                val_30d = df['Close'].iloc[-30] if len(df['Close']) >= 30 else val
                data[key] = {
                    "current": float(val.iloc[0]) if isinstance(val, pd.Series) else float(val),
                    "trend_30d": float(((val - val_30d) / val_30d).iloc[0] * 100) if isinstance(val, pd.Series) else float((val - val_30d) / val_30d * 100)
                }
        except Exception:
            data[key] = {"current": 0.0, "trend_30d": 0.0}

    # 2. Fetch FRED Interest Rate / Yield Data (Australia 10yr proxy)
    fred_api_key = os.getenv("FRED_API_KEY", "")
    data["au_10y_yield"] = {"current": 0.0, "trend_30d": 0.0}
    if fred_api_key:
        try:
            # IRLTLT01AUM156N is Long-Term Interest Rates for Australia (10-year bonds)
            url = f"https://api.stlouisfed.org/fred/series/observations?series_id=IRLTLT01AUM156N&api_key={fred_api_key}&file_type=json&sort_order=desc&limit=2"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                obs = resp.json().get("observations", [])
                if len(obs) >= 2:
                    current_rate = float(obs[0]["value"])
                    prev_rate = float(obs[1]["value"]) # Typically a month prior for this series
                    data["au_10y_yield"] = {
                        "current": current_rate,
                        "trend_30d": ((current_rate - prev_rate) / prev_rate) * 100 if prev_rate != 0 else 0
                    }
        except Exception as e:
            print(f"Error fetching FRED API: {e}")
            
    _macro_cache = data.copy()
    _macro_cache_ts = _time.time()
    return data

def calculate_macro_adjustment(sector: str, macro: dict) -> float:
    """Stage 1: Sector Rotation - Adjust predicted returns based on macro regime and rates."""
    adj = 1.0 # Default neutral
    if not macro:
        return adj
        
    s = sector.lower() if sector else ""
    
    au_yield_trend = macro.get("au_10y_yield", {}).get("trend_30d", 0)
    au_yield_current = macro.get("au_10y_yield", {}).get("current", 4.0)

    # 1. Mining / Materials (Commodity sensitive)
    if "material" in s or "mining" in s:
        copper_trend = macro.get("copper", {}).get("trend_30d", 0)
        gold_trend = macro.get("gold", {}).get("trend_30d", 0)
        if copper_trend > 2 or gold_trend > 2:
            adj *= 1.05
        elif copper_trend < -2 or gold_trend < -2:
            adj *= 0.95
            
    # 2. Financials / Banks (Rate margin sensitive)
    elif "financial" in s or "bank" in s:
        # Banks often benefit from moderately rising rates (NIM expansion), but suffer if rates get too high (defaults)
        if 0 < au_yield_trend < 10 and au_yield_current < 6.0:
            adj *= 1.03
        elif au_yield_trend < -5:
            adj *= 0.98

    # 3. Real Estate / REITs (Highly rate sensitive - negative correlation)
    elif "real estate" in s or "reit" in s:
        if au_yield_trend > 2:
            adj *= 0.95  # Rising rates hurt REITs
        elif au_yield_trend < -2:
            adj *= 1.05  # Falling rates help REITs

    # 4. Tech / Growth (Rate sensitive - negative correlation to high yields)
    elif "technology" in s or "tech" in s:
        if au_yield_trend > 5:
            adj *= 0.96

    return adj

def get_sector_relative_strength(symbol: str, sector: str) -> dict:
    """Stage 2 features"""
    return {"sector_rotation_score": calculate_macro_adjustment(sector, get_macro_data())}


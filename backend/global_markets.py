import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import threading

# Tickers to track
MARKETS = {
    "Equities": {
        "^IXIC": "Nasdaq",
        "^GSPC": "S&P 500",
        "^N225": "Nikkei 225",
        "^STI": "STI (Singapore)",
        "^FTSE": "FTSE 100",
        "^BSESN": "BSE Sensex",
        "^AXJO": "ASX 200"
    },
    "Commodities": {
        "GC=F": "Gold",
        "CL=F": "Crude Oil",
        "HG=F": "Copper",
        "SI=F": "Silver"
    },
    "Currencies": {
        "AUDUSD=X": "AUD/USD",
        "JPY=X": "USD/JPY",
        "GBPUSD=X": "GBP/USD"
    }
}

# Cache to avoid spamming Yahoo Finance
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 300  # 5 minutes

def get_global_markets():
    """Fetches global market data, utilizing an in-memory cache."""
    with _cache_lock:
        if "data" in _cache and "time" in _cache:
            if datetime.now() - _cache["time"] < timedelta(seconds=CACHE_TTL_SECONDS):
                return _cache["data"]

    # Gather all tickers
    all_tickers = []
    for category in MARKETS.values():
        all_tickers.extend(category.keys())
    
    # Download 1 month of daily data to calculate daily and 1-month changes
    try:
        # Download data for all tickers at once (fastest method)
        data = yf.download(all_tickers, period="1mo", interval="1d", group_by="ticker", auto_adjust=True, progress=False)
    except Exception as e:
        print(f"Error fetching global markets: {e}")
        return {"error": str(e)}

    results = {"Equities": [], "Commodities": [], "Currencies": []}

    for cat_name, tickers_dict in MARKETS.items():
        for ticker, name in tickers_dict.items():
            try:
                # Handle single vs multi-ticker download format returned by yfinance
                if len(all_tickers) == 1:
                    df = data
                else:
                    df = data[ticker] if ticker in data else pd.DataFrame()
                
                df = df.dropna(subset=['Close'])
                if df.empty or len(df) < 2:
                    continue
                
                current_price = df['Close'].iloc[-1]
                prev_price = df['Close'].iloc[-2]
                month_ago_price = df['Close'].iloc[0]
                
                daily_pct = ((current_price / prev_price) - 1) * 100
                month_pct = ((current_price / month_ago_price) - 1) * 100
                
                results[cat_name].append({
                    "symbol": ticker,
                    "name": name,
                    "price": round(current_price, 2),
                    "daily_pct": round(daily_pct, 2),
                    "month_pct": round(month_pct, 2)
                })
            except Exception as e:
                print(f"Error processing ticker {ticker}: {e}")
                continue

    # Update cache
    with _cache_lock:
        _cache["data"] = results
        _cache["time"] = datetime.now()

    return results

if __name__ == "__main__":
    # Test script functionality
    import json
    print(json.dumps(get_global_markets(), indent=2))

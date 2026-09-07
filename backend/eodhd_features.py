"""EODHD fundamentals → model features (point-in-time where possible).

Fetches /api/fundamentals/{TICKER}.AU for liquid symbols, extracts
9 candidate features:

1. eps_surprise        — latest actual vs estimate (%)
2. eps_estimate_revision — analyst estimate growth (Earnings.Trend)
3. analyst_count        — number of analysts covering
4. pct_insiders         — insider ownership %
5. pct_institutions     — institutional ownership %
6. insider_net_ratio    — net director buy/sell (via /api/insider-transactions)
7. esg_governance       — governance score
8. esg_controversy      — controversy level
9. payout_ratio         — dividend payout ratio

Uses current snapshot as proxy for historical (features are slowly-varying).
"""

import os
import sys
import time
import json
from datetime import date

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EODHD_BASE = "https://eodhd.com/api"


def _api_key() -> str:
    return os.getenv("EODHD_API_KEY", "").strip()


def extract_features(fundamentals: dict, insider_txns: list = None) -> dict:
    """Extract 9 candidate features from a fundamentals JSON + insider txns."""
    f = {}
    
    # Sector and industry classification
    general = fundamentals.get("General", {})
    f["sector"] = general.get("Sector", "")
    f["industry"] = general.get("Industry", "")

    # EPS surprise (latest quarter actual vs estimate)
    hist = fundamentals.get("Earnings", {}).get("History", {})
    if isinstance(hist, dict) and hist:
        latest_key = sorted(hist.keys())[-1]
        latest = hist[latest_key]
        try:
            actual = float(latest.get("epsActual") or 0)
            est = float(latest.get("epsEstimate") or 0)
            if est and actual:
                f["eps_surprise"] = round((actual - est) / abs(est) * 100, 3)
            else:
                f["eps_surprise"] = 0.0
        except Exception:
            f["eps_surprise"] = 0.0
    else:
        f["eps_surprise"] = 0.0

    # EPS estimate revision + analyst count (Earnings.Trend)
    trend = fundamentals.get("Earnings", {}).get("Trend", {})
    if isinstance(trend, dict) and trend:
        latest_key = sorted(trend.keys())[0]  # trend keys are future-dated
        t = trend[latest_key]
        try:
            f["eps_estimate_revision"] = float(t.get("earningsEstimateGrowth") or t.get("growth") or 0) * 100
        except Exception:
            f["eps_estimate_revision"] = 0.0
        try:
            f["analyst_count"] = float(t.get("earningsEstimateNumberOfAnalysts") or 0)
        except Exception:
            f["analyst_count"] = 0.0
    else:
        f["eps_estimate_revision"] = 0.0
        f["analyst_count"] = 0.0

    # Ownership
    ss = fundamentals.get("SharesStats", {})
    f["pct_insiders"] = float(ss.get("PercentInsiders") or 0)
    f["pct_institutions"] = float(ss.get("PercentInstitutions") or 0)

    # ESG
    esg = fundamentals.get("ESGScores", {})
    f["esg_governance"] = float(esg.get("GovernanceScore") or 0)
    f["esg_controversy"] = float(esg.get("ControversyLevel") or 0)

    # Payout ratio
    sd = fundamentals.get("SplitsDividends", {})
    f["payout_ratio"] = float(sd.get("PayoutRatio") or 0)

    # Insider net ratio (from insider transactions)
    if insider_txns is None:
        f["insider_net_ratio"] = 0.0
    else:
        buys = sells = 0
        for t in insider_txns:
            ttype = (t.get("transactionType") or t.get("type") or "").strip().upper()
            if "BUY" in ttype or "PURCHASE" in ttype:
                buys += 1
            elif "SELL" in ttype or "DISPOSE" in ttype:
                sells += 1
        total = buys + sells
        f["insider_net_ratio"] = round((buys - sells) / total, 4) if total > 0 else 0.0

    return f


def fetch_feature_map(symbols: list, db_conn=None) -> dict:
    """Fetch + extract 9 candidate features for symbols.

    Returns dict: symbol -> feature dict. Also stores to DB + file.
    """
    key = _api_key()
    result = {}
    for i, sym in enumerate(symbols):
        try:
            r = requests.get(
                f"{EODHD_BASE}/fundamentals/{sym}.AU",
                params={"api_token": key, "fmt": "json"},
                timeout=30,
            )
            if r.status_code != 200:
                result[sym] = None
                continue
            fundamentals = r.json()

            # Fetch insider transactions
            insider_txns = []
            try:
                r2 = requests.get(
                    f"{EODHD_BASE}/insider-transactions",
                    params={"api_token": key, "symbol": f"{sym}.AU", "limit": 50, "fmt": "json"},
                    timeout=20,
                )
                if r2.status_code == 200:
                    insider_txns = r2.json()
            except Exception:
                pass

            result[sym] = extract_features(fundamentals, insider_txns)
        except Exception:
            result[sym] = None
        if (i + 1) % 50 == 0:
            ok = sum(1 for v in result.values() if v is not None)
            print(f"[EODHD-Features] {i+1}/{len(symbols)} ({ok} ok)", flush=True)
        time.sleep(0.15)

    # Save to file for reuse
    ok_map = {k: v for k, v in result.items() if v is not None}
    # Save sector and industry information as well
    sector_map = {}
    for symbol, features in ok_map.items():
        sector_map[symbol] = {
            "sector": features.get("sector", ""),
            "industry": features.get("industry", "")
        }
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "sector_map.json"), "w") as f:
        json.dump(sector_map, f, indent=2)
    print(f"[EODHD-Features] Saved sector map for {len(sector_map)} symbols to data/sector_map.json", flush=True)
    
    with open(os.path.join(data_dir, "eodhd_features.json"), "w") as f:
        json.dump(ok_map, f, indent=2)
    print(f"[EODHD-Features] Saved {len(ok_map)} feature maps to data/eodhd_features.json", flush=True)

    return result


def load_sector_map() -> dict:
    """Load sector and industry map from file."""
    try:
        with open("/app/data/sector_map.json") as f:
            return json.load(f)
    except Exception:
        return {}


if __name__ == "__main__":
    from config.universe import get_universe_symbols
    syms = get_universe_symbols("core") + get_universe_symbols("broad")
    print(f"Fetching features for {len(syms)} symbols...")
    fetch_feature_map(syms)

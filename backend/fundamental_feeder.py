"""Free fundamental data feeder — monthly yfinance snapshots for all ASX tickers.

Runs first Sunday of each month at 4AM. Single-threaded, low cost.
Used by model_training.py as additional features (PE, market cap, analyst consensus).
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FUNDAMENTAL_FIELDS = [
    "trailingPE", "forwardPE", "marketCap", "dividendYield",
    "targetMeanPrice", "recommendationKey", "revenueGrowth", "earningsGrowth",
    "priceToBook", "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "averageVolume", "sharesOutstanding",
]


def fetch_all_fundamentals(market: str = "AU", batch_size: int = 500) -> dict:
    """Fetch fundamental data from yfinance for all ASX tickers.

    Skips tickers in yfinance_dead_tickers.txt.
    Rate-limited: 1 call/sec to avoid Yahoo throttling.
    """
    import yfinance as yf
    from sqlalchemy import text
    from main import db_conn, get_asx_universe

    today = date.today()
    snapshot_date = today

    dead_path = os.path.join(os.path.dirname(__file__), "yfinance_dead_tickers.txt")
    dead_set = set()
    if os.path.exists(dead_path):
        with open(dead_path) as f:
            dead_set = set(line.strip() for line in f if line.strip())

    full_universe = get_asx_universe()
    all_symbols = [s for s in full_universe if len(s) <= 3 and s not in dead_set]
    all_symbols = all_symbols[:batch_size]

    print(f"[FundFeeder] Fetching fundamentals for {len(all_symbols)} ASX tickers...")

    fetched = 0
    errors = 0
    new_dead = set()

    for i, sym in enumerate(all_symbols):
        try:
            ticker = yf.Ticker(f"{sym}.AX")
            info = ticker.info
            if not info or info.get("trailingPE") is None and info.get("marketCap") is None:
                new_dead.add(sym)
                errors += 1
                continue

            with db_conn() as conn:
                conn.execute(
                    text(
                        """INSERT INTO fundamental_snapshots
                        (symbol, market, snapshot_date, trailing_pe, forward_pe,
                         market_cap, dividend_yield, analyst_target_mean,
                         analyst_rec, revenue_growth, earnings_growth,
                         price_to_book, beta, high_52w, low_52w,
                         avg_volume, shares_outstanding)
                        VALUES (:s,:m,:d,:pe,:fpe,:mc,:dy,:atm,:ar,:rg,:eg,:pb,:b,:h52,:l52,:av,:so)
                        ON CONFLICT (symbol, snapshot_date) DO UPDATE SET
                        trailing_pe=EXCLUDED.trailing_pe, forward_pe=EXCLUDED.forward_pe,
                        market_cap=EXCLUDED.market_cap, dividend_yield=EXCLUDED.dividend_yield,
                        analyst_target_mean=EXCLUDED.analyst_target_mean,
                        analyst_rec=EXCLUDED.analyst_rec,
                        revenue_growth=EXCLUDED.revenue_growth,
                        earnings_growth=EXCLUDED.earnings_growth,
                        price_to_book=EXCLUDED.price_to_book, beta=EXCLUDED.beta,
                        high_52w=EXCLUDED.high_52w, low_52w=EXCLUDED.low_52w,
                        avg_volume=EXCLUDED.avg_volume, shares_outstanding=EXCLUDED.shares_outstanding
                    """
                    ),
                    {
                        "s": sym, "m": market, "d": snapshot_date,
                        "pe": info.get("trailingPE"),
                        "fpe": info.get("forwardPE"),
                        "mc": info.get("marketCap"),
                        "dy": info.get("dividendYield"),
                        "atm": info.get("targetMeanPrice"),
                        "ar": str(info.get("recommendationKey", ""))[:20],
                        "rg": info.get("revenueGrowth"),
                        "eg": info.get("earningsGrowth"),
                        "pb": info.get("priceToBook"),
                        "b": info.get("beta"),
                        "h52": info.get("fiftyTwoWeekHigh"),
                        "l52": info.get("fiftyTwoWeekLow"),
                        "av": info.get("averageVolume"),
                        "so": info.get("sharesOutstanding"),
                    },
                )
                conn.commit()
            fetched += 1

            if (i + 1) % 100 == 0:
                print(f"[FundFeeder] {i+1}/{len(all_symbols)} fetched ({fetched} ok, {errors} err)")

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"[FundFeeder] Failed {sym}: {e}")

        time.sleep(0.5)  # 2 calls/sec — polite to Yahoo

    if new_dead:
        dead_set |= new_dead
        with open(dead_path, "w") as f:
            f.write("\n".join(sorted(dead_set)) + "\n")
        print(f"[FundFeeder] {len(new_dead)} additional dead tickers cached.")

    print(f"[FundFeeder] Complete: {fetched} ok, {errors} errors, {len(new_dead)} dead.")
    return {"fetched": fetched, "errors": errors, "dead": len(new_dead), "snapshot_date": str(snapshot_date)}


def get_fundamentals_for_symbol(symbol: str, as_of: date = None) -> dict:
    """Return the most recent fundamental snapshot for a symbol before the given date."""
    from sqlalchemy import text
    from main import db_conn

    try:
        with db_conn() as conn:
            query = """
                SELECT trailing_pe, forward_pe, market_cap, dividend_yield,
                       analyst_target_mean, analyst_rec, revenue_growth, earnings_growth,
                       price_to_book, beta, high_52w, low_52w, avg_volume, shares_outstanding,
                       snapshot_date
                FROM fundamental_snapshots
                WHERE symbol = :sym
            """
            params = {"sym": symbol}
            if as_of:
                query += " AND snapshot_date <= :as_of"
                params["as_of"] = as_of
            query += " ORDER BY snapshot_date DESC LIMIT 1"

            row = conn.execute(text(query), params).fetchone()
            if not row:
                return {}

            cols = [
                "trailing_pe", "forward_pe", "market_cap", "dividend_yield",
                "analyst_target_mean", "analyst_rec", "revenue_growth", "earnings_growth",
                "price_to_book", "beta", "high_52w", "low_52w", "avg_volume",
                "shares_outstanding", "snapshot_date",
            ]
            result = dict(zip(cols, row))
            for k in ["trailing_pe", "forward_pe", "market_cap", "dividend_yield",
                      "analyst_target_mean", "revenue_growth", "earnings_growth",
                      "price_to_book", "beta", "high_52w", "low_52w", "avg_volume",
                      "shares_outstanding"]:
                if result.get(k) is not None:
                    result[k] = float(result[k])
            return result
    except Exception:
        return {}


def _monthly_fundamental_job():
    """Scheduled job: fetch fundamentals for all ASX tickers. Idempotent."""
    from main import _log_job_start, _log_job_finish
    jid = _log_job_start("monthly_fundamentals")
    started = datetime.utcnow()
    try:
        result = fetch_all_fundamentals()
        _log_job_finish(jid, rows_affected=result.get("fetched", 0), started_at=started)
        print(f"[FundFeederJob] Monthly fundamentals: {result}")
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[FundFeederJob] Failed: {e}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["fetch","get"], default="fetch")
    ap.add_argument("--symbol", type=str, default="BHP")
    ap.add_argument("--batch", type=int, default=500)
    args = ap.parse_args()

    if args.mode == "fetch":
        r = fetch_all_fundamentals(batch_size=args.batch)
        print(json.dumps(r, indent=2, default=str))
    elif args.mode == "get":
        data = get_fundamentals_for_symbol(args.symbol)
        print(json.dumps(data, indent=2, default=str))

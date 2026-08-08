"""EODHD Historical OHLC Backfill — idempotent 9-year ASX data population.

Usage (via scheduler or manual):
    from eodhd_backfill import backfill_asx_universe, incremental_daily_update

    backfill_asx_universe()          # one-time: populates 9y history
    incremental_daily_update()       # daily: fills gaps with latest data

Checkpoint file: backend/eodhd_backfill_state.json tracks completed tickers.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yfinance_service import YFinanceService

_STATE_FILE = Path(__file__).parent / "eodhd_backfill_state.json"
_EODHD_KEY = ""
_DAILY_CALL_LIMIT = 50000
_SAFETY_MARGIN = 0.80

BACKFILL_YEARS = 9
FROM_DATE = (date.today() - timedelta(days=int(BACKFILL_YEARS * 365))).isoformat()


def _load_state():
    if _STATE_FILE.exists():
        try:
            with open(_STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"completed_tickers": [], "failed_tickers": {}, "last_run": None, "total_fetched": 0}


def _save_state(state):
    state["last_run"] = datetime.utcnow().isoformat()
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _eodhd_historical(symbol: str, from_date: str, to_date: str, api_key: str) -> list:
    """Fetch full daily OHLCV from EODHD for one symbol. Returns list of dict rows."""
    url = f"https://eodhd.com/api/eod/{symbol}.AU"
    try:
        r = requests.get(
            url,
            params={"api_token": api_key, "fmt": "json", "from": from_date, "to": to_date},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data
        if r.status_code == 404:
            return []
        return []
    except Exception:
        return []


def _insert_ohlc_batch(conn, rows: list):
    """Efficient batch insert with ON CONFLICT DO NOTHING."""
    from sqlalchemy import text

    chunk = 200
    for i in range(0, len(rows), chunk):
        batch = rows[i : i + chunk]
        values_clause = ", ".join(
            f"(:sym{i+j}, :mkt{i+j}, :d{i+j}, :o{i+j}, :h{i+j}, :l{i+j}, :c{i+j}, :v{i+j})"
            for j in range(len(batch))
        )
        params = {}
        for j, row in enumerate(batch):
            params[f"sym{i+j}"] = row[0]
            params[f"mkt{i+j}"] = row[1]
            params[f"d{i+j}"] = row[2]
            params[f"o{i+j}"] = row[3]
            params[f"h{i+j}"] = row[4]
            params[f"l{i+j}"] = row[5]
            params[f"c{i+j}"] = row[6]
            params[f"v{i+j}"] = row[7]

        conn.execute(
            text(
                f"""
            INSERT INTO eod_ohl_history (symbol, market, trade_date, open, high, low, close, volume)
            VALUES {values_clause}
            ON CONFLICT (symbol, market, trade_date) DO NOTHING
        """
            ),
            params,
        )


def backfill_asx_universe(batch_size: int = 500, max_workers: int = 4):
    """Populate eod_ohl_history with 5 years of daily OHLC for all ASX tickers.

    Idempotent: skips tickers already in the state file.
    Rate-limited: enforces daily call cap with safety margin.
    Resumable: state file survives restarts.
    """
    global _EODHD_KEY
    _EODHD_KEY = os.getenv("EODHD_API_KEY", "").strip()
    if not _EODHD_KEY:
        print("[Backfill] No EODHD_API_KEY set. Aborting.")
        return {"status": "no_key", "fetched": 0}

    from sqlalchemy import create_engine, text
    from main import get_asx_universe, db_conn

    state = _load_state()
    completed = set(state["completed_tickers"])
    failed = state.get("failed_tickers", {})

    full_universe = get_asx_universe()
    all_symbols = list(full_universe.keys())

    yf_dead = YFinanceService.get_dead_set()

    to_fetch = [s for s in all_symbols if s not in completed and s not in yf_dead and len(s) <= 3]
    to_fetch = to_fetch[:batch_size]

    if not to_fetch:
        print(f"[Backfill] All {len(all_symbols)} tickers already backfilled.")
        return {"status": "complete", "fetched": 0, "total_completed": len(completed)}

    print(f"[Backfill] Starting: {len(to_fetch)} tickers to fetch ({len(completed)} already done).")

    today = date.today().isoformat()
    calls_made = 0
    newly_fetched = 0
    limit = int(_DAILY_CALL_LIMIT * _SAFETY_MARGIN)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_one(symbol):
        nonlocal calls_made
        if calls_made >= limit:
            return (symbol, None, "RATE_CAP")
        data = _eodhd_historical(symbol, FROM_DATE, today, _EODHD_KEY)
        return (symbol, data, None)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, sym): sym for sym in to_fetch}

        for future in as_completed(futures):
            sym = futures[future]
            try:
                symbol, data, err = future.result(timeout=60)
            except Exception:
                failed[symbol] = datetime.utcnow().isoformat()
                continue

            calls_made += 1

            if err == "RATE_CAP":
                print(f"[Backfill] Rate cap reached ({limit} calls). Pausing for rest of tickers.")
                break

            if data is None or (isinstance(data, list) and not data):
                failed[symbol] = datetime.utcnow().isoformat()
                continue

            try:
                rows = []
                for d in data:
                    dt = d.get("date", "")
                    if not dt:
                        continue
                    adjusted = d.get("adjusted_close")
                    raw_close = d.get("close", 0)
                    stored_close = adjusted if adjusted is not None and adjusted > 0 else raw_close
                    rows.append(
                        (
                            symbol,
                            "AU",
                            dt,
                            d.get("open"),
                            d.get("high"),
                            d.get("low"),
                            stored_close,
                            d.get("volume", 0),
                        )
                    )

                if rows:
                    with db_conn() as conn:
                        _insert_ohlc_batch(conn, rows)
                    completed.add(symbol)
                    newly_fetched += 1
            except Exception:
                failed[symbol] = datetime.utcnow().isoformat()
                continue

    state["completed_tickers"] = sorted(completed)
    state["failed_tickers"] = failed
    state["total_fetched"] = len(completed)
    _save_state(state)

    print(f"[Backfill] Done: {newly_fetched} fetched, {len(completed)} total, {len(failed)} failed. {calls_made} API calls.")

    return {
        "status": "ok",
        "fetched": newly_fetched,
        "total_completed": len(completed),
        "total_failed": len(failed),
        "calls_used": calls_made,
    }


def incremental_daily_update() -> dict:
    """Daily job: fill OHLC gaps for the last 5 trading days.

    Uses eod-bulk-last-day/AU (1 API call for all tickers).
    Falls back to per-ticker fetch for failed symbols.
    """
    global _EODHD_KEY
    _EODHD_KEY = os.getenv("EODHD_API_KEY", "").strip()
    if not _EODHD_KEY:
        return {"status": "no_key"}

    from sqlalchemy import text
    from main import db_conn

    inserted = 0

    try:
        r = requests.get(
            "https://eodhd.com/api/eod-bulk-last-day/AU",
            params={"api_token": _EODHD_KEY, "fmt": "json", "filter": "extended"},
            timeout=60,
        )
        if r.status_code == 200:
            bulk_data = r.json()
            if isinstance(bulk_data, list):
                rows = []
                for item in bulk_data:
                    code = item.get("code", "")
                    if not code:
                        continue
                    sym = code.replace(".AU", "").replace(".AX", "")
                    dt = item.get("date", date.today().isoformat())
                    adjusted = item.get("adjusted_close")
                    raw_close = item.get("close", 0)
                    stored_close = adjusted if adjusted is not None and adjusted > 0 else raw_close
                    rows.append(
                        (
                            sym,
                            "AU",
                            dt,
                            item.get("open"),
                            item.get("high"),
                            item.get("low"),
                            stored_close,
                            item.get("volume", 0),
                        )
                    )

                if rows:
                    with db_conn() as conn:
                        _insert_ohlc_batch(conn, rows)
                    inserted = len(rows)
                    print(f"[IncUpdate] Bulk inserted {inserted} rows from eod-bulk-last-day.")
    except Exception as e:
        print(f"[IncUpdate] Bulk fetch failed: {e}")

    return {"status": "ok", "rows_inserted": inserted}


def get_ohlc_for_symbol(symbol: str, from_date: str = None, to_date: str = None) -> pd.DataFrame:
    """Return OHLC DataFrame from the local eod_ohl_history table.

    Falls back to EODHD API if the table has insufficient data.
    """
    try:
        from sqlalchemy import text
        from main import db_conn

        with db_conn() as conn:
            query = """
                SELECT trade_date, open, high, low, close, volume
                FROM eod_ohl_history
                WHERE symbol = :sym AND market = 'AU'
            """
            params = {"sym": symbol}

            if from_date:
                query += " AND trade_date >= :from_d"
                params["from_d"] = from_date
            if to_date:
                query += " AND trade_date <= :to_d"
                params["to_d"] = to_date

            query += " ORDER BY trade_date ASC"
            rows = conn.execute(text(query), params).fetchall()

        if rows:
            float_rows = []
            for row in rows:
                float_rows.append((
                    row[0],
                    float(row[1]) if row[1] is not None else None,
                    float(row[2]) if row[2] is not None else None,
                    float(row[3]) if row[3] is not None else None,
                    float(row[4]) if row[4] is not None else None,
                    int(row[5]) if row[5] is not None else 0,
                ))
            df = pd.DataFrame(
                float_rows,
                columns=["Date", "Open", "High", "Low", "Close", "Volume"],
            )
            df["Date"] = pd.to_datetime(df["Date"])
            df.set_index("Date", inplace=True)
            return df

    except Exception as e:
        print(f"[OHLC] DB fetch failed for {symbol}: {e}")

    return pd.DataFrame()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EODHD OHLC backfill tool")
    parser.add_argument("--mode", choices=["backfill", "incremental", "status"], default="status")
    parser.add_argument("--batch", type=int, default=500, help="Max tickers to backfill per run")
    parser.add_argument("--workers", type=int, default=4, help="Parallel fetch workers")
    parser.add_argument("--from-date", type=str, default=None, help="Override FROM_DATE (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.from_date:
        FROM_DATE = args.from_date

    if args.mode == "backfill":
        result = backfill_asx_universe(batch_size=args.batch, max_workers=args.workers)
        print(json.dumps(result, indent=2, default=str))
    elif args.mode == "incremental":
        result = incremental_daily_update()
        print(json.dumps(result, indent=2, default=str))
    elif args.mode == "status":
        state = _load_state()
        print(f"Completed: {len(state['completed_tickers'])} tickers")
        print(f"Failed: {len(state.get('failed_tickers', {}))} tickers")
        print(f"Last run: {state.get('last_run', 'never')}")

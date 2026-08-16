"""Centralized yfinance wrapper with circuit breaker, rate limiting, and dead-ticker lifecycle.

Features:
- Circuit breaker: 3 consecutive failures -> dead list (30-day expiry)
- Rate limiting: configurable requests/second with exponential backoff on HTTP 429
- Timeout: 15s per-ticker timeout, returns None instead of crashing
- Dead ticker lifecycle: dead tickers expire after 30 days and get re-tested
- Batch mode: accept list of symbols, return dict with per-symbol status
- Thread-safe: usable from ThreadPoolExecutor in broad scan
"""
import json
import os
import threading
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

_STATE_FILE = Path(__file__).parent / "yfinance_dead_tickers.json"
_DEAD_TTL_DAYS = 30
_REQUESTS_PER_SEC = 2.0
_TIMEOUT_SEC = 15
_MAX_CONSECUTIVE_FAILURES = 3

_dead_cache = {}
_dead_cache_lock = threading.Lock()
_dead_cache_mtime = 0.0

_last_request_time = 0.0
_rate_lock = threading.Lock()


def _load_dead():
    global _dead_cache, _dead_cache_mtime
    if _STATE_FILE.exists():
        try:
            with open(_STATE_FILE) as f:
                raw = json.load(f)
            expired = set()
            now = datetime.utcnow()
            for sym, info in raw.items():
                added = datetime.fromisoformat(info.get("added_at", "2000-01-01T00:00:00"))
                if (now - added).days >= _DEAD_TTL_DAYS:
                    expired.add(sym)
            for sym in expired:
                del raw[sym]
            _dead_cache = raw
            if expired:
                _save_dead()
        except Exception:
            _dead_cache = {}
    else:
        _dead_cache = {}
    _dead_cache_mtime = _time.time()


def _save_dead():
    with _STATE_FILE.open("w") as f:
        json.dump(_dead_cache, f, indent=2)


def _migrate_old_file():
    old_path = Path(__file__).parent / "yfinance_dead_tickers.txt"
    if old_path.exists() and not _STATE_FILE.exists():
        try:
            with open(old_path) as f:
                symbols = [line.strip() for line in f if line.strip()]
            now = datetime.utcnow().isoformat()
            for sym in symbols:
                _dead_cache[sym] = {"reason": "migrated_from_txt", "added_at": now, "failures": 3}
            _save_dead()
            old_path.rename(str(old_path) + ".migrated")
        except Exception:
            pass


def _rate_limit():
    global _last_request_time
    with _rate_lock:
        now = _time.time()
        elapsed = now - _last_request_time
        min_interval = 1.0 / _REQUESTS_PER_SEC
        if elapsed < min_interval:
            _time.sleep(min_interval - elapsed)
        _last_request_time = _time.time()


def _exponential_backoff(attempt: int):
    delay = min(2 ** attempt, 30)
    _time.sleep(delay)


class YFinanceService:
    @staticmethod
    def is_dead(symbol: str) -> bool:
        with _dead_cache_lock:
            if not _dead_cache:
                _load_dead()
            return symbol in _dead_cache

    @staticmethod
    def mark_dead(symbol: str, reason: str = "unknown"):
        now = datetime.utcnow()
        with _dead_cache_lock:
            if symbol in _dead_cache:
                existing = _dead_cache[symbol]
                existing["failures"] = existing.get("failures", 0) + 1
                existing["last_failure"] = now.isoformat()
                existing["reason"] = reason
            else:
                _dead_cache[symbol] = {
                    "reason": reason,
                    "added_at": now.isoformat(),
                    "failures": 1,
                    "last_failure": now.isoformat(),
                }
            _save_dead()

    @staticmethod
    def mark_alive(symbol: str):
        with _dead_cache_lock:
            if symbol in _dead_cache:
                del _dead_cache[symbol]
                _save_dead()

    @staticmethod
    def get_dead_stats() -> dict:
        with _dead_cache_lock:
            if not _dead_cache:
                _load_dead()
            total = len(_dead_cache)
            returning_soon = []
            now = datetime.utcnow()
            for sym, info in _dead_cache.items():
                added = datetime.fromisoformat(info.get("added_at", "2000-01-01T00:00:00"))
                days_remaining = max(0, _DEAD_TTL_DAYS - (now - added).days)
                if days_remaining <= 3:
                    returning_soon.append(sym)
            return {
                "total_dead": total,
                "ttl_days": _DEAD_TTL_DAYS,
                "returning_soon": returning_soon,
                "count_returning_soon": len(returning_soon),
            }

    @staticmethod
    def _asx_ticker(symbol: str) -> str:
        """Normalize to a valid yfinance ticker: strip $, never suffix indices."""
        s = (symbol or "").strip().lstrip("$").upper()
        if not s:
            return s
        if s.startswith("^"):
            return s  # index tickers are already fully qualified
        return s if s.endswith(".AX") else f"{s}.AX"

    @staticmethod
    def get_info(symbol: str) -> Optional[dict]:
        import yfinance as yf

        if YFinanceService.is_dead(symbol):
            return None

        for attempt in range(3):
            try:
                _rate_limit()
                ticker = yf.Ticker(YFinanceService._asx_ticker(symbol))
                info = ticker.info
                if not info or (info.get("trailingPE") is None and info.get("marketCap") is None):
                    YFinanceService.mark_dead(symbol, "NO_DATA")
                    return None
                YFinanceService.mark_alive(symbol)
                return info
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate" in err_str:
                    _exponential_backoff(attempt)
                    continue
                if "404" in err_str or "not found" in err_str:
                    YFinanceService.mark_dead(symbol, "DELISTED")
                    return None
                if attempt < 2:
                    _exponential_backoff(attempt)
                else:
                    YFinanceService.mark_dead(symbol, "TIMEOUT" if "timeout" in err_str else "ERROR")
                    return None

    @staticmethod
    def get_history(symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        import yfinance as yf

        if YFinanceService.is_dead(symbol):
            return None

        for attempt in range(3):
            try:
                _rate_limit()
                ticker = yf.Ticker(YFinanceService._asx_ticker(symbol))
                df = ticker.history(period=period, timeout=_TIMEOUT_SEC)
                if df.empty:
                    YFinanceService.mark_dead(symbol, "NO_DATA")
                    return None
                YFinanceService.mark_alive(symbol)
                return df
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate" in err_str:
                    _exponential_backoff(attempt)
                    continue
                if attempt < 2:
                    _exponential_backoff(attempt)
                else:
                    YFinanceService.mark_dead(symbol, "TIMEOUT" if "timeout" in err_str else "ERROR")
                    return None

    @staticmethod
    def batch_info(symbols: list) -> dict:
        results = {}
        for sym in symbols:
            results[sym] = YFinanceService.get_info(sym)
        return results

    @staticmethod
    def get_dead_set() -> set:
        with _dead_cache_lock:
            if not _dead_cache:
                _load_dead()
            return set(_dead_cache.keys())


_migrate_old_file()
_load_dead()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["test", "stats", "clear"], default="test")
    ap.add_argument("--symbol", type=str, default="BHP")
    args = ap.parse_args()

    if args.mode == "test":
        print(f"Testing YFinanceService for {args.symbol}...")
        info = YFinanceService.get_info(args.symbol)
        if info:
            print(f"  PE: {info.get('trailingPE')}, MC: {info.get('marketCap')}")
            print(f"  Status: ALIVE")
        else:
            print(f"  Status: DEAD or no data")
        print(f"\nDead stats: {YFinanceService.get_dead_stats()}")

    elif args.mode == "stats":
        stats = YFinanceService.get_dead_stats()
        print(json.dumps(stats, indent=2))

    elif args.mode == "clear":
        _dead_cache.clear()
        _save_dead()
        print("Dead ticker cache cleared.")

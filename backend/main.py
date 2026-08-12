"""ASX Stock Predictor Backend.

FastAPI + SQLAlchemy data layer + dual LLM providers (Local/OpenAI).
"""

import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, date
import hashlib
import hmac
import json
import math
import os
import sys
from pathlib import Path
import re
import random
from typing import List, Optional
from uuid import uuid4

import macro_model

from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import numpy as np
import threading
from openai import OpenAI
try:
    from groq import Groq as GroqClient
    GROQ_SDK_AVAILABLE = True
except ImportError:
    GROQ_SDK_AVAILABLE = False
import pandas as pd
from pydantic import BaseModel
import jwt
import requests
from sqlalchemy import create_engine, text
import sqlalchemy.exc
from sqlalchemy.exc import SQLAlchemyError
import yfinance as yf

try:
    from agentic_brain import run_agentic_analysis
except ImportError as e:
    print(f"Agentic Brain not loaded: {e}")
    run_agentic_analysis = None

try:
    from portfolio_gate import PortfolioGate, calculate_position_size as pg_calculate_position_size
    _PORTFOLIO_GATE = PortfolioGate()
    _PORTFOLIO_GATE_AVAILABLE = True
except ImportError as e:
    print(f"PortfolioGate not loaded: {e}")
    _PORTFOLIO_GATE = None
    _PORTFOLIO_GATE_AVAILABLE = False

try:
    from circuit_breaker import DrawdownCircuitBreaker
    _CIRCUIT_BREAKER = DrawdownCircuitBreaker()
    _CIRCUIT_BREAKER_AVAILABLE = True
except ImportError as e:
    print(f"CircuitBreaker not loaded: {e}")
    _CIRCUIT_BREAKER = None
    _CIRCUIT_BREAKER_AVAILABLE = False

try:
    from core_sleeve import (compute_core_sleeve_state, create_core_position,
                             close_core_position, get_core_candidates, get_next_review_date)
    _CORE_SLEEVE_AVAILABLE = True
except ImportError as e:
    print(f"Core Sleeve not loaded: {e}")
    _CORE_SLEEVE_AVAILABLE = False
    compute_core_sleeve_state = None
    create_core_position = None
    close_core_position = None
    get_core_candidates = None
    get_next_review_date = None

try:
    from devils_advocate import run_devils_advocate
    _DEVILS_ADVOCATE_AVAILABLE = True
except ImportError as e:
    print(f"Devils Advocate not loaded: {e}")
    _DEVILS_ADVOCATE_AVAILABLE = False
    run_devils_advocate = None

try:
    from kill_switch import compute_kill_state, persist_kill_state, get_latest_kill_state, build_kill_switch_telegram
    _KILL_SWITCH_AVAILABLE = True
except ImportError as e:
    print(f"Kill Switch not loaded: {e}")
    _KILL_SWITCH_AVAILABLE = False
    compute_kill_state = None

try:
    from model_health import evaluate_signal_outcomes, persist_model_health
    _MODEL_HEALTH_AVAILABLE = True
except ImportError as e:
    print(f"Model Health not loaded: {e}")
    _MODEL_HEALTH_AVAILABLE = False

try:
    from data_sanity import check_data_sanity, log_sanity_check
    _DATA_SANITY_AVAILABLE = True
except ImportError as e:
    print(f"Data Sanity not loaded: {e}")
    _DATA_SANITY_AVAILABLE = False

try:
    from stale_heartbeat import check_stale_sessions
    _STALE_HEARTBEAT_AVAILABLE = True
except ImportError as e:
    print(f"Stale Heartbeat not loaded: {e}")
    _STALE_HEARTBEAT_AVAILABLE = False

try:
    from pypfopt import EfficientFrontier, risk_models, expected_returns, HRPOpt
    PYPFOPT_AVAILABLE = True
except ImportError:
    PYPFOPT_AVAILABLE = False

try:
    from fredapi import Fred
    FREDAPI_AVAILABLE = True
except ImportError:
    FREDAPI_AVAILABLE = False

try:
    import edgar as edgar_lib
    EDGAR_AVAILABLE = True
except ImportError:
    EDGAR_AVAILABLE = False

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
EODHD_API_KEY = os.getenv("EODHD_API_KEY", "").strip()

# ── UAT / Production Mode ─────────────────────────────────────────────────────
# Set ENV=UAT to enable safety guards: reduced scan cap, options channel disabled,
# extra inter-call sleep, and dry-run mode for LLM calls.
UAT_MODE = os.getenv("ENV", "production").upper() == "UAT"

# ── Paper Bootstrapping Mode ────────────────────────────────────────────────────
# When PAPER_BOOTSTRAP_MODE=1, the WFO capital gate is bypassed for paper trades
# (real capital is never deployed).  This lets the system auto-execute AI-approved
# picks as paper trades, accumulate evaluation data, and self-learn without
# waiting for manual user action.  Max 5 bootstrapping positions are kept open.
# Once WFO graduates to AMBER/GREEN, bootstrapping naturally phases out.
PAPER_BOOTSTRAP_MODE = os.getenv("PAPER_BOOTSTRAP_MODE", "1").strip() in {"1", "true", "yes", "on"}
PAPER_BOOTSTRAP_MAX_POSITIONS = int(os.getenv("PAPER_BOOTSTRAP_MAX_POSITIONS", "5"))
PAPER_BOOTSTRAP_AUTO_EXECUTE = os.getenv("PAPER_BOOTSTRAP_AUTO_EXECUTE", "1").strip() in {"1", "true", "yes", "on"}

# ── EODHD Rate Limiter (thread-safe token bucket for 20 calls/min free tier) ──
_EODHD_RATE_LOCK = threading.Lock()
_EODHD_LAST_CALL = 0.0
_EODHD_MIN_INTERVAL = 0.0  # No rate limit needed — 100K calls/day paid plan, scan uses ~5K
_YFINANCE_LOCK = threading.Lock()  # yfinance global state is not thread-safe

def _eodhd_rate_limit():
    """Block the calling thread until the EODHD rate-limit window reopens."""
    global _EODHD_LAST_CALL
    with _EODHD_RATE_LOCK:
        now = time.time()
        wait = _EODHD_MIN_INTERVAL - (now - _EODHD_LAST_CALL)
        if wait > 0:
            time.sleep(wait)
        _EODHD_LAST_CALL = time.time()

# Initialise FRED client if key is available
_fred_client = None
if FREDAPI_AVAILABLE and FRED_API_KEY:
    try:
        _fred_client = Fred(api_key=FRED_API_KEY)
    except Exception:
        _fred_client = None


# ── EODHD Dynamic Universe Cache ─────────────────────────────────────────────
# When EODHD_API_KEY is set (paid plan), we fetch the full ASX common-stock
# list (~1,600+ tickers) instead of relying on the 257-ticker hardcoded dict.
# This is cached in-memory for 24 hours and refreshed at startup.
_EODHD_ASX_UNIVERSE: Optional[dict] = None   # {code: name}
_EODHD_ASX_UNIVERSE_TS: float = 0.0
_EODHD_UNIVERSE_TTL: float = 86400.0  # 24 hours


def _fetch_eodhd_asx_universe() -> dict:
    """Fetch the full ASX common-stock list from EODHD exchange-symbol-list.

    Returns a {ticker: name} dict of clean 2-5 letter ASX codes.  Falls back
    to an empty dict on any error so callers can fall back to ASX_COMPANIES.
    Applies a minimal pre-filter:
      - Type = 'Common Stock' only (excludes Funds, Notes, Preferred)
      - Code must match ^[A-Z]{2,5}$ (real ASX ticker format)
      - Skips obvious micro-cap / shell name patterns to reduce scan noise
    """
    if not EODHD_API_KEY:
        return {}
    try:
        resp = requests.get(
            "https://eodhd.com/api/exchange-symbol-list/AU",
            params={"api_token": EODHD_API_KEY, "fmt": "json"},
            timeout=20,
        )
        if resp.status_code != 200:
            print(f"[EODHD Universe] HTTP {resp.status_code} — falling back to hardcoded list")
            return {}
        data = resp.json()
        universe: dict = {}
        skip_words = {"ltd", "limited", "resources", "minerals", "metals"}  # not skip words, just common
        # Words that strongly suggest shells / tiny illiquid tickers we want to exclude
        shell_words = {"dormant", "administration", "liquidat", "suspended"}
        for item in data:
            code = item.get("Code", "")
            name = item.get("Name") or ""
            typ  = item.get("Type", "")
            if typ != "Common Stock":
                continue
            if not re.match(r"^[A-Z]{2,5}$", code):
                continue
            name_lower = name.lower()
            if any(w in name_lower for w in shell_words):
                continue
            universe[code] = name
        print(f"[EODHD Universe] Loaded {len(universe)} ASX common stocks")
        return universe
    except Exception as exc:
        print(f"[EODHD Universe] Failed to fetch: {exc}")
        return {}


def get_asx_universe() -> dict:
    """Return the best available ASX universe dict {ticker: name}.

    Priority:
      1. EODHD dynamic list (refreshed every 24h) — 1,600+ stocks
      2. Hardcoded ASX_COMPANIES — 257 curated stocks
    """
    global _EODHD_ASX_UNIVERSE, _EODHD_ASX_UNIVERSE_TS
    now = time.time()
    if EODHD_API_KEY:
        if _EODHD_ASX_UNIVERSE is None or (now - _EODHD_ASX_UNIVERSE_TS) > _EODHD_UNIVERSE_TTL:
            fetched = _fetch_eodhd_asx_universe()
            if fetched:
                _EODHD_ASX_UNIVERSE = fetched
                _EODHD_ASX_UNIVERSE_TS = now
        if _EODHD_ASX_UNIVERSE:
            return _EODHD_ASX_UNIVERSE
    return ASX_COMPANIES



def get_macro_indicators() -> dict:
    """Fetch key macro indicators from FRED (Fed funds rate, CPI, yield curve)."""
    if not _fred_client:
        return {}
    series = {
        "fed_funds_rate": "FEDFUNDS",
        "cpi_yoy": "CPIAUCSL",
        "yield_curve_10y2y": "T10Y2Y",
        "unemployment": "UNRATE",
    }
    result: dict = {}
    for label, sid in series.items():
        try:
            data = _fred_client.get_series(sid, observation_start="2024-01-01")
            if data is not None and len(data) > 0:
                last_val = float(data.dropna().iloc[-1])
                result[label] = round(last_val, 3)
        except Exception:
            pass
    return result


# Dynamic ML multipliers modified by the Self-Learning Loop
DYNAMIC_PENALTIES = {
    "vix_extreme": 0.75,
    "vix_high": 0.88,
    "pe_extreme": 0.70,
    "pe_high": 0.85,
    "short_extreme": 0.50,
    "short_high": 0.75
}

# Initialize FastAPI
app = FastAPI(title="ASX Stock Predictor API", version="1.0.0")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# LLM configuration
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://host.docker.internal:1234/v1")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "deepseek-r1:7b")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# Provider order: defaults to deepseek → nvidia → groq → local → openai
_default_order = os.getenv("LLM_PROVIDER_ORDER", "deepseek,nvidia,groq,local,openai")
LLM_PROVIDER_ORDER = [
    provider.strip().lower()
    for provider in _default_order.split(",")
    if provider.strip()
]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
groq_client = GroqClient(api_key=GROQ_API_KEY) if (GROQ_SDK_AVAILABLE and GROQ_API_KEY) else None

# ── Nvidia NIM client (OpenAI-compatible endpoint) ────────────────────────────
nvidia_client = None
if NVIDIA_API_KEY:
    try:
        nvidia_client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=NVIDIA_API_KEY,
        )
    except Exception:
        nvidia_client = None

# ── DeepSeek client (OpenAI-compatible, native base URL) ──────────────────────
deepseek_client = None
if DEEPSEEK_API_KEY:
    try:
        deepseek_client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=DEEPSEEK_API_KEY,
        )
    except Exception:
        deepseek_client = None

N8N_URL = os.getenv("N8N_URL", "http://broker-n8n:5678").rstrip("/")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BROKER_ID = os.getenv("TELEGRAM_BROKER_ID", "").strip()
TELEGRAM_ACTION_INGEST_SECRET = os.getenv("TELEGRAM_ACTION_INGEST_SECRET", "").strip()

# ── Groq Free-Tier Rate Limiter (30 req/min, threadsafe) ─────────────────────
_GROQ_RATE_LOCK = threading.Lock()
_GROQ_CALL_COUNT = 0
_GROQ_WINDOW_START = 0.0
_GROQ_MAX_PER_MINUTE = 25  # 30 limit, 5 headroom
_GROQ_WINDOW_SECS = 60.0

def _groq_rate_limit():
    """Block until a Groq free-tier request slot is available (25 req/min)."""
    global _GROQ_CALL_COUNT, _GROQ_WINDOW_START
    with _GROQ_RATE_LOCK:
        now = time.time()
        if now - _GROQ_WINDOW_START >= _GROQ_WINDOW_SECS:
            _GROQ_CALL_COUNT = 0
            _GROQ_WINDOW_START = now
        if _GROQ_CALL_COUNT >= _GROQ_MAX_PER_MINUTE:
            wait = _GROQ_WINDOW_SECS - (now - _GROQ_WINDOW_START) + 0.5
            if wait > 0:
                time.sleep(wait)
            _GROQ_CALL_COUNT = 0
            _GROQ_WINDOW_START = time.time()
        _GROQ_CALL_COUNT += 1


# ── Centralized LLM Chat Router (handles all providers, rate limits, dry-run) ──
def _llm_chat(
    messages: list[dict],
    max_tokens: int = 400,
    temperature: float = 0.2,
    timeout: int = 90,
    dry_run_allowed: bool = True,
) -> Optional[str]:
    """Route a chat request through the configured LLM fallback chain.

    Providers tried in LLM_PROVIDER_ORDER.  Returns the first successful response
    content, or None if all providers fail.  In UAT_MODE with dry_run_allowed=True,
    returns a placeholder without making any API calls.
    """
    if UAT_MODE and dry_run_allowed:
        return json.dumps({"dry_run": True, "note": "UAT mode — LLM call suppressed"})

    for provider in LLM_PROVIDER_ORDER:
        try:
            if provider == "deepseek" and deepseek_client:
                r = deepseek_client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                return r.choices[0].message.content

            elif provider == "nvidia" and nvidia_client:
                r = nvidia_client.chat.completions.create(
                    model=NVIDIA_MODEL,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return r.choices[0].message.content

            elif provider == "local":
                r = requests.post(
                    f"{LOCAL_LLM_URL}/chat/completions",
                    json={
                        "model": LOCAL_LLM_MODEL,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                    timeout=timeout,
                )
                if r.status_code == 200:
                    return strip_think_tags(r.json()["choices"][0]["message"]["content"])

            elif provider == "openai" and openai_client:
                r = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return r.choices[0].message.content

        except Exception:
            continue

    return None


def _llm_chat_safe(prompt: str, max_tokens: int = 300, temperature: float = 0.3) -> Optional[str]:
    """Non-blocking LLM call — returns None silently on any failure."""
    try:
        return _llm_chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=60,
            dry_run_allowed=True,
        )
    except Exception:
        return None


def strip_think_tags(content: str) -> str:
    """Strip DeepSeek-R1 <think>...</think> reasoning blocks and markdown fences."""
    if not content:
        return content
    # Remove complete <think>...</think> blocks
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    # Remove incomplete <think> blocks (model hit token limit mid-reasoning)
    content = re.sub(r"<think>.*$", "", content, flags=re.DOTALL)
    # Strip markdown code fences
    content = re.sub(r"```(?:json)?\s*", "", content)
    content = re.sub(r"```\s*", "", content)
    return content.strip()


def extract_json_from_llm(content: str) -> Optional[dict]:
    """Extract JSON object from LLM response after stripping think tags."""
    content = strip_think_tags(content)
    if not content:
        return None
    # Try balanced brackets first
    m = re.search(r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}', content)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    # Fallback: first { to last }
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


JWT_SECRET = os.getenv("JWT_SECRET", "change-this-secret-in-env")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))

# Database setup (defaults to SQLite for local fallback)
DEFAULT_SQLITE_PATH = Path(__file__).parent / "data" / "shares.db"
DEFAULT_SQLITE_PATH.parent.mkdir(exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}")

engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=10,
    pool_timeout=30,
)


@contextmanager
def db_conn():
    conn = engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db_conn() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    full_name TEXT,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    used INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_shares (
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, symbol),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS tracking_windows (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    start_date TIMESTAMP NOT NULL,
                    end_date TIMESTAMP NOT NULL,
                    entry_price REAL NOT NULL,
                    target_price_14d REAL NOT NULL,
                    target_return_14d REAL NOT NULL,
                    expected_direction TEXT NOT NULL,
                    tolerance_pct REAL NOT NULL DEFAULT 0.015,
                    status TEXT NOT NULL DEFAULT 'active',
                    source TEXT NOT NULL DEFAULT 'manual_add',
                    actual_price_14d REAL,
                    actual_return_14d REAL,
                    direction_correct INTEGER,
                    within_tolerance INTEGER,
                    bucket TEXT,
                    evaluated_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS portfolios (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS portfolio_holdings (
                    id TEXT PRIMARY KEY,
                    portfolio_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'AU',
                    quantity REAL NOT NULL,
                    avg_buy_price REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS daily_predictions (
                    symbol TEXT NOT NULL,
                    prediction_date TEXT NOT NULL,
                    name TEXT,
                    current_price REAL,
                    predicted_price REAL,
                    trend TEXT,
                    score REAL,
                    prob_ge_5pct REAL,
                    expected_return_pct REAL,
                    high_volatility_warning INTEGER DEFAULT 0,
                    warning_message TEXT,
                    warning_type TEXT,
                    confidence_low REAL,
                    confidence_high REAL,
                    quality_reason TEXT,
                    score_raw REAL,
                    learning_note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, prediction_date)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'AU',
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 1,
                    entry_price REAL NOT NULL,
                    current_price REAL,
                    target_price REAL,
                    status TEXT NOT NULL DEFAULT 'open',
                    signal_score REAL,
                    signal_trend TEXT,
                    signal_warning TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_trades_open ON paper_trades(user_id, symbol, side) WHERE status = 'open'
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS position_events (
                    id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_message TEXT,
                    payload_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (trade_id) REFERENCES paper_trades(id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_telegram_recipients (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    label TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    UNIQUE(user_id, chat_id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS telegram_send_log (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    chat_id TEXT,
                    message_type TEXT NOT NULL,
                    market TEXT,
                    digest_key TEXT,
                    status TEXT NOT NULL,
                    delivery_mode TEXT NOT NULL DEFAULT 'manual',
                    source TEXT,
                    error_message TEXT,
                    telegram_message_id TEXT,
                    payload_preview TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sent_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS advice_execution_actions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'AU',
                    action_type TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    execution_price REAL NOT NULL,
                    gross_amount REAL,
                    commission REAL NOT NULL DEFAULT 0,
                    net_amount REAL,
                    advice_cache_key TEXT,
                    source_message_type TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_telegram_recipients_user ON user_telegram_recipients(user_id, is_active)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_telegram_send_log_user_created ON telegram_send_log(user_id, created_at DESC)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_telegram_send_log_digest ON telegram_send_log(user_id, message_type, digest_key, status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_position_events_trade_created ON position_events(trade_id, created_at DESC)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_advice_actions_user_created ON advice_execution_actions(user_id, created_at DESC)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_advice_actions_user_symbol ON advice_execution_actions(user_id, symbol, created_at DESC)"))
        # Migrate: add preferred_market to existing users table
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_market TEXT DEFAULT 'AU'"))
        except Exception:
            pass  # Column may already exist
        # Migrate: add type column to portfolios (manual / ai)
        try:
            conn.execute(text("ALTER TABLE portfolios ADD COLUMN IF NOT EXISTS type TEXT DEFAULT 'manual'"))
        except Exception:
            pass  # Column may already exist
        # Migrate: add investment budget columns to users
        for budget_sql in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS total_investment_budget REAL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS max_position_pct REAL DEFAULT 10",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS budget_currency TEXT DEFAULT 'AUD'",
        ]:
            try:
                conn.execute(text(budget_sql))
            except Exception:
                pass
        for sql in [
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS peak_price REAL",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS stop_loss_price REAL",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS take_profit_price REAL",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trailing_stop_pct REAL DEFAULT 3.0",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS review_date TIMESTAMP",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS last_alert_at TIMESTAMP",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMP",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS position_stage TEXT DEFAULT 'entered'",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS recommendation_action TEXT",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS source_reason TEXT",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ]:
            try:
                conn.execute(text(sql))
            except Exception:
                pass
        # Create position_sentiment_log table for news-driven sell alerts
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS position_sentiment_log (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'AU',
                sentiment TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0,
                themes TEXT,
                headline TEXT,
                alert_sent INTEGER DEFAULT 0,
                profit_at_risk REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sentiment_log_user_symbol ON position_sentiment_log(user_id, symbol, created_at DESC)"))

        # ── SMSF v2 migrations ──────────────────────────────────────────────
        for mt_sql in [
            "ALTER TABLE model_training_set ADD COLUMN IF NOT EXISTS hit_5pct_before_m5pct BOOLEAN",
            "ALTER TABLE model_training_set ADD COLUMN IF NOT EXISTS hit_8pct_before_m8pct BOOLEAN",
            "ALTER TABLE model_training_set ADD COLUMN IF NOT EXISTS close_5pct_63d BOOLEAN",
        ]:
            try:
                conn.execute(text(mt_sql))
            except Exception:
                pass

        for pt_sql in [
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS catastrophe_stop_price REAL",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS time_stop_date DATE",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS buy_thesis TEXT",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS sector TEXT",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_type TEXT",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS exit_reason TEXT",
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_date_parsed DATE",
        ]:
            try:
                conn.execute(text(pt_sql))
            except Exception:
                pass

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS portfolio_peak_tracker (
                id SERIAL PRIMARY KEY,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                peak_value REAL NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_portfolio_peak_time ON portfolio_peak_tracker(recorded_at DESC)"))

        # ── Core Sleeve table ──────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS core_positions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'AU',
                qty REAL NOT NULL,
                entry_price REAL NOT NULL,
                sector TEXT,
                thesis TEXT,
                franking_pct REAL DEFAULT 0,
                yield_est REAL DEFAULT 0,
                next_review_date DATE,
                status TEXT NOT NULL DEFAULT 'active',
                exit_reason TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_core_positions_status ON core_positions(symbol, status)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_core_positions_open ON core_positions(symbol) WHERE status = 'active'"))

        # ── Kill Switch state ──────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kill_switch_state (
                id SERIAL PRIMARY KEY,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                halt_all BOOLEAN NOT NULL DEFAULT FALSE,
                active_conditions TEXT,
                breaker_level TEXT DEFAULT 'NORMAL',
                model_freeze BOOLEAN DEFAULT FALSE,
                data_quality_flag BOOLEAN DEFAULT FALSE,
                description TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_kill_switch_time ON kill_switch_state(recorded_at DESC)"))

        # ── Model Health metrics ───────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS model_health_metrics (
                id SERIAL PRIMARY KEY,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                hit_rate_pct REAL,
                observations INTEGER,
                evaluated INTEGER,
                wins INTEGER,
                freeze_active BOOLEAN DEFAULT FALSE,
                warning_active BOOLEAN DEFAULT FALSE,
                description TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_model_health_time ON model_health_metrics(recorded_at DESC)"))

        # ── Data Sanity log ────────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS data_sanity_log (
                id SERIAL PRIMARY KEY,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT,
                sane BOOLEAN NOT NULL DEFAULT TRUE,
                gap_pct REAL,
                eodhd_close REAL,
                asx_close REAL,
                reason TEXT,
                is_stale BOOLEAN DEFAULT FALSE
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_data_sanity_time ON data_sanity_log(checked_at DESC)"))

        # ── Sleeve column on paper_trades ──────────────────────────────────
        for sleeve_sql in [
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS sleeve TEXT DEFAULT 'satellite'",
        ]:
            try:
                conn.execute(text(sleeve_sql))
            except Exception:
                pass

init_db()

# Data models
class Share(BaseModel):
    symbol: str
    name: str
    current_price: float
    change_percent: float
    prediction_3m: Optional[dict] = None
    weekly_data: List[dict] = []

class SymbolQuery(BaseModel):
    symbol: str

class PredictionRequest(BaseModel):
    symbol: str
    force_refresh: bool = False


class AISuggestRequest(BaseModel):
    query: str
    max_symbols: int = 10
    provider: str = "auto"


class MarketAnalysisRequest(BaseModel):
    regime: dict
    metrics: dict


class SentimentRequest(BaseModel):
    symbol: str


class RankRequest(BaseModel):
    symbols: List[str]


class CreatePortfolioRequest(BaseModel):
    name: str
    type: str = "manual"  # manual | ai


class AddHoldingRequest(BaseModel):
    symbol: str
    market: str = "AU"
    quantity: float
    avg_buy_price: float


class SuggestPortfolioRequest(BaseModel):
    risk_profile: str = "balanced"   # conservative | balanced | aggressive
    markets: List[str] = ["AU"]       # ["AU"], ["US"], ["IN"], or combinations
    num_stocks: int = 8
    sectors: List[str] = []


class AiBuildPortfolioRequest(BaseModel):
    name: str                          # Portfolio name chosen by the user
    risk_profile: str = "balanced"
    markets: List[str] = ["AU"]
    num_stocks: int = 8
    sectors: List[str] = []
    total_investment: float = 10000.0  # Dollar amount to allocate across holdings


class InvestmentBudgetRequest(BaseModel):
    total_budget: float
    max_position_pct: float = 10.0
    currency: str = "AUD"


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str
    user: dict


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class PasswordResetResponse(BaseModel):
    message: str
    reset_token: str
    instructions: str


security = HTTPBearer()

# ASX company data cache — broad coverage of ASX 200 + popular ETFs/mid-caps
ASX_COMPANIES = {
    # --- Financials ---
    "CBA": "Commonwealth Bank of Australia",
    "ANZ": "ANZ Banking Group Limited",
    "WBC": "Westpac Banking Corporation",
    "NAB": "National Australia Bank Limited",
    "MQG": "Macquarie Group Limited",
    "SUN": "Suncorp Group Limited",
    "QBE": "QBE Insurance Group Limited",
    "IAG": "Insurance Australia Group",
    "AMP": "AMP Limited",
    "CGF": "Challenger Limited",
    "PPT": "Perpetual Limited",
    "MFG": "Magellan Financial Group",
    "HUB": "Hub24 Limited",
    "NWL": "Netwealth Group Limited",
    "PTM": "Platinum Asset Management",
    "GQG": "GQG Partners Inc.",
    "BOQ": "Bank of Queensland",
    "BEN": "Bendigo and Adelaide Bank",
    "CQR": "Charter Hall Retail REIT",
    "CPU": "Computershare Limited",
    "ASX": "ASX Limited",
    "IFL": "Insignia Financial",
    "MMS": "McMillan Shakespeare",
    # --- Materials / Mining ---
    "BHP": "BHP Group Limited",
    "RIO": "Rio Tinto Limited",
    "FMG": "Fortescue Limited",
    "S32": "South32 Limited",
    "MIN": "Mineral Resources Limited",
    "OZL": "OZ Minerals Limited",
    "NST": "Northern Star Resources",
    "EVN": "Evolution Mining Limited",
    "NCM": "Newcrest Mining Limited",
    "SFR": "Sandfire Resources",
    "IGO": "IGO Limited",
    "LYC": "Lynas Rare Earths",
    "PLS": "Pilbara Minerals",
    "CIA": "Champion Iron Limited",
    "AWC": "Alumina Limited",
    "BSL": "BlueScope Steel Limited",
    "GRR": "Grange Resources",
    "WHC": "Whitehaven Coal",
    "NHC": "New Hope Corporation",
    "WDS": "Woodside Energy Group",
    "STO": "Santos Limited",
    "BPT": "Beach Energy Limited",
    "KAR": "Karoon Energy",
    "ORG": "Origin Energy Limited",
    "WOR": "Worley Limited",
    "ALD": "Ampol Limited",
    "VEA": "Viva Energy Group",
    "IPL": "Incitec Pivot Limited",
    "ORA": "Orora Limited",
    "ANN": "Ansell Limited",
    "ALQ": "ALS Limited",
    "NUF": "Nufarm Limited",
    "WGX": "Westgold Resources",
    "SLR": "Silver Lake Resources",
    "RMS": "Ramelius Resources",
    "GOR": "Gold Road Resources",
    "CMM": "Capricorn Metals",
    "PRU": "Perseus Mining",
    "OGC": "OceanaGold Corporation",
    "TGS": "Tiger Resources",
    # --- Healthcare ---
    "CSL": "CSL Limited",
    "RHC": "Ramsay Health Care",
    "SHL": "Sonic Healthcare Limited",
    "COH": "Cochlear Limited",
    "RMD": "ResMed Inc.",
    "MPL": "Medibank Private",
    "NHF": "nib Holdings Limited",
    "HLS": "Healius Limited",
    "PME": "Pro Medicus Limited",
    "AHL": "Adrad Holdings",
    "IDX": "Integral Diagnostics",
    "CAJ": "Capitol Health",
    "TLX": "Telix Pharmaceuticals",
    "IMX": "Immutep Limited",
    "PNV": "PolyNovo Limited",
    # --- Consumer Discretionary ---
    "WES": "Wesfarmers Limited",
    "WOW": "Woolworths Group Limited",
    "COL": "Coles Group Limited",
    "HVN": "Harvey Norman Holdings",
    "JBH": "JB Hi-Fi Limited",
    "PMV": "Premier Investments Limited",
    "SUL": "Super Retail Group Ltd",
    "MYR": "Myer Holdings Limited",
    "KGN": "Kogan.com Limited",
    "BBN": "Baby Bunting Group",
    "ADH": "Adairs Limited",
    "BAP": "Bapcor Limited",
    "ARB": "ARB Corporation Limited",
    "PWR": "Peter Warren Automotive",
    "APE": "Eagers Automotive",
    "GUD": "GUD Holdings",
    "GWA": "GWA Group Limited",
    "REH": "Reece Limited",
    "ABC": "AdBri Limited",
    "SKC": "SkyCity Entertainment",
    "TAH": "Tabcorp Holdings",
    "ALL": "Aristocrat Leisure Limited",
    "SGR": "Star Entertainment Group",
    "CWN": "Crown Resorts",
    "FLG": "Fairfax Financial",
    "FLT": "Flight Centre Travel Group",
    "WEB": "Webjet Limited",
    "CTD": "Corporate Travel Management",
    "QAN": "Qantas Airways Limited",
    "REX": "Regional Express Holdings",
    "SIG": "Sigma Healthcare",
    "PAR": "Paradigm Biopharmaceuticals",
    # --- Consumer Staples ---
    "TWE": "Treasury Wine Estates",
    "CCL": "Coca-Cola Europacific Partners",
    "GNC": "GrainCorp Limited",
    "ELD": "Elders Limited",
    "ING": "Inghams Group Limited",
    "AAC": "Australian Agricultural",
    # --- Industrials ---
    "BXB": "Brambles Limited",
    "AZJ": "Aurizon Holdings Limited",
    "DOW": "Downer EDI Limited",
    "QUB": "Qube Holdings Limited",
    "TCL": "Transurban Group",
    "SYA": "Sayona Mining",
    "ALX": "Atlas Arteria",
    "AIA": "Auckland International Airport",
    "SVW": "Seven Group Holdings",
    "CAR": "CAR Group Limited",
    "SEK": "Seek Limited",
    "IEL": "IDP Education Limited",
    "TNE": "Technology One Limited",
    "REA": "REA Group Ltd",
    "DHG": "Domain Holdings Australia",
    "OFX": "OFX Group Limited",
    "SWM": "Seven West Media",
    "NEC": "Nine Entertainment Co.",
    "NWS": "News Corporation",
    "SXL": "Southern Cross Media",
    "CWY": "Cleanaway Waste Management",
    "DOW": "Downer EDI Limited",
    "UGL": "UGL Limited",
    "MND": "Monadelphous Group",
    "CIM": "CIMIC Group",
    "RWC": "Reliance Worldwide",
    "JHX": "James Hardie Industries",
    "DLX": "DuluxGroup Limited",
    "ABB": "Aussie Broadband",
    "TPG": "TPG Telecom",
    # --- Telecom ---
    "TLS": "Telstra Corporation Limited",
    "TPM": "TPG Telecom Limited",
    "VOC": "Vocus Group Limited",
    "SPK": "Spark New Zealand",
    # --- Technology ---
    "WTC": "WiseTech Global",
    "XRO": "Xero Limited",
    "NXT": "NextDC Limited",
    "MP1": "Megaport Limited",
    "APX": "Appen Limited",
    "ALU": "Altium Limited",
    "PPS": "Praemium Limited",
    "IRI": "Integrated Research",
    "DUB": "Dubber Corporation",
    "Z1P": "Zip Co Limited",
    "SPT": "Splitit Payments",
    "EML": "EML Payments",
    "BTH": "Bigtincan Holdings",
    "LNK": "Link Administration",
    "OPT": "Opthea Limited",
    # --- Real Estate / REITs ---
    "GMG": "Goodman Group",
    "SCG": "Scentre Group Limited",
    "VCX": "Vicinity Centres",
    "DXS": "Dexus",
    "SGP": "Stockland",
    "MGR": "Mirvac Group",
    "CLW": "Charter Hall Long WALE REIT",
    "CHC": "Charter Hall Group",
    "CIP": "Centuria Industrial REIT",
    "COF": "Centuria Office REIT",
    "ARF": "Arena REIT",
    "SCP": "Shopping Centres Australasia",
    "LLC": "Lottery Corporation Limited",
    "TLC": "The Lottery Corporation",
    # --- Utilities ---
    "AGL": "AGL Energy Limited",
    "APA": "APA Group",
    "SKI": "Spark Infrastructure",
    "AST": "AusNet Services",
    "MEZ": "Meridian Energy Limited",
    "GNE": "Genesis Energy Limited",
    "CEN": "Contact Energy",
    # --- Diversified / Conglomerates ---
    "SOL": "Soul Pattinson (W.H) Ltd",
    "WOW": "Woolworths Group Limited",
    # --- Defence & Aerospace ---
    "DRO": "DroneShield Limited",
    "EOS": "Electro Optic Systems Holdings",
    "ASB": "Austal Limited",
    # --- Other / Mid-cap ---
    "OML": "Ooh!Media Limited",
    "ORE": "Orocobre Limited",
    "OSH": "Oil Search Limited",
    "TGP": "360 Capital Group",
    "TMG": "Trigg Mining Ltd",
    "ILU": "Iluka Resources",
    "IMD": "Imdex Limited",
    "NBI": "NBI Industrial REIT",
    "OFX": "OFX Group",
    "PAC": "Pacific Current Group",
    "PPH": "Pushpay Holdings",
    "PRN": "Perenti Global",
    "SOI": "Sofi AI",
    "SSR": "SSR Mining",
    "STX": "Strike Energy",
    "TLX": "Telix Pharmaceuticals",
    "URW": "Unibail-Rodamco-Westfield",
    # --- ASX-listed ETFs (Gold, Index, Bond, Sector) ---
    "PMGOLD": "Perth Mint Physical Gold",
    "GOLD": "ETFS Physical Gold",
    "QAU": "BetaShares Gold Bullion (AUD Hedged)",
    "ETPMAG": "ETFS Physical Silver",
    "ETPMPD": "ETFS Physical Palladium",
    "ETPMPT": "ETFS Physical Platinum",
    "VAS": "Vanguard Australian Shares Index ETF",
    "VGS": "Vanguard MSCI Index International Shares ETF",
    "VTS": "Vanguard US Total Market Shares ETF",
    "VEU": "Vanguard All-World ex-US Shares ETF",
    "VGB": "Vanguard Australian Govt Bond Index ETF",
    "VAF": "Vanguard Australian Fixed Interest Index ETF",
    "VHY": "Vanguard Australian Shares High Yield ETF",
    "STW": "SPDR S&P/ASX 200 Fund",
    "IOZ": "iShares Core S&P/ASX 200 ETF",
    "IVV": "iShares S&P 500 ETF",
    "IJR": "iShares S&P Small-Cap ETF",
    "NDQ": "BetaShares NASDAQ 100 ETF",
    "PMGOLD": "Perth Mint Gold",
    "HACK": "Betashares Global Cybersecurity ETF",
    "ETHI": "Betashares Global Sustainability Leaders ETF",
    "FAIR": "Betashares Australian Sustainability Leaders ETF",
    "CURE": "Global X Healthcare ETF",
    "TECH": "Global X Morningstar Global Technology ETF",
    "HACK": "BetaShares Global Cybersecurity ETF",
    "HNDQ": "BetaShares NASDAQ 100 (AUD Hedged) ETF",
    "DHHF": "BetaShares Diversified High Growth ETF",
    "VDHG": "Vanguard Diversified High Growth Index ETF",
    "VDBA": "Vanguard Diversified Balanced Index ETF",
    "VDGR": "Vanguard Diversified Growth Index ETF",
    "A200": "BetaShares Australia 200 ETF",
    "EX20": "BetaShares Ex-20 Australian Equities ETF",
    "MVW": "VanEck Australian Equal Weight ETF",
    "MVA": "VanEck Australian Property ETF",
    "ETHI": "BetaShares Global Sustainability Leaders ETF",
    "FAIR": "BetaShares Australian Sustainability Leaders ETF",
    "ASIA": "BetaShares Asia Technology Tigers ETF",
    "FANG": "BetaShares FAANG+ ETF",
    "DRIV": "Global X Autonomous & Electric Vehicles ETF",
    "RBTZ": "Global X Robotics & AI ETF",
    "ROBO": "ETFS ROBO Global Robotics & Automation ETF",
    "BNKS": "BetaShares Global Banks ETF",
    "FUEL": "BetaShares Global Energy Companies ETF",
    "FOOD": "BetaShares Global Agriculture Companies ETF",
    "GGUS": "BetaShares Geared US Equity (Hedge Fund)",
    "BEAR": "BetaShares Australian Equities Bear Hedge Fund",
    "BBOZ": "BetaShares Australian Equities Strong Bear",
    "BBUS": "BetaShares US Equities Strong Bear (Hedged)",
    "AAA": "BetaShares Australian High Interest Cash ETF",
    "BILL": "iShares Core Cash ETF",
    "IAF": "iShares Core Composite Bond ETF",
    "ISEC": "iShares Enhanced Cash ETF",
    "AGVT": "iShares Australian Govt Bond ETF",
    "CRED": "BetaShares Australian Investment Grade Bond ETF",
    "QPON": "BetaShares Australian Bank Senior Floating Rate Bond ETF",
    "FLOT": "VanEck Floating Rate ETF",
    "IHVV": "iShares S&P 500 (AUD Hedged) ETF",
    "IHWL": "iShares Core MSCI World All Cap (AUD Hedged) ETF",
    "HGBL": "BetaShares Global Government Bond 20+ yr (AUD Hedged) ETF",
    "GBND": "BetaShares Sustainability Leades Diversified Bond ETF",
    "OOO": "BetaShares Crude Oil Index ETF",
}

# Try to load broader ASX_COMPANIES, fallback to initial array if evaluating module early
try:
    TOP_ASX200_SYMBOLS = list(ASX_COMPANIES.keys())
except NameError:
    TOP_ASX200_SYMBOLS = [
        "BHP", "CBA", "CSL", "WBC", "NAB", "ANZ", "WES", "WOW", "TLS", "RIO",
        "MQG", "FMG", "WDS", "GMG", "ALL", "COL", "REA", "TCL", "QBE", "STO",
        "XRO", "RMD", "ORG", "COH", "JBH", "APA", "MIN", "PME", "S32", "SEK",
    ]
TOP_ASX200_SYMBOLS_SHUFFLED = False
_TOP_ASX200_CURSOR = 0

US_COMPANIES = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "GOOGL": "Alphabet Inc.",
    "AMZN": "Amazon.com Inc.",
    "NVDA": "NVIDIA Corporation",
    "META": "Meta Platforms Inc.",
    "TSLA": "Tesla Inc.",
    "V": "Visa Inc.",
    "JPM": "JPMorgan Chase",
    "JNJ": "Johnson & Johnson",
    "WMT": "Walmart Inc.",
    "UNH": "UnitedHealth Group",
    "XOM": "Exxon Mobil",
    "PG": "Procter & Gamble",
    "MA": "Mastercard Inc.",
    "HD": "Home Depot Inc.",
    "CVX": "Chevron Corporation",
    "ABBV": "AbbVie Inc.",
    "BAC": "Bank of America",
    "BRK-B": "Berkshire Hathaway B",
}

INDIA_COMPANIES = {
    # Large caps / Nifty 50
    "RELIANCE.NS": "Reliance Industries",
    "TCS.NS": "Tata Consultancy Services",
    "INFY.NS": "Infosys Limited",
    "HDFCBANK.NS": "HDFC Bank",
    "ICICIBANK.NS": "ICICI Bank",
    "KOTAKBANK.NS": "Kotak Mahindra Bank",
    "SBIN.NS": "State Bank of India",
    "AXISBANK.NS": "Axis Bank",
    "BAJFINANCE.NS": "Bajaj Finance",
    "MARUTI.NS": "Maruti Suzuki India",
    "TITAN.NS": "Titan Company",
    "SUNPHARMA.NS": "Sun Pharmaceutical",
    "WIPRO.NS": "Wipro Limited",
    "LT.NS": "Larsen & Toubro",
    "HINDUNILVR.NS": "Hindustan Unilever",
    "ASIANPAINT.NS": "Asian Paints",
    "NTPC.NS": "NTPC Limited",
    "POWERGRID.NS": "Power Grid Corp",
    "ITC.NS": "ITC Limited",
    "ULTRACEMCO.NS": "UltraTech Cement",
    "HCLTECH.NS": "HCL Technologies",
    "TECHM.NS": "Tech Mahindra",
    "ONGC.NS": "Oil & Natural Gas Corp",
    "BPCL.NS": "Bharat Petroleum",
    "IOC.NS": "Indian Oil Corporation",
    "COALINDIA.NS": "Coal India",
    "TATASTEEL.NS": "Tata Steel",
    "JSWSTEEL.NS": "JSW Steel",
    "HINDALCO.NS": "Hindalco Industries",
    "TATAMOTORS.NS": "Tata Motors",
    "BAJAJFINSV.NS": "Bajaj Finserv",
    "BAJAJ-AUTO.NS": "Bajaj Auto",
    "HEROMOTOCO.NS": "Hero MotoCorp",
    "EICHERMOT.NS": "Eicher Motors",
    "DRREDDY.NS": "Dr. Reddy's Laboratories",
    "CIPLA.NS": "Cipla Limited",
    "DIVISLAB.NS": "Divi's Laboratories",
    "APOLLOHOSP.NS": "Apollo Hospitals",
    "ADANIENT.NS": "Adani Enterprises",
    "ADANIPORTS.NS": "Adani Ports & SEZ",
    "ADANIGREEN.NS": "Adani Green Energy",
    "ADANIPOWER.NS": "Adani Power",
    "TATAPOWER.NS": "Tata Power",
    "NESTLEIND.NS": "Nestle India",
    "BRITANNIA.NS": "Britannia Industries",
    "DABUR.NS": "Dabur India",
    "MARICO.NS": "Marico Limited",
    "GODREJCP.NS": "Godrej Consumer Products",
    "COLPAL.NS": "Colgate-Palmolive India",
    "PIDILITIND.NS": "Pidilite Industries",
    "BERGEPAINT.NS": "Berger Paints",
    "HAVELLS.NS": "Havells India",
    "VOLTAS.NS": "Voltas Limited",
    "WHIRLPOOL.NS": "Whirlpool of India",
    "INDIGO.NS": "IndiGo (InterGlobe Aviation)",
    "IRCTC.NS": "Indian Railway Catering & Tourism",
    "DMART.NS": "Avenue Supermarts (DMart)",
    "NYKAA.NS": "FSN E-Commerce (Nykaa)",
    "ZOMATO.NS": "Zomato Limited",
    "PAYTM.NS": "One97 Communications (Paytm)",
    "POLICYBZR.NS": "PB Fintech (PolicyBazaar)",
    "NAUKRI.NS": "Info Edge (Naukri)",
    "JUSTDIAL.NS": "Just Dial",
    "INDIANB.NS": "Indian Bank",
    "BANKBARODA.NS": "Bank of Baroda",
    "PNB.NS": "Punjab National Bank",
    "CANBK.NS": "Canara Bank",
    "FEDERALBNK.NS": "Federal Bank",
    "INDUSINDBK.NS": "IndusInd Bank",
    "BANDHANBNK.NS": "Bandhan Bank",
    "IDFCFIRSTB.NS": "IDFC First Bank",
    "MUTHOOTFIN.NS": "Muthoot Finance",
    "CHOLAFIN.NS": "Cholamandalam Investment",
    "LICHSGFIN.NS": "LIC Housing Finance",
    "RECLTD.NS": "REC Limited",
    "PFC.NS": "Power Finance Corporation",
    "IRFC.NS": "Indian Railway Finance Corp",
    # Telecom & Media
    "BHARTIARTL.NS": "Bharti Airtel",
    "IDEA.NS": "Vodafone Idea",
    "TATACOMM.NS": "Tata Communications",
    "MTNL.NS": "MTNL",
    # IT mid-cap
    "MPHASIS.NS": "Mphasis Limited",
    "LTIM.NS": "LTIMindtree",
    "PERSISTENT.NS": "Persistent Systems",
    "COFORGE.NS": "Coforge Limited",
    "KPITTECH.NS": "KPIT Technologies",
    "TANLA.NS": "Tanla Platforms",
    "MASTEK.NS": "Mastek Limited",
    # Pharma & Healthcare
    "AUROPHARMA.NS": "Aurobindo Pharma",
    "BIOCON.NS": "Biocon Limited",
    "LUPIN.NS": "Lupin Limited",
    "TORNTPHARM.NS": "Torrent Pharmaceuticals",
    "ALKEM.NS": "Alkem Laboratories",
    "IPCALAB.NS": "IPCA Laboratories",
    # Industrials & Infra
    "SIEMENS.NS": "Siemens India",
    "ABB.NS": "ABB India",
    "BOSCHLTD.NS": "Bosch Limited",
    "CUMMINSIND.NS": "Cummins India",
    "BHEL.NS": "Bharat Heavy Electricals",
    "HAL.NS": "Hindustan Aeronautics",
    "BEL.NS": "Bharat Electronics",
    "GRINFRA.NS": "G R Infraprojects",
    "KEC.NS": "KEC International",
    "KALPATPOWR.NS": "Kalpataru Power",
    # Cement
    "SHREECEM.NS": "Shree Cement",
    "AMBUJACEM.NS": "Ambuja Cements",
    "ACC.NS": "ACC Limited",
    "RAMCOCEM.NS": "Ramco Cements",
    # Chemicals
    "SRF.NS": "SRF Limited",
    "DEEPAKNTR.NS": "Deepak Nitrite",
    "AAVAS.NS": "Aavas Financiers",
    # Small/Mid that user mentioned
    "PRAJIND.NS": "Praj Industries",
    "GLENMARK.NS": "Glenmark Pharmaceuticals",
    "ESCORTS.NS": "Escorts Kubota",
    "SONACOMS.NS": "Sona BLW Precision",
    "CAMPUS.NS": "Campus Activewear",
    "RVNL.NS": "Rail Vikas Nigam",
    "IRCON.NS": "IRCON International",
    "SUZLON.NS": "Suzlon Energy",
    "YESBANK.NS": "Yes Bank",
    "RPOWER.NS": "Reliance Power",
    "GMRINFRA.NS": "GMR Airports Infra",
    "JINDALSTEL.NS": "Jindal Steel & Power",
    "SAIL.NS": "Steel Authority of India",
    "NATIONALUM.NS": "National Aluminium",
    "NMDC.NS": "NMDC Limited",
    "VEDL.NS": "Vedanta Limited",
    "MOTHERSON.NS": "Samvardhana Motherson",
    "MINDA.NS": "Minda Corporation",
    "BALKRISIND.NS": "Balkrishna Industries",
    "APOLLOTYRE.NS": "Apollo Tyres",
    "MRF.NS": "MRF Limited",
    "CEATLTD.NS": "CEAT Limited",
    "PAGEIND.NS": "Page Industries",
    "TRENT.NS": "Trent Limited",
    "VARUNBEV.NS": "Varun Beverages",
    "UBL.NS": "United Breweries",
    "RADICO.NS": "Radico Khaitan",
    "RELAXO.NS": "Relaxo Footwears",
    "BATAINDIA.NS": "Bata India",
    "VGUARD.NS": "V-Guard Industries",
    "DIXON.NS": "Dixon Technologies",
    "AMBER.NS": "Amber Enterprises",
    "BLUEDART.NS": "Blue Dart Express",
    "DELHIVERY.NS": "Delhivery Limited",
    "VRL.NS": "VRL Logistics",
    "GESHIP.NS": "Great Eastern Shipping",
    "CONCOR.NS": "Container Corp of India",
    "MAHINDRA.NS": "Mahindra Logistics",
    "M&M.NS": "Mahindra & Mahindra",
    "TVSMOTOR.NS": "TVS Motor Company",
}

ALL_COMPANIES = {
    **ASX_COMPANIES,
    **US_COMPANIES,
    **{k.replace(".NS", ""): v for k, v in INDIA_COMPANIES.items()},
}

_IN_SYMBOLS = {k.replace(".NS", "") for k in INDIA_COMPANIES}

def detect_market(symbol: str) -> str:
    """Detect the market (AU/US/IN) for a bare symbol.
    Priority: explicit US list → explicit AU list → IN company list → try ASX live → default AU.
    """
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    if s in US_COMPANIES:
        return "US"
    if s in ASX_COMPANIES:
        return "AU"
    if s in _IN_SYMBOLS:
        return "IN"
    # Unknown symbol: probe yfinance to see if it trades on ASX before falling back.
    try:
        with _YFINANCE_LOCK:
            probe = yf.Ticker(f"{s}.AX").fast_info
        if getattr(probe, "last_price", None) or getattr(probe, "regular_market_price", None):
            return "AU"
    except Exception:
        pass
    # Final default: treat as ASX since this is an ASX-focused app.
    return "AU"


def format_ticker(symbol: str, market: str = "AU") -> str:
    """Return the correct yfinance ticker for a symbol in a given market."""
    market = (market or "AU").upper()
    symbol = symbol.lstrip("$")  # strip $ prefix (e.g. $^AXJO → ^AXJO, $AQI → AQI)
    if symbol.startswith("^"):
        return symbol  # index tickers already fully qualified
    if market == "AU":
        return f"{symbol}.AX" if not symbol.endswith(".AX") else symbol
    elif market == "IN":
        return f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
    return symbol  # US — no suffix


def get_market_companies(market: str) -> dict:
    """Return the company dict for the given market code (AU/US/IN)."""
    m = (market or "AU").upper()
    if m == "US":
        return US_COMPANIES
    if m == "IN":
        return INDIA_COMPANIES
    return ASX_COMPANIES


def get_stock_data(symbol: str, market: str = None) -> dict:
    """Fetch the latest price and change for a symbol across any market.
    Includes staleness check: rejects data older than 5 trading days."""
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    if market is None:
        market = detect_market(s)
        
    ticker_str = format_ticker(s, market)
    name = ASX_COMPANIES.get(s) or ALL_COMPANIES.get(s, s)

    # 1. Try EODHD Real-Time API for LIVE intraday price (15-min delayed)
    if EODHD_API_KEY:
        try:
            _eodhd_rate_limit()
            ticker_eodhd = format_ticker(s, market).replace(".AX", ".AU")
            url = f"https://eodhd.com/api/real-time/{ticker_eodhd}"
            r = requests.get(url, params={"api_token": EODHD_API_KEY, "fmt": "json"}, timeout=5)
            if r.status_code == 200:
                data = r.json()
                cp = data.get("close")
                if cp and cp != "NA" and float(cp) > 0:
                    prev = data.get("previousClose")
                    if prev == "NA" or not prev:
                        prev = cp
                    change = float(data.get("change_p", 0)) if data.get("change_p") != "NA" else 0.0
                    vol = data.get("volume")
                    return {
                        "current_price": float(cp),
                        "change_percent": change,
                        "name": name,
                        "volume": int(vol) if vol and vol != "NA" else None
                    }
        except Exception as e:
            print(f"[EODHD RealTime] Error fetching live price for {s}: {e}")
        
    # 2. Fallback to End-Of-Day 5d charts from EODHD if live price fails
    hist = _eodhd_historical_data(s, "5d", market)
            
    try:
        # Staleness check: reject if last data point is older than 5 calendar days
        if not hist.empty and len(hist) >= 1:
            last_date = hist.index[-1]
            if hasattr(last_date, 'date'):
                days_stale = (datetime.utcnow().date() - last_date.date()).days
            else:
                days_stale = 0
            if days_stale > 5:
                return {"current_price": 0.0, "change_percent": 0.0, "name": ASX_COMPANIES.get(s) or ALL_COMPANIES.get(s, s)}
        if len(hist) >= 2:
            current_price = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2])
        elif len(hist) == 1:
            current_price = float(hist["Close"].iloc[-1])
            prev_close = current_price
        else:
            current_price, prev_close = 0.0, 0.0
        change = ((current_price - prev_close) / prev_close * 100) if prev_close else 0.0
    except Exception:
        current_price, change = 0.0, 0.0
    # Resolve company name: check ASX dict first, then ALL_COMPANIES, then use symbol
    name = ASX_COMPANIES.get(s) or ALL_COMPANIES.get(s, s)
    return {
        "current_price": current_price,
        "change_percent": change,
        "name": name,
        "volume": int(hist["Volume"].iloc[-1]) if len(hist) >= 1 and "Volume" in hist.columns else None,
        "avg_volume_5d": round(float(hist["Volume"].tail(5).mean()), 0) if len(hist) >= 1 and "Volume" in hist.columns else None,
        "volume_spike": round(float(hist["Volume"].iloc[-1]) / float(hist["Volume"].tail(5).mean()), 2) if len(hist) >= 1 and "Volume" in hist.columns and float(hist["Volume"].tail(5).mean()) > 0 else None,
    }


def _eodhd_fundamentals(symbol: str, market: str) -> dict:
    """Fetch fundamentals from EODHD as primary source."""
    if not EODHD_API_KEY:
        return {}
    try:
        _eodhd_rate_limit()
        ticker = format_ticker(symbol, market).replace(".AX", ".AU")
        url = f"https://eodhd.com/api/fundamentals/{ticker}"
        r = requests.get(url, params={"api_token": EODHD_API_KEY, "fmt": "json"}, timeout=10)
        if r.status_code != 200:
            return {}
        d = r.json()
        highlights = d.get("Highlights", {})
        val = d.get("Valuation", {})
        tech = d.get("Technicals", {})
        shares = d.get("SharesStats", {})
        
        # Calculate days to earnings
        next_earnings_date = None
        days_to_earnings = None
        earnings = d.get("Earnings", {}).get("History", {})
        # EODHD history might contain future dates or past dates. If there's an upcoming earnings date:
        # Actually EODHD often provides it in Highlights.
        # Let's rely on standard parsing if we have it, or fallback.
        
        market_cap = highlights.get("MarketCapitalization")
        if market_cap: market_cap = float(market_cap)

        return {
            "pe": highlights.get("PERatio"),
            "forward_pe": val.get("ForwardPE"),
            "trailing_eps": highlights.get("EpsTtm"),
            "forward_eps": val.get("ForwardEps"), # Custom extraction if available
            "analyst_target_mean": highlights.get("WallStreetTargetPrice"),
            "market_cap": market_cap,
            "dividend_yield": highlights.get("DividendYield") * 100 if highlights.get("DividendYield") else None,
            "eps_growth_fwd_pct": None, # Complex to compute from basic Highlights
            "short_pct_float": shares.get("ShortPercentOfFloat") * 100 if shares.get("ShortPercentOfFloat") else None,
            "52w_high": tech.get("52WeekHigh"),
            "52w_low": tech.get("52WeekLow"),
            "sector": d.get("General", {}).get("Sector"),
            "industry": d.get("General", {}).get("Industry"),
            "revenue_growth": highlights.get("RevenueGrowthYOY"),
            "num_analyst_opinions": None, # Fallback
            "analyst_recommendation": None # Fallback
        }
    except Exception as e:
        print(f"[EODHD] Fundamentals error for {symbol}: {e}")
        return {}

def get_valuation_metrics(symbol: str) -> dict:
    """Fetch valuation metrics — DB snapshot first, then EODHD, yfinance last."""
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    market = detect_market(s)

    # 1. Try cached monthly fundamental snapshot (fast, no API call)
    try:
        from fundamental_feeder import get_fundamentals_for_symbol
        cached = get_fundamentals_for_symbol(s)
        if cached and cached.get("trailing_pe") is not None:
            current_p = 0
            try:
                sd = get_stock_data(s, market)
                current_p = sd.get("current_price", 0) or 0
            except Exception:
                pass
            upside = None
            if cached.get("analyst_target_mean") and current_p > 0:
                upside = round((float(cached["analyst_target_mean"]) / current_p - 1) * 100, 2)
            return {
                "pe": cached.get("trailing_pe"),
                "forward_pe": cached.get("forward_pe"),
                "market_cap": cached.get("market_cap"),
                "dividend_yield": cached.get("dividend_yield"),
                "analyst_target_mean": cached.get("analyst_target_mean"),
                "analyst_upside_pct": upside,
                "analyst_recommendation": cached.get("analyst_rec", ""),
                "revenue_growth": cached.get("revenue_growth"),
                "earnings_growth": cached.get("earnings_growth"),
                "52w_high": cached.get("high_52w"),
                "52w_low": cached.get("low_52w"),
                "pct_from_52w_high": round((current_p / float(cached["high_52w"]) - 1) * 100, 2) if cached.get("high_52w") and current_p > 0 else None,
                "avg_volume": cached.get("avg_volume"),
                "beta": cached.get("beta"),
                "pb": cached.get("price_to_book"),
                "_source": "db_snapshot",
            }
    except Exception:
        pass

    # 2. Try EODHD fundamentals
    eodhd_data = _eodhd_fundamentals(s, market)
    
    # 3. Fallback to yfinance ONLY if EODHD data is missing or empty
    stock = None
    info = {}
    if not eodhd_data or not eodhd_data.get("market_cap"):
        with _YFINANCE_LOCK:
            stock = yf.Ticker(format_ticker(s, market))
        try:
            with _YFINANCE_LOCK:
                info = stock.info
        except:
            pass
    
    # Handle dividend yield (comes as decimal like 0.042, convert to %)
    div_yield = info.get('dividendYield')
    if div_yield is not None:
        div_yield = float(div_yield) * 100 if div_yield < 1 else float(div_yield)
    
    market_cap = info.get('marketCap')
    if market_cap is not None:
        market_cap = float(market_cap)

    target_mean = eodhd_data.get('analyst_target_mean') or info.get('targetMeanPrice')
    target_high = info.get('targetHighPrice')
    target_low  = info.get('targetLowPrice')
    current_p   = info.get('currentPrice') or info.get('regularMarketPreviousClose') or 0
    upside_to_target = None
    if target_mean and current_p and current_p > 0:
        upside_to_target = round(((float(target_mean) - float(current_p)) / float(current_p)) * 100, 2)

    high_52w = eodhd_data.get('52w_high') or info.get('fiftyTwoWeekHigh')
    low_52w  = eodhd_data.get('52w_low') or info.get('fiftyTwoWeekLow')
    pct_from_52w_high = None
    if high_52w and current_p and current_p > 0:
        pct_from_52w_high = round(((float(current_p) - float(high_52w)) / float(high_52w)) * 100, 2)

    short_pct = eodhd_data.get('short_pct_float') or info.get('shortPercentOfFloat')
    if short_pct is not None and short_pct < 1:
        short_pct = round(float(short_pct) * 100, 2)

    trailing_eps = eodhd_data.get('trailing_eps') or info.get('trailingEps')
    forward_eps  = eodhd_data.get('forward_eps') or info.get('forwardEps')
    eps_growth_fwd = eodhd_data.get('eps_growth_fwd_pct')
    if eps_growth_fwd is None and trailing_eps and forward_eps and float(trailing_eps) != 0:
        eps_growth_fwd = round(((float(forward_eps) - float(trailing_eps)) / abs(float(trailing_eps))) * 100, 2)

    next_earnings_date = None
    days_to_earnings = None
    try:
        if stock is not None:
            cal = stock.calendar
            if cal is not None:
                if hasattr(cal, 'T'):
                    if 'Earnings Date' in cal.index:
                        raw_ed = cal.loc['Earnings Date']
                        raw_ed = raw_ed.dropna() if hasattr(raw_ed, 'dropna') else raw_ed
                        if hasattr(raw_ed, 'iloc') and len(raw_ed) > 0:
                            raw_ed = raw_ed.iloc[0]
                        if hasattr(raw_ed, 'date'):
                            next_earnings_date = str(raw_ed.date())
                elif isinstance(cal, dict):
                    raw_ed = cal.get('Earnings Date')
                    if raw_ed:
                        if isinstance(raw_ed, list) and len(raw_ed) > 0:
                            raw_ed = raw_ed[0]
                        if hasattr(raw_ed, 'date'):
                            next_earnings_date = str(raw_ed.date())
                        elif isinstance(raw_ed, str):
                            next_earnings_date = raw_ed
        if next_earnings_date:
            from datetime import date as _date
            ed = _date.fromisoformat(next_earnings_date[:10])
            days_to_earnings = (ed - datetime.utcnow().date()).days
    except Exception:
        pass

    return {
        "pe": eodhd_data.get('pe') or info.get('trailingPE'),
        "forward_pe": eodhd_data.get('forward_pe') or info.get('forwardPE'),
        "peg": info.get('pegRatio'),
        "pb": info.get('priceToBook'),
        "dividend_yield": eodhd_data.get('dividend_yield') or div_yield,
        "market_cap": eodhd_data.get('market_cap') or market_cap,
        "sector": eodhd_data.get('sector') or info.get('sector'),
        "industry": eodhd_data.get('industry') or info.get('industry'),
        "analyst_target_mean": round(float(target_mean), 2) if target_mean else None,
        "analyst_target_high": round(float(target_high), 2) if target_high else None,
        "analyst_target_low":  round(float(target_low), 2)  if target_low  else None,
        "analyst_upside_pct":  upside_to_target,
        "analyst_recommendation": eodhd_data.get('analyst_recommendation') or info.get('recommendationKey'),
        "num_analyst_opinions": eodhd_data.get('num_analyst_opinions') or info.get('numberOfAnalystOpinions'),
        "next_earnings_date": next_earnings_date,
        "days_to_earnings": days_to_earnings,
        "short_pct_float": short_pct,
        "52w_high": round(float(high_52w), 3) if high_52w else None,
        "52w_low":  round(float(low_52w), 3)  if low_52w  else None,
        "pct_from_52w_high": pct_from_52w_high,
    }


def get_risk_metrics(symbol: str) -> dict:
    """Calculate risk metrics: Beta, Max Drawdown, Sharpe Ratio"""
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    market = detect_market(s)
    stock = yf.Ticker(format_ticker(s, market))

    # Get 1 year of data for calculations
    hist = stock.history(period="1y")

    if len(hist) < 50:
        return {"beta": None, "max_drawdown_90d": None, "sharpe_90d": None}

    # Use the appropriate benchmark index for beta
    _benchmark_ticker = {"US": "^GSPC", "IN": "^NSEI"}.get(market, "^AXJO")
    try:
        asx200 = yf.Ticker(_benchmark_ticker).history(period="1y")
    except Exception:
        asx200 = None
    
    # Calculate daily returns
    returns = hist['Close'].pct_change().dropna()
    
    # Beta calculation (vs ASX200)
    beta = None
    if asx200 is not None and len(asx200) > 50:
        asx_returns = asx200['Close'].pct_change().dropna()
        # Align dates
        common_dates = returns.index.intersection(asx_returns.index)
        if len(common_dates) > 30:
            stock_ret = returns.loc[common_dates]
            market_ret = asx_returns.loc[common_dates]
            covariance = np.cov(stock_ret, market_ret)[0][1]
            market_variance = np.var(market_ret)
            if market_variance > 0:
                beta = covariance / market_variance
    
    # Max Drawdown (90 days)
    hist_90d = hist['Close'].tail(90)
    rolling_max = hist_90d.expanding().max()
    drawdowns = (hist_90d - rolling_max) / rolling_max
    max_drawdown_90d = float(drawdowns.min() * 100) if len(drawdowns) > 0 else None
    
    # Sharpe Ratio (90 days, assuming risk-free rate of 4% annual)
    risk_free_rate = 0.04 / 252  # Daily risk-free rate
    returns_90d = hist['Close'].pct_change().tail(90).dropna()
    if len(returns_90d) > 10:
        excess_returns = returns_90d - risk_free_rate
        if excess_returns.std() > 0:
            sharpe_90d = (excess_returns.mean() / excess_returns.std()) * np.sqrt(252)
        else:
            sharpe_90d = None
    else:
        sharpe_90d = None
    
    return {
        "beta": round(beta, 2) if beta is not None else None,
        "max_drawdown_90d": round(max_drawdown_90d, 2) if max_drawdown_90d is not None else None,
        "sharpe_90d": round(sharpe_90d, 2) if sharpe_90d is not None else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CATALYST & ENTRY TIMING
# ─────────────────────────────────────────────────────────────────────────────

def _entry_timing_assessment(valuation: dict, indicators: dict, prediction: dict) -> dict:
    """Return a structured entry timing signal combining catalyst risk and technical confirmation.

    Returns:
        {
          "entry_ok": bool,
          "entry_zone": "clear|caution|avoid",
          "reason": str,
          "days_to_earnings": int|None,
          "earnings_risk": "high|moderate|low|none",
          "technical_confirmed": bool,
          "upside_to_target_pct": float|None,
        }
    """
    days_to_e = valuation.get('days_to_earnings')
    upside = valuation.get('analyst_upside_pct')
    rsi = indicators.get('rsi') or 50
    current_price = prediction.get('current_price', 0)
    sma50 = indicators.get('sma_50', 0) or 0

    # ── Earnings proximity guard ──────────────────────────────────────────────
    if days_to_e is not None:
        if days_to_e < 0:
            earnings_risk = "none"           # past earnings — post-earnings drift window
            entry_zone_e = "clear"
        elif days_to_e <= 7:
            earnings_risk = "high"           # too close — binary event risk
            entry_zone_e = "avoid"
        elif days_to_e <= 14:
            earnings_risk = "moderate"
            entry_zone_e = "caution"
        elif days_to_e <= 30:
            earnings_risk = "low"
            entry_zone_e = "caution"
        else:
            earnings_risk = "none"
            entry_zone_e = "clear"
    else:
        earnings_risk = "unknown"
        entry_zone_e = "caution"

    # ── Event Risk Classifier (Volume Spike Guardrail) ────────────────────────
    vol_div = indicators.get('volume_divergence', 1.0) or 1.0
    event_risk_spike = (vol_div > 3.0)

    # ── Technical confirmation ────────────────────────────────────────────────
    # Strategy 1: Standard SMA/Momentum baseline
    momentum_20 = indicators.get('momentum_20', 0) or 0
    macd_hist = indicators.get('macd_hist', 0) or 0
    
    # Strategy 2: EMA Ribbon (9 > 20 > 50) Pullback / Crossover
    ema_9 = indicators.get('ema_9', 0) or 0
    ema_20 = indicators.get('ema_20', 0) or 0
    ema_50 = indicators.get('ema_50', 0) or 0
    ema_ribbon_bullish = (ema_9 > ema_20 > ema_50 > 0)
    
    # Strategy 3: Donchian Breakout
    donchian_high = indicators.get('donchian_high_20', 0) or 0
    is_donchian_breakout = (current_price >= donchian_high) and (donchian_high > 0)
    
    # We require either strong EMA momentum, a breakout, or solid MACD/SMA confirmation
    stoch_k = indicators.get('stoch_rsi_k', 50)
    stoch_d = indicators.get('stoch_rsi_d', 50)
    stoch_bullish = stoch_k > stoch_d
    
    technical_confirmed = (
        (current_price > sma50 > 0)
        and (rsi < 72 or indicators.get('kde_rsi_prob', 1.0) < 0.90)
        and (momentum_20 > -5)
        and (
            ema_ribbon_bullish 
            or is_donchian_breakout 
            or (macd_hist > -0.05 * abs(sma50) * 0.001)
            or (stoch_bullish and rsi > 50) # Fallback confirmation for exact timing
        )
    )

    # ── Analyst upside threshold ──────────────────────────────────────────────
    market_cap = valuation.get('market_cap')
    is_small_cap = (market_cap is not None and market_cap < 2000000000) or market_cap is None
    
    if is_small_cap and upside is None:
        analyst_ok = False
        analyst_reason = "Lack of analyst coverage for small cap is an elevated risk factor."
    else:
        analyst_ok = (upside is None) or (upside >= 3.0)
        analyst_reason = f"Analyst consensus target offers only {upside:.1f}% upside — risk/reward marginal." if upside is not None else "No analyst target."

    # ── Combine ───────────────────────────────────────────────────────────────
    if entry_zone_e == "avoid":
        entry_zone = "avoid"
        entry_ok = False
        reason = f"Earnings in {days_to_e}d — binary event risk. Wait for post-result clarity."
    elif entry_zone_e == "caution" and not technical_confirmed:
        entry_zone = "avoid"
        entry_ok = False
        reason = "Earnings approaching + technicals not confirmed. No clear entry."
    elif entry_zone_e == "caution":
        entry_zone = "caution"
        entry_ok = True
        reason = f"Earnings in {days_to_e}d — proceed with tighter stop. Technicals confirmed."
    elif not technical_confirmed:
        entry_zone = "caution"
        entry_ok = False
        reason = "Technicals not yet confirmed (price below SMA50, RSI overbought, or negative momentum)."
    elif not analyst_ok:
        entry_zone = "caution"
        entry_ok = False
        reason = analyst_reason
    elif event_risk_spike:
        entry_zone = "avoid"
        entry_ok = False
        reason = f"Extreme volume divergence detected ({vol_div:.1f}x average). Unconfirmed event risk. Avoid until news is verified."
    else:
        entry_zone = "clear"
        entry_ok = True
        
        # Give specific reason based on strategy triggered
        if is_donchian_breakout:
            reason = "Donchian 20-day volatility breakout confirmed. No imminent earnings risk."
        elif ema_ribbon_bullish:
            reason = "EMA Ribbon (9/20/50) pullback/crossover confirmed. Strong momentum. No earnings risk."
        else:
            reason = "Technicals confirmed (Price > SMA50, MACD healthy), no imminent earnings risk, analyst upside sufficient."

    return {
        "entry_ok": entry_ok,
        "entry_zone": entry_zone,
        "reason": reason,
        "days_to_earnings": days_to_e,
        "earnings_risk": earnings_risk,
        "technical_confirmed": technical_confirmed,
        "upside_to_target_pct": upside,
        "gap_risk_warning": earnings_risk == "high",
        "gap_risk_note": "⚠️ ASX halts + earnings gaps can breach stops. Tighten stop to 2% if holding through earnings." if earnings_risk in ("high", "moderate") else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO-AWARE POSITION SIZING — Fixed-Fractional + Equal Allocation + ASX Rules
# ═══════════════════════════════════════════════════════════════════════════════

PORTFOLIO_STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "200000"))
PORTFOLIO_RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.5"))
PORTFOLIO_MAX_SECTOR_PCT = float(os.getenv("MAX_SECTOR_PCT", "25"))
PORTFOLIO_MAX_ADV_PCT = float(os.getenv("MAX_ADV_PCT", "5"))
PORTFOLIO_ASX_MIN_PARCEL = float(os.getenv("ASX_MIN_PARCEL", "500"))


def get_starting_capital(uid: str = None) -> float:
    try:
        with db_conn() as conn:
            if uid:
                row = conn.execute(text("SELECT total_investment_budget FROM users WHERE id = :uid"), {"uid": uid}).fetchone()
            else:
                row = conn.execute(text("SELECT total_investment_budget FROM users ORDER BY total_investment_budget DESC LIMIT 1")).fetchone()
            
            if row and row[0] > 0:
                return float(row[0])
    except Exception:
        pass
    return PORTFOLIO_STARTING_CAPITAL

def _compute_portfolio_state(uid: str = None) -> dict:
    start_cap = get_starting_capital(uid)
    try:
        with db_conn() as conn:
            uid_filter = " AND user_id = :uid" if uid else ""
            params = {"uid": uid} if uid else {}
            closed_pnl = conn.execute(text(
                f"SELECT COALESCE(SUM((COALESCE(current_price,0)-COALESCE(entry_price,0))*COALESCE(quantity,1)),0) FROM paper_trades WHERE status='closed'{uid_filter}"
            ), params).fetchone()
            open_rows = conn.execute(text(
                f"SELECT symbol, COALESCE(entry_price,0)*COALESCE(quantity,0) AS cost "
                f"FROM paper_trades WHERE status='open'{uid_filter}"
            ), params).fetchall()
        total_pnl = float(closed_pnl[0] or 0)
        invested = sum(float(r[1] or 0) for r in open_rows)
        equity = start_cap + total_pnl
        available = equity - invested
        return {
            "starting_capital": start_cap,
            "total_equity": round(equity, 2),
            "invested": round(invested, 2),
            "available_cash": round(available, 2),
            "realized_pnl": round(total_pnl, 2),
        }
    except Exception:
        return {"total_equity": start_cap, "available_cash": start_cap,
                "invested": 0, "realized_pnl": 0}


def _get_sector_exposure(uid: str = None) -> dict:
    try:
        with db_conn() as conn:
            uid_filter = " AND user_id = :uid" if uid else ""
            params = {"uid": uid} if uid else {}
            open_syms = conn.execute(text(
                f"SELECT symbol FROM paper_trades WHERE status='open'{uid_filter}"
            ), params).fetchall()
    except Exception:
        return {}
    exposures = {}
    for (sym,) in open_syms:
        try:
            val = get_valuation_metrics(sym)
            sector = str(val.get("sector", "Unknown"))
            exposures[sector] = exposures.get(sector, 0) + 1
        except Exception:
            exposures["Unknown"] = exposures.get("Unknown", 0) + 1
    return exposures


def _calculate_position_size(price: float, stop_loss_pct: float, account_balance: float,
                              num_picks: int = 5, avg_volume: int = 0,
                              current_sector_exposure: dict = None,
                              candidate_sector: str = "Unknown") -> dict:
    risk_pct = PORTFOLIO_RISK_PER_TRADE_PCT / 100.0
    max_risk_amount = account_balance * risk_pct
    risk_per_share = abs(price * stop_loss_pct / 100.0)
    if risk_per_share <= 0:
        risk_per_share = 0.01

    qty_by_risk = int(max_risk_amount / risk_per_share)
    cost_by_risk = qty_by_risk * price

    max_per_stock = account_balance / max(num_picks, 1)
    qty_by_cap = int(max_per_stock / price) if price > 0 else 0

    qty = min(qty_by_risk, qty_by_cap)
    cost = qty * price

    warnings = []
    if current_sector_exposure and candidate_sector:
        sector_count = current_sector_exposure.get(candidate_sector, 0)
        sector_max_picks = int(num_picks * PORTFOLIO_MAX_SECTOR_PCT / 100)
        if sector_count >= sector_max_picks:
            warnings.append(f"Sector cap: {candidate_sector} at {sector_count}/{sector_max_picks} positions")
    if cost < PORTFOLIO_ASX_MIN_PARCEL:
        warnings.append(f"Below ASX ${PORTFOLIO_ASX_MIN_PARCEL:.0f} minimum parcel")
    if avg_volume > 0 and qty > avg_volume * PORTFOLIO_MAX_ADV_PCT / 100:
        warnings.append(f"Exceeds {PORTFOLIO_MAX_ADV_PCT:.0f}% ADV")

    return {
        "qty": max(qty, 0),
        "cost": round(cost, 2),
        "risk_per_share": round(risk_per_share, 4),
        "risk_amount": round(qty * risk_per_share, 2),
        "pct_of_account": round(cost / account_balance * 100, 1) if account_balance > 0 else 0,
        "stop_price": round(price * (1 + stop_loss_pct / 100), 2) if stop_loss_pct < 0 else round(price * (1 - stop_loss_pct / 100), 2),
        "warnings": warnings,
    }


def _enrich_candidates_with_tiers(candidates: list):
    """Compute 51-feature ensemble model score and assign target tier to each candidate.

    Called before DB write so the picks JSON stored in wealth_scan_cache
    includes tier labels for the 7AM AI pipeline to read.
    Uses Ridge coefficients with StandardScaler from training.
    """
    try:
        from model_training import get_latest_weights, FEATURE_COLS as ML_FEATURES
        _weights = get_latest_weights()
        # Load scaler stats
        from sqlalchemy import text
        from main import db_conn as _db_conn
        _scaler_stats = None
        try:
            with _db_conn() as conn:
                r = conn.execute(text(
                    "SELECT notes FROM model_weights_by_date WHERE feature_name='__scaler_stats__' "
                    "ORDER BY trained_at DESC LIMIT 1"
                )).fetchone()
                if r and r[0]:
                    _scaler_stats = json.loads(r[0])
        except Exception:
            pass
    except Exception:
        _weights = {}
        _scaler_stats = None

    for c in candidates:
        confluence = c.get("confluence") or {}
        channels = confluence.get("channels", {}) if isinstance(confluence, dict) else {}
        pred = c.get("prediction") or {}

        # Extract technical features from confluence channel signals
        ch_momentum = channels.get("momentum", {}) if isinstance(channels, dict) else {}
        ch_institutional = channels.get("institutional", {}) if isinstance(channels, dict) else {}
        ch_options = channels.get("options", {}) if isinstance(channels, dict) else {}
        ch_fundamental = channels.get("fundamental", {}) if isinstance(channels, dict) else {}
        ch_cross = channels.get("cross_asset", {}) if isinstance(channels, dict) else {}
        mom_signals = ch_momentum.get("signals", []) if isinstance(ch_momentum, dict) else []

        feat = {
            "sma_cross_20_50": 1.0 if "sma_cross_20_50" in str(mom_signals) else 0.0,
            "sma_cross_50_200": 1.0 if "sma_cross_50_200" in str(mom_signals) else 0.0,
            "rsi": 45.0 if "rsi_oversold" in str(mom_signals) else 55.0 if "rsi_overbought" in str(mom_signals) else 50.0,
            "rsi_slope": 0.0,
            "kde_rsi_prob": 0.6 if "rsi_oversold" in str(mom_signals) else 0.5,
            "macd_hist": 0.5 if "macd_bullish" in str(mom_signals) else -0.5 if "macd_bearish" in str(mom_signals) else 0.0,
            "momentum_20d": float(c.get("rel_strength_3m", 0) or 0) * 0.3,
            "momentum_63d": float(c.get("rel_strength_3m", 0) or 0),
            "donchian_breakout": 1.0 if "donchian_breakout" in str(mom_signals) else 0.0,
            "volume_spike": 1.5 if "block_volume" in str(mom_signals) else 1.0,
            "volume_ratio": 0.5 if "block_volume" in str(mom_signals) else 0.0,
            "obv_bullish": 1.0 if "obv_bullish" in str(mom_signals) else 0.0,
            "cmf": 0.15 if "cmf_bullish" in str(mom_signals) else 0.0,
            "cmf_bullish": 1.0 if "cmf_bullish" in str(mom_signals) else 0.0,
            "atr_pct": 0.02,
            "bb_width": 0.08,
            "bb_position": 0.5,
            "hv_20d": 0.25,
            "adx": 30.0 if "adx_trending" in str(mom_signals) else 18.0,
            "adx_trend": 1.0 if "adx_trending" in str(mom_signals) else 0.0,
            "ema_ribbon": 1.0 if "ema_bullish" in str(mom_signals) else 0.0,
            "ttm_squeeze_on": 1.0 if "ttm_squeeze" in str(mom_signals) else 0.0,
            "ttm_squeeze_fired": 0.0,
        }
        bullish_count = sum(1 for s in (ch_momentum, ch_institutional, ch_options,
                                         ch_fundamental, ch_cross)
                            if isinstance(s, dict) and s.get("bullish"))
        feat["signal_cluster"] = bullish_count + (1 if feat["cmf_bullish"] else 0) + (1 if feat["obv_bullish"] else 0)
        feat["trend_strength"] = abs(feat["momentum_20d"]) * feat["adx"] / 100
        feat["rsi_vol_adj"] = feat["rsi"] / (feat["hv_20d"] + 1)
        feat["mom_per_vol"] = feat["momentum_20d"] / (feat["hv_20d"] + 1e-9)
        feat["dist_from_sma50"] = float(c.get("pct_from_52w_high", 0) or 0) * 0.5
        feat["rsi_macd_div"] = 0.0
        feat["vol_confirm"] = feat["volume_spike"] * (1 if feat["momentum_20d"] > 0 else -1)
        feat["fee_squeeze_ratio"] = 5.0  # Keep legacy typo key for compat
        feat["bb_squeeze_ratio"] = 5.0   # Correct key used by FEATURE_COLS

        # New regime and volatility features (Phase 3: reasonable defaults from confluence)
        feat["regime_sma_alignment"] = 0.67 if feat["sma_cross_20_50"] and feat["sma_cross_50_200"] else 0.33
        feat["vwap_position"] = 1.0
        feat["gap_detection"] = 0.0
        feat["vol_regime_ratio"] = 1.0
        feat["garman_klass_vol"] = feat["hv_20d"]
        feat["parkinson_vol"] = feat["hv_20d"]
        feat["autocorr_5d"] = 0.0
        feat["skewness_20d"] = 0.0
        feat["kurtosis_20d"] = 0.0
        feat["max_drawdown_20d"] = feat["dist_from_sma50"] * 0.5
        # Macro features (approximate from market context)
        feat["xjo_momentum_63d"] = float(c.get("sector_perf_1mo", 0) or 0)
        feat["xjo_sma_position"] = 1.0 if str(c.get("trend", "")) in ("BULLISH", "UP") else 0.0
        feat["xjo_vol_20d"] = feat["hv_20d"]
        feat["relative_strength_vs_xjo"] = float(c.get("rel_strength_3m", 0) or 0)
        # Pure macro features (live values from cached macro data)
        macro = _get_macro_data_cached()
        feat["vix_level"] = float(macro.get("vix", {}).get("current", 20) or 20)
        feat["copper_gold_ratio"] = _safe_ratio(
            float(macro.get("copper", {}).get("current", 0) or 0),
            float(macro.get("gold", {}).get("current", 0) or 1),
            0.004,
        )
        feat["yield_curve_slope"] = float(macro.get("au_10y_yield", {}).get("current", 0) or 0)
        feat["aud_usd_trend"] = float(macro.get("aud_usd", {}).get("current", 0.65) or 0.65)

        pe = float(c.get("pe", 0) or 0)
        fpe = float(c.get("forward_pe", 0) or 0)
        mc = float(c.get("market_cap", 0) or 0)
        feat["fund_pe_inv"] = round(100.0 / pe, 2) if pe > 0 else 0.0
        feat["fund_forward_pe_inv"] = round(100.0 / fpe, 2) if fpe > 0 else 0.0
        feat["fund_market_cap_log"] = np.log(mc) if mc > 0 else 0.0
        feat["fund_div_yield"] = float(c.get("dividend_yield", 0) or 0)
        feat["fund_analyst_upside"] = float(c.get("analyst_upside_pct", 0) or 0)
        rec_map = {"strong_buy":5,"buy":4,"hold":3,"underperform":2,"sell":1}
        feat["fund_analyst_rec_score"] = float(rec_map.get(str(c.get("analyst_recommendation","")).lower(), 3))
        feat["fund_earnings_growth"] = float(c.get("eps_growth_fwd_pct", 0) or 0)
        feat["fund_revenue_growth"] = float(c.get("revenue_growth", 0) or 0)
        feat["fund_beta"] = float(c.get("beta", 1.0) or 1.0)
        feat["fund_pct_from_52w_high"] = float(c.get("pct_from_52w_high", 0) or 0)

        model_score_raw = 0.0
        _scaler_ok = False
        if _weights and _scaler_stats:
            # ── Scaler sanity check: if volume_spike mean/scale are extreme outliers,
            #     the scaler was trained on corrupt data.  Fall back to raw path so
            #     scores remain discriminative instead of collapsing to a narrow band.
            try:
                feature_order = _scaler_stats.get("feature_order", ML_FEATURES)
                vol_idx = feature_order.index("volume_spike") if "volume_spike" in feature_order else -1
                vol_mean = _scaler_stats.get("mean", [])[vol_idx] if vol_idx >= 0 else 0
                if vol_mean > 1000:  # reasonable volume_spike ≈ 0.5–5
                    print(f"[EnrichTiers] Scaler corrupted (volume_spike mean={vol_mean:.0f}) — falling back to raw path.")
                    _scaler_ok = False
                else:
                    _scaler_ok = True
            except Exception:
                _scaler_ok = True  # couldn't check, assume ok
        if _weights and _scaler_stats and _scaler_ok:
            # Apply StandardScaler: (x - mean) / scale
            feat_values = []
            feature_order = _scaler_stats.get("feature_order", ML_FEATURES)
            for col in feature_order:
                v = feat.get(col, 0.0)
                feat_values.append(float(v) if v is not None else 0.0)
            feat_arr = np.array(feat_values)
            mean_arr = np.array(_scaler_stats.get("mean", [0]*len(feature_order)))
            scale_arr = np.array(_scaler_stats.get("scale", [1]*len(feature_order)))
            feat_arr = np.where(scale_arr > 1e-9, (feat_arr - mean_arr) / scale_arr, 0.0)
            model_score_raw = sum(float(_weights.get(col, 0)) * feat_arr[i] for i, col in enumerate(feature_order))
        elif _weights:
            model_score_raw = sum(float(_weights.get(k, 0)) * float(v) for k, v in feat.items())
        c["_model_score"] = round(model_score_raw, 2)
        c["_model_confidence"] = min(99, max(1, round(100.0 / (1.0 + math.exp(-model_score_raw / 50.0)))))

        c["_model_score"] = round(model_score_raw, 4)
        c["_model_confidence"] = min(99, max(1, round(max(0, model_score_raw) * 100)))

    # ── After all candidates scored: use percentile-based tier thresholds ──
    # Hardcoded thresholds (0.22 / 0.155) are meaningless because model_score
    # is an uncalibrated dot product — its scale depends on feature scaling and
    # Ridge coefficient magnitudes, which vary between training runs.
    # Instead we rank candidates within each scan batch: top ~10% get 10pct
    # tier, next ~15% get 8pct tier, remainder get watch.
    scores = [(c.get("_model_score", 0) or 0, c) for c in candidates if c.get("_model_score", 0) is not None]
    scores.sort(key=lambda x: x[0], reverse=True)
    n = len(scores)
    cutoff_10pct_idx = max(1, int(n * 0.10))
    cutoff_8pct_idx = max(cutoff_10pct_idx + 1, int(n * 0.25))

    # Use WFO gate to widen/narrow tiers
    try:
        wfo_state = get_current_wfo_state()
        gate = wfo_state.get("capital_gate", {}).get("state", "INSUFFICIENT_DATA")
        if gate == "RED":
            cutoff_10pct_idx = max(1, int(n * 0.05))
            cutoff_8pct_idx = max(cutoff_10pct_idx + 1, int(n * 0.12))
        elif gate == "AMBER":
            cutoff_10pct_idx = max(1, int(n * 0.08))
            cutoff_8pct_idx = max(cutoff_10pct_idx + 1, int(n * 0.20))
    except Exception:
        pass

    cutoff_10pct = scores[cutoff_10pct_idx - 1][0] if n > 0 else 999
    cutoff_8pct = scores[min(cutoff_8pct_idx - 1, n - 1)][0] if n > 0 else 999

    for i, (score_val, c) in enumerate(scores):
        if i < cutoff_10pct_idx:
            c["_target_tier"] = "10pct"
            c["_tier_label"] = "10% TARGET TIER"
        elif i < cutoff_8pct_idx:
            c["_target_tier"] = "8pct"
            c["_tier_label"] = "8% COMPOUND TIER"
        else:
            c["_target_tier"] = "watch"
            c["_tier_label"] = "WATCH"

    print(f"[EnrichTiers] {n} candidates scored | 10% cutoff={cutoff_10pct:.4f} ({cutoff_10pct_idx} stocks) | 8% cutoff={cutoff_8pct:.4f} ({cutoff_8pct_idx} stocks)")


def _build_tier_signal_message(symbol: str, name: str, signal: dict, valuation: dict,
                               prediction: dict, entry_timing: dict,
                               tier_label: str = "📈 TIER") -> str:
    """Build tiered Telegram HTML message with model-predicted target tier."""
    score = signal.get('score', 0) or 0
    trend = (prediction.get('trend') or 'neutral').upper()
    upside = valuation.get('analyst_upside_pct')
    rec = (valuation.get('analyst_recommendation') or '').upper()
    n_analysts = valuation.get('num_analyst_opinions') or '?'
    model_score = signal.get('_model_score', 0)
    model_conf = signal.get('_model_confidence', 50)

    zone_emoji = {"clear": "🟢", "caution": "🟡", "avoid": "🔴"}.get(
        (entry_timing or {}).get('entry_zone', 'caution'), "⚪")
    trend_emoji = "📈" if trend == "BULLISH" else ("📉" if trend == "BEARISH" else "➡️")

    pred_chg = prediction.get('change_from_current', 0) or 0
    current_p = prediction.get('current_price')
    cp_str = f"${current_p:.2f}" if current_p is not None else 'N/A'
    predicted_peak = signal.get('predicted_change_pct', pred_chg)
    upside_str = f"{upside:+.1f}%" if upside is not None else 'N/A'

    pe_str = f"{valuation.get('pe'):.1f}x" if valuation.get('pe') else 'N/A'
    high52_str = f"${valuation.get('52w_high'):.2f}" if valuation.get('52w_high') else 'N/A'
    from_peak = valuation.get('pct_from_52w_high')
    from_peak_str = f"{from_peak:+.1f}%" if from_peak is not None else 'N/A'

    is_10pct = "10%" in tier_label
    is_8pct = "8%" in tier_label
    target_pct = "+10%" if is_10pct else "+8%"
    tier_desc = "Top 10% of all ASX stocks" if is_10pct else "Top 25% of all ASX stocks"

    return (
        f"<b>{zone_emoji} {tier_label}</b>\n"
        f"{symbol} — {name}\n\n"
        f"{'🚀' if is_10pct else '📈'} <b>Target: {target_pct} (2:1 R:R)</b>\n"
        f"💡 <b>Current Price (Max Entry):</b> {cp_str}\n"
        f"🎯 <b>Target: {target_pct} (2:1 R:R) | ML Prediction: {tier_desc}\n\n"
        f"{trend_emoji} <b>90-Day Forecast:</b> {trend}\n"
        f"📊 <b>Score:</b> {score:.2f} | <b>Entry:</b> {zone_emoji}\n\n"
        f"<b>💼 Analyst Consensus ({n_analysts} analysts)</b>\n"
        f"  Rec: <b>{rec or 'N/A'}</b> | Upside: <b>{upside_str}</b>\n"
        f"  P/E: {pe_str} | From 52w High: {from_peak_str}\n"
        f"  Target: ${valuation.get('analyst_target_mean') or 'N/A'}\n\n"
        f"<b>📅 Earnings:</b> {valuation.get('next_earnings_date', 'N/A')}\n"
        f"<b>🚨 AI Note:</b> Verify NO upcoming earnings, NO unadjusted splits, "
        f"and beware 'Fat Tail' micro-cap risk before entry.\n\n"
        f"<b>⏰ AI Review at 7:00 AM — do NOT buy yet.</b> "
        f"6-persona AI will approve or reject. "
        f"Buy button sent at 7 AM for AI-approved stocks.\n"
        f"📏 Position size: {'~30% of capital' if is_10pct else '~70% of capital'}"
    )


def _build_wealth_signal_message(symbol: str, name: str, signal: dict, valuation: dict,
                                  prediction: dict, entry_timing: dict) -> str:
    """Build a rich, emoji-annotated Telegram HTML message for a wealth-builder signal."""
    score = signal.get('score', 0) or 0
    trend = (prediction.get('trend') or 'neutral').upper()
    upside = valuation.get('analyst_upside_pct')
    rec = (valuation.get('analyst_recommendation') or '').upper()
    n_analysts = valuation.get('num_analyst_opinions') or '?'
    days_e = entry_timing.get('days_to_earnings')
    dte_str = f"{days_e}d" if days_e is not None else 'N/A'

    zone_emoji = {"clear": "🟢", "caution": "🟡", "avoid": "🔴"}.get(entry_timing['entry_zone'], "⚪")
    trend_emoji = "📈" if trend == "BULLISH" else ("📉" if trend == "BEARISH" else "➡️")

    upside_str = f"{upside:+.1f}%" if upside is not None else 'N/A'
    pe_str = f"{valuation.get('pe'):.1f}x" if valuation.get('pe') else 'N/A'
    fpe_str = f"{valuation.get('forward_pe'):.1f}x" if valuation.get('forward_pe') else 'N/A'
    high52_str = f"${valuation.get('52w_high'):.2f}" if valuation.get('52w_high') else 'N/A'
    from_peak = valuation.get('pct_from_52w_high')
    from_peak_str = f"{from_peak:+.1f}%" if from_peak is not None else 'N/A'
    short_str = f"{valuation.get('short_pct_float'):.1f}%" if valuation.get('short_pct_float') else 'N/A'
    pred_chg = prediction.get('change_from_current', 0) or 0
    current_p = prediction.get('current_price')
    cp_str = f"${current_p:.2f}" if current_p is not None else 'N/A'
    pred_p = prediction.get('predicted_price')
    pred_p_str = f"${pred_p:.2f}" if pred_p is not None else 'N/A'

    return (
        f"<b>{zone_emoji} WEALTH SIGNAL — {symbol}</b>\n"
        f"{name}\n\n"
        f"🚨 <b>SYSTEM GUARDRAIL:</b> Verify NO upcoming earnings, NO unadjusted stock splits, and beware 'Fat Tail' micro-cap risk before entry. AI evaluates price action only.\n\n"
        f"🎯 <b>Current Price (Max Entry):</b> {cp_str}\n"
        f"🎯 <b>AI Target Price (Exit):</b> {pred_p_str} (+{pred_chg:.1f}%)\n\n"
        f"{trend_emoji} <b>90-Day Forecast:</b> {trend}\n"
        f"📊 <b>Score:</b> {score:.2f} | <b>P(≥3%):</b> {signal.get('prob_ge_5pct', 0):.1f}%\n\n"
        f"<b>💼 Analyst Consensus ({n_analysts} analysts)</b>\n"
        f"  Recommendation: <b>{rec or 'N/A'}</b>\n"
        f"  Target (consensus): ${valuation.get('analyst_target_mean') or 'N/A'}\n"
        f"  Implied upside: <b>{upside_str}</b>\n\n"
        f"<b>📅 Catalyst Risk</b>\n"
        f"  Next Earnings: {valuation.get('next_earnings_date', 'N/A')} ({dte_str} away)\n"
        f"  Earnings Risk: {entry_timing['earnings_risk'].upper()}\n"
        f"  Entry Zone: {zone_emoji} <b>{entry_timing['entry_zone'].upper()}</b>\n"
        f"  Reason: {entry_timing['reason']}\n\n"
        f"<b>📐 Valuation</b>\n"
        f"  P/E: {pe_str} | Fwd P/E: {fpe_str}\n"
        f"  52-Week High: {high52_str} | From Peak: {from_peak_str}\n"
        f"  Short Interest: {short_str}\n"
    )


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return base64.b64encode(salt + digest).decode("utf-8")


def verify_password(password: str, encoded: str) -> bool:
    try:
        raw = base64.b64decode(encoded.encode("utf-8"))
        salt = raw[:16]
        original_digest = raw[16:]
        candidate_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
        return hmac.compare_digest(original_digest, candidate_digest)
    except Exception:
        return False


def create_access_token(user_id: str, email: str) -> str:
    expiry = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expiry,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        email = payload.get("email")
        if not user_id or not email:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    with engine.connect() as conn:
        user = conn.execute(
            text("SELECT id, email, full_name, preferred_market, total_investment_budget, max_position_pct, budget_currency FROM users WHERE id = :id"),
            {"id": user_id},
        ).fetchone()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return {
        "id": user[0],
        "email": user[1],
        "full_name": user[2] or "",
        "preferred_market": user[3] or "AU",
        "total_investment_budget": float(user[4] or 0),
        "max_position_pct": float(user[5] or 10),
        "budget_currency": user[6] or "AUD",
    }

def _eodhd_historical_data(symbol: str, period: str, market: str) -> pd.DataFrame:
    """Fetch historical EOD data from EODHD. Returns empty DataFrame on failure.
    
    If EODHD returns 200 with an empty list, the ticker likely has no data at all
    (delisted / dead). In that case, don't bother falling back to yfinance —
    return a single-row NaN DataFrame so callers can detect it as skip-worthy."""
    if not EODHD_API_KEY:
        return pd.DataFrame()
        
    exchange_suffix = "AU" if market == "AU" else "US" if market == "US" else "NSE" if market == "IN" else "AU"
    ticker = f"{symbol}.{exchange_suffix}"
    
    days = 365
    if period == "1y": days = 365
    elif period == "3mo": days = 90
    elif period == "1mo": days = 30
    elif period == "6mo": days = 180
    elif period == "5y": days = 365 * 5
    elif period == "1d": days = 3
    elif period == "5d": days = 7
    
    from_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"https://eodhd.com/api/eod/{ticker}"
    
    try:
        _eodhd_rate_limit()
        r = requests.get(url, params={"api_token": EODHD_API_KEY, "fmt": "json", "from": from_date}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data is None:
                return pd.DataFrame({"Close": [0.0]})  # definitive dead — don't retry
            if isinstance(data, list):
                if not data:
                    return pd.DataFrame({"Close": [0.0]})  # definitive dead — don't retry
                df = pd.DataFrame(data)
                if not df.empty:
                    df["Date"] = pd.to_datetime(df["date"])
                    df.set_index("Date", inplace=True)
                    df.rename(columns={
                        "open": "Open",
                        "high": "High",
                        "low": "Low",
                        "close": "Unadjusted Close",
                        "volume": "Volume",
                        "adjusted_close": "Close"
                    }, inplace=True)
                    return df
        elif r.status_code == 404:
            # EODHD confirmed ticker doesn't exist — don't fall back to yfinance
            return pd.DataFrame({"Close": [0.0]})
    except Exception:
        pass
        
    return pd.DataFrame()


def get_historical_data(symbol: str, period: str = "1y", market: str = None) -> pd.DataFrame:
    """Get historical price data from EODHD only. No yfinance fallback."""
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    if market is None:
        market = detect_market(s)
        
    df = _eodhd_historical_data(s, period, market)
    if df.empty:
        return df
    if "Close" in df.columns and len(df) == 1 and df["Close"].iloc[0] == 0.0:
        return pd.DataFrame()
    return df

def calculate_technical_indicators(df: pd.DataFrame) -> dict:
    """Calculate technical indicators"""
    indicators = {}
    
    if len(df) < 50:
        return indicators
    
    # Moving averages (SMA)
    indicators['sma_20'] = float(df['Close'].rolling(20).mean().iloc[-1])
    indicators['sma_50'] = float(df['Close'].rolling(50).mean().iloc[-1])
    indicators['sma_200'] = float(df['Close'].rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
    
    # Exponential Moving Averages (EMA Ribbon)
    indicators['ema_9'] = float(df['Close'].ewm(span=9, adjust=False).mean().iloc[-1])
    indicators['ema_20'] = float(df['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
    indicators['ema_50'] = float(df['Close'].ewm(span=50, adjust=False).mean().iloc[-1])
    
    # Donchian Channels (20-day High/Low for Breakouts)
    indicators['donchian_high_20'] = float(df['High'].rolling(20).max().iloc[-1]) if 'High' in df.columns else float(df['Close'].rolling(20).max().iloc[-1])
    indicators['donchian_low_20'] = float(df['Low'].rolling(20).min().iloc[-1]) if 'Low' in df.columns else float(df['Close'].rolling(20).min().iloc[-1])
    
    # Volatility
    indicators['volatility'] = float(df['Close'].pct_change().std() * np.sqrt(252))
    
    # RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi_series = 100 - (100 / (1 + rs))
    indicators['rsi'] = float(rsi_series.iloc[-1])
    
    # ── KDE RSI (Historical Probability Density) ─────────────
    # Adds a statistical probability of reversal based on historical context,
    # preventing false "overbought" signals during strong trends.
    try:
        from scipy.stats import gaussian_kde
        valid_rsi = rsi_series.dropna()
        if len(valid_rsi) > 30:
            kde = gaussian_kde(valid_rsi)
            current_rsi = float(valid_rsi.iloc[-1])
            # Probability that historically RSI has been at or above the current RSI
            prob_higher = kde.integrate_box_1d(current_rsi, 100)
            # The closer to 1.0, the more extreme (overbought) it is relative to its own history
            indicators['kde_rsi_prob'] = float(1.0 - prob_higher)
        else:
            indicators['kde_rsi_prob'] = 0.5
    except Exception:
        indicators['kde_rsi_prob'] = 0.5
    
    # ── Stochastic RSI ─────────────────────────────────────
    # Normalizes RSI between 0-100, easier for AI to detect overbought/oversold
    # Uses 14-period lookback of RSI values
    try:
        stoch_rsi_lookback = 14
        rsi_min = rsi_series.rolling(stoch_rsi_lookback).min()
        rsi_max = rsi_series.rolling(stoch_rsi_lookback).max()
        stoch_rsi = (rsi_series - rsi_min) / (rsi_max - rsi_min + 1e-9) * 100
        indicators['stoch_rsi'] = round(float(stoch_rsi.iloc[-1]), 2)
    except Exception:
        indicators['stoch_rsi'] = 50.0
    
    # MACD
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9, adjust=False).mean()
    indicators['macd'] = float(macd.iloc[-1])
    indicators['macd_signal'] = float(signal.iloc[-1])
    
    # Bollinger Bands
    sma = df['Close'].rolling(20).mean()
    std = df['Close'].rolling(20).std()
    bb_upper = float((sma + (std * 2)).iloc[-1])
    bb_lower = float((sma - (std * 2)).iloc[-1])
    bb_mid   = float(sma.iloc[-1])
    indicators['bb_upper'] = bb_upper
    indicators['bb_lower'] = bb_lower
    indicators['bb_mid']   = bb_mid
    
    # ── BB-adjusted RSI (removes statistical boundary extremes) ──────────────
    # When price is at/above the upper Bollinger Band the RSI signal is already
    # statistically "overbought" regardless of its raw value, so we clip it up.
    # When price is at/below the lower band we clip it down (oversold context).
    # This mirrors the HACOLT philosophy: contextual signal filtering.
    raw_rsi = indicators.get('rsi', 50.0)
    last_close = float(df['Close'].iloc[-1])
    bb_range = bb_upper - bb_lower if (bb_upper - bb_lower) > 0 else 1.0
    # BB %B position: 1.0 = at upper band, 0.0 = at lower band
    bb_pct_b = (last_close - bb_lower) / bb_range
    if bb_pct_b >= 1.0:
        # Price at or above upper band — treat RSI as at least 75 (overbought)
        indicators['rsi'] = max(raw_rsi, 75.0)
    elif bb_pct_b <= 0.0:
        # Price at or below lower band — treat RSI as at most 25 (oversold)
        indicators['rsi'] = min(raw_rsi, 25.0)
    elif bb_pct_b >= 0.85:
        # Approaching upper band — nudge RSI toward overbought
        blend = (bb_pct_b - 0.85) / 0.15   # 0→1 as price goes 85%→100% of BB
        indicators['rsi'] = raw_rsi + blend * (75.0 - raw_rsi) * 0.5
    elif bb_pct_b <= 0.15:
        # Approaching lower band — nudge RSI toward oversold
        blend = (0.15 - bb_pct_b) / 0.15   # 0→1 as price goes 15%→0% of BB
        indicators['rsi'] = raw_rsi - blend * (raw_rsi - 25.0) * 0.5
    indicators['bb_pct_b'] = round(bb_pct_b, 4)
    
    # Price momentum
    indicators['momentum_20'] = float((df['Close'].iloc[-1] - df['Close'].iloc[-20]) / df['Close'].iloc[-20] * 100)
    
    # HACOLT (Heikin Ashi Candles Oscillator Long Term)
    try:
        ha_close = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
        ha_open = (df['Open'].shift(1) + df['Close'].shift(1)) / 2
        ha_high = df[['High', 'Open', 'Close']].max(axis=1)
        ha_low = df[['Low', 'Open', 'Close']].min(axis=1)
        mhac = (ha_open + ha_high + ha_low + ha_close) / 4
        
        # 55-period TEMA of MHAC
        ema1 = mhac.ewm(span=55, adjust=False).mean()
        ema2 = ema1.ewm(span=55, adjust=False).mean()
        ema3 = ema2.ewm(span=55, adjust=False).mean()
        tema_mhac = 3 * ema1 - 3 * ema2 + ema3
        
        tema_diff = tema_mhac.diff().iloc[-1]
        last_ha_close = ha_close.iloc[-1]
        last_ha_open = ha_open.iloc[-1]
        
        if tema_diff > 0 and last_ha_close > last_ha_open:
            indicators['hacolt'] = 100.0  # Strong Buy / Uptrend
        elif tema_diff < 0 and last_ha_close < last_ha_open:
            indicators['hacolt'] = 0.0    # Strong Sell / Downtrend
        else:
            indicators['hacolt'] = 50.0   # Neutral / Choppy
    except Exception:
        indicators['hacolt'] = 50.0

    # ── ADX / DMI (Trend strength + directional bias) ─────────────────────────
    try:
        tr = pd.concat([
            df['High'] - df['Low'],
            (df['High'] - df['Close'].shift()).abs(),
            (df['Low'] - df['Close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        up_move = (df['High'] - df['High'].shift()).clip(lower=0)
        down_move = (df['Low'].shift() - df['Low']).clip(lower=0)
        plus_di = 100 * (up_move.ewm(span=14, adjust=False).mean() / atr14)
        minus_di = 100 * (down_move.ewm(span=14, adjust=False).mean() / atr14)
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        adx = dx.ewm(span=14, adjust=False).mean()
        indicators['adx'] = round(float(adx.iloc[-1]), 2) if not pd.isna(adx.iloc[-1]) else 0.0
        indicators['plus_di'] = round(float(plus_di.iloc[-1]), 2) if not pd.isna(plus_di.iloc[-1]) else 0.0
        indicators['minus_di'] = round(float(minus_di.iloc[-1]), 2) if not pd.isna(minus_di.iloc[-1]) else 0.0
        indicators['dmi_bullish'] = (indicators['plus_di'] > indicators['minus_di'] and indicators['adx'] > 25)
    except Exception:
        indicators['adx'] = 0.0
        indicators['plus_di'] = 0.0
        indicators['minus_di'] = 0.0
        indicators['dmi_bullish'] = False

    # ── RSI slope (acceleration vs just level) ────────────────────────────────
    try:
        rsi_series = 100 - (100 / (1 + rs))
        indicators['rsi_slope'] = round(float(rsi_series.diff(5).iloc[-1]), 2) if len(rsi_series) >= 6 else 0.0
    except Exception:
        indicators['rsi_slope'] = 0.0

    # ── Advanced Filters (Volume Divergence, VWAP, ATR, Supertrend) ──────────
    try:
        # ATR (14 period)
        high_low = df['High'] - df['Low']
        high_close = np.abs(df['High'] - df['Close'].shift())
        low_close = np.abs(df['Low'] - df['Close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(14).mean()
        indicators['atr'] = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else None
        if indicators['atr'] is not None and indicators['atr'] > 0:
            close_current = float(df['Close'].iloc[-1])
            indicators['atr_pct'] = round(float(indicators['atr'] / close_current * 100), 2) if close_current > 0 else 2.0
        else:
            indicators['atr_pct'] = 2.0

        # Supertrend (Basic approximation using ATR and Multiplier=3)
        hl2 = (df['High'] + df['Low']) / 2
        indicators['supertrend_lower'] = float((hl2 - (3 * atr)).iloc[-1]) if not pd.isna(atr.iloc[-1]) else None
        indicators['supertrend_upper'] = float((hl2 + (3 * atr)).iloc[-1]) if not pd.isna(atr.iloc[-1]) else None

        # VWAP & Volume Divergence (Intraday/Short-term institutional interest)
        if 'Volume' in df.columns and len(df) >= 20:
            vol = df['Volume']
            typical_price = (df['High'] + df['Low'] + df['Close']) / 3
            rolling_tp_vol = (typical_price * vol).rolling(20).sum()
            rolling_vol = vol.rolling(20).sum()
            vwap = rolling_tp_vol / rolling_vol
            indicators['vwap_20'] = float(vwap.iloc[-1]) if not pd.isna(vwap.iloc[-1]) else None
            
            price_change = df['Close'].diff().iloc[-1]
            vol_change = vol.diff().iloc[-1]
            if price_change > 0 and vol_change > 0:
                indicators['volume_trend'] = "Bullish Confirmation (Price Up, Volume Up)"
            elif price_change > 0 and vol_change < 0:
                indicators['volume_trend'] = "Bearish Divergence (Price Up, Volume Down)"
            elif price_change < 0 and vol_change > 0:
                indicators['volume_trend'] = "Bearish Confirmation (Price Down, Volume Up)"
            else:
                indicators['volume_trend'] = "Weakness (Price Down, Volume Down)"
            
            # ── Up/Down Volume Ratio (5-day) ──────────────────────────────
            up_vol = vol.where(df['Close'].diff() > 0, 0).tail(5).sum()
            down_vol = vol.where(df['Close'].diff() < 0, 0).tail(5).sum()
            indicators['up_down_vol_ratio'] = round(float(up_vol / max(down_vol, 1)), 2)
            
            # ── Close vs VWAP position ────────────────────────────────────
            if indicators.get('vwap_20') and indicators['vwap_20'] > 0:
                indicators['above_vwap'] = float(df['Close'].iloc[-1]) > indicators['vwap_20']
            else:
                indicators['above_vwap'] = False
            
            # ── Large block proxy (volume > 5× median) ────────────────────
            median_vol_20 = vol.tail(20).median()
            indicators['block_volume_detected'] = bool(vol.iloc[-1] > 5 * median_vol_20 and df['Close'].iloc[-1] > df['Open'].iloc[-1])
        else:
            indicators['vwap_20'] = None
            indicators['volume_trend'] = None
            indicators['up_down_vol_ratio'] = 0.0
            indicators['above_vwap'] = False
            indicators['block_volume_detected'] = False
            
        # ── 1. TTM Squeeze ────────────────────────────────────────────
        if indicators.get('bb_upper') and indicators.get('atr') and indicators.get('sma_20'):
            kc_upper = indicators['sma_20'] + (1.5 * indicators['atr'])
            kc_lower = indicators['sma_20'] - (1.5 * indicators['atr'])
            squeeze_on = (indicators['bb_upper'] < kc_upper) and (indicators['bb_lower'] > kc_lower)
            indicators['ttm_squeeze_on'] = squeeze_on
            indicators['ttm_squeeze_fired_long'] = (not squeeze_on) and (indicators.get('momentum_20', 0) > 0)
            
        # ── 2. OBV (On-Balance Volume) ────────────────────────────────
        if 'Volume' in df.columns:
            obv = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
            obv_sma20 = obv.rolling(20).mean()
            indicators['obv'] = float(obv.iloc[-1])
            indicators['obv_sma20'] = float(obv_sma20.iloc[-1])
            indicators['obv_bullish'] = float(obv.iloc[-1]) > float(obv_sma20.iloc[-1])
            
        # ── 3. Chaikin Money Flow (CMF) ───────────────────────────────
        if 'Volume' in df.columns:
            mf_multiplier = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / (df['High'] - df['Low'] + 1e-9)
            mf_volume = mf_multiplier * df['Volume']
            cmf = mf_volume.rolling(20).sum() / (df['Volume'].rolling(20).sum() + 1e-9)
            indicators['cmf'] = float(cmf.iloc[-1])
            indicators['cmf_bullish'] = indicators['cmf'] > 0.10
            
        # ── 4. Stochastic RSI ─────────────────────────────────────────
        rsi_s = 100 - (100 / (1 + rs))
        rsi_min = rsi_s.rolling(14).min()
        rsi_max = rsi_s.rolling(14).max()
        stoch_rsi = (rsi_s - rsi_min) / (rsi_max - rsi_min + 1e-9)
        stoch_rsi_k = stoch_rsi.rolling(3).mean() * 100
        stoch_rsi_d = stoch_rsi_k.rolling(3).mean()
        indicators['stoch_rsi_k'] = float(stoch_rsi_k.iloc[-1])
        indicators['stoch_rsi_d'] = float(stoch_rsi_d.iloc[-1])
            
    except Exception as e:
        pass # Optional advanced indicators; skip on failure
        
    return indicators


def std_norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _blend_empirical_prob(symbol: str, model_prob: float) -> float:
    """Multi-horizon blended probability with PID-adjusted horizon weights.

    Tracks hit rates separately for 63d and 90d horizons.  If one horizon consistently
    outperforms the other (higher hit rate), its weight increases via PID logic.
    Model-derived P(≥3%) enters at a baseline 0.40 weight; empirical evidence takes
    the remaining 0.60, split between 63d and 90d observations.

    Over the long run this means the system self-corrects: if the model's 90d
    predictions are unreliable but 63d works, the blend shifts toward 63d evidence.
    """
    try:
        with db_conn() as conn:
            row = conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN actual_return_63d >= 3.0 THEN 1 ELSE 0 END) AS hits_63,
                        SUM(CASE WHEN actual_peak_return_90d >= 5.0 THEN 1 ELSE 0 END) AS hits_90
                    FROM wealth_builder_evaluations
                    WHERE symbol = :sym AND evaluated = TRUE
                """),
                {"sym": symbol},
            ).fetchone()

        n = int(row[0] or 0)
        n_63 = int(row[1] or 0)
        n_90 = int(row[2] or 0)

        if n < 5:
            return model_prob  # not enough data — pure model

        hit_63 = n_63 / n
        hit_90 = n_90 / n

        # PID-adjusted horizon weights: the horizon with higher hit rate gets
        # proportionally more weight, capped at 70/30 split to avoid overfitting.
        total_hit = max(hit_63 + hit_90, 1e-6)
        w_63 = min(0.70, max(0.30, hit_63 / total_hit))
        w_90 = 1.0 - w_63

        # Blend: 40% model + 60% empirical (split by horizon weights)
        empirical = w_63 * hit_63 + w_90 * hit_90
        return round(0.40 * model_prob + 0.60 * empirical, 4)

    except Exception:
        pass
    return model_prob


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 BULLISH CONFLUENCE ENGINE — 5 independent signal channels
# ═══════════════════════════════════════════════════════════════════════════════

_CHANNEL_HIT_RATES = {
    "momentum":        0.62,
    "institutional":   0.58,
    "options":         0.65,
    "fundamental":     0.55,
    "cross_asset":     0.54,
}

_CHANNEL_WEIGHTS = {
    "momentum":        0.25,
    "institutional":   0.25,
    "options":         0.15,
    "fundamental":     0.20,
    "cross_asset":     0.15,
}

def _update_channel_hit_rates():
    """Calibrate per-channel hit rates from evaluated wealth_builder_evaluations.

    Uses the confluence.bullish_channels recorded in wealth_scan_history picks
    and matches them against evaluated wbe rows to compute per-channel accuracy.
    """
    global _CHANNEL_HIT_RATES
    try:
        with db_conn() as conn:
            total = conn.execute(text("""
                SELECT COUNT(*) FROM wealth_builder_evaluations
                WHERE evaluated = TRUE
            """)).fetchone()
            if not total or total[0] < 20:
                return

            channel_hits = {"momentum": 0, "institutional": 0, "options": 0, "fundamental": 0, "cross_asset": 0}
            channel_total = {"momentum": 0, "institutional": 0, "options": 0, "fundamental": 0, "cross_asset": 0}

            wbe_rows = conn.execute(text("""
                SELECT wbe.symbol, wbe.screened_at::DATE, wbe.actual_return_63d
                FROM wealth_builder_evaluations wbe
                WHERE wbe.evaluated = TRUE
                ORDER BY wbe.screened_at DESC
                LIMIT 500
            """)).fetchall()

            for wbe_row in wbe_rows:
                sym, screen_date, actual_63d = wbe_row[0], wbe_row[1], wbe_row[2]
                if actual_63d is None:
                    continue
                hit = actual_63d >= 3.0

                scan_row = conn.execute(text("""
                    SELECT picks FROM wealth_scan_history
                    WHERE scan_mode = 'broad' AND generated_at::DATE = :d
                    ORDER BY generated_at DESC LIMIT 1
                """), {"d": screen_date}).fetchone()

                if not scan_row:
                    continue

                picks = json.loads(scan_row[0]) if isinstance(scan_row[0], str) else (scan_row[0] or [])
                for c in picks:
                    if c.get("symbol") != sym:
                        continue
                    confluence = c.get("confluence", {})
                    bullish_channels = confluence.get("bullish_channels", [])
                    for ch in bullish_channels:
                        ch_name = str(ch).lower()
                        if ch_name in channel_total:
                            channel_total[ch_name] += 1
                            if hit:
                                channel_hits[ch_name] += 1
                    break

            for ch in _CHANNEL_HIT_RATES:
                if channel_total.get(ch, 0) >= 10:
                    _CHANNEL_HIT_RATES[ch] = round(channel_hits[ch] / channel_total[ch], 4)

            sample_sz = sum(channel_total.values())
            if sample_sz > 0:
                conn.execute(text("""
                    INSERT INTO channel_hit_rates (momentum, institutional, options, fundamental, cross_asset, sample_size)
                    VALUES (:mo, :in_, :op, :fu, :cr, :n)
                """), {
                    "mo": _CHANNEL_HIT_RATES.get("momentum"),
                    "in_": _CHANNEL_HIT_RATES.get("institutional"),
                    "op": _CHANNEL_HIT_RATES.get("options"),
                    "fu": _CHANNEL_HIT_RATES.get("fundamental"),
                    "cr": _CHANNEL_HIT_RATES.get("cross_asset"),
                    "n": sample_sz,
                })
                conn.commit()

            print(f"[ChannelCal] Per-channel hit rates calibrated from {sample_sz} signals: {_CHANNEL_HIT_RATES}")

    except Exception as e:
        print(f"[ChannelCal] Calibration skipped: {e}")


def _eval_channel_momentum(indicators: dict, prediction: dict, hist: pd.DataFrame) -> dict:
    """Channel A: Price Momentum — HACOLT, ADX/DMI, EMA Ribbon, RSI slope, Donchian."""
    signals = []
    conf = 0.0

    hacolt = indicators.get("hacolt", 50.0)
    if hacolt == 100.0:
        signals.append("hacolt_buy")
        conf += 0.22
    elif hacolt == 0.0:
        signals.append("hacolt_sell")

    adx = indicators.get("adx", 0)
    if indicators.get("dmi_bullish"):
        signals.append("dmi_bullish")
        conf += 0.20

    ema_9 = indicators.get("ema_9", 0) or 0
    ema_20 = indicators.get("ema_20", 0) or 0
    ema_50 = indicators.get("ema_50", 0) or 0
    if ema_9 > ema_20 > ema_50 > 0:
        signals.append("ema_ribbon")
        conf += 0.20

    rsi = indicators.get("rsi", 50)
    rsi_slope = indicators.get("rsi_slope", 0)
    kde_rsi_prob = indicators.get("kde_rsi_prob", 0.5)
    
    if (40 < rsi < 70 and rsi_slope > 3) or (rsi >= 70 and rsi_slope > 3 and kde_rsi_prob < 0.90):
        signals.append("rsi_accelerating")
        conf += 0.18

    current_price = float(hist["Close"].iloc[-1])
    donchian_high = indicators.get("donchian_high_20", 0) or 0
    if donchian_high > 0 and current_price >= donchian_high:
        signals.append("donchian_breakout")
        conf += 0.20

    if indicators.get("ttm_squeeze_fired_long"):
        signals.append("ttm_squeeze_breakout")
        conf += 0.25

    bullish = len(signals) >= 3
    hit_rate = _CHANNEL_HIT_RATES["momentum"]
    return {
        "channel": "momentum",
        "bullish": bullish,
        "confidence": round(min(conf, 0.80), 3),
        "signals": signals,
        "hit_rate": hit_rate,
    }


def _eval_channel_institutional(indicators: dict, sd: dict, hist: pd.DataFrame) -> dict:
    """Channel B: Institutional Flow — volume surge, up/down vol, VWAP, block trades."""
    signals = []
    conf = 0.0

    avg_vol = sd.get("avg_volume_5d", 0) or 0
    current_vol = sd.get("volume", 0) or 0
    if avg_vol > 0 and current_vol > 2 * avg_vol:
        current_price = sd.get("current_price", 0) or 0
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current_price
        if current_price > prev_close:
            signals.append("volume_surge_up")
            conf += 0.30

    up_down = indicators.get("up_down_vol_ratio", 0)
    if up_down > 1.5:
        signals.append("up_vol_dominant")
        conf += 0.25

    if indicators.get("above_vwap"):
        signals.append("above_vwap")
        conf += 0.25

    if indicators.get("block_volume_detected"):
        signals.append("block_trade")
        conf += 0.20

    if indicators.get("obv_bullish"):
        signals.append("obv_divergence")
        conf += 0.25

    if indicators.get("cmf_bullish"):
        signals.append("cmf_accumulation")
        conf += 0.25

    bullish = len(signals) >= 3
    hit_rate = _CHANNEL_HIT_RATES["institutional"]
    return {
        "channel": "institutional",
        "bullish": bullish,
        "confidence": round(min(conf, 0.80), 3),
        "signals": signals,
        "hit_rate": hit_rate,
    }


def _eval_channel_fundamental(valuation: dict) -> dict:
    """Channel D: Fundamental Quality — EPS growth, analyst momentum, short interest, P/E."""
    signals = []
    conf = 0.0

    eps = valuation.get("trailing_eps")
    fwd_eps = valuation.get("forward_eps")
    if eps and fwd_eps and float(eps) > 0 and float(fwd_eps) > float(eps) * 1.10:
        signals.append("eps_growing")
        conf += 0.25
    elif eps and float(eps) > 0:
        signals.append("eps_positive")
        conf += 0.15

    eps_growth = valuation.get("eps_growth_fwd_pct")
    if eps_growth is not None and eps_growth > 10:
        signals.append("eps_strong_growth")
        conf += 0.25

    rev_growth = valuation.get("revenue_growth")
    if rev_growth is not None and rev_growth > 0.05:
        signals.append("revenue_growing")
        conf += 0.20

    upside = valuation.get("analyst_upside_pct")
    num_analysts = valuation.get("num_analyst_opinions")
    if upside and upside >= 10 and num_analysts and num_analysts >= 3:
        signals.append("analyst_conviction")
        conf += 0.15

    pe = valuation.get("pe")
    if pe and 5 < float(pe) < 30:
        signals.append("reasonable_pe")
        conf += 0.15

    short_pct = valuation.get("short_pct_float")
    if short_pct is not None and short_pct < 5:
        signals.append("low_short_interest")
        conf += 0.10
    elif short_pct is not None and short_pct > 15:
        signals.append("high_short_interest")
        conf -= 0.15

    bullish = len(signals) >= 3
    hit_rate = _CHANNEL_HIT_RATES["fundamental"]
    return {
        "channel": "fundamental",
        "bullish": bullish,
        "confidence": round(max(min(conf, 0.85), 0.0), 3),
        "signals": signals,
        "hit_rate": hit_rate,
    }


def _eval_channel_cross_asset(symbol: str, market: str, valuation: dict, regime_snap: dict, sd: dict, hist: pd.DataFrame) -> dict:
    """Channel E: Cross-Asset — sector rotation, beta regime, commodity proxy, AUD tailwind."""
    signals = []
    conf = 0.0

    sector = valuation.get("sector", "")
    sector_lower = sector.lower() if sector else ""

    # Beta / regime fit
    bear = regime_snap.get("bear_market", False)
    vix = regime_snap.get("vix_level", 20)
    if not bear and vix < 22:
        signals.append("bull_regime")
        conf += 0.25

    # Sector strength vs index (3m)
    index_ticker = "^AXJO" if market == "AU" else "^IXIC" if market == "US" else None
    if index_ticker:
        try:
            xjo_hist = get_historical_data(index_ticker, period="3mo", market=None)
            if xjo_hist is not None and not xjo_hist.empty and len(xjo_hist) >= 60:
                xjo_ret = round((float(xjo_hist["Close"].iloc[-1]) / float(xjo_hist["Close"].iloc[-60])) - 1, 4) * 100
                cp = sd.get("current_price", 0) or 0
                if cp > 0 and len(hist) >= 60:
                    stock_ret = round((cp / float(hist["Close"].iloc[-60])) - 1, 4) * 100
                    if stock_ret > xjo_ret:
                        signals.append("outperforming_xjo")
                        conf += 0.20
        except Exception:
            pass

    # Commodity proxy for materials/mining
    if "material" in sector_lower or "mining" in sector_lower:
        try:
            macro = _get_macro_data_cached()
            copper = macro.get("copper", {}).get("trend_30d", 0)
            gold = macro.get("gold", {}).get("trend_30d", 0)
            if copper > 1 or gold > 1:
                signals.append("commodity_tailwind")
                conf += 0.20
        except Exception:
            pass

    # Financials / rate regime
    if "financial" in sector_lower or "bank" in sector_lower:
        try:
            macro = _get_macro_data_cached()
            au_yield_current = macro.get("au_10y_yield", {}).get("current", 4.0)
            if au_yield_current > 3.5 and au_yield_current < 6.0:
                signals.append("rate_margin_favorable")
                conf += 0.15
        except Exception:
            pass

    # AUD/USD tailwind
    try:
        macro = _get_macro_data_cached()
        aud_trend = macro.get("aud_usd", {}).get("trend_30d", 0)
        if aud_trend > 1:
            signals.append("aud_strengthening")
            conf += 0.10
        elif aud_trend < -2:
            if "material" in sector_lower or "mining" in sector_lower:
                signals.append("aud_weak_miners_tailwind")
                conf += 0.10
    except Exception:
        pass

    bullish = len(signals) >= 2
    hit_rate = _CHANNEL_HIT_RATES["cross_asset"]
    return {
        "channel": "cross_asset",
        "bullish": bullish,
        "confidence": round(min(conf, 0.70), 3),
        "signals": signals,
        "hit_rate": hit_rate,
    }


def _eval_channel_options(symbol: str, market: str) -> dict:
    """Channel C: Options Market — IV Rank, Put/Call ratio (EODHD options endpoint).
    Returns neutral when options data is unavailable (not all ASX stocks have ETOs).
    In UAT_MODE, always returns unavailable to avoid consuming EODHD API calls."""
    if UAT_MODE or not EODHD_API_KEY:
        return {"channel": "options", "bullish": True, "confidence": 0.0,
                "signals": ["no_options_data"], "hit_rate": 0.50, "unavailable": True}

    try:
        _eodhd_rate_limit()
        ticker = format_ticker(symbol, market).replace(".AX", ".AU")
        url = f"https://eodhd.com/api/options/{ticker}"
        r = requests.get(url, params={"api_token": EODHD_API_KEY, "fmt": "json"}, timeout=8)
        if r.status_code != 200:
            return {"channel": "options", "bullish": True, "confidence": 0.0,
                    "signals": ["no_options_data"], "hit_rate": 0.50, "unavailable": True}

        data = r.json()
        signals = []
        conf = 0.0

        # IV Rank approximation from available contract IVs
        iv_values = []
        for expiry in (data.get("data") or data if isinstance(data, list) else [data])[:1]:
            contracts = expiry.get("options", []) if isinstance(expiry, dict) else []
            for c in contracts:
                iv = c.get("impliedVolatility")
                if iv:
                    iv_values.append(float(iv))
        if iv_values:
            avg_iv = sum(iv_values) / len(iv_values)
            if avg_iv > 0.25:
                signals.append("elevated_iv")
                conf += 0.20
            elif avg_iv < 0.15:
                signals.append("low_iv")

        # Put/Call ratio from open interest / volume
        put_oi = 0
        call_oi = 0
        for expiry in (data.get("data") or data if isinstance(data, list) else [data])[:1]:
            contracts = expiry.get("options", []) if isinstance(expiry, dict) else []
            for c in contracts:
                oi = float(c.get("openInterest", 0))
                if c.get("type", "").upper() == "CALL":
                    call_oi += oi
                elif c.get("type", "").upper() == "PUT":
                    put_oi += oi

        if call_oi > 0:
            pc_ratio = put_oi / call_oi
            if pc_ratio < 0.7:
                signals.append("bullish_put_call")
                conf += 0.30
            elif pc_ratio > 1.5:
                signals.append("bearish_put_call")

        if not signals:
            signals.append("neutral_options")

        bullish = bool(signals) and all(s not in ["bearish_put_call"] for s in signals)
        hit_rate = _CHANNEL_HIT_RATES["options"]
        return {
            "channel": "options",
            "bullish": bullish,
            "confidence": round(min(conf, 0.65), 3),
            "signals": signals,
            "hit_rate": hit_rate,
            "unavailable": False,
        }
    except Exception:
        return {"channel": "options", "bullish": True, "confidence": 0.0,
                "signals": ["no_options_data"], "hit_rate": 0.50, "unavailable": True}


def bullish_confluence_score(symbol: str, market: str, indicators: dict, prediction: dict,
                              sd: dict, hist: pd.DataFrame, valuation: dict,
                              regime_snap: dict) -> dict:
    """Multi-channel bullish confluence voting engine.

    Requires ≥3 of 5 independent channels to vote bullish for HIGH confidence.
    Each channel is self-contained — bullish signal in one does not leak into another.
    Channels that are unavailable (e.g. no options data) are PASSED (neutral).
    """
    channels = {}

    # Channel A — Price Momentum
    ch_a = _eval_channel_momentum(indicators, prediction, hist)
    channels["momentum"] = ch_a

    # Channel B — Institutional Flow
    ch_b = _eval_channel_institutional(indicators, sd, hist)
    channels["institutional"] = ch_b

    # Channel C — Options Market (skipped for broad scan — most ASX stocks lack ETOs)
    ch_c = {"channel": "options", "bullish": True, "confidence": 0.0,
            "signals": ["no_options_data"], "hit_rate": 0.50, "unavailable": True}
    channels["options"] = ch_c

    # Channel D — Fundamental Quality
    ch_d = _eval_channel_fundamental(valuation)
    channels["fundamental"] = ch_d

    # Channel E — Cross-Asset / Macro
    ch_e = _eval_channel_cross_asset(symbol, market, valuation, regime_snap, sd, hist)
    channels["cross_asset"] = ch_e

    # ── Voting ─────────────────────────────────────────────────────────────────
    available_channels = [name for name, ch in channels.items()
                          if not ch.get("unavailable", False)]

    bullish_channels = [name for name, ch in channels.items()
                        if ch["bullish"] and not ch.get("unavailable", False)]

    n_available = len(available_channels) or 1
    n_bullish = len(bullish_channels)

    # Weighted score: sum(channel_weight × bullish_flag × hit_rate)
    total_weight = sum(_CHANNEL_WEIGHTS.get(n, 0) for n in available_channels)
    if total_weight == 0:
        total_weight = 1.0

    score = 0.0
    for name in available_channels:
        ch = channels[name]
        w = _CHANNEL_WEIGHTS.get(name, 0.20)
        if ch["bullish"]:
            score += w * ch["confidence"] * ch["hit_rate"] / total_weight

    # Confluence bonus: ≥4-of-5 = +12% boost; ≥3-of-5 = baseline
    if n_bullish >= 4:
        score = min(1.0, score * 1.12)
    elif n_bullish == 3:
        score = min(1.0, score * 1.05)

    # Normalize to 0-100 scale
    score_100 = round(score * 100, 2)
    confidence = "high" if n_bullish >= 4 else "medium" if n_bullish >= 2 else "low"

    return {
        "score": score_100,
        "confidence": confidence,
        "bullish_channels": n_bullish,
        "total_channels": n_available,
        "channels": {k: {
            "bullish": v["bullish"],
            "confidence": v["confidence"],
            "signals": v["signals"],
            "hit_rate": v["hit_rate"],
        } for k, v in channels.items()},
    }


def _get_strategy_params(dollar_volume: float) -> dict:
    """Return cap-tier calibrated strategy parameters.

    Targets are enforced to an asymmetric 2:1 Reward:Risk ratio.
      Large/mid cap: 4% target, 2% stop → R:R 2.0
      Small cap: 5% target, 2.5% stop → R:R 2.0
    Aligned for 3-4% per-cycle compound strategy (12-13% annualised).
    """
    if dollar_volume > 5_000_000:
        return {
            "target_pct": 0.040,
            "stop_pct": 0.020,
            "trailing_stop_pct": 2.5,
            "tier": "large_mid",
        }
    return {
        "target_pct": 0.050,
        "stop_pct": 0.025,
        "trailing_stop_pct": 3.0,
        "tier": "small",
    }


def get_trend_streak(symbol: str) -> dict:
    """Return streak of consecutive same-trend days from daily_predictions."""
    try:
        with db_conn() as conn:
            rows = conn.execute(
                text("""
                    SELECT trend FROM daily_predictions
                    WHERE symbol = :symbol
                    ORDER BY prediction_date DESC
                    LIMIT 7
                """),
                {"symbol": symbol},
            ).fetchall()
        if not rows:
            return {"trend_streak": 0, "streak_label": "New signal"}
        latest_trend = rows[0][0]
        streak = 1
        for row in rows[1:]:
            if row[0] == latest_trend:
                streak += 1
            else:
                break
        trend_word = (latest_trend or "neutral").capitalize()
        label = "New signal" if streak == 1 else f"{trend_word} {streak}d"
        return {"trend_streak": streak, "streak_label": label}
    except Exception:
        return {"trend_streak": 0, "streak_label": ""}


def get_user_telegram_recipients(user_id: str) -> list[dict]:
    try:
        with db_conn() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, user_id, chat_id, label, is_active, created_at, updated_at
                    FROM user_telegram_recipients
                    WHERE user_id = :user_id AND is_active = 1
                    ORDER BY created_at ASC
                    """
                ),
                {"user_id": user_id},
            ).fetchall()
        return [
            {
                "id": row[0],
                "user_id": row[1],
                "chat_id": str(row[2]),
                "label": row[3] or None,
                "is_active": bool(row[4]),
                "created_at": row[5].isoformat() if row[5] else None,
                "updated_at": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ]
    except Exception:
        return []


def list_recent_telegram_send_log(user_id: str, limit: int = 20) -> list[dict]:
    safe_limit = max(1, min(int(limit or 20), 50))
    try:
        with db_conn() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT id, user_id, chat_id, message_type, market, digest_key, status,
                           delivery_mode, source, error_message, telegram_message_id,
                           payload_preview, created_at, sent_at
                    FROM telegram_send_log
                    WHERE user_id = :user_id
                    ORDER BY created_at DESC
                    LIMIT {safe_limit}
                    """
                ),
                {"user_id": user_id},
            ).fetchall()
        return [
            {
                "id": row[0],
                "user_id": row[1],
                "chat_id": row[2],
                "message_type": row[3],
                "market": row[4],
                "digest_key": row[5],
                "status": row[6],
                "delivery_mode": row[7],
                "source": row[8],
                "error_message": row[9],
                "telegram_message_id": row[10],
                "payload_preview": row[11],
                "created_at": row[12].isoformat() if row[12] else None,
                "sent_at": row[13].isoformat() if row[13] else None,
            }
            for row in rows
        ]
    except Exception:
        return []


def log_position_event(trade_id: str, user_id: str, event_type: str, event_message: str, payload: Optional[dict] = None) -> None:
    try:
        with db_conn() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO position_events (id, trade_id, user_id, event_type, event_message, payload_json, created_at)
                    VALUES (:id, :trade_id, :user_id, :event_type, :event_message, :payload_json, :created_at)
                    """
                ),
                {
                    "id": str(uuid4()),
                    "trade_id": trade_id,
                    "user_id": user_id,
                    "event_type": event_type,
                    "event_message": event_message,
                    "payload_json": json.dumps(payload or {}),
                    "created_at": datetime.utcnow(),
                },
            )
    except Exception:
        pass


def list_position_events(user_id: str, limit: int = 30) -> list[dict]:
    safe_limit = max(1, min(int(limit or 30), 100))
    try:
        with db_conn() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT id, trade_id, user_id, event_type, event_message, payload_json, created_at
                    FROM position_events
                    WHERE user_id = :user_id
                    ORDER BY created_at DESC
                    LIMIT {safe_limit}
                    """
                ),
                {"user_id": user_id},
            ).fetchall()
        return [
            {
                "id": row[0],
                "trade_id": row[1],
                "user_id": row[2],
                "event_type": row[3],
                "event_message": row[4],
                "payload": json.loads(row[5]) if row[5] else {},
                "created_at": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ]
    except Exception:
        return []


def _estimate_commission(gross_amount: float) -> float:
    value = max(float(gross_amount or 0.0), 0.0)
    return 10.0 if value <= 1000.0 else 20.0


def list_advice_execution_actions(user_id: str, limit: int = 50) -> list[dict]:
    safe_limit = max(1, min(int(limit or 50), 200))
    try:
        with db_conn() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT id, user_id, symbol, market, action_type, quantity, execution_price,
                           gross_amount, commission, net_amount, advice_cache_key,
                           source_message_type, notes, created_at
                    FROM advice_execution_actions
                    WHERE user_id = :user_id
                    ORDER BY created_at DESC
                    LIMIT {safe_limit}
                    """
                ),
                {"user_id": user_id},
            ).fetchall()
        return [
            {
                "id": row[0],
                "user_id": row[1],
                "symbol": row[2],
                "market": row[3],
                "action_type": row[4],
                "quantity": float(row[5] or 0),
                "execution_price": float(row[6] or 0),
                "gross_amount": float(row[7] or 0),
                "commission": float(row[8] or 0),
                "net_amount": float(row[9] or 0),
                "advice_cache_key": row[10],
                "source_message_type": row[11],
                "notes": row[12],
                "created_at": row[13].isoformat() if row[13] else None,
            }
            for row in rows
        ]
    except Exception:
        return []


def get_user_execution_holdings(user_id: str) -> list[dict]:
    """Aggregate user-reported executed actions into current holdings state."""
    try:
        with db_conn() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT symbol, market, action_type, quantity, execution_price, commission, created_at
                    FROM advice_execution_actions
                    WHERE user_id = :user_id
                    ORDER BY created_at ASC
                    """
                ),
                {"user_id": user_id},
            ).fetchall()
    except Exception:
        rows = []

    state: dict[str, dict] = {}
    for row in rows:
        symbol = str(row[0] or "").upper().strip()
        if not symbol:
            continue
        market = (row[1] or "AU").upper()
        action_type = (row[2] or "BUY").upper().strip()
        qty = max(float(row[3] or 0), 0.0)
        price = max(float(row[4] or 0), 0.0)
        commission = max(float(row[5] or 0), 0.0)
        created_at = row[6]
        if qty <= 0:
            continue

        bucket = state.setdefault(
            symbol,
            {
                "symbol": symbol,
                "market": market,
                "quantity": 0.0,
                "cost_basis": 0.0,
                "commission_paid": 0.0,
                "last_action_at": None,
            },
        )

        if action_type in {"SELL", "REDUCE"}:
            if bucket["quantity"] <= 0:
                bucket["commission_paid"] += commission
            else:
                avg_cost = bucket["cost_basis"] / bucket["quantity"] if bucket["quantity"] > 0 else 0.0
                sell_qty = min(qty, bucket["quantity"])
                bucket["quantity"] -= sell_qty
                bucket["cost_basis"] = max(bucket["cost_basis"] - (avg_cost * sell_qty), 0.0)
                bucket["commission_paid"] += commission
        else:
            gross = qty * price
            bucket["quantity"] += qty
            bucket["cost_basis"] += gross + commission
            bucket["commission_paid"] += commission

        bucket["last_action_at"] = created_at or bucket["last_action_at"]

    holdings = []
    for item in state.values():
        qty = float(item["quantity"] or 0)
        if qty <= 0:
            continue
        avg_cost = item["cost_basis"] / qty if qty > 0 else 0.0
        holdings.append(
            {
                "symbol": item["symbol"],
                "market": item["market"],
                "quantity": round(qty, 6),
                "avg_cost": round(avg_cost, 4),
                "invested_amount": round(float(item["cost_basis"] or 0), 2),
                "commission_paid": round(float(item["commission_paid"] or 0), 2),
                "last_action_at": item["last_action_at"].isoformat() if item["last_action_at"] else None,
            }
        )
    holdings.sort(key=lambda row: row["invested_amount"], reverse=True)
    return holdings


def _build_holdings_signature(user_id: str) -> str:
    holdings = get_user_execution_holdings(user_id)
    if not holdings:
        return "none"
    return ",".join(
        f"{item['symbol']}:{item['quantity']:.4f}:{item['avg_cost']:.2f}" for item in holdings[:20]
    )


def get_broker_telegram_recipients() -> list:
    """Read active Telegram recipients from the broker app's shared database table."""
    try:
        broker_filter = ""
        params = {}
        if TELEGRAM_BROKER_ID:
            broker_filter = "AND r.broker_id = :broker_id"
            params["broker_id"] = TELEGRAM_BROKER_ID
        with db_conn() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT
                        r.broker_id,
                        r.chat_id,
                        COALESCE(r.name, '') AS name,
                        r.is_active,
                        r.created_at,
                        s.snoozed_until,
                        s.last_ack_at,
                        s.last_command
                    FROM broker_telegram_recipients r
                    LEFT JOIN broker_telegram_state s
                        ON s.broker_id = r.broker_id AND s.chat_id = r.chat_id
                    WHERE r.is_active = TRUE
                      AND (s.snoozed_until IS NULL OR s.snoozed_until <= CURRENT_TIMESTAMP)
                      {broker_filter}
                    ORDER BY r.created_at ASC
                """),
                params,
            ).fetchall()
        return [
            {
                "broker_id": row[0],
                "chat_id": str(row[1]),
                "name": row[2] or None,
                "is_active": bool(row[3]),
                "created_at": row[4],
                "snoozed_until": row[5].isoformat() if row[5] else None,
                "last_ack_at": row[6].isoformat() if row[6] else None,
                "last_command": row[7],
            }
            for row in rows
        ]
    except Exception:
        return []


def _format_strategy_dashboard_html(strategy_dashboard: Optional[dict]) -> str:
    """Render a compact, deterministic strategy block for UI and Telegram parity."""
    if not strategy_dashboard:
        return ""

    action_mix = strategy_dashboard.get("action_mix") or {}
    filters = strategy_dashboard.get("filters") or {}
    risk_controls = strategy_dashboard.get("risk_controls") or []
    portfolio_mix = strategy_dashboard.get("portfolio_mix") or {}

    mix_labels = [
        ("BUY", int(action_mix.get("buy") or 0)),
        ("ADD", int(action_mix.get("add") or 0)),
        ("HOLD", int(action_mix.get("hold") or 0)),
        ("WATCH", int(action_mix.get("watch") or 0)),
        ("REDUCE", int(action_mix.get("reduce") or 0)),
    ]
    mix_line = " | ".join(f"{label} {count}" for label, count in mix_labels if count > 0) or "No actions yet"

    controls_line = ", ".join(str(item) for item in risk_controls[:3]) if risk_controls else "Standard risk controls active"
    filters_line = (
        f"Stage filters: {int(filters.get('qualified_count') or 0)} qualified"
        f" / {int(filters.get('analyzed_count') or 0)} analysed"
        f" / {int(filters.get('candidate_count') or 0)} candidates"
    )
    if filters.get("min_score") is not None:
        filters_line += f" | min score {float(filters.get('min_score') or 0):.0f}+"

    exposure_line = (
        f"Mix target: Growth {int(portfolio_mix.get('growth_pct') or 0)}%"
        f" | Core {int(portfolio_mix.get('core_hold_pct') or 0)}%"
        f" | Defensive/Cash {int(portfolio_mix.get('defensive_cash_pct') or 0)}%"
    )

    return "<br/>".join([
        "<b>Strategy Dashboard</b>",
        f"{mix_line}",
        filters_line,
        exposure_line,
        f"Risk controls: {controls_line}",
    ])


def _merge_summary_with_strategy(summary_html: str, strategy_dashboard: Optional[dict]) -> str:
    strategy_html = _format_strategy_dashboard_html(strategy_dashboard)
    if not strategy_html:
        return summary_html or ""
    if summary_html and "<b>Strategy Dashboard</b>" in summary_html:
        return summary_html
    if not summary_html:
        return strategy_html
    return f"{summary_html}<br/><br/>{strategy_html}"


def _send_telegram_payload(
    message_html: str,
    recipients: list[dict],
    *,
    user_id: Optional[str] = None,
    message_type: str = "advice",
    market: Optional[str] = None,
    delivery_mode: str = "manual",
    digest_key: Optional[str] = None,
    source: str = "asx_backend",
    strategy_dashboard: Optional[dict] = None,
    reply_markup: Optional[dict] = None,
) -> dict:
    """Send a preformatted HTML Telegram payload and persist per-recipient results."""
    telegram_html = _merge_summary_with_strategy(message_html or "", strategy_dashboard)
    normalized_message = re.sub(r"<br\s*/?>", "\n", telegram_html or "")

    if not recipients:
        return {
            "sent": False,
            "success_count": 0,
            "failure_count": 0,
            "results": [],
            "error": "No active Telegram chat IDs configured for this user.",
        }

    if not TELEGRAM_BOT_TOKEN:
        return {
            "sent": False,
            "success_count": 0,
            "failure_count": len(recipients),
            "results": [],
            "error": "TELEGRAM_BOT_TOKEN is not configured.",
        }

    results = []
    success_count = 0
    with db_conn() as conn:
        for recipient in recipients:
            chat_id = str(recipient.get("chat_id") or "").strip()
            if not chat_id:
                continue

            log_id = str(uuid4())
            error_message = None
            telegram_message_id = None
            status_value = "failed"
            sent_at = None
            try:
                payload = {"chat_id": chat_id, "text": normalized_message, "parse_mode": "HTML"}
                if reply_markup:
                    payload["reply_markup"] = reply_markup
                resp = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json=payload,
                    timeout=8,
                )
                if resp.status_code == 200:
                    body = resp.json() if resp.content else {}
                    telegram_message_id = str((body.get("result") or {}).get("message_id") or "") or None
                    status_value = "sent"
                    sent_at = datetime.utcnow()
                    success_count += 1
                else:
                    try:
                        error_message = resp.json().get("description")
                    except Exception:
                        error_message = resp.text[:300] if resp.text else f"HTTP {resp.status_code}"
                    # Retry in plain text if Telegram rejects HTML entity parsing.
                    if error_message and "can't parse entities" in error_message.lower():
                        plain_text = re.sub(r"<[^>]+>", "", normalized_message)
                        retry = requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": chat_id, "text": plain_text},
                            timeout=8,
                        )
                        if retry.status_code == 200:
                            retry_body = retry.json() if retry.content else {}
                            telegram_message_id = str((retry_body.get("result") or {}).get("message_id") or "") or None
                            status_value = "sent"
                            sent_at = datetime.utcnow()
                            error_message = None
                            success_count += 1
                        else:
                            try:
                                error_message = retry.json().get("description")
                            except Exception:
                                error_message = retry.text[:300] if retry.text else f"HTTP {retry.status_code}"
            except Exception as exc:
                error_message = str(exc)

            conn.execute(
                text(
                    """
                    INSERT INTO telegram_send_log (
                        id, user_id, chat_id, message_type, market, digest_key, status,
                        delivery_mode, source, error_message, telegram_message_id,
                        payload_preview, created_at, sent_at
                    ) VALUES (
                        :id, :user_id, :chat_id, :message_type, :market, :digest_key, :status,
                        :delivery_mode, :source, :error_message, :telegram_message_id,
                        :payload_preview, :created_at, :sent_at
                    )
                    """
                ),
                {
                    "id": log_id,
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "message_type": message_type,
                    "market": market,
                    "digest_key": digest_key,
                    "status": status_value,
                    "delivery_mode": delivery_mode,
                    "source": source,
                    "error_message": error_message,
                    "telegram_message_id": telegram_message_id,
                    "payload_preview": normalized_message,
                    "created_at": datetime.utcnow(),
                    "sent_at": sent_at,
                },
            )
            results.append(
                {
                    "chat_id": chat_id,
                    "label": recipient.get("label") or recipient.get("name"),
                    "status": status_value,
                    "error_message": error_message,
                    "telegram_message_id": telegram_message_id,
                    "sent_at": sent_at.isoformat() if sent_at else None,
                }
            )

    return {
        "sent": success_count > 0,
        "success_count": success_count,
        "failure_count": max(len(results) - success_count, 0),
        "results": results,
        "error": None if success_count > 0 else "Telegram delivery failed for all configured chats.",
    }


def has_digest_been_sent(user_id: str, market: str, digest_key: str) -> bool:
    try:
        with db_conn() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM telegram_send_log
                    WHERE user_id = :user_id
                      AND message_type = 'daily_digest'
                      AND market = :market
                      AND digest_key = :digest_key
                      AND status = 'sent'
                    LIMIT 1
                    """
                ),
                {"user_id": user_id, "market": market, "digest_key": digest_key},
            ).fetchone()
        return bool(row)
    except Exception:
        return False


def _get_scheduler_timezone() -> ZoneInfo:
    tz_name = os.getenv("SCHEDULER_TIMEZONE", os.getenv("DAILY_DIGEST_TIMEZONE", "Australia/Melbourne"))
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Australia/Melbourne")


def _daily_digest_cache_key() -> str:
    # Use scheduler timezone date key so 8am local sends use the expected local day.
    return datetime.now(_get_scheduler_timezone()).date().isoformat()


def _ensure_cached_payload_strategy(payload: dict, *, market: str, is_digest: bool) -> dict:
    """Backfill strategy fields for legacy cache entries created before strategy_dashboard existed."""
    if not isinstance(payload, dict):
        return payload

    items = payload.get("items") or []
    strategy_dashboard = payload.get("strategy_dashboard")
    if strategy_dashboard is None:
        if is_digest:
            strategy_dashboard = _build_strategy_dashboard(
                items,
                market=(payload.get("market") or market or "AU"),
                min_score=None,
                candidate_count=int(payload.get("candidate_count") or len(items)),
                analyzed_count=int(payload.get("analyzed_count") or len(items)),
                qualified_count=int(payload.get("qualified_count") or len(items)),
                under_one_selected=int(payload.get("under_one_selected") or 0),
                holdings_count=0,
            )
        else:
            holdings_context = payload.get("holdings_context") or []
            strategy_dashboard = _build_strategy_dashboard(
                items,
                market=(payload.get("market") or market or "AU"),
                min_score=payload.get("min_score"),
                candidate_count=int(payload.get("candidate_count") or len(items)),
                analyzed_count=int(payload.get("analyzed_count") or len(items)),
                qualified_count=int(payload.get("qualified_count") or len(items)),
                under_one_selected=int(payload.get("under_one_selected") or 0),
                holdings_count=len(holdings_context),
            )
        payload["strategy_dashboard"] = strategy_dashboard

    payload["summary"] = _merge_summary_with_strategy(payload.get("summary") or "", payload.get("strategy_dashboard"))
    return payload


def get_daily_digest_cache(market: str, limit: int = 5, cache_key: Optional[str] = None) -> dict:
    market_key = (market or "AU").upper()
    cache_key = cache_key or _daily_digest_cache_key()
    try:
        with db_conn() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT payload_preview
                    FROM telegram_send_log
                    WHERE message_type = 'daily_digest_cache'
                      AND market = :market
                      AND digest_key = :digest_key
                      AND status = 'cached'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"market": market_key, "digest_key": cache_key},
            ).fetchone()
            if row and row[0]:
                payload = json.loads(row[0])
                payload = _ensure_cached_payload_strategy(payload, market=market_key, is_digest=True)
                payload["cache_key"] = cache_key
                payload["cache_hit"] = True
                return payload
    except Exception:
        pass

    digest = build_daily_digest(market_key, limit=limit)
    digest["cache_key"] = cache_key
    digest["cache_hit"] = False
    try:
        with db_conn() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO telegram_send_log (
                        id, user_id, chat_id, message_type, market, digest_key, status,
                        delivery_mode, source, payload_preview, created_at
                    ) VALUES (
                        :id, NULL, NULL, 'daily_digest_cache', :market, :digest_key, 'cached',
                        'system', 'digest_cache', :payload_preview, :created_at
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "market": market_key,
                    "digest_key": cache_key,
                    "payload_preview": json.dumps(digest),
                    "created_at": datetime.utcnow(),
                },
            )
    except Exception:
        pass
    return digest


def _build_hedge_advice_cache_key(symbols: list[str], market: str, limit: int, holdings_signature: str = "none") -> str:
    key_symbols = sorted({str(symbol).upper().strip() for symbol in symbols or [] if str(symbol).strip()})
    return f"{datetime.utcnow().strftime('%Y-%m-%d')}|{(market or 'AU').upper()}|{limit}|{','.join(key_symbols)}|{holdings_signature}"


def get_hedge_advice_cache(user_id: str, symbols: list[str], market: str, limit: int) -> dict:
    holdings_signature = _build_holdings_signature(user_id)
    cache_key = _build_hedge_advice_cache_key(symbols, market, limit, holdings_signature=holdings_signature)
    try:
        with db_conn() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT payload_preview
                    FROM telegram_send_log
                    WHERE user_id = :user_id
                      AND message_type = 'hedge_advice_cache'
                      AND digest_key = :cache_key
                      AND status = 'cached'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"user_id": user_id, "cache_key": cache_key},
            ).fetchone()
            if row and row[0]:
                payload = json.loads(row[0])
                payload = _ensure_cached_payload_strategy(payload, market=market, is_digest=False)
                payload["cache_key"] = cache_key
                payload["cache_hit"] = True
                return payload
    except Exception:
        pass

    advice = build_hedge_advice(symbols, market=market, limit=limit, user_id=user_id)
    advice["cache_key"] = cache_key
    advice["cache_hit"] = False
    try:
        with db_conn() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO telegram_send_log (
                        id, user_id, chat_id, message_type, market, digest_key, status,
                        delivery_mode, source, payload_preview, created_at
                    ) VALUES (
                        :id, :user_id, NULL, 'hedge_advice_cache', :market, :digest_key, 'cached',
                        'system', 'advice_cache', :payload_preview, :created_at
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "market": advice["market"],
                    "digest_key": cache_key,
                    "payload_preview": json.dumps(advice),
                    "created_at": datetime.utcnow(),
                },
            )
    except Exception:
        pass
    return advice


def _recommend_action(item: dict) -> tuple[str, str]:
    trend = str(item.get("trend") or "neutral").lower()
    score = float(item.get("score") or 0)
    warning = bool(item.get("high_volatility_warning"))
    expected = float(item.get("expected_return_3m_pct") or 0)

    if warning or trend == "bearish" or score < 45:
        action = "REDUCE"
    elif score >= 75 and trend == "bullish" and expected >= 5:
        action = "BUY"
    elif score >= 60:
        action = "WATCH"
    else:
        action = "HOLD"

    reasons = []
    if expected:
        reasons.append(f"{expected:+.2f}% 3M return")
    if item.get("prob_ge_5pct") is not None:
        reasons.append(f"P(≥3%) {float(item['prob_ge_5pct']):.1f}%")
    if item.get("streak_label"):
        reasons.append(str(item["streak_label"]))
    if item.get("warning_message"):
        reasons.append(str(item["warning_message"]))
    if item.get("quality_reason"):
        reasons.append(str(item["quality_reason"]))

    return action, " · ".join(reasons[:3]) if reasons else "Model ranking summary"


def _to_prediction_row(row) -> dict:
    return {
        "symbol": row[0],
        "name": row[1],
        "current_price": float(row[2] or 0),
        "predicted_price_3m": float(row[3] or 0),
        "trend": row[4],
        "score": float(row[5] or 0),
        "prob_ge_5pct": float(row[6] or 0),
        "expected_return_3m_pct": float(row[7] or 0),
        "high_volatility_warning": bool(row[8]),
        "warning_message": row[9],
        "warning_type": row[10],
        "quality_reason": row[11],
        "confidence_low": float(row[12] or 0) if row[12] is not None else None,
        "confidence_high": float(row[13] or 0) if row[13] is not None else None,
        "streak_label": "Cached",
    }


def _build_advice_candidates(symbols: list[str], market: str, max_candidates: int = 500) -> list[str]:
    cleaned = []
    for symbol in symbols or []:
        symbol_clean = str(symbol).upper().strip()
        if symbol_clean and symbol_clean not in cleaned:
            cleaned.append(symbol_clean)

    if cleaned:
        return cleaned[: max(1, max_candidates)]

    market_key = (market or "AU").upper()
    if market_key == "AU":
        broad = list(ASX_COMPANIES.keys())
        # Broad-by-default: include all known AU symbols, including small caps and ETFs.
        return broad[: max(1, max_candidates)]

    fallback = TOP_SYMBOLS_BY_MARKET.get(market_key, TOP_SYMBOLS_BY_MARKET["AU"])
    return list(fallback[: max(1, max_candidates)])


def _load_cached_predictions_for_today(candidates: list[str]) -> dict[str, dict]:
    if not candidates:
        return {}
    today_str = datetime.utcnow().date().isoformat()
    candidate_set = set(candidates)
    try:
        with db_conn() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT symbol, name, current_price, predicted_price, trend, score, prob_ge_5pct,
                           expected_return_pct, high_volatility_warning, warning_message,
                           warning_type, quality_reason, confidence_low, confidence_high
                    FROM daily_predictions
                    WHERE prediction_date = :prediction_date
                    """
                ),
                {"prediction_date": today_str},
            ).fetchall()
    except Exception:
        rows = []

    cache_map: dict[str, dict] = {}
    for row in rows:
        symbol = str(row[0] or "").upper().strip()
        if symbol and symbol in candidate_set:
            cache_map[symbol] = _to_prediction_row(row)
    return cache_map


def _build_assistant_guidance(item: dict, action: str) -> dict:
    expected = float(item.get("expected_return_3m_pct") or 0)
    score = float(item.get("score") or 0)
    trend = str(item.get("trend") or "neutral").lower()
    warning = item.get("warning_message")
    current_price = float(item.get("current_price") or 0)

    entry_idea = "Wait for clearer confirmation before adding capital."
    next_step = "Review tomorrow unless the score or warning state changes."
    invalidation = "Exit if the model flips bearish or volatility warning worsens."
    sell_trigger = "Trim if expected upside compresses below 2% or score drops below 55."
    review_trigger = "Review on the next daily refresh."

    if action == "BUY":
        entry_idea = "Accumulate in small tranches rather than entering full size at once."
        next_step = "Start with a pilot position and reassess after the next daily digest."
        invalidation = "Do not add if the signal loses trend support or a fresh warning appears."
        sell_trigger = "Take profit in stages once the upside case weakens or the score slips below 60."
        review_trigger = "Review sooner if price jumps sharply without volume support."
    elif action == "WATCH":
        entry_idea = "Keep this on watch until conviction improves or price offers a better entry."
        next_step = "Wait for score improvement or a stronger streak before committing capital."
        invalidation = "Remove from watchlist if the model turns bearish or risk warning intensifies."
        sell_trigger = "If already bought, trim on score deterioration below 50."
        review_trigger = "Review after the next signal update or regime shift."
    elif action == "REDUCE":
        entry_idea = "Avoid new exposure while the risk-reward is deteriorating."
        next_step = "Cut exposure, tighten stops, or move to watch-only mode."
        invalidation = "Stay defensive until warnings clear and the score rebuilds."
        sell_trigger = "Sell or hedge immediately if price confirms the bearish move or warning persists."
        review_trigger = "Review only if risk clears and the trend stabilizes."
    elif action == "HOLD":
        entry_idea = "No fresh size increase yet; maintain only if it still fits your plan."
        next_step = "Hold existing size and wait for either stronger conviction or a clearer exit signal."
        invalidation = "Reduce if the score fades or volatility expands further."
        sell_trigger = "Trim on a bearish flip, a score below 50, or a broken trailing stop."
        review_trigger = "Review on tomorrow's digest or after a material price swing."

    if current_price > 0:
        invalidation = f"{invalidation} Reference price: {current_price:.2f}."
    if expected >= 8 and score >= 70 and trend == "bullish" and action in {"BUY", "HOLD"}:
        sell_trigger = "Scale out progressively once momentum cools after a strong run or the score drops below 62."
    if warning:
        next_step = f"Risk first: {warning}"

    return {
        "entry_idea": entry_idea,
        "next_step": next_step,
        "invalidation": invalidation,
        "sell_trigger": sell_trigger,
        "review_trigger": review_trigger,
    }


def _build_strategy_dashboard(
    items: list[dict],
    *,
    market: str,
    min_score: Optional[float],
    candidate_count: int,
    analyzed_count: int,
    qualified_count: int,
    under_one_selected: int,
    holdings_count: int,
) -> dict:
    action_mix = {"buy": 0, "add": 0, "hold": 0, "watch": 0, "reduce": 0}
    warning_count = 0
    bullish_count = 0
    avg_score = 0.0

    if items:
        total_score = 0.0
        for row in items:
            action = str(row.get("action") or "").strip().lower()
            if action in action_mix:
                action_mix[action] += 1
            score = float(row.get("score") or 0.0)
            total_score += score
            if str(row.get("trend") or "").lower() == "bullish":
                bullish_count += 1
            if row.get("warning_message"):
                warning_count += 1
        avg_score = total_score / len(items)

    growth_slots = action_mix["buy"] + action_mix["add"]
    defensive_slots = action_mix["reduce"] + max(1 if warning_count > 0 else 0, 0)
    defensive_cash_pct = min(50, 15 + defensive_slots * 10)
    growth_pct = min(70, 25 + growth_slots * 10)
    if growth_pct + defensive_cash_pct > 90:
        growth_pct = max(20, 90 - defensive_cash_pct)
    core_hold_pct = max(10, 100 - (growth_pct + defensive_cash_pct))

    confidence = "low"
    if avg_score >= 75:
        confidence = "high"
    elif avg_score >= 65:
        confidence = "medium"

    filters = {
        "market": (market or "AU").upper(),
        "min_score": min_score,
        "candidate_count": int(candidate_count),
        "analyzed_count": int(analyzed_count),
        "qualified_count": int(qualified_count),
        "under_one_selected": int(under_one_selected),
        "bullish_selected": int(bullish_count),
        "warning_selected": int(warning_count),
        "holdings_tracked": int(holdings_count),
    }

    risk_controls = [
        "staged_entries",
        "score_recheck_on_daily_digest",
        "position_size_capped_for_sub_1",
    ]
    if warning_count > 0:
        risk_controls.append("warning_flag_reduction")

    return {
        "confidence": confidence,
        "avg_score": round(avg_score, 2),
        "action_mix": action_mix,
        "filters": filters,
        "portfolio_mix": {
            "growth_pct": int(growth_pct),
            "core_hold_pct": int(core_hold_pct),
            "defensive_cash_pct": int(defensive_cash_pct),
        },
        "risk_controls": risk_controls,
    }


def build_hedge_advice(symbols: list[str], market: str = "AU", limit: int = 5, user_id: Optional[str] = None) -> dict:
    output_limit = max(1, min(limit, 10))
    min_score = float(os.getenv("ADVICE_MIN_SCORE", "65"))
    under_one_quota = max(0, int(os.getenv("ADVICE_UNDER_ONE_QUOTA", "2")))
    max_candidates = max(100, int(os.getenv("ADVICE_MAX_CANDIDATES", "500")))
    max_live_eval = max(0, int(os.getenv("ADVICE_MAX_LIVE_EVAL", "60")))

    candidates = _build_advice_candidates(symbols, market, max_candidates=max_candidates)
    cached_rows = _load_cached_predictions_for_today(candidates)

    ranked = []
    for symbol in candidates:
        cached = cached_rows.get(symbol)
        if cached:
            ranked.append(cached)

    live_eval_count = 0
    for symbol in candidates:
        if symbol in cached_rows:
            continue
        if live_eval_count >= max_live_eval:
            break
        try:
            ranked.append(get_probability_and_score(symbol))
            live_eval_count += 1
        except Exception:
            continue

    qualified = [row for row in ranked if float(row.get("score") or 0) >= min_score]
    qualified.sort(key=lambda row: float(row.get("score") or 0), reverse=True)

    selected = []
    if under_one_quota > 0:
        sub_one = [row for row in qualified if float(row.get("current_price") or 0) > 0 and float(row.get("current_price") or 0) < 1.0]
        selected.extend(sub_one[: min(len(sub_one), under_one_quota, output_limit)])

    used_symbols = {str(item.get("symbol") or "") for item in selected}
    for row in qualified:
        symbol = str(row.get("symbol") or "")
        if symbol in used_symbols:
            continue
        selected.append(row)
        used_symbols.add(symbol)
        if len(selected) >= output_limit:
            break

    holdings_context = get_user_execution_holdings(user_id) if user_id else []
    holdings_map = {item["symbol"]: item for item in holdings_context}

    advice_items = []
    for item in selected:
        action, reason = _recommend_action(item)
        held = holdings_map.get(item["symbol"])
        held_qty = float(held.get("quantity") or 0) if held else 0.0
        held_avg_cost = float(held.get("avg_cost") or 0) if held else 0.0
        guidance = _build_assistant_guidance(item, action)

        if held_qty > 0 and action == "BUY":
            action = "ADD"
            reason = f"Already held ({held_qty:.2f} shares). {reason}"
        elif held_qty > 0 and action == "WATCH":
            action = "HOLD"
            reason = f"Already held ({held_qty:.2f} shares). Hold bias while signal matures."

        hedge_note = "Add defensives or cash buffer" if action == "REDUCE" else "Hold unless trend weakens"
        if action in {"BUY", "ADD"}:
            hedge_note = "Consider gradual entry; keep size modest"
        elif action == "WATCH":
            hedge_note = "Wait for confirmation before sizing up"

        current_price = float(item.get("current_price") or 0)
        unrealized_pct = None
        if held_qty > 0 and held_avg_cost > 0 and current_price > 0:
            unrealized_pct = ((current_price - held_avg_cost) / held_avg_cost) * 100.0

        advice_items.append({
            "symbol": item["symbol"],
            "name": item.get("name"),
            "action": action,
            "trend": item.get("trend"),
            "score": item.get("score"),
            "prob_ge_5pct": item.get("prob_ge_5pct"),
            "expected_return_3m_pct": item.get("expected_return_3m_pct"),
            "warning_message": item.get("warning_message"),
            "warning_type": item.get("warning_type"),
            "streak_label": item.get("streak_label"),
            "reason": reason,
            "hedge_note": hedge_note,
            "entry_idea": guidance["entry_idea"],
            "next_step": guidance["next_step"],
            "invalidation": guidance["invalidation"],
            "sell_trigger": guidance["sell_trigger"],
            "review_trigger": guidance["review_trigger"],
            "held_quantity": round(held_qty, 6),
            "held_avg_cost": round(held_avg_cost, 4) if held_qty > 0 else None,
            "held_unrealized_pct": round(float(unrealized_pct), 2) if unrealized_pct is not None else None,
        })

    strategy_dashboard = _build_strategy_dashboard(
        advice_items,
        market=market,
        min_score=min_score,
        candidate_count=len(candidates),
        analyzed_count=len(ranked),
        qualified_count=len(qualified),
        under_one_selected=len([item for item in selected if float(item.get("current_price") or 0) > 0 and float(item.get("current_price") or 0) < 1.0]),
        holdings_count=len(holdings_context),
    )

    if advice_items:
        summary_parts = []
        for item in advice_items[:3]:
            summary_parts.append(f"<b>{item['symbol']}</b> {item['action']} ({item['trend']})")
        summary = (
            "<b>ASX Hedge Assistant</b><br/>"
            f"Top actions: {' | '.join(summary_parts)}<br/>"
            "Use this as decision support, not an automatic trade signal."
        )
        if holdings_context:
            top_held = ", ".join(item["symbol"] for item in holdings_context[:5])
            summary = f"{summary}<br/>Holdings tracked: {top_held}"
        summary = _merge_summary_with_strategy(summary, strategy_dashboard)
    else:
        summary = (
            "<b>ASX Hedge Assistant</b><br/>"
            f"No symbols met the minimum score threshold of {min_score:.0f} today."
        )
        summary = _merge_summary_with_strategy(summary, strategy_dashboard)

    return {
        "summary": summary,
        "items": advice_items,
        "market": (market or "AU").upper(),
        "strategy_dashboard": strategy_dashboard,
        "holdings_context": holdings_context,
        "min_score": min_score,
        "candidate_count": len(candidates),
        "analyzed_count": len(ranked),
        "qualified_count": len(qualified),
        "under_one_selected": len([item for item in selected if float(item.get("current_price") or 0) > 0 and float(item.get("current_price") or 0) < 1.0]),
    }


def build_daily_digest(market: str = "AU", limit: int = 5) -> dict:
    """Generate a concise daily digest from the highest-ranked tracked universe."""
    market_key = (market or "AU").upper()
    symbols = TOP_SYMBOLS_BY_MARKET.get(market_key, TOP_SYMBOLS_BY_MARKET["AU"])
    universe_cap = max(5, int(os.getenv("DAILY_DIGEST_UNIVERSE_CAP", "25")))
    capped_symbols = list(symbols[:universe_cap])

    today_str = datetime.utcnow().date().isoformat()
    cached_map: dict[str, dict] = {}
    if capped_symbols:
        try:
            params = {"prediction_date": today_str}
            placeholders = []
            for idx, symbol in enumerate(capped_symbols):
                key = f"s{idx}"
                params[key] = symbol
                placeholders.append(f":{key}")

            with db_conn() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT symbol, name, current_price, predicted_price, trend, score, prob_ge_5pct,
                               expected_return_pct, high_volatility_warning, warning_message,
                               warning_type, quality_reason, confidence_low, confidence_high
                        FROM daily_predictions
                        WHERE prediction_date = :prediction_date
                          AND symbol IN ({', '.join(placeholders)})
                        """
                    ),
                    params,
                ).fetchall()

            for row in rows:
                symbol = row[0]
                streak_data = get_trend_streak(symbol)
                cached_map[symbol] = {
                    "symbol": symbol,
                    "name": row[1] or symbol,
                    "current_price": float(row[2] or 0),
                    "predicted_price_3m": float(row[3] or 0),
                    "trend": row[4] or "neutral",
                    "score": float(row[5] or 0),
                    "prob_ge_5pct": float(row[6] or 0),
                    "expected_return_3m_pct": float(row[7] or 0),
                    "high_volatility_warning": bool(row[8]),
                    "warning_message": row[9],
                    "warning_type": row[10],
                    "quality_reason": row[11],
                    "confidence_low": float(row[12] or 0),
                    "confidence_high": float(row[13] or 0),
                    "streak_label": streak_data.get("streak_label"),
                }
        except Exception:
            cached_map = {}

    ranked: list[dict] = []
    for symbol in capped_symbols:
        cached = cached_map.get(symbol)
        if cached:
            ranked.append(cached)
            continue
        try:
            ranked.append(get_probability_and_score(symbol))
        except Exception:
            continue
    ranked.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
    top_items = ranked[: max(1, min(limit, 10))]

    if not top_items:
        strategy_dashboard = _build_strategy_dashboard(
            [],
            market=market_key,
            min_score=None,
            candidate_count=len(capped_symbols),
            analyzed_count=len(ranked),
            qualified_count=0,
            under_one_selected=0,
            holdings_count=0,
        )
        return {
            "market": market_key,
            "summary": _merge_summary_with_strategy(
                f"<b>ASX Daily Digest</b><br/>No ranked candidates were available for {market_key}.",
                strategy_dashboard,
            ),
            "items": [],
            "strategy_dashboard": strategy_dashboard,
            "breakdown": {"buy": [], "hold": [], "reduce": []},
        }

    buy_list = []
    hold_list = []
    reduce_list = []
    for item in top_items:
        action, reason = _recommend_action(item)
        guidance = _build_assistant_guidance(item, action)
        row = {
            "symbol": item["symbol"],
            "name": item.get("name"),
            "action": action,
            "trend": item.get("trend"),
            "score": item.get("score"),
            "expected_return_3m_pct": item.get("expected_return_3m_pct"),
            "prob_ge_5pct": item.get("prob_ge_5pct"),
            "reason": reason,
            "streak_label": item.get("streak_label"),
            "warning_message": item.get("warning_message"),
            "entry_idea": guidance["entry_idea"],
            "next_step": guidance["next_step"],
            "invalidation": guidance["invalidation"],
            "sell_trigger": guidance["sell_trigger"],
            "review_trigger": guidance["review_trigger"],
        }
        if action == "BUY":
            buy_list.append(row)
        elif action == "REDUCE":
            reduce_list.append(row)
        else:
            hold_list.append(row)

    strategy_dashboard = _build_strategy_dashboard(
        buy_list + hold_list + reduce_list,
        market=market_key,
        min_score=None,
        candidate_count=len(capped_symbols),
        analyzed_count=len(ranked),
        qualified_count=len(top_items),
        under_one_selected=len([item for item in top_items if float(item.get("current_price") or 0) > 0 and float(item.get("current_price") or 0) < 1.0]),
        holdings_count=0,
    )

    parts = ["<b>ASX Daily Digest</b>"]
    parts.append(f"Market: <b>{market_key}</b>")
    parts.append(f"Buy/Watch: {len(buy_list)} · Hold: {len(hold_list)} · Reduce: {len(reduce_list)}")
    for label, bucket in (("BUY", buy_list), ("HOLD", hold_list), ("REDUCE", reduce_list)):
        if not bucket:
            continue
        lines = [f"<b>{label}</b>"]
        for item in bucket[:3]:
            lines.append(
                f"• <b>{item['symbol']}</b> {item['trend']} | score {float(item['score'] or 0):.1f} | {item['reason']}"
            )
        parts.append("<br/>".join(lines))
    summary = _merge_summary_with_strategy("<br/><br/>".join(parts), strategy_dashboard)

    return {
        "market": market_key,
        "summary": summary,
        "items": top_items,
        "breakdown": {"buy": buy_list, "hold": hold_list, "reduce": reduce_list},
        "strategy_dashboard": strategy_dashboard,
    }


def _paper_trade_snapshot(row, current_price: float) -> dict:
    entry_price = float(row[5])
    quantity = float(row[4])
    side = (row[3] or "LONG").upper()
    direction = -1.0 if side == "SHORT" else 1.0
    
    # Calculate gross value and CommSec tier brokerage
    entry_value = entry_price * quantity
    current_value = current_price * quantity
    
    # Commsec standard: $10 under $1000, else $20
    entry_fee = 10.0 if entry_value <= 1000.0 else 20.0
    exit_fee = 10.0 if current_value <= 1000.0 else 20.0
    total_brokerage = entry_fee + exit_fee
    
    gross_pnl_value = (current_price - entry_price) * quantity * direction
    net_pnl_value = gross_pnl_value - total_brokerage
    
    invested_capital = entry_value + entry_fee
    net_pnl_pct = (net_pnl_value / invested_capital) * 100.0 if invested_capital > 0 else 0.0

    return {
        "id": row[0],
        "symbol": row[1],
        "market": row[2],
        "side": side,
        "quantity": quantity,
        "entry_price": entry_price,
        "current_price": round(current_price, 2),
        "target_price": float(row[6]) if row[6] is not None else None,
        "status": row[7],
        "signal_score": float(row[8]) if row[8] is not None else None,
        "signal_trend": row[9],
        "signal_warning": row[10],
        "notes": row[11],
        "created_at": row[12].isoformat() if row[12] else None,
        "closed_at": row[13].isoformat() if row[13] else None,
        "peak_price": float(row[14]) if row[14] is not None else None,
        "stop_loss_price": float(row[15]) if row[15] is not None else None,
        "take_profit_price": float(row[16]) if row[16] is not None else None,
        "trailing_stop_pct": float(row[17]) if row[17] is not None else 6.0,
        "review_date": row[18].isoformat() if row[18] else None,
        "last_alert_at": row[19].isoformat() if row[19] else None,
        "position_stage": row[20] or "entered",
        "recommendation_action": row[21],
        "source_reason": row[22],
        "unrealized_pnl_pct": round(net_pnl_pct, 2),
        "unrealized_pnl_value": round(net_pnl_value, 2),
        "brokerage_fees": total_brokerage,
    }


def list_paper_trades(user_id: str) -> list[dict]:
    with db_conn() as conn:
        rows = conn.execute(
            text("""
                SELECT id, symbol, market, side, quantity, entry_price, target_price, status,
                      signal_score, signal_trend, signal_warning, notes, created_at, closed_at,
                      peak_price, stop_loss_price, take_profit_price, trailing_stop_pct,
                      review_date, last_alert_at, position_stage, recommendation_action, source_reason
                FROM paper_trades
                WHERE user_id = :user_id
                ORDER BY created_at DESC
            """),
            {"user_id": user_id},
        ).fetchall()

    results = []
    for row in rows:
        symbol = row[1]
        market = row[2] or "AU"
        current_price = float(row[5])  # default to entry price (avoids hanging on yfinance)
        results.append(_paper_trade_snapshot(row, current_price))
    return results


def get_probability_and_score(symbol: str) -> dict:
    # ── Daily prediction cache ────────────────────────────────────────────────
    today_str = datetime.utcnow().date().isoformat()
    try:
        with db_conn() as conn:
            cached = conn.execute(
                text("""
                    SELECT name, current_price, predicted_price, trend, score, prob_ge_5pct,
                           expected_return_pct, high_volatility_warning, warning_message,
                           warning_type, quality_reason, score_raw, confidence_low, confidence_high
                    FROM daily_predictions
                    WHERE symbol = :symbol AND prediction_date = :date
                """),
                {"symbol": symbol, "date": today_str},
            ).fetchone()
        if cached:
            streak_data = get_trend_streak(symbol)
            return {
                "symbol": symbol,
                "name": cached[0] or symbol,
                "current_price": round(float(cached[1] or 0), 2),
                "predicted_price_3m": cached[2],
                "expected_return_3m_pct": round(float(cached[6] or 0), 2),
                "prob_ge_5pct": round(float(cached[5] or 0), 2),
                "trend": cached[3],
                "score": round(float(cached[4] or 0), 2),
                "high_volatility_warning": bool(cached[7]),
                "warning_message": cached[8],
                "warning_type": cached[9],
                "quality_reason": cached[10],
                "score_raw": round(float(cached[11] or 0), 2),
                "learning_note": cached[14] if len(cached) > 14 else None,
                **streak_data,
            }
    except Exception:
        pass  # Fall through to fresh computation
    # ─────────────────────────────────────────────────────────────────────────

    stock_data = get_stock_data(symbol)
    hist = get_historical_data(symbol, period="1y")
    if len(hist) < 120:
        raise HTTPException(status_code=404, detail=f"Not enough data for {symbol}")

    current_price = stock_data["current_price"] or float(hist["Close"].iloc[-1])
    indicators = calculate_technical_indicators(hist)
    prediction = generate_statistical_prediction(hist, current_price)

    mu = prediction["change_from_current"] / 100.0
    
    # --- Constraint Model Layer ---
    # 1. Cap returns to realistic 2-3 month cycle band (3-4% target per cycle)
    max_allowable_return = 0.06 # Max 6% — anything beyond is speculative noise for compound cycles
    adjusted_mu = max(-max_allowable_return, min(max_allowable_return, mu))

    # 2. Mean Reversion Penalty (Overbought check)
    rsi = indicators.get("rsi", 50)
    kde_rsi_prob = indicators.get("kde_rsi_prob", 0.5)
    if rsi > 70 and kde_rsi_prob >= 0.90:
        adjusted_mu -= 0.03  # Shave 3% off expected return if overbought
    elif rsi < 30:
        adjusted_mu += 0.01  # Small boost for oversold bounce potential
        
    # Apply capped return back to prediction to avoid unrealistic numbers in UI
    if adjusted_mu != mu:
        prediction["change_from_current"] = adjusted_mu * 100.0
        prediction["predicted_price"] = current_price * (1 + adjusted_mu)

    # 3. Calculate probability of ≥3% return (aligned with 3-4% per-cycle compound target)
    daily_vol = float(hist["Close"].pct_change().dropna().std())
    sigma_63 = max(daily_vol * math.sqrt(63), 1e-6)
    z = (0.03 - adjusted_mu) / sigma_63
    prob_ge_5pct = max(0.0, min(1.0, 1.0 - std_norm_cdf(z)))
    # Calibrate against empirical win rate from completed tracking windows to
    # break the circular dependency where P(≥3%) is derived from the same mu
    # that drives the composite score.
    prob_ge_5pct = _blend_empirical_prob(symbol, prob_ge_5pct)

    trend = prediction["trend"]
    trend_score = 0.9 if trend == "bullish" else 0.55 if trend == "neutral" else 0.2

    # ── HACOLT confirmation multiplier ─────────────────────────────────────
    # HACOLT (0=Strong Sell, 50=Neutral, 100=Strong Buy) acts as a
    # Heikin-Ashi trend confirmation gate on the statistical trend_score:
    #   - HACOLT confirms trend  → up to +10% boost on trend_score
    #   - HACOLT contradicts trend → up to -30% penalty on trend_score
    #   - HACOLT neutral (50)  → no change
    hacolt = indicators.get("hacolt", 50.0)
    if hacolt == 100.0:   # Strong Buy signal from Heikin-Ashi
        if trend == "bullish":
            trend_score = min(1.0, trend_score * 1.10)   # Trend confirmed
        else:
            trend_score = trend_score * 0.85             # Bullish HA, but stat says otherwise
    elif hacolt == 0.0:   # Strong Sell signal from Heikin-Ashi
        if trend == "bearish":
            trend_score = min(1.0, trend_score * 1.10)   # Bear confirmed
        else:
            trend_score = trend_score * 0.70             # HA says sell, stat says up — big conflict
    # hacolt == 50 (neutral/choppy) → no change to trend_score

    momentum = indicators.get("momentum_20", 0)
    quality_score = max(0.0, min(1.0, (1 - abs(rsi - 55) / 55) * 0.6 + (0.5 + momentum / 40) * 0.4))

    regime_fit = 0.65 if trend == "bullish" else 0.45 if trend == "neutral" else 0.3
    
    # Initialize warning flags
    high_volatility_warning = False
    warning_message = None
    warning_type = None

    avg_vol = float(hist["Volume"].tail(20).mean()) if "Volume" in hist else 0
    dollar_volume = avg_vol * current_price
    
    # Base liquidity on DOLLAR volume, not raw shares (prevents small cap bias)
    liquidity_score = max(0.1, min(1.0, dollar_volume / 5_000_000))
    
    # --- Expert Safety Net 1: The Liquidity/Slippage Trap ---
    if avg_vol > 0 and dollar_volume < 100000:
        high_volatility_warning = True
        warning_type = "liquidity_trap"
        warning_message = f"LIQUIDITY TRAP: Traded value is critically low (~${int(dollar_volume):,} / day). Extreme slippage risk."
        liquidity_score *= 0.1  # Severe penalty for untradable stocks

    # --- Volume-Price Divergence Logic ---
    current_vol = float(hist["Volume"].iloc[-1]) if "Volume" in hist else 0
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
    price_spike = (current_price - float(hist["Close"].iloc[-2])) / float(hist["Close"].iloc[-2]) if len(hist) > 1 else 0

    if price_spike > 0.05 and vol_ratio < 1.0:
        high_volatility_warning = True
        warning_type = "volume_divergence"
        warning_message = "Weak Spike Detected: Price jumped >5% on below-average volume. High risk of reversal."
        liquidity_score *= 0.5  # Penalize score for weak volume support
    elif price_spike > 0.05 and vol_ratio > 2.0:
        # Avoid the "Pump and Dump" trap: Only reward high-conviction volume spikes 
        # if the stock is highly liquid. Penny stock spikes are often manipulated.
        if dollar_volume > 2_000_000:
            quality_score = min(1.0, quality_score * 1.1)
        else:
            high_volatility_warning = True
            warning_type = "pump_risk"
            warning_message = "PUMP RISK: Extreme volume spike on low-liquidity stock. High risk of dump/reversal."
            quality_score *= 0.6  # Penalize micro-cap pumps

    # 4. Drawdown Risk Quantification (3-month window for relevance)
    hist_3m = hist.tail(63)
    peak_price = float(hist_3m["Close"].max())
    mean_price = float(hist_3m["Close"].mean())
    std_price = float(hist_3m["Close"].std())
    max_drawdown_expected = peak_price - (mean_price - 2 * std_price)
    drawdown_pct = max_drawdown_expected / current_price if current_price > 0 else 0

    drawdown_penalty = 0.0
    if drawdown_pct > 0.25:
        high_volatility_warning = True
        warning_type = "drawdown_severe"
        warning_message = f"HIGH DRAWDOWN RISK: Severe potential downside detected (~{int(drawdown_pct*100)}%)."
        drawdown_penalty = 0.20
    elif drawdown_pct > 0.15:
        if not high_volatility_warning:
            high_volatility_warning = True
            warning_type = "drawdown_moderate"
            warning_message = "MODERATE DRAWDOWN RISK: Elevated historical variance."
        drawdown_penalty = 0.08

    # 5. Beta-Adjusted Returns & Regime Detection
    # Baseline ASX200 daily volatility ~0.9%
    assumed_beta = min(3.0, daily_vol / 0.009) 
    hurdle_rate_3m = (0.04 + (assumed_beta * 0.06)) / 4.0
    if (adjusted_mu - hurdle_rate_3m) < 0:
        trend_score *= 0.5  # Penalize if it doesn't beat its CAPM risk hurdle

    sma_20 = float(hist["Close"].rolling(20).mean().iloc[-1])
    sma_50 = float(hist["Close"].rolling(min(50, len(hist))).mean().iloc[-1])
    sma_long = float(hist["Close"].rolling(min(120, len(hist))).mean().iloc[-1])
    sma_200 = float(hist["Close"].rolling(min(200, len(hist))).mean().iloc[-1])
    
    if current_price > sma_20 > sma_50 > sma_long:
        regime_fit = 0.85  # Clean trending bull regime
    elif current_price < sma_20 and current_price > sma_long:
        regime_fit = 0.40  # Choppy/Mean-reverting regime
    else:
        regime_fit = 0.30  # Bear or broken regime

    # --- Expert Safety Net 2: The Falling Knife Trap ---
    if current_price < sma_200:
        if not high_volatility_warning:
            high_volatility_warning = True
            warning_type = "falling_knife"
            warning_message = "FALLING KNIFE: Price is below the 200-day moving average. Long-term trend is structurally broken."
        regime_fit *= 0.5  # Severe penalty for catching falling knives
        prob_ge_5pct *= 0.7  # Degrade probability of success

    # --- Regime-Specific Volatility Triggers (VIX) ---
    snapshot = compute_regime_snapshot()
    vix = snapshot.get("vix_level", 20)
    vix_penalty = 0.0
    if vix > 30:
        # High volatility regime: market-wide drawdown risk
        vix_penalty = 0.15
        quality_score *= 0.8  # Deflate quality on high panic
        if not high_volatility_warning:
            high_volatility_warning = True
            warning_type = "systemic_vix"
            warning_message = f"SYSTEMIC RISK: Market fear is extremely elevated (VIX {vix}). Hit rates degrade."
    elif vix > 22:
        vix_penalty = 0.05
    elif vix < 15:
        # Low volatility regime: trends tend to persist with higher hit rates
        regime_fit = min(1.0, regime_fit * 1.1)
        prob_ge_5pct = min(1.0, prob_ge_5pct * 1.05)
    # ---------------------------------------------------

    # --- Large Cap Stability Premium ---
    # Large caps have low volatility, so they mathematically struggle to trigger
    # a 3% swing despite being excellent, safe compound-cycle setups. We add a stability premium.
    if daily_vol < 0.015 and dollar_volume > 10_000_000:
        prob_ge_5pct = min(1.0, prob_ge_5pct + 0.25) # Boost win rate for safe blue chips
        quality_score = min(1.0, quality_score * 1.20)
    elif daily_vol < 0.025 and dollar_volume > 2_000_000:
        prob_ge_5pct = min(1.0, prob_ge_5pct + 0.15) # Boost mid-caps

    raw_score = (
        0.45 * prob_ge_5pct
        + 0.20 * trend_score
        + 0.15 * quality_score
        + 0.10 * regime_fit
        + 0.10 * liquidity_score
    ) - drawdown_penalty - vix_penalty
    
    # 6. Cap maximum composite score
    score = max(0.0, min(0.92, raw_score))
    
    # 7. Minimum quality gate — flag stocks unlikely to deliver 3% in 2-3 months
    quality_reason = None
    if score < 0.45:
        if prob_ge_5pct < 0.55:
            quality_reason = f"Low probability of 3% return ({prob_ge_5pct*100:.0f}%) — need ≥55% for 2:1 R:R"
        elif drawdown_pct > 0.15:
            quality_reason = f"High drawdown risk ({drawdown_pct*100:.0f}%)"
        elif rsi > 70 and indicators.get("kde_rsi_prob", 0.5) >= 0.90:
            quality_reason = "Overbought with mean reversion risk"
        else:
            quality_reason = "Composite score below quality threshold"
    
    # 8. Self-Learning / Adaptive Penalty Logic
    learning_note = None
    try:
        with db_conn() as conn:
            avg_miss = conn.execute(
                text("""
                    SELECT AVG(actual_return_14d - target_return_14d) 
                    FROM tracking_windows 
                    WHERE symbol = :sym AND status = 'completed'
                """), 
                {"sym": symbol}
            ).fetchone()
            
            if avg_miss and avg_miss[0] is not None:
                hist_bias = float(avg_miss[0])
                if hist_bias < -0.03: 
                    score = score * 0.85
                    learning_note = f"Self-Learning: Score discounted due to historical over-optimism ({hist_bias*100:.1f}% avg miss)"
                elif hist_bias < -0.01:
                    score = score * 0.95
                    learning_note = "Self-Learning: Marginal penalty applied for historical over-prediction"
                elif hist_bias > 0.02:
                    score = min(0.95, score * 1.05)
                    learning_note = f"Self-Learning: Score boosted due to historical outperformance ({hist_bias*100:.1f}% avg beat)"
    except Exception:
        pass

    # 8. Flag severe anomalies for the user
    # Warning flags initialized early for Volume & Drawdown logic
    if not high_volatility_warning:
        if adjusted_mu > 0.05 or adjusted_mu < -0.05:
            high_volatility_warning = True
            warning_type = "extreme_projection"
            warning_message = "Extreme statistical projection detected. Guardrails applied to bound targets."
        elif rsi > 70 and indicators.get("kde_rsi_prob", 0.5) >= 0.90:
            high_volatility_warning = True
            warning_type = "overbought"
            warning_message = "Stock is highly overbought (RSI > 70 and KDE). High risk of immediate mean reversion."
    # ------------------------------

    result = {
        "symbol": symbol,
        "name": stock_data["name"],
        "current_price": round(float(current_price), 2),
        "predicted_price_3m": prediction["predicted_price"],
        "expected_return_3m_pct": round(prediction["change_from_current"], 2),
        "prob_ge_5pct": round(prob_ge_5pct * 100, 2),
        "trend": trend,
        "score": round(score * 100, 2),
        "high_volatility_warning": high_volatility_warning,
        "warning_message": warning_message,
        "warning_type": warning_type,
        "quality_reason": quality_reason,
        "score_raw": round(raw_score * 100, 2),
        "learning_note": learning_note,
    }

    # Persist to daily prediction cache (non-critical)
    try:
        with db_conn() as conn:
            conn.execute(
                text("""
                    INSERT INTO daily_predictions
                        (symbol, prediction_date, name, current_price, predicted_price, trend, score,
                         prob_ge_5pct, expected_return_pct, high_volatility_warning, warning_message,
                         warning_type, confidence_low, confidence_high, quality_reason, score_raw, learning_note)
                    VALUES
                        (:symbol, :date, :name, :current_price, :predicted_price, :trend, :score,
                         :prob_ge_5pct, :expected_return_pct, :high_volatility_warning, :warning_message,
                         :warning_type, :confidence_low, :confidence_high, :quality_reason, :score_raw)
                    ON CONFLICT (symbol, prediction_date) DO NOTHING
                """),
                {
                    "symbol": symbol,
                    "date": today_str,
                    "name": result["name"],
                    "current_price": result["current_price"],
                    "predicted_price": result["predicted_price_3m"],
                    "trend": result["trend"],
                    "score": result["score"],
                    "prob_ge_5pct": result["prob_ge_5pct"],
                    "expected_return_pct": result["expected_return_3m_pct"],
                    "high_volatility_warning": 1 if result["high_volatility_warning"] else 0,
                    "warning_message": result["warning_message"],
                    "warning_type": result["warning_type"],
                    "confidence_low": prediction.get("confidence_low"),
                    "confidence_high": prediction.get("confidence_high"),
                    "quality_reason": result["quality_reason"],
                    "score_raw": result["score_raw"],
                },
            )
    except Exception:
        pass  # Non-critical — do not fail the prediction on cache write error

    streak_data = get_trend_streak(symbol)
    result.update(streak_data)
    return result


def create_tracking_window(user_id: str, symbol: str, source: str = "manual_add") -> None:
    stock_data = get_stock_data(symbol)
    hist = get_historical_data(symbol, period="1y")
    if len(hist) == 0:
        return

    entry_price = stock_data["current_price"] or float(hist["Close"].iloc[-1])
    prediction = generate_statistical_prediction(hist, entry_price)
    # Scale the 90-day forecast proportionally to the 63-day tracking window.
    # Previously used /4.5 (a 14-day fraction of 90 days) which caused the
    # self-learning loop to train a 90-day model on 14-day outcomes — invalid.
    target_return_14d = (prediction["change_from_current"] / 100.0) * (63.0 / 90.0)
    target_price_14d = entry_price * (1 + target_return_14d)
    expected_direction = "up" if target_return_14d >= 0 else "down"

    with db_conn() as conn:
        active = conn.execute(
            text(
                """
                SELECT id FROM tracking_windows
                WHERE user_id = :user_id AND symbol = :symbol AND status = 'active'
                """
            ),
            {"user_id": user_id, "symbol": symbol},
        ).fetchone()

        if active:
            return

        start_date = datetime.utcnow()
        conn.execute(
            text(
                """
                INSERT INTO tracking_windows (
                    id, user_id, symbol, start_date, end_date, entry_price,
                    target_price_14d, target_return_14d, expected_direction,
                    tolerance_pct, status, source
                ) VALUES (
                    :id, :user_id, :symbol, :start_date, :end_date, :entry_price,
                    :target_price_14d, :target_return_14d, :expected_direction,
                    :tolerance_pct, 'active', :source
                )
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "symbol": symbol,
                "start_date": start_date,
                "end_date": start_date + timedelta(days=63),
                "entry_price": float(entry_price),
                "target_price_14d": float(target_price_14d),
                "target_return_14d": float(target_return_14d),
                "expected_direction": expected_direction,
                "tolerance_pct": 0.015,
                "source": source,
            },
        )


def parse_candidate_symbols(text_value: str, max_symbols: int = 10) -> List[str]:
    found = []
    normalized = text_value.upper()
    for symbol in ASX_COMPANIES.keys():
        if symbol in normalized and symbol not in found:
            found.append(symbol)
        if len(found) >= max_symbols:
            break
    return found


def extract_json_symbols(content: str) -> List[str]:
    content = (content or "").strip()
    if not content:
        return []

    try:
        payload = json.loads(content)
        return [s.upper().strip() for s in payload.get("symbols", []) if s]
    except Exception:
        pass

    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(content[start:end + 1])
            return [s.upper().strip() for s in payload.get("symbols", []) if s]
        except Exception:
            return []
    return []


def heuristic_symbols_from_query(query: str, max_symbols: int) -> List[str]:
    q = query.lower()
    keyword_map = {
        "bank": ["CBA", "NAB", "WBC", "ANZ"],
        "financial": ["CBA", "NAB", "WBC", "ANZ", "QBE", "MQG"],
        "mining": ["BHP", "RIO", "FMG", "S32", "MIN", "EVN", "NST"],
        "miner": ["BHP", "RIO", "FMG", "S32", "MIN", "EVN", "NST"],
        "gold": ["NST", "EVN", "GOR", "SBM", "RMS"],
        "lithium": ["PLS", "MIN", "IGO", "LTR", "SYR"],
        "energy": ["WDS", "STO", "OSH", "ORG", "APA"],
        "oil": ["WDS", "STO", "ORG", "FMG"],
        "gas": ["WDS", "STO", "ORG", "APA"],
        "retail": ["WOW", "WES", "HVN", "PMV", "JBH", "SUL"],
        "consumer": ["WOW", "WES", "COL", "WTC", "HVN"],
        "health": ["CSL", "RHC", "SHL", "RMD", "COH", "PME"],
        "tech": ["REA", "SEK", "XRO", "WTC", "TNE"],
        "telecom": ["TLS", "SPK", "TNE"],
        "property": ["GMG", "SGP", "MGR", "SCG", "ALL"],
        "infrastructure": ["TCL", "APA", "ALL", "SYD"],
        "dividend": ["TLS", "WOW", "WES", "CBA", "CSL", "BHP"],
        "growth": ["PME", "XRO", "REA", "WTC", "COH"],
        "value": ["BHP", "WBC", "ANZ", "FMG", "NAB", "TLS"],
        "defensive": ["TLS", "WOW", "WES", "CSL", "COL"],
        "defence": ["DRO", "EOS", "ASB"],
        "defense": ["DRO", "EOS", "ASB"],
        "aerospace": ["ASB", "DRO", "EOS"],
        "military": ["DRO", "EOS", "ASB"],
        "smallcap": ["DRO", "WTC", "BRN", "VUL", "CXO", "SYR"],
        "midcap": ["PME", "JBH", "MIN", "APA", "XRO", "ORG"],
        "bluechip": ["BHP", "CBA", "CSL", "WBC", "NAB", "WES"],
        "turnaround": ["FMG", "PLS", "MIN", "WDS", "IGO"],
        "uranium": ["DYL", "PDN", "BOE", "ERA"],
        "rareearth": ["LYC", "VML", "ILU"],
        "agriculture": ["GNC", "ELD", "RIC"],
    }

    picks: List[str] = []
    for keyword, symbols in keyword_map.items():
        if keyword in q:
            for symbol in symbols:
                if symbol in ASX_COMPANIES and symbol not in picks:
                    picks.append(symbol)
                if len(picks) >= max_symbols:
                    return picks

    parsed = parse_candidate_symbols(query, max_symbols=max_symbols)
    for symbol in parsed:
        if symbol not in picks:
            picks.append(symbol)
        if len(picks) >= max_symbols:
            return picks

    # Rotating cursor through the diversified universe to avoid always returning the same top shares
    global _TOP_ASX200_CURSOR, TOP_ASX200_SYMBOLS_SHUFFLED
    if not TOP_ASX200_SYMBOLS_SHUFFLED:
        random.shuffle(TOP_ASX200_SYMBOLS)
        TOP_ASX200_SYMBOLS_SHUFFLED = True
    cursor = _TOP_ASX200_CURSOR % len(TOP_ASX200_SYMBOLS)
    _TOP_ASX200_CURSOR = (cursor + max_symbols) % len(TOP_ASX200_SYMBOLS)
    for i in range(len(TOP_ASX200_SYMBOLS)):
        symbol = TOP_ASX200_SYMBOLS[(cursor + i) % len(TOP_ASX200_SYMBOLS)]
        if symbol not in picks:
            picks.append(symbol)
        if len(picks) >= max_symbols:
            break
    return picks


def domain_seed_symbols(query: str, max_symbols: int) -> List[str]:
    q = query.lower()
    seeds: List[str] = []

    if any(word in q for word in ["defence", "defense", "military", "aerospace", "aukus"]):
        seeds.extend(["DRO", "EOS", "ASB", "TLS"])

    if any(word in q for word in ["energy", "oil", "gas", "lng"]):
        seeds.extend(["WDS", "STO", "ORG", "APA"])

    if any(word in q for word in ["mining", "miner", "iron ore", "copper", "commodity"]):
        seeds.extend(["BHP", "RIO", "FMG", "S32", "MIN"])

    if any(word in q for word in ["gold", "precious metal", "bullion"]):
        seeds.extend(["NST", "EVN", "GOR", "RMS", "SBM"])

    if any(word in q for word in ["lithium", "battery metal", "rare earth", "ev battery"]):
        seeds.extend(["PLS", "MIN", "IGO", "LTR", "LYC"])

    if any(word in q for word in ["bank", "banking", "financial", "finance"]):
        seeds.extend(["CBA", "NAB", "WBC", "ANZ", "MQG", "QBE"])

    if any(word in q for word in ["health", "healthcare", "biotech", "medical"]):
        seeds.extend(["CSL", "RMD", "COH", "PME", "RHC"])

    if any(word in q for word in ["tech", "technology", "software", "saas"]):
        seeds.extend(["XRO", "WTC", "TNE", "SEK", "REA"])

    if any(word in q for word in ["property", "real estate", "reit"]):
        seeds.extend(["GMG", "SGP", "MGR", "SCG", "ALL"])

    if any(word in q for word in ["retail", "consumer", "discretionary"]):
        seeds.extend(["WOW", "WES", "COL", "HVN", "JBH"])

    if any(word in q for word in ["dividend", "income", "yield"]):
        seeds.extend(["TLS", "WOW", "WES", "BHP", "CBA", "FMG"])

    unique: List[str] = []
    for symbol in seeds:
        if symbol in ASX_COMPANIES and symbol not in unique:
            unique.append(symbol)
        if len(unique) >= max_symbols:
            break
    return unique


def align_symbols_to_query(query: str, valid_symbols: List[str], max_symbols: int) -> List[str]:
    domain_symbols = domain_seed_symbols(query, max_symbols)

    if not valid_symbols:
        if domain_symbols:
            return domain_symbols[:max_symbols]
        return heuristic_symbols_from_query(query, max_symbols=max_symbols)

    # Preserve model output but promote symbols that align with explicit domain intent.
    ordered: List[str] = []
    for symbol in domain_symbols:
        if symbol in valid_symbols and symbol not in ordered:
            ordered.append(symbol)
    for symbol in domain_symbols:
        if symbol not in ordered:
            ordered.append(symbol)
    for symbol in valid_symbols:
        if symbol not in ordered:
            ordered.append(symbol)

    return ordered[:max_symbols]


def local_completion_urls() -> List[str]:
    base = LOCAL_LLM_URL.rstrip("/")
    urls = [f"{base}/chat/completions"]

    if "/api/v1" in base:
        urls.append(f"{base.replace('/api/v1', '/v1')}/chat/completions")
    elif "/v1" in base:
        urls.append(f"{base.replace('/v1', '/api/v1')}/chat/completions")
    else:
        urls.append(f"{base}/v1/chat/completions")
        urls.append(f"{base}/api/v1/chat/completions")

    unique = []
    seen = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


async def suggest_symbols_from_ai(query: str, max_symbols: int, provider_preference: str = "auto") -> tuple[List[str], str, str]:
    prompt = (
        "Return only JSON with this schema: {\"symbols\": [\"CODE\"]}. "
        f"Pick up to {max_symbols} ASX symbols from this user ask: {query}. "
        "Use only valid ASX symbols and no commentary."
    )

    provider_errors = {"local": "", "openai": "", "groq": ""}

    def try_local_provider() -> Optional[List[str]]:
        try:
            symbols = []
            for url in local_completion_urls():
                response = requests.post(
                    url,
                    json={
                        "model": LOCAL_LLM_MODEL,
                        "messages": [
                            {"role": "system", "content": "You output strict JSON only."},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 600,
                        "temperature": 0.0,
                    },
                    timeout=60,
                )
                if response.status_code != 200:
                    continue
                content = strip_think_tags(response.json()["choices"][0]["message"]["content"])
                symbols = extract_json_symbols(content)
                if not symbols:
                    symbols = parse_candidate_symbols(content, max_symbols=max_symbols)
                if symbols:
                    break
            valid = [s for s in symbols if s in ASX_COMPANIES or re.match(r'^[A-Z0-9]{2,6}$', s)]
            return align_symbols_to_query(query, valid, max_symbols)
        except Exception as exc:
            provider_errors["local"] = str(exc)
            return None

    def try_openai_provider() -> Optional[List[str]]:
        if not openai_client:
            return None
        try:
            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You output strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=600,
                temperature=0.0,
            )
            content = strip_think_tags(response.choices[0].message.content or "{}")
            symbols = extract_json_symbols(content)
            if not symbols:
                symbols = parse_candidate_symbols(content, max_symbols=max_symbols)
            valid = [s for s in symbols if s in ASX_COMPANIES or re.match(r'^[A-Z0-9]{2,6}$', s)]
            return align_symbols_to_query(query, valid, max_symbols)
        except Exception as exc:
            provider_errors["openai"] = str(exc)
            return None

    provider_preference = (provider_preference or "auto").strip().lower()
    strict_provider = provider_preference in {"local", "openai"}

    if strict_provider:
        provider_order = [provider_preference]
    else:
        provider_order = LLM_PROVIDER_ORDER

    def try_groq_suggest() -> Optional[List[str]]:
        if not groq_client:
            return None
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You output strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=300,
                temperature=0.0,
            )
            content = strip_think_tags(response.choices[0].message.content or "{}")
            symbols = extract_json_symbols(content)
            if not symbols:
                symbols = parse_candidate_symbols(content, max_symbols=max_symbols)
            valid = [s for s in symbols if s in ASX_COMPANIES or re.match(r'^[A-Z0-9]{2,6}$', s)]
            return align_symbols_to_query(query, valid, max_symbols)
        except Exception as exc:
            provider_errors["groq"] = str(exc)
            return None

    for provider in provider_order:
        if provider == "local":
            result = try_local_provider()
            if result:
                return result, "local", ""
        if provider == "groq":
            result = try_groq_suggest()
            if result:
                return result, "groq", ""
        if provider == "openai":
            result = try_openai_provider()
            if result:
                return result, "openai", ""

    if strict_provider:
        return [], provider_preference, provider_errors.get(provider_preference, "")

    return heuristic_symbols_from_query(query, max_symbols=max_symbols), "fallback", ""

def generate_statistical_prediction(df: pd.DataFrame, current_price: float, sector: str = "", symbol: str = "") -> dict:
    """Generate statistical prediction using robust short-term methods (14-day rolling horizon)."""

    future_days = 14 # Shortened to 14 days to minimize forecast noise
    close = df['Close'].values
    model_count = 0
    
    # Kalman Filter for smoothing prices
    try:
        from pykalman import KalmanFilter
        kf = KalmanFilter(transition_matrices=[1], observation_matrices=[1], initial_state_mean=close[0], initial_state_covariance=1, observation_covariance=1, transition_covariance=0.01)
        state_means, _ = kf.filter(close)
        smoothed_close = state_means.flatten()
    except Exception:
        smoothed_close = close

    # Method 1: Linear trend projection (short-term window only)
    short_window_len = min(future_days * 2, len(smoothed_close))
    short_close = smoothed_close[-short_window_len:]
    X = np.arange(len(short_close)).reshape(-1, 1)
    from sklearn.linear_model import LinearRegression
    lr = LinearRegression()
    lr.fit(X, short_close)
    future_X = np.arange(len(short_close), len(short_close) + future_days).reshape(-1, 1)
    lr_pred = float(lr.predict(future_X)[-1])
    
    # Clamp extreme linear projections to realistic bounds
    if abs(lr_pred - current_price) / max(current_price, 1e-6) > 0.15:
        lr_pred = current_price * (1.15 if lr_pred > current_price else 0.85)
    model_count += 1

    # Method 2: SMA projection (always available)
    sma_20 = float(df['Close'].rolling(20).mean().iloc[-1]) if len(df) >= 20 else float(np.mean(close))
    model_count += 1

    # Method 3: Exponential smoothing
    weights = np.linspace(1, 2, min(14, len(df)))
    weighted_avg = float(np.average(df['Close'].tail(len(weights)), weights=weights))
    model_count += 1
    
    # 14-day target blending
    combined_pred = lr_pred * 0.30 + sma_20 * 0.30 + weighted_avg * 0.40

    # Ensemble disagreement metric
    preds = [lr_pred, sma_20, weighted_avg]
    ensemble_std = float(np.std(preds))
    ensemble_disagreement = ensemble_std / current_price if current_price > 0 else 0

    # Apply Macroeconomic Top-Down Adjustments
    try:
        macro_adj = macro_model.calculate_macro_adjustment(sector, _get_macro_data_cached())
    except Exception:
        macro_adj = 1.0

    combined_pred = combined_pred * macro_adj

    # Confidence interval based on volatility and disagreement
    try:
        volatility = float(pd.Series(close).pct_change().dropna().std())
        if np.isnan(volatility) or np.isinf(volatility):
            volatility = 0.02
    except Exception:
        volatility = 0.02
        
    if np.isnan(combined_pred) or np.isinf(combined_pred):
        combined_pred = current_price
        
    confidence_margin = combined_pred * (volatility + ensemble_disagreement) * 2

    # Determine trend — thresholds aligned for 3-4% compound cycle targets
    if combined_pred > current_price * 1.03:
        trend = "bullish"
    elif combined_pred < current_price * 0.97:
        trend = "bearish"
    else:
        trend = "neutral"

    return {
        "predicted_price": round(float(combined_pred), 2),
        "confidence_low": round(float(combined_pred - confidence_margin), 2),
        "confidence_high": round(float(combined_pred + confidence_margin), 2),
        "trend": trend,
        "change_from_current": round(float(((combined_pred - current_price) / max(current_price, 1e-6) * 100)), 2),
        "model_count": model_count,
        "ensemble_disagreement": round(ensemble_disagreement, 4),
        "arima_pred": None,
        "xgb_pred": None,
    }

def get_recent_news(symbol: str, market: str) -> list:
    """Fetch recent news from EODHD to include in analysis."""
    if not EODHD_API_KEY:
        return []
    try:
        _eodhd_rate_limit()
        ticker = format_ticker(symbol, market).replace(".AX", ".AU")
        url = f"https://eodhd.com/api/news"
        r = requests.get(url, params={"s": ticker, "api_token": EODHD_API_KEY, "limit": 4, "offset": 0}, timeout=5)
        if r.status_code != 200:
            return []
        data = r.json()
        news_items = []
        for item in data:
            title = item.get("title", "")
            sentiment = item.get("sentiment", {})
            polarity = sentiment.get("polarity", 0)
            if title:
                news_items.append({"title": title, "polarity": polarity})
        return news_items
    except Exception as e:
        print(f"[EODHD News] Error fetching news for {symbol}: {e}")
        return []


async def get_llm_analysis(
    symbol: str, 
    stock_data: dict, 
    indicators: dict, 
    prediction: dict,
    valuation: dict = None,
    risk: dict = None,
    regime: dict = None,
    historical_accuracy: dict = None
) -> str:
    """Two-stage LLM analysis pipeline.

    Stage 1 (local DeepSeek 7B): raw data → compact structured JSON.
    Stage 2 (Groq Llama 70B): structured JSON → broker-grade narrative.

    Falls back to Groq/OpenAI with full prompt if Stage 1 is unavailable,
    and to mock analysis if no LLM is reachable.
    """

    # Default values for optional params
    if valuation is None:
        valuation = {}
    if risk is None:
        risk = {}
    if regime is None:
        regime = {}
    if historical_accuracy is None:
        historical_accuracy = {}

    # Format market cap nicely
    market_cap = valuation.get('market_cap')
    if market_cap:
        if market_cap > 1e12:
            market_cap_str = f"${market_cap/1e12:.2f}T"
        elif market_cap > 1e9:
            market_cap_str = f"${market_cap/1e9:.2f}B"
        else:
            market_cap_str = f"${market_cap/1e6:.2f}M"
    else:
        market_cap_str = "N/A"

    # Full raw prompt — used by Stage 1 and as fallback for cloud providers
    model_note = ""
    if prediction.get("model_count", 1) >= 3:
        notes = []
        if prediction.get("arima_pred") is not None:
            notes.append(f"ARIMA=${prediction['arima_pred']:.2f}")
        if prediction.get("xgb_pred") is not None:
            notes.append(f"XGBoost=${prediction['xgb_pred']:.2f}")
        if notes:
            model_note = f"\n- Individual model forecasts: {', '.join(notes)}"

    # Try to insert latest macro snapshot into prompt
    macro_notes = ""
    try:
        if macro_model:
            md = _get_macro_data_cached()
            if md:
                au_yield_str = f"- AU 10-Yr Bond Yield: {md.get('au_10y_yield', {}).get('current', 0.0):.2f}% (Trend: {md.get('au_10y_yield', {}).get('trend_30d', 0):.1f}%)\n" if md.get('au_10y_yield', {}).get('current', 0) > 0 else ""
                macro_notes = f"\nMacroeconomic Snapshot:\n- ASX200 (Market): {md.get('asx200', {}).get('trend_30d', 0):.2f}% (30d)\n- AUD/USD: {md.get('aud_usd', {}).get('current', 0):.4f}\n{au_yield_str}- Copper: {md.get('copper', {}).get('trend_30d', 0):.1f}% (30d)\n- Gold: {md.get('gold', {}).get('trend_30d', 0):.1f}% (30d)\n- Crude Oil: {md.get('crude_oil', {}).get('trend_30d', 0):.1f}% (30d)\n"
    except Exception:
        pass

    # ── Analyst consensus + catalyst block ────────────────────────────────────
    analyst_consensus_block = ""
    if valuation.get('analyst_target_mean') or valuation.get('analyst_recommendation'):
        rec = (valuation.get('analyst_recommendation') or 'N/A').upper()
        n_opinions = valuation.get('num_analyst_opinions') or 'N/A'
        t_mean  = f"${valuation['analyst_target_mean']:.2f}" if valuation.get('analyst_target_mean') else 'N/A'
        t_high  = f"${valuation['analyst_target_high']:.2f}" if valuation.get('analyst_target_high') else 'N/A'
        t_low   = f"${valuation['analyst_target_low']:.2f}"  if valuation.get('analyst_target_low')  else 'N/A'
        upside  = f"{valuation['analyst_upside_pct']:+.1f}%" if valuation.get('analyst_upside_pct') is not None else 'N/A'
        analyst_consensus_block = f"""
Analyst Consensus ({n_opinions} analysts):
- Recommendation: {rec}
- Consensus Price Target: {t_mean} (range {t_low} – {t_high})
- Implied Upside/Downside vs Current: {upside}"""

    catalyst_block = ""
    earnings_warning = ""
    days_to_e = valuation.get('days_to_earnings')
    if valuation.get('next_earnings_date') or valuation.get('short_pct_float') or valuation.get('pct_from_52w_high') is not None:
        dte_str = f"{days_to_e}d" if days_to_e is not None else 'N/A'
        short_str = f"{valuation['short_pct_float']:.1f}%" if valuation.get('short_pct_float') else 'N/A'
        high_52_str = f"${valuation['52w_high']:.2f}" if valuation.get('52w_high') else 'N/A'
        low_52_str  = f"${valuation['52w_low']:.2f}"  if valuation.get('52w_low')  else 'N/A'
        from_peak_str = f"{valuation['pct_from_52w_high']:+.1f}% from 52-week high" if valuation.get('pct_from_52w_high') is not None else 'N/A'
        eps_growth_str = f"{valuation['eps_growth_fwd_pct']:+.1f}%" if valuation.get('eps_growth_fwd_pct') is not None else 'N/A'
        rev_growth_str = f"{valuation['revenue_growth']*100:+.1f}%" if valuation.get('revenue_growth') else 'N/A'
        catalyst_block = f"""
Catalyst & Market Structure:
- Next Earnings Date: {valuation.get('next_earnings_date', 'N/A')} ({dte_str} away)
- 52-Week Range: {low_52_str} – {high_52_str} | Position: {from_peak_str}
- Short Interest (% float): {short_str}
- EPS (Trailing / Forward): ${valuation.get('trailing_eps', 'N/A')} / ${valuation.get('forward_eps', 'N/A')} | Fwd EPS Growth: {eps_growth_str}
- Revenue Growth (YoY): {rev_growth_str}"""
        if days_to_e is not None and 0 <= days_to_e <= 10:
            earnings_warning = f"\n⚠️  EARNINGS IN {days_to_e} DAYS — elevated event risk. Avoid new entry; existing positions consider protective stops."
        elif days_to_e is not None and 0 < days_to_e <= 30:
            earnings_warning = f"\n📅  Earnings in {days_to_e} days — watch for analyst estimate revisions pre-result."

    # Fetch recent news
    market = detect_market(symbol)
    news_items = get_recent_news(symbol, market)
    news_block = ""
    if news_items:
        news_lines = "\n".join([f"- {n['title']} (Sentiment Polarity: {n['polarity']:.2f})" for n in news_items])
        news_block = f"\nRecent Financial News Sentiment:\n{news_lines}\n"

    full_prompt = f"""You are a financial data analyst.
You do not give financial advice.
You only analyse data, identify patterns, compare companies, and explain market behaviour.

Stock: {symbol}
Sector: {valuation.get('sector', 'N/A')}
Industry: {valuation.get('industry', 'N/A')}{macro_notes}

Current Price: ${stock_data['current_price']:.2f}
Daily Change: {stock_data['change_percent']:.2f}%
{earnings_warning}
Technical Indicators:
- SMA 20: ${indicators.get('sma_20', 0):.2f}
- SMA 50: ${indicators.get('sma_50', 0):.2f}
- SMA 200: ${indicators.get('sma_200', 0):.2f}
- RSI (14): {indicators.get('rsi', 0):.1f}
- HACOLT Trend: {indicators.get('hacolt', 50):.1f} (0=Sell, 50=Neutral, 100=Buy)
- MACD: {indicators.get('macd', 0):.2f}
- Volatility (annualized): {indicators.get('volatility', 0)*100:.1f}%
- 20-day Momentum: {indicators.get('momentum_20', 0):.1f}%

Valuation:
- P/E: {valuation.get('pe', 'N/A')}
- Forward P/E: {valuation.get('forward_pe', 'N/A')}
- PEG: {valuation.get('peg', 'N/A')}
- Price-to-Book: {valuation.get('pb', 'N/A')}
- Dividend Yield: {valuation.get('dividend_yield', 'N/A')}
- Market Cap: {market_cap_str}
{analyst_consensus_block}
{catalyst_block}
{news_block}

Risk Metrics:
- Beta (vs ASX200): {risk.get('beta', 'N/A')}
- Max Drawdown (90d): {risk.get('max_drawdown_90d', 'N/A')}%
- Sharpe Ratio (90d): {risk.get('sharpe_90d', 'N/A')}

Forecast (90 days, {prediction.get('model_count', 1)}-model ensemble):
- Predicted Price: ${prediction['predicted_price']:.2f}
- Range: ${prediction['confidence_low']:.2f} - ${prediction['confidence_high']:.2f}
- Trend: {prediction['trend'].upper()}
- Expected Change: {prediction['change_from_current']:.1f}%{model_note}

Model Confidence:
- Probability of ≥3%: {prediction.get('prob_ge_5pct', 'N/A')}%
- Composite Score: {prediction.get('score', 'N/A')}

Market Regime:
- Regime: {regime.get('name', 'N/A')}
- Confidence: {regime.get('confidence', 'N/A')}%

Historical Forecast Accuracy:
- Directional Accuracy: {historical_accuracy.get('directional_accuracy', 'N/A')}%
- Average Error: {historical_accuracy.get('avg_error', 'N/A')}%

Task:
Explain the stock's outlook based on the above data. Cover: (1) technical setup and momentum, (2) valuation relative to analyst targets and own history, (3) upcoming catalyst risk (earnings proximity, short interest), (4) macro regime alignment, (5) key risks and watch points.
Do not give buy/sell recommendations."""

    # Stage 1 prompt — asks DeepSeek 7B to distil data into structured JSON
    stage1_prompt = f"""You are a quantitative data parser. Extract and summarise the following stock data as compact JSON only.
Output ONLY valid JSON with these exact keys — no explanation, no markdown, no extra text:

{{
  "symbol": "<ticker>",
  "trend_bias": "<bullish|bearish|neutral>",
  "rsi_signal": "<overbought|oversold|neutral>",
  "macd_signal": "<bullish|bearish|neutral>",
  "price_vs_sma50": "<above|below>",
  "price_vs_sma200": "<above|below>",
  "volatility_level": "<low|moderate|high>",
  "valuation_stance": "<expensive|fair|cheap|unknown>",
  "analyst_vs_price": "<above_target|at_target|below_target|no_data>",
  "earnings_risk": "<high|moderate|low|none|unknown>",
  "entry_zone": "<clear|caution|avoid>",
  "regime": "<risk_on|risk_off|liquidity_rally|mixed>",
  "regime_fit": "<aligned|misaligned|neutral>",
  "prediction_confidence": "<high|medium|low>",
  "ensemble_models": <number of models used>,
  "arima_agrees": <true|false|null>,
  "xgb_agrees": <true|false|null>,
  "key_risks": ["<risk1>", "<risk2>"],
  "key_positives": ["<positive1>", "<positive2>"],
  "catalyst_summary": "<one sentence on nearest catalyst or earnings risk>"
}}

--- RAW DATA ---
{full_prompt}"""

    # ── Stage 1: Local DeepSeek 7B → structured JSON ──────────────────────
    def try_stage1_local() -> Optional[dict]:
        try:
            response = requests.post(
                f"{LOCAL_LLM_URL}/chat/completions",
                json={
                    "model": LOCAL_LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a JSON-only data extraction tool. Output valid JSON and nothing else."},
                        {"role": "user", "content": stage1_prompt}
                    ],
                    "max_tokens": 500,
                    "temperature": 0.1,
                },
                timeout=60,
            )
            if response.status_code == 200:
                raw = strip_think_tags(response.json()['choices'][0]['message']['content'])
                return extract_json_from_llm(raw)
            return None
        except Exception:
            return None

    # ── Stage 2: Groq Llama 3.3 70B → broker-grade narrative ──────────────
    def try_groq_provider(structured: Optional[dict] = None) -> Optional[str]:
        if not groq_client:
            return None
        try:
            if structured:
                stage2_prompt = f"""You are a senior equity research analyst writing a concise 3-4 paragraph stock outlook for a broker platform.
Do not give buy/sell recommendations. Write in clear professional prose.

Use this pre-processed signal summary to write the outlook:
{json.dumps(structured, indent=2)}

Reference the key signals (trend, RSI, MACD, valuation stance, regime fit, model ensemble count, ARIMA and XGBoost agreement where available).
Cover: (1) price momentum and technical setup, (2) valuation and fundamental context, (3) macro/regime alignment, (4) top risks and watch points."""
            else:
                stage2_prompt = full_prompt + "\n\nWrite a 3-4 paragraph broker-grade outlook covering momentum, valuation, macro alignment, and key risks."

            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are a senior equity research analyst. Write concise, data-driven stock outlooks."},
                    {"role": "user", "content": stage2_prompt}
                ],
                max_tokens=1200,
                temperature=0.4,
            )
            return strip_think_tags(response.choices[0].message.content)
        except Exception:
            return None

    # ── OpenAI fallback (full prompt, existing behaviour) ─────────────────
    def try_openai_provider() -> Optional[str]:
        if not openai_client:
            return None
        try:
            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a professional stock analyst providing investment insights."},
                    {"role": "user", "content": full_prompt}
                ],
                max_tokens=800,
                temperature=0.3,
            )
            return strip_think_tags(response.choices[0].message.content)
        except Exception:
            return None

    # ── Orchestration: Groq primary → local fallback → OpenAI last resort ──
    
    # Stage 1: Groq does the full analysis (128K context, fast inference)
    structured_json: Optional[dict] = None
    if "groq" in LLM_PROVIDER_ORDER:
        content = await asyncio.to_thread(try_groq_provider, None)
        if content:
            return content

    # Stage 2: Local produces structured JSON, then Groq formats it as prose
    if "local" in LLM_PROVIDER_ORDER:
        structured_json = await asyncio.to_thread(try_stage1_local)

    # Groq Stage 2 retry — pass structured JSON if Stage 1 succeeded
    if structured_json and groq_client:
        content = await asyncio.to_thread(try_groq_provider, structured_json)
        if content:
            return content

    # OpenAI fallback
    if "openai" in LLM_PROVIDER_ORDER:
        content = await asyncio.to_thread(try_openai_provider)
        if content:
            return content

    # Last resort: if local is the only provider and Stage 1 produced something,
    # return a plain-text rendering of the JSON
    if structured_json:
        trend = structured_json.get("trend_bias", prediction["trend"])
        risks = ", ".join(structured_json.get("key_risks", []))
        positives = ", ".join(structured_json.get("key_positives", []))
        return (f"{symbol} shows a {trend} outlook. "
                f"RSI is {structured_json.get('rsi_signal', 'neutral')}, MACD is {structured_json.get('macd_signal', 'neutral')}. "
                f"Key positives: {positives or 'N/A'}. Key risks: {risks or 'N/A'}.")

    # Mock analysis if no LLM available
    rsi_val = indicators.get('rsi')
    rsi_str = f"{rsi_val:.1f}" if isinstance(rsi_val, (int, float)) and not math.isnan(rsi_val) else "N/A"
    rsi_cond = ('overbought' if isinstance(rsi_val, (int, float)) and rsi_val > 70
                else 'oversold' if isinstance(rsi_val, (int, float)) and rsi_val < 30
                else 'neutral')
    sma50 = indicators.get('sma_50') or 0
    return (f"Based on technical analysis, {symbol} shows a {prediction['trend']} trend. "
            f"Current RSI is {rsi_str} indicating {rsi_cond} conditions. "
            f"The stock is trading {'above' if stock_data['current_price'] > sma50 else 'below'} "
            f"its 50-day moving average.")


def get_weekly_data(symbol: str) -> List[dict]:
    """Get last 7-14 days of data with actual model predictions for each day"""
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    market = detect_market(s)
    stock = yf.Ticker(format_ticker(s, market))
    
    # Get last 30 days to have enough historical context
    hist = stock.history(period="30d")
    
    if len(hist) < 8:
        # Fallback if not enough data
        hist = stock.history(period="1y")
    
    weekly_data = []
    
    # Get only last 14 days for display
    recent_hist = hist.tail(14)
    
    for i in range(len(recent_hist)):
        # Get data up to this point (not including future data)
        data_up_to_now = hist.iloc[:hist.index.get_loc(recent_hist.index[i])+1]
        
        if len(data_up_to_now) < 5:
            continue
        
        # Generate prediction using the same model as main analysis
        # Using only historical data available at that time
        X = np.arange(len(data_up_to_now)).reshape(-1, 1)
        y = data_up_to_now['Close'].values
        
        from sklearn.linear_model import LinearRegression
        lr = LinearRegression()
        lr.fit(X, y)
        
        # Predict next ~14 days
        future_days = 14
        future_X = np.array([[len(data_up_to_now) + future_days - 1]])
        lr_pred = lr.predict(future_X)[0]
        
        # SMA method
        sma_50 = data_up_to_now['Close'].rolling(50).mean().iloc[-1]
        sma_50 = sma_50 if not np.isnan(sma_50) else data_up_to_now['Close'].iloc[-1]
        
        # Weighted average (recent prices weighted higher)
        weights = np.linspace(1, 2, min(50, len(data_up_to_now)))
        weighted_avg = np.average(data_up_to_now['Close'].tail(50), weights=weights)
        
        # Combined prediction (same weights as main model)
        pred_price = (lr_pred * 0.4 + sma_50 * 0.3 + weighted_avg * 0.3)
        
        actual_price = data_up_to_now['Close'].iloc[-1]
        date = recent_hist.index[i].strftime("%Y-%m-%d")
        
        weekly_data.append({
            "date": date,
            "actual_price": round(actual_price, 2),
            "predicted_price": round(pred_price, 2)
        })
    
    return weekly_data


def get_daily_return(ticker: str) -> float:
    try:
        period_map = {"^AXJO": ("AXJO", "AU"), "^GSPC": ("GSPC", "US"),
                       "^NSEI": ("NSEI", "IN"), "^BSESN": ("BSESN", "IN"),
                       "GC=F": ("GC=F", "COMMODITY"), "DX-Y.NYB": ("DX-Y.NYB", "COMMODITY")}
        if ticker in period_map:
            sym, mkt = period_map[ticker]
            df = _eodhd_historical_data(sym, "5d", mkt)
            if df.empty:
                return 0.0
        else:
            return 0.0
        if len(df) < 2:
            return 0.0
        prev_close = float(df["Close"].iloc[-2])
        last_close = float(df["Close"].iloc[-1])
        if prev_close == 0 or math.isnan(prev_close) or math.isnan(last_close):
            return 0.0
        return ((last_close - prev_close) / prev_close) * 100.0
    except Exception:
        return 0.0


_regime_snapshot_cache: dict = {"data": None, "expires": 0.0}


def compute_regime_snapshot() -> dict:
    now = time.monotonic()
    if _regime_snapshot_cache["data"] is not None and now < _regime_snapshot_cache["expires"]:
        return _regime_snapshot_cache["data"]

    asx200_ret = get_daily_return("^AXJO")
    sp500_ret = get_daily_return("^GSPC")
    nifty_ret = get_daily_return("^NSEI")
    sensex_ret = get_daily_return("^BSESN")
    gold_ret = get_daily_return("GC=F")
    dxy_ret = get_daily_return("DX-Y.NYB")
    
    try:
        vix_df = _eodhd_historical_data("VIX", "5d", "US")
        vix_level = float(vix_df["Close"].iloc[-1]) if not vix_df.empty else 20.0
    except:
        vix_level = 20.0

    # Bear market detection: 200-day SMA check on ASX200 via EODHD
    bear_market = False
    try:
        asx200_hist = _eodhd_historical_data("AXJO", "1y", "AU")
        if not asx200_hist.empty and len(asx200_hist) >= 200:
            sma_200 = float(asx200_hist["Close"].rolling(200).mean().iloc[-1])
            current_asx = float(asx200_hist["Close"].iloc[-1])
            bear_market = current_asx < sma_200
    except Exception:
        pass

    equities_ret = asx200_ret
    safe_haven_flag = equities_ret < 0 and gold_ret > 0
    usd_headwind_flag = dxy_ret > 0.4
    divergence_flag = equities_ret > 0 and gold_ret > 0

    if safe_haven_flag:
        regime = "risk_off"
    elif bear_market:
        regime = "bear_market"
    elif divergence_flag:
        regime = "liquidity_rally"
    elif equities_ret > 0:
        regime = "risk_on"
    else:
        regime = "mixed"

    confidence = min(100.0, abs(equities_ret) * 30 + abs(gold_ret) * 30 + abs(dxy_ret) * 20)

    tags = []
    if safe_haven_flag:
        tags.append("safe_haven_risk_off")
    if usd_headwind_flag:
        tags.append("usd_headwind_exporters")
    if divergence_flag:
        tags.append("liquidity_rally_divergence")

    result = {
        "asx200_ret": round(asx200_ret, 2),
        "sp500_ret": round(sp500_ret, 2),
        "nifty_ret": round(nifty_ret, 2),
        "sensex_ret": round(sensex_ret, 2),
        "gold_ret": round(gold_ret, 2),
        "dxy_ret": round(dxy_ret, 2),
        "vix_level": round(vix_level, 2),
        "regime": regime,
        "confidence": round(confidence, 2),
        "safe_haven_flag": safe_haven_flag,
        "usd_headwind_flag": usd_headwind_flag,
        "divergence_flag": divergence_flag,
        "bear_market": bear_market,
        "tags": tags,
    }
    _regime_snapshot_cache["data"] = result
    _regime_snapshot_cache["expires"] = now + 300  # cache for 5 minutes
    return result


def regime_summary_text(snapshot: dict) -> str:
    if snapshot["regime"] == "risk_off":
        return "Risk-off conditions detected: equities are soft while gold is bid. Prioritize defensive risk management."
    if snapshot["regime"] == "liquidity_rally":
        return "Equities and gold rising together suggests a liquidity-driven rally. Treat momentum as opportunistic, not purely fundamental."
    if snapshot["regime"] == "risk_on":
        return "Risk-on tone with equities advancing. Trend-following setups may remain favored if volatility stays contained."
    return "Mixed market regime. Keep position sizing moderate and wait for cleaner directional confirmation."

# API Routes

@app.get("/")
async def root():
    return {"message": "ASX Stock Predictor API", "version": "1.0.0"}

@app.get("/api/health")
async def health_check():
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db_ok = False

    # ── Scheduled task freshness ──────────────────────────────────────────────
    tz = _get_scheduler_timezone()
    now_local = datetime.now(tz)
    now_utc = datetime.utcnow()

    scan_age_h = None
    digest_sent_today = False
    weekly_age_d = None
    try:
        with db_conn() as _c:
            _sr = _c.execute(text(
                "SELECT generated_at FROM wealth_scan_cache ORDER BY generated_at DESC LIMIT 1"
            )).fetchone()
            if _sr and _sr[0]:
                _g = _sr[0] if isinstance(_sr[0], datetime) else datetime.fromisoformat(str(_sr[0]))
                scan_age_h = round((now_utc - _g.replace(tzinfo=None)).total_seconds() / 3600, 1)

            _dk = _daily_digest_cache_key()
            _dr = _c.execute(text(
                "SELECT COUNT(*) FROM telegram_send_log WHERE message_type='daily_digest' AND digest_key=:dk AND status='sent'"
            ), {"dk": _dk}).fetchone()
            digest_sent_today = (_dr[0] if _dr else 0) > 0

            _wr = _c.execute(text("SELECT MAX(generated_at) FROM weekly_digests")).fetchone()
            if _wr and _wr[0]:
                _wg = _wr[0] if isinstance(_wr[0], datetime) else datetime.fromisoformat(str(_wr[0]))
                weekly_age_d = round((now_utc - _wg.replace(tzinfo=None)).total_seconds() / 86400, 1)
    except Exception:
        pass

    from datetime import time as _t
    is_market_hours = (_t(10, 0) <= now_local.time() <= _t(16, 0)) and (now_local.weekday() < 5)

    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "database_url": DATABASE_URL.split("@")[-1],
        "llm_provider_order": LLM_PROVIDER_ORDER,
        "local_llm_configured": bool(LOCAL_LLM_URL),
        "openai_configured": bool(openai_client),
        "openai_model": OPENAI_MODEL,
        "auth_enabled": True,
        "scheduler": {
            "broad_scan_age_hours": scan_age_h,
            "broad_scan_fresh": scan_age_h is not None and scan_age_h < 20,
            "daily_digest_sent_today": digest_sent_today,
            "weekly_picks_age_days": weekly_age_d,
            "market_hours_active": is_market_hours,
            "local_time": now_local.strftime("%a %Y-%m-%d %H:%M %Z"),
        },
    }


@app.get("/api/smsf/dashboard")
async def smsf_dashboard(current_user: dict = Depends(get_current_user)):
    """SMSF v2 Driver Dashboard — consolidated view of strategy, positions, risk, and pipeline state."""
    uid = current_user["id"]
    result = {
        "portfolio": {"value": 0, "cash": 0, "pnl_pct": 0, "open_count": 0, "closed_count": 0},
        "circuit_breaker": {"level": "NORMAL", "drawdown_pct": 0, "peak_value": 0, "description": "Normal"},
        "calendar": {"signal": "NEUTRAL", "emoji": "📅", "score_multiplier": 1.0, "allow_new": True, "max_new": 12},
        "model": {"target": "hit_8pct_before_m8pct", "feature_count": 62, "weights_age_hours": 0},
        "regime": {"regime": "NEUTRAL", "alert": "Normal regime", "color": "#1a2235", "sector_weights": {}},
        "cgt_alerts": [],
        "positions": [],
        "announcements": [],
        "backfill_complete": True,
        "data_from": "2017",
        "data_to": "2026",
    }

    try:
        # Portfolio
        paper = list_paper_trades(uid)
        open_positions = [p for p in paper if p.get("status") == "open"]
        closed_positions = [p for p in paper if p.get("status") == "closed"]

        portfolio_state = _compute_portfolio_state(uid)
        starting_capital = portfolio_state["starting_capital"]
        cash = portfolio_state["available_cash"]
        total_value = portfolio_state["total_equity"]
        pnl_pct = (total_value / starting_capital - 1) * 100 if starting_capital > 0 else 0

        result["portfolio"] = {
            "value": round(total_value, 0), "cash": round(cash, 0),
            "pnl_pct": round(pnl_pct, 1),
            "open_count": len(open_positions), "closed_count": len(closed_positions),
        }

        # Circuit breaker
        from circuit_breaker import DrawdownCircuitBreaker, get_peak_value
        breaker = DrawdownCircuitBreaker(peak_value=max(get_peak_value(), starting_capital))
        breaker_state = breaker.check(total_value)
        result["circuit_breaker"] = {
            "level": breaker_state["level"], "drawdown_pct": breaker_state.get("drawdown_pct", 0),
            "peak_value": breaker_state.get("peak_value", total_value),
            "description": breaker_state.get("description", ""),
        }

        # Calendar gate
        from calendar_gate import get_calendar_status
        cal = get_calendar_status(wfo_state=get_current_wfo_state()["state"])
        result["calendar"] = {
            "signal": cal.get("calendar_signal", "NEUTRAL"),
            "emoji": {"STRONG": "🟢", "MODERATE": "🔵", "NEUTRAL": "⚪", "CAUTION": "🟡", "AVOID": "🔴", "SUPPRESSED": "⛔"}.get(cal.get("calendar_signal", ""), "📅"),
            "score_multiplier": cal.get("score_multiplier", 1.0),
            "allow_new": cal.get("allow_new_entries", True),
            "max_new": cal.get("max_new_positions", 12),
        }

        # Model
        try:
            with db_conn() as c:
                latest = c.execute(text("SELECT MAX(trained_at), LEFT(notes, 200) FROM model_weights_by_date WHERE model_type='pathaware_v2' OR model_type='ridge'")).fetchone()
                if latest and latest[0]:
                    age_h = (datetime.utcnow() - latest[0].replace(tzinfo=None) if hasattr(latest[0], 'replace') else 24).total_seconds() / 3600
                    result["model"]["weights_age_hours"] = round(max(0, age_h) if not isinstance(age_h, complex) else 24, 1)
                    result["model"]["feature_count"] = 62
        except: pass

        # Macro regime
        try:
            from macro_rotation import classify_regime
            regime = classify_regime({})
            result["regime"] = {
                "regime": regime.get("regime", "NEUTRAL"),
                "alert": regime.get("alert", ""),
                "color": {"RISK_ON_COMMODITY": "#2d5016", "RISK_ON_GROWTH": "#1a3a5c", "RISK_OFF": "#5c1a1a", "CARRY_UNWIND": "#5c4a1a"}.get(regime.get("regime", ""), "#1a2235"),
                "sector_weights": regime.get("sector_weights", {}),
            }
        except: pass

        # CGT Alerts
        try:
            from tax_tracker import TaxTracker
            tracker = TaxTracker()
            result["cgt_alerts"] = [
                s for s in tracker.batch_status([
                    {"symbol": p.get("symbol",""), "entry_price": p.get("entry_price",0),
                     "current_price": p.get("current_price",0), "id": p.get("id",""),
                     "entry_date": p.get("entry_date") or p.get("created_at")}
                    for p in open_positions
                ]) if s.get("alert")
            ][:5]
        except: pass

        # Positions (no LLM call per position — uses cached sentinel from DB if available)
        for p in open_positions:
            ep = float(p.get("entry_price", 0))
            cp = float(p.get("current_price", 0))
            ed_str = p.get("entry_date_parsed") or p.get("created_at") or ""
            days_held = 0
            try:
                from datetime import date
                if ed_str:
                    if isinstance(ed_str, str):
                        ed = date.fromisoformat(ed_str[:10])
                    else:
                        ed = ed_str.date() if hasattr(ed_str, 'date') else date.today()
                    days_held = (date.today() - ed).days
            except: pass

            sentinel_verdict = p.get("position_stage", "entered")
            if sentinel_verdict not in ("INTACT", "WEAKENED", "BROKEN"):
                sentinel_verdict = "INTACT"

            result["positions"].append({
                "symbol": p.get("symbol",""),
                "entry_price": round(ep, 3),
                "current_price": round(cp, 3),
                "pnl_pct": round((cp / ep - 1) * 100, 2) if ep > 0 else 0,
                "days_held": days_held,
                "sentinel_verdict": sentinel_verdict,
                "exit_signal": "HOLD",
                "exit_reason": None,
                "cgt_defer": False,
            })

        # Data state
        result["data_from"] = "2015"
        result["data_to"] = datetime.utcnow().strftime("%Y-%m")

        # ── Core Sleeve ───────────────────────────────────────────────────
        if _CORE_SLEEVE_AVAILABLE and compute_core_sleeve_state:
            try:
                with db_conn() as c:
                    nav = total_value
                    core_state = compute_core_sleeve_state(c, nav)
                    result["core_sleeve"] = core_state
            except Exception as e:
                result["core_sleeve"] = {"error": str(e), "positions": [], "total_value": 0}

        # ── Model Health ──────────────────────────────────────────────────
        if _MODEL_HEALTH_AVAILABLE:
            try:
                with db_conn() as c:
                    mh = evaluate_signal_outcomes(c)
                    result["model_health"] = mh
            except Exception:
                result["model_health"] = {"status": "unknown", "hit_rate": 0}

        # ── Kill Switch ──────────────────────────────────────────────────
        if _KILL_SWITCH_AVAILABLE:
            try:
                with db_conn() as c:
                    ks = get_latest_kill_state(c)
                    result["kill_switch"] = ks
            except Exception:
                result["kill_switch"] = {"halt_all": False}

        # ── Stale Heartbeat ──────────────────────────────────────────────
        if _STALE_HEARTBEAT_AVAILABLE:
            try:
                from config.universe import get_universe_symbols
                core_syms = get_universe_symbols("core")
                with db_conn() as c:
                    sh = check_stale_sessions(c, core_syms[:50])
                    result["stale_heartbeat"] = sh
            except Exception:
                result["stale_heartbeat"] = {"status": "unknown", "stale_pct": 0}

        return result
    except Exception as e:
        return {"error": str(e), **result}


# ── Core Sleeve API ────────────────────────────────────────────────────────────
@app.get("/api/smsf/core-sleeve")
async def get_core_sleeve(current_user: dict = Depends(get_current_user)):
    uid = current_user["id"]
    nav = get_starting_capital(uid)
    try:
        with db_conn() as c:
            state = compute_core_sleeve_state(c, nav)
            candidates = get_core_candidates()
            return {"core_sleeve": state, "candidates": candidates}
    except Exception as e:
        return {"error": str(e), "core_sleeve": {"positions": [], "total_value": 0}}


class CorePositionRequest(BaseModel):
    symbol: str
    qty: float
    entry_price: float
    market: str = "AU"
    notes: str = ""


@app.post("/api/smsf/core-sleeve")
async def add_core_position(req: CorePositionRequest, current_user: dict = Depends(get_current_user)):
    uid = current_user["id"]
    try:
        with db_conn() as c:
            pos_id = create_core_position(c, uid, req.symbol.upper(), req.qty, req.entry_price,
                                          req.market, req.notes)
            c.commit()
            if pos_id:
                return {"ok": True, "id": pos_id, "symbol": req.symbol.upper()}
            return {"ok": False, "error": "Position creation failed (duplicate or limit reached)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/smsf/core-sleeve/{position_id}/close")
async def close_core_pos(position_id: str, reason: str = "manual",
                         current_user: dict = Depends(get_current_user)):
    try:
        with db_conn() as c:
            ok = close_core_position(c, position_id, reason)
            c.commit()
            return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/smsf/sleeves")
async def get_sleeve_breakdown(current_user: dict = Depends(get_current_user)):
    uid = current_user["id"]
    nav = get_starting_capital(uid)
    try:
        with db_conn() as c:
            core = compute_core_sleeve_state(c, nav)

            sat_rows = c.execute(
                "SELECT symbol, COALESCE(entry_price,0)*COALESCE(qty,0) AS value FROM paper_trades WHERE status='open'"
            ).fetchall()
            sat_value = sum(r[1] for r in sat_rows)
            sat_count = len(sat_rows)

            cash = max(0, nav - core["total_value"] - sat_value)

            return {
                "nav": round(nav, 2),
                "core": {"value": core["total_value"], "pct": core["pct_of_nav"], "count": core["position_count"],
                         "target_pct": core["target_pct"]},
                "satellite": {"value": round(sat_value, 2), "pct": round(sat_value/nav*100, 1), "count": sat_count,
                              "max_positions": 8, "target_pct": 40},
                "cash": {"value": round(cash, 2), "pct": round(cash/nav*100, 1)},
            }
    except Exception as e:
        return {"error": str(e), "nav": nav, "core": {}, "satellite": {}, "cash": {}}


@app.get("/api/v1/market/pulse")
async def market_pulse(current_user: dict = Depends(get_current_user)):
    del current_user
    snapshot = compute_regime_snapshot()
    macro = get_macro_indicators()
    return {
        "metrics": {
            "asx200_ret": snapshot["asx200_ret"],
            "sp500_ret": snapshot["sp500_ret"],
            "nifty_ret": snapshot["nifty_ret"],
            "sensex_ret": snapshot["sensex_ret"],
            "gold_ret": snapshot["gold_ret"],
            "dxy_ret": snapshot["dxy_ret"],
        },
        "regime": {
            "name": snapshot["regime"],
            "confidence": snapshot["confidence"],
            "tags": snapshot["tags"],
            "safe_haven_flag": snapshot["safe_haven_flag"],
            "usd_headwind_flag": snapshot["usd_headwind_flag"],
            "divergence_flag": snapshot["divergence_flag"],
        },
        "summary": regime_summary_text(snapshot),
        "macro": macro,
    }


@app.get("/api/v1/backtest/summary")
async def backtest_summary(current_user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT symbol, start_date, target_return_14d, actual_return_14d, bucket, status
                FROM tracking_windows
                WHERE user_id = :user_id AND status = 'completed' AND actual_return_14d IS NOT NULL
                ORDER BY start_date DESC
                """
            ),
            {"user_id": current_user["id"]},
        ).fetchall()

    predictions = np.array([float(row[2]) for row in rows], dtype=float) if rows else np.array([])
    actuals = np.array([float(row[3]) for row in rows], dtype=float) if rows else np.array([])

    if len(actuals) > 0:
        abs_err = np.abs(predictions - actuals)
        mae = float(np.mean(abs_err))
        rmse = float(np.sqrt(np.mean((predictions - actuals) ** 2)))

        safe_denominator = np.where(np.abs(actuals) < 1e-8, 1e-8, np.abs(actuals))
        mape = float(np.mean(np.abs((predictions - actuals) / safe_denominator)) * 100.0)

        dir_acc = float(np.mean(np.sign(predictions) == np.sign(actuals)) * 100.0)
        hit_ratio = float(np.mean(np.array([row[4] == "meeting_expectation" for row in rows])) * 100.0)
    else:
        mae = rmse = mape = dir_acc = hit_ratio = 0.0

    calibration_bins = [
        {"bin": "0-20", "predicted_avg": 10.0, "realized_positive_pct": 0.0, "count": 0},
        {"bin": "20-40", "predicted_avg": 30.0, "realized_positive_pct": 0.0, "count": 0},
        {"bin": "40-60", "predicted_avg": 50.0, "realized_positive_pct": 0.0, "count": 0},
        {"bin": "60-80", "predicted_avg": 70.0, "realized_positive_pct": 0.0, "count": 0},
        {"bin": "80-100", "predicted_avg": 90.0, "realized_positive_pct": 0.0, "count": 0},
    ]

    for row in rows:
        pred_ret = float(row[2])
        actual_ret = float(row[3])
        prob_proxy = max(0.0, min(100.0, 50.0 + pred_ret * 500.0))
        bucket_idx = min(4, int(prob_proxy // 20.0))
        calibration_bins[bucket_idx]["count"] += 1
        calibration_bins[bucket_idx].setdefault("positive_hits", 0)
        if actual_ret >= 0:
            calibration_bins[bucket_idx]["positive_hits"] += 1

    for bucket in calibration_bins:
        hits = bucket.pop("positive_hits", 0)
        count = bucket["count"]
        bucket["realized_positive_pct"] = round((hits / count) * 100.0, 2) if count else 0.0

    recent = [
        {
            "symbol": row[0],
            "start_date": row[1].isoformat(),
            "predicted_return_14d_pct": round(float(row[2]) * 100.0, 2),
            "actual_return_14d_pct": round(float(row[3]) * 100.0, 2),
            "bucket": row[4],
        }
        for row in rows[:20]
    ]

    return {
        "metrics": {
            "mae_pct": round(mae * 100.0, 3),
            "rmse_pct": round(rmse * 100.0, 3),
            "mape_pct": round(mape, 3),
            "directional_accuracy_pct": round(dir_acc, 2),
            "hit_ratio_14d_pct": round(hit_ratio, 2),
            "samples": len(rows),
        },
        "calibration": calibration_bins,
        "recent": recent,
    }


@app.post("/api/auth/register", response_model=AuthResponse)
def register(payload: RegisterRequest):
    email = payload.email.strip().lower()
    password = payload.password

    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Please provide a valid email")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    with db_conn() as conn:
        existing = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        user_id = str(uuid4())
        conn.execute(
            text(
                """
                INSERT INTO users (id, email, full_name, password_hash)
                VALUES (:id, :email, :full_name, :password_hash)
                """
            ),
            {
                "id": user_id,
                "email": email,
                "full_name": (payload.full_name or "").strip(),
                "password_hash": hash_password(password),
            },
        )

    token = create_access_token(user_id=user_id, email=email)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "email": email,
            "full_name": (payload.full_name or "").strip(),
        },
    }


@app.post("/api/auth/login", response_model=AuthResponse)
def login(payload: LoginRequest):
    email = payload.email.strip().lower()

    with engine.connect() as conn:
        user = conn.execute(
            text("SELECT id, email, full_name, password_hash FROM users WHERE email = :email"),
            {"email": email},
        ).fetchone()

    if not user or not verify_password(payload.password, user[3]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user_id=user[0], email=user[1])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user[0],
            "email": user[1],
            "full_name": user[2] or "",
        },
    }


@app.post("/api/auth/forgot-password", response_model=PasswordResetResponse)
def forgot_password(payload: ForgotPasswordRequest):
    email = payload.email.strip().lower()

    with db_conn() as conn:
        user = conn.execute(
            text("SELECT id, email FROM users WHERE email = :email"),
            {"email": email},
        ).fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="Email not found")

        # Generate a reset token
        reset_token = str(uuid4())
        user_id = user[0]
        expires_at = datetime.utcnow() + timedelta(minutes=15)  # Token valid for 15 minutes

        # Store the reset token in database
        conn.execute(
            text(
                """
                INSERT INTO password_reset_tokens (token, user_id, expires_at)
                VALUES (:token, :user_id, :expires_at)
                """
            ),
            {"token": reset_token, "user_id": user_id, "expires_at": expires_at},
        )

    return {
        "message": "Password reset token generated successfully",
        "reset_token": reset_token,
        "instructions": f"Use this token to reset your password. Token expires in 15 minutes. Call POST /api/auth/reset-password with your token and new password.",
    }


@app.post("/api/auth/reset-password")
def reset_password(payload: ResetPasswordRequest):
    token = payload.token.strip()
    new_password = payload.new_password

    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Verify token exists, belongs to user, is not expired, and not used
    with db_conn() as conn:
        reset_record = conn.execute(
            text(
                """
                SELECT user_id, expires_at, used FROM password_reset_tokens
                WHERE token = :token
                """
            ),
            {"token": token},
        ).fetchone()

        if not reset_record:
            raise HTTPException(status_code=404, detail="Invalid reset token")

        user_id, expires_at, used = reset_record

        if used:
            raise HTTPException(status_code=400, detail="This reset token has already been used")

        if datetime.fromisoformat(expires_at.isoformat()) < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Reset token has expired")

        # Update user password and mark token as used
        conn.execute(
            text(
                """
                UPDATE users SET password_hash = :password_hash
                WHERE id = :user_id
                """
            ),
            {"password_hash": hash_password(new_password), "user_id": user_id},
        )
        conn.execute(
            text(
                """
                UPDATE password_reset_tokens SET used = 1
                WHERE token = :token
                """
            ),
            {"token": token},
        )

    return {
        "message": "Password reset successfully! You can now log in with your new password.",
        "success": True,
    }


@app.get("/api/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    return current_user


@app.put("/api/auth/me/market")
async def update_preferred_market(payload: dict, current_user: dict = Depends(get_current_user)):
    market = (payload.get("market") or "AU").upper()
    if market not in {"AU", "US", "IN"}:
        raise HTTPException(status_code=400, detail="Invalid market. Choose AU, US, or IN")
    with db_conn() as conn:
        conn.execute(
            text("UPDATE users SET preferred_market = :market WHERE id = :uid"),
            {"market": market, "uid": current_user["id"]},
        )
    return {"preferred_market": market}


# ── Investment Budget endpoints ──────────────────────────────────────────────

# Hedge instrument universe — used by hedge suggestions
HEDGE_INSTRUMENTS = {
    "AU": {
        "gold": [
            {"symbol": "GOLD", "name": "ETFS Physical Gold", "type": "gold_etf"},
            {"symbol": "QAU", "name": "BetaShares Gold Bullion", "type": "gold_etf"},
            {"symbol": "PMGOLD", "name": "Perth Mint Gold", "type": "gold_etf"},
        ],
        "bonds": [
            {"symbol": "IAF", "name": "iShares Core Composite Bond", "type": "bond_etf"},
            {"symbol": "VBND", "name": "Vanguard Global Aggregate Bond", "type": "bond_etf"},
            {"symbol": "BOND", "name": "PIMCO Australian Bond", "type": "bond_etf"},
        ],
        "cash": [
            {"symbol": "AAA", "name": "BetaShares High Interest Cash", "type": "cash_etf"},
            {"symbol": "BILL", "name": "iShares Core Cash", "type": "cash_etf"},
        ],
        "defensive": [
            {"symbol": "VHY", "name": "Vanguard High Yield", "type": "defensive_etf"},
            {"symbol": "HVST", "name": "BetaShares Diversified Markets", "type": "defensive_etf"},
        ],
    },
    "US": {
        "gold": [
            {"symbol": "GLD", "name": "SPDR Gold Trust", "type": "gold_etf"},
            {"symbol": "IAU", "name": "iShares Gold Trust", "type": "gold_etf"},
            {"symbol": "SGOL", "name": "Aberdeen Physical Gold", "type": "gold_etf"},
        ],
        "bonds": [
            {"symbol": "TLT", "name": "iShares 20+ Year Treasury", "type": "bond_etf"},
            {"symbol": "AGG", "name": "iShares Core US Aggregate Bond", "type": "bond_etf"},
            {"symbol": "BND", "name": "Vanguard Total Bond Market", "type": "bond_etf"},
        ],
        "inverse": [
            {"symbol": "SH", "name": "ProShares Short S&P500", "type": "inverse_etf"},
            {"symbol": "PSQ", "name": "ProShares Short QQQ", "type": "inverse_etf"},
        ],
        "cash": [
            {"symbol": "SHV", "name": "iShares Short Treasury Bond", "type": "cash_etf"},
        ],
    },
    "IN": {
        "gold": [
            {"symbol": "GOLDBEES", "name": "Nippon India Gold ETF", "type": "gold_etf"},
        ],
        "bonds": [
            {"symbol": "LIQUIDBEES", "name": "Nippon Liquid ETF", "type": "bond_etf"},
        ],
    },
}


@app.get("/api/user/investment-budget")
async def get_investment_budget(current_user: dict = Depends(get_current_user)):
    """Get user's investment budget with deployed/remaining capital calculation."""
    total_budget = float(current_user.get("total_investment_budget") or 0)
    max_position_pct = float(current_user.get("max_position_pct") or 10)
    currency = current_user.get("budget_currency") or "AUD"

    holdings = get_user_execution_holdings(current_user["id"])
    deployed_capital = sum(float(h.get("invested_amount") or 0) for h in holdings)
    remaining_capital = max(total_budget - deployed_capital, 0.0)
    max_per_position = total_budget * (max_position_pct / 100) if total_budget > 0 else 0.0

    # Sector/symbol concentration
    position_weights = []
    for h in holdings:
        weight_pct = (float(h.get("invested_amount") or 0) / total_budget * 100) if total_budget > 0 else 0.0
        position_weights.append({
            "symbol": h["symbol"],
            "market": h.get("market", "AU"),
            "invested": round(float(h.get("invested_amount") or 0), 2),
            "weight_pct": round(weight_pct, 2),
            "over_limit": weight_pct > max_position_pct,
        })

    over_limit_count = sum(1 for pw in position_weights if pw["over_limit"])

    return {
        "total_budget": round(total_budget, 2),
        "deployed_capital": round(deployed_capital, 2),
        "remaining_capital": round(remaining_capital, 2),
        "utilization_pct": round((deployed_capital / total_budget * 100) if total_budget > 0 else 0, 2),
        "max_position_pct": round(max_position_pct, 2),
        "max_per_position": round(max_per_position, 2),
        "currency": currency,
        "positions_count": len(holdings),
        "over_limit_count": over_limit_count,
        "position_weights": position_weights,
        "budget_set": total_budget > 0,
    }


@app.put("/api/user/investment-budget")
async def update_investment_budget(payload: InvestmentBudgetRequest, current_user: dict = Depends(get_current_user)):
    """Set or update user's investment budget."""
    total_budget = max(float(payload.total_budget or 0), 0.0)
    max_position_pct = max(min(float(payload.max_position_pct or 10), 100), 1)
    currency = (payload.currency or "AUD").upper().strip()[:3]

    with db_conn() as conn:
        conn.execute(
            text(
                "UPDATE users SET total_investment_budget = :budget, max_position_pct = :max_pct, budget_currency = :currency WHERE id = :uid"
            ),
            {"budget": total_budget, "max_pct": max_position_pct, "currency": currency, "uid": current_user["id"]},
        )

    # Return the updated budget view
    holdings = get_user_execution_holdings(current_user["id"])
    deployed_capital = sum(float(h.get("invested_amount") or 0) for h in holdings)

    return {
        "ok": True,
        "total_budget": round(total_budget, 2),
        "deployed_capital": round(deployed_capital, 2),
        "remaining_capital": round(max(total_budget - deployed_capital, 0.0), 2),
        "max_position_pct": round(max_position_pct, 2),
        "max_per_position": round(total_budget * max_position_pct / 100, 2),
        "currency": currency,
    }


# ── Hedge Suggestions endpoint ──────────────────────────────────────────────

def _calculate_sector_exposure(holdings: list[dict]) -> dict[str, float]:
    """Calculate sector-level exposure from holdings (uses yfinance info)."""
    sector_totals: dict[str, float] = {}
    total_invested = sum(float(h.get("invested_amount") or 0) for h in holdings)
    if total_invested <= 0:
        return {}
    for h in holdings:
        invested = float(h.get("invested_amount") or 0)
        sym = h["symbol"]
        market = h.get("market", "AU")
        try:
            info = yf.Ticker(format_ticker(sym, market)).info
            sector = info.get("sector") or "Unknown"
        except Exception:
            sector = "Unknown"
        sector_totals[sector] = sector_totals.get(sector, 0) + invested
    return {sector: round(amt / total_invested * 100, 2) for sector, amt in sector_totals.items()}


@app.get("/api/positions/hedge-suggestions")
async def portfolio_hedge_suggestions(current_user: dict = Depends(get_current_user)):
    """Proactive hedge suggestions based on regime + user holdings."""
    holdings = get_user_execution_holdings(current_user["id"])
    if not holdings:
        return {"suggestions": [], "regime": {}, "message": "No active positions to hedge."}

    regime = compute_regime_snapshot()
    market = current_user.get("preferred_market") or "AU"
    total_invested = sum(float(h.get("invested_amount") or 0) for h in holdings)

    suggestions = []

    # 1. Regime-based suggestions
    if regime.get("safe_haven_flag"):
        hedge_instruments = HEDGE_INSTRUMENTS.get(market, HEDGE_INSTRUMENTS["AU"]).get("gold", [])
        for inst in hedge_instruments[:2]:
            suggestions.append({
                "instrument": inst["symbol"],
                "name": inst["name"],
                "type": "safe_haven",
                "category": "gold",
                "reason": f"Equities declining ({regime.get('asx200_ret', 0):.1f}%) while gold rising ({regime.get('gold_ret', 0):.1f}%) — classic safe-haven rotation",
                "suggested_allocation_pct": 10,
                "suggested_amount": round(total_invested * 0.10, 2),
                "urgency": "high",
            })

    if regime.get("usd_headwind_flag"):
        suggestions.append({
            "instrument": "CURRENCY_HEDGE",
            "name": "Currency-hedged ETF or AUD cash",
            "type": "currency_protection",
            "category": "cash",
            "reason": f"USD strengthening (DXY: {regime.get('dxy_ret', 0):.1f}%). Exporter-heavy positions at risk.",
            "suggested_allocation_pct": 5,
            "suggested_amount": round(total_invested * 0.05, 2),
            "urgency": "medium",
        })

    if regime.get("regime") == "risk_off":
        bond_instruments = HEDGE_INSTRUMENTS.get(market, HEDGE_INSTRUMENTS["AU"]).get("bonds", [])
        for inst in bond_instruments[:1]:
            suggestions.append({
                "instrument": inst["symbol"],
                "name": inst["name"],
                "type": "risk_off_protection",
                "category": "bonds",
                "reason": "Market in risk-off mode. Bonds typically outperform during equity drawdowns.",
                "suggested_allocation_pct": 15,
                "suggested_amount": round(total_invested * 0.15, 2),
                "urgency": "medium",
            })

    # 2. Sector concentration warnings
    try:
        sector_exposure = _calculate_sector_exposure(holdings)
        for sector, weight in sector_exposure.items():
            if weight > 40:
                suggestions.append({
                    "instrument": "DIVERSIFY",
                    "name": f"Reduce {sector} concentration",
                    "type": "concentration_warning",
                    "category": "rebalance",
                    "reason": f"{sector} sector is {weight:.1f}% of portfolio (>40%). Consider trimming or hedging with uncorrelated assets.",
                    "suggested_allocation_pct": 0,
                    "sector": sector,
                    "sector_weight_pct": weight,
                    "urgency": "medium",
                })
    except Exception:
        pass

    # 3. If no regime issues, suggest maintaining defensive buffer
    if not suggestions:
        cash_instruments = HEDGE_INSTRUMENTS.get(market, HEDGE_INSTRUMENTS["AU"]).get("cash", [])
        if cash_instruments:
            suggestions.append({
                "instrument": cash_instruments[0]["symbol"],
                "name": cash_instruments[0]["name"],
                "type": "defensive_buffer",
                "category": "cash",
                "reason": "Market conditions normal. Maintain a 5-10% cash buffer for opportunistic rebalancing.",
                "suggested_allocation_pct": 5,
                "suggested_amount": round(total_invested * 0.05, 2),
                "urgency": "low",
            })

    return {
        "regime": {
            "current": regime.get("regime"),
            "confidence": regime.get("confidence"),
            "safe_haven_flag": regime.get("safe_haven_flag"),
            "usd_headwind_flag": regime.get("usd_headwind_flag"),
            "divergence_flag": regime.get("divergence_flag"),
            "equities_ret": regime.get("asx200_ret"),
            "gold_ret": regime.get("gold_ret"),
            "dxy_ret": regime.get("dxy_ret"),
        },
        "total_invested": round(total_invested, 2),
        "positions_count": len(holdings),
        "suggestions": suggestions,
        "hedge_instruments": HEDGE_INSTRUMENTS.get(market, HEDGE_INSTRUMENTS["AU"]),
        "market": market,
    }


# ── Position Sentiment Monitor endpoint ──────────────────────────────────────

def _get_sentiment_for_position(symbol: str, market: str = "AU") -> dict:
    """Lightweight sentiment check for position monitoring (cached-aware)."""
    try:
        ticker_str = format_ticker(symbol, market)
        tk = yf.Ticker(ticker_str)
        news_items = []
        try:
            for article in (tk.news or [])[:5]:
                news_items.append({
                    "title": article.get("title", ""),
                    "publisher": article.get("publisher", ""),
                    "link": article.get("link", ""),
                    "type": article.get("type", ""),
                })
        except Exception:
            pass

        if not news_items:
            return {"sentiment": "neutral", "score": 0.0, "themes": [], "news": [], "headline": ""}

        # Use LLM for sentiment analysis (quick version)
        headlines_text = "\n".join(f"- {n['title']}" for n in news_items if n.get("title"))
        prompt = (
            f"Analyse these recent news headlines for {symbol} and return ONLY a JSON object.\n\n"
            f"Headlines:\n{headlines_text}\n\n"
            f"Return: {{\"sentiment\": \"positive|neutral|negative\", \"score\": -1.0 to 1.0, "
            f"\"themes\": [\"theme1\", \"theme2\"], \"summary\": \"one sentence\"}}"
        )

        raw = None
        for provider in LLM_PROVIDER_ORDER:
            try:
                if provider == "local":
                    r = requests.post(
                        f"{LOCAL_LLM_URL}/chat/completions",
                        json={"model": LOCAL_LLM_MODEL,
                              "messages": [{"role": "system", "content": "Financial sentiment analyst. Return strict JSON only."},
                                           {"role": "user", "content": prompt}],
                              "max_tokens": 300, "temperature": 0.2},
                        timeout=30,
                    )
                    if r.status_code == 200:
                        raw = strip_think_tags(r.json()["choices"][0]["message"]["content"])
                        break
                elif provider == "openai" and openai_client:
                    r = openai_client.chat.completions.create(
                        model=OPENAI_MODEL,
                        messages=[{"role": "system", "content": "Financial sentiment analyst. Return strict JSON only."},
                                   {"role": "user", "content": prompt}],
                        max_tokens=300, temperature=0.2,
                    )
                    raw = r.choices[0].message.content
                    break
            except Exception:
                continue

        if raw:
            try:
                cleaned = extract_json_from_llm(raw) or raw
                s = cleaned.find("{")
                e = cleaned.rfind("}") + 1
                parsed = json.loads(cleaned[s:e]) if s >= 0 else {}
                return {
                    "sentiment": parsed.get("sentiment", "neutral"),
                    "score": float(parsed.get("score", 0)),
                    "themes": parsed.get("themes", []),
                    "summary": parsed.get("summary", ""),
                    "news": news_items,
                    "headline": news_items[0]["title"] if news_items else "",
                }
            except Exception:
                pass

        return {"sentiment": "neutral", "score": 0.0, "themes": [], "news": news_items, "headline": ""}
    except Exception:
        return {"sentiment": "neutral", "score": 0.0, "themes": [], "news": [], "headline": ""}


def _calculate_profit_at_risk(symbol: str, market: str, holdings: list[dict], risk_pct: float = 5.0) -> dict:
    """Calculate unrealized profit and what could be lost if stock drops by risk_pct%."""
    for h in holdings:
        if h["symbol"] == symbol:
            qty = float(h.get("quantity") or 0)
            avg_cost = float(h.get("avg_cost") or 0)
            invested = float(h.get("invested_amount") or 0)
            try:
                sd = get_stock_data(symbol, market)
                live_price = float(sd.get("current_price") or 0)
            except Exception:
                live_price = avg_cost

            current_value = qty * live_price if live_price > 0 else invested
            unrealized_profit = current_value - invested
            unrealized_pct = ((live_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0
            potential_loss = current_value * (risk_pct / 100)
            profit_after_drop = unrealized_profit - potential_loss

            return {
                "symbol": symbol,
                "quantity": round(qty, 6),
                "avg_cost": round(avg_cost, 4),
                "live_price": round(live_price, 4),
                "invested": round(invested, 2),
                "current_value": round(current_value, 2),
                "unrealized_profit": round(unrealized_profit, 2),
                "unrealized_pct": round(unrealized_pct, 2),
                "potential_loss_at_risk_pct": round(potential_loss, 2),
                "risk_pct": risk_pct,
                "profit_after_drop": round(profit_after_drop, 2),
                "would_be_negative": profit_after_drop < 0,
            }
    return {}


def _check_and_cache_sentiment(user_id: str, sym: str, market: str, now: datetime) -> dict:
    """Check cache for recent sentiment or fetch new and cache it."""
    try:
        with engine.connect() as conn:
            recent = conn.execute(
                text(
                    "SELECT sentiment, score, themes, headline, created_at FROM position_sentiment_log "
                    "WHERE user_id = :uid AND symbol = :sym "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"uid": user_id, "sym": sym},
            ).fetchone()
            if recent and recent[4]:
                hours_ago = (now - recent[4]).total_seconds() / 3600
                if hours_ago < 4:
                    return {
                        "sentiment": recent[0],
                        "score": float(recent[1] or 0),
                        "themes": json.loads(recent[2]) if recent[2] else [],
                        "headline": recent[3] or "",
                        "cached": True
                    }
    except Exception:
        pass

    # Run fresh sentiment scan
    sentiment = _get_sentiment_for_position(sym, market)
    res = {
        "sentiment": sentiment["sentiment"],
        "score": sentiment["score"],
        "themes": sentiment.get("themes", []),
        "headline": sentiment.get("headline", ""),
        "summary": sentiment.get("summary", ""),
        "cached": False,
    }

    # Log to position_sentiment_log
    try:
        with db_conn() as conn:
            conn.execute(
                text(
                    "INSERT INTO position_sentiment_log (id, user_id, symbol, market, sentiment, score, themes, headline, created_at) "
                    "VALUES (:id, :uid, :sym, :market, :sentiment, :score, :themes, :headline, :created_at)"
                ),
                {
                    "id": str(uuid4()),
                    "uid": user_id,
                    "sym": sym,
                    "market": market,
                    "sentiment": res["sentiment"],
                    "score": res["score"],
                    "themes": json.dumps(res["themes"]),
                    "headline": res["headline"],
                    "created_at": now,
                },
            )
    except Exception:
        pass
    return res


@app.get("/api/positions/sentiment-scan")
async def positions_sentiment_scan(current_user: dict = Depends(get_current_user)):
    """Scan all held positions for news sentiment and generate sell alerts."""
    holdings = get_user_execution_holdings(current_user["id"])
    if not holdings:
        return {"alerts": [], "scanned": 0, "message": "No active positions to scan."}

    alerts = []
    sentiment_results = []
    now = datetime.utcnow()

    for h in holdings:
        sym = h["symbol"]
        market = h.get("market", "AU")

        sentiment = _check_and_cache_sentiment(current_user["id"], sym, market, now)
        
        sentiment_results.append({
            "symbol": sym,
            "sentiment": sentiment["sentiment"],
            "score": sentiment["score"],
            "themes": sentiment.get("themes", []),
            "headline": sentiment.get("headline", ""),
            "cached": sentiment.get("cached", False),
        })

        # Generate alert if sentiment is negative
        if sentiment["sentiment"] == "negative" and sentiment["score"] < -0.3:
            par = _calculate_profit_at_risk(sym, market, holdings)
            alerts.append({
                "type": "negative_news",
                "symbol": sym,
                "market": market,
                "action": "REVIEW_SELL",
                "urgency": "high",
                "sentiment_score": sentiment["score"],
                "themes": sentiment.get("themes", []),
                "headline": sentiment.get("headline", ""),
                "summary": sentiment.get("summary", ""),
                "reason": f"Negative news detected (score: {sentiment['score']:.2f}). {sentiment.get('summary', '')}",
                "profit_at_risk": par,
                "cached": False,
            })
        elif sentiment["sentiment"] == "negative":
            alerts.append({
                "type": "mild_negative",
                "symbol": sym,
                "market": market,
                "action": "MONITOR",
                "urgency": "medium",
                "sentiment_score": sentiment["score"],
                "themes": sentiment.get("themes", []),
                "headline": sentiment.get("headline", ""),
                "reason": f"Mildly negative sentiment (score: {sentiment['score']:.2f}). Monitor closely.",
                "cached": False,
            })

    high_urgency = [a for a in alerts if a.get("urgency") == "high"]
    medium_urgency = [a for a in alerts if a.get("urgency") == "medium"]

    return {
        "scanned": len(sentiment_results),
        "alerts": alerts,
        "sentiment_results": sentiment_results,
        "high_urgency_count": len(high_urgency),
        "medium_urgency_count": len(medium_urgency),
        "summary": f"{len(high_urgency)} positions need urgent review" if high_urgency else "All positions sentiment normal.",
        "scanned_at": now.isoformat(),
    }


@app.get("/api/shares")
async def get_all_shares(current_user: dict = Depends(get_current_user)):
    """Get all tracked shares with current data"""
    with engine.connect() as conn:
        shares = conn.execute(
            text("SELECT symbol, name, added_at FROM user_shares WHERE user_id = :user_id"),
            {"user_id": current_user["id"]},
        ).fetchall()
    
    result = []
    for share in shares:
        symbol = share[0]
        stock_data = get_stock_data(symbol)
        weekly_data = get_weekly_data(symbol)
        
        result.append({
            "symbol": symbol,
            "name": stock_data["name"],
            "current_price": stock_data["current_price"],
            "change_percent": round(stock_data["change_percent"], 2),
            "weekly_data": weekly_data
        })
    
    return result

@app.post("/api/shares")
async def add_share(symbol: str, source: str = "manual_add", current_user: dict = Depends(get_current_user)):
    """Add a new share to track"""
    symbol = symbol.upper().strip().replace(".AX", "").replace(".NS", "")

    if symbol in ALL_COMPANIES:
        name = ALL_COMPANIES[symbol]
    else:
        # Try each market until a valid name is returned
        name = None
        for m in [detect_market(symbol), "AU", "US", "IN"]:
            try:
                info = yf.Ticker(format_ticker(symbol, m)).info
                n = info.get("longName") or info.get("shortName", "")
                if n and n.lower() != symbol.lower():
                    name = n
                    ALL_COMPANIES[symbol] = name
                    break
            except Exception:
                continue
        if not name:
            raise HTTPException(status_code=400, detail=f"Invalid symbol: {symbol}")
    
    with db_conn() as conn:
        existing = conn.execute(
            text("SELECT symbol FROM user_shares WHERE user_id = :user_id AND symbol = :symbol"),
            {"user_id": current_user["id"], "symbol": symbol},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail=f"Share {symbol} already tracked")

        conn.execute(
            text("INSERT INTO user_shares (user_id, symbol, name) VALUES (:user_id, :symbol, :name)"),
            {"user_id": current_user["id"], "symbol": symbol, "name": name},
        )

    create_tracking_window(current_user["id"], symbol, source=source)
    
    return {"message": f"Share {symbol} added successfully", "symbol": symbol}

@app.delete("/api/shares/{symbol}")
async def remove_share(symbol: str, current_user: dict = Depends(get_current_user)):
    """Remove a share from tracking"""
    symbol = symbol.upper()
    
    with db_conn() as conn:
        result = conn.execute(
            text("DELETE FROM user_shares WHERE user_id = :user_id AND symbol = :symbol"),
            {"user_id": current_user["id"], "symbol": symbol},
        )
        deleted = result.rowcount
    
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"Share {symbol} not found")
    
    return {"message": f"Share {symbol} removed successfully"}

@app.get("/api/shares/{symbol}")
async def get_share_details(symbol: str, current_user: dict = Depends(get_current_user)):
    """Get detailed info with prediction for a share"""
    symbol = symbol.upper()

    with engine.connect() as conn:
        tracked = conn.execute(
            text("SELECT symbol FROM user_shares WHERE user_id = :user_id AND symbol = :symbol"),
            {"user_id": current_user["id"], "symbol": symbol},
        ).fetchone()
    if not tracked:
        raise HTTPException(status_code=404, detail=f"Share {symbol} is not tracked by this user")
    
    # Get stock data
    stock_data = get_stock_data(symbol)
    
    # Get historical data
    hist = get_historical_data(symbol)
    
    if len(hist) == 0:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")
    
    # Calculate technical indicators
    indicators = calculate_technical_indicators(hist)
    
    # Generate prediction
    prediction = generate_statistical_prediction(hist, stock_data["current_price"])
    
    # Get valuation metrics
    valuation = get_valuation_metrics(symbol)
    
    # Get risk metrics
    risk = get_risk_metrics(symbol)
    
    # Get regime context
    regime_snapshot = compute_regime_snapshot()
    regime = {
        "name": regime_snapshot.get("regime", "N/A"),
        "confidence": regime_snapshot.get("confidence", 0)
    }
    
    # Historical accuracy (placeholder - would need database integration)
    historical_accuracy = {"directional_accuracy": None, "avg_error": None}
    
    # Get LLM analysis with comprehensive context
    llm_analysis = await get_llm_analysis(
        symbol, 
        stock_data, 
        indicators, 
        prediction,
        valuation=valuation,
        risk=risk,
        regime=regime,
        historical_accuracy=historical_accuracy
    )
    prediction["llm_analysis"] = llm_analysis
    
    # Get weekly data
    weekly_data = get_weekly_data(symbol)
    
    # Entry timing assessment
    entry_timing = _entry_timing_assessment(
        valuation, indicators, {**prediction, "current_price": stock_data["current_price"]}
    )

    return {
        "symbol": symbol,
        "name": stock_data["name"],
        "current_price": stock_data["current_price"],
        "change_percent": round(stock_data["change_percent"], 2),
        "technical_indicators": indicators,
        "valuation_metrics": valuation,
        "risk_metrics": risk,
        "prediction_3m": prediction,
        "weekly_data": weekly_data,
        "entry_timing": entry_timing,
    }

@app.get("/api/search")
async def search_shares(query: str, market: str = None, _: dict = Depends(get_current_user)):
    """Search for shares across AU, US, and IN markets."""
    q_upper = query.upper()
    q_lower = query.lower()
    results = []
    search_pool: dict
    if market == "US":
        search_pool = US_COMPANIES
    elif market == "IN":
        search_pool = {k.replace(".NS", ""): v for k, v in INDIA_COMPANIES.items()}
    elif market == "AU":
        search_pool = ASX_COMPANIES
    else:
        search_pool = ALL_COMPANIES
    for symbol, name in search_pool.items():
        if q_upper in symbol or q_lower in name.lower():
            results.append({"symbol": symbol, "name": name, "market": detect_market(symbol)})
    return results[:20]


def _fetch_analyze_data(symbol: str, market: str = None):
    """Collect all blocking yfinance data for analysis in parallel."""
    with ThreadPoolExecutor(max_workers=5) as executor:
        f_stock = executor.submit(get_stock_data, symbol, market)
        f_hist = executor.submit(get_historical_data, symbol, "1y", market)
        f_val = executor.submit(get_valuation_metrics, symbol)
        f_risk = executor.submit(get_risk_metrics, symbol)
        f_regime = executor.submit(compute_regime_snapshot)
        f_weekly = executor.submit(get_weekly_data, symbol)
    return (
        f_stock.result(),
        f_hist.result(),
        f_val.result(),
        f_risk.result(),
        f_regime.result(),
        f_weekly.result(),
    )


@app.get("/api/ai/analyze/{symbol}")
async def analyze_share(symbol: str, market: str = None, current_user: dict = Depends(get_current_user)):
    """Analyze a single share without requiring it to be tracked.
    Pass ?market=AU|US|IN to force exchange lookup."""
    symbol = symbol.upper()
    # Normalise and validate market param; default to AU (ASX)
    if market:
        market = market.upper()
        if market not in {"AU", "US", "IN"}:
            market = None
    if not market:
        market = detect_market(symbol)

    # Run all blocking yfinance calls in a single background thread to free the event loop
    stock_data, hist, valuation, risk, regime_snapshot, weekly_data = await asyncio.to_thread(
        _fetch_analyze_data, symbol, market
    )

    if len(hist) == 0:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

    # Calculate technical indicators (pure Python, non-blocking)
    indicators = calculate_technical_indicators(hist)

    # Generate prediction using multiple methods
    prediction = generate_statistical_prediction(hist, stock_data["current_price"], sector=valuation.get('sector', ''), symbol=symbol)

    # Apply Wealth Builder constraints so Analyze Tab is perfectly in sync with the scanner
    try:
        score_data = get_probability_and_score(symbol)
        prediction["score"] = score_data.get("score", 0)
        prediction["prob_ge_5pct"] = score_data.get("prob_ge_5pct", 0)
        prediction["high_volatility_warning"] = score_data.get("high_volatility_warning", False)
        prediction["warning_message"] = score_data.get("warning_message", "")
        prediction["quality_reason"] = score_data.get("quality_reason", "")
        
        # Override naive statistical prediction with the constraint-adjusted prediction
        if "expected_return_3m_pct" in score_data:
            prediction["change_from_current"] = score_data["expected_return_3m_pct"]
        if "predicted_price_3m" in score_data:
            prediction["predicted_price"] = score_data["predicted_price_3m"]
        if "trend" in score_data:
            prediction["trend"] = score_data["trend"]
    except Exception as e:
        print(f"[AnalyzeTab] Could not apply wealth constraints to {symbol}: {e}")

    regime = {
        "name": regime_snapshot.get("regime", "N/A"),
        "confidence": regime_snapshot.get("confidence", 0)
    }

    # Get LLM analysis with comprehensive context
    llm_analysis = await get_llm_analysis(
        symbol, 
        stock_data, 
        indicators, 
        prediction,
        valuation=valuation,
        risk=risk,
        regime=regime,
        historical_accuracy={"directional_accuracy": None, "avg_error": None}
    )

    # Entry timing assessment
    entry_timing = _entry_timing_assessment(
        valuation, indicators, {**prediction, "current_price": stock_data["current_price"]}
    )

    # ── Proactive Telegram alert if strong signal and clear entry ─────────────
    try:
        recipients = get_user_telegram_recipients(current_user["id"])
        score_val = prediction.get("score") or 0
        prob_val = prediction.get("prob_ge_5pct") or 0
        if (recipients
                and entry_timing["entry_ok"]
                and entry_timing["entry_zone"] == "clear"
                and (score_val >= 0.65 or prob_val >= 75)):
            signal_msg = _build_wealth_signal_message(
                symbol,
                stock_data["name"],
                {"score": score_val, "prob_ge_5pct": prob_val},
                valuation,
                prediction,
                entry_timing,
            )
            signal_msg += (
                f"\n<b>🤖 AI Summary:</b>\n"
                f"{llm_analysis[:600]}{'…' if len(llm_analysis) > 600 else ''}"
            )
            _send_telegram_payload(
                signal_msg,
                recipients,
                user_id=current_user["id"],
                message_type="strong_signal",
                market=market,
                delivery_mode="auto",
                source="analyze_share",
            )
    except Exception:
        pass

    return {
        "symbol": symbol,
        "name": stock_data["name"],
        "current_price": stock_data["current_price"],
        "change_percent": round(stock_data["change_percent"], 2),
        "technical_indicators": indicators,
        "valuation_metrics": valuation,
        "risk_metrics": risk,
        "prediction": prediction,
        "weekly_data": weekly_data,
        "regime": regime,
        "llm_analysis": llm_analysis,
        "entry_timing": entry_timing,
    }


@app.post("/api/notes/{symbol}")
async def save_note(symbol: str, payload: dict, current_user: dict = Depends(get_current_user)):
    """Save a user's note for a specific symbol"""
    symbol = symbol.upper()
    note = payload.get("note", "")
    
    # Create user notes directory
    notes_dir = Path(__file__).parent / "data" / "user_notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    
    # Save note to file (one file per user per symbol)
    user_id = current_user["id"]
    note_file = notes_dir / f"{user_id}_{symbol}.txt"
    
    with open(note_file, "a") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {note}\n")
    
    return {"status": "saved", "symbol": symbol}


@app.get("/api/notes/{symbol}")
async def get_notes(symbol: str, current_user: dict = Depends(get_current_user)):
    """Get user's notes for a specific symbol"""
    symbol = symbol.upper()
    
    user_id = current_user["id"]
    note_file = Path(__file__).parent / "data" / "user_notes" / f"{user_id}_{symbol}.txt"
    
    if not note_file.exists():
        return {"notes": "", "symbol": symbol}
    
    with open(note_file, "r") as f:
        notes = f.read()
    
    return {"notes": notes, "symbol": symbol}


TOP_SYMBOLS_BY_MARKET = {
    "AU": [
        "BHP", "CBA", "CSL", "WBC", "NAB", "ANZ", "WES", "WOW", "TLS", "RIO",
        "MQG", "FMG", "WDS", "GMG", "ALL", "COL", "REA", "TCL", "QBE", "STO",
        "XRO", "PME", "COH", "RMD", "JBH", "APA", "MIN", "S32", "SEK", "TNE",
        "WTC", "ASX", "DRO", "PLS", "DHHF", "NDQ", "VAS", "STW", "IOZ", "A200",
        # Small cap momentum and speculative (under $1 or high growth potential)
        "SYA", "VUL", "LRS", "ZIP", "BRN", "EXR", "MAY", "88E", "DCC", "RNU",
        "AGY", "TLG", "RAC", "IMU", "OPT", "BOT",
    ],
    "US": [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "V", "JPM", "JNJ",
        "HD", "MA", "UNH", "XOM", "PG", "CVX", "ABBV", "BAC", "BRK-B", "ADBE",
        "CRM", "COST", "AVGO", "LLY", "PEP", "MRK", "WMT", "AMD", "KO", "NFLX",
    ],
    "IN": [
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "HINDUNILVR", "ITC", "LT",
        "KOTAKBANK", "BAJFINANCE", "MARUTI", "AXISBANK", "HCLTECH", "SUNPHARMA", "TITAN", "WIPRO", "ADANIENT", "NTPC",
        "ULTRACEMCO", "TATAMOTORS", "ASIANPAINT", "BAJAJFINSV", "NESTLEIND", "POWERGRID", "ONGC", "COALINDIA", "SBILIFE", "DRREDDY",
    ],
}

def _fetch_universe_items(symbols: list, market: str) -> list:
    """Fetch price data for a list of symbols in a single thread."""
    items = []
    for symbol in symbols:
        data = get_stock_data(symbol, market)
        items.append({
            "symbol": symbol,
            "name": data["name"],
            "current_price": round(data["current_price"], 2),
            "change_percent": round(data["change_percent"], 2),
        })
    return items


@app.get("/api/universe/top")
async def get_top_universe(market: str = "AU", current_user: dict = Depends(get_current_user)):
    del current_user
    m = (market or "AU").upper()
    symbols = TOP_SYMBOLS_BY_MARKET.get(m, TOP_SYMBOLS_BY_MARKET["AU"])
    # Run blocking yfinance calls in a background thread to free the event loop
    items = await asyncio.to_thread(_fetch_universe_items, symbols, m)
    return {"items": items}


@app.post("/api/ai/suggest-shares")
async def ai_suggest_shares(payload: AISuggestRequest, current_user: dict = Depends(get_current_user)):
    del current_user
    max_symbols = max(1, min(payload.max_symbols, 15))
    symbols, provider_used, provider_error = await suggest_symbols_from_ai(
        payload.query,
        max_symbols=max_symbols,
        provider_preference=payload.provider,
    )

    requested_provider = (payload.provider or "auto").strip().lower()
    if requested_provider in {"local", "openai"} and provider_used == requested_provider and not symbols:
        if requested_provider == "local":
            raise HTTPException(
                status_code=503,
                detail=(
                    "Local provider selected but unavailable. Start LM Studio server and check URL (/v1 or /api/v1), "
                    f"or switch provider to OpenAI. Details: {provider_error or 'n/a'}"
                ),
            )
        raise HTTPException(
            status_code=503,
            detail=(
                "OpenAI provider selected but unavailable. Check OPENAI_API_KEY/billing quota or switch provider to Local. "
                f"Details: {provider_error or 'n/a'}"
            ),
        )

    return {
        "query": payload.query,
        "provider_used": provider_used,
        "symbols": symbols,
        "items": [{"symbol": symbol, "name": ASX_COMPANIES.get(symbol, symbol)} for symbol in symbols],
    }


@app.post("/api/ai/market-analysis")
async def get_market_analysis(payload: MarketAnalysisRequest, current_user: dict = Depends(get_current_user)):
    del current_user
    
    regime = payload.regime
    metrics = payload.metrics
    
    prompt = f"""You are a professional market analyst. Provide a detailed analysis of the current market conditions.

MARKET DATA:
- ASX200: {metrics.get('asx200_ret', 0):.2f}%
- S&P 500: {metrics.get('sp500_ret', 0):.2f}%
- Gold: {metrics.get('gold_ret', 0):.2f}%
- DXY (USD): {metrics.get('dxy_ret', 0):.2f}%

REGIME ANALYSIS:
- Current Regime: {regime.get('name', 'unknown')}
- Confidence: {regime.get('confidence', 0):.1f}%
- Flags: {', '.join(regime.get('tags', []))}

Provide a comprehensive market analysis covering:
1. What the market data indicates
2. Sector rotation implications  
3. Risk assessment
4. Investment implications for Australian investors
5. Key watchouts

Be detailed and specific. This is for educational purposes only."""
    
    # Try local provider first
    analysis = None
    
    def try_local():
        try:
            response = requests.post(
                f"{LOCAL_LLM_URL}/chat/completions",
                json={
                    "model": LOCAL_LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a professional market analyst providing detailed insights."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 1200,
                    "temperature": 0.3
                },
                timeout=90
            )
            if response.status_code == 200:
                return strip_think_tags(response.json()['choices'][0]['message']['content'])
        except:
            pass
        return None
    
    def try_openai():
        if not openai_client:
            return None
        try:
            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a professional market analyst providing detailed insights."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1200,
                temperature=0.3
            )
            return strip_think_tags(response.choices[0].message.content)
        except:
            return None
    
    for provider in LLM_PROVIDER_ORDER:
        if provider == "local":
            analysis = try_local()
            if analysis:
                break
        if provider == "openai":
            analysis = try_openai()
            if analysis:
                break
    
    if not analysis:
        analysis = "Market analysis unavailable. Please check LLM configuration."
    
    return {"analysis": analysis}


@app.post("/api/ai/deep-dive")
async def deep_dive_analysis(query: SymbolQuery, current_user: dict = Depends(get_current_user)):
    """Trigger the LangGraph 6-Persona Multi-Agent Debate (6 analysts → rebuttal → CIO verdict)"""
    if not run_agentic_analysis:
        raise HTTPException(status_code=501, detail="LangGraph harness is not fully installed or available.")
    
    sym = query.symbol.strip().upper()
    market = "AU"
    try:
        sd = get_stock_data(sym, market)
        val = get_valuation_metrics(sym)
        hist = get_historical_data(sym, period="1y")
        tech = {}
        if len(hist) > 0:
            tech = calculate_technical_indicators(hist)
            tech = {k: v for k, v in tech.items() if k in [
                "rsi", "rsi_slope", "stoch_rsi", "macd", "macd_signal", "sma_200",
                "ema_9", "ema_20", "ema_50", "volatility", "momentum_20", "adx",
                "dmi_bullish", "atr_pct", "vwap_20", "above_vwap", "up_down_vol_ratio",
                "block_volume_detected", "bb_pct_b", "cmf", "ttm_squeeze_on",
            ]}

        # Build Layer 1 confluence snapshot
        try:
            prediction = generate_statistical_prediction(hist, sd.get("current_price", 0) or float(hist["Close"].iloc[-1]))
            regime_snap = compute_regime_snapshot()
        except Exception:
            prediction = {}
            regime_snap = {}
        confluence = bullish_confluence_score(sym, market, tech, prediction, sd, hist, val, regime_snap)

        # Run the 6-Persona Multi-Agent Debate
        result = await asyncio.to_thread(run_agentic_analysis, sym, market, tech, val, confluence)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deep Dive failed: {str(e)}")


@app.post("/api/ai/sentiment")
async def get_sentiment_analysis(payload: SentimentRequest, current_user: dict = Depends(get_current_user)):
    del current_user
    symbol = payload.symbol.upper().strip()
    
    # Get stock news from EODHD
    market = detect_market(symbol)
    recent_news = get_recent_news(symbol, market)
    news_items = []
    
    if recent_news:
        for item in recent_news[:10]:
            news_items.append({
                "title": item.get("title", ""),
                "publisher": "Financial News",
                "link": ""
            })
    
    # If no news, provide sample analysis
    if not news_items:
        news_items = [{"title": f"No recent news available for {symbol}", "publisher": "System", "link": ""}]
    
    # Format news for LLM
    news_text = "\n".join([f"- {item['title']} ({item['publisher']})" for item in news_items])
    
    prompt = f"""You are a financial sentiment analyst. Analyze the news sentiment for {symbol}.

NEWS HEADLINES:
{news_text}

Provide a detailed sentiment analysis:
1. Overall sentiment (bullish/bearish/neutral) with score from -1 (very bearish) to +1 (very bullish)
2. Key themes from the news
3. Short summary (2-3 sentences)

Return JSON format:
{{"sentiment": "bullish/bearish/neutral", "score": 0.0, "themes": ["theme1", "theme2"], "summary": "summary text"}}"""
    
    sentiment_result = {"sentiment": "neutral", "score": 0.0, "themes": [], "summary": "No analysis available"}
    
    def try_local():
        try:
            response = requests.post(
                f"{LOCAL_LLM_URL}/chat/completions",
                json={
                    "model": LOCAL_LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a financial sentiment analyst. Return strict JSON only."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 800,
                    "temperature": 0.3
                },
                timeout=90
            )
            if response.status_code == 200:
                return strip_think_tags(response.json()['choices'][0]['message']['content'])
        except:
            pass
        return None
    
    def try_openai():
        if not openai_client:
            return None
        try:
            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a financial sentiment analyst. Return strict JSON only."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=300,
                temperature=0.3
            )
            return response.choices[0].message.content
        except:
            return None
    
    for provider in LLM_PROVIDER_ORDER:
        if provider == "local":
            result = try_local()
            if result:
                try:
                    sentiment_result = json.loads(extract_json_from_llm(result) or result)
                except:
                    sentiment_result = {"sentiment": "neutral", "score": 0.0, "themes": [], "summary": result[:200]}
                break
        if provider == "openai":
            result = try_openai()
            if result:
                try:
                    sentiment_result = json.loads(result)
                except:
                    sentiment_result = {"sentiment": "neutral", "score": 0.0, "themes": [], "summary": result[:200]}
                break
    
    return {
        "symbol": symbol,
        "sentiment": sentiment_result.get("sentiment", "neutral"),
        "score": sentiment_result.get("score", 0.0),
        "themes": sentiment_result.get("themes", []),
        "summary": sentiment_result.get("summary", ""),
        "news": news_items[:5]  # Return top 5 news items
    }


@app.post("/api/screener/rank")
async def rank_symbols(payload: RankRequest, current_user: dict = Depends(get_current_user)):
    del current_user
    ranked = []
    for symbol in payload.symbols:
        symbol_clean = symbol.upper().strip()
        if symbol_clean not in ALL_COMPANIES:
            continue
        try:
            ranked.append(get_probability_and_score(symbol_clean))
        except HTTPException:
            continue
        except Exception:
            continue

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return {"items": ranked}


@app.get("/api/tracking/overview")
async def tracking_overview(current_user: dict = Depends(get_current_user)):
    now = datetime.utcnow()
    with db_conn() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, symbol, start_date, end_date, entry_price, target_price_14d,
                       target_return_14d, expected_direction, tolerance_pct, status,
                       actual_price_14d, actual_return_14d, direction_correct,
                       within_tolerance, bucket
                FROM tracking_windows
                WHERE user_id = :user_id
                ORDER BY start_date DESC
                """
            ),
            {"user_id": current_user["id"]},
        ).fetchall()

        overview_rows = []
        completed = []

        for row in rows:
            symbol = row[1]
            stock_data = get_stock_data(symbol)
            current_price = float(stock_data["current_price"])
            start_date = row[2]
            end_date = row[3]
            entry_price = float(row[4])
            target_price = float(row[5])
            target_return = float(row[6])
            expected_direction = row[7]
            tolerance = float(row[8])
            status = row[9]
            bucket = row[14]

            days_elapsed = max(0, (now.date() - start_date.date()).days)
            actual_return_now = ((current_price - entry_price) / entry_price) if entry_price else 0.0

            if status == "active" and now >= end_date:
                direction_correct = (actual_return_now >= 0 and expected_direction == "up") or (
                    actual_return_now < 0 and expected_direction == "down"
                )
                within_tolerance = (
                    actual_return_now >= (target_return - tolerance)
                    if expected_direction == "up"
                    else actual_return_now <= (target_return + tolerance)
                )
                bucket = "meeting_expectation" if direction_correct and within_tolerance else "non_meeting_expectation"

                conn.execute(
                    text(
                        """
                        UPDATE tracking_windows
                        SET status = 'completed',
                            actual_price_14d = :actual_price_14d,
                            actual_return_14d = :actual_return_14d,
                            direction_correct = :direction_correct,
                            within_tolerance = :within_tolerance,
                            bucket = :bucket,
                            evaluated_at = :evaluated_at
                        WHERE id = :id
                        """
                    ),
                    {
                        "actual_price_14d": current_price,
                        "actual_return_14d": actual_return_now,
                        "direction_correct": 1 if direction_correct else 0,
                        "within_tolerance": 1 if within_tolerance else 0,
                        "bucket": bucket,
                        "evaluated_at": now,
                        "id": row[0],
                    },
                )
                status = "completed"

            progress = 0.0
            denom = target_price - entry_price
            if abs(denom) > 1e-8:
                progress = (current_price - entry_price) / denom

            view_row = {
                "id": row[0],
                "symbol": symbol,
                "start_date": start_date.isoformat(),
                "current_day": min(days_elapsed, 63),
                "entry_price": round(entry_price, 2),
                "target_price_14d": round(target_price, 2),
                "current_price": round(current_price, 2),
                "expected_direction": expected_direction,
                "actual_direction": "up" if actual_return_now >= 0 else "down",
                "progress_to_target": round(progress * 100, 2),
                "target_return_14d_pct": round(target_return * 100, 2),
                "actual_return_pct": round(actual_return_now * 100, 2),
                "status": status,
                "bucket": bucket,
            }
            overview_rows.append(view_row)

            if status == "completed":
                completed.append(view_row)

    meetings = [r for r in completed if r["bucket"] == "meeting_expectation"]
    non_meetings = [r for r in completed if r["bucket"] == "non_meeting_expectation"]
    hit_rate = (len(meetings) / len(completed) * 100) if completed else 0.0
    avg_error = (
        sum(abs(r["actual_return_pct"] - r["target_return_14d_pct"]) for r in completed) / len(completed)
        if completed
        else 0.0
    )

    return {
        "rows": overview_rows,
        "buckets": {
            "meeting_expectation": len(meetings),
            "non_meeting_expectation": len(non_meetings),
        },
        "metrics": {
            "validation_hit_rate_pct": round(hit_rate, 2),
            "average_forecast_error_pct": round(avg_error, 2),
        },
    }

# ── Portfolio endpoints ──────────────────────────────────────────────────────

@app.get("/api/portfolios")
async def list_portfolios(current_user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, type, created_at FROM portfolios WHERE user_id = :uid ORDER BY created_at"),
            {"uid": current_user["id"]},
        ).fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2] or "manual", "created_at": r[3]} for r in rows]


@app.post("/api/portfolios", status_code=201)
async def create_portfolio(payload: CreatePortfolioRequest, current_user: dict = Depends(get_current_user)):
    pid = str(uuid4())
    ptype = payload.type if payload.type in ("manual", "ai") else "manual"
    with db_conn() as conn:
        conn.execute(
            text("INSERT INTO portfolios (id, user_id, name, type) VALUES (:id, :uid, :name, :type)"),
            {"id": pid, "uid": current_user["id"], "name": payload.name.strip(), "type": ptype},
        )
    return {"id": pid, "name": payload.name.strip(), "type": ptype}


@app.get("/api/portfolios/{portfolio_id}/holdings")
async def list_holdings(portfolio_id: str, current_user: dict = Depends(get_current_user)):
    _assert_portfolio_ownership(portfolio_id, current_user["id"])
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, symbol, market, quantity, avg_buy_price FROM portfolio_holdings WHERE portfolio_id = :pid"),
            {"pid": portfolio_id},
        ).fetchall()
    result = []
    for r in rows:
        hid, sym, market, qty, avg = r[0], r[1], r[2], r[3], r[4]
        ticker_str = format_ticker(sym, market)
        try:
            info = yf.Ticker(ticker_str).fast_info
            current_price = float(getattr(info, "last_price", 0) or 0)
        except Exception:
            current_price = 0.0
        current_value = current_price * qty
        cost_basis = avg * qty
        pnl = current_value - cost_basis
        pnl_pct = (pnl / cost_basis * 100) if cost_basis else 0.0
        result.append({
            "id": hid, "symbol": sym, "market": market,
            "quantity": qty, "avg_buy_price": avg,
            "current_price": round(current_price, 4),
            "current_value": round(current_value, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
        })
    return result


@app.post("/api/portfolios/{portfolio_id}/holdings", status_code=201)
async def add_holding(portfolio_id: str, payload: AddHoldingRequest, current_user: dict = Depends(get_current_user)):
    _assert_portfolio_ownership(portfolio_id, current_user["id"])
    hid = str(uuid4())
    with db_conn() as conn:
        conn.execute(
            text(
                "INSERT INTO portfolio_holdings (id, portfolio_id, symbol, market, quantity, avg_buy_price) "
                "VALUES (:id, :pid, :sym, :market, :qty, :avg)"
            ),
            {"id": hid, "pid": portfolio_id, "sym": payload.symbol.upper().strip(),
             "market": payload.market.upper(), "qty": payload.quantity, "avg": payload.avg_buy_price},
        )
    return {"id": hid}


@app.delete("/api/portfolios/{portfolio_id}/holdings/{holding_id}")
async def remove_holding(portfolio_id: str, holding_id: str, current_user: dict = Depends(get_current_user)):
    _assert_portfolio_ownership(portfolio_id, current_user["id"])
    with db_conn() as conn:
        conn.execute(
            text("DELETE FROM portfolio_holdings WHERE id = :hid AND portfolio_id = :pid"),
            {"hid": holding_id, "pid": portfolio_id},
        )
    return {"status": "deleted"}


@app.get("/api/portfolios/{portfolio_id}/performance")
async def portfolio_performance(portfolio_id: str, current_user: dict = Depends(get_current_user)):
    holdings = await list_holdings(portfolio_id, current_user)
    total_value = sum(h["current_value"] for h in holdings)
    total_cost = sum(h["avg_buy_price"] * h["quantity"] for h in holdings)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
    return {
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "holdings_count": len(holdings),
    }


@app.post("/api/portfolios/{portfolio_id}/optimize")
async def optimize_portfolio(portfolio_id: str, current_user: dict = Depends(get_current_user)):
    if not PYPFOPT_AVAILABLE:
        raise HTTPException(status_code=501, detail="PyPortfolioOpt not installed")
    _assert_portfolio_ownership(portfolio_id, current_user["id"])
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT symbol, market FROM portfolio_holdings WHERE portfolio_id = :pid"),
            {"pid": portfolio_id},
        ).fetchall()
    if len(rows) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 holdings to optimise")
    tickers = {r[0]: format_ticker(r[0], r[1]) for r in rows}
    prices_data = {}
    for sym, yticker in tickers.items():
        try:
            hist = yf.Ticker(yticker).history(period="1y")["Close"]
            if len(hist) > 60:
                prices_data[sym] = hist
        except Exception:
            pass
    if len(prices_data) < 2:
        raise HTTPException(status_code=400, detail="Not enough price history to optimise")
    df = pd.DataFrame(prices_data).dropna()
    mu = expected_returns.mean_historical_return(df)
    S = risk_models.sample_cov(df)
    ef = EfficientFrontier(mu, S)
    ef.max_sharpe(risk_free_rate=0.04)
    cleaned_weights = ef.clean_weights()
    perf = ef.portfolio_performance(risk_free_rate=0.04)
    return {
        "weights": {k: round(float(v), 4) for k, v in cleaned_weights.items() if v > 0.001},
        "expected_annual_return": round(float(perf[0]), 4),
        "annual_volatility": round(float(perf[1]), 4),
        "sharpe_ratio": round(float(perf[2]), 4),
    }


@app.post("/api/portfolios/{portfolio_id}/llm-review")
async def llm_portfolio_review(portfolio_id: str, current_user: dict = Depends(get_current_user)):
    _assert_portfolio_ownership(portfolio_id, current_user["id"])
    holdings = await list_holdings(portfolio_id, current_user)
    if not holdings:
        raise HTTPException(status_code=400, detail="Portfolio has no holdings")

    holdings_text = "\n".join(
        f"  - {h['symbol']} ({h['market']}): qty={h['quantity']}, avg_buy=${h['avg_buy_price']:.2f}, "
        f"current=${h['current_price']:.2f}, P&L={h['pnl_pct']:.1f}%"
        for h in holdings
    )
    prompt = f"""You are a financial portfolio risk analyst. Analyse the holdings below and return ONLY a JSON object.

Holdings:
{holdings_text}

Return this exact JSON schema:
{{"risk_grade": "A/B/C/D", "diversification_score": 7, "summary": "2-3 sentence overview", "rebalancing_actions": ["action1", "action2"], "scenarios": {{"bull": "brief bull scenario", "bear": "brief bear scenario", "crash": "brief crash scenario"}}}}

risk_grade: A=low risk, B=moderate, C=high, D=very high.
diversification_score: 1-10 integer.
Do not give buy/sell advice."""

    raw = None

    def _call_local():
        try:
            r = requests.post(
                f"{LOCAL_LLM_URL}/chat/completions",
                json={"model": LOCAL_LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 1500, "temperature": 0.2},
                timeout=120,
            )
            if r.status_code == 200:
                return strip_think_tags(r.json()["choices"][0]["message"]["content"])
        except Exception:
            pass
        return None

    def _call_openai():
        if not openai_client:
            return None
        try:
            r = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600, temperature=0.2,
            )
            return r.choices[0].message.content
        except Exception:
            return None

    for provider in LLM_PROVIDER_ORDER:
        if provider == "local":
            raw = _call_local()
        elif provider == "openai":
            raw = _call_openai()
        if raw:
            break

    if not raw:
        return {"risk_grade": "N/A", "diversification_score": 0, "summary": "LLM unavailable.",
                "rebalancing_actions": [], "scenarios": {}}

    try:
        cleaned = extract_json_from_llm(raw) or raw
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        result = json.loads(cleaned[start:end]) if start >= 0 else {}
    except Exception:
        result = {"summary": raw[:500]}
    return result


def _assert_portfolio_ownership(portfolio_id: str, user_id: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM portfolios WHERE id = :pid AND user_id = :uid"),
            {"pid": portfolio_id, "uid": user_id},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")


@app.post("/api/portfolios/{portfolio_id}/suggest")
async def suggest_portfolio(
    portfolio_id: str,
    payload: SuggestPortfolioRequest,
    current_user: dict = Depends(get_current_user),
):
    """AI-powered portfolio builder: LLM picks stocks, PyPortfolioOpt optimises weights."""
    if not PYPFOPT_AVAILABLE:
        raise HTTPException(status_code=501, detail="PyPortfolioOpt not installed")
    _assert_portfolio_ownership(portfolio_id, current_user["id"])

    markets = [m.upper() for m in (payload.markets or ["AU"])]
    risk_profile = (payload.risk_profile or "balanced").lower()
    num_stocks = min(max(int(payload.num_stocks), 3), 15)

    # Build candidate pool from requested markets
    candidate_pool: dict = {}
    for m in markets:
        for sym, name in get_market_companies(m).items():
            clean = sym.replace(".NS", "") if m == "IN" else sym
            candidate_pool[clean] = {"name": name, "market": m}

    if not candidate_pool:
        raise HTTPException(status_code=400, detail="No companies found for the selected markets")

    # Ask LLM to select symbols matching risk profile
    pool_text = "\n".join(
        f"  {sym}: {info['name']} ({info['market']})"
        for sym, info in list(candidate_pool.items())[:50]
    )
    sectors_hint = f"Focus on: {', '.join(payload.sectors)}. " if payload.sectors else ""
    prompt = (
        f"You are a portfolio expert. Select {num_stocks} stocks for a {risk_profile} portfolio.\n"
        f"Risk profiles: conservative=low volatility, dividend payers, defensive; "
        f"balanced=mix growth+value, diversified; aggressive=high growth, tech, higher volatility.\n"
        f"{sectors_hint}\nAvailable stocks:\n{pool_text}\n"
        f"Return ONLY JSON: {{\"symbols\": [\"SYM1\", \"SYM2\", ...], \"rationale\": \"brief explanation\"}}\n"
        f"Select exactly {num_stocks} diverse symbols matching the {risk_profile} profile."
    )

    suggested_symbols: List[str] = []
    rationale = ""
    for provider in LLM_PROVIDER_ORDER:
        if suggested_symbols:
            break
        try:
            if provider == "local":
                r = requests.post(
                    f"{LOCAL_LLM_URL}/chat/completions",
                    json={"model": LOCAL_LLM_MODEL,
                          "messages": [{"role": "system", "content": "Output strict JSON only."},
                                       {"role": "user", "content": prompt}],
                          "max_tokens": 1000, "temperature": 0.2},
                    timeout=90,
                )
                if r.status_code == 200:
                    content = strip_think_tags(r.json()["choices"][0]["message"]["content"])
                    s = content.find("{"); e = content.rfind("}") + 1
                    if s >= 0:
                        parsed = json.loads(content[s:e])
                        suggested_symbols = parsed.get("symbols", [])
                        rationale = parsed.get("rationale", "")
            elif provider == "openai" and openai_client:
                r = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": "Output strict JSON only."},
                               {"role": "user", "content": prompt}],
                    max_tokens=400, temperature=0.2,
                )
                content = r.choices[0].message.content or "{}"
                s = content.find("{"); e = content.rfind("}") + 1
                if s >= 0:
                    parsed = json.loads(content[s:e])
                    suggested_symbols = parsed.get("symbols", [])
                    rationale = parsed.get("rationale", "")
        except Exception:
            pass

    # Validate and map to pool
    valid_symbols: List[str] = []
    symbol_markets: dict = {}
    for sym in suggested_symbols:
        key = sym.upper().strip()
        if key in candidate_pool and key not in valid_symbols:
            valid_symbols.append(key)
            symbol_markets[key] = candidate_pool[key]["market"]

    # Fallback: heuristic stock selection if LLM response is insufficient
    if len(valid_symbols) < 3:
        priority = {
            "conservative": {"AU": ["TLS", "WOW", "WES", "CSL", "CBA", "ANZ"],
                              "US": ["JNJ", "PG", "WMT", "V", "UNH"],
                              "IN": ["TCS", "INFY", "HINDUNILVR", "NTPC", "POWERGRID"]},
            "aggressive":   {"AU": ["DRO", "MIN", "FMG", "BHP", "REA", "SEK"],
                              "US": ["NVDA", "TSLA", "META", "GOOGL", "AMZN"],
                              "IN": ["BAJFINANCE", "TITAN", "MARUTI", "RELIANCE"]},
            "balanced":     {"AU": ["CSL", "BHP", "CBA", "WES", "TLS", "WOW"],
                              "US": ["AAPL", "MSFT", "JPM", "V", "JNJ"],
                              "IN": ["TCS", "INFY", "HDFCBANK", "RELIANCE", "TITAN"]},
        }
        for m in markets:
            for sym in priority.get(risk_profile, priority["balanced"]).get(m, []):
                if sym in candidate_pool and sym not in valid_symbols:
                    valid_symbols.append(sym)
                    symbol_markets[sym] = candidate_pool[sym]["market"]
                if len(valid_symbols) >= num_stocks:
                    break
        valid_symbols = valid_symbols[:num_stocks]

    # Fetch 1-year price history
    prices_data: dict = {}
    for sym in valid_symbols:
        ticker_str = format_ticker(sym, symbol_markets.get(sym, "AU"))
        try:
            hist = yf.Ticker(ticker_str).history(period="1y")["Close"]
            if len(hist) >= 60:
                prices_data[sym] = hist
        except Exception:
            pass

    if len(prices_data) < 2:
        raise HTTPException(
            status_code=400,
            detail="Not enough price history for optimisation. Try different markets or reduce stock count.",
        )

    df = pd.DataFrame(prices_data).dropna()
    if len(df) < 60:
        raise HTTPException(status_code=400, detail="Insufficient price data for optimisation")

    mu = expected_returns.mean_historical_return(df)
    
    try:
        # HRP does not invert the covariance matrix and is much more robust to noise
        hrp = HRPOpt(df.pct_change().dropna())
        hrp.optimize()
        raw_weights = hrp.clean_weights()
        
        # Enforce minimum position size (5%)
        # Filter out anything below 5% and re-normalize to reduce excessive small trades
        min_weight = 0.05
        filtered_weights = {k: v for k, v in raw_weights.items() if v >= min_weight}
        
        if not filtered_weights:
            symbols = df.columns.tolist()
            w = 1.0 / len(symbols)
            cleaned_weights = {s: w for s in symbols}
        else:
            total_w = sum(filtered_weights.values())
            cleaned_weights = {k: v / total_w for k, v in filtered_weights.items()}

        # Estimate impact of transaction costs on return (approx 0.2% penalty per position on a standard $10k portfolio)
        txn_penalty = len(cleaned_weights) * 0.002

        # ── Sector Concentration Cap (ASX is heavily financials/materials) ───
        # Cap any sector at 40% of portfolio to avoid single-sector blowup.
        sector_weights = {}
        ASX_SECTOR_MAP = {
            "BHP": "Materials", "RIO": "Materials", "FMG": "Materials", "WDS": "Energy",
            "CBA": "Financials", "NAB": "Financials", "WBC": "Financials", "ANZ": "Financials",
            "MQG": "Financials", "CSL": "Healthcare", "WES": "Industrials", "TLS": "Telecom",
            "WOW": "Consumer", "COL": "Consumer", "GMG": "Real Estate", "TCL": "Industrials",
        }
        MAX_SECTOR_WEIGHT = 0.40
        for sym, w in list(cleaned_weights.items()):
            sector = ASX_SECTOR_MAP.get(sym, "Other")
            sector_weights[sector] = sector_weights.get(sector, 0.0) + w
        # If any sector exceeds 40%, scale all weights in that sector down proportionally
        overflow_sectors = {s: v for s, v in sector_weights.items() if v > MAX_SECTOR_WEIGHT}
        if overflow_sectors:
            for sym, w in list(cleaned_weights.items()):
                sector = ASX_SECTOR_MAP.get(sym, "Other")
                if sector in overflow_sectors:
                    cleaned_weights[sym] = w * (MAX_SECTOR_WEIGHT / overflow_sectors[sector])
            total_w = sum(cleaned_weights.values())
            cleaned_weights = {k: v / total_w for k, v in cleaned_weights.items()}

        raw_perf = hrp.portfolio_performance(risk_free_rate=0.04)
        perf = (raw_perf[0] - txn_penalty, raw_perf[1], (raw_perf[0] - txn_penalty - 0.04) / max(raw_perf[1], 1e-6))
    except Exception:
        # Fallback to simple equal weight if optimization completely fails
        symbols = df.columns.tolist()
        w = 1.0 / len(symbols)
        cleaned_weights = {s: w for s in symbols}
        perf = (0.0, 0.0, 0.0)

    holdings_out = sorted(
        [
            {
                "symbol": sym,
                "market": symbol_markets.get(sym, "AU"),
                "name": candidate_pool.get(sym, {}).get("name", sym),
                "weight": round(float(w), 4),
                "suggested_quantity_pct": round(float(w) * 100, 2),
            }
            for sym, w in cleaned_weights.items()
            if w > 0.001
        ],
        key=lambda x: x["weight"],
        reverse=True,
    )
    return {
        "risk_profile": risk_profile,
        "markets": markets,
        "suggested_holdings": holdings_out,
        "expected_annual_return": round(float(perf[0]), 4),
        "annual_volatility": round(float(perf[1]), 4),
        "sharpe_ratio": round(float(perf[2]), 4),
        "rationale": rationale or f"Optimised {risk_profile} portfolio using Modern Portfolio Theory.",
    }


@app.post("/api/portfolios/ai-build", status_code=201)
async def ai_build_portfolio(
    payload: AiBuildPortfolioRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a named AI portfolio, optimise weights, and populate holdings in one step."""
    if not PYPFOPT_AVAILABLE:
        raise HTTPException(status_code=501, detail="PyPortfolioOpt not installed")

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Portfolio name is required")

    markets = [m.upper() for m in (payload.markets or ["AU"])]
    risk_profile = (payload.risk_profile or "balanced").lower()
    num_stocks = min(max(int(payload.num_stocks), 3), 15)
    total_investment = max(float(payload.total_investment or 10000.0), 1.0)

    # ── Reuse suggest logic ──────────────────────────────────────────────────
    candidate_pool: dict = {}
    for m in markets:
        for sym, cname in get_market_companies(m).items():
            clean = sym.replace(".NS", "") if m == "IN" else sym
            candidate_pool[clean] = {"name": cname, "market": m}

    if not candidate_pool:
        raise HTTPException(status_code=400, detail="No companies found for the selected markets")

    pool_text = "\n".join(
        f"  {sym}: {info['name']} ({info['market']})"
        for sym, info in list(candidate_pool.items())[:50]
    )
    sectors_hint = f"Focus on: {', '.join(payload.sectors)}. " if payload.sectors else ""
    prompt = (
        f"You are a portfolio expert. Select {num_stocks} stocks for a {risk_profile} portfolio.\n"
        f"Risk: conservative=defensive/dividends; balanced=growth+value; aggressive=high-growth/tech.\n"
        f"{sectors_hint}\nAvailable stocks:\n{pool_text}\n"
        f"Return ONLY JSON: {{\"symbols\": [...], \"rationale\": \"brief explanation\"}}"
    )

    suggested_symbols: List[str] = []
    rationale = ""
    for provider in LLM_PROVIDER_ORDER:
        if suggested_symbols:
            break
        try:
            if provider == "local":
                r = requests.post(
                    f"{LOCAL_LLM_URL}/chat/completions",
                    json={"model": LOCAL_LLM_MODEL,
                          "messages": [{"role": "system", "content": "Output strict JSON only."},
                                       {"role": "user", "content": prompt}],
                          "max_tokens": 1000, "temperature": 0.2},
                    timeout=90,
                )
                if r.status_code == 200:
                    content = strip_think_tags(r.json()["choices"][0]["message"]["content"])
                    s = content.find("{"); e = content.rfind("}") + 1
                    if s >= 0:
                        parsed = json.loads(content[s:e])
                        suggested_symbols = parsed.get("symbols", [])
                        rationale = parsed.get("rationale", "")
            elif provider == "openai" and openai_client:
                r = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": "Output strict JSON only."},
                               {"role": "user", "content": prompt}],
                    max_tokens=400, temperature=0.2,
                )
                content = r.choices[0].message.content or "{}"
                s = content.find("{"); e = content.rfind("}") + 1
                if s >= 0:
                    parsed = json.loads(content[s:e])
                    suggested_symbols = parsed.get("symbols", [])
                    rationale = parsed.get("rationale", "")
        except Exception:
            pass

    valid_symbols: List[str] = []
    symbol_markets: dict = {}
    for sym in suggested_symbols:
        key = sym.upper().strip()
        if key in candidate_pool and key not in valid_symbols:
            valid_symbols.append(key)
            symbol_markets[key] = candidate_pool[key]["market"]

    if len(valid_symbols) < 3:
        priority = {
            "conservative": {"AU": ["TLS", "WOW", "WES", "CSL", "CBA", "ANZ"],
                              "US": ["JNJ", "PG", "WMT", "V", "UNH"],
                              "IN": ["TCS", "INFY", "HINDUNILVR", "NTPC", "POWERGRID"]},
            "aggressive":   {"AU": ["DRO", "MIN", "FMG", "BHP", "REA", "SEK"],
                              "US": ["NVDA", "TSLA", "META", "GOOGL", "AMZN"],
                              "IN": ["BAJFINANCE", "TITAN", "MARUTI", "RELIANCE"]},
            "balanced":     {"AU": ["CSL", "BHP", "CBA", "WES", "TLS", "WOW"],
                              "US": ["AAPL", "MSFT", "JPM", "V", "JNJ"],
                              "IN": ["TCS", "INFY", "HDFCBANK", "RELIANCE", "TITAN"]},
        }
        for m in markets:
            for sym in priority.get(risk_profile, priority["balanced"]).get(m, []):
                if sym in candidate_pool and sym not in valid_symbols:
                    valid_symbols.append(sym)
                    symbol_markets[sym] = candidate_pool[sym]["market"]
                if len(valid_symbols) >= num_stocks:
                    break
        valid_symbols = valid_symbols[:num_stocks]

    prices_data: dict = {}
    current_prices: dict = {}
    for sym in valid_symbols:
        ticker_str = format_ticker(sym, symbol_markets.get(sym, "AU"))
        try:
            tk = yf.Ticker(ticker_str)
            hist = tk.history(period="1y")["Close"]
            if len(hist) >= 60:
                prices_data[sym] = hist
                current_prices[sym] = float(hist.iloc[-1])
        except Exception:
            pass

    if len(prices_data) < 2:
        raise HTTPException(status_code=400, detail="Not enough price history. Try different markets or fewer stocks.")

    df = pd.DataFrame(prices_data).dropna()
    if len(df) < 60:
        raise HTTPException(status_code=400, detail="Insufficient price data for optimisation")

    mu = expected_returns.mean_historical_return(df)
    S = risk_models.sample_cov(df)
    try:
        ef = EfficientFrontier(mu, S)
        if risk_profile == "conservative":
            ef.min_volatility()
        elif risk_profile == "aggressive":
            ef.max_quadratic_utility(risk_aversion=0.5)
        else:
            ef.max_sharpe(risk_free_rate=0.04)
        cleaned_weights = ef.clean_weights()
        perf = ef.portfolio_performance(risk_free_rate=0.04)
    except Exception:
        ef2 = EfficientFrontier(mu, S)
        ef2.max_sharpe(risk_free_rate=0.04)
        cleaned_weights = ef2.clean_weights()
        perf = ef2.portfolio_performance(risk_free_rate=0.04)

    # ── Create portfolio ─────────────────────────────────────────────────────
    pid = str(uuid4())
    with db_conn() as conn:
        conn.execute(
            text("INSERT INTO portfolios (id, user_id, name, type) VALUES (:id, :uid, :name, 'ai')"),
            {"id": pid, "uid": current_user["id"], "name": name},
        )
        # Add holdings based on optimal weights × total investment
        holdings_created = []
        for sym, weight in cleaned_weights.items():
            if float(weight) <= 0.001:
                continue
            mkt = symbol_markets.get(sym, "AU")
            cp = current_prices.get(sym, 1.0)
            alloc = float(weight) * total_investment
            qty = round(alloc / cp, 4) if cp > 0 else 0
            if qty <= 0:
                continue
            hid = str(uuid4())
            conn.execute(
                text(
                    "INSERT INTO portfolio_holdings (id, portfolio_id, symbol, market, quantity, avg_buy_price) "
                    "VALUES (:id, :pid, :sym, :market, :qty, :avg)"
                ),
                {"id": hid, "pid": pid, "sym": sym, "market": mkt,
                 "qty": qty, "avg": round(cp, 4)},
            )
            holdings_created.append({
                "symbol": sym,
                "market": mkt,
                "name": candidate_pool.get(sym, {}).get("name", sym),
                "weight_pct": round(float(weight) * 100, 2),
                "allocated": round(alloc, 2),
                "quantity": qty,
                "avg_buy_price": round(cp, 4),
            })

    return {
        "portfolio_id": pid,
        "portfolio_name": name,
        "portfolio_type": "ai",
        "risk_profile": risk_profile,
        "markets": markets,
        "total_investment": total_investment,
        "holdings": sorted(holdings_created, key=lambda x: x["weight_pct"], reverse=True),
        "expected_annual_return": round(float(perf[0]), 4),
        "annual_volatility": round(float(perf[1]), 4),
        "sharpe_ratio": round(float(perf[2]), 4),
        "rationale": rationale or f"AI-built {risk_profile} portfolio optimised with Modern Portfolio Theory.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: Weekly Predictions, Crypto Dashboard, ETF Explorer, AI Suggestions
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from pycoingecko import CoinGeckoAPI
    cg = CoinGeckoAPI()
    COINGECKO_AVAILABLE = True
except ImportError:
    cg = None
    COINGECKO_AVAILABLE = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False

import time as _time

# ── Cache layer ───────────────────────────────────────────────────────────────
_cache: dict = {}
_cache_lock = threading.Lock()

def cache_get(key: str, ttl_seconds: int = 300):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (_time.time() - entry["ts"]) < ttl_seconds:
            return entry["data"]
    return None

def cache_set(key: str, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": _time.time()}

# ── New DB tables ─────────────────────────────────────────────────────────────
def init_phase6_tables():
    with db_conn() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS weekly_digests (
                id TEXT PRIMARY KEY,
                week_iso VARCHAR(10) NOT NULL,
                market VARCHAR(5) NOT NULL,
                category_type VARCHAR(20) NOT NULL,
                category VARCHAR(50) NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                picks TEXT NOT NULL,
                ai_summary TEXT,
                regime VARCHAR(30),
                UNIQUE(week_iso, market, category_type, category)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crypto_watchlist (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                coin_id VARCHAR(100) NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                name VARCHAR(200),
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                UNIQUE(user_id, coin_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS weekly_pick_evaluations (
                id TEXT PRIMARY KEY,
                digest_id TEXT NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                predicted_change_pct REAL,
                actual_change_7d_pct REAL,
                direction_correct INTEGER,
                evaluated_at TIMESTAMP,
                UNIQUE(digest_id, symbol),
                FOREIGN KEY (digest_id) REFERENCES weekly_digests(id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wealth_scan_cache (
                id TEXT PRIMARY KEY,
                market VARCHAR(5) NOT NULL,
                scan_mode VARCHAR(20) NOT NULL DEFAULT 'broad',
                scanned_count INTEGER NOT NULL DEFAULT 0,
                candidates_found INTEGER NOT NULL DEFAULT 0,
                picks TEXT NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wealth_scan_history (
                id TEXT PRIMARY KEY,
                market VARCHAR(5) NOT NULL,
                scan_mode VARCHAR(20) NOT NULL DEFAULT 'broad',
                scanned_count INTEGER NOT NULL DEFAULT 0,
                candidates_found INTEGER NOT NULL DEFAULT 0,
                picks TEXT NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wealth_builder_evaluations (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                market VARCHAR(5) NOT NULL DEFAULT 'AU',
                wealth_rank NUMERIC(10, 4),
                score NUMERIC(6, 2),
                prob_ge_5pct NUMERIC(6, 2),
                predicted_change_pct NUMERIC(8, 2),
                price_at_screen NUMERIC(12, 4),
                actual_return_14d NUMERIC(8, 2),
                actual_return_30d NUMERIC(8, 2),
                actual_return_63d NUMERIC(8, 2),
                actual_return_90d NUMERIC(8, 2),
                actual_peak_return_90d NUMERIC(8, 2),
                actual_max_drawdown_90d NUMERIC(8, 2),
                entry_zone VARCHAR(10),
                target_tier VARCHAR(10),
                model_score NUMERIC(10, 2),
                screened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                evaluated BOOLEAN DEFAULT FALSE,
                UNIQUE(symbol, screened_at)
            )
        """))


        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS eod_ohl_history (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                market VARCHAR(5) NOT NULL DEFAULT 'AU',
                trade_date DATE NOT NULL,
                open NUMERIC(12, 4),
                high NUMERIC(12, 4),
                low NUMERIC(12, 4),
                close NUMERIC(12, 4),
                volume BIGINT,
                UNIQUE(symbol, market, trade_date)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_eod_ohl_symbol_date
            ON eod_ohl_history(symbol, market, trade_date)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS model_training_set (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                market VARCHAR(5) NOT NULL DEFAULT 'AU',
                signal_date DATE NOT NULL,
                entry_price NUMERIC(12, 4),
                features JSONB,
                forward_return_63d NUMERIC(8, 2),
                forward_peak_return_63d NUMERIC(8, 2),
                forward_max_drawdown_63d NUMERIC(8, 2),
                hit_3pct BOOLEAN,
                direction_correct BOOLEAN,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, market, signal_date)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_mts_signal_date
            ON model_training_set(signal_date)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS model_weights_by_date (
                id SERIAL PRIMARY KEY,
                trained_at DATE NOT NULL,
                feature_name VARCHAR(100) NOT NULL,
                weight NUMERIC(12, 6),
                coefficient NUMERIC(12, 6),
                standard_error NUMERIC(12, 6),
                significance_pct NUMERIC(8, 2),
                model_type VARCHAR(20) DEFAULT 'logistic',
                sample_size INTEGER,
                in_sample_hit_rate NUMERIC(6, 2),
                notes TEXT,
                UNIQUE(trained_at, feature_name)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS channel_hit_rates (
                id SERIAL PRIMARY KEY,
                calibrated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                momentum NUMERIC(6, 4),
                institutional NUMERIC(6, 4),
                options NUMERIC(6, 4),
                fundamental NUMERIC(6, 4),
                cross_asset NUMERIC(6, 4),
                sample_size INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS daily_ai_runs (
                id SERIAL PRIMARY KEY,
                run_date DATE NOT NULL UNIQUE,
                total_candidates INTEGER,
                ai_approved INTEGER,
                ai_rejected INTEGER,
                approval_rate_pct NUMERIC(6, 2),
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id SERIAL PRIMARY KEY,
                job_name VARCHAR(100) NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                status VARCHAR(20) DEFAULT 'running',
                error TEXT,
                rows_affected INTEGER,
                duration_seconds NUMERIC(8, 2)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_job_runs_started
            ON job_runs(started_at DESC)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                migration_name VARCHAR(200) NOT NULL UNIQUE,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ai_self_learning_metrics (
                id SERIAL PRIMARY KEY,
                evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_trades INTEGER,
                win_count INTEGER,
                loss_count INTEGER,
                win_rate_pct NUMERIC(6, 2),
                avg_win_pct NUMERIC(6, 2),
                avg_loss_pct NUMERIC(6, 2),
                recommended_action TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fundamental_snapshots (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                market VARCHAR(5) NOT NULL DEFAULT 'AU',
                snapshot_date DATE NOT NULL,
                trailing_pe NUMERIC(12, 2),
                forward_pe NUMERIC(12, 2),
                market_cap NUMERIC(20, 2),
                dividend_yield NUMERIC(8, 2),
                analyst_target_mean NUMERIC(12, 2),
                analyst_rec VARCHAR(20),
                revenue_growth NUMERIC(8, 2),
                earnings_growth NUMERIC(8, 2),
                price_to_book NUMERIC(8, 2),
                beta NUMERIC(6, 2),
                high_52w NUMERIC(12, 2),
                low_52w NUMERIC(12, 2),
                avg_volume BIGINT,
                shares_outstanding BIGINT,
                UNIQUE(symbol, snapshot_date)
            )
        """))
        # Historical fundamental ratios (yfinance income/balance/cashflow, 4yr)
        try:
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
        except Exception:
            pass

try:
    init_phase6_tables()
except Exception:
    pass

# ── Weekly Universe ───────────────────────────────────────────────────────────
WEEKLY_UNIVERSE = {
    "AU": {
        "large_cap": [
            "BHP", "CBA", "CSL", "NAB", "WBC", "ANZ", "WES", "MQG",
            "FMG", "WDS", "TLS", "WOW", "RIO", "COL", "GMG",
            "TCL", "ALL", "QBE", "STO", "REA", "SUN", "ORG", "WTC",
        ],
        "mid_cap": [
            "XRO", "PME", "COH", "RMD", "JBH", "MIN", "APA",
            "TWE", "SGP", "HVN", "BXB", "DOW",
            "TNE", "SUL", "WHC", "ILU", "EVN", "NST",
            "ASX", "QAN", "ALD", "BSL", "BPT", "AZJ", "BOQ", "CIA",
            "GQG", "HUB", "MFG", "WOR", "AGL", "OZL", "PPT",
        ],
        "small_cap": [
            "LYC", "PLS", "SYR", "IGO",
            "LTR", "CXO", "BRN", "NVX", "VUL",
            "RMS", "WAF", "SBM", "PRU", "DRO",
            "APX", "BTH", "EOS", "EML",
            "HLS", "MP1", "Z1P", "NXT", "TLX",
            "IMU", "PNV", "CLW", "SCP", "SHL",
            "GMA", "CUV", "IMD", "IDX", "CWY",
            "DHG", "IEL", "GNC", "ELD", "ING",
            "NUF", "OML", "CAR", "REH", "ARB",
        ],
    },
    "US": {
        "large_cap": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B", "JPM", "V"],
        "mid_cap": ["CRWD", "DDOG", "NET", "SNOW", "PANW", "ZS", "MELI", "SHOP", "SQ", "COIN"],
        "small_cap": ["SOFI", "PLTR", "RKLB", "IONQ", "APP", "SMCI", "UPST", "AFRM", "HIMS", "DUOL"],
    },
    "IN": {
        "large_cap": [
            "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "BHARTIARTL",
            "HINDUNILVR", "ITC", "SBIN", "LT", "KOTAKBANK", "BAJFINANCE",
            "MARUTI", "AXISBANK", "HCLTECH", "SUNPHARMA", "TITAN", "WIPRO",
            "ADANIENT", "NTPC",
        ],
        "mid_cap": [
            "TRENT", "PERSISTENT", "COFORGE", "POLYCAB", "TATAELXSI", "PIIND",
            "HAL", "BEL", "PFC", "IRFC", "SIEMENS", "ABB", "DIXON", "MPHASIS",
            "LTIM", "GODREJCP", "CONCOR", "SBILIFE", "VEDL", "TATAPOWER",
        ],
        "small_cap": [
            "SUZLON", "RVNL", "IRCON", "NHPC", "YESBANK", "ZOMATO",
            "PAYTM", "JSWENERGY", "CANBK", "FEDERALBNK", "DEEPAKNTR", "SRF",
            "AUROPHARMA", "BIOCON", "LUPIN", "MRF", "TVSMOTOR", "BATAINDIA",
            "PRAJIND", "ESCORTS",
        ],
    },
}

SECTOR_MAPPING = {
    "Energy": {
        "AU": ["WDS", "STO", "ORG", "WHC", "BPT"],
        "US": ["XOM", "CVX", "COP", "SLB", "EOG"],
        "IN": ["RELIANCE", "NTPC", "POWERGRID", "ADANIGREEN", "TATAPOWER"],
    },
    "Technology": {
        "AU": ["XRO", "WTC", "TNE", "MP1", "PME"],
        "US": ["AAPL", "MSFT", "NVDA", "CRM", "ADBE"],
        "IN": ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM"],
    },
    "Healthcare": {
        "AU": ["CSL", "COH", "RMD", "PME", "SHL"],
        "US": ["UNH", "JNJ", "LLY", "PFE", "ABBV"],
        "IN": ["SUNPHARMA", "CIPLA", "DRREDDY", "AUROPHARMA", "BIOCON"],
    },
    "Financials": {
        "AU": ["CBA", "NAB", "ANZ", "WBC", "MQG"],
        "US": ["JPM", "BAC", "GS", "MS", "V"],
        "IN": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK"],
    },
    "Materials": {
        "AU": ["BHP", "RIO", "FMG", "MIN", "S32"],
        "US": ["LIN", "APD", "ECL", "NEM", "FCX"],
        "IN": ["TATASTEEL", "HINDALCO", "JSWSTEEL", "VEDL", "COALINDIA"],
    },
    "Real Estate": {
        "AU": ["GMG", "SCG", "GPT", "CLW", "MGR"],
        "US": ["PLD", "AMT", "EQIX", "SPG", "O"],
        "IN": ["DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "BRIGADE"],
    },
    "Consumer": {
        "AU": ["WES", "WOW", "COL", "JBH", "HVN"],
        "US": ["AMZN", "TSLA", "HD", "NKE", "SBUX"],
        "IN": ["HINDUNILVR", "ITC", "TITAN", "TRENT", "BATAINDIA"],
    },
    "Industrials": {
        "AU": ["TCL", "BXB", "DOW", "QAN", "AZJ"],
        "US": ["CAT", "DE", "UPS", "BA", "GE"],
        "IN": ["LT", "SIEMENS", "ABB", "HAL", "BEL"],
    },
    "Automobile": {
        "AU": [],
        "US": ["TSLA", "F", "GM", "RIVN", "LCID"],
        "IN": ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "HEROMOTOCO"],
    },
    "Utilities": {
        "AU": ["APA", "AGL", "ORG"],
        "US": ["NEE", "DUK", "SO", "D", "AEP"],
        "IN": ["NTPC", "POWERGRID", "TATAPOWER", "ADANIGREEN", "NHPC"],
    },
}

# ── ETF Universe ──────────────────────────────────────────────────────────────
ETF_UNIVERSE = {
    "AU_broad": {
        "VAS.AX": "Vanguard Australian Shares Index",
        "A200.AX": "Betashares ASX 200",
        "IOZ.AX": "iShares Core S&P/ASX 200",
        "STW.AX": "SPDR S&P/ASX 200",
        "VLC.AX": "Vanguard Large Cap",
        "VSO.AX": "Vanguard Small Companies",
        "MVW.AX": "VanEck Equal Weight",
    },
    "AU_international": {
        "VGS.AX": "Vanguard MSCI International",
        "IVV.AX": "iShares S&P 500",
        "NDQ.AX": "Betashares NASDAQ 100",
        "VDHG.AX": "Vanguard Diversified High Growth",
        "DHHF.AX": "Betashares Diversified All Growth",
        "HACK.AX": "Betashares Global Cybersecurity",
        "ASIA.AX": "Betashares Asia Technology Tigers",
    },
    "AU_thematic": {
        "ATEC.AX": "Betashares S&P/ASX Technology",
        "CLNE.AX": "Betashares Climate Innovation",
        "ACDC.AX": "Betashares Battery Tech & Lithium",
        "DRIV.AX": "Betashares Electric Vehicles",
        "RBTZ.AX": "Betashares Robotics & AI",
        "SEMI.AX": "Betashares Global Semiconductors",
        "BNKS.AX": "Betashares Global Banks",
    },
    "AU_income": {
        "VHY.AX": "Vanguard High Yield",
        "SYI.AX": "Betashares High Income",
        "REIT.AX": "Betashares Australian Property",
    },
    "US_major": {
        "SPY": "SPDR S&P 500",
        "QQQ": "Invesco NASDAQ 100",
        "VTI": "Vanguard Total Stock Market",
        "IWM": "iShares Russell 2000",
        "DIA": "SPDR Dow Jones",
        "ARKK": "ARK Innovation",
    },
    "US_sector": {
        "XLF": "Financial Select SPDR",
        "XLK": "Technology Select SPDR",
        "XLE": "Energy Select SPDR",
        "XLV": "Health Care Select SPDR",
        "XLI": "Industrial Select SPDR",
        "XLRE": "Real Estate Select SPDR",
        "XLP": "Consumer Staples SPDR",
    },
    "US_thematic": {
        "SOXX": "iShares Semiconductor",
        "BOTZ": "Global Robotics & AI",
        "TAN": "Invesco Solar",
        "LIT": "Global Lithium & Battery",
        "HACK": "ETFMG Prime Cyber Security",
    },
    "US_bond": {
        "BND": "Vanguard Total Bond",
        "TLT": "iShares 20+ Year Treasury",
        "HYG": "iShares High Yield Corporate",
        "AGG": "iShares Core U.S. Aggregate Bond",
    },
    "US_commodity": {
        "GLD": "SPDR Gold Trust",
        "SLV": "iShares Silver Trust",
        "USO": "United States Oil Fund",
    },
    "IN_broad": {
        "NIFTYBEES.NS": "Nippon Nifty 50 ETF",
        "BANKBEES.NS": "Nippon Bank Nifty ETF",
        "JUNIORBEES.NS": "Nippon Nifty Next 50 ETF",
        "SETFNIF50.NS": "SBI Nifty 50 ETF",
        "ICICNIFTY.NS": "ICICI Nifty 50 ETF",
    },
    "IN_sector": {
        "SETFNIFBK.NS": "SBI Nifty Bank ETF",
        "ITBEES.NS": "Nippon Nifty IT ETF",
        "PHARMABEES.NS": "Nippon Nifty Pharma ETF",
        "INFRABEES.NS": "Nippon Nifty Infra ETF",
        "PSUBNKBEES.NS": "Nippon Nifty PSU Bank ETF",
    },
    "IN_gold": {
        "GOLDBEES.NS": "Nippon Gold ETF",
        "GOLDSHARE.NS": "UTI Gold ETF",
    },
    "IN_thematic": {
        "MOM50.NS": "Motilal Oswal Midcap 50 ETF",
        "MON100.NS": "Motilal Oswal NASDAQ 100 ETF",
    },
}

# ── Crypto categories ─────────────────────────────────────────────────────────
CRYPTO_CATEGORIES = {
    "blue_chip": ["bitcoin", "ethereum", "solana", "cardano", "avalanche-2"],
    "layer2": ["polygon-ecosystem-token", "arbitrum", "optimism", "starknet"],
    "defi": ["uniswap", "aave", "maker", "lido-dao", "curve-dao-token"],
    "ai_tokens": ["render-token", "fetch-ai", "ocean-protocol", "singularitynet", "akash-network"],
    "meme": ["dogecoin", "shiba-inu", "pepe", "bonk", "floki"],
    "gaming": ["immutable-x", "the-sandbox", "axie-infinity", "gala"],
    "infrastructure": ["chainlink", "polkadot", "cosmos", "near", "internet-computer"],
    "exchange": ["binancecoin", "crypto-com-chain", "okb"],
}

# ── Weekly helpers ────────────────────────────────────────────────────────────
def get_current_iso_week() -> str:
    now = datetime.utcnow()
    return f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"


def score_and_rank(symbols: list, market: str) -> list:
    """Score a batch of symbols using existing ensemble engine with explicit market."""
    results = []
    for sym in symbols:
        try:
            # Pass market explicitly so symbols not in ASX/US/IN dicts work correctly
            stock_data = get_stock_data(sym, market=market)
            hist = get_historical_data(sym, period="1y", market=market)
            if len(hist) < 60:
                continue

            current_price = stock_data["current_price"] or float(hist["Close"].iloc[-1])
            if not current_price:
                continue

            indicators = calculate_technical_indicators(hist)
            prediction = generate_statistical_prediction(hist, current_price)

            mu = prediction["change_from_current"] / 100.0
            daily_vol = float(hist["Close"].pct_change().dropna().std())
            sigma_63 = max(daily_vol * math.sqrt(63), 1e-6)
            z = (0.03 - mu) / sigma_63
            prob_ge_5pct = max(0.0, min(1.0, 1.0 - std_norm_cdf(z)))
            prob_ge_5pct = _blend_empirical_prob(sym, prob_ge_5pct)

            trend = prediction["trend"]
            trend_score = 0.9 if trend == "bullish" else 0.55 if trend == "neutral" else 0.2

            # HACOLT confirmation multiplier (same logic as primary engine)
            hacolt = indicators.get("hacolt", 50.0)
            if hacolt == 100.0:
                trend_score = min(1.0, trend_score * 1.10) if trend == "bullish" else trend_score * 0.85
            elif hacolt == 0.0:
                trend_score = min(1.0, trend_score * 1.10) if trend == "bearish" else trend_score * 0.70

            rsi = indicators.get("rsi", 50)
            momentum = indicators.get("momentum_20", 0)
            quality_score = max(0.0, min(1.0, (1 - abs(rsi - 55) / 55) * 0.6 + (0.5 + momentum / 40) * 0.4))
            regime_fit = 0.65 if trend == "bullish" else 0.45 if trend == "neutral" else 0.3
            avg_vol = float(hist["Volume"].tail(20).mean()) if "Volume" in hist else 1_000_000
            liquidity_score = max(0.1, min(1.0, avg_vol / 8_000_000))

            score = (
                0.45 * prob_ge_5pct
                + 0.20 * trend_score
                + 0.15 * quality_score
                + 0.10 * regime_fit
                + 0.10 * liquidity_score
            ) * 100

            # ── Layer 1 Confluence ──────────────────────────────────────────
            try:
                regime_snap = compute_regime_snapshot()
            except Exception:
                regime_snap = {}
            valuation = get_valuation_metrics(sym)
            confluence = bullish_confluence_score(
                sym, market, indicators, prediction, stock_data, hist, valuation, regime_snap
            )
            # Boost score by confluence multiplier if ≥3 channels bullish
            if confluence["confidence"] == "high" and confluence["bullish_channels"] >= 4:
                score = score * 1.12
            elif confluence["confidence"] == "medium" and confluence["bullish_channels"] >= 3:
                score = score * 1.05

            results.append({
                "symbol": sym,
                "name": stock_data.get("name", sym),
                "current_price": round(float(current_price), 2),
                "predicted_price_3m": prediction.get("predicted_price"),
                "expected_return_3m_pct": round(prediction["change_from_current"], 2),
                "prob_ge_5pct": round(prob_ge_5pct * 100, 2),
                "trend": trend,
                "score": round(score, 2),
                "market": market,
                "cap_tier": None,
                "confluence": {
                    "confidence": confluence["confidence"],
                    "bullish_channels": confluence["bullish_channels"],
                },
            })
        except Exception:
            continue
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def get_llm_pick_summary(pick: dict, sector: str = "", cap_tier: str = "") -> str:
    """Get a 2-3 sentence LLM summary for a weekly pick."""
    prompt = (
        f"You are a financial data analyst. Do not give buy/sell advice.\n"
        f"Stock: {pick['symbol']} ({pick['name']})\n"
        f"Sector: {sector} | Market Cap Tier: {cap_tier}\n"
        f"Current Price: ${pick['current_price']} | Score: {pick['score']}/100\n"
        f"Expected Return 3m: {pick['expected_return_3m_pct']}% | Trend: {pick['trend']}\n\n"
        f"In 2-3 sentences, explain why this stock ranks well this week. "
        f"Mention the key driver and primary risk."
    )
    for provider in LLM_PROVIDER_ORDER:
        try:
            if provider == "local":
                r = requests.post(
                    f"{LOCAL_LLM_URL}/chat/completions",
                    json={"model": LOCAL_LLM_MODEL,
                          "messages": [{"role": "system", "content": "Financial analyst. Brief factual output."},
                                       {"role": "user", "content": prompt}],
                          "max_tokens": 500, "temperature": 0.3},
                    timeout=60,
                )
                if r.status_code == 200:
                    return strip_think_tags(r.json()["choices"][0]["message"]["content"]).strip()
            elif provider == "openai" and openai_client:
                r = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": "Financial analyst. Brief factual output."},
                               {"role": "user", "content": prompt}],
                    max_tokens=150, temperature=0.3,
                )
                return (r.choices[0].message.content or "").strip()
        except Exception:
            continue
    return f"{pick['symbol']} scores {pick['score']}/100 with {pick['trend']} trend."


def generate_weekly_digest_for_market(market: str) -> dict:
    """Generate all weekly picks for a given market."""
    week = get_current_iso_week()
    digest = {
        "week": week,
        "market": market,
        "generated_at": datetime.utcnow().isoformat(),
    }

    universe = WEEKLY_UNIVERSE.get(market, {})

    # By market cap tier
    for tier in ["large_cap", "mid_cap", "small_cap"]:
        symbols = universe.get(tier, [])
        if not symbols:
            continue
        scored = score_and_rank(symbols, market)
        top5 = scored[:5]
        for pick in top5:
            pick["llm_summary"] = get_llm_pick_summary(pick, cap_tier=tier)
        digest[f"stocks_{tier}"] = top5

        # Store to DB
        try:
            with db_conn() as conn:
                did = str(uuid4())
                conn.execute(text("""
                    INSERT INTO weekly_digests (id, week_iso, market, category_type, category, picks, regime)
                    VALUES (:id, :week, :market, 'market_cap', :cat, :picks, :regime)
                    ON CONFLICT (week_iso, market, category_type, category) DO UPDATE
                    SET picks = :picks, generated_at = CURRENT_TIMESTAMP, regime = :regime
                """), {"id": did, "week": week, "market": market, "cat": tier,
                       "picks": json.dumps(top5), "regime": ""})
        except Exception:
            pass

    # By sector
    for sector, tickers_by_market in SECTOR_MAPPING.items():
        symbols = tickers_by_market.get(market, [])
        if not symbols:
            continue
        scored = score_and_rank(symbols, market)
        top5 = scored[:5]
        for pick in top5:
            pick["llm_summary"] = get_llm_pick_summary(pick, sector=sector)
        digest[f"sector_{sector.lower().replace(' ', '_')}"] = top5

        try:
            with db_conn() as conn:
                did = str(uuid4())
                conn.execute(text("""
                    INSERT INTO weekly_digests (id, week_iso, market, category_type, category, picks, regime)
                    VALUES (:id, :week, :market, 'sector', :cat, :picks, :regime)
                    ON CONFLICT (week_iso, market, category_type, category) DO UPDATE
                    SET picks = :picks, generated_at = CURRENT_TIMESTAMP, regime = :regime
                """), {"id": did, "week": week, "market": market, "cat": sector,
                       "picks": json.dumps(top5), "regime": ""})
        except Exception:
            pass

    return digest


# ── Weekly Prediction Endpoints ───────────────────────────────────────────────

class WeeklyGenerateRequest(BaseModel):
    market: str = "AU"

@app.get("/api/weekly/latest")
async def weekly_latest(market: str = "AU", current_user: dict = Depends(get_current_user)):
    """Get latest weekly digest (cached, regenerated Mondays)."""
    del current_user
    m = (market or "AU").upper()
    week = get_current_iso_week()

    cached = cache_get(f"weekly_{m}_{week}", ttl_seconds=3600)
    if cached:
        return cached

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT category_type, category, picks, ai_summary, regime
            FROM weekly_digests WHERE week_iso = :week AND market = :market
            ORDER BY category_type, category
        """), {"week": week, "market": m}).fetchall()

    if not rows:
        return {"week": week, "market": m, "status": "not_generated",
                "message": "No picks generated yet for this week. Use POST /api/weekly/generate to create them."}

    result = {"week": week, "market": m, "picks_by_cap": {}, "picks_by_sector": {}}
    for row in rows:
        cat_type, cat, picks_json, summary, regime = row
        picks = json.loads(picks_json) if isinstance(picks_json, str) else picks_json
        if cat_type == "market_cap":
            result["picks_by_cap"][cat] = {"picks": picks, "ai_summary": summary}
        else:
            result["picks_by_sector"][cat] = {"picks": picks, "ai_summary": summary}

    cache_set(f"weekly_{m}_{week}", result)
    return result


@app.get("/api/weekly/picks")
async def weekly_picks(market: str = "AU", category: str = "market_cap", tier: str = None, sector: str = None, current_user: dict = Depends(get_current_user)):
    """Get top 5 for specific category/tier/sector."""
    del current_user
    m = (market or "AU").upper()
    week = get_current_iso_week()

    cat_type = category.lower()
    cat_value = tier if cat_type == "market_cap" else sector
    if not cat_value:
        raise HTTPException(status_code=400, detail="Specify 'tier' for market_cap or 'sector' for sector category")

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT picks, ai_summary FROM weekly_digests
            WHERE week_iso = :week AND market = :market AND category_type = :cat_type AND category = :cat
        """), {"week": week, "market": m, "cat_type": cat_type, "cat": cat_value}).fetchone()

    if not row:
        return {"week": week, "market": m, "category_type": cat_type, "category": cat_value, "picks": []}

    return {
        "week": week, "market": m, "category_type": cat_type, "category": cat_value,
        "picks": json.loads(row[0]) if isinstance(row[0], str) else row[0],
        "ai_summary": row[1],
    }


@app.post("/api/weekly/generate")
async def weekly_generate(payload: WeeklyGenerateRequest, current_user: dict = Depends(get_current_user)):
    """Force regeneration of weekly picks for a market."""
    del current_user
    m = (payload.market or "AU").upper()
    if m not in WEEKLY_UNIVERSE:
        raise HTTPException(status_code=400, detail=f"Unknown market: {m}. Use AU, US, or IN.")
    digest = generate_weekly_digest_for_market(m)
    return {"status": "generated", "market": m, "week": digest["week"],
            "cap_tiers": [k for k in digest if k.startswith("stocks_")],
            "sectors": [k for k in digest if k.startswith("sector_")]}


@app.get("/api/weekly/history")
async def weekly_history(market: str = "AU", weeks: int = 4, current_user: dict = Depends(get_current_user)):
    del current_user
    m = (market or "AU").upper()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT week_iso FROM weekly_digests
            WHERE market = :market ORDER BY week_iso DESC LIMIT :lim
        """), {"market": m, "lim": weeks}).fetchall()
    return {"market": m, "weeks": [r[0] for r in rows]}


@app.get("/api/weekly/accuracy")
async def weekly_accuracy(market: str = "AU", current_user: dict = Depends(get_current_user)):
    del current_user
    m = (market or "AU").upper()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT e.symbol, e.predicted_change_pct, e.actual_change_7d_pct, e.direction_correct
            FROM weekly_pick_evaluations e
            JOIN weekly_digests d ON d.id = e.digest_id
            WHERE d.market = :market AND e.actual_change_7d_pct IS NOT NULL
            ORDER BY e.evaluated_at DESC LIMIT 50
        """), {"market": m}).fetchall()

    if not rows:
        return {"market": m, "evaluations": [], "summary": {}}

    evals = [{"symbol": r[0], "predicted": r[1], "actual": r[2], "direction_correct": bool(r[3])} for r in rows]
    dir_acc = sum(1 for r in rows if r[3]) / len(rows) * 100
    return {
        "market": m,
        "evaluations": evals,
        "summary": {"total": len(evals), "direction_accuracy_pct": round(dir_acc, 2)},
    }


@app.get("/api/weekly/sectors")
async def weekly_sectors():
    """Return available sectors with per-market tickers."""
    return {"sectors": {k: list(v.keys()) for k, v in SECTOR_MAPPING.items()}}


# ── Crypto Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/crypto/market")
async def crypto_market(vs_currency: str = "usd", current_user: dict = Depends(get_current_user)):
    """Top 20 coins + global stats + fear & greed."""
    del current_user
    ck = f"crypto_market_{vs_currency}"
    cached = cache_get(ck, ttl_seconds=300)
    if cached:
        return cached

    result = {"coins": [], "global_stats": {}, "fear_greed": {}}

    # Top 20 from CoinGecko
    if COINGECKO_AVAILABLE:
        try:
            coins = cg.get_coins_markets(
                vs_currency=vs_currency, order="market_cap_desc",
                per_page=20, page=1, sparkline=True,
                price_change_percentage="24h,7d,30d",
            )
            result["coins"] = [
                {
                    "id": c["id"], "symbol": c["symbol"], "name": c["name"],
                    "image": c.get("image", ""),
                    "current_price": c.get("current_price"),
                    "market_cap": c.get("market_cap"),
                    "market_cap_rank": c.get("market_cap_rank"),
                    "total_volume": c.get("total_volume"),
                    "price_change_24h_pct": c.get("price_change_percentage_24h_in_currency"),
                    "price_change_7d_pct": c.get("price_change_percentage_7d_in_currency"),
                    "price_change_30d_pct": c.get("price_change_percentage_30d_in_currency"),
                    "sparkline_7d": c.get("sparkline_in_7d", {}).get("price", []),
                }
                for c in coins
            ]
        except Exception:
            pass

        try:
            g = cg.get_global()
            result["global_stats"] = {
                "total_market_cap": g.get("data", {}).get("total_market_cap", {}).get(vs_currency),
                "total_volume": g.get("data", {}).get("total_volume", {}).get(vs_currency),
                "btc_dominance": g.get("data", {}).get("market_cap_percentage", {}).get("btc"),
                "eth_dominance": g.get("data", {}).get("market_cap_percentage", {}).get("eth"),
                "active_cryptos": g.get("data", {}).get("active_cryptocurrencies"),
            }
        except Exception:
            pass
    else:
        # Fallback: use yfinance for major cryptos
        for sym, name in [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("SOL-USD", "Solana")]:
            try:
                tk = yf.Ticker(sym)
                h = tk.history(period="7d")
                if len(h) > 0:
                    result["coins"].append({
                        "id": name.lower(), "symbol": sym.split("-")[0].lower(), "name": name,
                        "current_price": round(float(h["Close"].iloc[-1]), 2),
                        "price_change_24h_pct": round(float(h["Close"].pct_change().iloc[-1] * 100), 2) if len(h) >= 2 else 0,
                    })
            except Exception:
                continue

    # Fear & Greed Index
    try:
        fng_url = os.getenv("FEAR_GREED_API_URL", "https://api.alternative.me/fng/")
        fng_resp = requests.get(fng_url, timeout=10)
        if fng_resp.status_code == 200:
            fng_data = fng_resp.json().get("data", [{}])[0]
            result["fear_greed"] = {
                "value": int(fng_data.get("value", 0)),
                "label": fng_data.get("value_classification", ""),
                "timestamp": fng_data.get("timestamp", ""),
            }
    except Exception:
        pass

    cache_set(ck, result)
    return result


@app.get("/api/crypto/search")
async def crypto_search(q: str, current_user: dict = Depends(get_current_user)):
    del current_user
    if not COINGECKO_AVAILABLE:
        raise HTTPException(status_code=501, detail="CoinGecko not available")
    try:
        results = cg.search(query=q)
        coins = results.get("coins", [])[:10]
        return {"results": [{"id": c["id"], "symbol": c["symbol"], "name": c["name"],
                             "market_cap_rank": c.get("market_cap_rank"),
                             "thumb": c.get("thumb", "")} for c in coins]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/crypto/{coin_id}")
async def crypto_detail(coin_id: str, vs_currency: str = "usd", current_user: dict = Depends(get_current_user)):
    del current_user
    ck = f"crypto_detail_{coin_id}_{vs_currency}"
    cached = cache_get(ck, ttl_seconds=600)
    if cached:
        return cached

    if not COINGECKO_AVAILABLE:
        raise HTTPException(status_code=501, detail="CoinGecko not available")

    try:
        coin = cg.get_coin_by_id(id=coin_id, localization=False, tickers=False,
                                  community_data=False, developer_data=False)
        md = coin.get("market_data", {})
        result = {
            "id": coin["id"], "symbol": coin["symbol"], "name": coin["name"],
            "description": (coin.get("description", {}).get("en", "") or "")[:500],
            "image": coin.get("image", {}).get("large", ""),
            "current_price": md.get("current_price", {}).get(vs_currency),
            "market_cap": md.get("market_cap", {}).get(vs_currency),
            "market_cap_rank": md.get("market_cap_rank"),
            "total_volume": md.get("total_volume", {}).get(vs_currency),
            "high_24h": md.get("high_24h", {}).get(vs_currency),
            "low_24h": md.get("low_24h", {}).get(vs_currency),
            "price_change_24h_pct": md.get("price_change_percentage_24h"),
            "price_change_7d_pct": md.get("price_change_percentage_7d"),
            "price_change_30d_pct": md.get("price_change_percentage_30d"),
            "ath": md.get("ath", {}).get(vs_currency),
            "ath_change_pct": md.get("ath_change_percentage", {}).get(vs_currency),
            "categories": coin.get("categories", []),
        }
        cache_set(ck, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/crypto/{coin_id}/chart")
async def crypto_chart(coin_id: str, days: int = 90, vs_currency: str = "usd", current_user: dict = Depends(get_current_user)):
    del current_user
    days = min(max(1, days), 365)
    ck = f"crypto_chart_{coin_id}_{days}_{vs_currency}"
    cached = cache_get(ck, ttl_seconds=600)
    if cached:
        return cached

    if not COINGECKO_AVAILABLE:
        raise HTTPException(status_code=501, detail="CoinGecko not available")

    try:
        data = cg.get_coin_market_chart_by_id(id=coin_id, vs_currency=vs_currency, days=days)
        prices = [{"ts": p[0], "price": p[1]} for p in data.get("prices", [])]
        volumes = [{"ts": v[0], "volume": v[1]} for v in data.get("total_volumes", [])]
        result = {"coin_id": coin_id, "days": days, "vs_currency": vs_currency,
                  "prices": prices, "volumes": volumes}
        cache_set(ck, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/crypto/{coin_id}/analyze")
async def crypto_analyze(coin_id: str, vs_currency: str = "usd", current_user: dict = Depends(get_current_user)):
    """LLM analysis of a specific coin."""
    del current_user
    # Get coin data first
    if not COINGECKO_AVAILABLE:
        raise HTTPException(status_code=501, detail="CoinGecko not available")

    try:
        coin = cg.get_coin_by_id(id=coin_id, localization=False, tickers=False,
                                  community_data=False, developer_data=False)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Coin {coin_id} not found")

    md = coin.get("market_data", {})
    prompt = (
        f"You are a cryptocurrency analyst. Do not give buy/sell advice.\n"
        f"Coin: {coin['name']} ({coin['symbol'].upper()})\n"
        f"Price: {md.get('current_price', {}).get(vs_currency, 'N/A')}\n"
        f"Market Cap Rank: #{md.get('market_cap_rank', 'N/A')}\n"
        f"24h: {md.get('price_change_percentage_24h', 0):.2f}% | "
        f"7d: {md.get('price_change_percentage_7d', 0):.2f}% | "
        f"30d: {md.get('price_change_percentage_30d', 0):.2f}%\n"
        f"Categories: {', '.join(coin.get('categories', [])[:5])}\n\n"
        f"In 3-4 sentences, analyze current momentum and key catalysts. Note the primary risk."
    )

    analysis = ""
    for provider in LLM_PROVIDER_ORDER:
        try:
            if provider == "local":
                r = requests.post(
                    f"{LOCAL_LLM_URL}/chat/completions",
                    json={"model": LOCAL_LLM_MODEL,
                          "messages": [{"role": "system", "content": "Crypto analyst. Brief factual output."},
                                       {"role": "user", "content": prompt}],
                          "max_tokens": 600, "temperature": 0.3},
                    timeout=60,
                )
                if r.status_code == 200:
                    analysis = strip_think_tags(r.json()["choices"][0]["message"]["content"]).strip()
                    break
            elif provider == "openai" and openai_client:
                r = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": "Crypto analyst. Brief factual output."},
                               {"role": "user", "content": prompt}],
                    max_tokens=200, temperature=0.3,
                )
                analysis = (r.choices[0].message.content or "").strip()
                break
        except Exception:
            continue

    return {
        "coin_id": coin_id, "name": coin["name"], "symbol": coin["symbol"],
        "analysis": analysis or "Analysis unavailable — LLM providers are offline.",
    }


@app.get("/api/crypto/categories")
async def crypto_categories(current_user: dict = Depends(get_current_user)):
    """Return crypto category definitions."""
    del current_user
    return {"categories": CRYPTO_CATEGORIES}


@app.post("/api/crypto/watchlist")
async def crypto_watchlist_add(coin_id: str, symbol: str, name: str = "", current_user: dict = Depends(get_current_user)):
    wid = str(uuid4())
    try:
        with db_conn() as conn:
            conn.execute(text("""
                INSERT INTO crypto_watchlist (id, user_id, coin_id, symbol, name)
                VALUES (:id, :uid, :cid, :sym, :name)
                ON CONFLICT (user_id, coin_id) DO NOTHING
            """), {"id": wid, "uid": current_user["id"], "cid": coin_id, "sym": symbol, "name": name})
        return {"status": "added", "coin_id": coin_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/crypto/watchlist")
async def crypto_watchlist_get(current_user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT coin_id, symbol, name, added_at FROM crypto_watchlist
            WHERE user_id = :uid ORDER BY added_at DESC
        """), {"uid": current_user["id"]}).fetchall()

    items = [{"coin_id": r[0], "symbol": r[1], "name": r[2], "added_at": r[3].isoformat() if r[3] else None}
             for r in rows]

    # Enrich with live prices if CoinGecko available
    if COINGECKO_AVAILABLE and items:
        try:
            ids = ",".join(i["coin_id"] for i in items)
            prices = cg.get_price(ids=ids, vs_currencies="usd,inr",
                                   include_24hr_change=True)
            for item in items:
                pd_data = prices.get(item["coin_id"], {})
                item["price_usd"] = pd_data.get("usd")
                item["price_inr"] = pd_data.get("inr")
                item["change_24h_pct"] = pd_data.get("usd_24h_change")
        except Exception:
            pass

    return {"watchlist": items}


@app.delete("/api/crypto/watchlist/{coin_id}")
async def crypto_watchlist_remove(coin_id: str, current_user: dict = Depends(get_current_user)):
    with db_conn() as conn:
        conn.execute(text("DELETE FROM crypto_watchlist WHERE user_id = :uid AND coin_id = :cid"),
                     {"uid": current_user["id"], "cid": coin_id})
    return {"status": "removed", "coin_id": coin_id}


# ── ETF Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/etf/list")
async def etf_list(market: str = None, category: str = None, current_user: dict = Depends(get_current_user)):
    """List ETFs filtered by market prefix and/or category."""
    del current_user
    results = {}
    for cat_key, etfs in ETF_UNIVERSE.items():
        # Filter by market prefix (AU_, US_, IN_)
        if market:
            m = market.upper()
            if not cat_key.startswith(m + "_"):
                continue
        # Filter by category suffix
        if category and not cat_key.endswith("_" + category.lower()):
            continue
        results[cat_key] = [{"ticker": t, "name": n} for t, n in etfs.items()]

    return {"categories": results}


@app.get("/api/etf/search")
async def etf_search(q: str, current_user: dict = Depends(get_current_user)):
    del current_user
    q_lower = q.lower()
    matches = []
    for cat_key, etfs in ETF_UNIVERSE.items():
        for ticker, name in etfs.items():
            if q_lower in ticker.lower() or q_lower in name.lower():
                matches.append({"ticker": ticker, "name": name, "category": cat_key})
    return {"results": matches[:20]}


@app.get("/api/etf/{ticker:path}")
async def etf_detail(ticker: str, current_user: dict = Depends(get_current_user)):
    """Detailed ETF info using yfinance."""
    del current_user
    ck = f"etf_detail_{ticker}"
    cached = cache_get(ck, ttl_seconds=3600)
    if cached:
        return cached

    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        hist = tk.history(period="1y")

        ytd_start = datetime(datetime.utcnow().year, 1, 1)
        ytd_data = hist[hist.index >= str(ytd_start)]
        ytd_return = 0
        if len(ytd_data) > 0 and len(hist) > 0:
            first_price = float(ytd_data["Close"].iloc[0])
            last_price = float(hist["Close"].iloc[-1])
            ytd_return = ((last_price - first_price) / first_price) * 100 if first_price > 0 else 0

        one_year_return = 0
        if len(hist) > 200:
            one_year_return = ((float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[0])) / float(hist["Close"].iloc[0])) * 100

        result = {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName", ticker),
            "current_price": info.get("regularMarketPrice") or (float(hist["Close"].iloc[-1]) if len(hist) > 0 else None),
            "expense_ratio": info.get("annualReportExpenseRatio"),
            "dividend_yield": info.get("yield"),
            "total_assets": info.get("totalAssets"),
            "ytd_return_pct": round(ytd_return, 2),
            "one_year_return_pct": round(one_year_return, 2),
            "52_week_high": info.get("fiftyTwoWeekHigh"),
            "52_week_low": info.get("fiftyTwoWeekLow"),
            "category": info.get("category", ""),
            "exchange": info.get("exchange", ""),
        }
        cache_set(ck, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"ETF {ticker} not found: {e}")


class ETFCompareRequest(BaseModel):
    tickers: List[str]

@app.post("/api/etf/compare")
async def etf_compare(payload: ETFCompareRequest, current_user: dict = Depends(get_current_user)):
    """Side-by-side comparison of 2-4 ETFs."""
    del current_user
    tickers = payload.tickers[:4]
    if len(tickers) < 2:
        raise HTTPException(status_code=400, detail="Provide at least 2 ETF tickers to compare")

    comparison = []
    chart_data = {}
    for ticker in tickers:
        try:
            tk = yf.Ticker(ticker)
            info = tk.info
            hist = tk.history(period="1y")
            if len(hist) > 0:
                # Normalize to 100 for comparison chart
                normalized = (hist["Close"] / float(hist["Close"].iloc[0])) * 100
                chart_data[ticker] = [{"date": d.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
                                       for d, v in normalized.items()]
            comparison.append({
                "ticker": ticker,
                "name": info.get("longName") or info.get("shortName", ticker),
                "current_price": info.get("regularMarketPrice") or (float(hist["Close"].iloc[-1]) if len(hist) > 0 else None),
                "expense_ratio": info.get("annualReportExpenseRatio"),
                "dividend_yield": info.get("yield"),
                "total_assets": info.get("totalAssets"),
            })
        except Exception:
            comparison.append({"ticker": ticker, "error": "Data unavailable"})

    return {"comparison": comparison, "chart_data": chart_data}


class ETFSuggestRequest(BaseModel):
    risk_profile: str = "balanced"
    themes: List[str] = []
    market: str = "AU"

@app.post("/api/etf/suggest")
async def etf_suggest(payload: ETFSuggestRequest, current_user: dict = Depends(get_current_user)):
    """AI-powered ETF recommendation."""
    del current_user
    m = payload.market.upper()

    # Gather relevant ETFs
    relevant = {}
    for cat_key, etfs in ETF_UNIVERSE.items():
        if cat_key.startswith(m + "_") or cat_key.startswith("US_"):
            relevant.update(etfs)

    etf_list_text = "\n".join(f"  {t}: {n}" for t, n in list(relevant.items())[:30])
    themes_text = f"Focus themes: {', '.join(payload.themes)}. " if payload.themes else ""

    prompt = (
        f"You are an ETF analyst. Do not give buy/sell advice.\n"
        f"Risk profile: {payload.risk_profile}\n"
        f"{themes_text}\n"
        f"Available ETFs:\n{etf_list_text}\n\n"
        f"Recommend 3-5 ETFs for this risk profile. For each, give ticker and 1 sentence rationale.\n"
        f"Return JSON: {{\"recommendations\": [{{\"ticker\": \"...\", \"name\": \"...\", \"rationale\": \"...\"}}]}}"
    )

    for provider in LLM_PROVIDER_ORDER:
        try:
            if provider == "local":
                r = requests.post(
                    f"{LOCAL_LLM_URL}/chat/completions",
                    json={"model": LOCAL_LLM_MODEL,
                          "messages": [{"role": "system", "content": "Output strict JSON only."},
                                       {"role": "user", "content": prompt}],
                          "max_tokens": 800, "temperature": 0.3},
                    timeout=60,
                )
                if r.status_code == 200:
                    content = strip_think_tags(r.json()["choices"][0]["message"]["content"])
                    s = content.find("{"); e = content.rfind("}") + 1
                    if s >= 0:
                        return json.loads(content[s:e])
            elif provider == "openai" and openai_client:
                r = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": "Output strict JSON only."},
                               {"role": "user", "content": prompt}],
                    max_tokens=300, temperature=0.3,
                )
                content = r.choices[0].message.content or "{}"
                s = content.find("{"); e = content.rfind("}") + 1
                if s >= 0:
                    return json.loads(content[s:e])
        except Exception:
            continue

    # Fallback
    return {"recommendations": [{"ticker": t, "name": n, "rationale": "Suggested based on category match."}
                                for t, n in list(relevant.items())[:3]]}


@app.get("/api/etf/spotlight")
async def etf_spotlight(current_user: dict = Depends(get_current_user)):
    """Weekly AI thematic spotlight."""
    del current_user
    ck = "etf_spotlight"
    cached = cache_get(ck, ttl_seconds=3600)
    if cached:
        return cached

    snapshot = compute_regime_snapshot()
    macro = get_macro_indicators()

    prompt = (
        f"You are an ETF analyst. Do not give buy/sell advice.\n"
        f"Market Regime: {snapshot['regime']}\n"
        f"ASX200: {snapshot['asx200_ret']}% | S&P500: {snapshot['sp500_ret']}% | "
        f"NIFTY: {snapshot['nifty_ret']}% | Gold: {snapshot['gold_ret']}%\n"
        f"Macro: {json.dumps(macro)}\n\n"
        f"Pick ONE ETF theme for this week (e.g., semiconductors, gold, energy, bonds).\n"
        f"In 3-4 sentences explain why. Name 1 AU ETF and 1 US ETF for the theme.\n"
        f"Return JSON: {{\"theme\": \"...\", \"commentary\": \"...\", \"au_etf\": \"...\", \"us_etf\": \"...\"}}"
    )

    for provider in LLM_PROVIDER_ORDER:
        try:
            if provider == "local":
                r = requests.post(
                    f"{LOCAL_LLM_URL}/chat/completions",
                    json={"model": LOCAL_LLM_MODEL,
                          "messages": [{"role": "system", "content": "Output strict JSON only."},
                                       {"role": "user", "content": prompt}],
                          "max_tokens": 800, "temperature": 0.3},
                    timeout=60,
                )
                if r.status_code == 200:
                    content = strip_think_tags(r.json()["choices"][0]["message"]["content"])
                    s = content.find("{"); e = content.rfind("}") + 1
                    if s >= 0:
                        result = json.loads(content[s:e])
                        cache_set(ck, result)
                        return result
            elif provider == "openai" and openai_client:
                r = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": "Output strict JSON only."},
                               {"role": "user", "content": prompt}],
                    max_tokens=250, temperature=0.3,
                )
                content = r.choices[0].message.content or "{}"
                s = content.find("{"); e = content.rfind("}") + 1
                if s >= 0:
                    result = json.loads(content[s:e])
                    cache_set(ck, result)
                    return result
        except Exception:
            continue

    return {"theme": "Broad Market", "commentary": "ETF spotlight unavailable — LLM offline.",
            "au_etf": "VAS.AX", "us_etf": "SPY"}


class IncomeCalcRequest(BaseModel):
    ticker: str
    amount: float

@app.post("/api/etf/income-calc")
async def etf_income_calc(payload: IncomeCalcRequest, current_user: dict = Depends(get_current_user)):
    """Estimate annual income from dividend ETF."""
    del current_user
    try:
        tk = yf.Ticker(payload.ticker)
        info = tk.info
        div_yield = info.get("yield") or 0
        annual_income = payload.amount * div_yield
        return {
            "ticker": payload.ticker,
            "investment": payload.amount,
            "dividend_yield_pct": round(div_yield * 100, 2),
            "estimated_annual_income": round(annual_income, 2),
            "estimated_monthly_income": round(annual_income / 12, 2),
        }
    except Exception:
        raise HTTPException(status_code=404, detail=f"ETF {payload.ticker} not found")


# ── AI Suggestion Engine ──────────────────────────────────────────────────────

@app.get("/api/ai/weekly-summary")
async def ai_weekly_summary(current_user: dict = Depends(get_current_user)):
    """Cross-asset weekly AI summary."""
    del current_user
    ck = "ai_weekly_summary"
    cached = cache_get(ck, ttl_seconds=3600)
    if cached:
        return cached

    snapshot = compute_regime_snapshot()
    macro = get_macro_indicators()

    # Get fear & greed
    fng_val = ""
    try:
        fng_resp = requests.get(os.getenv("FEAR_GREED_API_URL", "https://api.alternative.me/fng/"), timeout=5)
        if fng_resp.status_code == 200:
            fng_data = fng_resp.json().get("data", [{}])[0]
            fng_val = f"Crypto Fear & Greed: {fng_data.get('value', '?')} ({fng_data.get('value_classification', '')})"
    except Exception:
        pass

    prompt = (
        f"You are a macro strategist. Do not give buy/sell advice.\n\n"
        f"Market Regime: {snapshot['regime']}\n"
        f"ASX200: {snapshot['asx200_ret']}% | S&P500: {snapshot['sp500_ret']}% | "
        f"NIFTY50: {snapshot['nifty_ret']}% | SENSEX: {snapshot['sensex_ret']}%\n"
        f"Gold: {snapshot['gold_ret']}% | USD Index: {snapshot['dxy_ret']}%\n"
        f"Macro: {json.dumps(macro)}\n"
        f"{fng_val}\n\n"
        f"Provide a 4-5 sentence weekly market summary covering:\n"
        f"1. Overall market tone and regime\n"
        f"2. Key theme driving returns\n"
        f"3. Which asset classes / sectors look strong\n"
        f"4. Primary risk to watch this week"
    )

    summary = ""
    for provider in LLM_PROVIDER_ORDER:
        try:
            if provider == "local":
                r = requests.post(
                    f"{LOCAL_LLM_URL}/chat/completions",
                    json={"model": LOCAL_LLM_MODEL,
                          "messages": [{"role": "system", "content": "Macro strategist. Brief factual output."},
                                       {"role": "user", "content": prompt}],
                          "max_tokens": 800, "temperature": 0.3},
                    timeout=60,
                )
                if r.status_code == 200:
                    summary = strip_think_tags(r.json()["choices"][0]["message"]["content"]).strip()
                    break
            elif provider == "openai" and openai_client:
                r = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": "Macro strategist. Brief factual output."},
                               {"role": "user", "content": prompt}],
                    max_tokens=300, temperature=0.3,
                )
                summary = (r.choices[0].message.content or "").strip()
                break
        except Exception:
            continue

    result = {
        "week": get_current_iso_week(),
        "regime": snapshot["regime"],
        "regime_confidence": snapshot["confidence"],
        "metrics": {
            "asx200": snapshot["asx200_ret"],
            "sp500": snapshot["sp500_ret"],
            "nifty50": snapshot["nifty_ret"],
            "sensex": snapshot["sensex_ret"],
            "gold": snapshot["gold_ret"],
            "dxy": snapshot["dxy_ret"],
        },
        "macro": macro,
        "summary": summary or "Weekly summary unavailable — LLM providers are offline.",
    }
    cache_set(ck, result)
    return result


class AIAskRequest(BaseModel):
    question: str


class HedgeAdviceRequest(BaseModel):
    symbols: List[str] = []
    market: str = "AU"
    send_telegram: bool = False
    limit: int = 5


class DigestRequest(BaseModel):
    market: str = "AU"
    send_telegram: bool = False
    limit: int = 5


class TelegramRecipientCreate(BaseModel):
    chat_id: str
    label: Optional[str] = None


class PaperTradeCreate(BaseModel):
    symbol: str
    market: str = "AU"
    side: str = "LONG"
    quantity: float = 100
    notes: Optional[str] = None
    # User's actual fill price — may differ from the live quote at signal time.
    # When provided this becomes the entry_price recorded in the trade.
    actual_price: Optional[float] = None


class PaperTradeClose(BaseModel):
    notes: Optional[str] = None


class PaperTradeUpdate(BaseModel):
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    review_date: Optional[datetime] = None
    position_stage: Optional[str] = None
    notes: Optional[str] = None


class AdviceActionCreate(BaseModel):
    symbol: str
    market: str = "AU"
    action_type: str = "BUY"
    quantity: float
    execution_price: float
    commission: Optional[float] = None
    advice_cache_key: Optional[str] = None
    source_message_type: Optional[str] = "hedge_advice"
    notes: Optional[str] = None
    send_telegram: bool = False
    analyst_target: Optional[float] = None


class TelegramActionIngest(BaseModel):
    chat_id: str
    symbol: str
    market: str = "AU"
    action_type: str = "BUY"
    quantity: float
    execution_price: float
    commission: Optional[float] = None
    advice_cache_key: Optional[str] = None
    source_message_type: Optional[str] = "hedge_advice"
    notes: Optional[str] = None
    raw_text: Optional[str] = None


def _resolve_user_for_chat(chat_id: str) -> tuple[Optional[str], bool]:
    try:
        with db_conn() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT user_id, updated_at, created_at
                    FROM user_telegram_recipients
                    WHERE chat_id = :chat_id AND is_active = 1
                    ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
                    """
                ),
                {"chat_id": chat_id},
            ).fetchall()
    except Exception:
        return None, False

    if not rows:
        return None, False
    return rows[0][0], len(rows) > 1


def _create_advice_action_for_user(
    *,
    user_id: str,
    symbol: str,
    market: str,
    action_type: str,
    quantity: float,
    execution_price: float,
    commission: Optional[float],
    advice_cache_key: Optional[str],
    source_message_type: Optional[str],
    notes: Optional[str],
) -> dict:
    gross_amount = quantity * execution_price
    fee = commission
    if fee is None:
        fee = _estimate_commission(gross_amount)
    fee = max(float(fee or 0), 0.0)

    net_amount = gross_amount + fee if action_type in {"BUY", "ADD", "HOLD"} else max(gross_amount - fee, 0.0)
    row_id = str(uuid4())
    now = datetime.utcnow()

    with db_conn() as conn:
        conn.execute(
            text(
                """
                INSERT INTO advice_execution_actions (
                    id, user_id, symbol, market, action_type, quantity, execution_price,
                    gross_amount, commission, net_amount, advice_cache_key,
                    source_message_type, notes, created_at
                ) VALUES (
                    :id, :user_id, :symbol, :market, :action_type, :quantity, :execution_price,
                    :gross_amount, :commission, :net_amount, :advice_cache_key,
                    :source_message_type, :notes, :created_at
                )
                """
            ),
            {
                "id": row_id,
                "user_id": user_id,
                "symbol": symbol,
                "market": market,
                "action_type": action_type,
                "quantity": quantity,
                "execution_price": execution_price,
                "gross_amount": gross_amount,
                "commission": fee,
                "net_amount": net_amount,
                "advice_cache_key": (advice_cache_key or "").strip() or None,
                "source_message_type": (source_message_type or "").strip() or None,
                "notes": (notes or "").strip() or None,
                "created_at": now,
            },
        )

        existing_share = conn.execute(
            text("SELECT symbol FROM user_shares WHERE user_id = :user_id AND symbol = :symbol"),
            {"user_id": user_id, "symbol": symbol},
        ).fetchone()
        if not existing_share and action_type in {"BUY", "ADD", "HOLD"}:
            conn.execute(
                text("INSERT INTO user_shares (user_id, symbol, name) VALUES (:user_id, :symbol, :name)"),
                {"user_id": user_id, "symbol": symbol, "name": symbol},
            )

    return {
        "id": row_id,
        "symbol": symbol,
        "action_type": action_type,
        "quantity": quantity,
        "execution_price": execution_price,
        "gross_amount": round(gross_amount, 2),
        "commission": round(fee, 2),
        "net_amount": round(net_amount, 2),
        "holdings": get_user_execution_holdings(user_id),
    }

@app.post("/api/ai/ask")
async def ai_ask(payload: AIAskRequest, current_user: dict = Depends(get_current_user)):
    """General AI Q&A about markets."""
    del current_user
    snapshot = compute_regime_snapshot()
    macro = get_macro_indicators()

    prompt = (
        f"You are a financial data analyst. Do not give buy/sell advice. "
        f"Provide factual analysis only.\n\n"
        f"Current Context:\n"
        f"Regime: {snapshot['regime']} | ASX200: {snapshot['asx200_ret']}% | "
        f"S&P500: {snapshot['sp500_ret']}% | NIFTY50: {snapshot['nifty_ret']}% | "
        f"Gold: {snapshot['gold_ret']}%\n"
        f"Macro: {json.dumps(macro)}\n\n"
        f"User Question: {payload.question}"
    )

    answer = ""
    for provider in LLM_PROVIDER_ORDER:
        try:
            if provider == "local":
                r = requests.post(
                    f"{LOCAL_LLM_URL}/chat/completions",
                    json={"model": LOCAL_LLM_MODEL,
                          "messages": [{"role": "system", "content": "Financial analyst. Factual, no advice."},
                                       {"role": "user", "content": prompt}],
                          "max_tokens": 1000, "temperature": 0.3},
                    timeout=60,
                )
                if r.status_code == 200:
                    answer = strip_think_tags(r.json()["choices"][0]["message"]["content"]).strip()
                    break
            elif provider == "openai" and openai_client:
                r = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": "Financial analyst. Factual, no advice."},
                               {"role": "user", "content": prompt}],
                    max_tokens=400, temperature=0.3,
                )
                answer = (r.choices[0].message.content or "").strip()
                break
        except Exception:
            continue

    return {"question": payload.question, "answer": answer or "Unable to generate answer — LLM providers offline."}


@app.get("/api/telegram/recipients")
async def telegram_recipients(current_user: dict = Depends(get_current_user)):
    recipients = get_user_telegram_recipients(current_user["id"])
    return {
        "count": len(recipients),
        "recipients": recipients,
        "broker_filter": None,
        "source": "user_scoped",
        "admin_managed": True,
    }


@app.get("/api/telegram/history")
async def telegram_history(current_user: dict = Depends(get_current_user)):
    history = list_recent_telegram_send_log(current_user["id"])
    return {"items": history}


@app.post("/api/telegram/recipients", status_code=201)
async def create_telegram_recipient(payload: TelegramRecipientCreate, current_user: dict = Depends(get_current_user)):
    chat_id = str(payload.chat_id or "").strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")

    with db_conn() as conn:
        existing = conn.execute(
            text("SELECT id FROM user_telegram_recipients WHERE user_id = :user_id AND chat_id = :chat_id"),
            {"user_id": current_user["id"], "chat_id": chat_id},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="This Telegram chat is already linked to your account")

        conn.execute(
            text(
                """
                INSERT INTO user_telegram_recipients (id, user_id, chat_id, label, is_active, created_at, updated_at)
                VALUES (:id, :user_id, :chat_id, :label, 1, :created_at, :updated_at)
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": current_user["id"],
                "chat_id": chat_id,
                "label": (payload.label or "").strip() or None,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            },
        )

    return {"ok": True, "recipients": get_user_telegram_recipients(current_user["id"])}


@app.delete("/api/telegram/recipients/{recipient_id}")
async def delete_telegram_recipient(recipient_id: str, current_user: dict = Depends(get_current_user)):
    with db_conn() as conn:
        result = conn.execute(
            text("DELETE FROM user_telegram_recipients WHERE id = :id AND user_id = :user_id"),
            {"id": recipient_id, "user_id": current_user["id"]},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Telegram recipient not found")
    return {"ok": True}


@app.get("/api/positions/history")
async def position_history(current_user: dict = Depends(get_current_user)):
    return {"items": list_position_events(current_user["id"])}


@app.get("/api/positions/wealth-summary")
async def positions_wealth_summary(current_user: dict = Depends(get_current_user)):
    holdings = get_user_execution_holdings(current_user["id"])
    if not holdings:
        return {
            "total_invested": 0.0,
            "current_value": 0.0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "daily_change": 0.0,
            "positions": [],
        }

    total_invested = 0.0
    current_value = 0.0
    daily_change_total = 0.0
    positions = []

    for h in holdings:
        sym = h["symbol"]
        market = h.get("market", "AU")
        qty = h["quantity"]
        avg_cost = h["avg_cost"]
        invested = h["invested_amount"]

        try:
            sd = get_stock_data(sym, market)
            live_price = sd.get("current_price", 0) or 0
            day_chg_pct = sd.get("change_percent", 0) or 0
        except Exception:
            live_price = avg_cost
            day_chg_pct = 0.0

        pos_value = qty * live_price if live_price > 0 else invested
        pos_pnl = pos_value - invested
        pos_pnl_pct = ((live_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0

        total_invested += invested
        current_value += pos_value
        daily_change_total += pos_value * (day_chg_pct / 100) if day_chg_pct != 0 else 0.0

        positions.append({
            "symbol": sym,
            "market": market,
            "quantity": round(qty, 6),
            "avg_cost": round(avg_cost, 4),
            "live_price": round(live_price, 4),
            "invested_amount": round(invested, 2),
            "current_value": round(pos_value, 2),
            "pnl": round(pos_pnl, 2),
            "pnl_pct": round(pos_pnl_pct, 2),
            "day_change_pct": round(day_chg_pct, 2),
        })

    total_pnl = current_value - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0

    return {
        "total_invested": round(total_invested, 2),
        "current_value": round(current_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "daily_change": round(daily_change_total, 2),
        "positions": positions,
    }


@app.get("/api/positions/monitor-check")
async def positions_monitor_check(current_user: dict = Depends(get_current_user)):
    holdings = get_user_execution_holdings(current_user["id"])
    if not holdings:
        return {"alerts": [], "summary": "No active positions to monitor."}

    alerts = []
    now = datetime.utcnow()

    for h in holdings:
        sym = h["symbol"]
        market = h.get("market", "AU")
        qty = h["quantity"]
        avg_cost = h["avg_cost"]
        invested = h["invested_amount"]

        try:
            sd = get_stock_data(sym, market)
            live_price = sd.get("current_price", 0) or 0
        except Exception:
            continue

        if live_price <= 0:
            continue

        pnl_pct = ((live_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0
        try:
            hist = get_historical_data(sym, period="6mo")
            if hist is not None and len(hist) >= 50:
                indicators = calculate_technical_indicators(hist)
                rsi = indicators.get("rsi")
            else:
                rsi = None
        except Exception:
            rsi = None

        try:
            val = get_valuation_metrics(sym)
            target_mean = val.get("analyst_target_mean")
            days_to_e = val.get("days_to_earnings")
        except Exception:
            target_mean = None
            days_to_e = None

        last_action_at = h.get("last_action_at")
        days_held = None
        if last_action_at:
            try:
                entry_date = datetime.fromisoformat(last_action_at.replace("Z", "+00:00"))
                days_held = (now.replace(tzinfo=None) - entry_date.replace(tzinfo=None)).days
            except Exception:
                days_held = None

        alerts_for_symbol = []

        if pnl_pct <= -8:
            alerts_for_symbol.append({
                "type": "stop_loss",
                "symbol": sym,
                "action": "SELL",
                "reason": f"Down {abs(pnl_pct):.1f}% from entry — stop loss zone",
                "urgency": "high",
                "pnl_pct": round(pnl_pct, 2),
                "live_price": round(live_price, 2),
                "entry_price": round(avg_cost, 2),
            })
        elif target_mean and live_price >= float(target_mean):
            alerts_for_symbol.append({
                "type": "profit_target",
                "symbol": sym,
                "action": "SELL",
                "reason": f"Price ${live_price:.2f} reached analyst target ${float(target_mean):.2f}",
                "urgency": "high",
                "pnl_pct": round(pnl_pct, 2),
                "live_price": round(live_price, 2),
                "entry_price": round(avg_cost, 2),
                "target_price": round(float(target_mean), 2),
            })
        elif rsi is not None and rsi > 75:
            alerts_for_symbol.append({
                "type": "overbought",
                "symbol": sym,
                "action": "REVIEW",
                "reason": f"RSI at {rsi:.1f} — overbought territory",
                "urgency": "medium",
                "rsi": round(rsi, 1),
                "live_price": round(live_price, 2),
                "pnl_pct": round(pnl_pct, 2),
            })
        elif days_held is not None and days_held > 90:
            alerts_for_symbol.append({
                "type": "review_prompt",
                "symbol": sym,
                "action": "REVIEW",
                "reason": f"Held {days_held} days without signal — time to review",
                "urgency": "low",
                "days_held": days_held,
                "pnl_pct": round(pnl_pct, 2),
            })

        if not alerts_for_symbol:
            alerts_for_symbol.append({
                "type": "ok",
                "symbol": sym,
                "action": "HOLD",
                "reason": "Within normal range",
                "urgency": "none",
                "pnl_pct": round(pnl_pct, 2),
                "live_price": round(live_price, 2),
                "entry_price": round(avg_cost, 2),
            })

        alerts.extend(alerts_for_symbol)

    high_urgency = [a for a in alerts if a.get("urgency") == "high"]
    medium_urgency = [a for a in alerts if a.get("urgency") == "medium"]
    summary = "All positions normal." if not high_urgency else f"{len(high_urgency)} positions need attention"

    return {
        "alerts": alerts,
        "summary": summary,
        "high_urgency_count": len(high_urgency),
        "medium_urgency_count": len(medium_urgency),
    }


@app.get("/api/advice/actions")
async def advice_actions(current_user: dict = Depends(get_current_user)):
    items = list_advice_execution_actions(current_user["id"], limit=100)
    holdings = get_user_execution_holdings(current_user["id"])
    return {
        "items": items,
        "holdings": holdings,
    }


@app.post("/api/advice/actions", status_code=201)
async def record_advice_action(payload: AdviceActionCreate, current_user: dict = Depends(get_current_user)):
    symbol = str(payload.symbol or "").upper().strip()
    market = str(payload.market or "AU").upper().strip()
    action_type = str(payload.action_type or "BUY").upper().strip()
    quantity = float(payload.quantity or 0)
    execution_price = float(payload.execution_price or 0)
    analyst_target = float(payload.analyst_target or 0)

    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    if action_type not in {"BUY", "ADD", "SELL", "REDUCE", "HOLD"}:
        raise HTTPException(status_code=400, detail="action_type must be BUY, ADD, SELL, REDUCE, or HOLD")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be greater than zero")
    if execution_price <= 0:
        raise HTTPException(status_code=400, detail="execution_price must be greater than zero")

    result = _create_advice_action_for_user(
        user_id=current_user["id"],
        symbol=symbol,
        market=market,
        action_type=action_type,
        quantity=quantity,
        execution_price=execution_price,
        commission=payload.commission,
        advice_cache_key=payload.advice_cache_key,
        source_message_type=payload.source_message_type,
        notes=payload.notes,
    )

    telegram_delivery = None
    if payload.send_telegram and action_type in {"BUY", "ADD", "SELL", "REDUCE"}:
        recipients = get_user_telegram_recipients(current_user["id"])
        if recipients:
            gross_amount = quantity * execution_price
            _sd = get_stock_data(symbol, market)
            _dv = float(_sd.get("avg_volume_5d") or 0) * execution_price
            _sp = _get_strategy_params(_dv)
            stop_loss_price = round(execution_price * (1 - _sp["stop_pct"]), 2)
            stop_pct_display = int(_sp["stop_pct"] * 100)
            target_pct = round(((analyst_target - execution_price) / execution_price) * 100, 2) if analyst_target and execution_price > 0 else None
            target_str = f"${analyst_target:.2f} ({target_pct:+.1f}%)" if analyst_target and target_pct else "N/A"

            buy_msg = (
                f"<b>✅ {'BUY' if action_type in ('BUY', 'ADD') else action_type} RECORDED — {symbol}.{market}</b>\n"
                f"Shares: {quantity} @ ${execution_price:.2f}\n"
                f"Total invested: ${gross_amount:,.2f}\n"
                f"Stop loss target: ~${stop_loss_price:.2f} (-{stop_pct_display}%)\n"
                f"Analyst target: {target_str}\n"
                f"Monitoring active \U0001f4e1 — you'll be notified when conditions change."
            )
            telegram_delivery = _send_telegram_payload(
                buy_msg,
                recipients,
                user_id=current_user["id"],
                message_type="buy_confirmation",
                market=market,
                delivery_mode="manual",
                source="buy_flow",
            )

    return {
        "ok": True,
        **result,
        "telegram": telegram_delivery,
    }


@app.post("/api/telegram/action-ingest")
async def telegram_action_ingest(payload: TelegramActionIngest, request: Request):
    secret_header = request.headers.get("x-telegram-action-secret", "")
    if TELEGRAM_ACTION_INGEST_SECRET and not hmac.compare_digest(secret_header, TELEGRAM_ACTION_INGEST_SECRET):
        raise HTTPException(status_code=401, detail="Invalid ingest secret")

    chat_id = str(payload.chat_id or "").strip()
    symbol = str(payload.symbol or "").upper().strip()
    market = str(payload.market or "AU").upper().strip()
    action_type = str(payload.action_type or "BUY").upper().strip()
    quantity = float(payload.quantity or 0)
    execution_price = float(payload.execution_price or 0)

    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    if action_type not in {"BUY", "ADD", "SELL", "REDUCE", "HOLD"}:
        raise HTTPException(status_code=400, detail="action_type must be BUY, ADD, SELL, REDUCE, or HOLD")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be greater than zero")

    if execution_price <= 0:
        stock_data = get_stock_data(symbol, market)
        execution_price = float(stock_data.get("current_price") or 0)
        if execution_price <= 0:
            raise HTTPException(status_code=400, detail=f"execution_price not provided and unable to fetch market price for {symbol}")

    resolved_user_id, ambiguous_chat_mapping = _resolve_user_for_chat(chat_id)
    if resolved_user_id is None:
        raise HTTPException(status_code=404, detail="No active user mapping for this Telegram chat")

    notes = (payload.notes or "").strip()
    if payload.raw_text:
        notes = (f"{notes} | raw: {payload.raw_text}" if notes else f"raw: {payload.raw_text}")[:500]

    result = _create_advice_action_for_user(
        user_id=resolved_user_id,
        symbol=symbol,
        market=market,
        action_type=action_type,
        quantity=quantity,
        execution_price=execution_price,
        commission=payload.commission,
        advice_cache_key=payload.advice_cache_key,
        source_message_type=payload.source_message_type,
        notes=notes,
    )
    return {
        "ok": True,
        "chat_id": chat_id,
        "user_id": resolved_user_id,
        "ambiguous_chat_mapping": ambiguous_chat_mapping,
        **result,
    }


@app.post("/api/ai/hedge-advice")
async def ai_hedge_advice(payload: HedgeAdviceRequest, current_user: dict = Depends(get_current_user)):
    advice = get_hedge_advice_cache(current_user["id"], payload.symbols, payload.market, payload.limit)
    recipients = get_user_telegram_recipients(current_user["id"])

    delivery = {
        "sent": False,
        "success_count": 0,
        "failure_count": 0,
        "results": [],
        "error": None,
    }
    if payload.send_telegram:
        delivery = _send_telegram_payload(
            advice["summary"],
            recipients,
            user_id=current_user["id"],
            message_type="hedge_advice",
            market=advice["market"],
            delivery_mode="manual",
            strategy_dashboard=advice.get("strategy_dashboard"),
        )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "market": advice["market"],
        "summary": advice["summary"],
        "items": advice["items"],
        "min_score": advice.get("min_score"),
        "candidate_count": advice.get("candidate_count"),
        "analyzed_count": advice.get("analyzed_count"),
        "qualified_count": advice.get("qualified_count"),
        "strategy_dashboard": advice.get("strategy_dashboard"),
        "under_one_selected": advice.get("under_one_selected"),
        "recipients": recipients,
        "telegram_sent": delivery["sent"],
        "telegram_enabled": bool(recipients),
        "delivery": delivery,
        "history": list_recent_telegram_send_log(current_user["id"], limit=10),
        "cache_hit": bool(advice.get("cache_hit")),
        "holdings_context": advice.get("holdings_context") or get_user_execution_holdings(current_user["id"]),
    }


@app.post("/api/ai/daily-digest")
async def ai_daily_digest(payload: DigestRequest, current_user: dict = Depends(get_current_user)):
    digest = get_daily_digest_cache(payload.market, limit=payload.limit)
    recipients = get_user_telegram_recipients(current_user["id"])
    digest_key = digest.get("cache_key") or datetime.utcnow().strftime("%Y-%m-%d")

    delivery = {
        "sent": False,
        "success_count": 0,
        "failure_count": 0,
        "results": [],
        "error": None,
    }
    if payload.send_telegram:
        if has_digest_been_sent(current_user["id"], digest["market"], digest_key):
            delivery = {
                "sent": False,
                "success_count": 0,
                "failure_count": 0,
                "results": [],
                "error": "Daily digest already auto-sent for this user today. Use history to confirm delivery.",
            }
        else:
            delivery = _send_telegram_payload(
                digest["summary"],
                recipients,
                user_id=current_user["id"],
                message_type="daily_digest",
                market=digest["market"],
                delivery_mode="manual",
                digest_key=digest_key,
                strategy_dashboard=digest.get("strategy_dashboard"),
            )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "market": digest["market"],
        "summary": digest["summary"],
        "items": digest["items"],
        "breakdown": digest["breakdown"],
        "strategy_dashboard": digest.get("strategy_dashboard"),
        "recipients": recipients,
        "telegram_sent": delivery["sent"],
        "telegram_enabled": bool(recipients),
        "delivery": delivery,
        "history": list_recent_telegram_send_log(current_user["id"], limit=10),
        "digest_key": digest_key,
        "cache_hit": bool(digest.get("cache_hit")),
    }


@app.get("/api/paper-trades")
async def get_paper_trades(current_user: dict = Depends(get_current_user)):
    return list_paper_trades(current_user["id"])


@app.post("/api/paper-trades")
async def create_paper_trade(payload: PaperTradeCreate, current_user: dict = Depends(get_current_user)):
    symbol = payload.symbol.upper().strip()
    market = (payload.market or "AU").upper().strip()
    side = (payload.side or "LONG").upper().strip()
    if side not in {"LONG", "SHORT"}:
        raise HTTPException(status_code=400, detail="side must be LONG or SHORT")

    stock_data = get_stock_data(symbol, market)
    live_price = float(stock_data.get("current_price") or 0)
    if live_price <= 0:
        raise HTTPException(status_code=404, detail=f"Unable to fetch market price for {symbol}")

    # ── Actual fill price ─────────────────────────────────────────────────────
    # The user may have filled at a price different from the live quote (e.g.
    # they acted on a Telegram signal minutes/hours earlier, or got a partial
    # fill at a limit price).  We record their actual fill as the entry so that
    # PnL math is accurate.  We allow up to ±10% deviation from live quote as a
    # sanity guard against typos; outside that range we fall back to live price.
    if payload.actual_price and payload.actual_price > 0:
        deviation_pct = abs(payload.actual_price - live_price) / live_price
        if deviation_pct <= 0.10:
            entry_price = round(float(payload.actual_price), 4)
        else:
            entry_price = live_price  # suspicious deviation — use live price
            payload = payload.model_copy(update={"notes": (
                (payload.notes or "") +
                f" [Note: Actual price ${payload.actual_price:.3f} deviated >{deviation_pct*100:.1f}% from live ${live_price:.3f} — using live price.]"
            ).strip()})
    else:
        entry_price = live_price

    # ── Cap-tier calibrated targets ───────────────────────────────────────────
    # Large/mid cap: 4% target, 2% stop → R:R 2.0
    # Small cap: 5% target, 2.5% stop → R:R 2.0
    # Aligned for 3-4% per-cycle compound strategy (12-13% annualised).
    signal = get_probability_and_score(symbol)
    avg_vol_5d = float(stock_data.get("avg_volume_5d") or 0)
    dollar_volume = avg_vol_5d * entry_price
    _params = _get_strategy_params(dollar_volume)

    if side == "LONG":
        target_price = round(entry_price * (1 + _params["target_pct"]), 4)
    else:  # SHORT
        target_price = round(entry_price * (1 - _params["target_pct"]), 4)

    # ── Stop-loss (cap-tier calibrated for R:R alignment) ─────────────────────
    if side == "SHORT":
        stop_loss_price = round(entry_price * (1 + _params["stop_pct"]), 4)
    else:
        stop_loss_price = round(entry_price * (1 - _params["stop_pct"]), 4)

    trade_id = str(uuid4())

    try:
        with db_conn() as conn:
            conn.execute(
                text("""
                    INSERT INTO paper_trades
                    (id, user_id, symbol, market, side, quantity, entry_price, current_price,
                     target_price, status, signal_score, signal_trend, signal_warning, notes, peak_price,
                     stop_loss_price, take_profit_price, trailing_stop_pct, review_date,
                     position_stage, recommendation_action, source_reason, updated_at)
                VALUES
                    (:id, :user_id, :symbol, :market, :side, :quantity, :entry_price, :current_price,
                     :target_price, 'open', :signal_score, :signal_trend, :signal_warning, :notes, :entry_price,
                     :stop_loss_price, :take_profit_price, :trailing_stop_pct, :review_date,
                     :position_stage, :recommendation_action, :source_reason, :updated_at)
            """),
            {
                "id": trade_id,
                "user_id": current_user["id"],
                "symbol": symbol,
                "market": market,
                "side": side,
                "quantity": float(payload.quantity or 1),
                "entry_price": entry_price,
                "current_price": live_price,
                "target_price": target_price,
                "signal_score": signal.get("score"),
                "signal_trend": signal.get("trend"),
                "signal_warning": signal.get("warning_message"),
                "notes": payload.notes,
                "peak_price": entry_price,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": target_price,
                "trailing_stop_pct": _params["trailing_stop_pct"],
                "review_date": datetime.utcnow() + timedelta(days=1),
                "position_stage": "entered",
                "recommendation_action": "SHORT" if side == "SHORT" else "BUY",
                "source_reason": signal.get("warning_message") or signal.get("quality_reason") or signal.get("streak_label"),
                "updated_at": datetime.utcnow(),
            },
        )
    except sqlalchemy.exc.IntegrityError:
        raise HTTPException(status_code=400, detail=f"You already have an open {side} position for {symbol}.")

    log_position_event(trade_id, current_user["id"], "opened", f"Opened {side} tracking position for {symbol}", {
        "symbol": symbol,
        "market": market,
        "entry_price": entry_price,
        "live_price_at_signal": live_price,
        "target_price": target_price,
        "stop_loss_price": stop_loss_price,
    })

    # Ensure this trade is synced with the Advice Execution Engine so the background 
    # Positions Monitor tracks it just like a Telegram trade.
    try:
        val = get_valuation_metrics(symbol)
        analyst_target = val.get("analyst_target_mean") or 0
        
        _create_advice_action_for_user(
            user_id=current_user["id"],
            symbol=symbol,
            market=market,
            action_type="BUY" if side == "LONG" else "SHORT",
            quantity=float(payload.quantity or 1),
            execution_price=entry_price,
            commission=0,
            advice_cache_key=None,
            source_message_type="web_wealth_tab",
            notes=payload.notes,
        )
        
        # Send Telegram confirmation if configured
        recipients = get_user_telegram_recipients(current_user["id"])
        if recipients:
            gross_amount = float(payload.quantity or 1) * entry_price
            stop_pct_display = int(_params["stop_pct"] * 100)
            target_pct = round(((analyst_target - entry_price) / entry_price) * 100, 2) if analyst_target and entry_price > 0 else None
            target_str = f"${analyst_target:.2f} ({target_pct:+.1f}%)" if analyst_target and target_pct else "N/A"
            buy_msg = (
                f"<b>✅ {'BUY' if side == 'LONG' else 'SHORT'} RECORDED (Web) — {symbol}.{market}</b>\n"
                f"Shares: {float(payload.quantity or 1)} @ ${entry_price:.2f}\n"
                f"Total invested: ${gross_amount:,.2f}\n"
                f"Stop loss target: ~${stop_loss_price:.2f} (-{stop_pct_display}%)\n"
                f"Analyst target: {target_str}\n"
                f"Monitoring active 📡 — you'll be notified when conditions change."
            )
            _send_telegram_payload(recipients, buy_msg)
    except Exception as e:
        print(f"Error syncing paper trade to advice engine/telegram: {e}")

    return {
        "id": trade_id,
        "symbol": symbol,
        "market": market,
        "side": side,
        "entry_price": entry_price,
        "live_price_at_signal": live_price,
        "target_price": target_price,
        "signal": signal,
    }


@app.patch("/api/paper-trades/{trade_id}")
async def update_paper_trade(trade_id: str, payload: PaperTradeUpdate, current_user: dict = Depends(get_current_user)):
    updates = []
    params = {"trade_id": trade_id, "user_id": current_user["id"], "updated_at": datetime.utcnow()}

    if payload.stop_loss_price is not None:
        updates.append("stop_loss_price = :stop_loss_price")
        params["stop_loss_price"] = float(payload.stop_loss_price)
    if payload.take_profit_price is not None:
        updates.append("take_profit_price = :take_profit_price")
        params["take_profit_price"] = float(payload.take_profit_price)
    if payload.trailing_stop_pct is not None:
        updates.append("trailing_stop_pct = :trailing_stop_pct")
        params["trailing_stop_pct"] = float(payload.trailing_stop_pct)
    if payload.review_date is not None:
        updates.append("review_date = :review_date")
        params["review_date"] = payload.review_date
    if payload.position_stage is not None:
        updates.append("position_stage = :position_stage")
        params["position_stage"] = payload.position_stage
    if payload.notes is not None:
        updates.append("notes = :notes")
        params["notes"] = payload.notes

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    updates.append("updated_at = :updated_at")
    with db_conn() as conn:
        result = conn.execute(
            text(f"UPDATE paper_trades SET {', '.join(updates)} WHERE id = :trade_id AND user_id = :user_id"),
            params,
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Paper trade not found")

    log_position_event(trade_id, current_user["id"], "updated", "Updated trade plan", {
        key: value for key, value in params.items() if key not in {"trade_id", "user_id", "updated_at"}
    })
    return {"ok": True}


@app.post("/api/paper-trades/{trade_id}/close")
async def close_paper_trade(trade_id: str, payload: PaperTradeClose, current_user: dict = Depends(get_current_user)):
    with db_conn() as conn:
        row = conn.execute(
            text("""
                SELECT id, symbol, market, side, quantity, entry_price, target_price, status,
                       signal_score, signal_trend, signal_warning, notes, created_at, closed_at,
                       peak_price, stop_loss_price, take_profit_price, trailing_stop_pct,
                       review_date, last_alert_at, position_stage, recommendation_action, source_reason
                FROM paper_trades
                WHERE id = :trade_id AND user_id = :user_id
            """),
            {"trade_id": trade_id, "user_id": current_user["id"]},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper trade not found")

        current_price = float(get_stock_data(row[1], row[2]).get("current_price") or row[5])
        conn.execute(
            text("""
                UPDATE paper_trades
                SET status = 'closed', current_price = :current_price, closed_at = :closed_at,
                    notes = COALESCE(:notes, notes), position_stage = 'closed', updated_at = :closed_at
                WHERE id = :trade_id AND user_id = :user_id
            """),
            {
                "trade_id": trade_id,
                "user_id": current_user["id"],
                "current_price": current_price,
                "closed_at": datetime.utcnow(),
                "notes": payload.notes,
            },
        )

    log_position_event(trade_id, current_user["id"], "closed", f"Closed tracking position for {row[1]}", {
        "current_price": current_price,
    })

    return {"status": "closed", "trade_id": trade_id, "current_price": round(current_price, 2)}


# ── Scheduler (generate weekly on Mondays) ────────────────────────────────────
def _scheduled_weekly_generation():
    for m in ["AU", "US", "IN"]:
        try:
            generate_weekly_digest_for_market(m)
        except Exception:
            pass


def _scheduled_daily_digest():
    try:
        market = os.getenv("DAILY_DIGEST_MARKET", "AU").upper()
        local_digest_key = _daily_digest_cache_key()
        digest = get_daily_digest_cache(
            market,
            limit=int(os.getenv("DAILY_DIGEST_LIMIT", "5")),
            cache_key=local_digest_key,
        )
        digest_key = digest.get("cache_key") or local_digest_key
        with db_conn() as conn:
            users = conn.execute(
                text(
                    """
                    SELECT DISTINCT u.id
                    FROM users u
                    JOIN user_telegram_recipients r ON r.user_id = u.id
                    WHERE r.is_active = 1
                    """
                )
            ).fetchall()
        for row in users:
            user_id = row[0]
            if has_digest_been_sent(user_id, digest["market"], digest_key):
                continue
            recipients = get_user_telegram_recipients(user_id)
            if not recipients:
                continue
            _send_telegram_payload(
                digest["summary"],
                recipients,
                user_id=user_id,
                message_type="daily_digest",
                market=digest["market"],
                delivery_mode="auto",
                digest_key=digest_key,
            )
    except Exception as exc:
        print(f"daily digest scheduler failed: {exc}")


def _scheduled_broad_scan_precompute():
    """Pre-compute broad ASX 200+ wealth scan at 5AM and cache to DB.

    This runs in a background thread so the main API isn't blocked.
    The broad scan hits 200+ tickers sequentially to avoid overwhelming yfinance.

    Position entry is gated by WFO capital state — if INSUFFICIENT_DATA or RED,
    the scan still runs (to accumulate data for future WFO evaluation) but new
    positions are blocked by _wfo_position_gate().
    """
    import time as _sleep_time
    start = datetime.utcnow()
    market = "AU"

    if getattr(_scheduled_broad_scan_precompute, "_running", False):
        print("[BroadScan] Scan already running — skipping duplicate.")
        return
    _scheduled_broad_scan_precompute._running = True

    wfo = get_current_wfo_state()
    wfo_state = wfo["state"]
    gate = _wfo_position_gate()
    # In UAT mode, cap reduces to 30 to stay within EODHD 20 calls/min free tier
    # and Groq 30 req/min limits.
    import time as _sleep_time
    market = "AU"
    broad_scan_cap = int(os.getenv("BROAD_SCAN_CAP", "500"))
    if UAT_MODE:
        broad_scan_cap = min(broad_scan_cap, 30)
        _EODHD_MIN_INTERVAL_INTERNAL = 3.0
    else:
        _EODHD_MIN_INTERVAL_INTERNAL = _EODHD_MIN_INTERVAL
    # Use config/universe.py liquid symbols (~467) instead of full 1661 EODHD universe
    from config.universe import get_universe_symbols
    core_syms = get_universe_symbols("core")
    broad_syms = get_universe_symbols("broad")
    symbol_pool = list(set(core_syms + broad_syms))
    print(f"[BroadScan] Universe: {len(symbol_pool)} liquid symbols (core={len(core_syms)}, broad={len(broad_syms)})")
    # Apply broad_scan_cap if set
    # Apply broad_scan_cap — use deterministic sort (by symbol) for reproducible results
    if broad_scan_cap > 0 and broad_scan_cap < len(symbol_pool):
        symbol_pool.sort()  # deterministic: alphabetical order, same results every run
        symbol_pool = symbol_pool[:broad_scan_cap]
        print(f"[BroadScan] Capped to {broad_scan_cap} symbols")

    # yfinance dead ticker tracking via centralized YFinanceService
    from yfinance_service import YFinanceService
    yfinance_dead = YFinanceService.get_dead_set()
    if yfinance_dead:
        symbol_pool = [s for s in symbol_pool if s not in yfinance_dead]
        print(f"[BroadScan] Skipping {len(yfinance_dead)} tickers known to lack yfinance data")

    scanned = 0
    candidates = []

    scan_start = datetime.utcnow()
    print(f"[BroadScan] Universe: {len(symbol_pool)} liquid tickers (EODHD storage={'yes' if EODHD_API_KEY else 'no'}) — scanning at {scan_start.isoformat()}")

    new_dead = set()
    scan_workers = 5  # concurrency: 100K EODHD calls/day, ~500 tickers = ~100 calls/worker
    def _score_one(sym):
        try:
            r = _score_wealth_candidate(sym, market)
            return ("ok", sym, r)
        except:
            return ("dead", sym, None)
    with ThreadPoolExecutor(max_workers=scan_workers) as ex:
        futs = {ex.submit(_score_one, s): s for s in symbol_pool}
        for f in as_completed(futs):
            s = futs[f]
            try:
                st, _, r = f.result(timeout=90)
                scanned += 1
                if r and (r.get("score") or 0) >= 35.0:
                    candidates.append(r)
                elif r is None:
                    new_dead.add(s)
                if scanned % 50 == 0:
                    print(f"[BroadScan] {scanned}/{len(symbol_pool)} processed, {len(candidates)} candidates")
            except:
                new_dead.add(s)

    # Persist dead tickers via centralized YFinanceService
    if new_dead:
        for sym in new_dead:
            YFinanceService.mark_dead(sym, "broad_scan_null")
        print(f"[BroadScan] {len(new_dead)} additional tickers failed — cached for future skip")

    # Sort by wealth_rank descending
    candidates.sort(key=lambda x: x.get("wealth_rank", 0) or x.get("score", 0), reverse=True)

    # Apply Sector Concentration Cap (Max 15 per sector to enforce diversification)
    # ── Sector diversification ──────────────────────────────────────────────
    sector_counts = {}
    diversified_candidates = []
    for c in candidates:
        sector = (c.get("valuation_metrics") or {}).get("sector", "Unknown")
        if sector_counts.get(sector, 0) < 15:
            diversified_candidates.append(c)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

    candidates = diversified_candidates

    # ── Model tier classification (before DB write, so AI pipeline can read tiers) ──
    _enrich_candidates_with_tiers(candidates)

    try:
        with db_conn() as conn:
            did = str(uuid4())
            picks_json = json.dumps(candidates)
            conn.execute(text("""
                INSERT INTO wealth_scan_cache (id, market, scan_mode, scanned_count, candidates_found, picks, generated_at)
                VALUES (:id, :market, :mode, :scanned, :found, :picks, CURRENT_TIMESTAMP)
            """), {
                "id": did,
                "market": market,
                "mode": "broad",
                "scanned": scanned,
                "found": len(candidates),
                "picks": picks_json,
            })
            conn.execute(text("""
                INSERT INTO wealth_scan_history (id, market, scan_mode, scanned_count, candidates_found, picks, generated_at)
                VALUES (:id, :market, :mode, :scanned, :found, :picks, CURRENT_TIMESTAMP)
            """), {
                "id": did,
                "market": market,
                "mode": "broad",
                "scanned": scanned,
                "found": len(candidates),
                "picks": picks_json,
            })
            now_ts = datetime.utcnow()
            for c in candidates:
                conn.execute(text("""
                    INSERT INTO wealth_builder_evaluations
                        (symbol, market, wealth_rank, score, prob_ge_5pct,
                         predicted_change_pct, price_at_screen,
                         entry_zone, target_tier, model_score, screened_at)
                    VALUES (:symbol, :market, :wr, :sc, :p5, :pc, :pas, :ez, :tt, :ms, :at)
                    ON CONFLICT (symbol, screened_at) DO NOTHING
                """), {
                    "symbol": c.get("symbol", ""),
                    "market": market,
                    "wr": c.get("wealth_rank"),
                    "sc": c.get("score"),
                    "p5": c.get("prob_ge_5pct"),
                    "pc": c.get("predicted_change_pct"),
                    "pas": c.get("current_price"),
                    "ez": (c.get("entry_timing") or {}).get("entry_zone", ""),
                    "tt": c.get("_target_tier", ""),
                    "ms": c.get("_model_score", 0),
                    "at": now_ts,
                })
    except Exception as e:
        print(f"[BroadScan] DB write failed: {e}")

    # --- Auto-send Telegram Alerts for Model-Tiered Signals ---
    try:
        tier10 = [c for c in candidates if c.get("_target_tier") == "10pct"]
        tier8 = [c for c in candidates if c.get("_target_tier") == "8pct"]
        print(f"[BroadScan] Model tiers: {len(tier10)}×10%, {len(tier8)}×8%, "
              f"{len(candidates)-len(tier10)-len(tier8)}×watch")

        today_key = datetime.utcnow().date().isoformat()
        with db_conn() as conn:
            users = conn.execute(text("SELECT id FROM users")).fetchall()

        for tier_set, tier_label in [(tier10, "🚀 10% TARGET TIER"), (tier8, "📈 8% COMPOUND TIER")]:
            if not tier_set:
                continue
            for user in users:
                uid = user[0]
                recipients = get_user_telegram_recipients(uid)
                if not recipients:
                    continue
                # ── 5AM per-stock alerts SUPPRESSED — only 8AM AI report fires ──
                continue
                for hc in tier_set:
                    sym = hc["symbol"]
                    digest_key = f"auto_tier_{sym}_{today_key}"
                    if has_digest_been_sent(uid, "AU", digest_key):
                        continue
                    msg = _build_tier_signal_message(
                        hc["symbol"], hc.get("name", ""), hc,
                        hc.get("valuation", {}), hc.get("prediction", {}),
                        hc.get("entry_timing", {}), tier_label=hc.get("_tier_label", "")
                    )
                    _send_telegram_payload(
                        msg, recipients, user_id=uid, message_type="daily_digest",
                        market="AU", digest_key=digest_key, source="broad_scan_tiered"
                    )
                    time.sleep(0.2)
    except Exception as e:
        print(f"[BroadScan] Auto-alerting failed: {e}")

    duration = (datetime.utcnow() - scan_start).total_seconds()
    print(f"[BroadScan] Complete: {scanned} scanned, {len(candidates)} candidates in {duration:.0f}s. Sent auto-alerts if any.")
    print(f"[BroadScan] WFO gate: {wfo_state} — {gate['reason']}")
    _scheduled_broad_scan_precompute._running = False


# ═══════════════════════════════════════════════════════════════════════════════
# UAT HEALTH WATCHDOG — fully automated 30-day burn-in monitoring
# ═══════════════════════════════════════════════════════════════════════════════

def _scheduled_uat_health_report():
    """Daily UAT health report (4:30 PM AEST, after WFO).

    Summarizes everything a human would otherwise need to check manually:
      - Did the broad scan run today? How many candidates?
      - Is the EODHD API healthy? Any rate-limit failures?
      - How many signals have accumulated toward the 30d WFO threshold?
      - What's the current WFO capital gate state?
      - Are there any stalled paper trades that need attention?
      - How many Tavily credits were consumed today?

    All of this is automated — no human needs to log in and run queries.
    If something breaks (scan failed, EODHD down, zero candidates), the
    report flags it with ⚠️ so you know to investigate.
    """
    _ensure_wfo_table()

    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)

    lines = [f"<b>🏥 UAT HEALTH REPORT — {today.isoformat()}</b>", ""]

    # 1. Broad scan status
    try:
        with db_conn() as conn:
            scan = conn.execute(text("""
                SELECT scanned_count, candidates_found, generated_at
                FROM wealth_scan_cache
                WHERE scan_mode = 'broad'
                  AND generated_at >= :yesterday
                ORDER BY generated_at DESC LIMIT 1
            """), {"yesterday": yesterday.isoformat()}).fetchone()

        if scan and scan[0]:
            lines.append(f"✅ Broad scan: {scan[0]} stocks → {scan[1]} candidates at {str(scan[2])[:19]}")
        else:
            lines.append(f"⚠️ Broad scan: DID NOT RUN today. Check scheduler / EODHD key.")
    except Exception as e:
        lines.append(f"⚠️ Broad scan check failed: {e}")

    # 2. EODHD API health
    try:
        eodhd_ok = bool(EODHD_API_KEY)
        if eodhd_ok:
            # Quick test: fetch one stock to verify API still works
            test_ticker = format_ticker("BHP", "AU").replace(".AX", ".AU")
            _eodhd_rate_limit()
            r = requests.get(
                f"https://eodhd.com/api/real-time/{test_ticker}",
                params={"api_token": EODHD_API_KEY, "fmt": "json"},
                timeout=8
            )
            eodhd_ok = r.status_code == 200
            lines.append(f"{'✅' if eodhd_ok else '⚠️'} EODHD API: {'healthy' if eodhd_ok else f'DOWN (status {r.status_code})'}")
        else:
            lines.append(f"⚠️ EODHD API: NO KEY SET — data pipeline disabled")
    except Exception:
        lines.append(f"⚠️ EODHD API: CONNECTION FAILED")

    # 3. Signal accumulation toward WFO Day 30
    try:
        with db_conn() as conn:
            for horizon_days in [30, 63, 90]:
                cutoff = today - timedelta(days=horizon_days)
                cache_count = conn.execute(text("""
                    SELECT COUNT(DISTINCT DATE(screened_at)) FROM wealth_builder_evaluations
                    WHERE screened_at <= :cutoff
                """), {"cutoff": cutoff.isoformat()}).fetchone()
                wfo_row = conn.execute(text("""
                    SELECT notes FROM wfo_metrics
                    WHERE horizon_window_days = :hd
                    ORDER BY run_at DESC LIMIT 1
                """), {"hd": horizon_days}).fetchone()
                status = wfo_row[0][:60] if wfo_row and wfo_row[0] else "not yet checked"

                earliest_scan = conn.execute(text("""
                    SELECT MIN(screened_at) FROM wealth_builder_evaluations
                """)).fetchone()

                if earliest_scan and earliest_scan[0]:
                    earliest_date = earliest_scan[0].date() if hasattr(earliest_scan[0], 'date') else earliest_scan[0]
                    if isinstance(earliest_date, datetime):
                        earliest_date = earliest_date.date()
                    elif isinstance(earliest_date, str):
                        try:
                            earliest_date = datetime.strptime(earliest_date.split(' ')[0], '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    days_accumulated = max(0, (today - earliest_date).days) if hasattr(earliest_date, 'year') else 0
                else:
                    days_accumulated = 0

                ratio = min(1.0, days_accumulated / horizon_days) if horizon_days > 0 else 0
                filled = int(30 * ratio)
                bar = "█" * filled + "░" * (30 - filled)
                lines.append(
                    f"  {'🟢' if days_accumulated >= horizon_days else '🟡'} "
                    f"{horizon_days}d horizon: {days_accumulated}/{horizon_days} days [{bar}] | "
                    f"Eval rows ≥{horizon_days}d old: {cache_count[0] or 0} | {status}"
                )
    except Exception as e:
        lines.append(f"⚠️ Signal accumulation check failed: {e}")

    # 4. WFO capital gate state
    try:
        wfo = get_current_wfo_state()
        emoji = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴", "RED_MANUAL_REVIEW": "🔴", "INSUFFICIENT_DATA": "🟡"}
        lines.append(f"")
        lines.append(f"{emoji.get(wfo['state'], '⚪')} <b>Capital Gate: {wfo['state']}</b>")
        lines.append(f"  {wfo['rules']['description']}")
        if wfo['state'] != "INSUFFICIENT_DATA":
            lines.append(f"  Max positions: {wfo['rules']['max_positions']} | "
                        f"Max single: {wfo['rules']['max_single_position_pct']}% | "
                        f"Max sector: {wfo['rules']['max_sector_pct']}%")
    except Exception as e:
        lines.append(f"⚠️ WFO state check failed: {e}")

    # 5. Paper trade summary
    try:
        with db_conn() as conn:
            open_count = conn.execute(text(
                "SELECT COUNT(*) FROM paper_trades WHERE status = 'open'"
            )).fetchone()
            stale_count = conn.execute(text("""
                SELECT COUNT(*) FROM paper_trades
                WHERE status = 'open' AND created_at < :stale
            """), {"stale": today - timedelta(days=14)}).fetchone()
            lines.append(f"")
            lines.append(f"📊 <b>Paper Trades:</b> {open_count[0] or 0} open")
            if (stale_count[0] or 0) > 0:
                lines.append(f"  ⚠️ {stale_count[0]} positions stale (>14d) — may need review")
    except Exception:
        pass

    # 6. Tavily credit consumption
    try:
        try:
            from agentic_brain import _TAVILY_CALL_COUNT as tavily_used
        except ImportError:
            tavily_used = 0
        lines.append(f"")
        lines.append(f"🔍 Tavily credits consumed: ~{tavily_used} today (250/day cap, monthly reset)")
    except Exception:
        pass

    lines.append(f"")
    lines.append(f"<b>🤖 Model Training Status</b>")
    try:
        with db_conn() as conn:
            last_train = conn.execute(text("""
                SELECT trained_at, sample_size, in_sample_hit_rate
                FROM model_weights_by_date
                ORDER BY trained_at DESC LIMIT 1
            """)).fetchone()
            mts_count = conn.execute(text("SELECT COUNT(*) FROM model_training_set")).fetchone()
            ohlc_count = conn.execute(text("SELECT COUNT(DISTINCT symbol) FROM eod_ohl_history")).fetchone()
            
            if last_train:
                lines.append(f"  ✅ Last trained: {last_train[1]} samples, in-sample acc: {float(last_train[2])*100:.1f}%")
            else:
                lines.append(f"  🟡 No model trained yet — matrix: {mts_count[0] or 0} rows, OHLC: {ohlc_count[0] or 0} symbols")

            channel_cal = conn.execute(text("""
                SELECT calibrated_at, momentum, institutional, fundamental, sample_size
                FROM channel_hit_rates ORDER BY calibrated_at DESC LIMIT 1
            """)).fetchone()
            if channel_cal:
                lines.append(f"  📊 Channel rates [M:{float(channel_cal[1])*100:.0f}% I:{float(channel_cal[2])*100:.0f}% F:{float(channel_cal[3])*100:.0f}%] ({channel_cal[4]} signals)")
            else:
                lines.append(f"  🟡 Channel calibration not yet run")

            ai_runs = conn.execute(text("""
                SELECT run_date, total_candidates, ai_approved, approval_rate_pct
                FROM daily_ai_runs ORDER BY run_date DESC LIMIT 3
            """)).fetchall()
            if ai_runs:
                ai_line = "  🤖 Recent AI approvals: " + " | ".join(
                    f"{r[0]}: {r[2]}/{r[1]} ({float(r[3] or 0):.0f}%)" for r in ai_runs
                )
                lines.append(ai_line)

            # Check for stale jobs
            stale_jobs = conn.execute(text("""
                SELECT job_name, started_at FROM job_runs
                WHERE status = 'running' AND started_at < NOW() - INTERVAL '2 hours'
                ORDER BY started_at DESC LIMIT 5
            """)).fetchall()
            if stale_jobs:
                lines.append(f"  ⚠️ {len(stale_jobs)} stale jobs (stuck >2h): " +
                             ", ".join([r[0] for r in stale_jobs]))
    except Exception as e:
        lines.append(f"  ⚠️ Training metrics check failed: {e}")

    lines.append(f"")
    da = days_accumulated if 'days_accumulated' in dir() else 0
    next_milestone = next((m for m in [30, 63, 90] if da < m), 90)
    lines.append(f"<i>Day {da}/{next_milestone} toward WFO Sharpe evaluation. "
                f"Next milestone: {max(0, next_milestone - da)} days.</i>")
    lines.append(f"")
    lines.append(f"<b>🔄 Safe shutdown window: 5:00 PM – 4:30 AM AEST</b>")
    lines.append(f"   After today's 4:30 PM health report, safe to power off until 4:30 AM tomorrow.")
    lines.append(f"   Startup catch-up will re-run any missed jobs on next boot.")

    message = "\n".join(lines)

    # Count warnings — if any present, suppress this job execution as needing human review
    has_warnings = any(l.startswith("⚠️") for l in lines)

    # Broadcast to all Telegram users
    try:
        with db_conn() as conn:
            users = conn.execute(text("SELECT id FROM users")).fetchall()
        for user in users:
            uid = user[0]
            recipients = get_user_telegram_recipients(uid)
            if not recipients:
                continue
            digest_key = f"uat_health_{today.isoformat()}"
            if not has_digest_been_sent(uid, market="AU", digest_key=digest_key):
                pass  # UAT health alerts SUPPRESSED
            # if not has_digest_been_sent(uid, market="AU", digest_key=digest_key):
            #     _send_telegram_payload(
            #         message, recipients, user_id=uid,
            #         message_type="uat_health", market="AU",
            #         digest_key=digest_key, source="uat_health_watchdog",
            #         delivery_mode="auto",
            #     )

            # ── URGENT ALERT: if anything has ⚠️, send a separate high-priority message ──
            if has_warnings and False:  # UAT health alerts SUPPRESSED
                alert_key = f"uat_alert_{today.isoformat()}"
                if not has_digest_been_sent(uid, market="AU", digest_key=alert_key):
                    alert_lines = [
                        "🚨 <b>UAT HEALTH ALERT — ACTION REQUIRED</b>",
                        "",
                        "The 4:30 PM health report detected ⚠️ conditions:",
                        "",
                    ]
                    warning_lines = [l for l in lines if l.startswith("⚠️")]
                    alert_lines.extend(warning_lines[:5])
                    alert_lines.append("")
                    alert_lines.append(f"Full report sent separately. Investigate before next 5 AM scan.")
                    alert_lines.append(f"Shutdown window remains: 5:00 PM – 4:30 AM AEST.")
                    alert_message = "\n".join(alert_lines)
                    _send_telegram_payload(
                        alert_message, recipients, user_id=uid,
                        message_type="uat_health_alert",
                        market="AU",
                        digest_key=alert_key,
                        source="uat_health_alert",
                        delivery_mode="auto",
                    )
    except Exception as e:
        print(f"[UATHealth] Broadcast failed: {e}")

    print(f"[UATHealth] Report sent. {days_accumulated if 'days_accumulated' in dir() else 0}/30 days to WFO Day 30.")
    # ── Expanded universe via EODHD (falls back to hardcoded if key not set) ──
    # BROAD_SCAN_CAP caps the number of tickers per run so a mini-PC (6800H)
    # finishes in a reasonable time (~500 stocks × 0.3s = ~2.5 minutes).


def _scheduled_paper_trade_monitor(max_trades_override: Optional[int] = None):
    try:
        now = datetime.utcnow()
        min_interval_minutes = max(1, int(os.getenv("PAPER_MONITOR_MIN_INTERVAL_MIN", "10")))
        configured_max = max(10, int(os.getenv("PAPER_MONITOR_MAX_TRADES", "250")))
        if max_trades_override is not None:
            max_trades_per_cycle = max(10, min(int(max_trades_override), configured_max))
        else:
            max_trades_per_cycle = configured_max
        check_before = now - timedelta(minutes=min_interval_minutes)
        price_cache: dict[tuple[str, str], float] = {}
        recipients_cache: dict[str, list[dict]] = {}
        valuation_cache: dict[str, dict] = {}

        with db_conn() as conn:
            trades = conn.execute(text("""
                SELECT id, user_id, symbol, market, side, quantity, entry_price, current_price, target_price,
                       peak_price, stop_loss_price, take_profit_price, trailing_stop_pct, position_stage,
                       last_alert_at, last_checked_at, created_at, notes
                FROM paper_trades 
                WHERE status = 'open'
                  AND (last_checked_at IS NULL OR last_checked_at <= :check_before)
                ORDER BY COALESCE(last_checked_at, created_at) ASC
                LIMIT :max_trades
            """), {"check_before": check_before, "max_trades": max_trades_per_cycle}).fetchall()

            if not trades:
                return
            
            for t in trades:
                (trade_id, user_id, symbol, market, side, qty, entry, last_cp, target,
                 peak, stop_loss_price, take_profit_price, trailing_stop_pct, position_stage,
                 last_alert_at, last_checked_at, created_at, notes) = t

                cache_key = (symbol, market or "AU")
                if cache_key in price_cache:
                    cp = price_cache[cache_key]
                else:
                    stock_data = get_stock_data(symbol, market)
                    cp = float(stock_data.get("current_price") or last_cp or 0)
                    price_cache[cache_key] = cp

                recipients = recipients_cache.get(user_id)
                if recipients is None:
                    recipients = get_user_telegram_recipients(user_id)
                    recipients_cache[user_id] = recipients
                
                if cp <= 0:
                    conn.execute(text("UPDATE paper_trades SET last_checked_at = :now WHERE id = :tid"), {"now": now, "tid": trade_id})
                    continue

                in_cooldown = bool(last_alert_at and isinstance(last_alert_at, datetime) and (now - last_alert_at) < timedelta(hours=6))
                stage_update = None
                alert = None

                # ── P&L context helpers ────────────────────────────────────────
                direction = -1.0 if (side or "LONG") == "SHORT" else 1.0
                entry_f = float(entry or 0)
                qty_f = float(qty or 1)
                gross_pnl = (cp - entry_f) * qty_f * direction
                pnl_pct = ((cp - entry_f) / entry_f * 100 * direction) if entry_f > 0 else 0
                pnl_emoji = "🟢" if gross_pnl >= 0 else "🔴"
                days_held = (now - created_at).days if created_at else 0

                # ── Valuation / analyst target context ────────────────────────
                val = valuation_cache.get(symbol)
                if val is None:
                    try:
                        val = get_valuation_metrics(symbol)
                        valuation_cache[symbol] = val
                    except Exception:
                        val = {}

                target_mean_str = f"${val.get('analyst_target_mean'):.2f}" if val.get('analyst_target_mean') else "N/A"
                upside_str = f"{val.get('analyst_upside_pct'):+.1f}%" if val.get('analyst_upside_pct') is not None else "N/A"
                days_to_e = val.get('days_to_earnings')

                def _rich_alert(headline: str, action_hint: str) -> str:
                    """Build a detailed Telegram HTML alert string."""
                    return (
                        f"<b>{headline}</b>\n\n"
                        f"📌 <b>{symbol}</b> ({market or 'AU'}) | {side or 'LONG'}\n"
                        f"  Entry: <b>${entry_f:.2f}</b> → Now: <b>${cp:.2f}</b>\n"
                        f"  {pnl_emoji} P&amp;L: <b>{'%+.2f' % gross_pnl} ({pnl_pct:+.1f}%)</b> | Held: {days_held}d\n"
                        f"  Analyst target: {target_mean_str} | Upside: {upside_str}\n"
                        + (f"  📅 Earnings in: {days_to_e}d\n" if days_to_e is not None and days_to_e >= 0 else "")
                        + f"\n💡 <b>Action hint:</b> {action_hint}"
                    )

                if side == 'LONG':
                    new_peak = max(peak or entry, cp)
                    drop_from_peak = (new_peak - cp) / new_peak if new_peak > 0 else 0
                    trailing_pct = float(trailing_stop_pct or 3.0) / 100.0

                    if take_profit_price and cp >= float(take_profit_price) and position_stage != 'trim_signal':
                        stage_update = 'trim_signal'
                        alert = _rich_alert(
                            "🎯 TAKE PROFIT TRIGGER",
                            f"Price {cp:.2f} ≥ target {float(take_profit_price):.2f}. "
                            f"Consider trimming 50% and raising trailing stop to lock gains."
                        )
                    elif stop_loss_price and cp <= float(stop_loss_price) and position_stage != 'exit_signal':
                        stage_update = 'exit_signal'
                        alert = _rich_alert(
                            "🛑 STOP LOSS TRIGGER",
                            f"Price {cp:.2f} ≤ stop {float(stop_loss_price):.2f}. "
                            f"Thesis may be broken. Review for exit to protect capital."
                        )
                    elif drop_from_peak >= trailing_pct and position_stage != 'exit_signal':
                        stage_update = 'exit_signal'
                        alert = _rich_alert(
                            "⚠️ TRAILING STOP TRIGGER",
                            f"Down {drop_from_peak*100:.1f}% from peak ${new_peak:.2f}. "
                            f"Momentum reversing. Consider closing to preserve gains."
                        )
                    # Time stop: position flat > 30 days with < 2% gain
                    elif days_held >= 30 and abs(pnl_pct) < 2.0 and position_stage not in ('exit_signal', 'trim_signal'):
                        stage_update = 'review'
                        alert = _rich_alert(
                            "⏱️ TIME STOP — 30-DAY REVIEW",
                            f"Position is flat after {days_held}d ({pnl_pct:+.1f}%). "
                            f"Signal may have failed. Consider freeing capital for higher-conviction ideas."
                        )

                    # ── Exit Engine (v2: catastrophe-stop / time-stop / CGT deferral) ──
                    if stage_update is None:
                        try:
                            from exit_engine import compute_exit_plan
                            atr_pct = 0.03
                            try:
                                hist = get_historical_data(symbol, period="3mo")
                                if hist is not None and len(hist) >= 20:
                                    ind = calculate_technical_indicators(hist)
                                    atr_pct = (ind.get("atr_pct") or 3.0) / 100.0
                            except Exception:
                                atr_pct = 0.03
                            entry_date_val = created_at.date() if created_at else date.today()
                            sentinel = position_stage if position_stage in ("INTACT", "WEAKENED", "BROKEN") else "INTACT"
                            exit_plan = compute_exit_plan(
                            symbol=symbol,
                            entry_date=entry_date_val,
                            entry_price=entry_f,
                            current_price=cp,
                            atr_20d_pct=atr_pct,
                            days_to_earnings=days_to_e,
                            sentinel_verdict=sentinel,
                            portfolio_breaker_level=breaker_level if 'breaker_level' in dir() else "NORMAL",
                            )
                            es = exit_plan.get("exit_signal", "HOLD")
                            if es == "FORCE_EXIT" or es == "EXIT":
                                stage_update = "exit_signal"
                                alert = _rich_alert(
                                    f"🚪 EXIT ENGINE: {exit_plan.get('exit_reason', 'EXIT')}",
                                    f"Day {days_held}/63. "
                                    f"Gain {pnl_pct:+.1f}%. Stop at ${exit_plan.get('cat_stop_price', 0):.2f}."
                                )
                            elif es == "HOLD_EXTEND":
                                print(f"[PaperMonitor] {symbol}: CGT deferral — holding for discount")
                        except Exception as e:
                            pass  # exit engine non-blocking

                    conn.execute(text("""
                        UPDATE paper_trades
                        SET current_price = :cp,
                            peak_price = :np,
                            position_stage = COALESCE(:stage_update, position_stage),
                            last_alert_at = CASE WHEN :stage_update IS NOT NULL THEN :now ELSE last_alert_at END,
                            last_checked_at = :now,
                            updated_at = :now
                        WHERE id = :tid
                    """), {"cp": cp, "np": new_peak, "stage_update": stage_update, "now": now, "tid": trade_id})

                elif side == 'SHORT':
                    new_trough = min(peak or entry, cp)
                    jump_from_trough = (cp - new_trough) / new_trough if new_trough > 0 else 0
                    trailing_pct = float(trailing_stop_pct or 3.0) / 100.0

                    if take_profit_price and cp <= float(take_profit_price) and position_stage != 'trim_signal':
                        stage_update = 'trim_signal'
                        alert = _rich_alert(
                            "🎯 SHORT TAKE PROFIT TRIGGER",
                            f"Price {cp:.2f} ≤ short target {float(take_profit_price):.2f}. "
                            f"Consider covering 50% and tightening buy-stop."
                        )
                    elif stop_loss_price and cp >= float(stop_loss_price) and position_stage != 'exit_signal':
                        stage_update = 'exit_signal'
                        alert = _rich_alert(
                            "🛑 SHORT STOP LOSS TRIGGER",
                            f"Price {cp:.2f} ≥ stop {float(stop_loss_price):.2f}. "
                            f"Cover position to limit further loss."
                        )
                    elif jump_from_trough >= trailing_pct and position_stage != 'exit_signal':
                        stage_update = 'exit_signal'
                        alert = _rich_alert(
                            "⚠️ SHORT TRAILING STOP TRIGGER",
                            f"Rebounded {jump_from_trough*100:.1f}% from trough ${new_trough:.2f}. "
                            f"Momentum turning against short. Review for exit."
                        )
                    conn.execute(text("""
                        UPDATE paper_trades
                        SET current_price = :cp,
                            peak_price = :nt,
                            position_stage = COALESCE(:stage_update, position_stage),
                            last_alert_at = CASE WHEN :stage_update IS NOT NULL THEN :now ELSE last_alert_at END,
                            last_checked_at = :now,
                            updated_at = :now
                        WHERE id = :tid
                    """), {"cp": cp, "nt": new_trough, "stage_update": stage_update, "now": now, "tid": trade_id})

                # ── Earnings proximity alert for open positions (daily max) ──
                if (not alert and recipients and not in_cooldown
                        and days_to_e is not None and 0 <= days_to_e <= 7):
                    earnings_alert = (
                        f"<b>📅 EARNINGS IN {days_to_e} DAYS — {symbol}</b>\n\n"
                        f"📌 <b>{symbol}</b> | {side or 'LONG'} | Entry ${entry_f:.2f}\n"
                        f"  {pnl_emoji} P&amp;L: <b>{'%+.2f' % gross_pnl} ({pnl_pct:+.1f}%)</b> | Held: {days_held}d\n\n"
                        f"<b>Pre-earnings checklist:</b>\n"
                        f"  • Confirm stop-loss is set (current: "
                        + (f"${float(stop_loss_price):.2f}" if stop_loss_price else "<b>⚠️ NOT SET</b>")
                        + f")\n"
                        f"  • Consider reducing to half position to manage binary event risk\n"
                        f"  • Analyst consensus: {(val.get('analyst_recommendation') or 'N/A').upper()} | Target: {target_mean_str}\n"
                    )
                    _send_telegram_payload(
                        earnings_alert,
                        recipients,
                        user_id=user_id,
                        message_type="earnings_proximity",
                        market=market,
                        delivery_mode="auto",
                        source="position_monitor",
                    )
                    conn.execute(text(
                        "UPDATE paper_trades SET last_alert_at = :now, last_checked_at = :now WHERE id = :tid"
                    ), {"now": now, "tid": trade_id})
                    log_position_event(trade_id, user_id, "earnings_alert",
                                       f"Earnings proximity alert sent for {symbol} ({days_to_e}d away)",
                                       {"days_to_earnings": days_to_e})
                    continue

                if alert and recipients and not in_cooldown:
                    _send_telegram_payload(
                        alert,
                        recipients,
                        user_id=user_id,
                        message_type="position_trigger",
                        market=market,
                        delivery_mode="auto",
                        source="position_monitor",
                    )
                    log_position_event(trade_id, user_id, "trigger",
                                       alert.replace("<b>", "").replace("</b>", "").replace("<br/>", " | ")[:300], {
                                           "symbol": symbol,
                                           "market": market,
                                           "current_price": cp,
                                           "pnl_pct": round(pnl_pct, 2),
                                           "position_stage": stage_update,
                                       })

    except Exception as e:
        import traceback
        traceback.print_exc()
        pass


# ─────────────────────────────────────────────────────────────────────────────
# WEALTH BUILDER — multi-factor screener endpoint
# ─────────────────────────────────────────────────────────────────────────────

class WealthBuilderRequest(BaseModel):
    market: str = "AU"
    min_analyst_upside: float = 3.0       # % upside to analyst consensus target (aligned for 3-4% compound cycles)
    min_score: float = 0.45               # composite score threshold
    min_prob_5pct: float = 50.0           # probability ≥3% 2-3 month return (55%+ required for 2:1 R:R positive edge)
    max_symbols: int = 20                 # candidates to scan (capped at 100)
    send_telegram: bool = False
    scan_mode: str = "top"                # "top" = top 40, "broad" = full ASX_COMPANIES pool


def _score_wealth_candidate(symbol: str, market: str) -> Optional[dict]:
    """Score a single symbol as a wealth-builder candidate.  Returns None on failure."""
    index_ticker = "^AXJO" if market == "AU" else "^IXIC" if market == "US" else None
    try:
        sd = get_stock_data(symbol, market)
        if not sd or sd.get("current_price", 0) <= 0:
            return None
        cp = float(sd["current_price"])
        volume = sd.get("volume", 0)
        avg_vol = sd.get("avg_volume_5d", 0)
        vol_spike = sd.get("volume_spike", None)

        hist = get_historical_data(symbol, period="1y")
        if len(hist) < 120:
            return None

        valuation = get_valuation_metrics(symbol)
        
        # Flaw 11: Micro-Cap Shell Contamination - enforce $50M minimum Market Cap
        market_cap_val = valuation.get("market_cap") or (cp * avg_vol * 200 if avg_vol else None)
        if market_cap_val and market_cap_val < 50_000_000:
            return None

        # ── Volume/Liquidity quality ──────────────────────────────────────────
        liquidity_ok = True
        liquidity_penalty = 1.0
        liquidity_flags = []
        if avg_vol and avg_vol < 50000:
            liquidity_ok = False
            liquidity_penalty = 0.4
            liquidity_flags.append("LOW_VOL")
        elif avg_vol and avg_vol < 200000:
            liquidity_penalty = 0.7
            liquidity_flags.append("LOW_VOL")
        if vol_spike and vol_spike > 3:
            liquidity_flags.append("VOL_SPIKE")

        indicators = calculate_technical_indicators(hist)
        prediction = generate_statistical_prediction(
            hist, cp, sector=valuation.get("sector", ""), symbol=symbol
        )
        entry_timing = _entry_timing_assessment(valuation, indicators, {**prediction, "current_price": cp})

        # Basic signal score (reuse existing helper)
        signal = get_probability_and_score(symbol)

        # ── Bear market / regime penalty ──────────────────────────────────────
        try:
            regime_snap = compute_regime_snapshot()
            bear_market = regime_snap.get("bear_market", False)
            vix_level = regime_snap.get("vix_level", 20)
        except Exception:
            regime_snap = {}
            bear_market = False
            vix_level = 20

        # ── Layer 1 Bullish Confluence Engine ──────────────────────────────────
        confluence = bullish_confluence_score(
            symbol, market, indicators, prediction, sd, hist, valuation, regime_snap
        )

        # ── Relative strength vs sector ───────────────────────────────────────
        sector = valuation.get("sector", "")
        rel_strength_3m = None
        if sector and len(hist) >= 60:
            try:
                my_ret = round((cp / float(hist["Close"].iloc[-60])) - 1, 4)
                # Use lightweight own-return-based relative strength (avoid N additional yfinance calls)
                rel_strength_3m = round(my_ret * 100, 1)
            except Exception:
                pass

        # ── Earnings quality check ─────────────────────────────────────────────
        eps_diluted = valuation.get("trailing_eps")
        eps_growth = valuation.get("eps_growth_fwd_pct")
        revenue_growth = valuation.get("revenue_growth")
        earnings_quality_flags = []
        if eps_diluted is not None and eps_diluted < 0:
            earnings_quality_flags.append("NEG_EPS")
        if eps_growth is not None and eps_growth < -10:
            earnings_quality_flags.append("EPS_DECLINING")
        if revenue_growth is not None and revenue_growth < -0.05:
            earnings_quality_flags.append("REV_DECLINING")

        # ── Sector rotation context ────────────────────────────────────────────
        sector_perf = None
        if sector:
            try:
                xjo_hist = get_historical_data(index_ticker, period="1mo", market=None)
                if xjo_hist is not None and not xjo_hist.empty and len(xjo_hist) >= 5:
                    sector_perf = round((float(xjo_hist["Close"].iloc[-1]) / float(xjo_hist["Close"].iloc[0])) - 1, 4) * 100
            except Exception:
                pass

        result = {
            "symbol": symbol,
            "name": sd.get("name", symbol),
            "market": market,
            "current_price": round(cp, 2),
            "change_percent": round(sd.get("change_percent", 0), 2),
            "score": signal.get("score", 0),
            "prob_ge_5pct": signal.get("prob_ge_5pct", 0),
            "trend": prediction.get("trend", "neutral"),
            "predicted_change_pct": round(prediction.get("change_from_current", 0), 2),
            "analyst_target_mean": valuation.get("analyst_target_mean"),
            "analyst_upside_pct": valuation.get("analyst_upside_pct"),
            "analyst_recommendation": valuation.get("analyst_recommendation"),
            "num_analyst_opinions": valuation.get("num_analyst_opinions"),
            "next_earnings_date": valuation.get("next_earnings_date"),
            "days_to_earnings": valuation.get("days_to_earnings"),
            "short_pct_float": valuation.get("short_pct_float"),
            "pct_from_52w_high": valuation.get("pct_from_52w_high"),
            "pe": valuation.get("pe"),
            "forward_pe": valuation.get("forward_pe"),
            "eps_growth_fwd_pct": valuation.get("eps_growth_fwd_pct"),
            "market_cap": valuation.get("market_cap") or (cp * avg_vol * 200 if avg_vol else None),
            "52w_high": float(hist["High"].max()) if len(hist) > 0 and "High" in hist.columns else None,
            "52w_low": float(hist["Low"].min()) if len(hist) > 0 and "Low" in hist.columns else None,
            # New dimensions
            "avg_volume": avg_vol,
            "liquidity_ok": liquidity_ok,
            "liquidity_flags": liquidity_flags,
            "rel_strength_3m": rel_strength_3m,
            "earnings_quality_flags": earnings_quality_flags,
            "sector": sector,
            "sector_perf_1mo": sector_perf,
            "trailing_eps": eps_diluted,
            "revenue_growth": revenue_growth,
             "entry_timing": entry_timing,
            # Weighted-sum wealth rank with Layer 2 confluence integration.
            # Hard veto gates (zero the rank) on structural risks before scoring.
            # Weighted components discriminate between moderate and high conviction
            # instead of collapsing both into near-zero (multiplicative trap).
            #
            # Veto gates: any single one = wealth_rank 0 (excluded from picks)
            "wealth_rank": 0.0 if (
                bool(not entry_timing["entry_ok"])                                       # entry timing veto
                or bool(not liquidity_ok)                                                # liquidity veto
                or bool(entry_timing.get("earnings_risk") == "high")                     # binary event risk
                or bool((valuation.get("short_pct_float") or 0) > 15)                   # extreme short interest
                or bool((valuation.get("pe") or 0) > 80 or (valuation.get("pe") or 0) < 0)  # extreme PE
                or bool(confluence.get("confidence") == "low" and confluence.get("bullish_channels", 0) <= 1)  # Layer 2 rejection
            ) else round(
                # ── Base conviction (Layer 1 score, 40% weight) ─────────────────
                0.40 * (signal.get("score", 0) or 0)
                # ── Analyst consensus upside (10% weight) ────────────────────────
                + 0.10 * min(max(0, (valuation.get("analyst_upside_pct") or 0) / 5.0), 1.0) * 100
                # ── Confluence score (Layer 2, 15% weight) ───────────────────────
                + 0.15 * (confluence.get("score", 0) or 0)
                # ── Dividend yield bonus (5% weight) ─────────────────────────────
                + 0.05 * min(max(0, (valuation.get("dividend_yield") or 0) / 5.0), 1.0) * 100
                # ── Liquidity quality (10% weight) ───────────────────────────────
                + 0.10 * liquidity_penalty * 100
                # ── Earnings quality (8% weight) ─────────────────────────────────
                + 0.08 * max(0, (100 - 20 * len(earnings_quality_flags)))
                # ── Analyst coverage (5% weight) ─────────────────────────────────
                + 0.05 * (100 if (valuation.get("num_analyst_opinions") or 0) >= 2 else 50)
                # ── Drawdown position (7% weight) ────────────────────────────────
                + 0.07 * (100 if (valuation.get("pct_from_52w_high") or 0) > -40 else 50)
                # ── Deductions ───────────────────────────────────────────────────
                - (30 if (valuation.get("short_pct_float") or 0) > 8 else 0)         # moderate short interest
                - (20 if (valuation.get("pe") or 0) > 40 else 0)                      # moderate PE
                - (30 if vix_level > 30 else 10 if vix_level > 22 else 0)            # VIX regime
                - (20 if bear_market else 0),                                          # bear market penalty
                4
            ),
            "confluence": confluence,
            "valuation": valuation,
            "prediction": prediction,
        }
        return result
    except Exception:
        return None


_WEALTH_SCAN_SEMAPHORE = threading.Semaphore(6)  # max 6 concurrent yfinance calls
_MACRO_CACHE = None
_MACRO_CACHE_TIME = 0
_MACRO_CACHE_TTL = 300  # 5 min TTL for macro data

def _get_macro_data_cached():
    global _MACRO_CACHE, _MACRO_CACHE_TIME
    now = time.time()
    if _MACRO_CACHE is not None and (now - _MACRO_CACHE_TIME) < _MACRO_CACHE_TTL:
        return _MACRO_CACHE
    _MACRO_CACHE = macro_model.get_macro_data()
    _MACRO_CACHE_TIME = now
    return _MACRO_CACHE


def _safe_ratio(numerator: float, denominator: float, fallback: float = 0.0) -> float:
    if denominator == 0.0 or denominator is None:
        return fallback
    return numerator / denominator


def _score_wealth_candidate_safe(symbol: str, market: str, timeout: float = 25.0) -> Optional[dict]:
    """Score a candidate with a semaphore for rate limiting and per-stock timeout."""
    acquired = _WEALTH_SCAN_SEMAPHORE.acquire(timeout=timeout)
    if not acquired:
        return None
    try:
        future = ThreadPoolExecutor(max_workers=1).submit(_score_wealth_candidate, symbol, market)
        return future.result(timeout=timeout)
    except Exception:
        return None
    finally:
        _WEALTH_SCAN_SEMAPHORE.release()


import math

def sanitize_nan(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: sanitize_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_nan(item) for item in obj]
    return obj

@app.post("/api/signals/wealth-builder")
async def wealth_builder_signals(
    payload: WealthBuilderRequest,
    current_user: dict = Depends(get_current_user),
):
    """Multi-factor wealth-builder screener combining technical score,
    analyst consensus upside, and catalyst-aware entry timing."""
    t0 = time.time()
    market = (payload.market or "AU").upper()
    max_scan = max(5, min(int(payload.max_symbols or 20), 100))
    scan_mode = (payload.scan_mode or "top").lower()

    if scan_mode == "broad" and market == "AU":
        symbol_pool = list(ASX_COMPANIES.keys())[:max_scan]
    elif scan_mode == "broad" and market == "US":
        symbol_pool = TOP_SYMBOLS_BY_MARKET.get("US", [])[:max_scan]
    elif scan_mode == "broad" and market == "IN":
        symbol_pool = TOP_SYMBOLS_BY_MARKET.get("IN", [])[:max_scan]
    else:
        symbol_pool = TOP_SYMBOLS_BY_MARKET.get(market, TOP_SYMBOLS_BY_MARKET["AU"])[:max_scan]

    # Run scoring with rate-limited thread pool (max 6 concurrent yfinance calls)
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, _score_wealth_candidate_safe, sym, market)
        for sym in symbol_pool
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed_s = round(time.time() - t0, 1)

    candidates = []
    for r in raw_results:
        if r is None or isinstance(r, Exception):
            continue
        # Hard exclude: entry_timing "avoid" zone (e.g. earnings within 7 days)
        entry_zone = (r.get("entry_timing") or {}).get("entry_zone", "")
        if entry_zone == "avoid":
            continue
        # Hard exclude: wealth_rank = 0 (zeroed out by entry_ok=False penalty)
        if (r.get("wealth_rank") or 0) <= 0:
            continue
        # Apply filters
        if (r.get("score") or 0) < payload.min_score:
            continue
        if (r.get("prob_ge_5pct") or 0) < payload.min_prob_5pct:
            continue
        analyst_upside = r.get("analyst_upside_pct")
        if analyst_upside is not None and analyst_upside < payload.min_analyst_upside:
            continue
        candidates.append(r)

    # Sort by composite wealth_rank descending
    candidates.sort(key=lambda x: x.get("wealth_rank", 0), reverse=True)

    # Sector concentration cap: max 40% of candidates from any single sector
    sector_counts = {}
    diversified = []
    max_per_sector = max(2, int(len(candidates) * 0.4)) if candidates else len(candidates)
    for c in candidates:
        sec = c.get("sector") or "Unknown"
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        if sector_counts[sec] <= max_per_sector:
            diversified.append(c)
    candidates = diversified

    # ── Telegram dispatch ─────────────────────────────────────────────────────
    telegram_delivery = {"sent": False, "success_count": 0, "error": None}
    if payload.send_telegram and candidates:
        recipients = get_user_telegram_recipients(current_user["id"])
        top3 = candidates[:3]
        lines = [
            f"<b>🏦 WEALTH BUILDER SIGNALS — {market}</b>",
            f"<i>{len(candidates)} candidates found from {max_scan} screened</i>\n",
        ]
        for i, c in enumerate(top3, 1):
            zone_e = {"clear": "🟢", "caution": "🟡", "avoid": "🔴"}.get(
                (c.get("entry_timing") or {}).get("entry_zone", ""), "⚪"
            )
            upside = c.get("analyst_upside_pct")
            upside_str = f"{upside:+.1f}%" if upside is not None else "N/A"
            rec = (c.get("analyst_recommendation") or "N/A").upper()
            n_e = c.get("days_to_earnings")
            dte_str = f"{n_e}d" if n_e is not None else "N/A"
            lines.append(
                f"<b>#{i} {c['symbol']}</b> — {c['name']}\n"
                f"  Score: {c.get('score', 0):.2f} | P(≥3%): {c.get('prob_ge_5pct', 0):.1f}%\n"
                f"  Analyst: {rec} | Upside: {upside_str}\n"
                f"  Earnings: {c.get('next_earnings_date', 'N/A')} ({dte_str})\n"
                f"  Entry: {zone_e} {(c.get('entry_timing') or {}).get('entry_zone', 'N/A').upper()}\n"
            )
        lines.append(f"\n<i>Use /api/shares/SYMBOL/analyze for full deep-dive analysis.</i>")
        message = "\n".join(lines)
        telegram_delivery = _send_telegram_payload(
            message,
            recipients,
            user_id=current_user["id"],
            message_type="wealth_builder",
            market=market,
            delivery_mode="manual",
            source="wealth_builder",
        )

    # ── Persist candidates for backtesting ──────────────────────────────────────
    try:
        now_ts = datetime.utcnow()
        with engine.connect() as conn:
            for c in candidates:
                conn.execute(text("""
                    INSERT INTO wealth_builder_evaluations
                        (symbol, market, wealth_rank, score, prob_ge_5pct,
                         predicted_change_pct, price_at_screen,
                         entry_zone, screened_at)
                    VALUES (:symbol, :market, :wr, :sc, :p5, :pc, :pas, :ez, :at)
                    ON CONFLICT (symbol, screened_at) DO NOTHING
                """), {
                    "symbol": c["symbol"],
                    "market": market,
                    "wr": c.get("wealth_rank"),
                    "sc": c.get("score"),
                    "p5": c.get("prob_ge_5pct"),
                    "pc": c.get("predicted_change_pct"),
                    "pas": c.get("current_price"),
                    "ez": (c.get("entry_timing") or {}).get("entry_zone", ""),
                    "at": now_ts,
                })
            conn.commit()
    except Exception:
        pass

    return {
        "market": market,
        "screened": len(symbol_pool),
        "candidates_found": len(candidates),
        "filters": {
            "min_score": payload.min_score,
            "min_prob_5pct": payload.min_prob_5pct,
            "min_analyst_upside": payload.min_analyst_upside,
        },
        "candidates": candidates,
        "telegram": telegram_delivery,
        "generated_at": datetime.utcnow().isoformat(),
        "elapsed_s": elapsed_s,
    }


@app.get("/api/signals/wealth-builder/cached-broad")
async def wealth_builder_cached_broad(
    market: str = "AU",
    min_score: float = 35.0,
    min_prob_5pct: float = 50.0,
    min_analyst_upside: float = 3.0,
    current_user: dict = Depends(get_current_user),
):
    """Return the 5AM pre-computed broad scan from DB cache."""
    del current_user
    m = market.upper()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT picks, scanned_count, candidates_found, generated_at
            FROM wealth_scan_cache
            WHERE market = :market AND scan_mode = 'broad'
            ORDER BY generated_at DESC LIMIT 1
        """), {"market": m}).fetchone()

    if not row:
        return {"market": m, "candidates": [], "message": "No cached scan yet. Broad scan runs daily at 5AM.", "generated_at": None}

    candidates = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    # Apply optional filters on the cached data
    filtered = [c for c in candidates
                if (c.get("score") or 0) >= min_score
                and (c.get("prob_ge_5pct") or 0) >= min_prob_5pct
                and (c.get("analyst_upside_pct") or 0) >= min_analyst_upside]

    return sanitize_nan({
        "market": m,
        "screened": int(row[1]),
        "candidates_found": len(filtered),
        "candidates": filtered,
        "generated_at": str(row[3]) if row[3] else None,
        "scan_mode": "broad",
    })


@app.get("/api/signals/wealth-builder/backtest")
async def wealth_builder_backtest(
    market: str = "AU",
    days_ago: int = 14,
    min_rank: float = 0,
    current_user: dict = Depends(get_current_user),
):
    """Evaluate historical wealth builder predictions vs actual returns."""
    del current_user
    m = market.upper()
    cutoff = datetime.utcnow() - timedelta(days=days_ago)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT symbol, wealth_rank, score, prob_ge_5pct,
                   predicted_change_pct, price_at_screen,
                   actual_return_14d, actual_return_30d, actual_return_90d,
                   actual_peak_return_90d, actual_max_drawdown_90d,
                   entry_zone, screened_at, evaluated
            FROM wealth_builder_evaluations
            WHERE market = :market
              AND screened_at <= :cutoff
              AND wealth_rank >= :min_rank
            ORDER BY screened_at DESC, wealth_rank DESC
            LIMIT 100
        """), {"market": m, "cutoff": cutoff, "min_rank": min_rank}).fetchall()

    total = len(rows)
    if total == 0:
        return {"market": m, "count": 0, "evaluations": [], "message": f"No evaluations older than {days_ago} days yet."}

    evals = []
    hit_count = 0
    dir_correct = 0
    ranked_pairs = []
    for r in rows:
        pred = r[3] or 0  # predicted_change_pct
        actual_14d = r[6]
        actual_30d = r[7]
        actual_90d = r[8]
        peak = r[9]
        dd = r[10]
        rank = r[1] or 0

        if actual_14d is not None and actual_14d > 0:
            dir_correct += 1 if pred > 0 else 0
        elif actual_14d is not None and actual_14d < 0:
            dir_correct += 1 if pred < 0 else 0

        if actual_90d is not None and actual_90d >= 3:
            hit_count += 1

        ranked_pairs.append((rank, actual_90d or 0))

        evals.append({
            "symbol": r[0],
            "wealth_rank": rank,
            "score": r[2],
            "prob_ge_5pct": r[3],
            "predicted_change_pct": pred,
            "price_at_screen": r[5],
            "actual_return_14d": actual_14d,
            "actual_return_30d": actual_30d,
            "actual_return_90d": actual_90d,
            "actual_peak_return_90d": peak,
            "actual_max_drawdown_90d": dd,
            "entry_zone": r[11],
            "screened_at": str(r[12]) if r[12] else None,
            "evaluated": bool(r[13]),
        })

    # Rank correlation: does higher wealth_rank correlate with higher returns?
    if len(ranked_pairs) >= 5:
        rs = pd.Series([p[0] for p in ranked_pairs])
        rets = pd.Series([p[1] for p in ranked_pairs])
        spearman = float(rs.corr(rets, method="spearman")) if rs.std() > 0 and rets.std() > 0 else 0
    else:
        spearman = 0

    return {
        "market": m,
        "count": total,
        "hit_rate_5pct_90d": round(hit_count / total * 100, 1) if total > 0 else 0,
        "dir_accuracy_14d": round(dir_correct / total * 100, 1) if total > 0 else 0,
        "rank_90d_spearman": round(spearman, 2),
        "evaluations": evals,
    }


@app.post("/api/signals/wealth-builder/evaluate")
async def wealth_builder_evaluate_historical(
    current_user: dict = Depends(get_current_user),
):
    """Background job: evaluate past wealth builder candidates against actual returns.
    Evaluates progressively — at 14d fill 14d, at 30d fill 30d, etc.
    Only marks evaluated=TRUE once ALL horizons (14/30/63/90d) are populated."""
    del current_user
    cutoff = datetime.utcnow() - timedelta(days=14)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, symbol, market, price_at_screen, screened_at
            FROM wealth_builder_evaluations
            WHERE screened_at <= :cutoff
              AND (actual_return_14d IS NULL
                OR actual_return_30d IS NULL
                OR actual_return_63d IS NULL
                OR actual_return_90d IS NULL)
            LIMIT 50
        """), {"cutoff": cutoff}).fetchall()

    updated = 0
    for row in rows:
        eid, sym, mkt, price, at_str = row[0], row[1], row[2], row[4], row[5]
        try:
            at_date = at_str.replace(tzinfo=None) if hasattr(at_str, 'replace') else at_str
            hist = get_historical_data(sym, period="6mo")
            is_dead = hist.empty or len(hist) < 14 or (
                len(hist) == 1 and "Close" in hist.columns 
                and abs(float(hist["Close"].iloc[0])) < 0.01
            )
            if is_dead:
                # Survivorship bias mitigation: If we can't get history for a stock we screened in the past,
                # it may be delisted or bankrupt. We assign a -100% return if it's > 30 days old.
                if isinstance(at_date, datetime) and (datetime.utcnow() - at_date).days > 30:
                    conn.execute(text("""
                        UPDATE wealth_builder_evaluations
                        SET actual_return_14d = -100, actual_return_30d = -100,
                            actual_return_63d = -100,
                            actual_return_90d = -100, actual_peak_return_90d = 0,
                            actual_max_drawdown_90d = -100, evaluated = TRUE
                        WHERE id = :eid
                    """), {"eid": eid})
                    updated += 1
                continue
            prices_after = hist[hist.index > pd.Timestamp(at_date)]
            if prices_after.empty:
                continue
            current_p = price if price else 1.0
            days_passed = (datetime.utcnow() - at_date.replace(tzinfo=None)).days if isinstance(at_date, datetime) else 0

            # Peak-within-window returns (a stock that hits +5% on day 3 IS a hit at 14d)
            peak_14d = float(prices_after["Close"].iloc[:min(14, len(prices_after))].max()) if len(prices_after) >= 3 else None
            peak_30d = float(prices_after["Close"].iloc[:min(30, len(prices_after))].max()) if len(prices_after) >= 3 else None
            peak_63d = float(prices_after["Close"].iloc[:min(63, len(prices_after))].max()) if len(prices_after) >= 3 else None
            peak_90d = float(prices_after["Close"].max()) if len(prices_after) >= 3 else None

            actual_14d = round((peak_14d / current_p - 1) * 100, 2) if peak_14d and days_passed >= 14 else None
            actual_30d = round((peak_30d / current_p - 1) * 100, 2) if peak_30d and days_passed >= 30 else None
            actual_63d = round((peak_63d / current_p - 1) * 100, 2) if peak_63d and days_passed >= 63 else None
            actual_90d = round((peak_90d / current_p - 1) * 100, 2) if peak_90d and days_passed >= 90 else None
            peak_ret = round((peak_90d / current_p - 1) * 100, 2) if peak_90d and days_passed >= 14 else None
            max_dd = round((float(prices_after["Close"].min()) / current_p - 1) * 100, 2) if days_passed >= 14 else None

            # Incremental: only fill NULL columns
            sets = []
            params = {"eid": eid}
            if actual_14d is not None:
                sets.append("actual_return_14d = :r14"); params["r14"] = actual_14d
            if actual_30d is not None:
                sets.append("actual_return_30d = :r30"); params["r30"] = actual_30d
            if actual_63d is not None:
                sets.append("actual_return_63d = :r63"); params["r63"] = actual_63d
            if actual_90d is not None:
                sets.append("actual_return_90d = :r90"); params["r90"] = actual_90d
            if peak_ret is not None:
                sets.append("actual_peak_return_90d = :pk"); params["pk"] = peak_ret
            if max_dd is not None:
                sets.append("actual_max_drawdown_90d = :dd"); params["dd"] = max_dd
            all_filled = actual_14d is not None and actual_30d is not None and \
                          actual_63d is not None and actual_90d is not None
            sets.append("evaluated = :ev"); params["ev"] = all_filled

            if sets:
                conn.execute(text(f"UPDATE wealth_builder_evaluations SET {', '.join(sets)} WHERE id = :eid"), params)
            updated += 1
        except Exception:
            pass
    conn.commit()

    return {"evaluated": updated}


def _scheduled_self_learning_loop():
    """Scheduled job: Evaluate paper trades to find win rates and tweak learning metrics."""
    try:
        print("[SelfLearning] Starting auto-learning evaluation cycle...")
        with engine.connect() as conn:
            # Get all closed/exited trades
            trades = conn.execute(text("""
                SELECT initial_price, current_price, position_stage
                FROM paper_trades
                WHERE status = 'closed' OR position_stage IN ('trim_signal', 'exit_signal')
            """)).fetchall()

            min_trades = 3 if PAPER_BOOTSTRAP_MODE else 8
            if len(trades) < min_trades:
                print(f"[SelfLearning] Need ≥{min_trades} closed trades for PID (have {len(trades)}).")
                return

            win_count = 0
            loss_count = 0
            win_pcts = []
            loss_pcts = []
            
            for t in trades:
                initial = float(t[0] or 0)
                current = float(t[1] or 0)
                stage = t[2]
                if initial > 0:
                    ret = (current - initial) / initial * 100
                    if stage == 'trim_signal' or ret > 0:
                        win_count += 1
                        win_pcts.append(ret)
                    else:
                        loss_count += 1
                        loss_pcts.append(ret)

            total = win_count + loss_count
            win_rate = (win_count / total * 100) if total > 0 else 0
            avg_win = sum(win_pcts) / len(win_pcts) if win_pcts else 0
            avg_loss = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0
            
            target_win_rate = 55.0
            error = target_win_rate - win_rate

            # Constants: max penalty movement from baseline per cycle
            PENALTY_BASELINE = {"vix_extreme": 0.75, "pe_extreme": 0.70, "short_extreme": 0.50}
            MAX_DEVIATION = 0.30  # max ±30% from baseline
            STEP_DOWN = 0.03      # tighten by 0.03 per cycle
            STEP_UP = 0.02        # loosen by 0.02 per cycle
            MIN_FLOOR = {k: max(0.3, v - MAX_DEVIATION) for k, v in PENALTY_BASELINE.items()}
            MAX_CEIL = {k: min(0.95, v + MAX_DEVIATION) for k, v in PENALTY_BASELINE.items()}

            rec = "Hold steady."
            if error > 5:
                for key in ["vix_extreme", "pe_extreme", "short_extreme"]:
                    DYNAMIC_PENALTIES[key] = max(MIN_FLOOR[key], DYNAMIC_PENALTIES[key] - STEP_DOWN)
                rec = f"PID tightened penalties (win_rate={win_rate:.0f}% vs target {target_win_rate:.0f}%)."
            elif error < -5:
                for key in ["vix_extreme", "pe_extreme", "short_extreme"]:
                    DYNAMIC_PENALTIES[key] = min(MAX_CEIL[key], DYNAMIC_PENALTIES[key] + STEP_UP)
                rec = f"PID loosened penalties (win_rate={win_rate:.0f}% > target {target_win_rate:.0f}%)."
            else:
                rec = f"Win rate {win_rate:.0f}% within ±5pp of target — strategy is calibrated."

            conn.execute(text("""
                INSERT INTO ai_self_learning_metrics (total_trades, win_count, loss_count, win_rate_pct, avg_win_pct, avg_loss_pct, recommended_action)
                VALUES (:t, :w, :l, :wr, :aw, :al, :rec)
            """), {"t": total, "w": win_count, "l": loss_count, "wr": round(win_rate, 2), "aw": round(avg_win, 2), "al": round(avg_loss, 2), "rec": rec})
            conn.commit()
            print(f"[SelfLearning] Cycle complete. Win rate: {win_rate:.1f}%")
    except Exception as e:
        print(f"[SelfLearning] Error during auto-learning: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# DAILY AI PIPELINE — Layer 1 Screen → Layer 2 Deep-Dive → Telegram Broadcast
# ═══════════════════════════════════════════════════════════════════════════════

TOPTIER_MAX_AI_DEEP_DIVES = int(os.getenv("TOPTIER_MAX_AI_DEEP_DIVES", "0"))
TOPTIER_PER_TIER = int(os.getenv("TOPTIER_PER_TIER", "12"))

def _cap_tier_from_market_cap(market_cap: Optional[float]) -> str:
    """Classify a stock into large/mid/small cap tier based on market cap."""
    if not market_cap:
        return "small_cap"
    if market_cap > 10_000_000_000:
        return "large_cap"
    if market_cap > 2_000_000_000:
        return "mid_cap"
    return "small_cap"

def _format_ai_report_for_telegram(analysis: dict, candidate: dict) -> str:
    """Build a compact Telegram HTML card from Layer 2 AI analysis."""
    sym = analysis.get("symbol", "???")
    name = candidate.get("name", sym)
    decision = (analysis.get("decision") or "REJECT").upper()
    confidence = analysis.get("confidence", 0)
    allocation = analysis.get("allocation_pct", 0)
    stop = analysis.get("stop_loss_pct", 0)
    key_risk = analysis.get("key_risk", "N/A")
    score = candidate.get("score", 0) or 0
    prob = candidate.get("prob_ge_5pct", 0) or 0
    trend = (candidate.get("trend") or "neutral").upper()
    price = candidate.get("current_price", 0) or 0
    confluence = (candidate.get("confluence") or {}).get("confidence", "?")
    mconf = candidate.get("_model_confidence", 50)
    tier = candidate.get("_tier_label", "")

    d_emoji = "✅" if decision == "APPROVE" else "⛔"
    conf_bar = "🟢" if confidence >= 70 else "🟡" if confidence >= 40 else "🔴"
    t_emoji = "📈" if trend == "BULLISH" else "📉" if trend == "BEARISH" else "➡️"
    c_emoji = "🌟" if confluence == "high" else "⭐" if confluence == "medium" else "·"

    reasoning = analysis.get("reasoning", "")[:180]
    scenario_risks = analysis.get("scenario_risks", [])[:2]
    risks_block = "\n".join(f"  ⚠️ {r}" for r in scenario_risks) if scenario_risks else ""

    return (
        f"{d_emoji} <b>{sym}</b> {c_emoji} [{tier}] — {name}\n"
        f"  {t_emoji} Trend: {trend} | Score: {score:.2f} | {tier}\n"
        f"  💰 ${price:.2f} | Conf: {confidence}% {conf_bar} | Alloc: {allocation:.1f}% | Stop: -{stop:.1f}%\n"
        f"{'  ' + reasoning + chr(10) if reasoning else ''}"
        f"{risks_block}"
    )


# V2 Daily Scan — single unified AI pipeline
def _scheduled_v2_daily_scan():
    """V2 Daily Scan (replaces broad_scan and daily_ai_pipeline).
    
    1. Fetches top 500 liquid symbols (core + broad universe).
    2. Scores them using the V2 ensemble model.
    3. Runs the 6-persona AI deep dive on the top candidates.
    4. Auto-creates paper trades and broadcasts to Telegram.
    """
    if not run_agentic_analysis:
        print("[V2DailyScan] Agentic brain not available — skipping.")
        return

    print("[V2DailyScan] Starting V2 daily scan pipeline...")
    market = "AU"
    today_key = datetime.utcnow().date().isoformat()

    try:
        from config.universe import get_universe_symbols
        # Fetch 500 liquid symbols (core + broad)
        core_symbols = get_universe_symbols("core")
        broad_symbols = get_universe_symbols("broad")
        symbol_pool = list(set(core_symbols + broad_symbols))
        print(f"[V2DailyScan] Found {len(symbol_pool)} liquid symbols in universe.")
    except Exception as e:
        print(f"[V2DailyScan] Failed to load universe: {e}")
        return

    if not symbol_pool:
        return

    # Run V2 model scoring concurrently (similar to wealth_builder_signals)
    import concurrent.futures
    print("[V2DailyScan] Scoring candidates using V2 models...")
    candidates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_score_wealth_candidate_safe, sym, market): sym for sym in symbol_pool}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                candidates.append(res)
    
    # Filter for top tier candidates
    valid_candidates = [c for c in candidates if (c.get("prob_ge_5pct") or 0) >= 55.0]
    pool = sorted(valid_candidates, key=lambda x: x.get("wealth_rank", 0), reverse=True)
    
    MAX_AI = int(os.getenv("TOPTIER_MAX_AI_DEEP_DIVES", "0"))
    if MAX_AI > 0:
        selected = pool[:MAX_AI]
    else:
        # Default cap if unlimited is risky: only deep dive top 5
        selected = pool[:5]

    print(f"[V2DailyScan] Selected {len(selected)} top-tier candidates for AI deep-dive.")

    ai_results = []
    for i, cand in enumerate(selected):
        sym = cand["symbol"]
        print(f"[V2DailyScan] Deep-dive {i+1}/{len(selected)}: {sym} ...")
        try:
            val = get_valuation_metrics(sym)
            hist = get_historical_data(sym, period="1y")
            if len(hist) < 60:
                continue
            tech = calculate_technical_indicators(hist)
            tech_compact = {k: v for k, v in tech.items() if k in [
                "rsi", "rsi_slope", "stoch_rsi", "macd", "macd_signal", "sma_200",
                "ema_9", "ema_20", "ema_50", "volatility", "momentum_20", "adx",
                "dmi_bullish", "atr_pct", "vwap_20", "above_vwap", "up_down_vol_ratio",
                "block_volume_detected", "bb_pct_b", "cmf", "ttm_squeeze_on"
            ]}
            confluence = cand.get("confluence", {})
            val["model_tier"] = cand.get("_target_tier", "none")
            val["model_score"] = cand.get("_model_score", 0)
            val["model_tier_label"] = cand.get("_tier_label", "")

            result = run_agentic_analysis(sym, market, tech_compact, val, confluence)
            result["candidate"] = cand
            ai_results.append(result)

            if i < len(selected) - 1:
                time.sleep(0.5)
        except Exception as e:
            print(f"[V2DailyScan] Deep-dive failed for {sym}: {e}")
            continue

    print(f"[V2DailyScan] Completed {len(ai_results)} deep-dives. Approved: {sum(1 for r in ai_results if r.get('decision')=='APPROVE')}")
    if not ai_results:
        print("[V2DailyScan] No AI results — skipping broadcast.")
        return

    # Build Telegram broadcast per approved stock
    approved = [r for r in ai_results if r.get("decision") == "APPROVE"]
    rejected = [r for r in ai_results if r.get("decision") == "REJECT"]

    lines = [
        f"<b>🤖 V2 AI DAILY SCAN — {today_key}</b>",
        f"",
        f"📊 Layer 1 scored {len(candidates)} symbols → Layer 2 AI analyzed top {len(selected)}",
        f"",
    ]
    if approved:
        lines.append(f"<b>✅ AI-APPROVED ({len(approved)})</b>")
        for a in approved:
            cand = a.get("candidate", {})
            lines.append("")
            lines.append(_format_ai_report_for_telegram(a, cand))
    if rejected:
        lines.append(f"\n<b>⛔ AI-REJECTED ({len(rejected)})</b>")
        for a in rejected[:4]:
            cand = a.get("candidate", {})
            lines.append("")
            lines.append(_format_ai_report_for_telegram(a, cand))
        if len(rejected) > 4:
            lines.append(f"  ... and {len(rejected) - 4} more rejected by AI")
    
    lines.append(f"\n<i>🖥️ Powered by V2 Ensemble Model & Layer 2 Agentic AI</i>")
    message_html = "\n".join(lines)

    try:
        with db_conn() as conn:
            users = conn.execute(text("SELECT id FROM users")).fetchall()
        for user in users:
            uid = user[0]
            recipients = get_user_telegram_recipients(uid)
            if not recipients:
                continue
            digest_key = f"v2_scan_{today_key}"
            if not has_digest_been_sent(uid, market, digest_key):
                _send_telegram_payload(
                    message_html, recipients, user_id=uid,
                    message_type="daily_digest", market=market,
                    digest_key=digest_key, source="v2_daily_scan",
                    delivery_mode="auto",
                )
    except Exception as e:
        print(f"[V2DailyScan] Broadcast failed: {e}")

    # Process buys — per-user portfolio sizing
    if approved:
        num_approved = len(approved)
        print(f"[V2DailyScan] {num_approved} approved. Processing per-user paper trades...")

        try:
            with db_conn() as conn:
                post_users = conn.execute(text("SELECT id FROM users")).fetchall()
        except Exception:
            post_users = []
        for user in post_users:
            uid = user[0]
            recipients = get_user_telegram_recipients(uid)
            if not recipients:
                continue
            for a in approved:
                # Per-user portfolio state for correct position sizing
                pf = _compute_portfolio_state(uid)
                available_capital = pf["available_cash"]
                sector_exp = _get_sector_exposure(uid)
                if available_capital <= 0:
                    print(f"[DailyAI] User {uid[:8]} has no available capital — skipping buys")
                    continue

                cand = a.get("candidate", {})
                sym = a.get("symbol", "???")
                tier = cand.get("_tier_label", "")
                is_10pct = "10%" in tier
                tier_pct = "+10%" if is_10pct else "+8%"
                tier_emoji = "🚀" if is_10pct else "📈"
                digest_key = f"ai_buy_{sym}_{today_key}"
                if has_digest_been_sent(uid, market, digest_key):
                    continue
                
                already_bought = False
                try:
                    with db_conn() as check_conn:
                        existing = check_conn.execute(
                            text("SELECT 1 FROM paper_trades WHERE symbol=:sym AND user_id=:uid AND status='open' LIMIT 1"),
                            {"sym": sym, "uid": uid}
                        ).fetchone()
                        if existing:
                            already_bought = True
                except Exception:
                    pass
                if already_bought:
                    continue
                
                price = float(cand.get("current_price", 1) or 1)
                stop_pct = float(a.get("stop_loss_pct", 10) or 10)
                adv = int(cand.get("avg_volume", 0) or 0)
                sector = str((cand.get("valuation") or {}).get("sector", cand.get("sector", "Unknown")))

                size = _calculate_position_size(
                    price=price, stop_loss_pct=-abs(stop_pct),
                    account_balance=available_capital, num_picks=num_approved,
                    avg_volume=int(adv), current_sector_exposure=sector_exp,
                    candidate_sector=sector,
                )
                qty = size["qty"]
                cost = size["cost"]
                risk = size["risk_amount"]
                stop_p = size["stop_price"]

                warn_note = ""
                if size["warnings"]:
                    warn_note = "\n⚠️ " + ", ".join(size["warnings"])

                # ── Devil's Advocate ──────────────────────────────────
                if _DEVILS_ADVOCATE_AVAILABLE and run_devils_advocate:
                    try:
                        thesis = a.get("reasoning", a.get("decision_reasoning", ""))[:500]
                        da_result = run_devils_advocate(sym, thesis, llm_call_fn=_llm_chat_safe)
                        if da_result.get("size_reduction_pct", 0) > 0:
                            reduction = da_result["size_reduction_pct"]
                            qty = int(qty * (1 - reduction))
                            cost = qty * price
                            warn_note += f"\n🛡️ Devil's advocate: {da_result.get('key_risk','')}"
                            warn_note += f"\n⚠️ Size reduced {reduction*100:.0f}%"
                    except Exception:
                        pass

                auto_trade_id = None
                if PAPER_BOOTSTRAP_MODE and PAPER_BOOTSTRAP_AUTO_EXECUTE and qty > 0:
                    gate = _wfo_position_gate()
                    if gate["allowed"]:
                        auto_trade_id = _auto_create_paper_trade(
                            uid, sym, market, qty, price,
                            source_reason="v2_daily_scan",
                            notes="V2 auto-executed bootstrap paper trade"
                        )
                        if auto_trade_id:
                            warn_note += "\n🤖 AUTO-EXECUTED (bootstrap mode)"
                    else:
                        warn_note += f"\n⛔ Bootstrap gate: {gate['reason']}"

                buy_msg = (
                    f"<b>✅ V2 AI-APPROVED — {tier}</b>\n"
                    f"{sym} — {cand.get('name', sym)}\n\n"
                    f"{tier_emoji} <b>Target: {tier_pct} (2:1 R:R)</b>\n"
                    f"💰 Price: ${price:.2f} | Stop: ${stop_p:.2f} (-{abs(stop_pct):.1f}%)\n"
                    f"📊 {qty} shares = ${cost:.0f} ({size['pct_of_account']}% of portfolio)\n"
                    f"🎯 Risk: ${risk:.0f} ({PORTFOLIO_RISK_PER_TRADE_PCT}% of capital per trade)\n"
                    f"💼 Portfolio: ${available_capital:.0f} available | {num_approved} picks today{warn_note}\n\n"
                    f"<i>AI: {a.get('reasoning','')[:120]}...</i>"
                )
                kb = {"inline_keyboard": [[
                    {"text": f"🚀 Buy {qty} shares (~${cost:.0f})",
                     "callback_data": f"buy_{sym}_{qty}"}
                ]]}
                _send_telegram_payload(
                    buy_msg, recipients, user_id=uid, message_type="daily_digest",
                    market=market, digest_key=digest_key, source="v2_ai_approved_buy",
                    reply_markup=kb
                )
                time.sleep(0.3)


# ═══════════════════════════════════════════════════════════════════════════════

# WFO CAPITAL DEPLOYMENT GATES — what each state mechanically controls
# ═══════════════════════════════════════════════════════════════════════════════

_WFO_CAPITAL_RULES = {
    "INSUFFICIENT_DATA": {
        "max_positions": 0,
        "max_single_position_pct": 0,
        "max_sector_pct": 0,
        "allow_new_positions": False,
        "description": "Paper only. No live capital deployed. Evaluate OOS Sharpe before sizing.",
    },
    "RED": {
        "max_positions": 0,
        "max_single_position_pct": 0,
        "max_sector_pct": 0,
        "allow_new_positions": False,
        "description": "No edge. Halve existing positions. No new entries. Manual review required.",
    },
    "RED_MANUAL_REVIEW": {
        "max_positions": 0,
        "max_single_position_pct": 0,
        "max_sector_pct": 0,
        "allow_new_positions": False,
        "description": "Manual review in progress. Hold existing positions, no new entries.",
    },
    "AMBER": {
        "max_positions": 6,
        "max_single_position_pct": 8,
        "max_sector_pct": 30,
        "allow_new_positions": True,
        "description": "Edge unproven. Max 6 positions, single position ≤8%, sector ≤30%.",
    },
    "GREEN": {
        "max_positions": 12,
        "max_single_position_pct": 12,
        "max_sector_pct": 40,
        "allow_new_positions": True,
        "description": "Edge confirmed (95% CI lower bound ≥ 0). Max 12 positions, single position ≤12%, sector ≤40%.",
    },
}

_WFO_STATE_KEY = "wfo_capital_state"

def get_current_wfo_state() -> dict:
    """Return the most restrictive WFO capital state across all 3 horizons.

    Priority: RED > RED_MANUAL_REVIEW > INSUFFICIENT_DATA > AMBER > GREEN.
    Returns the state name AND the mechanical limits it enforces.
    """
    _ensure_wfo_table()
    states = []

    for horizon_days in _WFO_HORIZON_DAYS:
        try:
            with db_conn() as conn:
                row = conn.execute(text("""
                    SELECT notes FROM wfo_metrics
                    WHERE horizon_window_days = :hd
                    ORDER BY run_at DESC LIMIT 1
                """), {"hd": horizon_days}).fetchone()
            if row and row[0]:
                notes = row[0]
                if "RED_MANUAL_REVIEW" in notes:
                    states.append("RED_MANUAL_REVIEW")
                elif "RED" in notes and "MANUAL" not in notes:
                    states.append("RED")
                elif "AMBER" in notes:
                    states.append("AMBER")
                elif "GREEN" in notes:
                    states.append("GREEN")
                elif "INSUFFICIENT_DATA" in notes:
                    states.append("INSUFFICIENT_DATA")
        except Exception:
            pass

    # Priority order: worst state wins
    for candidate in ["RED", "RED_MANUAL_REVIEW", "INSUFFICIENT_DATA", "AMBER", "GREEN"]:
        if candidate in states:
            return {"state": candidate, "rules": _WFO_CAPITAL_RULES[candidate]}

    return {"state": "INSUFFICIENT_DATA", "rules": _WFO_CAPITAL_RULES["INSUFFICIENT_DATA"]}


def _wfo_position_gate(user_id: str = None) -> dict:
    """Enforce WFO capital deployment limits before opening a new position.

    When PAPER_BOOTSTRAP_MODE is active, paper trades bypass the WFO gate so the
    system can accumulate evaluation data and self-learn without manual intervention.
    Real capital deployment is never allowed during bootstrapping.

    Returns:
        {"allowed": bool, "max_positions": int, "max_pct": int, "reason": str}
    """
    wfo = get_current_wfo_state()
    state = wfo["state"]
    rules = wfo["rules"]

    if not rules["allow_new_positions"]:
        # ── Paper bootstrapping bypass: allow paper trades to accumulate data ──
        if PAPER_BOOTSTRAP_MODE:
            bootstrap_count = 0
            try:
                with db_conn() as conn:
                    count_row = conn.execute(text(
                        "SELECT COUNT(*) FROM paper_trades WHERE status = 'open' AND (source_reason LIKE 'wealth_builder%' OR source_reason = 'telegram_buy')"
                    )).fetchone()
                    bootstrap_count = count_row[0] if count_row else 0
            except Exception:
                pass
            if bootstrap_count < PAPER_BOOTSTRAP_MAX_POSITIONS:
                paper_cap = max(1, PAPER_BOOTSTRAP_MAX_POSITIONS - bootstrap_count)
                return {
                    "allowed": True,
                    "max_positions": PAPER_BOOTSTRAP_MAX_POSITIONS,
                    "max_single_pct": 8,
                    "max_sector_pct": 40,
                    "reason": f"WFO state {state}: bootstrapping ({bootstrap_count}/{PAPER_BOOTSTRAP_MAX_POSITIONS} paper positions). New entries allowed.",
                }
            else:
                return {
                    "allowed": False,
                    "max_positions": 0,
                    "max_single_pct": 0,
                    "max_sector_pct": 0,
                    "reason": f"WFO state {state}: bootstrapping cap reached ({bootstrap_count}/{PAPER_BOOTSTRAP_MAX_POSITIONS}). {rules['description']}",
                }
        return {
            "allowed": False,
            "max_positions": 0,
            "max_single_pct": 0,
            "max_sector_pct": 0,
            "reason": f"WFO state {state}: {rules['description']}",
        }

    # Count current open model-driven positions
    open_count = 0
    try:
        with db_conn() as conn:
            count_row = conn.execute(text(
                "SELECT COUNT(*) FROM paper_trades WHERE status = 'open' AND (source_reason LIKE 'wealth_builder%' OR source_reason = 'telegram_buy')"
            )).fetchone()
            open_count = count_row[0] if count_row else 0
    except Exception:
        pass

    if open_count >= rules["max_positions"]:
        return {
            "allowed": False,
            "max_positions": rules["max_positions"],
            "max_single_pct": rules["max_single_position_pct"],
            "max_sector_pct": rules["max_sector_pct"],
            "reason": f"WFO state {state}: {open_count}/{rules['max_positions']} positions open. Max reached.",
        }

    return {
        "allowed": True,
        "max_positions": rules["max_positions"],
        "max_single_pct": rules["max_single_position_pct"],
        "max_sector_pct": rules["max_sector_pct"],
        "reason": f"WFO state {state}: {open_count}/{rules['max_positions']} positions. Entry allowed.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD OOS VALIDATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

_WFO_TABLE_SQL = """
    DROP TABLE IF EXISTS wfo_metrics CASCADE;
    CREATE TABLE IF NOT EXISTS wfo_metrics (
        run_id TEXT PRIMARY KEY,
        run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        horizon_window_days INTEGER NOT NULL,
        total_signals INTEGER,
        hit_count INTEGER,
        hit_rate_pct NUMERIC(6,2),
        avg_return_pct NUMERIC(8,4),
        avg_predicted_return_pct NUMERIC(8,4),
        return_stdev_pct NUMERIC(8,4),
        oos_sharpe NUMERIC(8,4),
        oos_sharpe_ci_lower NUMERIC(8,4),
        oos_sharpe_ci_upper NUMERIC(8,4),
        direction_accuracy_pct NUMERIC(6,2),
        benchmark_return_pct NUMERIC(8,4),
        excess_return_pct NUMERIC(8,4),
        signal_count_large INTEGER DEFAULT 0,
        signal_count_mid INTEGER DEFAULT 0,
        signal_count_small INTEGER DEFAULT 0,
        hit_rate_large NUMERIC(6,2),
        hit_rate_mid NUMERIC(6,2),
        hit_rate_small NUMERIC(6,2),
        corporate_action_drops INTEGER DEFAULT 0,
        top_10_picks JSONB,
        notes TEXT
    )
"""

def _ensure_wfo_table():
    try:
        with db_conn() as conn:
            conn.execute(text(_WFO_TABLE_SQL))
            conn.commit()
    except Exception:
        pass


_WFO_LAST_HORIZON = 0
_WFO_HORIZON_DAYS = [30, 63, 90]  # rolling check at 30d, 63d, 90d post-signal

def _scheduled_walk_forward_oos():
    """Walk-Forward Out-of-Sample Validation (runs daily after market close).

    For every wealth_scan_cache entry older than horizon_window_days, fetches
    the actual post-signal price change from EODHD/yfinance and computes:

      - Directional accuracy: did the signal point the right way?
      - Hit rate: P(actual_return >= target) for each horizon
      - OOS Sharpe: (avg_return - r_f) / stdev(return) × sqrt(252/horizon)
      - Benchmark excess: signal return minus ASX200 return over same window
      - Cap-tier breakdown: large/mid/small hit rates separately
      - 95% confidence interval on OOS Sharpe via bootstrap

    All metrics are persisted to wfo_metrics table so a mini-PC that is
    powered off overnight doesn't lose the signal history.  Previous runs are
    always recoverable — just restart and the scheduler picks up where it left
    off.

    Minimum data requirement: ≥20 evaluated signals per horizon window
    (Sharpe on <20 observations has confidence intervals too wide to act on).

    RED/AMBER/GREEN pre-commit rules (configured, NOT rationalised after the fact):
      GREEN (Sharpe ≥ 0.25, lower bound of 95% CI ≥ 0): model has edge — continue
      AMBER (Sharpe ≥ 0, but lower bound of 95% CI < 0): edge unproven — widen stops
      RED (Sharpe < 0): model has no edge — reduce allocation to 50%, escalate to manual review
    """
    global _WFO_LAST_HORIZON
    _ensure_wfo_table()

    today = datetime.utcnow().date()
    r_f_daily = 0.04 / 252  # 4% annual risk-free rate

    for horizon_days in _WFO_HORIZON_DAYS:
        cutoff_date = today - timedelta(days=horizon_days)
        horizon_key = f"wfo_{today.isoformat()}_h{horizon_days}"

        # Skip if already computed today
        try:
            with db_conn() as conn:
                exists = conn.execute(text(
                    "SELECT 1 FROM wfo_metrics WHERE run_id = :rid"
                ), {"rid": horizon_key}).fetchone()
            if exists:
                continue
        except Exception:
            pass

        # Load all signals screened ≥ horizon_days ago
        try:
            with db_conn() as conn:
                rows = conn.execute(text("""
                    SELECT picks, generated_at
                    FROM wealth_scan_history
                    WHERE scan_mode = 'broad'
                      AND generated_at <= :cutoff
                    ORDER BY generated_at DESC
                    LIMIT 50
                """), {"cutoff": cutoff_date}).fetchall()
        except Exception as e:
            print(f"[WFO] DB read failed for h{horizon_days}: {e}")
            continue

        if not rows:
            # Write INSUFFICIENT_DATA so we know the horizon was checked
            try:
                with db_conn() as conn:
                    conn.execute(text("""
                        INSERT INTO wfo_metrics (run_id, horizon_window_days, total_signals, notes)
                        VALUES (:rid, :hd, 0, 'INSUFFICIENT_DATA: no scan cache entries ≥ horizon_days old')
                        ON CONFLICT (run_id) DO UPDATE SET
                            run_at = NOW(), total_signals = EXCLUDED.total_signals,
                            notes = EXCLUDED.notes
                    """), {"rid": horizon_key, "hd": horizon_days})
                    conn.commit()
            except Exception:
                pass
            print(f"[WFO] h{horizon_days}d: no scan cache entries ≥{horizon_days}d old — INSUFFICIENT_DATA.")
            continue

        # Flatten all candidates across scan runs
        all_signals = []
        top_10pct_symbols = set()
        top_8pct_symbols = set()
        for picks_json, gen_at in rows:
            candidates = json.loads(picks_json) if isinstance(picks_json, str) else (picks_json or [])
            for c in candidates:
                sym = c.get("symbol", "")
                score = c.get("score") or 0
                prob = c.get("prob_ge_5pct") or 0
                price = c.get("current_price") or 0
                pred_chg = c.get("predicted_change_pct") or c.get("expected_return_3m_pct") or 0
                trend = c.get("trend", "neutral")
                mc = (c.get("valuation") or {}).get("market_cap")
                sector = (c.get("valuation") or {}).get("sector", "Unknown")
                tier = c.get("_target_tier", "8pct")
                if tier == "10pct":
                    top_10pct_symbols.add(sym)
                elif tier == "8pct":
                    top_8pct_symbols.add(sym)
                all_signals.append({
                    "symbol": sym, "score": score, "prob": prob,
                    "screened_at": str(gen_at)[:10] if gen_at else str(cutoff_date),
                    "entry_price": price, "predicted_change_pct": pred_chg,
                    "trend": trend, "market_cap": mc, "sector": sector, "tier": tier,
                })

        if not all_signals:
            continue

        # Deduplicate: keep best signal per symbol per horizon window
        seen = {}
        for s in all_signals:
            sym = s["symbol"]
            if sym not in seen or s["score"] > seen[sym]["score"]:
                seen[sym] = s
        unique_signals = list(seen.values())

        # Evaluate actual outcomes using NEXT TRADING DAY OPEN as entry price
        # (not 5AM pre-market screen price, which would overstate returns).
        evaluated = []
        dropped_corporate_action = 0
        for sig in unique_signals:
            try:
                hist = get_historical_data(sig["symbol"], period="6mo")
                if hist.empty or len(hist) < horizon_days + 5:
                    continue

                # ── Entry price: next trading day OPEN after signal date ──────
                screen_price = sig["entry_price"]
                if screen_price <= 0:
                    continue
                signal_dt = pd.Timestamp(sig["screened_at"])
                after_signal = hist[hist.index > signal_dt]
                if after_signal.empty:
                    continue
                entry_price = float(after_signal["Open"].iloc[0]) if "Open" in after_signal.columns else float(after_signal["Close"].iloc[0])

                # ── Exit price: horizon_days trading days after entry ────────
                future = hist[hist.index >= signal_dt]
                if future.empty or len(future) < max(horizon_days, 5):
                    continue
                exit_idx = min(horizon_days, len(future) - 1)
                exit_price = float(future["Close"].iloc[exit_idx])

                # ── Corporate action / gap detection ──────────────────────────
                # If the exit price moved >50% in a single day, flag as corporate
                # action and exclude from benchmark (but track count).
                recent_returns = future["Close"].pct_change().tail(5)
                if recent_returns.abs().max() > 0.50:
                    dropped_corporate_action += 1
                    continue

                actual_return = (exit_price / entry_price - 1) * 100
                screen_to_entry_gap = (entry_price / screen_price - 1) * 100

                predicted_up = sig["trend"] == "bullish"
                actual_up = actual_return > 0
                direction_correct = predicted_up == actual_up
                hit = actual_return >= 3.0

                mc = sig.get("market_cap")
                tier = "large" if (mc and mc > 10_000_000_000) else "mid" if (mc and mc > 2_000_000_000) else "small"
                sector = sig.get("sector", "Unknown")

                evaluated.append({
                    "symbol": sig["symbol"],
                    "predicted_change_pct": sig["predicted_change_pct"],
                    "actual_return_pct": round(actual_return, 2),
                    "direction_correct": direction_correct,
                    "hit": hit,
                    "tier": tier,
                    "sector": sector,
                    "screen_to_entry_gap_pct": round(screen_to_entry_gap, 2),
                })
            except Exception:
                continue

        n = len(evaluated)
        if dropped_corporate_action > 0:
            print(f"[WFO] h{horizon_days}d: dropped {dropped_corporate_action} signals with corporate actions / extreme gaps.")

        # ── Minimum 20 evaluated signals for statistically meaningful Sharpe ──
        # (Sharpe on <20 observations has CI width >0.4 — not actionable)
        # Horizons without enough data go to INSUFFICIENT_DATA, not AMBER/RED.
        # This includes 63d and 90d horizons at Day 30 — they won't be evaluable
        # until enough signals age to that window.
        if n < 20:
            # Write INSUFFICIENT_DATA record so we know the horizon was checked
            try:
                with db_conn() as conn:
                    conn.execute(text("""
                        INSERT INTO wfo_metrics (run_id, horizon_window_days, total_signals, notes)
                        VALUES (:rid, :hd, :n, 'INSUFFICIENT_DATA')
                        ON CONFLICT (run_id) DO UPDATE SET
                            run_at = NOW(), total_signals = EXCLUDED.total_signals,
                            notes = EXCLUDED.notes
                    """), {"rid": horizon_key, "hd": horizon_days, "n": n})
                    conn.commit()
            except Exception:
                pass
            print(f"[WFO] h{horizon_days}d: only {n} evaluated signals (need ≥20). Status: INSUFFICIENT_DATA")
            continue

        hits = sum(1 for e in evaluated if e["hit"])
        dir_correct = sum(1 for e in evaluated if e["direction_correct"])
        returns = [e["actual_return_pct"] for e in evaluated]
        preds = [e["predicted_change_pct"] for e in evaluated]
        avg_return = sum(returns) / n
        avg_pred = sum(preds) / n
        stdev_return = (sum((r - avg_return) ** 2 for r in returns) / (n - 1)) ** 0.5 if n > 1 else 1.0
        hit_rate = hits / n * 100
        dir_acc = dir_correct / n * 100

        # ── OOS Sharpe: cross-sectional across signals evaluated today ────────
        # Each signal is an independent observation.  We annualize by scaling
        # the cross-sectional mean/stdev ratio by √(252/horizon_days).
        # This is a cross-sectional Sharpe, not a time-series Sharpe.
        oos_sharpe = ((avg_return / 100 - r_f_daily * horizon_days) / (stdev_return / 100 + 1e-9)) * (252 / horizon_days) ** 0.5

        # ── 95% Confidence Interval on OOS Sharpe via bootstrap ───────────────
        import random
        n_boot = 1000
        boot_sharpes = []
        for _ in range(n_boot):
            sample = random.choices(returns, k=n)
            avg_s = sum(sample) / n
            std_s = (sum((r - avg_s) ** 2 for r in sample) / (n - 1)) ** 0.5 if n > 1 else 1.0
            sr_s = ((avg_s / 100 - r_f_daily * horizon_days) / (std_s / 100 + 1e-9)) * (252 / horizon_days) ** 0.5
            boot_sharpes.append(sr_s)
        boot_sharpes.sort()
        sharpe_ci_lower = boot_sharpes[int(n_boot * 0.025)]
        sharpe_ci_upper = boot_sharpes[int(n_boot * 0.975)]

        # ── Benchmark (ASX200 over same horizon window) ───────────────────────
        benchmark_ret = None
        excess_ret = None
        try:
            xjo = get_historical_data("^AXJO", period="6mo")
            if not xjo.empty and len(xjo) >= horizon_days:
                xjo_ret = (float(xjo["Close"].iloc[-1]) / float(xjo["Close"].iloc[-horizon_days]) - 1) * 100
                benchmark_ret = round(xjo_ret, 2)
                excess_ret = round(avg_return - xjo_ret, 2)
        except Exception:
            pass

        # ── Cap-tier breakdown ────────────────────────────────────────────────
        tier_signals = {"large": [], "mid": [], "small": []}
        for e in evaluated:
            tier_signals[e["tier"]].append(e)
        tier_hits = {
            f"hit_rate_{t}": round(sum(1 for x in v if x["hit"]) / len(v) * 100, 2) if v else None
            for t, v in tier_signals.items()
        }

        # ── Per-tier WFO (5% tier vs 3% tier) ────────────────────────────────
        tier10_picks = [e for e in evaluated if e.get("symbol") in top_10pct_symbols]
        tier8_picks = [e for e in evaluated if e.get("symbol") in top_8pct_symbols]
        tier10_hit_rate = round(sum(1 for x in tier10_picks if x["hit"]) / len(tier10_picks) * 100, 1) if tier10_picks else None
        tier8_hit_rate = round(sum(1 for x in tier8_picks if x["hit"]) / len(tier8_picks) * 100, 1) if tier8_picks else None

        # ── Regime-segmented WFO ─────────────────────────────────────────────
        regime_map = {"bull": [], "bear": [], "sideways": []}
        try:
            xjo_hist = get_historical_data("^AXJO", period="6mo")
            if xjo_hist is not None and not xjo_hist.empty and len(xjo_hist) >= 200:
                xjo_close = xjo_hist["Close"].astype(float)
                xjo_sma50 = xjo_close.rolling(50).mean()
                xjo_sma200 = xjo_close.rolling(200).mean()
                is_bull = (xjo_close.iloc[-1] > xjo_sma200.iloc[-1]) and (xjo_sma50.iloc[-1] > xjo_sma200.iloc[-1])
                is_bear = xjo_close.iloc[-1] < xjo_sma200.iloc[-1]
                current_regime = "bull" if is_bull else "bear" if is_bear else "sideways"
            else:
                current_regime = "sideways"
        except Exception:
            current_regime = "sideways"

        regime_map[current_regime] = evaluated[:]
        regime_hit = {}
        for reg in ["bull", "bear", "sideways"]:
            r_list = regime_map[reg]
            regime_hit[reg] = round(sum(1 for x in r_list if x["hit"]) / len(r_list) * 100, 1) if r_list else None

        # ── Peak-based evaluation (align with model training labels) ────────
        peak_evaluated = []
        for sig in unique_signals:
            try:
                hist = get_historical_data(sig["symbol"], period="6mo")
                if hist.empty or len(hist) < horizon_days + 5:
                    continue
                screen_price = sig["entry_price"]
                if screen_price <= 0:
                    continue
                signal_dt = pd.Timestamp(sig["screened_at"])
                after_signal = hist[hist.index > signal_dt]
                if after_signal.empty:
                    continue
                entry_price = float(after_signal["Open"].iloc[0]) if "Open" in after_signal.columns else float(after_signal["Close"].iloc[0])
                future = hist[hist.index >= signal_dt]
                if future.empty or len(future) < max(horizon_days, 5):
                    continue
                exit_idx = min(horizon_days, len(future) - 1)
                peak_price = float(future["High"].iloc[:exit_idx+1].max())
                close_price = float(future["Close"].iloc[exit_idx])
                peak_return = (peak_price / entry_price - 1) * 100
                close_return = (close_price / entry_price - 1) * 100
                peak_hit = peak_return >= 3.0
                mc = sig.get("market_cap")
                peak_evaluated.append({
                    "symbol": sig["symbol"],
                    "peak_return_pct": round(peak_return, 2),
                    "close_return_pct": round(close_return, 2),
                    "peak_hit": peak_hit,
                    "close_hit": close_return >= 3.0,
                    "tier": "10pct" if sig["symbol"] in top_10pct_symbols else "8pct" if sig["symbol"] in top_8pct_symbols else "other",
                })
            except Exception:
                continue

        peak_hits = sum(1 for e in peak_evaluated if e["peak_hit"])
        peak_n = len(peak_evaluated)
        peak_hit_rate = round(peak_hits / peak_n * 100, 1) if peak_n > 0 else None
        close_hits = sum(1 for e in peak_evaluated if e["close_hit"])
        close_hit_rate = round(close_hits / peak_n * 100, 1) if peak_n > 0 else None

        # ── Sector concentration audit ────────────────────────────────────────
        # If >30% of evaluated signals are from a single sector, flag it —
        # bootstrap CI assumes independence which breaks under concentration.
        sector_counts = {}
        for e in evaluated:
            s = e.get("sector", "Unknown")
            sector_counts[s] = sector_counts.get(s, 0) + 1
        max_sector_name = max(sector_counts, key=sector_counts.get) if sector_counts else "Unknown"
        max_sector_pct = round(sector_counts[max_sector_name] / n * 100, 1) if sector_counts else 0
        concentration_warning = (
            f"⚠️ Sector concentration: {max_sector_name} = {max_sector_pct}% of signals. "
            f"Bootstrap CI assumes independence — >30% from one sector undermines this. "
            f"Consider sub-sector analysis before acting on GREEN/AMBER/RED."
        ) if max_sector_pct > 30 else None

        # ── Screen-to-entry gap monitoring ────────────────────────────────────
        gaps = [e.get("screen_to_entry_gap_pct", 0) or 0 for e in evaluated]
        avg_gap = sum(gaps) / n if gaps else 0
        gap_warning = (
            f"⚠️ Front-run risk: average screen-to-entry gap is {avg_gap:+.3f}%. "
            f"If consistently >|0.3%|, your 5AM signals are being priced in before market open — "
            f"real returns are {abs(avg_gap):.2f}% worse than reported per signal."
        ) if abs(avg_gap) > 0.3 else None

        # ── Pre-committed response rules (time-boxed, documented) ────────────
        # GREEN:  95% CI lower bound ≥ 0 → model has edge, full allocation
        # AMBER:  Sharpe ≥ 0 but CI straddles zero → edge unproven, widen stops
        # RED:    Sharpe < 0 → halve allocation, manual review (5-day deadline)
        # MANUAL: Past RED → in manual review window, no auto-adjustments
        # INSUFFICIENT_DATA: <20 evaluated signals, no decision taken

        review_deadline = None
        if sharpe_ci_lower >= 0:
            edge_status = "GREEN"
            action = "Model has edge (95% CI lower bound ≥ 0). Continue at full allocation."
            manual_review_active = False
        elif oos_sharpe >= 0:
            edge_status = "AMBER"
            action = "Edge unproven (95% CI straddles zero). Widen stops by 1%, hold allocation. Review in 14 days."
            manual_review_active = False
        elif oos_sharpe < 0:
            # Check if we're already in an active manual review from a prior RED
            try:
                with db_conn() as conn:
                    prev = conn.execute(text("""
                        SELECT notes FROM wfo_metrics
                        WHERE horizon_window_days = :hd
                          AND notes LIKE '%MANUAL_REVIEW_ACTIVE%'
                        ORDER BY run_at DESC LIMIT 1
                    """), {"hd": horizon_days}).fetchone()
                already_in_review = prev is not None
            except Exception:
                already_in_review = False

            if already_in_review:
                edge_status = "RED_MANUAL_REVIEW"
                action = (
                    "MANUAL REVIEW IN PROGRESS — must conclude within 5 trading days of first RED. "
                    "Decision required: RESUME (return to full allocation), HOLD (stay at 50%), or EXIT (close all model-driven positions). "
                    "No new model-driven positions opened until review concludes."
                )
                manual_review_active = True
            else:
                edge_status = "RED"
                review_deadline = (today + timedelta(days=7)).isoformat()  # 5 trading days ≈ 7 calendar
                action = (
                    f"No edge (OOS Sharpe < 0, CI lower bound {sharpe_ci_lower:.3f}). "
                    f"Halve allocation immediately. Manual review required by {review_deadline}. "
                    "Review must conclude with documented decision: RESUME / HOLD / EXIT. "
                    "This note is the audit trail — the decision date and rationale must be recorded."
                )
                manual_review_active = True

        # Combine all notes
        full_notes_parts = [f"{edge_status}: {action}"]
        full_notes_parts.append(f"Regime={current_regime} HitRate={regime_hit.get(current_regime,'?')}% | PeakHitRate={peak_hit_rate}% vs CloseHitRate={close_hit_rate}%")
        full_notes_parts.append(f"10pct_tier_hit={tier10_hit_rate}% | 8pct_tier_hit={tier8_hit_rate}%")
        if concentration_warning:
            full_notes_parts.append(concentration_warning)
        if gap_warning:
            full_notes_parts.append(gap_warning)
        full_notes = " | ".join(full_notes_parts)

        print(
            f"[WFO] h{horizon_days}d | n={n} | Hit={hit_rate:.1f}% | Dir={dir_acc:.1f}% | "
            f"Sharpe={oos_sharpe:.3f} (95% CI [{sharpe_ci_lower:.3f}, {sharpe_ci_upper:.3f}]) | "
            f"Bmk={benchmark_ret}% | Excess={excess_ret}% | "
            f"10%Tier={tier10_hit_rate}% | 8%Tier={tier8_hit_rate}% | "
            f"Regime={current_regime}({regime_hit.get(current_regime,'?')}%) | "
            f"PeakHit={peak_hit_rate}% vs CloseHit={close_hit_rate}% | "
            f"{edge_status}"
        )
        if concentration_warning:
            print(f"[WFO]   {concentration_warning}")
        if gap_warning:
            print(f"[WFO]   {gap_warning}")

        try:
            with db_conn() as conn:
                conn.execute(text("""
                    INSERT INTO wfo_metrics (
                        run_id, horizon_window_days, total_signals, hit_count, hit_rate_pct,
                        avg_return_pct, avg_predicted_return_pct, return_stdev_pct,
                        oos_sharpe, oos_sharpe_ci_lower, oos_sharpe_ci_upper,
                        direction_accuracy_pct,
                        benchmark_return_pct, excess_return_pct,
                        signal_count_large, signal_count_mid, signal_count_small,
                        hit_rate_large, hit_rate_mid, hit_rate_small,
                        corporate_action_drops,
                        top_10_picks, notes
                    ) VALUES (
                        :rid, :horizon, :total, :hits, :hit_rate,
                        :avg_ret, :avg_pred, :stdev,
                        :sharpe, :ci_low, :ci_high,
                        :dir_acc,
                        :bmk, :excess,
                        :sc_large, :sc_mid, :sc_small,
                        :hr_large, :hr_mid, :hr_small,
                        :ca_drops,
                        :top10, :notes
                    )
                    ON CONFLICT (run_id) DO UPDATE SET
                        run_at = NOW(),
                        total_signals = EXCLUDED.total_signals,
                        hit_rate_pct = EXCLUDED.hit_rate_pct,
                        oos_sharpe = EXCLUDED.oos_sharpe,
                        oos_sharpe_ci_lower = EXCLUDED.oos_sharpe_ci_lower,
                        oos_sharpe_ci_upper = EXCLUDED.oos_sharpe_ci_upper,
                        direction_accuracy_pct = EXCLUDED.direction_accuracy_pct,
                        benchmark_return_pct = EXCLUDED.benchmark_return_pct,
                        excess_return_pct = EXCLUDED.excess_return_pct,
                        notes = EXCLUDED.notes
                """), {
                    "rid": horizon_key, "horizon": horizon_days,
                    "total": n, "hits": hits, "hit_rate": round(hit_rate, 2),
                    "avg_ret": round(avg_return, 4), "avg_pred": round(avg_pred, 4),
                    "stdev": round(stdev_return, 4), "sharpe": round(oos_sharpe, 4),
                    "ci_low": round(sharpe_ci_lower, 4), "ci_high": round(sharpe_ci_upper, 4),
                    "dir_acc": round(dir_acc, 2),
                    "bmk": benchmark_ret, "excess": excess_ret,
                    "sc_large": len(tier_signals["large"]),
                    "sc_mid": len(tier_signals["mid"]),
                    "sc_small": len(tier_signals["small"]),
                    "hr_large": tier_hits["hit_rate_large"],
                    "hr_mid": tier_hits["hit_rate_mid"],
                    "hr_small": tier_hits["hit_rate_small"],
                    "ca_drops": dropped_corporate_action,
                    "top10": json.dumps(sorted(evaluated, key=lambda x: x["actual_return_pct"], reverse=True)[:10]),
                    "notes": full_notes,
                })
                conn.commit()
        except Exception as e:
            print(f"[WFO] DB write failed: {e}")

    _WFO_LAST_HORIZON = 0

    # ── Summary with response rules ───────────────────────────────────────────
    try:
        with db_conn() as conn:
            latest = conn.execute(text("""
                SELECT horizon_window_days, oos_sharpe, oos_sharpe_ci_lower, oos_sharpe_ci_upper,
                       hit_rate_pct, direction_accuracy_pct, notes
                FROM wfo_metrics
                ORDER BY run_at DESC LIMIT 3
            """)).fetchall()
        if latest:
            print(f"[WFO] === Walk-Forward OOS Summary ===")
            for row in latest:
                ci_str = f"CI [{row[2]:.3f}, {row[3]:.3f}]" if row[2] is not None else "CI pending"
                print(f"[WFO]   {row[0]}d: Sharpe={row[1]:.3f} {ci_str}  Hit={row[4]:.1f}%  Dir={row[5]:.1f}%")
                print(f"[WFO]         {row[6]}")
    except Exception:
        pass


def _scheduled_wealth_builder_evaluate():
    """Scheduled job: evaluate past wealth builder candidates."""
    try:
        # Evaluate anything from 14 days ago up to 90 days ago
        cutoff = datetime.utcnow() - timedelta(days=14)
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, symbol, market, price_at_screen, screened_at
                FROM wealth_builder_evaluations
                WHERE evaluated = FALSE AND screened_at <= :cutoff
                LIMIT 50
            """), {"cutoff": cutoff}).fetchall()

            for row in rows:
                eid, sym, mkt, price, at_str = row[0], row[1], row[2], row[3], row[4]
                try:
                    at_date = at_str.replace(tzinfo=None) if hasattr(at_str, 'replace') else at_str
                    hist = get_historical_data(sym, period="6mo")
                    if hist.empty or len(hist) < 14:
                        if isinstance(at_date, datetime) and (datetime.utcnow() - at_date).days > 30:
                            conn.execute(text("""
                                UPDATE wealth_builder_evaluations
                                SET actual_return_14d = -100, actual_return_30d = -100,
                                    actual_return_90d = -100, actual_peak_return_90d = 0,
                                    actual_max_drawdown_90d = -100, evaluated = TRUE
                                WHERE id = :eid
                            """), {"eid": eid})
                        continue
                    
                    prices_after = hist[hist.index > pd.Timestamp(at_date)]
                    if prices_after.empty:
                        continue
                    
                    current_p = float(price) if price else 1.0
                    actual_14d = round((float(prices_after["Close"].iloc[min(14, len(prices_after)) - 1]) / current_p - 1) * 100, 2) if len(prices_after) >= 14 else None
                    actual_30d = round((float(prices_after["Close"].iloc[min(30, len(prices_after)) - 1]) / current_p - 1) * 100, 2) if len(prices_after) >= 30 else None
                    actual_63d = round((float(prices_after["Close"].iloc[min(63, len(prices_after)) - 1]) / current_p - 1) * 100, 2) if len(prices_after) >= 63 else None
                    actual_90d = round((float(prices_after["Close"].iloc[min(90, len(prices_after)) - 1]) / current_p - 1) * 100, 2) if len(prices_after) >= 90 else None
                    
                    peak_ret = round((float(prices_after["Close"].max()) / current_p - 1) * 100, 2)
                    max_dd = round((float(prices_after["Close"].min()) / current_p - 1) * 100, 2)

                    # Only mark as fully evaluated when we have hit the 90-day horizon
                    is_final = len(prices_after) >= 90 or (datetime.utcnow() - at_date).days >= 100

                    conn.execute(text("""
                        UPDATE wealth_builder_evaluations
                        SET actual_return_14d = :r14, actual_return_30d = :r30,
                            actual_return_90d = :r90, actual_peak_return_90d = :pk,
                            actual_max_drawdown_90d = :dd, evaluated = :final
                        WHERE id = :eid
                    """), {"r14": actual_14d, "r30": actual_30d, "r90": actual_90d,
                           "pk": peak_ret, "dd": max_dd, "final": is_final, "eid": eid})
                except Exception:
                    pass
            conn.commit()
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# AUTONOMOUS POSITIONS MONITOR — runs every 2 hours during market hours
# ─────────────────────────────────────────────────────────────────────────────
def _scheduled_positions_monitor():
    # ── Hourly auto-positions monitor alerts SUPPRESSED — monitor still runs, alerts disabled ──
    return
    try:
        from datetime import time as _dt_time
        melbourne_tz = _get_scheduler_timezone()
        now_mel = datetime.now(melbourne_tz)
        if now_mel.weekday() >= 5:
            return
        market_open = _dt_time(10, 0)
        market_close = _dt_time(16, 0)
        current_time = now_mel.time()
        if current_time < market_open or current_time > market_close:
            return

        with db_conn() as conn:
            users_rows = conn.execute(text(
                "SELECT DISTINCT user_id FROM advice_execution_actions"
            )).fetchall()

        for row in users_rows:
            user_id = row[0]
            holdings = get_user_execution_holdings(user_id)
            if not holdings:
                continue

            recipients = get_user_telegram_recipients(user_id)
            if not recipients:
                continue

            for h in holdings:
                sym = h["symbol"]
                market = h.get("market", "AU")
                avg_cost = h["avg_cost"]
                invested = h["invested_amount"]

                try:
                    sd = get_stock_data(sym, market)
                    live_price = sd.get("current_price", 0) or 0
                except Exception:
                    continue

                if live_price <= 0:
                    continue

                pnl_pct = ((live_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0

                try:
                    hist = get_historical_data(sym, period="6mo")
                    if hist is not None and len(hist) >= 50:
                        indicators = calculate_technical_indicators(hist)
                        rsi = indicators.get("rsi")
                    else:
                        rsi = None
                except Exception:
                    rsi = None

                try:
                    val = get_valuation_metrics(sym)
                    target_mean = val.get("analyst_target_mean")
                    days_to_e = val.get("days_to_earnings")
                except Exception:
                    target_mean = None
                    days_to_e = None

                alert = None
                alert_type = None

                if pnl_pct <= -8:
                    alert_type = "stop_loss"
                    alert = (
                        f"<b>🔴 STOP LOSS WARNING — {sym}.{market}</b>\n\n"
                        f"Your entry: ${avg_cost:.2f} | Current: ${live_price:.2f}\n"
                        f"P&amp;L: -${abs(invested * abs(pnl_pct) / 100):,.2f} ({pnl_pct:+.1f}%)\n"
                        f"⚠️ Down more than 8% from entry\n\n"
                        f"📊 <b>Action recommended:</b> Review immediately. Consider cutting losses or hedging.\n"
                        f"Stop loss reference: ~${round(avg_cost * 0.92, 2)}"
                    )
                elif target_mean and live_price >= float(target_mean) and pnl_pct > 0:
                    alert_type = "profit_target"
                    upside = pnl_pct
                    target_float = float(target_mean)
                    alert = (
                        f"<b>🟢 PROFIT TARGET REACHED — {sym}.{market}</b>\n\n"
                        f"Your entry: ${avg_cost:.2f} | Current: ${live_price:.2f}\n"
                        f"P&amp;L: +${round(invested * upside / 100, 2):,.2f} (+{upside:.1f}%)\n"
                        f"Analyst target: ${target_float:.2f}\n"
                        + (f"RSI: {rsi:.0f}" if rsi is not None else "")
                        + f"\n\n📊 <b>Action recommended:</b> Consider taking profit.\n"
                        f"Reply <code>SELL {sym} [qty] [price]</code> to record your sell."
                    )
                elif rsi is not None and rsi > 75:
                    alert_type = "overbought"
                    alert = (
                        f"<b>🟡 OVERBOUGHT — {sym}.{market}</b>\n\n"
                        f"RSI: {rsi:.1f} — overbought territory\n"
                        f"Current price: ${live_price:.2f} | Entry: ${avg_cost:.2f}\n"
                        f"P&amp;L: {pnl_pct:+.1f}%\n\n"
                        f"📊 <b>Action recommended:</b> Monitor closely. Consider trailing stop."
                    )
                elif pnl_pct <= -2:
                    alert_type = "daily_drop"
                    alert = (
                        f"<b>📉 PORTFOLIO DIP — {sym}.{market}</b>\n\n"
                        f"Down {abs(pnl_pct):.1f}% today\n"
                        f"Current: ${live_price:.2f} | Entry: ${avg_cost:.2f}\n\n"
                        f"📊 <b>Note:</b> If broader market is also down, this may be normal."
                    )
                else:
                    # Check sentiment if no other urgent alerts
                    sentiment = _check_and_cache_sentiment(user_id, sym, market, datetime.utcnow())
                    if sentiment.get("sentiment") == "negative" and sentiment.get("score", 0) < -0.3:
                        par = _calculate_profit_at_risk(sym, market, holdings)
                        alert_type = "negative_news"
                        alert = (
                            f"<b>⚠️ NEGATIVE NEWS — {sym}.{market}</b>\n\n"
                            f"<i>\"{sentiment.get('headline', '')}\"</i>\n"
                            f"Score: {sentiment.get('score', 0):.2f}\n\n"
                            f"P&amp;L: {pnl_pct:+.1f}%\n"
                        )
                        if par and par.get("potential_loss_at_risk_pct"):
                            alert += f"Risk if drops 5%: -${par['potential_loss_at_risk_pct']:.2f}\n"
                        alert += f"\n📊 <b>Action recommended:</b> Review position immediately."

                if alert and alert_type:
                    last_key = f"pos_{sym}_{alert_type}"
                    try:
                        with db_conn() as check_conn:
                            existing = check_conn.execute(text(
                                "SELECT 1 FROM telegram_send_log "
                                "WHERE user_id = :uid AND message_type = 'position_alert' "
                                "AND payload_preview LIKE :pat "
                                "AND created_at > :since "
                                "LIMIT 1"
                            ), {
                                "uid": user_id,
                                "pat": f"%{alert_type}%{sym}%",
                                "since": datetime.utcnow() - timedelta(hours=24),
                            }).fetchone()
                            if existing:
                                continue
                    except Exception:
                        pass

                    _send_telegram_payload(
                        alert,
                        recipients,
                        user_id=user_id,
                        message_type="position_alert",
                        market=market,
                        delivery_mode="auto",
                        source="auto_positions_monitor",
                    )
                    log_position_event("auto", user_id, alert_type,
                                       alert.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")[:300],
                                       {"symbol": sym, "live_price": live_price, "pnl_pct": round(pnl_pct, 2)})

    except Exception:
        import traceback
        traceback.print_exc()

@app.get("/api/weekly/backtest")
async def weekly_backtest(market: str = "AU", weeks: int = 8, current_user: dict = Depends(get_current_user)):
    """Enhanced backtest: past weekly picks vs ASX200 benchmark.

    Returns per-week accuracy, cumulative returns, and index comparison.
    """
    del current_user
    m = (market or "AU").upper()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT e.symbol, e.predicted_change_pct, e.actual_change_7d_pct, e.direction_correct,
                   d.week_iso, d.generated_at
            FROM weekly_pick_evaluations e
            JOIN weekly_digests d ON d.id = e.digest_id
            WHERE d.market = :market AND e.actual_change_7d_pct IS NOT NULL
            ORDER BY d.week_iso DESC, e.symbol
            LIMIT :lim
        """), {"market": m, "lim": weeks * 20}).fetchall()

    if not rows:
        return {"market": m, "weeks": [], "summary": {"direction_accuracy_pct": 0, "avg_predicted": 0, "avg_actual": 0}}

    by_week = {}
    all_evals = []
    for r in rows:
        sym, pred, actual, dir_correct, week, gen_at = r
        ev = {"symbol": sym, "predicted": pred or 0, "actual": actual or 0, "direction_correct": bool(dir_correct)}
        all_evals.append(ev)
        if week not in by_week:
            by_week[week] = {"picks": [], "generated_at": str(gen_at) if gen_at else None}
        by_week[week]["picks"].append(ev)

    weeks_list = []
    for wk, data in sorted(by_week.items(), reverse=True):
        picks = data["picks"]
        if picks:
            avg_pred = sum(p["predicted"] for p in picks) / len(picks)
            avg_actual = sum(p["actual"] for p in picks) / len(picks)
            dir_acc = sum(1 for p in picks if p["direction_correct"]) / len(picks) * 100
            weeks_list.append({
                "week": wk,
                "picks_count": len(picks),
                "avg_predicted_pct": round(avg_pred, 2),
                "avg_actual_pct": round(avg_actual, 2),
                "direction_accuracy": round(dir_acc, 1),
                "generated_at": data["generated_at"],
            })

    # Try to get XJO/AXJO benchmark for AU
    benchmark_return = None
    if m == "AU":
        try:
            xjo = get_historical_data("^AXJO", period="6mo")
            if len(xjo) >= 126:
                benchmark_return = round((float(xjo["Close"].iloc[-1]) / float(xjo["Close"].iloc[-126])) - 1, 4) * 100
        except Exception:
            pass

    dir_acc = sum(1 for r in rows if r[3]) / len(rows) * 100 if rows else 0
    return {
        "market": m,
        "weeks": weeks_list[:weeks],
        "summary": {
            "total_picks_evaluated": len(all_evals),
            "direction_accuracy_pct": round(dir_acc, 2),
            "avg_predicted_pct": round(sum(e["predicted"] for e in all_evals) / len(all_evals), 2) if all_evals else 0,
            "avg_actual_pct": round(sum(e["actual"] for e in all_evals) / len(all_evals), 2) if all_evals else 0,
            "benchmark_6mo_pct": benchmark_return,
            "outperform": all_evals and benchmark_return is not None and
                (sum(e["actual"] for e in all_evals) / len(all_evals)) > benchmark_return,
        },
        "evaluations": all_evals[:50],
        "walk_forward_note": "For out-of-sample validation, track OOS Sharpe on a rolling 8-week window. Currently: directional accuracy only. OOS Sharpe = (avg_actual - risk_free) / stdev(actual) × sqrt(52/n_weeks). This is the single most important metric — until this is ≥0.25, the model has no edge.",
    }
@app.get("/api/suggestions/tracking")
async def get_suggestion_tracking(current_user: dict = Depends(get_current_user), days: int = 30):
    """Return suggestions with tier, entry, tracking status. Pass days=N to look back further."""
    del current_user
    rows_list = []
    try:
        cutoff = datetime.utcnow() - timedelta(days=max(days, 1))
        with db_conn() as conn:
            rows = conn.execute(text("""
                SELECT symbol, price_at_screen, target_tier, screened_at, predicted_change_pct,
                       evaluated, actual_return_63d, actual_peak_return_90d
                FROM wealth_builder_evaluations
                WHERE screened_at >= :cutoff
                ORDER BY screened_at DESC LIMIT 200
            """), {"cutoff": cutoff}).fetchall()
        for r in rows:
            entry_price = float(r[1] or 0)
            target_tier = r[2] or ""
            tier_pct = 10 if target_tier == "10pct" else 8 if target_tier == "8pct" else None
            target_price = round(entry_price * (1 + tier_pct / 100), 2) if tier_pct and entry_price > 0 else None
            days_ago = (datetime.utcnow() - r[3].replace(tzinfo=None)).days if r[3] else None
            evaluated = r[5]
            actual_ret = float(r[6] or 0) if r[6] else None
            peak_ret = float(r[7] or 0) if r[7] else None
            if evaluated and tier_pct:
                # Hit if peak within 90 days reached the tier target
                status = "HIT" if peak_ret and peak_ret >= tier_pct else "MISS"
            elif evaluated:
                status = "EVALUATED"
            else:
                status = "PENDING"
            rows_list.append({
                "symbol": r[0], "entry": entry_price, "tier": target_tier or "N/A",
                "target_pct": tier_pct, "target_price": target_price,
                "screened_at": str(r[3]), "days_ago": days_ago,
                "predicted_pct": float(r[4] or 0),
                "evaluated": evaluated, "actual_return_63d": actual_ret,
                "actual_peak_90d": peak_ret, "status": status,
            })
    except Exception as e:
        pass
    return {"suggestions": rows_list, "count": len(rows_list)}


@app.get("/api/model/status")
async def model_status(current_user: dict = Depends(get_current_user)):
    """Return latest model training metadata: training date, R², sample count, ensemble composition."""
    del current_user
    try:
        with db_conn() as conn:
            row = conn.execute(text("""
                SELECT trained_at, model_type, sample_size, in_sample_hit_rate, notes
                FROM model_weights_by_date
                ORDER BY trained_at DESC LIMIT 1
            """)).fetchone()
            if row:
                return {
                    "latest_training_date": str(row[0])[:10] if row[0] else None,
                    "model_type": row[1] or "unknown",
                    "sample_size": row[2],
                    "r2": float(row[3] or 0),
                    "notes": row[4],
                    "feature_count": len(FEATURE_COLS) if FEATURE_COLS else 51,
                }
    except Exception:
        pass
    return {"latest_training_date": None, "model_type": "unknown", "sample_size": 0, "r2": 0, "feature_count": 0}


@app.get("/api/walk-forward/oos")
async def walk_forward_oos_metrics(current_user: dict = Depends(get_current_user)):
    """Return the latest walk-forward out-of-sample validation metrics.

    Computed daily after market close across 30d, 63d, and 90d horizons.
    Key metric: OOS Sharpe >= 0.25 indicates statistical edge.
    Returns per-horizon time series for trend analysis.
    """
    del current_user
    _ensure_wfo_table()
    try:
        with db_conn() as conn:
            rows = conn.execute(text("""
                SELECT run_id, run_at, horizon_window_days, total_signals, hit_rate_pct,
                       avg_return_pct, avg_predicted_return_pct, return_stdev_pct,
                       oos_sharpe, oos_sharpe_ci_lower, oos_sharpe_ci_upper,
                       direction_accuracy_pct,
                       benchmark_return_pct, excess_return_pct,
                       hit_rate_large, hit_rate_mid, hit_rate_small,
                       corporate_action_drops,
                       top_10_picks, notes
                FROM wfo_metrics
                ORDER BY run_at DESC
                LIMIT 90
            """)).fetchall()
    except Exception as e:
        return {"error": str(e), "metrics": []}

    by_horizon = {30: [], 63: [], 90: []}
    latest = {30: None, 63: None, 90: None}
    for r in rows:
        h = r[2]
        entry = {
            "date": str(r[1])[:10] if r[1] else None,
            "run_id": r[0],
            "total_signals": r[3],
            "hit_rate_pct": float(r[4] or 0),
            "avg_return_pct": float(r[5] or 0),
            "oos_sharpe": float(r[8] or 0),
            "oos_sharpe_ci_lower": float(r[9] or 0) if r[9] is not None else None,
            "oos_sharpe_ci_upper": float(r[10] or 0) if r[10] is not None else None,
            "direction_accuracy_pct": float(r[11] or 0),
            "benchmark_return_pct": float(r[12] or 0) if r[12] is not None else None,
            "excess_return_pct": float(r[13] or 0) if r[13] is not None else None,
            "hit_rate_large": float(r[14] or 0) if r[14] is not None else None,
            "hit_rate_mid": float(r[15] or 0) if r[15] is not None else None,
            "hit_rate_small": float(r[16] or 0) if r[16] is not None else None,
            "corporate_action_drops": r[17] or 0,
            "notes": r[19],
        }
        if h in by_horizon:
            by_horizon[h].append(entry)
            if latest[h] is None:
                latest[h] = entry

    return {
        "latest_30d": latest[30],
        "latest_63d": latest[63],
        "latest_90d": latest[90],
        "history_30d": by_horizon[30][:30],
        "history_63d": by_horizon[63][:30],
        "history_90d": by_horizon[90][:30],
        "edge_threshold": 0.25,
        "response_rules": {
            "GREEN": "95% CI lower bound ≥ 0 — model has statistical edge. Continue at full allocation.",
            "AMBER": "95% CI straddles zero — edge unproven. Widen stops by 1%. Hold allocation, do not increase. Re-evaluate in 14 days.",
            "RED": "OOS Sharpe < 0 — no edge. Halve allocation immediately. Manual review required within 5 trading days. Must conclude with documented RESUME/HOLD/EXIT decision.",
            "RED_MANUAL_REVIEW": "Prior RED triggered manual review. No new model-driven positions until review concludes with documented decision.",
            "INSUFFICIENT_DATA": "Fewer than 20 evaluated signals for this horizon. No decision taken — status is informational only. Check again when signal count reaches 20+.",
        },
        "has_edge": any(
            (latest[h] or {}).get("oos_sharpe", 0) >= 0.25
            and (latest[h] or {}).get("oos_sharpe_ci_lower", -99) >= 0
            for h in [30, 63, 90] if latest[h]
        ),
        "manual_review_active": any(
            (latest[h] or {}).get("notes", "").startswith("RED")
            for h in [30, 63, 90] if latest[h]
        ),
        "insufficient_data_horizons": [
            h for h in [30, 63, 90]
            if latest[h] and (latest[h].get("notes") or "").startswith("INSUFFICIENT_DATA")
        ],
        "capital_gate": get_current_wfo_state(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM BOT WEBHOOK — receives messages directly from the Telegram bot
# ─────────────────────────────────────────────────────────────────────────────

def _parse_trade_command(text: str) -> Optional[dict]:
    """Parse a natural-language trade command from Telegram.

    Supported formats:
      BUY BHP 100 45.50
      BUY BHP 100 @ 45.50
      SELL CBA 50 123
      ADD FMG 200 18.50
      REDUCE WOW 100 @ 30.00
    Returns dict with keys: action, symbol, quantity, price  OR  None.
    """
    import re as _re
    t = text.strip().upper()
    # BUY|SELL|ADD|REDUCE  SYMBOL  QTY  [@]  PRICE
    m = _re.match(
        r'^(BUY|SELL|ADD|REDUCE)\s+([A-Z0-9\-\.]+)\s+([\d.]+)\s*@?\s*([\d.]+)',
        t
    )
    if m:
        return {
            "action": m.group(1),
            "symbol": m.group(2),
            "quantity": float(m.group(3)),
            "price": float(m.group(4)),
        }
    return None


def _send_telegram_reply(chat_id: str, text: str) -> None:
    """Send a simple text message to a specific chat_id."""
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception:
        pass


def _send_telegram_broadcast(message_html: str, message_type: str = "smsf") -> None:
    """Send a message to all active Telegram recipients across all users. Fire-and-forget."""
    if not TELEGRAM_BOT_TOKEN or not message_html:
        return
    try:
        with db_conn() as conn:
            recipients = conn.execute(text("""
                SELECT DISTINCT chat_id FROM user_telegram_recipients WHERE is_active = TRUE
            """)).fetchall()
            for (chat_id,) in recipients:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": message_html, "parse_mode": "HTML"},
                        timeout=8,
                    )
                except Exception:
                    pass
    except Exception:
        pass


def _send_telegram_reply(chat_id: str, text: str) -> None:
    """Send a plain-text reply back to a Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception:
        pass


def _auto_create_paper_trade(user_id: str, symbol: str, market: str, quantity: float, entry_price: float,
                             source_reason: str = "telegram_buy", notes: str = None) -> Optional[str]:
    """Create a paper trade with sensible defaults for auto-monitoring.
    Returns the trade_id or None on failure.
    Prevents duplicate open positions for the same symbol.
    Blocks trades if model quality is below threshold (R² < 0.05).
    """
    try:
        # ── Model quality gate — block trades if model is random noise ──────
        try:
            with db_conn() as conn:
                latest_auc = conn.execute(text(
                    "SELECT in_sample_hit_rate FROM model_weights_by_date "
                    "WHERE model_type='logistic' AND feature_name='rsi' "
                    "ORDER BY trained_at DESC LIMIT 1"
                )).fetchone()
                if latest_auc and latest_auc[0] is not None and latest_auc[0] < 0.52:
                    print(f"[AutoCreate] BLOCKED: Model AUC={latest_auc[0]:.3f} < 0.52 threshold — no signal quality")
                    return None
                elif latest_auc and latest_auc[0] is not None:
                    print(f"[AutoCreate] Model quality OK: AUC={latest_auc[0]:.3f}")
        except Exception as e:
            # Non-blocking — if we can't check AUC, allow trade but log warning
            print(f"[AutoCreate] Model quality check failed (allowing trade): {e}")

        # ── Duplicate position guard ──────────────────────────────────────
        with db_conn() as conn:
            existing = conn.execute(text("""
                SELECT COUNT(*) FROM paper_trades
                WHERE user_id = :uid AND symbol = :sym AND status = 'open'
            """), {"uid": user_id, "sym": symbol}).fetchone()
            if existing and existing[0] > 0:
                print(f"[AutoCreate] SKIP {symbol} — open position already exists")
                return None
        # ── End duplicate guard ───────────────────────────────────────────

        # ── Portfolio Gate (hard risk limits) ──────────────────────────────
        if _PORTFOLIO_GATE_AVAILABLE and _PORTFOLIO_GATE is not None:
            try:
                from portfolio_gate import PortfolioGate
                pf = _compute_portfolio_state(user_id)
                open_positions = []
                sector_map = {}
                with db_conn() as conn:
                    rows = conn.execute(text(
                        "SELECT symbol, COALESCE(entry_price,0)*COALESCE(quantity,0) AS value FROM paper_trades WHERE status='open' AND user_id=:uid"
                    ), {"uid": user_id}).fetchall()
                    for sym, val in rows:
                        try:
                            vm = get_valuation_metrics(sym)
                            sector = str(vm.get("sector", "Unknown"))
                            sector_map[sym] = sector
                        except Exception:
                            sector = "Unknown"
                        open_positions.append({"symbol": sym, "value": float(val or 0), "sector": sector})
                
                adv_20d = 0
                try:
                    sd = get_stock_data(symbol, market)
                    adv_20d = float(sd.get("avg_volume", 0) or 0) * entry_price
                except Exception:
                    pass
                
                try:
                    vm = get_valuation_metrics(symbol)
                    sector = str(vm.get("sector", "Unknown"))
                except Exception:
                    sector = "Unknown"
                
                gate_result = _PORTFOLIO_GATE.can_open_position(
                    proposed_symbol=symbol,
                    proposed_sector=sector,
                    proposed_value=quantity * entry_price,
                    proposed_adv_20d=adv_20d,
                    portfolio_total_value=pf["total_equity"],
                    portfolio_cash=pf["available_cash"],
                    open_positions=open_positions,
                    is_core=False,
                )
                if not gate_result.get("approved", True):
                    print(f"[AutoCreate] PORTFOLIO GATE BLOCKED {symbol}: {gate_result['reason']}")
                    return None
                print(f"[AutoCreate] Portfolio gate passed for {symbol}: {gate_result['reason']}")
            except Exception as e:
                print(f"[AutoCreate] Portfolio gate check failed (non-blocking): {e}")

        # ── Circuit Breaker ───────────────────────────────────────────────
        if _CIRCUIT_BREAKER_AVAILABLE and _CIRCUIT_BREAKER is not None:
            try:
                pf = _compute_portfolio_state(user_id)
                breaker_state = _CIRCUIT_BREAKER.check(pf["total_equity"])
                if not breaker_state.get("allow_new_entries", True):
                    print(f"[AutoCreate] CIRCUIT BREAKER BLOCKED {symbol}: level={breaker_state.get('level')}, dd={breaker_state.get('drawdown_pct')}%")
                    return None
                if breaker_state.get("sell_all", False):
                    print(f"[AutoCreate] CIRCUIT BREAKER RED - all satellite selling, no new entries for {symbol}")
                    return None
            except Exception as e:
                print(f"[AutoCreate] Circuit breaker check failed (non-blocking): {e}")

        # ── Calendar Gate (Friday / bear-market block) ──────────────────────
        try:
            from calendar_gate import get_calendar_status
            cal = get_calendar_status()
            if not cal.get("allow_new_entries", True):
                print(f"[AutoCreate] CALENDAR GATE BLOCKED {symbol}: {cal.get('calendar_signal', 'blocked')} — {cal.get('reason', '')}")
                return None
        except Exception as e:
            pass  # non-blocking if calendar_gate fails to import

        # ── Data Sanity (EODHD vs ASX gap) ──────────────────────────────
        if _DATA_SANITY_AVAILABLE and check_data_sanity:
            try:
                with db_conn() as ds_conn:
                    sanity = check_data_sanity(ds_conn, symbol)
                    if not sanity.get("sane", True):
                        print(f"[AutoCreate] DATA SANITY BLOCKED {symbol}: {sanity['reason']} (gap: {sanity.get('gap_pct', 0):.1f}%)")
                        log_sanity_check(ds_conn, sanity)
                        return None
            except Exception as e:
                pass  # non-blocking

        # ── Kill Switch ──────────────────────────────────────────────────
        if _KILL_SWITCH_AVAILABLE and compute_kill_state and get_latest_kill_state:
            try:
                with db_conn() as ks_conn:
                    ks = get_latest_kill_state(ks_conn)
                    if ks.get("halt_all"):
                        print(f"[AutoCreate] KILL SWITCH BLOCKED {symbol}: automation halted")
                        return None
            except Exception:
                pass

        signal = get_probability_and_score(symbol)
        predicted = float(signal.get("predicted_price_3m") or 0)
        # For a LONG trade, target must be > entry. If the model is bearish, default to 12% upside.
        target_price = predicted if predicted > entry_price else entry_price * 1.12
        stop_loss_price = round(entry_price * 0.92, 3)   # 8% stop
        take_profit_price = round(target_price, 3)
        trade_id = str(uuid4())
        now = datetime.utcnow()
        trade_notes = notes or f'Auto-created from {source_reason}'
        with db_conn() as conn:
            conn.execute(text("""
                INSERT INTO paper_trades
                    (id, user_id, symbol, market, side, quantity, entry_price, current_price,
                     target_price, status, signal_score, signal_trend, signal_warning, notes, peak_price,
                     stop_loss_price, take_profit_price, trailing_stop_pct, review_date,
                     position_stage, recommendation_action, source_reason, updated_at)
                VALUES
                    (:id, :user_id, :symbol, :market, 'LONG', :quantity, :entry_price, :entry_price,
                     :target_price, 'open', :signal_score, :signal_trend, NULL,
                     :notes, :entry_price,
                     :stop_loss, :take_profit, 3.0, :review_date,
                     'entered', 'BUY', :source_reason, :now)
            """), {
                "id": trade_id, "user_id": user_id, "symbol": symbol, "market": market,
                "quantity": quantity, "entry_price": entry_price,
                "target_price": take_profit_price,
                "signal_score": signal.get("score"),
                "signal_trend": signal.get("trend"),
                "stop_loss": stop_loss_price, "take_profit": take_profit_price,
                "review_date": now + timedelta(days=14),
                "now": now,
                "source_reason": source_reason,
                "notes": trade_notes,
            })
        log_position_event(trade_id, user_id, "opened",
                           f"Auto-opened via {source_reason}: {symbol}",
                           {"entry_price": entry_price, "stop_loss": stop_loss_price,
                            "take_profit": take_profit_price, "source_reason": source_reason})
        return trade_id
    except Exception:
        return None


def _auto_close_paper_trade(user_id: str, symbol: str, market: str, close_price: float) -> Optional[str]:
    """Close the most recent open paper trade for a symbol. Returns trade_id or None."""
    try:
        with db_conn() as conn:
            row = conn.execute(text("""
                SELECT id FROM paper_trades
                WHERE user_id = :uid AND symbol = :sym AND market = :mkt AND status = 'open'
                ORDER BY created_at DESC LIMIT 1
            """), {"uid": user_id, "sym": symbol, "mkt": market}).fetchone()
            if not row:
                return None
            trade_id = row[0]
            conn.execute(text("""
                UPDATE paper_trades
                SET status = 'closed', current_price = :cp, closed_at = :now,
                    position_stage = 'closed', updated_at = :now,
                    notes = COALESCE(notes, '') || ' | Closed via Telegram SELL command'
                WHERE id = :tid AND user_id = :uid
            """), {"cp": close_price, "now": datetime.utcnow(), "tid": trade_id, "uid": user_id})
        log_position_event(trade_id, user_id, "closed",
                           f"Closed via Telegram SELL command at {close_price}",
                           {"close_price": close_price})
        return trade_id
    except Exception:
        return None


_TELEGRAM_BOT_COMMANDS_HELP = """\
<b>📱 ASX Bot Commands</b>

<b>Trade Recording (updates your portfolio):</b>
  <code>BUY BHP 100 45.50</code>  — record a buy, auto-starts monitoring
  <code>ADD FMG 200 @ 18.50</code> — add to existing position
  <code>SELL CBA 50 @ 123.00</code> — record a sell, closes monitoring
  <code>REDUCE WOW 100 30.00</code> — reduce position size

<b>Portfolio &amp; Status:</b>
  <code>PORTFOLIO</code> or <code>P</code> — show your current holdings
  <code>STATUS</code> or <code>S</code> — show open monitored positions with P&amp;L
  <code>TRACK BHP</code> — add BHP to your watchlist

<b>Other:</b>
  <code>HELP</code> — show this guide

<i>Prices are recorded at the price you specify. Monitoring uses 8% stop-loss, 3% trailing stop, and model target price. Alerts fire on Telegram automatically.</i>
"""


@app.post("/api/telegram/bot-webhook")
async def telegram_bot_webhook(request: Request):
    """Receive Telegram bot updates (messages from users).
    Telegram calls this endpoint directly — no auth header required.
    Supports trade commands, portfolio queries, and help.
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}

    message = body.get("message") or body.get("edited_message")
    if not message:
        return {"ok": True}

    chat_id = str((message.get("chat") or {}).get("id") or "")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True}

    # Look up user by chat_id
    user_id, ambiguous = _resolve_user_for_chat(chat_id)
    if not user_id:
        _send_telegram_reply(chat_id,
            "Your Telegram chat is not linked to any account.\n"
            "Open the app, go to Strategy > Telegram Setup and add your chat ID: " + chat_id
        )
        return {"ok": True}

    cmd = text.strip().upper()

    # ── HELP ───────────────────────────────────────────────────────────────
    if cmd in {"HELP", "/HELP", "/START"}:
        _send_telegram_reply(chat_id, _TELEGRAM_BOT_COMMANDS_HELP)
        return {"ok": True}

    # ── PORTFOLIO ──────────────────────────────────────────────────────────
    if cmd in {"PORTFOLIO", "P", "/PORTFOLIO"}:
        holdings = get_user_execution_holdings(user_id)
        if not holdings:
            _send_telegram_reply(chat_id, "No holdings recorded yet. Send: <code>BUY BHP 100 45.50</code> to record a trade.")
            return {"ok": True}
        lines = ["<b>💼 Your Portfolio</b>\n"]
        for h in holdings:
            lines.append(
                f"<b>{h['symbol']}</b> — {h['quantity']} @ avg ${h['avg_cost']:.2f} "
                f"| Invested: ${h['invested_amount']:.0f}"
            )
        _send_telegram_reply(chat_id, "\n".join(lines))
        return {"ok": True}

    # ── STATUS ─────────────────────────────────────────────────────────────
    if cmd in {"STATUS", "S", "/STATUS"}:
        trades = list_paper_trades(user_id)
        open_trades = [t for t in trades if t.get("status") == "open"]
        if not open_trades:
            _send_telegram_reply(chat_id, "No open monitored positions. Send <code>BUY SYMBOL QTY PRICE</code> to start tracking.")
            return {"ok": True}
        lines = ["<b>📊 Open Positions</b>\n"]
        for t in open_trades:
            pnl = t.get("unrealized_pnl_pct", 0) or 0
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            stage = (t.get("position_stage") or "entered").upper()
            lines.append(
                f"{pnl_emoji} <b>{t['symbol']}</b> {t.get('quantity')} | "
                f"Entry ${t['entry_price']:.2f} → Now ${t.get('current_price', t['entry_price']):.2f} "
                f"({pnl:+.1f}%) | Stage: {stage}"
            )
        _send_telegram_reply(chat_id, "\n".join(lines))
        return {"ok": True}

    # ── TRACK SYMBOL ───────────────────────────────────────────────────────
    if cmd.startswith("TRACK "):
        sym = cmd.split(" ", 1)[1].strip().upper()
        if sym:
            try:
                with db_conn() as conn:
                    existing = conn.execute(
                        text("SELECT symbol FROM user_shares WHERE user_id = :uid AND symbol = :sym"),
                        {"uid": user_id, "sym": sym}
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            text("INSERT INTO user_shares (user_id, symbol, name) VALUES (:uid, :sym, :name)"),
                            {"uid": user_id, "sym": sym, "name": sym}
                        )
                _send_telegram_reply(chat_id, f"✅ <b>{sym}</b> added to your watchlist.")
            except Exception:
                _send_telegram_reply(chat_id, f"Could not add {sym} to watchlist.")
        return {"ok": True}

    # ── TRADE COMMANDS ─────────────────────────────────────────────────────
    trade_cmd = _parse_trade_command(text)
    if trade_cmd:
        action = trade_cmd["action"]     # BUY | SELL | ADD | REDUCE
        symbol = trade_cmd["symbol"]
        quantity = trade_cmd["quantity"]
        price = trade_cmd["price"]
        market = detect_market(symbol)

        # Record in advice_execution_actions (updates portfolio holdings)
        result = _create_advice_action_for_user(
            user_id=user_id,
            symbol=symbol,
            market=market,
            action_type=action,
            quantity=quantity,
            execution_price=price,
            commission=None,
            advice_cache_key=None,
            source_message_type="telegram_bot",
            notes=f"Telegram: {text}",
        )

        # Auto-create paper trade monitor on BUY/ADD (gated by WFO capital state)
        trade_id = None
        monitoring_msg = ""
        if action in {"BUY", "ADD"}:
            gate = _wfo_position_gate(user_id)
            if not gate["allowed"]:
                monitoring_msg = f"\n\n<b>⛔ Position BLOCKED by WFO gate:</b> {gate['reason']}"
            else:
                # ── Manual trade cap: 2% NAV max ──────────────────────────
                nav = get_starting_capital(user_id)
                trade_value = quantity * price
                if trade_value > nav * 0.02 and nav > 0:
                    monitoring_msg = (
                        f"\n\n<b>⛔ BLOCKED: Manual trade ${trade_value:.0f} exceeds 2% NAV limit "
                        f"(max ${nav*0.02:.0f} for ${nav:.0f} NAV)</b>\n"
                        f"<i>Guardrail #17: all manual trades capped at 2% NAV.</i>"
                    )
                else:
                    trade_id = _auto_create_paper_trade(user_id, symbol, market, quantity, price)
                    if trade_id:
                        stop = round(price * 0.92, 2)
                        target = round(result.get("holdings", [{}])[0].get("avg_cost", price) * 1.12, 2) if action == "BUY" else round(price * 1.12, 2)
                        monitoring_msg = (
                            f"\n\n<b>Monitoring started</b>\n"
                            f"Stop-loss: <b>${stop:.2f}</b> (8% below entry)\n"
                            f"Target: model forecast | Trailing stop: 3%\n"
                            f"You'll get alerts if price hits stop, target, or goes flat 30 days."
                        )

        # Auto-close paper trade monitor on SELL/REDUCE
        closed_id = None
        if action in {"SELL", "REDUCE"}:
            closed_id = _auto_close_paper_trade(user_id, symbol, market, price)
            if closed_id:
                monitoring_msg = "\n\n<b>Position monitoring closed.</b>"

        # Format confirmation
        fee = result.get("commission", 0) or 0
        gross = result.get("gross_amount", 0) or 0
        net = result.get("net_amount", 0) or 0
        action_emoji = {"BUY": "✅", "ADD": "✅", "SELL": "💰", "REDUCE": "💰"}.get(action, "📝")
        reply = (
            f"{action_emoji} <b>{action} {symbol}</b> recorded\n"
            f"Qty: <b>{quantity}</b> @ <b>${price:.2f}</b>\n"
            f"Gross: ${gross:.2f} | Fee: ${fee:.2f} | Net: ${net:.2f}"
            f"{monitoring_msg}\n\n"
            f"<i>Portfolio updated. Check Strategy &gt; Holdings in the app.</i>"
        )
        _send_telegram_reply(chat_id, reply)
        return {"ok": True}

    # ── Unrecognised command ────────────────────────────────────────────────
    _send_telegram_reply(chat_id,
        f"Hmm, I didn't understand that. Try:\n"
        f"<code>BUY BHP 100 45.50</code>\n"
        f"<code>SELL CBA 50 123.00</code>\n"
        f"<code>PORTFOLIO</code> or <code>STATUS</code>\n"
        f"Send <code>HELP</code> for all commands."
    )
    return {"ok": True}


@app.post("/api/telegram/register-webhook")
async def register_telegram_webhook(request: Request, current_user: dict = Depends(get_current_user)):
    """Register (or refresh) the Telegram bot webhook URL with Telegram's API.
    Call this once after deploying or changing the API URL.
    """
    del current_user
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN not configured")

    # Try to auto-detect the public URL from the request Host header
    host = request.headers.get("host") or request.headers.get("x-forwarded-host") or ""
    proto = request.headers.get("x-forwarded-proto") or "https"
    if not host:
        raise HTTPException(status_code=400, detail="Could not determine public host from request headers")

    webhook_url = f"{proto}://{host}/api/telegram/bot-webhook"
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
        json={"url": webhook_url, "allowed_updates": ["message", "edited_message"]},
        timeout=10,
    )
    body = resp.json() if resp.content else {}
    return {
        "ok": body.get("ok"),
        "webhook_url": webhook_url,
        "telegram_response": body,
    }


# ── Job Audit Trail ────────────────────────────────────────────────────────────
def _log_job_start(job_name: str) -> Optional[int]:
    """Insert a 'running' row into job_runs. Returns row ID for later finish."""
    try:
        with db_conn() as conn:
            row = conn.execute(text("""
                INSERT INTO job_runs (job_name, started_at, status)
                VALUES (:name, NOW(), 'running')
                RETURNING id
            """), {"name": job_name}).fetchone()
            conn.commit()
            return row[0] if row else None
    except Exception:
        return None

def _log_job_finish(job_id: Optional[int], status: str = "ok", error: str = None,
                    rows_affected: int = None, started_at: datetime = None):
    """Mark a job run as finished."""
    if job_id is None:
        return
    try:
        duration = (datetime.utcnow() - started_at).total_seconds() if started_at else None
        with db_conn() as conn:
            conn.execute(text("""
                UPDATE job_runs SET finished_at = NOW(), status = :s, error = :e,
                    rows_affected = :r, duration_seconds = :d
                WHERE id = :id
            """), {"s": status, "e": error, "r": rows_affected, "d": duration, "id": job_id})
            conn.commit()
    except Exception:
        pass

def _scheduled_inc_update():
    """3 AM: Incremental OHLC update from EODHD bulk endpoint."""
    jid = _log_job_start("inc_update")
    started = datetime.utcnow()
    try:
        from eodhd_backfill import incremental_daily_update
        result = incremental_daily_update()
        _log_job_finish(jid, rows_affected=result.get("rows_inserted", 0), started_at=started)
        print(f"[IncUpdate] Complete: {result}")
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[IncUpdate] Failed: {e}")

def _scheduled_model_training():
    """7 AM: Build training matrix and fit model weights (after broad scan data available)."""
    jid = _log_job_start("model_training")
    started = datetime.utcnow()
    try:
        from model_training import daily_training_pipeline
        result = daily_training_pipeline()
        rows = result.get("matrix", {}).get("rows_inserted", 0)
        _log_job_finish(jid, rows_affected=rows, started_at=started)
        print(f"[ModelTrain] Complete: {result}")
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[ModelTrain] Failed: {e}")

def _scheduled_channel_calibration():
    """9 AM: Recalibrate per-channel hit rates from evaluated outcomes."""
    jid = _log_job_start("channel_calibration")
    started = datetime.utcnow()
    try:
        _update_channel_hit_rates()
        _log_job_finish(jid, started_at=started)
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[ChannelCal] Failed: {e}")

def _scheduled_monthly_fundamentals():
    """1st of each month at 4AM: Fetch fundamentals from yfinance for all ASX tickers."""
    jid = _log_job_start("monthly_fundamentals")
    started = datetime.utcnow()
    try:
        from fundamental_feeder import fetch_all_fundamentals
        result = fetch_all_fundamentals()
        # Also fetch historical financial statements (income/balance/cashflow)
        try:
            from fundamental_history_feeder import fetch_historical_fundamentals
            hist = fetch_historical_fundamentals()
            result["historical"] = hist
        except Exception as e:
            print(f"[FundFeeder] Historical fetch failed: {e}")
        _log_job_finish(jid, rows_affected=result.get("fetched", 0), started_at=started)
        print(f"[FundFeeder] Monthly fundamentals: {result}")
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[FundFeeder] Failed: {e}")


# ── SMSF v2 Pipeline Functions ─────────────────────────────────────────────

def _scheduled_smsf_pipeline():
    """7AM daily: run position sentinel + calendar gate check for all open positions."""
    jid = _log_job_start("smsf_pipeline")
    started = datetime.utcnow()
    alerts = []
    try:
        from position_sentinel import evaluate_position
        from calendar_gate import get_calendar_status
        from macro_rotation import classify_regime, build_telegram_regime_report

        users = _get_all_telegram_users()
        for user in users:
            uid = user["id"]
            paper = list_paper_trades(uid)
            open_positions = [p for p in paper if p.get("status") == "open"]
            if not open_positions:
                continue

            # Calendar gate
            wfo = get_current_wfo_state()
            cal = get_calendar_status(
                wfo_state=wfo["state"],
                axjo_vs_sma200_pct=_get_axjo_vs_sma200(),
                vix=_get_vix_level(),
            )
            if cal.get("stop_tighten_pct", 0) > 0:
                alerts.append(f"📅 Calendar: {cal['reason']} — tighten stops by {cal['stop_tighten_pct']}%")

            # Position sentinel
            macro_note = ""
            try:
                regime = classify_regime({})
                macro_note = regime.get("alert", "")
            except Exception:
                pass

            for pos in open_positions:
                try:
                    result = evaluate_position(
                        symbol=pos.get("symbol", ""),
                        sector=pos.get("sector", "Unknown"),
                        entry_date=pos.get("entry_date_parsed", date.today()),
                        entry_price=float(pos.get("entry_price", 0)),
                        current_price=float(pos.get("current_price", 0)),
                    )
                    if result.get("verdict") == "BROKEN":
                        alerts.append(f"🚨 SENTINEL BROKEN: {result['symbol']} — {result.get('reason','')[:100]}")
                        _auto_close_paper_trade(uid, result["symbol"], "AU",
                                                float(pos.get("current_price", 0)))
                    elif result.get("verdict") == "WEAKENED":
                        alerts.append(f"⚠️ SENTINEL WEAKENED: {result['symbol']} — {result.get('reason','')[:100]}")
                except Exception as e:
                    print(f"[SMSF] Sentinel failed for {pos.get('symbol')}: {e}")

        if alerts:
            _send_telegram_broadcast("\n".join(alerts), "smsf_sentinel")
        _log_job_finish(jid, rows_affected=len(alerts), started_at=started)
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[SMSF] Pipeline failed: {e}")


def _scheduled_sunday_rotation():
    """Sunday 6PM: Run macro rotation dashboard + Telegram regime report."""
    jid = _log_job_start("sunday_rotation")
    started = datetime.utcnow()
    try:
        from macro_rotation import classify_regime, build_telegram_regime_report
        from calendar_gate import get_calendar_status

        macro_data = {}
        try:
            macro_data = _fetch_macro_dashboard_data()
        except Exception:
            pass

        regime = classify_regime(macro_data)
        report = build_telegram_regime_report(regime)

        cal = get_calendar_status()
        report += f"\n\n📅 <b>Next Week Calendar Signal:</b> {cal.get('calendar_signal','NORMAL')} — {cal.get('reason','')}"

        _send_telegram_broadcast(report, "sunday_rotation")
        _log_job_finish(jid, started_at=started)
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[Rotation] Failed: {e}")


def _scheduled_smsf_announcement_check():
    """Every 60 min during market hours: check ASX announcements for open positions."""
    try:
        from announcement_monitor import check_all_open_positions, build_telegram_alert

        users = _get_all_telegram_users()
        for user in users:
            paper = list_paper_trades(user["id"])
            open_positions = [p for p in paper if p.get("status") == "open"]
            if not open_positions:
                continue
            alerts = check_all_open_positions(open_positions)
            critical = [a for a in alerts if a.get("status") == "CRITICAL_ANNOUNCEMENT"]
            if critical:
                msg = build_telegram_alert(alerts)
                if msg:
                    _send_telegram_broadcast(msg, "smsf_announcements")
                    for a in critical:
                        uid = a.get("user_id") or user.get("id")
                        _auto_close_paper_trade(uid, a["code"], "AU",
                                                float(a.get("current_price", 0)))
    except Exception as e:
        print(f"[AnnCheck] Failed: {e}")


def _scheduled_pipeline_health_report():
    """4:30 PM: One-line health check per pipeline stage. Telegram summary."""
    jid = _log_job_start("pipeline_health")
    started = datetime.utcnow()
    today = date.today()
    results = []

    def _ok(stage, detail=""): results.append(f"✅ {stage}" + (f" ({detail})" if detail else ""))
    def _warn(stage, detail=""): results.append(f"⚠️ {stage}" + (f" ({detail})" if detail else ""))
    def _fail(stage, detail=""): results.append(f"❌ {stage}" + (f" ({detail})" if detail else ""))

    try:
        with db_conn() as c:
            # 1. Data capture (inc_update)
            r = c.execute(text(
                "SELECT MAX(trade_date) FROM eod_ohl_history"
            )).fetchone()
            latest_ohlc = r[0] if r else None
            days_behind = (today - latest_ohlc).days if latest_ohlc and hasattr(latest_ohlc, 'date') else 99
            if days_behind <= 1:
                _ok("Data", f"latest {latest_ohlc}")
            elif days_behind <= 2:
                _warn("Data", f"{days_behind}d stale")
            else:
                _fail("Data", f"{days_behind}d behind")

            # 2. Broad scan
            r = c.execute(text(
                "SELECT MAX(generated_at) FROM wealth_scan_cache"
            )).fetchone()
            scan_ts = r[0] if r else None
            if scan_ts:
                scan_date = scan_ts.date() if hasattr(scan_ts, 'date') else scan_ts
                scan_age_d = (today - scan_date).days
                if scan_age_d <= 1:
                    _ok("Scan", f"scores fresh")
                elif scan_age_d <= 2:
                    _warn("Scan", f"{scan_age_d}d since last")
                else:
                    _fail("Scan", f"{scan_age_d}d stale")
            else:
                _fail("Scan", "never ran")

            # 3. Model training
            r = c.execute(text(
                "SELECT MAX(trained_at), ROUND(MAX(in_sample_hit_rate)::numeric,3) FROM model_weights_by_date WHERE model_type='logistic'"
            )).fetchone()
            train_ts, model_auc = (r[0], r[1]) if r else (None, None)
            if train_ts:
                train_date = train_ts.date() if hasattr(train_ts, 'date') else train_ts
                train_age_d = (today - train_date).days
                if train_age_d <= 1:
                    wc = c.execute(text(
                        "SELECT COUNT(*) FROM model_weights_by_date WHERE trained_at = (SELECT MAX(trained_at) FROM model_weights_by_date WHERE model_type='logistic') AND model_type='logistic' AND weight IS NOT NULL AND weight != 0"
                    )).fetchone()
                    auc_str = f", AUC={model_auc}" if model_auc is not None else ""
                    _ok("Model", f"{wc[0]} features{auc_str}")
                elif train_age_d <= 2:
                    _warn("Model", f"{train_age_d}d since last")
                else:
                    _fail("Model", f"{train_age_d}d stale")
            else:
                _fail("Model", "never trained")

            # 4. V2 AI scan
            r = c.execute(text(
                "SELECT MAX(run_date), SUM(ai_approved) FROM daily_ai_runs"
            )).fetchone()
            if r and r[0]:
                scan_d = r[0] if isinstance(r[0], date) else r[0].date() if hasattr(r[0], 'date') else None
                approved = r[1] or 0
                age_d = (today - scan_d).days if scan_d else 99
                if age_d <= 1:
                    _ok("AI Scan", f"approved={approved}")
                elif age_d <= 2:
                    _warn("AI Scan", f"{age_d}d stale")
                else:
                    _fail("AI Scan", f"{age_d}d since last, approved={approved}")
            else:
                _fail("AI Scan", "never ran")

            # 5. Paper trades
            r = c.execute(text(
                "SELECT COUNT(*) FILTER (WHERE status='open'), COUNT(*) FILTER (WHERE status='closed') FROM paper_trades"
            )).fetchone()
            open_n, closed_n = r if r else (0, 0)
            if open_n > 0:
                pnl_r = c.execute(text(
                    "SELECT ROUND(AVG((current_price/entry_price-1)*100)::numeric,1) FROM paper_trades WHERE status='open'"
                )).fetchone()
                avg_pnl = pnl_r[0] if pnl_r else 0
                emoji = "🟢" if (avg_pnl or 0) > 0 else "🔴"
                _ok("Positions", f"{open_n} open {emoji}{avg_pnl}%")
            else:
                _warn("Positions", "0 open")

            # 6. Scheduler
            r = c.execute(text("SELECT COUNT(*) FROM apscheduler_jobs")).fetchone()
            job_n = r[0] if r else 0
            if job_n >= 10:
                _ok("Scheduler", f"{job_n} jobs")
            elif job_n > 0:
                _warn("Scheduler", f"only {job_n} jobs")
            else:
                _fail("Scheduler", "no jobs")

            # 7. Kill switch state
            r = c.execute(text(
                "SELECT halt_all, active_conditions FROM kill_switch_state ORDER BY recorded_at DESC LIMIT 1"
            )).fetchone()
            if r and r[0]:
                _fail("Kill", f"ACTIVE: {r[1]}")
            else:
                _ok("Kill", "inactive")

            # 8. Stale heartbeat
            from stale_heartbeat import check_stale_sessions
            from config.universe import get_universe_symbols
            try:
                core_syms = get_universe_symbols("core")[:50]
                sh = check_stale_sessions(c, core_syms)
                if sh["freeze"]:
                    _fail("Heartbeat", f"{sh['stale_pct']}% stale")
                elif sh["stale_pct"] > 2:
                    _warn("Heartbeat", f"{sh['stale_pct']}%")
                else:
                    _ok("Heartbeat", "clean")
            except Exception:
                _warn("Heartbeat", "check failed")

            # 9. Pipeline activity check (failure alerting)
            r = c.execute(text(
                "SELECT COUNT(*) FROM job_execution_log WHERE started_at >= CURRENT_DATE"
            )).fetchone()
            jobs_today = r[0] if r else 0
            if jobs_today == 0:
                _fail("Pipeline", "no jobs ran today — system may be down")
            elif jobs_today < 3:
                _warn("Pipeline", f"only {jobs_today} jobs ran today")
            else:
                _ok("Pipeline", f"{jobs_today} jobs ran")

            # 10. Memory usage check
            try:
                import psutil
                mem = psutil.virtual_memory()
                if mem.percent > 85:
                    _fail("Memory", f"{mem.percent}% — risk of OOM")
                elif mem.percent > 75:
                    _warn("Memory", f"{mem.percent}%")
                else:
                    _ok("Memory", f"{mem.percent}%")
            except Exception:
                _warn("Memory", "unable to check")

        # Build Telegram message
        fail_count = sum(1 for r in results if r.startswith("❌"))
        warn_count = sum(1 for r in results if r.startswith("⚠️"))
        emoji = "🔴" if fail_count > 0 else ("🟡" if warn_count > 0 else "🟢")

        msg = f"<b>{emoji} Pipeline Health — {today.strftime('%a %b %d')}</b>\n"
        msg += "\n".join(results)
        if fail_count > 0:
            msg += f"\n\n<b>{fail_count} stage(s) failed</b> — check logs"
        elif warn_count > 0:
            msg += f"\n\n{warn_count} warning(s)"
        else:
            msg += "\n\nAll systems healthy"

        _send_telegram_broadcast(msg, "pipeline_health")
        _log_job_finish(jid, rows_affected=len(results), started_at=started)
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[PipelineHealth] Failed: {e}")


def _scheduled_smsf_eod_checks():
    """4:35 PM: CGT timer + circuit breaker check + EOD daily summary."""
    jid = _log_job_start("smsf_eod")
    started = datetime.utcnow()
    lines = ["📊 <b>SMSF End-of-Day Check</b>"]
    try:
        from tax_tracker import TaxTracker
        from circuit_breaker import DrawdownCircuitBreaker, get_peak_value, persist_peak_value

        users = _get_all_telegram_users()
        for user in users:
            uid = user["id"]
            paper = list_paper_trades(uid)
            closed = [p for p in paper if p.get("status") == "closed"]
            open_positions = [p for p in paper if p.get("status") == "open"]

            # Portfolio value
            cash = _estimate_cash(uid)
            satellite_value = sum(float(p.get("current_price", 0)) * float(p.get("quantity", 0))
                                  for p in open_positions)
            portfolio_value = cash + satellite_value
            persist_peak_value(portfolio_value)

            breaker = DrawdownCircuitBreaker(peak_value=get_peak_value())
            state = breaker.check(portfolio_value)
            lines.append(breaker.get_status_str(state))

            # CGT
            tracker = TaxTracker()
            for status in tracker.batch_status(open_positions):
                if status.get("alert"):
                    lines.append(f"  ⏰ {status['alert']}")

            # Positions summary
            lines.append(f"\n💼 {len(open_positions)} open | {len(closed)} closed (lifetime)")
            if open_positions:
                lines.append("Open:")
                for p in open_positions:
                    pnl = (float(p.get("current_price", 0)) / float(p.get("entry_price", 1)) - 1) * 100
                    lines.append(f"  {p['symbol']}: {pnl:+.1f}% ({p.get('days_held','?')}d)")

            _send_telegram_broadcast("\n".join(lines), "smsf_eod")

            # ── Model Health + Stale Heartbeat + Kill Switch (after broadcast) ─
            breaker_level_for_ks = "NORMAL"
            try:
                from circuit_breaker import DrawdownCircuitBreaker, get_peak_value
                b = DrawdownCircuitBreaker(peak_value=get_peak_value())
                bs = b.check(portfolio_value)
                breaker_level_for_ks = bs.get("level", "NORMAL")
            except Exception:
                pass

            model_freeze = False
            data_flag = False

            if _MODEL_HEALTH_AVAILABLE:
                try:
                    with db_conn() as mh_conn:
                        mh = evaluate_signal_outcomes(mh_conn)
                        persist_model_health(mh_conn, mh)
                        model_freeze = mh.get("freeze", False)
                        if mh.get("freeze") or mh.get("warning"):
                            _send_telegram_broadcast(
                                f"📊 <b>Model Health Alert</b>\n"
                                f"Hit rate: {mh['hit_rate']}% ({mh.get('wins',0)}/{mh.get('evaluated',0)})\n"
                                f"Status: {'🔴 FREEZE' if mh.get('freeze') else '🟡 WARNING halve size'}\n"
                                f"{mh['description']}",
                                "model_health"
                            )
                except Exception as e:
                    print(f"[SMSF EOD] Model health failed: {e}")

            if _STALE_HEARTBEAT_AVAILABLE:
                try:
                    from config.universe import get_universe_symbols
                    core_syms = get_universe_symbols("core")
                    with db_conn() as sh_conn:
                        sh = check_stale_sessions(sh_conn, core_syms[:50])
                        data_flag = sh.get("freeze", False)
                        if sh.get("freeze"):
                            _send_telegram_broadcast(
                                f"<b>⚠️ Stale Pipeline Alert</b>\n"
                                f"{sh['stale_count']}/{sh['total_checked']} symbols stale ({sh['stale_pct']}%)\n"
                                f"Action: FREEZE new entries",
                                "stale_heartbeat"
                            )
                except Exception as e:
                    print(f"[SMSF EOD] Stale heartbeat failed: {e}")

            if _KILL_SWITCH_AVAILABLE:
                try:
                    with db_conn() as ks_conn:
                        ks = compute_kill_state(breaker_level_for_ks, model_freeze, data_flag)
                        persist_kill_state(ks_conn, ks)
                        if ks["halt_all"]:
                            _send_telegram_broadcast(build_kill_switch_telegram(ks), "kill_switch")
                except Exception as e:
                    print(f"[SMSF EOD] Kill switch failed: {e}")

            _log_job_finish(jid, started_at=started)
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[SMSF EOD] Failed: {e}")


def _get_all_telegram_users():
    try:
        with db_conn() as conn:
            rows = conn.execute(text("SELECT DISTINCT user_id as id FROM user_telegram_recipients")).fetchall()
            return [{"id": r[0]} for r in rows] if rows else [{"id": "default"}]
    except Exception:
        return [{"id": "default"}]


def _estimate_cash(user_id: str) -> float:
    try:
        with db_conn() as conn:
            row = conn.execute(text(
                "SELECT COALESCE(total_investment_budget, 0) FROM users WHERE id = :uid"
            ), {"uid": user_id}).fetchone()
            return float(row[0]) if row else 200_000.0
    except Exception:
        return 200_000.0


def _get_axjo_vs_sma200() -> float:
    try:
        from eodhd_backfill import get_ohlc_for_symbol
        df = get_ohlc_for_symbol("XJO")
        if not df.empty and len(df) > 200:
            close = df["Close"].astype(float)
            sma200 = close.rolling(200).mean().iloc[-1]
            return (close.iloc[-1] / sma200 - 1) if sma200 > 0 else 0.0
    except Exception:
        pass
    return 0.0


def _get_vix_level() -> float:
    try:
        import yfinance as yf
        vix = yf.download("^VIX", period="5d", progress=False)
        if not vix.empty:
            return float(vix["Close"].iloc[-1])
    except Exception:
        pass
    return 18.0


def _fetch_macro_dashboard_data() -> dict:
    return {}


if SCHEDULER_AVAILABLE:

    try:
        # ── Scheduler persistence via SQLAlchemy ─────────────────────────────
        # Jobs survive Docker restarts by persisting state in PostgreSQL.
        # Falls back silently to in-memory if DB is unavailable at startup.
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.executors.pool import ThreadPoolExecutor as SchedulerExecutor
        from apscheduler.jobstores.memory import MemoryJobStore

        jobstores = {"default": MemoryJobStore()}
        try:
            jobstores["default"] = SQLAlchemyJobStore(
                url=DATABASE_URL,
                tablename="apscheduler_jobs",
                engine_options={"pool_size": 3, "max_overflow": 3},
            )
            print("[Scheduler] SQLAlchemyJobStore enabled — jobs persist across restarts.")
        except Exception as e:
            print(f"[Scheduler] SQLAlchemyJobStore unavailable ({e}) — falling back to memory.")

        scheduler = BackgroundScheduler(
            timezone=_get_scheduler_timezone(),
            jobstores=jobstores,
            executors={"default": SchedulerExecutor(max_workers=8)},
            job_defaults={"coalesce": True, "misfire_grace_time": 86400},
        )

        # Clear stale persisted jobs from DB (fresh registration on every start)
        try:
            with engine.connect() as c:
                c.execute(text("DELETE FROM apscheduler_jobs"))
                c.commit()
            print("[Scheduler] Cleared stale persisted jobs for fresh registration.", flush=True)
        except Exception as e:
            print(f"[Scheduler] Job cleanup failed (safe to ignore): {e}", flush=True)

        # ── SMSF v2 Core Pipeline ────────────────────────────────────────
        scheduler.add_job(
            _scheduled_inc_update, "cron",
            minute=0, hour=3, day_of_week="mon-fri",
            id="inc_update_3am", max_instances=1,
        )
        scheduler.add_job(
            _scheduled_broad_scan_precompute, "cron",
            minute=0, hour=5, day_of_week="mon-fri",
            id="broad_scan_5am", max_instances=1,
        )
        scheduler.add_job(
            _scheduled_smsf_pipeline, "cron",
            minute=0, hour=7, day_of_week="mon-fri",
            id="smsf_pipeline_7am", max_instances=1,
        )
        scheduler.add_job(
            _scheduled_sunday_rotation, "cron",
            day_of_week="sun", hour=18, minute=0,
            id="sunday_rotation", max_instances=1,
        )
        scheduler.add_job(
            _scheduled_smsf_announcement_check, "cron",
            minute="0", hour="10-15", day_of_week="mon-fri",
            id="smsf_ann_check", max_instances=1,
        )
        scheduler.add_job(
            _scheduled_pipeline_health_report, "cron",
            minute=30, hour=16, day_of_week="mon-fri",
            id="pipeline_health_1630", max_instances=1,
        )
        scheduler.add_job(
            _scheduled_smsf_eod_checks, "cron",
            minute=35, hour=16, day_of_week="mon-fri",
            id="smsf_eod_checks", max_instances=1,
        )

        # ── Paper Trade Monitoring ───────────────────────────────────────
        scheduler.add_job(
            _scheduled_paper_trade_monitor, "cron",
            minute="0,15,30,45", id="paper_trade_monitor",
        )

        # ── Essential Data Pipeline ──────────────────────────────────────
        scheduler.add_job(
            _scheduled_model_training, "cron",
            minute=0, hour=7, day_of_week="mon-fri",
            id="model_training_7am", max_instances=1,
        )
        scheduler.add_job(
            _scheduled_monthly_fundamentals, "cron",
            day="1", hour=4, minute=0,
            id="monthly_fundamentals", max_instances=1,
        )

        # ── Walk-Forward OOS Validation ──────────────────────────────────
        scheduler.add_job(
            _scheduled_v2_daily_scan, "cron",
            minute=0, hour=8, day_of_week="mon-fri",
            id="v2_daily_scan_8am", max_instances=1,
        )

        scheduler.add_job(
            _scheduled_walk_forward_oos, "cron",
            minute=15, hour=16, day_of_week="mon-fri",
            id="walk_forward_oos", max_instances=1,
        )

        scheduler.start()

        # ── Startup Catch-Up Engine ───────────────────────────────────────────
        # This system runs on a mini-PC that is powered off overnight and may
        # restart any time (7AM, 8AM, or after a full day offline).  APScheduler
        # cron jobs only fire at their scheduled wall-clock time — if the machine
        # was off at 5AM, the broad scan never ran.  The catch-up engine below
        # detects missed jobs using DB timestamps and re-runs them once on startup,
        # staggered so the CPU/network is not hammered all at once.
        def _startup_catchup():
            import time as _st
            tz = _get_scheduler_timezone()
            now_local = datetime.now(tz)
            now_utc   = datetime.utcnow()
            weekday   = now_local.weekday()   # 0=Mon … 6=Sun
            hour_local = now_local.hour
            tasks_run = []

            print(f"[StartupCatchup] Checking for missed jobs at {now_local.strftime('%a %H:%M %Z')} …", flush=True)

            # ── 1. Paper trade monitor — always run immediately on startup ────
            try:
                startup_max_trades = max(10, int(os.getenv("PAPER_MONITOR_STARTUP_MAX_TRADES", "75")))
                startup_catchup_enabled = os.getenv("PAPER_MONITOR_STARTUP_CATCHUP", "true").strip().lower() not in {"0", "false", "no"}
                if startup_catchup_enabled:
                    _scheduled_paper_trade_monitor(max_trades_override=startup_max_trades)
                    tasks_run.append("paper_trade_monitor")
            except Exception as _e:
                print(f"[StartupCatchup] paper_trade_monitor failed: {_e}")

            _st.sleep(5)  # Brief pause before heavy work

            # ── 1.5 Incremental EODHD update — run if stale (>18 hours no update) ──
            try:
                with db_conn() as _conn:
                    _row = _conn.execute(text(
                        "SELECT MAX(trade_date) FROM eod_ohl_history"
                    )).fetchone()
                inc_stale = True
                if _row and _row[0]:
                    _gen = _row[0] if isinstance(_row[0], datetime) else datetime.fromisoformat(str(_row[0]))
                    inc_age_h = (now_utc - _gen.replace(tzinfo=None)).total_seconds() / 3600
                    inc_stale = inc_age_h > 18
                if inc_stale:
                    print(f"[StartupCatchup] OHLC data may be stale — running incremental update …")
                    _scheduled_inc_update()
                    tasks_run.append("inc_update")
                else:
                    print(f"[StartupCatchup] OHLC data fresh — skipping incremental update.")
            except Exception as _e:
                print(f"[StartupCatchup] inc_update failed: {_e}")

            _st.sleep(5)

            # ── 2. Broad wealth scan — re-run if cache is stale (>20 hours) ──
            try:
                with db_conn() as _conn:
                    _row = _conn.execute(text(
                        "SELECT generated_at FROM wealth_scan_cache ORDER BY generated_at DESC LIMIT 1"
                    )).fetchone()
                scan_age_hours = 999
                if _row and _row[0]:
                    _gen = _row[0] if isinstance(_row[0], datetime) else datetime.fromisoformat(str(_row[0]))
                    scan_age_hours = (now_utc - _gen.replace(tzinfo=None)).total_seconds() / 3600
                if scan_age_hours > 20:
                    print(f"[StartupCatchup] Broad scan stale ({scan_age_hours:.1f}h old) — running now …")
                    _scheduled_broad_scan_precompute()
                    tasks_run.append("broad_scan")
                else:
                    print(f"[StartupCatchup] Broad scan fresh ({scan_age_hours:.1f}h old) — skipping.")
            except Exception as _e:
                print(f"[StartupCatchup] broad_scan check failed: {_e}")

            _st.sleep(10)

            # ── 3. (Deprecated daily_digest — replaced by 6AM AI pipeline) ───────
            # Old daily_digest catch-up removed. The AI pipeline handles this now.

            # ── 4. Walk-forward OOS — always run on startup (idempotent, skipped if already computed today) ──
            try:
                print(f"[StartupCatchup] Running walk-forward OOS validation …")
                _scheduled_walk_forward_oos()
                tasks_run.append("walk_forward_oos")
            except Exception as _e:
                print(f"[StartupCatchup] WFO failed: {_e}")

            # ── 5. UAT health report — runs after WFO on boot ──────────────────
            try:
                print(f"[StartupCatchup] Generating UAT health report …")
                _scheduled_uat_health_report()
                tasks_run.append("uat_health_report")
            except Exception as _e:
                print(f"[StartupCatchup] UAT health report failed: {_e}")

            _st.sleep(10)

            # ── 4. Weekly generation — re-run if this week's picks are missing ─
            # Only on weekdays, and only if weekly picks table is stale.
            try:
                if weekday < 5:  # Mon–Fri only
                    with db_conn() as _conn:
                        _week_row = _conn.execute(text("""
                            SELECT MAX(generated_at) FROM weekly_digests
                        """)).fetchone()
                    weekly_age_days = 999
                    if _week_row and _week_row[0]:
                        _wgen = _week_row[0] if isinstance(_week_row[0], datetime) else datetime.fromisoformat(str(_week_row[0]))
                        weekly_age_days = (now_utc - _wgen.replace(tzinfo=None)).total_seconds() / 86400
                    if weekly_age_days > 7:
                        print(f"[StartupCatchup] Weekly picks stale ({weekly_age_days:.1f}d old) — running …")
                        _scheduled_weekly_generation()
                        tasks_run.append("weekly_generation")
                    else:
                        print(f"[StartupCatchup] Weekly picks fresh ({weekly_age_days:.1f}d old) — skipping.")
            except Exception as _e:
                print(f"[StartupCatchup] weekly_generation check failed: {_e}")

            _st.sleep(10)

            # ── 5. Positions monitor — run if we're in market hours ───────────
            try:
                from datetime import time as _dt_time
                market_open  = _dt_time(10, 0)
                market_close = _dt_time(16, 0)
                is_weekday   = weekday < 5
                is_market_hours = market_open <= now_local.time() <= market_close
                if is_weekday and is_market_hours:
                    print("[StartupCatchup] In market hours — running positions monitor …")
                    _scheduled_positions_monitor()
                    tasks_run.append("positions_monitor")
                else:
                    print(f"[StartupCatchup] Outside market hours or weekend — skipping positions monitor.")
            except Exception as _e:
                print(f"[StartupCatchup] positions_monitor failed: {_e}")

            _st.sleep(10)

            # ── 6. Self Learning Loop ─────────────────────────────────────────
            # Runs at the very end of startup catchup since it's lower priority
            try:
                with db_conn() as _conn:
                    _row = _conn.execute(text(
                        "SELECT evaluated_at FROM ai_self_learning_metrics ORDER BY evaluated_at DESC LIMIT 1"
                    )).fetchone()
                
                learning_age_hours = 999
                if _row and _row[0]:
                    _eval_ts = _row[0] if isinstance(_row[0], datetime) else datetime.fromisoformat(str(_row[0]))
                    learning_age_hours = (now_utc - _eval_ts.replace(tzinfo=None)).total_seconds() / 3600
                
                # If hasn't run in over 6 days (144 hours), run it now to catch up
                if learning_age_hours > 144:
                    print(f"[StartupCatchup] Self-learning loop stale ({learning_age_hours:.1f}h old) — running now (lowest priority) ...")
                    _scheduled_self_learning_loop()
                    tasks_run.append("self_learning")
                else:
                    print(f"[StartupCatchup] Self-learning fresh ({learning_age_hours:.1f}h old) — skipping.")
            except Exception as _e:
                print(f"[StartupCatchup] self_learning_loop check failed: {_e}")

            _st.sleep(5)

            # ── 7. Incremental OHLC update — run if stale (>24 hours) ───────────
            try:
                with db_conn() as _conn:
                    _row = _conn.execute(text(
                        "SELECT MAX(trade_date) FROM eod_ohl_history"
                    )).fetchone()
                ohlc_age_hours = 999
                if _row and _row[0]:
                    _max_dt = _row[0] if isinstance(_row[0], datetime) else datetime.strptime(str(_row[0]), "%Y-%m-%d")
                    ohlc_age_hours = (now_utc - _max_dt.replace(tzinfo=None)).total_seconds() / 3600 if hasattr(_max_dt, 'replace') else 999
                if ohlc_age_hours > 24:
                    print(f"[StartupCatchup] OHLC stale ({ohlc_age_hours:.1f}h) — running inc update …")
                    _scheduled_inc_update()
                    tasks_run.append("inc_update")
                else:
                    print(f"[StartupCatchup] OHLC fresh — skipping inc update.")
            except Exception as _e:
                print(f"[StartupCatchup] inc_update failed: {_e}")

            _st.sleep(5)

            # ── 8. Model training — run if not trained today ────────────────────
            try:
                with db_conn() as _conn:
                    _row = _conn.execute(text(
                        "SELECT MAX(trained_at) FROM model_weights_by_date"
                    )).fetchone()
                train_today = False
                if _row and _row[0]:
                    _td = _row[0] if isinstance(_row[0], datetime) else datetime.fromisoformat(str(_row[0]))
                    _td_local = _td.replace(tzinfo=timezone.utc).astimezone(tz) if _td.tzinfo is None else _td.astimezone(tz)
                    train_today = _td_local.date() == now_local.date()
                if not train_today:
                    print(f"[StartupCatchup] Model not trained today — running training pipeline …")
                    _scheduled_model_training()
                    tasks_run.append("model_training")
                else:
                    print(f"[StartupCatchup] Model already trained today — skipping.")
            except Exception as _e:
                print(f"[StartupCatchup] model_training failed: {_e}")

            _st.sleep(5)

            # ── 8.5 V2 Daily Scan ────────────────────────────────────────────────
            try:
                if now_local.hour >= 8:
                    with db_conn() as _conn:
                        _scanned = _conn.execute(text(
                            "SELECT COUNT(*) FROM daily_ai_runs WHERE run_date = :today"
                        ), {"today": now_local.date()}).fetchone()
                    already_scanned = _scanned and _scanned[0] > 0
                    if already_scanned:
                        print(f"[StartupCatchup] V2 daily scan already ran today — skipping.")
                    else:
                        print(f"[StartupCatchup] Running V2 daily scan...")
                        _scheduled_v2_daily_scan()
                        tasks_run.append("v2_daily_scan")
            except Exception as _e:
                print(f"[StartupCatchup] v2_daily_scan failed: {_e}")

            # ── 9. Channel calibration — run if not calibrated today ────────────
            try:
                with db_conn() as _conn:
                    _row = _conn.execute(text(
                        "SELECT MAX(calibrated_at) FROM channel_hit_rates"
                    )).fetchone()
                cal_today = False
                if _row and _row[0]:
                    _cd = _row[0] if isinstance(_row[0], datetime) else datetime.fromisoformat(str(_row[0]))
                    _cd_local = _cd.replace(tzinfo=timezone.utc).astimezone(tz) if _cd.tzinfo is None else _cd.astimezone(tz)
                    cal_today = _cd_local.date() == now_local.date()
                if not cal_today:
                    print(f"[StartupCatchup] Channel calibration not run today — running …")
                    _scheduled_channel_calibration()
                    tasks_run.append("channel_calibration")
                else:
                    print(f"[StartupCatchup] Channel calibration already done today.")
            except Exception as _e:
                print(f"[StartupCatchup] channel_calibration failed: {_e}")

            print(f"[StartupCatchup] Done. Ran: {tasks_run or ['none needed']}", flush=True)

            # ── Pipeline health report — always at end of catchup ────────
            try:
                _scheduled_pipeline_health_report()
                tasks_run.append("pipeline_health")
            except Exception as _e:
                print(f"[StartupCatchup] pipeline_health failed: {_e}")

        # Run catch-up in a background thread so it doesn't block FastAPI startup
        def _catchup_wrapper():
            import sys
            print("[StartupCatchup] Background catchup thread starting...", flush=True, file=sys.stderr)
            try:
                _startup_catchup()
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[StartupCatchup] CRITICAL: {e}", flush=True, file=sys.stderr)
        _catchup_thread = threading.Thread(target=_catchup_wrapper, daemon=True, name="startup-catchup")
        _catchup_thread.start()
        print("[StartupCatchup] Background catchup thread launched.", flush=True)

    except Exception as e:
        import traceback
        print(f"[SCHEDULER] CRITICAL: Scheduler initialization failed: {e}", flush=True, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        SCHEDULER_AVAILABLE = False


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


"""ASX Stock Predictor Backend.

FastAPI + SQLAlchemy data layer + dual LLM providers (Local/OpenAI).
"""

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import numpy as np
from openai import OpenAI
import pandas as pd
from pydantic import BaseModel
import jwt
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
import yfinance as yf

try:
    from pypfopt import EfficientFrontier, risk_models, expected_returns
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

# Initialise FRED client if key is available
_fred_client = None
if FREDAPI_AVAILABLE and FRED_API_KEY:
    try:
        _fred_client = Fred(api_key=FRED_API_KEY)
    except Exception:
        _fred_client = None


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
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen2.5-7b-instruct")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
LLM_PROVIDER_ORDER = [
    provider.strip().lower()
    for provider in os.getenv("LLM_PROVIDER_ORDER", "local,openai").split(",")
    if provider.strip()
]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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

init_db()

# Data models
class Share(BaseModel):
    symbol: str
    name: str
    current_price: float
    change_percent: float
    prediction_3m: Optional[dict] = None
    weekly_data: List[dict] = []

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


security = HTTPBearer()

# ASX company data cache
ASX_COMPANIES = {
    "BHP": "BHP Group Limited",
    "CBA": "Commonwealth Bank of Australia",
    "ANZ": "ANZ Banking Group Limited",
    "WOW": "Woolworths Group Limited",
    "TLS": "Telstra Corporation Limited",
    "WBC": "Westpac Banking Corporation",
    "NAB": "National Australia Bank Limited",
    "CSL": "CSL Limited",
    "RIO": "Rio Tinto Limited",
    "FMG": "Fortescue Metals Group Ltd",
    "AMD": "Aristocrat Leisure Limited",
    "QBE": "QBE Insurance Group Limited",
    "S32": "South32 Limited",
    "MIN": "Mineral Resources Limited",
    "AIA": "Auckland International Airport",
    "AZJ": "Aurizon Holdings Limited",
    "BXB": "Brambles Limited",
    "CIA": "Champion Iron Limited",
    "CWY": "Cleanaway Waste Management",
    "DLX": "DuluxGroup Limited",
    "HVN": "Harvey Norman Holdings",
    "IPL": "Incitec Pivot Limited",
    "JHX": "James Hardie Industries",
    "LLC": "Lottery Corporation Limited",
    "MEZ": "Meridian Energy Limited",
    "NCM": "Newcrest Mining Limited",
    "NEC": "Nine Entertainment Co.",
    "OML": "Ooh!Media Limited",
    "ORE": "Orocobre Limited",
    "ORA": "Orora Limited",
    "OSH": "Oil Search Limited",
    "PMV": "Premier Investments Limited",
    "PPT": "Perpetual Limited",
    "QAN": "Qantas Airways Limited",
    "REA": "REA Group Ltd",
    "RHC": "Ramsay Health Care",
    "SCG": "Scentre Group Limited",
    "SEK": "Seek Limited",
    "SHL": "Sonic Healthcare Limited",
    "SOL": "Soul Pattinson (W.H) Ltd",
    "SPK": "Spark New Zealand",
    "STO": "Santos Limited",
    "SUL": "Super Retail Group Ltd",
    "TGP": "Travel360 Group Limited",
    "TLC": "The Lottery Corporation",
    "TMG": "Trigg Mining Ltd",
    "VOC": "Vocus Group Limited",
    "WDS": "Woodside Energy Group",
    "WES": "Wesfarmers Limited",
    "ORG": "Origin Energy Limited",
    "DRO": "DroneShield Limited",
    "EOS": "Electro Optic Systems Holdings",
    "ASB": "Austal Limited",
}

TOP_ASX200_SYMBOLS = [
    "BHP", "CBA", "CSL", "WBC", "NAB", "ANZ", "WES", "WOW", "TLS", "RIO"
]

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
    "VODAFONE.NS": "Vodafone Idea",
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
    "PIDILITIND.NS": "Pidilite Industries",
    "SRF.NS": "SRF Limited",
    "DEEPAKNTR.NS": "Deepak Nitrite",
    "AAVAS.NS": "Aavas Financiers",
    # Small/Mid that user mentioned
    "PRAJIND.NS": "Praj Industries",
    "GLENMARK.NS": "Glenmark Pharmaceuticals",
    "ESCORTS.NS": "Escorts Kubota",
    "SONACOMS.NS": "Sona BLW Precision",
    "CAMPUS.NS": "Campus Activewear",
    "RAILVIKAS.NS": "Rail Vikas Nigam",
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
    "PIDILITIND.NS": "Pidilite Industries",
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
    Priority: explicit US list → explicit AU list → default to IN (NSE).
    Any symbol not known to be AU or US is treated as an NSE stock.
    """
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    if s in US_COMPANIES:
        return "US"
    if s in ASX_COMPANIES:
        return "AU"
    if s in _IN_SYMBOLS:
        return "IN"
    # Unknown symbol — default to NSE (India) rather than ASX
    return "IN"


def format_ticker(symbol: str, market: str = "AU") -> str:
    """Return the correct yfinance ticker for a symbol in a given market."""
    market = (market or "AU").upper()
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
    """Fetch the latest price and change for a symbol across any market."""
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    if market is None:
        market = detect_market(s)
    ticker_str = format_ticker(s, market)
    stock = yf.Ticker(ticker_str)
    try:
        hist = stock.history(period="5d")
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
    return {
        "current_price": current_price,
        "change_percent": change,
        "name": ALL_COMPANIES.get(s, s),
    }


def get_valuation_metrics(symbol: str) -> dict:
    """Fetch valuation metrics from yfinance"""
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    market = detect_market(s)
    stock = yf.Ticker(format_ticker(s, market))
    
    try:
        info = stock.info
    except:
        info = {}
    
    # Handle dividend yield (comes as decimal like 0.042, convert to %)
    div_yield = info.get('dividendYield')
    if div_yield is not None:
        div_yield = float(div_yield) * 100 if div_yield < 1 else float(div_yield)
    
    # Market cap (convert to billions if available)
    market_cap = info.get('marketCap')
    if market_cap is not None:
        market_cap = float(market_cap)
    
    return {
        "pe": info.get('trailingPE'),
        "forward_pe": info.get('forwardPE'),
        "peg": info.get('pegRatio'),
        "pb": info.get('priceToBook'),
        "dividend_yield": div_yield,
        "market_cap": market_cap,
        "sector": info.get('sector'),
        "industry": info.get('industry'),
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
            text("SELECT id, email, full_name, preferred_market FROM users WHERE id = :id"),
            {"id": user_id},
        ).fetchone()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return {
        "id": user[0],
        "email": user[1],
        "full_name": user[2] or "",
        "preferred_market": user[3] or "AU",
    }

def get_historical_data(symbol: str, period: str = "1y", market: str = None) -> pd.DataFrame:
    """Get historical price data for any market."""
    s = symbol.upper().replace(".AX", "").replace(".NS", "")
    if market is None:
        market = detect_market(s)
    stock = yf.Ticker(format_ticker(s, market))
    return stock.history(period=period)

def calculate_technical_indicators(df: pd.DataFrame) -> dict:
    """Calculate technical indicators"""
    indicators = {}
    
    if len(df) < 50:
        return indicators
    
    # Moving averages
    indicators['sma_20'] = float(df['Close'].rolling(20).mean().iloc[-1])
    indicators['sma_50'] = float(df['Close'].rolling(50).mean().iloc[-1])
    indicators['sma_200'] = float(df['Close'].rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
    
    # Volatility
    indicators['volatility'] = float(df['Close'].pct_change().std() * np.sqrt(252))
    
    # RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    indicators['rsi'] = float(100 - (100 / (1 + rs)).iloc[-1])
    
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
    indicators['bb_upper'] = float((sma + (std * 2)).iloc[-1])
    indicators['bb_lower'] = float((sma - (std * 2)).iloc[-1])
    
    # Price momentum
    indicators['momentum_20'] = float((df['Close'].iloc[-1] - df['Close'].iloc[-20]) / df['Close'].iloc[-20] * 100)
    
    return indicators


def std_norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def get_probability_and_score(symbol: str) -> dict:
    stock_data = get_stock_data(symbol)
    hist = get_historical_data(symbol, period="1y")
    if len(hist) < 120:
        raise HTTPException(status_code=404, detail=f"Not enough data for {symbol}")

    current_price = stock_data["current_price"] or float(hist["Close"].iloc[-1])
    indicators = calculate_technical_indicators(hist)
    prediction = generate_statistical_prediction(hist, current_price)

    mu = prediction["change_from_current"] / 100.0
    daily_vol = float(hist["Close"].pct_change().dropna().std())
    sigma_63 = max(daily_vol * math.sqrt(63), 1e-6)
    z = (0.05 - mu) / sigma_63
    prob_ge_5pct = max(0.0, min(1.0, 1.0 - std_norm_cdf(z)))

    trend = prediction["trend"]
    trend_score = 0.9 if trend == "bullish" else 0.55 if trend == "neutral" else 0.2

    rsi = indicators.get("rsi", 50)
    momentum = indicators.get("momentum_20", 0)
    quality_score = max(0.0, min(1.0, (1 - abs(rsi - 55) / 55) * 0.6 + (0.5 + momentum / 40) * 0.4))

    regime_fit = 0.65 if trend == "bullish" else 0.45 if trend == "neutral" else 0.3

    avg_vol = float(hist["Volume"].tail(20).mean()) if "Volume" in hist else 0
    liquidity_score = max(0.1, min(1.0, avg_vol / 8_000_000))

    score = (
        0.45 * prob_ge_5pct
        + 0.20 * trend_score
        + 0.15 * quality_score
        + 0.10 * regime_fit
        + 0.10 * liquidity_score
    )

    return {
        "symbol": symbol,
        "name": stock_data["name"],
        "current_price": round(float(current_price), 2),
        "predicted_price_3m": prediction["predicted_price"],
        "expected_return_3m_pct": round(prediction["change_from_current"], 2),
        "prob_ge_5pct": round(prob_ge_5pct * 100, 2),
        "trend": trend,
        "score": round(score * 100, 2),
    }


def create_tracking_window(user_id: str, symbol: str, source: str = "manual_add") -> None:
    stock_data = get_stock_data(symbol)
    hist = get_historical_data(symbol, period="1y")
    if len(hist) == 0:
        return

    entry_price = stock_data["current_price"] or float(hist["Close"].iloc[-1])
    prediction = generate_statistical_prediction(hist, entry_price)
    target_return_14d = (prediction["change_from_current"] / 100.0) / 4.5
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
                "end_date": start_date + timedelta(days=14),
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
        "financial": ["CBA", "NAB", "WBC", "ANZ", "QBE"],
        "mining": ["BHP", "RIO", "FMG", "S32", "MIN"],
        "miner": ["BHP", "RIO", "FMG", "S32", "MIN"],
        "energy": ["WDS", "STO", "OSH"],
        "oil": ["WDS", "STO", "ORG"],
        "gas": ["WDS", "STO", "ORG"],
        "retail": ["WOW", "WES", "HVN", "PMV"],
        "health": ["CSL", "RHC", "SHL"],
        "tech": ["REA", "SEK", "XRO"],
        "telecom": ["TLS", "VOC", "SPK"],
        "defensive": ["TLS", "WOW", "WES", "CSL"],
        "defence": ["DRO", "EOS", "ASB"],
        "defense": ["DRO", "EOS", "ASB"],
        "aerospace": ["ASB", "DRO", "EOS"],
        "military": ["DRO", "EOS", "ASB"],
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

    for symbol in TOP_ASX200_SYMBOLS:
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
        seeds.extend(["WDS", "STO", "ORG"])

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

    provider_errors = {"local": "", "openai": ""}

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
                        "max_tokens": 220,
                        "temperature": 0.1,
                    },
                    timeout=45,
                )
                if response.status_code != 200:
                    continue
                content = response.json()["choices"][0]["message"]["content"]
                symbols = extract_json_symbols(content)
                if not symbols:
                    symbols = parse_candidate_symbols(content, max_symbols=max_symbols)
                if symbols:
                    break
            valid = [s for s in symbols if s in ASX_COMPANIES]
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
                max_tokens=220,
                temperature=0.1,
            )
            content = response.choices[0].message.content or "{}"
            symbols = extract_json_symbols(content)
            if not symbols:
                symbols = parse_candidate_symbols(content, max_symbols=max_symbols)
            valid = [s for s in symbols if s in ASX_COMPANIES]
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

    for provider in provider_order:
        if provider == "local":
            result = try_local_provider()
            if result:
                return result, "local", ""
        if provider == "openai":
            result = try_openai_provider()
            if result:
                return result, "openai", ""

    if strict_provider:
        return [], provider_preference, provider_errors.get(provider_preference, "")

    return heuristic_symbols_from_query(query, max_symbols=max_symbols), "fallback", ""

def generate_statistical_prediction(df: pd.DataFrame, current_price: float) -> dict:
    """Generate statistical prediction using multiple methods"""
    
    # Method 1: Linear trend projection
    X = np.arange(len(df)).reshape(-1, 1)
    y = df['Close'].values
    
    from sklearn.linear_model import LinearRegression
    lr = LinearRegression()
    lr.fit(X, y)
    
    # Project 90 days (approx 3 months trading days)
    future_days = 90
    future_X = np.arange(len(df), len(df) + future_days).reshape(-1, 1)
    lr_pred = lr.predict(future_X)[-1]
    
    # Method 2: Simple moving average projection
    sma_50 = df['Close'].rolling(50).mean().iloc[-1]
    sma_200 = df['Close'].rolling(200).mean().iloc[-1] if len(df) >= 200 else sma_50
    
    # Method 3: Weighted average (favor recent data)
    weights = np.linspace(1, 2, min(50, len(df)))
    weighted_avg = np.average(df['Close'].tail(50), weights=weights)
    
    # Combine methods
    combined_pred = (lr_pred * 0.4 + sma_50 * 0.3 + weighted_avg * 0.3)
    
    # Calculate confidence interval based on volatility
    volatility = df['Close'].pct_change().std()
    confidence_margin = combined_pred * volatility * 2
    
    # Determine trend
    if combined_pred > current_price * 1.05:
        trend = "bullish"
    elif combined_pred < current_price * 0.95:
        trend = "bearish"
    else:
        trend = "neutral"
    
    return {
        "predicted_price": round(combined_pred, 2),
        "confidence_low": round(combined_pred - confidence_margin, 2),
        "confidence_high": round(combined_pred + confidence_margin, 2),
        "trend": trend,
        "change_from_current": round(((combined_pred - current_price) / current_price * 100), 2)
    }

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
    """Get LLM analysis from configured providers with comprehensive context."""
    
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
    
    prompt = f"""You are a financial data analyst. 
You do not give financial advice. 
You only analyse data, identify patterns, compare companies, and explain market behaviour.

Stock: {symbol}
Sector: {valuation.get('sector', 'N/A')}
Industry: {valuation.get('industry', 'N/A')}

Current Price: ${stock_data['current_price']:.2f}
Daily Change: {stock_data['change_percent']:.2f}%

Technical Indicators:
- SMA 20: ${indicators.get('sma_20', 0):.2f}
- SMA 50: ${indicators.get('sma_50', 0):.2f}
- SMA 200: ${indicators.get('sma_200', 0):.2f}
- RSI (14): {indicators.get('rsi', 0):.1f}
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

Risk Metrics:
- Beta (vs ASX200): {risk.get('beta', 'N/A')}
- Max Drawdown (90d): {risk.get('max_drawdown_90d', 'N/A')}%
- Sharpe Ratio (90d): {risk.get('sharpe_90d', 'N/A')}

Forecast (90 days):
- Predicted Price: ${prediction['predicted_price']:.2f}
- Range: ${prediction['confidence_low']:.2f} - ${prediction['confidence_high']:.2f}
- Trend: {prediction['trend'].upper()}
- Expected Change: {prediction['change_from_current']:.1f}%

Model Confidence:
- Probability of ≥5%: {prediction.get('prob_ge_5pct', 'N/A')}%
- Composite Score: {prediction.get('score', 'N/A')}

Market Regime:
- Regime: {regime.get('name', 'N/A')}
- Confidence: {regime.get('confidence', 'N/A')}%

Historical Forecast Accuracy:
- Directional Accuracy: {historical_accuracy.get('directional_accuracy', 'N/A')}%
- Average Error: {historical_accuracy.get('avg_error', 'N/A')}%

Task:
Explain the stock's outlook based on the above data.
Identify key drivers, risks, sector context, and how the forecast aligns with the indicators.
Do not give buy/sell recommendations."""
    
    def try_local_provider() -> Optional[str]:
        try:
            response = requests.post(
                f"{LOCAL_LLM_URL}/chat/completions",
                json={
                    "model": LOCAL_LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a professional stock analyst providing investment insights."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 200,
                    "temperature": 0.7
                },
                timeout=60
            )
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            return None
        except Exception:
            return None

    def try_openai_provider() -> Optional[str]:
        if not openai_client:
            return None
        try:
            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a professional stock analyst providing investment insights."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception:
            return None

    for provider in LLM_PROVIDER_ORDER:
        if provider == "local":
            content = try_local_provider()
            if content:
                return content
        if provider == "openai":
            content = try_openai_provider()
            if content:
                return content
    
    # Return mock analysis if no LLM available
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
    hist = yf.Ticker(ticker).history(period="5d")
    if len(hist) < 2:
        return 0.0
    prev_close = float(hist["Close"].iloc[-2])
    last_close = float(hist["Close"].iloc[-1])
    if prev_close == 0:
        return 0.0
    return ((last_close - prev_close) / prev_close) * 100.0


def compute_regime_snapshot() -> dict:
    asx200_ret = get_daily_return("^AXJO")
    sp500_ret = get_daily_return("^GSPC")
    gold_ret = get_daily_return("GC=F")
    dxy_ret = get_daily_return("DX-Y.NYB")

    equities_ret = asx200_ret
    safe_haven_flag = equities_ret < 0 and gold_ret > 0
    usd_headwind_flag = dxy_ret > 0.4
    divergence_flag = equities_ret > 0 and gold_ret > 0

    if safe_haven_flag:
        regime = "risk_off"
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

    return {
        "asx200_ret": round(asx200_ret, 2),
        "sp500_ret": round(sp500_ret, 2),
        "gold_ret": round(gold_ret, 2),
        "dxy_ret": round(dxy_ret, 2),
        "regime": regime,
        "confidence": round(confidence, 2),
        "safe_haven_flag": safe_haven_flag,
        "usd_headwind_flag": usd_headwind_flag,
        "divergence_flag": divergence_flag,
        "tags": tags,
    }


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

    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "database_url": DATABASE_URL.split("@")[-1],
        "llm_provider_order": LLM_PROVIDER_ORDER,
        "local_llm_configured": bool(LOCAL_LLM_URL),
        "openai_configured": bool(openai_client),
        "openai_model": OPENAI_MODEL,
        "auth_enabled": True,
    }


@app.get("/api/v1/market/pulse")
async def market_pulse(current_user: dict = Depends(get_current_user)):
    del current_user
    snapshot = compute_regime_snapshot()
    macro = get_macro_indicators()
    return {
        "metrics": {
            "asx200_ret": snapshot["asx200_ret"],
            "sp500_ret": snapshot["sp500_ret"],
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
async def register(payload: RegisterRequest):
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
async def login(payload: LoginRequest):
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
    
    return {
        "symbol": symbol,
        "name": stock_data["name"],
        "current_price": stock_data["current_price"],
        "change_percent": round(stock_data["change_percent"], 2),
        "technical_indicators": indicators,
        "valuation_metrics": valuation,
        "risk_metrics": risk,
        "prediction_3m": prediction,
        "weekly_data": weekly_data
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


@app.get("/api/ai/analyze/{symbol}")
async def analyze_share(symbol: str, current_user: dict = Depends(get_current_user)):
    """Analyze a single share without requiring it to be tracked"""
    symbol = symbol.upper()
    
    # Get stock data
    stock_data = get_stock_data(symbol)
    
    # Get historical data
    hist = get_historical_data(symbol)
    
    if len(hist) == 0:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")
    
    # Calculate technical indicators
    indicators = calculate_technical_indicators(hist)
    
    # Generate prediction using multiple methods
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
    
    # Get weekly data
    weekly_data = get_weekly_data(symbol)
    
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


@app.get("/api/universe/top")
async def get_top_universe(current_user: dict = Depends(get_current_user)):
    del current_user
    items = []
    for symbol in TOP_ASX200_SYMBOLS:
        stock_data = get_stock_data(symbol)
        items.append(
            {
                "symbol": symbol,
                "name": stock_data["name"],
                "current_price": round(stock_data["current_price"], 2),
                "change_percent": round(stock_data["change_percent"], 2),
            }
        )
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
        "items": [{"symbol": symbol, "name": ASX_COMPANIES[symbol]} for symbol in symbols],
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
                    "max_tokens": 500,
                    "temperature": 0.7
                },
                timeout=60
            )
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
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
                max_tokens=500,
                temperature=0.7
            )
            return response.choices[0].message.content
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


@app.post("/api/ai/sentiment")
async def get_sentiment_analysis(payload: SentimentRequest, current_user: dict = Depends(get_current_user)):
    del current_user
    symbol = payload.symbol.upper().strip()
    
    # Get stock news from yfinance
    ticker = yf.Ticker(f"{symbol}.AX")
    news_items = []
    
    try:
        news = ticker.news
        if news:
            for item in news[:10]:  # Get up to 10 news items
                news_items.append({
                    "title": item.get("title", ""),
                    "publisher": item.get("publisher", ""),
                    "link": item.get("link", "")
                })
    except:
        pass
    
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
                    "max_tokens": 300,
                    "temperature": 0.3
                },
                timeout=60
            )
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
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
                    import json
                    sentiment_result = json.loads(result)
                except:
                    sentiment_result = {"sentiment": "neutral", "score": 0.0, "themes": [], "summary": result[:200]}
                break
        if provider == "openai":
            result = try_openai()
            if result:
                try:
                    import json
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
        if symbol_clean not in ASX_COMPANIES:
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
                "current_day": min(days_elapsed, 14),
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
                      "max_tokens": 600, "temperature": 0.2},
                timeout=90,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
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
        start = raw.find("{")
        end = raw.rfind("}") + 1
        result = json.loads(raw[start:end]) if start >= 0 else {}
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
                          "max_tokens": 400, "temperature": 0.2},
                    timeout=60,
                )
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
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
                          "max_tokens": 400, "temperature": 0.2},
                    timeout=60,
                )
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

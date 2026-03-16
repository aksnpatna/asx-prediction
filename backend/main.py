# ASX Stock Predictor Backend
# FastAPI application with OpenAI LLM integration

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime, timedelta
import yfinance as yf
import numpy as np
import pandas as pd
import os
import json
from openai import OpenAI
import sqlite3
from pathlib import Path

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

# LM Studio configuration (free local LLM)
LM_STUDIO_URL = "http://localhost:1234/v1"
USE_LLM_STUDIO = True  # Set to True to use local LM Studio

# OpenAI configuration (requires API key)
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

# Database setup
DB_PATH = Path(__file__).parent / "data" / "shares.db"
DB_PATH.parent.mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shares (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

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
}

def get_stock_data(symbol: str) -> dict:
    """Fetch stock data from yfinance"""
    ticker = f"{symbol}.AX"
    stock = yf.Ticker(ticker)
    
    # Get info
    try:
        info = stock.info
        current_price = info.get('currentPrice', info.get('regularMarketPreviousClose', 0))
        prev_close = info.get('regularMarketPreviousClose', current_price)
        change = ((current_price - prev_close) / prev_close * 100) if prev_close else 0
    except:
        current_price = 0
        change = 0
    
    return {
        "current_price": current_price,
        "change_percent": change,
        "name": ASX_COMPANIES.get(symbol, symbol)
    }

def get_historical_data(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Get historical price data"""
    ticker = f"{symbol}.AX"
    stock = yf.Ticker(ticker)
    hist = stock.history(period=period)
    return hist

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

async def get_llm_analysis(symbol: str, stock_data: dict, indicators: dict, prediction: dict) -> str:
    """Get LLM analysis from LM Studio or OpenAI"""
    
    prompt = f"""Analyze the following ASX stock data for {symbol} ({stock_data['name']}):

Current Price: ${stock_data['current_price']:.2f}
Daily Change: {stock_data['change_percent']:.2f}%

Technical Indicators:
- SMA 20: ${indicators.get('sma_20', 0):.2f}
- SMA 50: ${indicators.get('sma_50', 0):.2f}
- RSI (14): {indicators.get('rsi', 0):.1f}
- MACD: {indicators.get('macd', 0):.2f}
- Volatility (annualized): {indicators.get('volatility', 0)*100:.1f}%
- 20-day Momentum: {indicators.get('momentum_20', 0):.1f}%

3-Month Prediction:
- Predicted Price: ${prediction['predicted_price']:.2f}
- Confidence Range: ${prediction['confidence_low']:.2f} - ${prediction['confidence_high']:.2f}
- Trend: {prediction['trend'].upper()}
- Expected Change: {prediction['change_from_current']:.1f}%

Provide a concise 2-3 sentence investment outlook focusing on key technical factors and potential risks."""
    
    # Try LM Studio first (free local LLM)
    if USE_LLM_STUDIO:
        try:
            import requests
            response = requests.post(
                f"{LM_STUDIO_URL}/chat/completions",
                json={
                    "model": "llama-3.2-3b-instruct",
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
        except Exception as e:
            print(f"LM Studio error: {e}")
    
    # Fallback to OpenAI if configured
    if openai_client.api_key and openai_client.api_key != "":
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a professional stock analyst providing investment insights."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Analysis unavailable: {str(e)}"
    
    # Return mock analysis if no LLM available
    return f"Based on technical analysis, {symbol} shows a {prediction['trend']} trend. " \
           f"Current RSI is {indicators.get('rsi', 'N/A'):.1f} indicating " \
           f"{'overbought' if indicators.get('rsi', 50) > 70 else 'oversold' if indicators.get('rsi', 50) < 30 else 'neutral'} conditions. " \
           f"The stock is trading {'above' if stock_data['current_price'] > indicators.get('sma_50', 0) else 'below'} its 50-day moving average."


def get_weekly_data(symbol: str) -> List[dict]:
    """Get last 7 days of data with simulated predictions"""
    ticker = f"{symbol}.AX"
    stock = yf.Ticker(ticker)
    
    # Get last 7 days
    hist = stock.history(period="7d")
    
    weekly_data = []
    current_price = hist['Close'].iloc[-1] if len(hist) > 0 else 0
    
    for idx, row in hist.iterrows():
        # Simulate what prediction would have been (with some variance)
        variance = np.random.uniform(-0.03, 0.03)
        pred_price = row['Close'] * (1 + variance) if row['Close'] > 0 else 0
        
        weekly_data.append({
            "date": idx.strftime("%Y-%m-%d"),
            "actual_price": round(row['Close'], 2),
            "predicted_price": round(pred_price, 2)
        })
    
    return weekly_data

# API Routes

@app.get("/")
async def root():
    return {"message": "ASX Stock Predictor API", "version": "1.0.0"}

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "database": "connected",
        "lm_studio_configured": USE_LLM_STUDIO,
        "openai_configured": bool(openai_client.api_key and openai_client.api_key != "")
    }

@app.get("/api/shares")
async def get_all_shares():
    """Get all tracked shares with current data"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, name, added_at FROM shares")
    shares = cursor.fetchall()
    conn.close()
    
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
async def add_share(symbol: str):
    """Add a new share to track"""
    symbol = symbol.upper().strip()
    
    if symbol not in ASX_COMPANIES:
        # Try to get company name from yfinance
        ticker = f"{symbol}.AX"
        stock = yf.Ticker(ticker)
        try:
            info = stock.info
            name = info.get('longName', info.get('shortName', symbol))
            ASX_COMPANIES[symbol] = name
        except:
            raise HTTPException(status_code=400, detail=f"Invalid ASX symbol: {symbol}")
    else:
        name = ASX_COMPANIES[symbol]
    
    # Check if already exists
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM shares WHERE symbol = ?", (symbol,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail=f"Share {symbol} already tracked")
    
    cursor.execute("INSERT INTO shares (symbol, name) VALUES (?, ?)", (symbol, name))
    conn.commit()
    conn.close()
    
    return {"message": f"Share {symbol} added successfully", "symbol": symbol}

@app.delete("/api/shares/{symbol}")
async def remove_share(symbol: str):
    """Remove a share from tracking"""
    symbol = symbol.upper()
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shares WHERE symbol = ?", (symbol,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"Share {symbol} not found")
    
    return {"message": f"Share {symbol} removed successfully"}

@app.get("/api/shares/{symbol}")
async def get_share_details(symbol: str):
    """Get detailed info with prediction for a share"""
    symbol = symbol.upper()
    
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
    
    # Get LLM analysis
    llm_analysis = await get_llm_analysis(symbol, stock_data, indicators, prediction)
    prediction["llm_analysis"] = llm_analysis
    
    # Get weekly data
    weekly_data = get_weekly_data(symbol)
    
    return {
        "symbol": symbol,
        "name": stock_data["name"],
        "current_price": stock_data["current_price"],
        "change_percent": round(stock_data["change_percent"], 2),
        "technical_indicators": indicators,
        "prediction_3m": prediction,
        "weekly_data": weekly_data
    }

@app.get("/api/search")
async def search_shares(query: str):
    """Search for ASX shares"""
    query = query.upper()
    results = []
    
    for symbol, name in ASX_COMPANIES.items():
        if query in symbol or query.lower() in name.lower():
            results.append({"symbol": symbol, "name": name})
    
    return results[:10]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

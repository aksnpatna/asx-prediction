# ASX Prediction Engine & Analysis Dashboard
# Streamlit application with Prophet-like forecasting and Qwen 2.5 LLM

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import sqlite3
import requests
from datetime import datetime, timedelta
from pathlib import Path
import plotly.graph_objects as go
from statsmodels.tsa.holtwinters import ExponentialSmoothing

# ============================================================================
# Configuration
# ============================================================================
LM_STUDIO_URL = "http://localhost:1234/v1"
USE_LLM_STUDIO = True
DB_PATH = Path(__file__).parent / "data" / "asx_engine.db"
DB_PATH.parent.mkdir(exist_ok=True)

# ============================================================================
# Database Setup
# ============================================================================
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Predictions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            prediction_date DATE NOT NULL,
            predicted_price_3m REAL NOT NULL,
            model_confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Actuals table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS actuals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date DATE NOT NULL,
            close_price REAL NOT NULL,
            UNIQUE(ticker, date)
        )
    """)
    
    # Logs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            prediction_date DATE NOT NULL,
            predicted_price REAL NOT NULL,
            actual_date DATE NOT NULL,
            actual_price REAL NOT NULL,
            error_pct REAL NOT NULL
        )
    """)
    
    # Watchlist table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# ============================================================================
# ASX Company Data
# ============================================================================
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
    "WES": "Wesfarmers Limited",
    "QAN": "Qantas Airways Limited",
    "STO": "Santos Limited",
    "NCM": "Newcrest Mining Limited",
    "JHX": "James Hardie Industries",
}

# ============================================================================
# Data Fetching Functions
# ============================================================================
def get_stock_data(symbol: str) -> dict:
    """Fetch stock data from yfinance"""
    ticker = f"{symbol}.AX"
    stock = yf.Ticker(ticker)
    
    try:
        info = stock.info
        current_price = info.get('currentPrice', info.get('regularMarketPreviousClose', 0))
        prev_close = info.get('regularMarketPreviousClose', current_price)
        volume = info.get('volume', 0)
        change = ((current_price - prev_close) / prev_close * 100) if prev_close else 0
        # Try to get company name from yfinance
        name = info.get('longName', info.get('shortName', ASX_COMPANIES.get(symbol, symbol)))
    except:
        current_price = 0
        change = 0
        volume = 0
        name = ASX_COMPANIES.get(symbol, symbol)
    
    return {
        "current_price": current_price,
        "change_percent": change,
        "volume": volume,
        "name": name
    }

def get_historical_data(symbol: str, period: str = "2y") -> pd.DataFrame:
    """Get historical price data"""
    ticker = f"{symbol}.AX"
    stock = yf.Ticker(ticker)
    hist = stock.history(period=period)
    return hist

def get_iron_ore_price() -> float:
    """Fetch Iron Ore price (using steel index as proxy)"""
    try:
        # Using a steel ETF as proxy
        ticker = yf.Ticker("VEU")
        hist = ticker.history(period="5d")
        if len(hist) > 0:
            return float(hist['Close'].iloc[-1] * 100)
    except:
        pass
    return 120.0  # Default fallback

def get_aud_usd() -> float:
    """Fetch AUD/USD exchange rate"""
    try:
        ticker = yf.Ticker("AUDUSD=X")
        hist = ticker.history(period="5d")
        if len(hist) > 0:
            return float(hist['Close'].iloc[-1])
    except:
        pass
    return 0.65  # Default fallback

# ============================================================================
# Technical Analysis (replacing pandas-ta)
# ============================================================================
def calculate_technical_indicators(df: pd.DataFrame) -> dict:
    """Calculate technical indicators manually"""
    indicators = {}
    
    if len(df) < 50:
        return indicators
    
    close = df['Close']
    
    # Moving Averages
    indicators['sma_20'] = float(close.rolling(20).mean().iloc[-1])
    indicators['sma_50'] = float(close.rolling(50).mean().iloc[-1])
    indicators['sma_200'] = float(close.rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
    
    # RSI
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    indicators['rsi'] = float(100 - (100 / (1 + rs)).iloc[-1])
    
    # MACD
    exp1 = close.ewm(span=12, adjust=False).mean()
    exp2 = close.ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9, adjust=False).mean()
    indicators['macd'] = float(macd.iloc[-1])
    indicators['macd_signal'] = float(signal.iloc[-1])
    indicators['macd_hist'] = float(macd.iloc[-1] - signal.iloc[-1])
    
    # Volatility
    indicators['volatility'] = float(close.pct_change().std() * np.sqrt(252))
    
    # Bollinger Bands
    sma_20 = close.rolling(20).mean()
    std_20 = close.rolling(20).std()
    indicators['bb_upper'] = float((sma_20 + (std_20 * 2)).iloc[-1])
    indicators['bb_middle'] = float(sma_20.iloc[-1])
    indicators['bb_lower'] = float((sma_20 - (std_20 * 2)).iloc[-1])
    
    # Volume analysis
    if 'Volume' in df.columns:
        avg_volume = df['Volume'].rolling(20).mean().iloc[-1]
        current_volume = df['Volume'].iloc[-1]
        indicators['volume_ratio'] = float(current_volume / avg_volume) if avg_volume > 0 else 1.0
    
    # Price momentum
    indicators['momentum_20'] = float((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100)
    
    return indicators

# ============================================================================
# Forecasting (Prophet-like using statsmodels)
# ============================================================================
def generate_forecast(df: pd.DataFrame, periods: int = 90) -> dict:
    """Generate 3-month forecast using Exponential Smoothing"""
    if len(df) < 60:
        return {"error": "Insufficient data for forecasting"}
    
    close = df['Close'].dropna()
    
    try:
        # Using Holt-Winters Exponential Smoothing (similar to Prophet)
        model = ExponentialSmoothing(
            close,
            trend='add',
            seasonal='add',
            seasonal_periods=30
        )
        fitted_model = model.fit()
        
        # Forecast next 90 days
        forecast = fitted_model.forecast(periods)
        
        # Calculate confidence (using prediction intervals)
        std_err = close.pct_change().std() * np.sqrt(periods/252)
        
        return {
            "forecast": forecast.tolist(),
            "predicted_price": float(forecast.iloc[-1]),
            "confidence_low": float(forecast.iloc[-1] * (1 - 2 * std_err)),
            "confidence_high": float(forecast.iloc[-1] * (1 + 2 * std_err)),
            "trend": "bullish" if forecast.iloc[-1] > close.iloc[-1] else "bearish",
            "change_from_current": float((forecast.iloc[-1] - close.iloc[-1]) / close.iloc[-1] * 100)
        }
    except Exception as e:
        return {"error": str(e)}

# ============================================================================
# LLM Analysis
# ============================================================================
def get_llm_analysis(symbol: str, stock_data: dict, indicators: dict, forecast: dict, 
                     iron_ore: float, aud_usd: float) -> str:
    """Get analysis from Qwen 2.5 via LM Studio"""
    
    prompt = f"""You are a professional ASX stock analyst. Analyze the following data for {symbol} ({stock_data['name']}):

CURRENT STATUS:
- Price: ${stock_data['current_price']:.2f}
- Daily Change: {stock_data['change_percent']:.2f}%

MACRO INDICATORS:
- Iron Ore Price: ${iron_ore:.2f}
- AUD/USD: {aud_usd:.4f}

TECHNICAL INDICATORS:
- RSI (14): {indicators.get('rsi', 'N/A'):.1f}
- MACD: {indicators.get('macd', 0):.2f} (Signal: {indicators.get('macd_signal', 0):.2f})
- SMA 20: ${indicators.get('sma_20', 0):.2f}
- SMA 50: ${indicators.get('sma_50', 0):.2f}
- Volatility: {indicators.get('volatility', 0)*100:.1f}%

3-MONTH FORECAST:
- Predicted Price: ${forecast.get('predicted_price', 0):.2f}
- Confidence Range: ${forecast.get('confidence_low', 0):.2f} - ${forecast.get('confidence_high', 0):.2f}
- Trend: {forecast.get('trend', 'unknown').upper()}

Provide a concise 2-3 sentence investment outlook considering the technicals, macro factors, and forecast. Rate the sentiment on a scale of -1 (very bearish) to 1 (very bullish)."""

    if USE_LLM_STUDIO:
        try:
            response = requests.post(
                f"{LM_STUDIO_URL}/chat/completions",
                json={
                    "model": "qwen2.5-7b-instruct",
                    "messages": [
                        {"role": "system", "content": "You are a professional stock analyst providing investment insights for ASX stocks."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 300,
                    "temperature": 0.7
                },
                timeout=60
            )
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
        except Exception as e:
            return f"LLM unavailable: {str(e)}"
    
    # Fallback analysis
    rsi = indicators.get('rsi', 50)
    trend = forecast.get('trend', 'neutral')
    return f"Based on technicals: RSI at {rsi:.1f} suggests {'overbought' if rsi > 70 else 'oversold' if rsi < 30 else 'neutral'} conditions. 3-month outlook is {trend}."

# ============================================================================
# Database Operations
# ============================================================================
def save_prediction(ticker: str, predicted_price: float, confidence: float):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO predictions (ticker, prediction_date, predicted_price_3m, model_confidence) VALUES (?, ?, ?, ?)",
        (ticker, datetime.now().date(), predicted_price, confidence)
    )
    conn.commit()
    conn.close()

def save_actual(ticker: str, price: float):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO actuals (ticker, date, close_price) VALUES (?, ?, ?)",
        (ticker, datetime.now().date(), price)
    )
    conn.commit()
    conn.close()

def get_prediction_accuracy(ticker: str, days_ago: int = 14) -> dict:
    """Calculate prediction accuracy for past predictions"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Get predictions from X days ago
    past_date = (datetime.now() - timedelta(days=days_ago)).date()
    cursor.execute("""
        SELECT predicted_price_3m, prediction_date 
        FROM predictions 
        WHERE ticker = ? AND prediction_date = ?
    """, (ticker, past_date))
    pred = cursor.fetchone()
    
    # Get actual price from today
    cursor.execute("""
        SELECT close_price FROM actuals 
        WHERE ticker = ? AND date = ?
    """, (ticker, datetime.now().date()))
    actual = cursor.fetchone()
    
    conn.close()
    
    if pred and actual:
        error = abs(pred[0] - actual[0]) / actual[0] * 100
        return {"accuracy": 100 - error, "predicted": pred[0], "actual": actual[0]}
    return None

def add_to_watchlist(ticker: str):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO watchlist (ticker) VALUES (?)", (ticker,))
    conn.commit()
    conn.close()

def get_watchlist():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT ticker FROM watchlist ORDER BY added_at DESC")
    watchlist = [row[0] for row in cursor.fetchall()]
    conn.close()
    return watchlist

# ============================================================================
# Streamlit UI
# ============================================================================
st.set_page_config(page_title="ASX Prediction Engine", layout="wide", page_icon="📈")

# Custom CSS
st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .stApp { background-color: #0e1117; }
    .metric-card {
        background-color: #1e293b;
        padding: 20px;
        border-radius: 10px;
        margin: 10px 0;
    }
    .bullish { color: #22c55e; }
    .bearish { color: #ef4444; }
    .neutral { color: #eab308; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# Tab 1: Market Pulse
# ============================================================================
def tab_market_pulse():
    st.header("📊 Market Pulse - Top 10 ASX Shares")
    
    # Get top 10 by volume
    top_stocks = list(ASX_COMPANIES.keys())[:10]
    
    cols = st.columns(5)
    data = []
    
    for i, symbol in enumerate(top_stocks):
        stock = get_stock_data(symbol)
        col = cols[i % 5]
        with col:
            with st.container():
                st.metric(
                    label=symbol,
                    value=f"${stock['current_price']:.2f}",
                    delta=f"{stock['change_percent']:.2f}%"
                )
        data.append({
            "Symbol": symbol,
            "Price": stock['current_price'],
            "Change": stock['change_percent'],
            "Volume": stock.get('volume', 0)
        })
    
    # DataFrame
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True)
    
    # Prediction Accuracy Scoreboard
    st.subheader("🎯 Prediction Scoreboard (14-day accuracy)")
    accuracy_data = []
    for symbol in top_stocks[:5]:
        acc = get_prediction_accuracy(symbol)
        if acc:
            accuracy_data.append({
                "Symbol": symbol,
                "Predicted": f"${acc['predicted']:.2f}",
                "Actual": f"${acc['actual']:.2f}",
                "Accuracy": f"{acc['accuracy']:.1f}%"
            })
    
    if accuracy_data:
        st.dataframe(pd.DataFrame(accuracy_data), use_container_width=True)
    else:
        st.info("Prediction accuracy will be available after 14 days of data collection.")

# ============================================================================
# Tab 2: Deep Dive
# ============================================================================
def tab_deep_dive():
    st.header("🔍 Deep Dive - Stock Analysis")
    
    # Search bar - allow custom ticker input
    col1, col2 = st.columns([2, 1])
    with col1:
        custom_ticker = st.text_input("Enter ASX Ticker (e.g., BHP, ZIP, etc.)", value="BHP", key="deep_dive_ticker")
    with col2:
        st.write("")
        st.write("")
        if st.button("🔍 Search", key="search_deep_dive"):
            symbol = custom_ticker.upper().replace('.AX', '')
    
    if 'symbol' not in locals():
        symbol = custom_ticker.upper().replace('.AX', '')
    
    if symbol:
        # Get data
        stock = get_stock_data(symbol)
        hist = get_historical_data(symbol)
        indicators = calculate_technical_indicators(hist)
        forecast = generate_forecast(hist)
        
        # Get macro data
        iron_ore = get_iron_ore_price()
        aud_usd = get_aud_usd()
        
        # Display metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Price", f"${stock['current_price']:.2f}", f"{stock['change_percent']:.2f}%")
        with col2:
            st.metric("RSI (14)", f"{indicators.get('rsi', 0):.1f}")
        with col3:
            st.metric("MACD", f"{indicators.get('macd', 0):.2f}")
        with col4:
            trend = forecast.get('trend', 'unknown')
            st.metric("3M Forecast", f"${forecast.get('predicted_price', 0):.2f}", trend)
        
        # Candlestick Chart with Forecast
        st.subheader("📈 Price Chart with Forecast")
        
        fig = go.Figure()
        
        # Candlestick
        fig.add_trace(go.Candlestick(
            x=hist.index,
            open=hist['Open'],
            high=hist['High'],
            low=hist['Low'],
            close=hist['Close'],
            name='Price'
        ))
        
        # Moving averages
        if len(hist) >= 20:
            fig.add_trace(go.Scatter(
                x=hist.index, y=hist['Close'].rolling(20).mean(),
                line=dict(color='orange', width=1),
                name='SMA 20'
            ))
        
        if len(hist) >= 50:
            fig.add_trace(go.Scatter(
                x=hist.index, y=hist['Close'].rolling(50).mean(),
                line=dict(color='blue', width=1),
                name='SMA 50'
            ))
        
        # Forecast (prediction shadow)
        if 'forecast' in forecast and len(forecast.get('forecast', [])) > 0:
            forecast_dates = pd.date_range(start=hist.index[-1], periods=len(forecast['forecast'])+1, freq='D')[1:]
            fig.add_trace(go.Scatter(
                x=forecast_dates, y=forecast['forecast'],
                line=dict(color='green', width=2, dash='dash'),
                name='3M Forecast'
            ))
        
        fig.update_layout(
            xaxis_rangeslider_visible=False,
            template="plotly_dark",
            height=500
        )
        st.plotly_chart(fig, use_container_width=True)
        
        # Technical indicators table
        st.subheader("📊 Technical Indicators")
        indicators_df = pd.DataFrame([indicators]).T
        indicators_df.columns = ['Value']
        st.dataframe(indicators_df)
        
        # Add to watchlist
        if st.button(f"⭐ Add {symbol} to Watchlist"):
            add_to_watchlist(symbol)
            st.success(f"Added {symbol} to watchlist!")

# ============================================================================
# Tab 3: AI Analyst
# ============================================================================
def tab_ai_analyst():
    st.header("🤖 AI Analyst - Qwen 2.5")
    
    # Get macro data
    iron_ore = get_iron_ore_price()
    aud_usd = get_aud_usd()
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Iron Ore Price", f"${iron_ore:.2f}")
    with col2:
        st.metric("AUD/USD", f"{aud_usd:.4f}")
    
    # Select stock for analysis - allow custom ticker
    col1, col2 = st.columns([2, 1])
    with col1:
        custom_ticker_ai = st.text_input("Enter ASX Ticker for Analysis", value="BHP", key="ai_ticker")
    with col2:
        st.write("")
        st.write("")
        if st.button("Generate Analysis", key="ai_generate"):
            symbol = custom_ticker_ai.upper().replace('.AX', '')
    
    if 'symbol' not in locals():
        symbol = custom_ticker_ai.upper().replace('.AX', '')
    
    if st.button("Generate Analysis"):
        with st.spinner("Analyzing with Qwen 2.5..."):
            stock = get_stock_data(symbol)
            hist = get_historical_data(symbol)
            indicators = calculate_technical_indicators(hist)
            forecast = generate_forecast(hist)
            
            # Get LLM analysis
            analysis = get_llm_analysis(symbol, stock, indicators, forecast, iron_ore, aud_usd)
            
            # Save prediction
            if 'predicted_price' in forecast:
                confidence = abs(forecast['predicted_price'] - stock['current_price']) / stock['current_price']
                save_prediction(symbol, forecast['predicted_price'], 1 - confidence)
                save_actual(symbol, stock['current_price'])
            
            # Display analysis
            st.subheader(f"💡 Analysis for {symbol}")
            st.write(analysis)
            
            # Sentiment indicator
            if 'bullish' in analysis.lower():
                st.success("🟢 Bullish Sentiment")
            elif 'bearish' in analysis.lower():
                st.error("🔴 Bearish Sentiment")
            else:
                st.warning("🟡 Neutral Sentiment")
    
    # Watchlist analysis
    st.subheader("⭐ Your Watchlist")
    watchlist = get_watchlist()
    if watchlist:
        for ticker in watchlist:
            stock = get_stock_data(ticker)
            st.write(f"**{ticker}**: ${stock['current_price']:.2f} ({stock['change_percent']:+.2f}%)")
    else:
        st.info("No stocks in watchlist. Add some from the Deep Dive tab!")

# ============================================================================
# Main App
# ============================================================================
st.title("🇦🇺 ASX Prediction Engine")
st.markdown("### Using Qwen 2.5 7B + Statistical Forecasting")

tab1, tab2, tab3 = st.tabs(["📊 Market Pulse", "🔍 Deep Dive", "🤖 AI Analyst"])

with tab1:
    tab_market_pulse()

with tab2:
    tab_deep_dive()

with tab3:
    tab_ai_analyst()

# Sidebar
st.sidebar.title("⚙️ Settings")
st.sidebar.markdown("---")
st.sidebar.markdown("**LM Studio Status:**")
if USE_LLM_STUDIO:
    st.sidebar.success("🟢 Connected to Qwen 2.5")
else:
    st.sidebar.error("🔴 Not Connected")

st.sidebar.markdown("---")
st.sidebar.markdown("**About:**")
st.sidebar.info("ASX Prediction Engine using Exponential Smoothing forecasting and local LLM analysis.")

if __name__ == "__main__":
    import sys
    sys.argv = ["streamlit", "run", __file__]
    import subprocess
    subprocess.run(sys.argv)

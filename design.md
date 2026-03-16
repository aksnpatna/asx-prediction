# ASX Prediction Engine & Analysis Dashboard (System Design)

## 1. Overview
A containerized full-stack data engineering project that fetches **ASX (Australian Securities Exchange)** data, performs statistical forecasting, and uses a local **Qwen 2.5 7B** LLM (via LM Studio) to provide context-aware financial insights.

---

## 2. System Architecture
- **Data Ingestion:** Python `yfinance` using the `.AX` suffix to cover all 2,000+ ASX listings.
- **Backend Database:** **SQLite** (lightweight, file-based) to store:
    - Daily "Top 10" snapshots.
    - Prediction history (to calculate the 2-week confidence score).
    - User-inputted "Watchlist" tickers.
- **Compute Layer:**
    - **Statistical:** `Prophet` (by Meta) for 3-month trend forecasting.
    - **Technical:** `pandas-ta` to generate RSI, MACD, and Moving Averages (50/200 day).
    - **LLM Engine:** **LM Studio** (running Qwen 2.5 7B) hosted on the local machine.
- **Frontend:** **Streamlit** dashboard for visualization.
- **Deployment:** **Docker Compose** (Hybrid setup: App in Docker, LLM on Host).

---

## 3. Data Strategy & Predictability Enhancements
To move beyond "simple" predictions, the engine will use **Feature Engineering**:
1. **Macro Correlation:** Fetch **Iron Ore prices** and **AUD/USD** rates as external regressors (ASX is heavily commodity-driven).
2. **Technical Signals:** Instead of just price, the model inputs:
    - **Relative Strength Index (RSI)** to detect overbought/oversold conditions.
    - **Volume Spikes** to confirm trend strength.
3. **LLM Sentiment Feature:** 
    - Scrape recent ASX company headlines.
    - Prompt Qwen 2.5: *"On a scale of -1 to 1, how bullish is this news?"*
    - Pass this numerical score into the statistical model.

---

## 4. Dashboard Design (Streamlit)
- **Tab 1: Market Pulse (Top 10)**
    - Real-time tracker of the top 10 ASX shares by volume.
    - **Confidence Metric:** A "Scoreboard" showing how accurate the 14-day-old predictions were against today's actual price.
- **Tab 2: Deep Dive (User Input)**
    - Search bar for any ASX ticker (e.g., `BHP.AX`, `ZIP.AX`).
    - Interactive **Plotly** candlestick chart with a 3-month "Prediction Shadow."
- **Tab 3: AI Analyst (Qwen 2.5)**
    - A text area where the LLM explains the "Why" behind the numbers.
    - Example: *"While the 3-month trend is up, the RSI is 75, suggesting a short-term pullback is likely."*

---

## 5. Docker & Environment Configuration

### `docker-compose.yml` Logic
```yaml
services:
  asx-app:
    build: .
    ports:
      - "8501:8501"
    extra_hosts:
      - "host.docker.internal:host-gateway" # Connects to LM Studio on host
    volumes:
      - ./database:/app/db # Persistent storage for SQLite
    environment:
      - LM_STUDIO_URL=http://host.docker.internal


# further details
Backend Database Schema (SQLite)
predictions: ticker, prediction_date, predicted_price_3m, model_confidence.
actuals: ticker, date, close_price.
logs: To track "Prediction vs Actual" error rates over the 2-week window.
6. Implementation Steps
Setup LM Studio: Load Qwen 2.5 7B and start the Local Server.
Dev Script: Write a Python script to fetch CBA.AX and verify it can talk to the LLM API.
The Prophet Model: Implement the forecast using yfinance history.
The SQLite Loop: Create a cron-job (or Streamlit background task) that records daily prices for the Top 10.
Containerise: Wrap the code in a Dockerfile and launch via Compose.

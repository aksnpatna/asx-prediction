# ASX Stock Predictor - Technical Specification

## Project Overview
- **Name:** ASX Stock Predictor
- **Architecture:** React Frontend + FastAPI Backend + OpenAI LLM
- **Data Source:** yfinance (Yahoo Finance API)
- **Deployment:** Docker + Docker Compose
- **Purpose:** Predict ASX share prices for next 3 months using statistical analysis + LLM

---

## Tech Stack

### Frontend
- React 18 with Vite
- Chart.js + react-chartjs-2 for visualizations
- Axios for API calls
- CSS Modules or styled-components

### Backend
- FastAPI (Python)
- yfinance for stock data
- OpenAI API for LLM predictions
- pandas, numpy for statistical analysis

### Infrastructure
- Docker + Docker Compose
- Nginx (optional for production)

---

## UI/UX Specification

### Color Palette
- Primary Background: `#0D1117` (deep space black)
- Secondary Background: `#161B22` (card background)
- Accent Primary: `#58A6FF` (electric blue)
- Accent Success: `#3FB950` (green - gains)
- Accent Danger: `#F85149` (red - losses)
- Accent Warning: `#D29922` (amber)
- Text Primary: `#E6EDF3`
- Text Secondary: `#8B949E`
- Chart Predicted: `#A371F7` (purple)
- Chart Actual: `#58A6FF` (blue)

### Typography
- Headings: Outfit (Google Fonts)
- Data/Numbers: JetBrains Mono

### Components
1. **Dashboard** - Grid of share cards with mini charts
2. **Share Card** - Symbol, price, prediction, sparkline
3. **Prediction Chart** - Full chart with predicted vs actual
4. **Add Share Modal** - Search ASX symbols
5. **Stats Panel** - Portfolio metrics

---

## API Endpoints

### Backend (FastAPI)

```
GET  /api/shares              - Get all tracked shares
POST /api/shares              - Add new share
DELETE /api/shares/{symbol}   - Remove share
GET  /api/shares/{symbol}     - Get share details with prediction
GET  /api/shares/{symbol}/history - Get historical data
POST /api/predict             - Generate prediction for a share
GET  /api/health              - Health check
```

### Data Models

```python
class Share(BaseModel):
    symbol: str
    name: str
    current_price: float
    change_percent: float
    prediction_3m: Prediction3M
    weekly_data: List[DailyData]

class Prediction3M(BaseModel):
    predicted_price: float
    confidence_low: float
    confidence_high: float
    trend: str  # "bullish", "bearish", "neutral"
    factors: List[str]
    llm_analysis: str

class DailyData(BaseModel):
    date: date
    actual_price: float
    predicted_price: Optional[float]
```

---

## Prediction Logic

### Statistical Analysis (Python)
1. Fetch 1 year historical data using yfinance
2. Calculate:
   - Moving averages (20, 50, 200 day)
   - Volatility (standard deviation)
   - RSI (Relative Strength Index)
   - MACD
   - Bollinger Bands
3. Generate 3-month forecast using:
   - Linear regression
   - ARIMA model
   - Monte Carlo simulation

### LLM Enhancement (OpenAI)
1. Send analysis to LLM with:
   - Technical indicators
   - Recent news/sentiment (simulated)
   - Historical patterns
2. Get AI-generated:
   - Price prediction with reasoning
   - Key factors affecting price
   - Risk assessment

---

## Acceptance Criteria

1. ✅ Can add ASX shares by symbol (e.g., BHP, CBA, ANZ)
2. ✅ Shows current price and daily change
3. ✅ 3-month price prediction with confidence interval
4. ✅ Weekly chart showing predicted vs actual
5. ✅ LLM-powered analysis and reasoning
6. ✅ Responsive dashboard
7. ✅ Dockerized for easy deployment
8. ✅ Data persists in database (SQLite for simplicity)

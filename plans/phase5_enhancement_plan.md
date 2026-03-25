# ASX Intelligence Dashboard - Phase 5 Enhancement Plan

## Overview
This plan documents enhancements to add comprehensive context to LLM prompts, sentiment analysis in Tab 3, and IG.com-inspired design updates.

---

## 1. LLM Model Upgrade: DeepSeek R1-Distill-Qwen 7B

### Why DeepSeek R1?
| Feature | Qwen 2.5 | DeepSeek R1-Distill |
|---------|----------|---------------------|
| Reasoning | Good | Excellent (chain-of-thought) |
| Structured data | Moderate | Strong |
| Financial tasks | Avoids | Embraces |
| Long explanations | Moderate | Coherent |
| Hardware speed | Fast | Faster |

### Configuration Update
Update `.env`:
```env
LOCAL_LLM_MODEL=deepseek-r1-distill-qwen-7b-q4_k_m
LOCAL_LLM_URL=http://host.docker.internal:1234/v1
LLM_PROVIDER_ORDER=local,openai
```

---

## 2. Comprehensive LLM Prompt Structure (Priority: HIGH)

### Enhanced Prompt for Institutional-Grade Reasoning

```
You are a financial data analyst. 
You do not give financial advice. 
You only analyse data, identify patterns, compare companies, and explain market behaviour.

Stock: {symbol}
Sector: {sector}
Industry: {industry}
Sector 90-day Trend: {sector_trend}%
Peer Average Return (90 days): {peer_return}%

Current Price: ${price}
Daily Change: {change}%

Technical Indicators:
- SMA 20: ${sma20}
- SMA 50: ${sma50}
- SMA 200: ${sma200}
- RSI (14): {rsi}
- MACD: {macd}
- Volatility (annualized): {volatility}%
- 20-day Momentum: {momentum}%

Valuation:
- P/E: {pe}
- Forward P/E: {fwd_pe}
- PEG: {peg}
- Price-to-Book: {pb}
- Dividend Yield: {div_yield}%
- Market Cap: ${market_cap}

Risk Metrics:
- Beta (vs ASX200): {beta}
- Max Drawdown (90d): {max_dd}%
- Sharpe Ratio (90d): {sharpe}
- Liquidity Score: {liquidity}

Forecast (90 days):
- Predicted Price: ${pred}
- Range: ${low} - ${high}
- Trend: {trend}
- Expected Change: {x}%

Model Confidence:
- Probability of ≥5%: {prob}%
- Composite Score: {score}
- Breakdown:
  * Trend Strength: {trend_strength}
  * Quality Score: {quality}
  * Regime Fit: {regime_fit}
  * Liquidity Score: {liquidity}

Market Regime:
- ASX200 Trend: {regime}
- Volatility Regime: {vol_regime}

Historical Forecast Accuracy (for this stock):
- Last 10 predictions: {direction_acc}% directionally correct
- Average absolute error: {avg_error}%

Task:
Explain the stock's outlook based on the above data.
Identify key drivers, risks, sector context, and how the forecast aligns with the indicators.
Do not give buy/sell recommendations.
```

---

## 3. Data Fields to Add

### A. Sector & Industry Context (NEW)
| Field | Source | Required |
|-------|--------|----------|
| Sector | yfinance.info['sector'] | Yes |
| Industry | yfinance.info['industry'] | Yes |
| Sector 90-day Trend | Compute from sector ETFs | Yes |
| Peer Average Return | Compute from industry peers | Yes |

### B. Valuation Metrics (NEW)
| Field | Source | Required |
|-------|--------|----------|
| P/E | yfinance.info['trailingPE'] | Yes |
| Forward P/E | yfinance.info['forwardPE'] | Yes |
| PEG | yfinance.info['pegRatio'] | Yes |
| Price-to-Book | yfinance.info['priceToBook'] | Yes |
| Dividend Yield | yfinance.info['dividendYield'] | Yes |
| Market Cap | yfinance.info['marketCap'] | Yes |

### C. Risk Metrics (NEW)
| Field | Formula/Source | Required |
|-------|---------------|----------|
| Beta | yfinance or compute vs ASX200 | Yes |
| Max Drawdown (90d) | Compute from price history | Yes |
| Sharpe Ratio (90d) | (return - risk-free) / volatility | Yes |
| Liquidity Score | Already computed | Yes |

### D. Model Confidence Breakdown (NEW)
Add to prediction output:
- Trend Strength: 0.0-1.0
- Quality Score: 0.0-1.0  
- Regime Fit: 0.0-1.0
- Liquidity Score: 0.0-1.0

### E. Historical Forecast Accuracy (NEW)
| Field | Description |
|-------|-------------|
| Directional Accuracy | % of correct direction predictions |
| Average Error | Mean absolute percentage error |
| Per-stock tracking | Store in prediction_evaluations table |

---

## 4. Backend Implementation

### New Function: Get Comprehensive Stock Data
```python
def get_comprehensive_stock_data(symbol: str) -> dict:
    """Get all data needed for enhanced LLM prompt"""
    ticker = yf.Ticker(f"{symbol}.AX")
    info = ticker.info
    
    hist = get_historical_data(symbol, period="1y")
    indicators = calculate_technical_indicators(hist)
    prediction = generate_statistical_prediction(hist, current_price)
    
    # Valuation
    valuation = {
        'pe': info.get('trailingPE'),
        'forward_pe': info.get('forwardPE'),
        'peg': info.get('pegRatio'),
        'pb': info.get('priceToBook'),
        'dividend_yield': info.get('dividendYield'),
        'market_cap': info.get('marketCap'),
    }
    
    # Risk metrics
    risk = compute_risk_metrics(hist)  # Beta, MaxDD, Sharpe
    
    # Sector context
    sector = {
        'sector': info.get('sector'),
        'industry': info.get('industry'),
        'sector_trend': compute_sector_trend(info.get('sector')),
    }
    
    # Historical accuracy
    accuracy = get_stock_accuracy(symbol)
    
    return {
        'stock_data': stock_data,
        'indicators': indicators,
        'prediction': prediction,
        'valuation': valuation,
        'risk': risk,
        'sector': sector,
        'accuracy': accuracy,
    }
```

### Updated LLM Prompt Builder
```python
def build_enhanced_prompt(data: dict) -> str:
    """Build the comprehensive prompt for DeepSeek"""
    return f"""You are a financial data analyst...
    
Stock: {data['symbol']}
Sector: {data['sector']['sector']}
Industry: {data['sector']['industry']}
...
"""
```

---

## 5. Database Schema Additions

### New Table: stock_metrics_daily
```sql
CREATE TABLE stock_metrics_daily (
    symbol TEXT,
    trade_date DATE,
    pe_ratio REAL,
    forward_pe REAL,
    peg_ratio REAL,
    pb_ratio REAL,
    dividend_yield REAL,
    market_cap REAL,
    beta REAL,
    max_drawdown_90d REAL,
    sharpe_90d REAL,
    sector_trend_90d REAL,
    PRIMARY KEY (symbol, trade_date)
);
```

### New Table: prediction_evaluations
```sql
CREATE TABLE prediction_evaluations (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    prediction_date DATE NOT NULL,
    predicted_price REAL NOT NULL,
    actual_price REAL,
    direction_correct BOOLEAN,
    error_pct REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 6. Files to Modify

| File | Changes |
|------|---------|
| `.env` | Update LOCAL_LLM_MODEL to DeepSeek |
| `backend/main.py` | Add get_comprehensive_stock_data(), update LLM prompt |
| `backend/app.py` | Add valuation/risk functions |
| `SPEC.md` | Document new fields |

---

## 7. Implementation Priority

| # | Task | Priority | Est. Effort |
|---|------|----------|-------------|
| 1 | Update LLM config to DeepSeek | HIGH | 1 hour |
| 2 | Add valuation metrics fetching | HIGH | 1 day |
| 3 | Add risk metrics (Beta, MaxDD, Sharpe) | HIGH | 1 day |
| 4 | Add sector context | MEDIUM | 1 day |
| 5 | Add historical accuracy tracking | MEDIUM | 2 days |
| 6 | Build enhanced prompt structure | HIGH | 1 day |
| 7 | Tab 3 sentiment (from previous plan) | MEDIUM | 2 days |
| 8 | IG.com design updates | LOW | 2 days |

---

## 8. Acceptance Criteria

- [ ] DeepSeek R1-Distill model configured and working
- [ ] LLM prompt includes all 6 categories (Sector, Valuation, Risk, Regime, Confidence, History)
- [ ] All new metrics fetched from yfinance
- [ ] Historical accuracy tracked per stock
- [ ] Existing functionality preserved

# ASX Intelligence Dashboard - Technical Specification

## 1. Goal
Deliver a production-style ASX dashboard with:
1. 14-day tracking and validation buckets.
2. 3-month probability-based screener.
3. Dual LLM support (local LLM and OpenAI fallback).
4. Full Docker deployment with PostgreSQL persistence.

## 2. Architecture

### 2.1 Runtime Stack
1. Frontend: React + Vite + Nginx.
2. Backend: FastAPI + Python analytics pipeline.
3. Database: PostgreSQL 16.
4. LLM providers:
   - Local OpenAI-compatible endpoint (for example LM Studio).
   - OpenAI API.

### 2.2 Service Diagram
1. frontend -> backend REST API.
2. backend -> PostgreSQL (read/write).
3. backend -> local LLM URL and/or OpenAI API.
4. scheduled jobs in backend/worker for daily ingest and evaluation.

## 3. Product Requirements

### 3.0 Multi-user Access
1. User registration via email and password.
2. User login with JWT bearer tokens.
3. All shares, watchlists, and tracking windows are scoped by user_id.
4. One user cannot read or modify another user's data.

### 3.1 Tab 1 - 14-Day Tracker (Primary)
1. Add share symbol to tracking.
2. Snapshot baseline and compute target_price_14d.
3. Track daily progression until D+14.
4. Auto-classify into buckets:
   - meeting_expectation
   - non_meeting_expectation

Evaluation rule:
1. Direction correctness must hold.
2. Return must be within tolerance of expected return.
3. Default tolerance: 1.5 percentage points.

### 3.2 Tab 2 - Screener
1. Build ASX universe and liquidity filter.
2. Compute features and expected return distribution.
3. Rank by P(return_3m >= 5%).
4. Allow promoting candidate into Tab 1 tracker.

### 3.3 Tab 3 - Explainability
1. Error metrics over time.
2. Calibration chart.
3. Regime-segmented performance.

## 4. Regime Logic Checks

### 4.1 Safe Haven Check
If stocks are down and gold is up:
1. regime = risk_off
2. explanation tag = safe_haven_risk_off

### 4.2 Currency Headwind Check
If DXY spikes:
1. tag = usd_headwind_exporters
2. warn for exporter-heavy names.

### 4.3 Divergence Alert
If stocks and gold both rise:
1. tag = liquidity_rally_divergence
2. flag as unusual co-movement.

## 5. Statistical Methods and Formulas

### 5.1 Returns
$$r_t = \frac{P_t - P_{t-1}}{P_{t-1}}$$
$$R_{14} = \frac{P_{t+14} - P_t}{P_t}$$
$$R_{63} = \frac{P_{t+63} - P_t}{P_t}$$

### 5.2 Volatility and Momentum
$$\sigma_{ann} = \text{std}(r)\sqrt{252}$$
$$M_{20} = \frac{P_t - P_{t-20}}{P_{t-20}}$$

### 5.3 Probability Target
Assume model output distribution:
$$R_{63} \sim \mathcal{N}(\mu,\sigma^2)$$
Probability threshold metric:
$$P(R_{63} \ge 0.05) = 1 - \Phi\left(\frac{0.05-\mu}{\sigma}\right)$$

### 5.4 Composite Score
$$
Score = 0.45P(R_{63}\ge 5\%) + 0.20Trend + 0.15Quality + 0.10RegimeFit + 0.10Liquidity
$$

### 5.5 Validation KPIs
$$MAE = \frac{1}{N}\sum |\hat{y}_i-y_i|$$
$$RMSE = \sqrt{\frac{1}{N}\sum (\hat{y}_i-y_i)^2}$$
$$MAPE = \frac{100}{N}\sum\left|\frac{\hat{y}_i-y_i}{y_i}\right|$$
$$DirAcc = \frac{1}{N}\sum \mathbf{1}[\text{sign}(\hat{R})=\text{sign}(R)]$$

## 6. Data Model (PostgreSQL)

### 6.1 Tables
1. instruments(symbol PK, name, sector, is_exporter, market_cap_bucket, is_active)
2. market_data_daily(symbol, trade_date, open, high, low, close, volume, PK(symbol, trade_date))
3. macro_daily(trade_date PK, sp500_ret, gold_ret, dxy_ret, audusd, asx200_ret)
4. features_daily(symbol, trade_date, rsi14, macd, macd_signal, sma20, sma50, volatility20, momentum20, PK(symbol, trade_date))
5. prediction_runs(run_id PK, model_version, run_ts, horizon_days)
6. predictions(run_id, symbol, asof_date, expected_return_63, prob_ge_5pct, ci_low, ci_high, target_price_14d, target_price_63d, PK(run_id, symbol))
7. tracking_windows(tracking_id PK, symbol, start_date, end_date, entry_price, target_price_14d, target_return_14d, tolerance_pct, status)
8. tracking_evaluations(tracking_id, eval_date, actual_price_14d, actual_return_14d, direction_correct, within_tolerance, bucket, error_abs_pct)
9. regime_daily(trade_date PK, regime, confidence, safe_haven_flag, usd_headwind_flag, divergence_flag, details_json)
10. ai_explanations(id PK, created_at, context_type, symbol_nullable, prompt_json, response_json, model_name)
11. chat_sessions(id PK, created_at)
12. chat_messages(id PK, session_id, role, content, created_at)

## 7. API Contract (Core)
1. POST /api/auth/register
2. POST /api/auth/login
3. GET /api/auth/me
4. GET /api/health
5. GET /api/shares
6. POST /api/shares?symbol={SYMBOL}
7. DELETE /api/shares/{symbol}
8. GET /api/shares/{symbol}
9. GET /api/search?query={text}
10. GET /api/v1/tracking/buckets (next iteration)
11. GET /api/v1/screener/top-picks (next iteration)

## 8. LLM Provider Strategy

### 8.1 Config
1. LLM_PROVIDER_ORDER=local,openai or openai,local
2. LOCAL_LLM_URL and LOCAL_LLM_MODEL
3. OPENAI_API_KEY and OPENAI_MODEL

### 8.2 Selection Logic
1. Try providers in configured order.
2. If one fails, fallback to next provider.
3. If all fail, return deterministic rule-based analysis.

### 8.3 Prompt Structure
Input envelope:
1. raw metrics
2. derived regime flags
3. symbol context
4. strict output task (for example 3 bullets)

## 9. Frontend UX Direction (IG-like Vibe)
1. Dark-professional dashboard shell.
2. Dense but readable table-first layout.
3. Right-side persistent AI assistant panel.
4. Fast interactions: filter, add, bucket drill-down.
5. Mobile: AI drawer and compact cards.

## 10. Security and Ops
1. Never commit API keys.
2. Use .env locally and secret manager in production.
3. Restrict CORS in production.
4. Add API rate limits for chat endpoint.
5. Add daily PostgreSQL backup routine.

## 11. Docker Deployment

### 11.1 Services
1. db: PostgreSQL 16 with volume.
2. backend: FastAPI at :8000.
3. frontend: Nginx at :80.

### 11.2 Zero-cost Access Pattern
1. Run stack locally or low-cost host.
2. Expose through Cloudflare Tunnel free tier.
3. Optional free DNS via Cloudflare.

## 12. Build and Test Sequence
1. docker compose config
2. docker compose build backend
3. docker compose up -d db
4. docker compose up -d backend
5. GET /api/health
6. POST /api/shares?symbol=BHP
7. GET /api/shares
8. GET /api/shares/BHP
9. docker compose build frontend
10. docker compose up -d frontend
11. open app and verify UI/API integration

## 13. Definition of Done
1. PostgreSQL-backed backend is running in Docker.
2. Both local LLM and OpenAI paths are configurable and tested.
3. API health confirms provider config.
4. Tracker add/remove/detail flows function.
5. Spec and design are synchronized with implementation path.

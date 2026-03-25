# ASX Intelligence Dashboard - Detailed Design Document

## 1. Product Vision
Build a lightweight but professional ASX decision-support platform with:
- FastAPI backend for data, analytics, ranking, and AI orchestration.
- React frontend with an IG-style trading dashboard vibe (clean, dense, dark-professional, responsive).
- PostgreSQL for persistent analytics history and 2-week prediction tracking.
- LLM reasoning (Qwen/OpenAI-compatible) that explains market regime and candidate picks.

Important product positioning:
- The system ranks opportunities by probability and evidence.
- The system does not guarantee returns.
- User language in UI should use "probability of >=5%" instead of "will return 5%."

---

## 2. Core User Outcomes
1. Add shares quickly and monitor them over a 14-day validation window.
2. See if predictions align with expected target movement.
3. Auto-bucket symbols into:
   - Meeting Expectation.
   - Non-Meeting Expectation.
4. Ask AI chat for explanation of market regime, movers, and risk.
5. Screen ASX candidates with quantitative scoring + AI reasoning.
6. Use personal account login so each user only sees their own data and history.

---

## 3. Functional Scope

### 3.1 Tab 1 - Tracker + Validation Buckets (Primary Screen)
Purpose: Add shares, evaluate prediction alignment over 2 weeks, and classify outcome.

UI blocks:
1. Add Share panel (symbol search + add button).
2. Active 14-Day Tracking table:
   - Symbol, start date, current day (D+N), entry price, target price (D+14), current price, progress to target, expected vs actual direction, confidence.
3. Bucket cards:
   - Meeting Expectation.
   - Non-Meeting Expectation.
4. Quick metrics:
   - Validation hit rate (last 30 windows).
   - Average forecast error.
   - Median time-to-target.
5. Right-side AI chat panel:
   - Suggested prompts: "Explain why my non-meeting bucket expanded today", "Which tracked shares are closest to target?"

Validation lifecycle:
1. User adds symbol.
2. System snapshots baseline features and creates a D+14 target.
3. Daily close updates tracking state.
4. At D+14, symbol is auto-evaluated and moved to one of two buckets.

Bucket rule (recommended):
- Meeting Expectation if both are true:
  1. Direction correct (predicted up/down matches realized move).
  2. Target tolerance satisfied:
     - For bullish target: actual_return_14d >= target_return_14d - tol.
     - For bearish target: actual_return_14d <= target_return_14d + tol.
- Non-Meeting otherwise.

Default tolerance:
- tol = 1.5 percentage points (configurable).

### 3.2 Tab 2 - Candidate Movers Screener
Purpose: Find candidates likely to deliver >=5% in 3 months.

Flow:
1. Build universe (ASX100/200 + liquidity filter).
2. Compute factor features and regime context.
3. Model returns distribution and probability of >=5%.
4. Rank and display top candidates with risk notes.
5. User can promote candidates into Tab 1 tracking.

### 3.3 Tab 3 - Explainability + Backtest
Purpose: Build trust with evidence.

Widgets:
1. Predicted vs actual return plot.
2. Calibration chart (predicted probability bins vs realized frequency).
3. Error trend (MAE, RMSE, MAPE by week).
4. Regime-segmented performance heatmap.

---

## 4. Market Regime Intelligence (Required Logic Checks)

### 4.1 Safe Haven Check
Condition:
- Equities down and gold up.

Interpretation:
- Risk-off regime, defensive positioning likely.

### 4.2 Currency Headwind Check
Condition:
- DXY spike above threshold (for example > +0.4% day-over-day or z-score > 1).

Interpretation:
- Potential earnings drag for large-cap exporters due to stronger USD.

### 4.3 Divergence Alert
Condition:
- Equities up and gold up simultaneously.

Interpretation:
- Potential liquidity rally; not always a pure growth signal.

### 4.4 Prompt Contract for LLM
Always send:
1. Raw metrics.
2. Derived regime flags.
3. Portfolio context (for example tech-heavy).
4. Output schema constraints.

Example prompt payload:
```text
Data: S&P500 (-1.2%), Gold (+0.8%), DXY (+0.5%), Corr30d=-0.85
DerivedRegime: risk_off
Flags: safe_haven_risk_off, usd_headwind_exporters
Task: Explain current regime and impact on a tech-heavy portfolio in 3 bullet points.
OutputFields: regime_summary, portfolio_impact, risk_to_watch
```

---

## 5. Quantitative Model Design and Formulas

### 5.1 Return and Target Definitions
- Daily return:
  $$r_t = \frac{P_t - P_{t-1}}{P_{t-1}}$$
- 14-day realized return:
  $$R_{14} = \frac{P_{t+14} - P_t}{P_t}$$
- 3-month realized return (approx 63 trading days):
  $$R_{63} = \frac{P_{t+63} - P_t}{P_t}$$

### 5.2 Volatility and Momentum
- Annualized volatility:
  $$\sigma_{ann} = \text{std}(r) \times \sqrt{252}$$
- 20-day momentum:
  $$M_{20} = \frac{P_t - P_{t-20}}{P_{t-20}}$$

### 5.3 RSI and MACD (core technical features)
- RSI via average gains/losses over 14 periods.
- MACD = EMA(12) - EMA(26), signal = EMA(MACD, 9).

### 5.4 Probability of >=5% Return in 3 Months
If model outputs mean and standard deviation for 63-day return:
- $$R_{63} \sim \mathcal{N}(\mu, \sigma^2)$$
- $$P(R_{63} \ge 0.05) = 1 - \Phi\left(\frac{0.05 - \mu}{\sigma}\right)$$

### 5.5 Composite Ranking Score
Recommended score:
$$
Score = 0.45\cdot P(R_{63}\ge 5\%) +
0.20\cdot Trend +
0.15\cdot Quality +
0.10\cdot RegimeFit +
0.10\cdot Liquidity
$$

Where each component is normalized to [0,1].

### 5.6 Validation and Monitoring Metrics
- Mean absolute error:
  $$MAE = \frac{1}{N}\sum_{i=1}^{N}|\hat{y}_i - y_i|$$
- Root mean squared error:
  $$RMSE = \sqrt{\frac{1}{N}\sum_{i=1}^{N}(\hat{y}_i - y_i)^2}$$
- Mean absolute percentage error:
  $$MAPE = \frac{100}{N}\sum_{i=1}^{N}\left|\frac{\hat{y}_i - y_i}{y_i}\right|$$
- Directional accuracy:
  $$DirAcc = \frac{1}{N}\sum\mathbf{1}[\text{sign}(\hat{R}) = \text{sign}(R)]$$
- Bucket hit ratio (14-day):
  $$HitRatio_{14} = \frac{\#Meeting}{\#Meeting + \#NonMeeting}$$

---

## 6. Data Model (PostgreSQL)

### 6.1 Entity List
1. instruments
2. market_data_daily
3. macro_daily
4. features_daily
5. prediction_runs
6. predictions
7. tracking_windows (14-day watch)
8. tracking_evaluations
9. regime_daily
10. ai_explanations
11. chat_sessions
12. chat_messages
13. users

### 6.2 Key Tables (suggested columns)

instruments:
- symbol (PK)
- name
- sector
- is_exporter (bool)
- market_cap_bucket
- is_active

market_data_daily:
- symbol (FK)
- trade_date
- open, high, low, close, volume
- PRIMARY KEY(symbol, trade_date)

macro_daily:
- trade_date (PK)
- sp500_ret
- gold_ret
- dxy_ret
- audusd
- asx200_ret

features_daily:
- symbol
- trade_date
- rsi14
- macd
- macd_signal
- sma20
- sma50
- volatility20
- momentum20
- beta_market
- PRIMARY KEY(symbol, trade_date)

prediction_runs:
- run_id (PK)
- model_version
- run_ts
- horizon_days
- feature_cutoff_ts

predictions:
- run_id (FK)
- symbol
- asof_date
- expected_return_63
- prob_ge_5pct
- ci_low
- ci_high
- target_price_14d
- target_price_63d
- PRIMARY KEY(run_id, symbol)

tracking_windows:
- tracking_id (PK)
- symbol
- start_date
- end_date
- entry_price
- target_price_14d
- target_return_14d
- tolerance_pct
- status (active, completed)
- source (manual_add, promoted_from_screener)

tracking_evaluations:
- tracking_id (FK)
- eval_date
- actual_price_14d
- actual_return_14d
- direction_correct (bool)
- within_tolerance (bool)
- bucket (meeting, non_meeting)
- error_abs_pct

regime_daily:
- trade_date (PK)
- regime
- confidence
- safe_haven_flag
- usd_headwind_flag
- divergence_flag
- details_json

ai_explanations:
- id (PK)
- created_at
- context_type (market, symbol, screener, tracking)
- symbol_nullable
- prompt_json
- response_json
- model_name

---

## 7. Backend Design (FastAPI)

### 7.1 Service Layers
1. Ingestion service: pulls ASX + macro data daily.
2. Feature service: computes technical and statistical features.
3. Prediction service: generates 14-day and 63-day targets + probabilities.
4. Regime service: computes safe haven, currency headwind, divergence.
5. Tracking service: manages 2-week windows and bucket assignment.
6. LLM service: structured prompt builder + response validation.

### 7.2 API Endpoints (v1)
- GET /api/v1/market/pulse
- POST /api/v1/tracking/add
- GET /api/v1/tracking/active
- GET /api/v1/tracking/buckets
- POST /api/v1/tracking/evaluate-now (admin)
- GET /api/v1/screener/candidates
- GET /api/v1/screener/top-picks
- GET /api/v1/symbol/{symbol}/details
- POST /api/v1/chat/analyze (streaming)
- GET /api/v1/backtest/summary
- GET /api/v1/health

### 7.3 Scheduled Jobs
- Daily EOD price ingest.
- Daily macro ingest.
- Nightly feature compute.
- Nightly model run.
- Daily tracking update/evaluation.

---

## 8. Frontend Design (IG.com Vibe, Lightweight)

Design principle:
- Professional trading terminal feel, but simpler and faster.
- Use a custom visual language inspired by financial dashboards, not a clone.

### 8.1 Layout System
Desktop:
1. Top market ribbon (ASX200, Gold, DXY, regime badge).
2. Left nav rail (tabs + watchlists).
3. Main content (tab-dependent).
4. Right AI panel (chat, contextual actions, can collapse).

Mobile:
1. Top ribbon compact.
2. Tabs as segmented control.
3. AI panel moves to slide-up drawer.

### 8.2 Tab 1 Visual Structure
1. Header row: "2-Week Alignment Tracker" + add share input.
2. Two bucket cards side by side:
   - Meeting Expectation (green accent).
   - Non-Meeting Expectation (amber/red accent).
3. Active tracker grid with row state badges.
4. Mini performance panel with hit rate and forecast errors.
5. Right AI panel context switched to tracking insights.

### 8.3 Interaction Patterns
1. One-click promote from screener to tracker.
2. Row drilldown opens side sheet with:
   - Prediction details.
   - Feature snapshot at add date.
   - Daily trajectory vs expected path.
3. Tooltips explain formulas and confidence.

### 8.4 Styling Guidance
1. Dense data cards with high contrast and restrained accent colors.
2. Consistent typography scale (numeric font for prices).
3. Smooth but subtle motion (fade and slide only).
4. Avoid heavy decorative effects to keep render fast.

---

## 9. Container and Deployment Design (Docker + Near Zero Cost)

### 9.1 Compose Services
1. frontend (React + Nginx)
2. backend (FastAPI + Uvicorn/Gunicorn)
3. db (PostgreSQL)
4. worker (scheduler jobs)
5. optional: adminer/pgadmin (dev only)

### 9.2 Persistent Volumes
- postgres_data volume for DB durability.
- optional model cache volume.

### 9.3 Internet Access with Minimal/Zero Cost
Preferred option (self-host):
1. Run stack on your own machine/VPS/home server.
2. Use Cloudflare Tunnel (free) to expose HTTPS endpoint securely.
3. Use free Cloudflare DNS.

Alternative options:
1. Tailscale Funnel (free tier limits apply).
2. Oracle Cloud Always Free VM (resource limits, operational overhead).

Important:
- "Zero cost" is realistic only if you self-host and accept uptime/network constraints.
- If using home internet, set expectations around availability and latency.

### 9.4 Security Baseline
1. JWT auth for app users (register/login/me).
2. Rate limit chat and prediction endpoints.
3. Store secrets in environment variables, never in code.
4. CORS restricted to known frontend domain.
5. Daily DB backups (compressed dump).
6. Row-level ownership checks in API handlers to enforce user data isolation.

---

## 10. Phased Delivery Plan

Phase 1 (Foundation, 1-2 weeks):
1. FastAPI + PostgreSQL schema + migrations.
2. Ingestion jobs for ASX + macro.
3. Tab 1 tracker with add share and active rows.

Phase 2 (Validation engine, 1 week):
1. 14-day evaluation logic.
2. Meeting vs non-meeting bucket assignment.
3. Basic metrics (hit rate, MAE, directional accuracy).

Phase 3 (Screener + regime AI, 1-2 weeks):
1. Probability >=5% ranking model.
2. Regime checks and structured prompt output.
3. Tab 2 candidate flow and promotion to tracker.

Phase 4 (Explainability + production hardening, 1 week):
1. Tab 3 backtest dashboards.
2. Auth, rate limits, observability.
3. Cloudflare Tunnel deployment and monitoring.

---

## 11. Acceptance Criteria
1. User can add ASX symbols and start a 14-day tracking window.
2. System auto-evaluates windows and assigns meeting/non-meeting bucket.
3. User can view error metrics and hit ratio over time.
4. Screener ranks candidates by probability of >=5% in 3 months.
5. LLM explanations include regime context and structured reasons.
6. Full stack runs via Docker Compose with PostgreSQL persistence.
7. System is externally reachable through secure tunnel at near-zero cost.

---

## 12. Final Notes on Feasibility
Yes, this is a solid and realistic ask.

Why this is decent:
1. It has measurable feedback loops (14-day bucketing).
2. It blends quant signals with AI explanation instead of relying on AI alone.
3. It is deployable in low-cost mode with Docker and tunnel-based access.

Main risk to manage:
- Prediction quality drift; address with regular backtesting and strict bucket metrics.

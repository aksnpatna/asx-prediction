# ASX Intelligence Platform — Technical Methodology & Risk Framework

**Version:** 1.0 | **Audience:** Analysts, Advisors, Informed Investors  
**Last Updated:** June 2026

---

## Executive Summary

The ASX Intelligence Platform generates 90-day price forecasts and 14-day tracking signals for ASX-listed equities and ETFs by combining a **multi-model quantitative ensemble**, **real-time macroeconomic overlays**, **structured risk gating**, and **AI-narrative synthesis**. No single method drives the output — each layer provides a check on the others, and the composite signal is only elevated when multiple independent signals align.

> **Important Disclaimer:** Predictions are probabilistic estimates, not guarantees. Past accuracy does not ensure future performance. All signals should be used as one input in a broader investment process, not as stand-alone trade instructions.

---

## 1. Data Foundation

### 1.1 Price & Volume Data
- **Source:** Yahoo Finance via `yfinance` (live intraday + 2-year history)
- **Minimum data requirement:** 120 trading days before any prediction is issued
- **Coverage:** ASX 200+ equities, sector ETFs, Australian/US/Indian equities

### 1.2 Macroeconomic Data
| Indicator | Source | Refresh |
|---|---|---|
| AU 10-Year Bond Yield (`IRLTLT01AUM156N`) | FRED API | Daily |
| US Fed Funds Rate (`FEDFUNDS`) | FRED API | Monthly |
| CPI Year-on-Year (`CPIAUCSL`) | FRED API | Monthly |
| US Yield Curve 10Y–2Y (`T10Y2Y`) | FRED API | Daily |
| US Unemployment (`UNRATE`) | FRED API | Monthly |
| AUD/USD Exchange Rate | Yahoo Finance | Live |
| Copper Futures (HG=F) | Yahoo Finance | Live |
| Brent Crude (CL=F) | Yahoo Finance | Live |
| Gold Futures (GC=F) | Yahoo Finance | Live |
| VIX Volatility Index (^VIX) | Yahoo Finance | Live |
| ASX 200 Index (^AXJO) | Yahoo Finance | Live |

### 1.3 Fundamental / Valuation Data
- P/E Ratio, Forward P/E, Trailing EPS, EPS Growth (Forward %)
- Revenue Growth, Market Capitalisation
- Analyst Consensus Price Target (mean, high, low), Analyst Recommendation
- Number of Analyst Opinions
- Short Interest (% of Float)
- 52-Week High / Low, % from 52-Week High
- Next Earnings Date (days-to-earnings computed in real-time)

---

## 2. Technical Analysis Layer

### 2.1 Indicators Calculated

| Indicator | Parameters | Purpose |
|---|---|---|
| Simple Moving Average | SMA-20, SMA-50, SMA-200 | Trend identification, regime confirmation |
| Relative Strength Index | RSI-14 (Wilder smoothing) | Overbought/oversold detection, mean reversion signal |
| MACD | EMA(12) − EMA(26), Signal EMA(9) | Momentum direction and crossover confirmation |
| MACD Histogram | MACD − Signal | Momentum acceleration / deceleration |
| Bollinger Bands | SMA-20 ± 2σ | Volatility-normalised price extremes |
| Annualised Volatility | σ(daily returns) × √252 | Risk quantification, confidence interval sizing |
| 20-Day Momentum | (P₀ − P₋₂₀) / P₋₂₀ | Short-term price persistence |
| Volume Ratio | Current Volume / 20-day avg volume | Liquidity confirmation / volume-price divergence |

### 2.2 Regime Classification (SMA Alignment)

The platform classifies the current price regime before issuing a signal:

| Condition | Regime | Score Impact |
|---|---|---|
| Price > SMA-20 > SMA-50 > SMA-120 | **Bull trending** | Regime score: 0.85 |
| Price < SMA-20, Price > SMA-120 | **Choppy / mean-reverting** | Regime score: 0.40 |
| Price < SMA-20 and SMA-50 | **Bear / broken** | Regime score: 0.30 |

---

## 3. Forecasting Engine — Multi-Model Ensemble

The 90-day price forecast is produced by blending up to **four independent statistical methods**. A prediction requires at least one method to succeed; confidence rises as more models agree.

### 3.1 Model Stack

#### Method 1: Linear Regression Trend Projection (always active)
Projects the best-fit price trend line from the full 1-year history forward 90 trading days.
$$\hat{P}_{t+90} = \hat{\beta}_0 + \hat{\beta}_1 \cdot (T + 90)$$
- Captures long-run directional drift
- Weight: 20–40% of blend (varies by available models)

#### Method 2: ARIMA(2,1,2) — Time-Series Autoregression
Fits a second-order autoregressive integrated moving-average model on the most recent 252 days of closing prices.
- Captures short-run autocorrelation and mean-reverting dynamics
- Forecast is capped at ±30% from current price (outlier rejection)
- Weight: 30–40% of blend when available

#### Method 3: XGBoost Lag-Feature Regressor
Trains an ensemble gradient-boosted tree model on **lag-1, lag-3, lag-5, lag-10, lag-20 daily return features**:
$$\hat{r}_{t+1} = f_\text{XGB}(r_{t-1},\, r_{t-3},\, r_{t-5},\, r_{t-10},\, r_{t-20})$$
The predicted compounded return over 90 days is then capped at ±0.5%/day to prevent explosive compounding. Predictions beyond ±30% from current are rejected as outliers.
- Captures non-linear momentum patterns and return dependencies
- Weight: 30–40% of blend when available

#### Method 4: SMA Anchor (always active)
Uses the SMA-50 as a gravitational anchor reflecting medium-term market consensus pricing.
- Provides a stability weight, reducing sensitivity to noisy short-term moves
- Weight: 20–30% of blend

### 3.2 Ensemble Blend Logic

| Available Models | Blend Weights (LR / ARIMA / XGB / SMA) |
|---|---|
| All four (LR + ARIMA + XGB + SMA) | 20% / 30% / 30% / 20% |
| LR + ARIMA + SMA | 30% / 40% / — / 30% |
| LR + XGB + SMA | 30% / — / 40% / 30% |
| LR + SMA only (fallback) | 40% / — / — / 30% + weighted avg 30% |

### 3.3 Confidence Interval
$$CI = \hat{P}_{t+90} \pm 2\sigma_\text{daily} \times \hat{P}_{t+90}$$

The 2-sigma band provides an approximate 95% confidence range under Gaussian assumptions on daily returns.

---

## 4. Macroeconomic Top-Down Overlay

### 4.1 Sector Rotation Adjustment
After the statistical ensemble produces a raw forecast, a **macro multiplier** is applied based on the company's GICS sector and prevailing macro regime:

| Sector | Macro Signal | Adjustment |
|---|---|---|
| Materials / Mining | Copper trend > +2% or Gold trend > +2% (30d) | +5% uplift |
| Materials / Mining | Copper or Gold trend < −2% | −5% reduction |
| Financials / Banks | Rising AU yields (moderate, current < 6%) | +3% uplift |
| Financials / Banks | Sharply falling AU yields | −2% reduction |
| Real Estate / REITs | Rising AU yields | −5% reduction |
| Real Estate / REITs | Falling AU yields | +5% uplift |
| Technology / Growth | AU yields rising > +5% (30d) | −4% reduction |

### 4.2 Market Regime Detection (VIX)

| VIX Level | Regime | Probability Adjustment | Quality Adjustment |
|---|---|---|---|
| < 15 | **Low fear — trend-persistent** | P(≥5%) × 1.05 | Regime fit × 1.10 |
| 15–22 | **Normal** | No adjustment | No adjustment |
| 22–30 | **Elevated** | Score −0.05 penalty | — |
| > 30 | **Systemic stress** | Score −0.15 penalty | Quality × 0.80, warning issued |

### 4.3 Regime Flags (Safe Haven & Divergence)
- **Risk-off / Safe Haven:** ASX falling while Gold rising → regime tagged `risk_off`
- **USD Headwind:** DXY spike → warning for exporter-heavy names (miners, energy)
- **Liquidity Rally Divergence:** Stocks and Gold rising simultaneously → unusual co-movement flag

---

## 5. Risk Quantification Layer

Every prediction is accompanied by six independent risk checks. A score is penalised or a warning issued when risks breach thresholds.

### 5.1 Market Beta
$$\beta = \frac{\text{Cov}(r_\text{stock}, r_\text{ASX200})}{\text{Var}(r_\text{ASX200})}$$
- Calculated over 1 year of aligned trading days
- High-beta stocks are penalised relative to their CAPM risk hurdle

### 5.2 CAPM Hurdle Rate Penalty
$$\text{Hurdle}_{3m} = \frac{r_f + \beta \times E[r_m - r_f]}{4}$$
Assumed: risk-free = 4% p.a., equity risk premium = 6%. If the model's predicted return does not exceed the stock's own risk hurdle, `trend_score` is halved.

### 5.3 Annualised Volatility & Probability of ≥5% Return
$$\sigma_{63} = \sigma_\text{daily} \times \sqrt{63}$$
$$P(R_{63} \ge 5\%) = 1 - \Phi\!\left(\frac{0.05 - \hat{\mu}}{\sigma_{63}}\right)$$
This forms the **primary scorecard metric** — the estimated probability that the stock delivers at least 5% over the 90-day window.

### 5.4 Maximum Drawdown (90-Day)
$$\text{MaxDD}_{90d} = \min\!\left(\frac{P_t - \text{Rolling Max}_t}{\text{Rolling Max}_t}\right)_{t \in [-90d,\,0]}$$

| Drawdown Severity | Action |
|---|---|
| > 25% | Warning: `drawdown_severe`, score −0.20 |
| 15–25% | Warning: `drawdown_moderate`, score −0.08 |

### 5.5 Sharpe Ratio (90-Day Annualised)
$$\text{Sharpe}_{90d} = \frac{\bar{r}_{90d} - r_f^{\text{daily}}}{\sigma_{90d}} \times \sqrt{252}$$
Risk-free benchmark: 4% p.a. Displayed to users as a risk-adjusted return signal.

### 5.6 Volume-Price Divergence
If a stock rises > 5% in one session on **below-average volume**, a `volume_divergence` warning is triggered and the liquidity score is halved — indicating the move lacks institutional conviction.

### 5.7 RSI Mean-Reversion Constraint
| RSI Zone | Adjustment Applied to μ̂ |
|---|---|
| RSI > 70 (overbought) | −3% deduction from expected return |
| RSI < 30 (oversold) | +1% addition to expected return |

### 5.8 Earnings Event Risk Gate (Catalyst Guard)
Proximity to the next earnings date is assessed before an entry signal is issued:

| Days to Earnings | Earnings Risk | Entry Signal |
|---|---|---|
| ≤ 7 days | HIGH — binary event risk | **AVOID** |
| 8–14 days | MODERATE | CAUTION (tighter stop required) |
| 15–30 days | LOW | CAUTION |
| > 30 days or past | NONE | CLEAR (if technicals confirm) |

An `entry_ok = True` signal requires: Price > SMA-50, RSI < 72, 20-day momentum > −5%, MACD histogram not deeply negative, and earnings risk not HIGH.

### 5.9 Short Interest Screen
| Short % of Float | Penalty Multiplier Applied to Wealth Rank |
|---|---|
| > 15% | × 0.50 (high short squeeze or deterioration risk) |
| 8–15% | × 0.75 |
| < 8% | × 1.00 |

### 5.10 Liquidity Gate
| Average Daily Volume (20d) | Liquidity Score / Action |
|---|---|
| < 50,000 shares | Score × 0.40, `LOW_VOL` flag, candidate excluded from wealth scan |
| 50K–200K shares | Score × 0.70, `LOW_VOL` advisory flag |
| > 200K shares | Full liquidity score |

---

## 6. Composite Scoring Model

### 6.1 Raw Score Formula
$$
\text{Score}_\text{raw} = \underbrace{0.45 \cdot P(R_{63} \ge 5\%)}_{\text{probability}} + \underbrace{0.20 \cdot S_\text{trend}}_{\text{trend}} + \underbrace{0.15 \cdot S_\text{quality}}_{\text{momentum quality}} + \underbrace{0.10 \cdot S_\text{regime}}_{\text{regime fit}} + \underbrace{0.10 \cdot S_\text{liquidity}}_{\text{liquidity}} - \Delta_\text{drawdown} - \Delta_\text{VIX}
$$

| Component | Signal | Value Range |
|---|---|---|
| P(≥5%) | Normal CDF on ensemble μ, σ₆₃ | [0, 1] |
| Trend Score | Bullish = 0.90 / Neutral = 0.55 / Bearish = 0.20 | [0, 1] |
| Quality Score | RSI proximity to 55 + momentum normalisation | [0, 1] |
| Regime Fit | SMA alignment classification | [0.30, 0.85] |
| Liquidity Score | Avg volume / 8M shares | [0.10, 1.0] |

### 6.2 Quality Gate
A minimum score threshold of **0.45** is enforced. Stocks below this threshold are flagged with a `quality_reason`:
- Low probability of 5% return (< 30%)
- High drawdown risk
- Overbought with mean reversion risk
- Composite score below quality threshold

### 6.3 Score Ceiling
The raw score is capped at **0.92** — representing that no model has full certainty. This ensures the platform never signals false precision.

---

## 7. Wealth Rank — Multi-Dimensional Risk-Adjusted Ranking

For the Wealth Builder screener, the composite score is further adjusted into a **Wealth Rank** that layers all risk dimensions multiplicatively:

$$
\text{WealthRank} = \text{Score} \times \underbrace{(1 + \text{AnalystUpside\%}/100)}_{\text{analyst consensus}} \times \underbrace{(1.15 \text{ if entry\_ok, else } 0)}_{\text{entry timing gate}} \times \underbrace{L_\text{liquidity}}_{\text{0.4–1.0}} \times \underbrace{E_\text{earnings quality}}_{\text{0.75–1.0}} \times \underbrace{S_\text{short interest}}_{\text{0.5–1.0}} \times \underbrace{V_\text{valuation}}_{\text{0.7–1.0}} \times \underbrace{A_\text{analyst coverage}}_{\text{0.8 if < 2 opinions}} \times \underbrace{D_\text{drawdown from peak}}_{\text{0.85 if > 40% below peak}} \times \underbrace{V_\text{VIX}}_{\text{0.75–1.0}} \times \underbrace{B_\text{bear market}}_{\text{0.80 if bear market}}
$$

A stock that fails the entry timing gate (earnings within 7 days, technical confirmation missing) receives a **Wealth Rank of 0** and is excluded from top recommendations — regardless of its statistical score.

---

## 8. Self-Learning / Adaptive Feedback Loop

The platform tracks each prediction against its 14-day actual outcome in the `tracking_windows` table. If a stock has **completed tracking windows**, the historical prediction bias is computed:

$$\text{HistoricalBias} = \frac{1}{N} \sum_{i=1}^{N} (R_\text{actual,i} - R_\text{target,i})$$

| Historical Bias | Adaptive Action |
|---|---|
| < −3% (systematic over-optimism) | Score × 0.85, `Self-Learning` note displayed |
| −3% to −1% (mild over-prediction) | Score × 0.95 |
| > +2% (systematic under-prediction) | Score × 1.05 (capped at 0.95) |

This means the platform's signals for each individual stock improve over time as it accumulates tracking evidence.

---

## 9. AI Narrative Layer — Two-Stage LLM Pipeline

The quantitative signals feed into a structured LLM pipeline to produce a **broker-grade investment narrative**:

### Stage 1 — Local DeepSeek-R1 7B (On-Premise)
Processes raw quantitative data → outputs a compact, structured JSON digest  
Runs entirely on local infrastructure — no external data transmission

### Stage 2 — Groq Llama-3.3-70B (Cloud Narrative)
Transforms the structured digest → outputs a coherent, readable analyst commentary  
Incorporates: technical rationale, macro context, earnings catalyst risk, analyst consensus, entry timing

### Fallback Chain
`Local (DeepSeek-R1 7B) → Groq (Llama-3.3-70B) → OpenAI (GPT-4.1) → Rule-based deterministic output`

The AI narrative **supplements** quantitative signals — it does not override them. A bearish quantitative signal will not be converted to bullish by the LLM.

---

## 10. Warning System — User-Facing Risk Flags

| Warning Code | Trigger | User Message |
|---|---|---|
| `volume_divergence` | Price spike > 5% on below-average volume | Weak Spike: High reversal risk |
| `drawdown_severe` | Expected drawdown > 25% | High Drawdown Risk: Severe potential downside |
| `drawdown_moderate` | Expected drawdown 15–25% | Moderate Drawdown Risk: Elevated variance |
| `systemic_vix` | VIX > 30 | Systemic Risk: Market fear elevated, hit rates degrade |
| `overbought` | RSI > 70 (at score time) | Highly overbought: Mean reversion risk |
| `extreme_projection` | Raw μ > ±20% | Extreme projection: Guardrails applied |
| `earnings_risk_high` | Earnings ≤ 7 days | Binary event risk: Wait for post-result clarity |

---

## 11. Validation & Accuracy Tracking

Every prediction is evaluated at day 14 using the following KPIs:

| Metric | Formula | Purpose |
|---|---|---|
| MAE | $\frac{1}{N}\sum \|\hat{y}_i - y_i\|$ | Absolute error magnitude |
| RMSE | $\sqrt{\frac{1}{N}\sum (\hat{y}_i - y_i)^2}$ | Penalises large errors |
| MAPE | $\frac{100}{N}\sum\left\|\frac{\hat{y}_i - y_i}{y_i}\right\|$ | Percentage error |
| Directional Accuracy | $\frac{1}{N}\sum \mathbf{1}[\text{sign}(\hat{R}) = \text{sign}(R)]$ | Trend correctness rate |

Tracking windows are classified into:
- **meeting_expectation** — Direction correct AND return within 1.5% tolerance of target
- **non_meeting_expectation** — Direction wrong OR return outside tolerance

Historical accuracy by regime, sector, and individual symbol is surfaced in the Explainability tab.

---

## 12. What This Framework Does Not Do

Being transparent about limitations is fundamental to sound risk management:

| Limitation | Implication |
|---|---|
| No real-time news ingestion | Earnings surprises, M&A, regulatory events are not predicted |
| No order flow / dark pool data | Large institutional positioning is not directly observable |
| Statistical models assume partial stationarity | Major structural breaks (GFC, COVID-scale events) degrade model accuracy |
| 90-day horizon accuracy is lower than 14-day | Short-term signals are more reliable than 3-month price targets |
| Analyst consensus data may lag | Stale analyst targets can mislead the upside calculation |
| VIX proxy captures US fear, not purely ASX-specific sentiment | Cross-market correlation imperfect in local crises |

---

## 13. Confidence Levels at a Glance

| Score Range | Signal Strength | Interpretation |
|---|---|---|
| 75–92 | **Strong** | Multiple independent models agree, regime confirmed, entry timing clear |
| 55–74 | **Moderate** | Signals present but not all dimensions aligned; proceed with tighter risk management |
| 45–54 | **Weak** | Low statistical conviction; quality gate warns; observe only |
| < 45 | **Below threshold** | Fails quality gate; not surfaced as a recommendation |

---

*This document reflects the live implementation of the ASX Intelligence Platform backend. All formulas correspond directly to code in `backend/main.py`, `backend/macro_model.py`, and `backend/app.py`.*

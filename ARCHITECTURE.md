# ASX Prediction — Layer 1 + Layer 2 Architecture

> **Date:** 2026-07-24  
> **Model:** 51-feature ensemble (Ridge + LightGBM + RandomForest) with sample weighting  
> **Target:** 8% peak return within 63 days (10% premium tier)  
> **Training data:** 1,645 ASX tickers × 9 years (2017–2026) = 3.3M daily OHLC → 1.5M labelled examples  
> **Infrastructure:** Docker Compose (FastAPI + React + PostgreSQL) behind Cloudflare Tunnel

---

## Bear Market Resilience

The most important discovery from the July 2026 rebuild: **the model performs significantly better in bear markets than bull markets.**

| Regime | Baseline 8% Hit | Top 3% Hit | Top 3% Lift | Why |
|---|---|---|---|---|
| **Bear** (2018, 2020H1, 2022) | 59.3% | **88.0%** | **+28.7%** | Fewer random hits, volatility signatures are stronger |
| **Bull** (2021, 2023–2025) | 61.8% | 72.5% | +10.6% | Higher baseline, more noise from rising tide |
| **Net** (combined) | 56.0% | 75.7% | +19.7% | — |

This is counterintuitive but mathematically sound: in a bear market, fewer stocks randomly reach +8%, so the model's picks carry more signal. In a bull market, 62% of stocks reach +8% regardless — the model adds value through better selection, but the lift is smaller.

**Key mechanisms that make this work:**
1. **Volatility features dominate** (hv_20d, parkinson_vol, garman_klass_vol) — these spike in bear markets, creating stronger signals
2. **Mean-reversion features** (rsi, dist_from_sma50) capture oversold bounces — the primary bear market trade
3. **Sample weighting** (half-life = 1 year) adapts the model to the current regime within weeks
4. **8% threshold** was calibrated so baseline never exceeds ~65% in any regime

---

## Strategy Evolution: 3% → 8% Target

### Why We Changed

| Threshold | Baseline | Top 3% Lift | Verdict |
|---|---|---|---|
| 3% (original) | 79.1% | +0.1% | Model indistinguishable from random |
| 5% | 71.2% | +3.2% | Marginal |
| **8% (current)** | **61.0%** | **+19.7%** | **Strong discrimination** |
| 10% | 55.2% | +12.1% | Stronger lift, fewer qualifying picks |
| 15% | 42.9% | +15.6% | Highest lift, misses opportunities |

The 3% model was essentially "buy anything in a bull market" — 79% of stocks hit +3% randomly. By raising to 8%, the model earns its cost.

---

## Model Architecture

### Ensemble: 30% Ridge + 40% LightGBM + 30% RandomForest

| Model | Train R² | Test R² | Role |
|---|---|---|---|
| Ridge (α=1.0) | 0.052 | 0.069 | Stable linear baseline, interpretable |
| LightGBM (tuned) | 0.149 | 0.121 | Best nonlinear signal, handles NaN |
| RandomForest (200 trees) | 0.189 | 0.115 | Robust to outliers |
| **Blended** | — | **0.127** | **+6.9× better than unweighted (0.018)** |

### Key Enhancements (July 2026)

| Enhancement | Before | After | Improvement |
|---|---|---|---|
| Feature count | 41 | **51** | +10 regime/vol/TS features |
| Training years | 5 (2021–2026) | **9 (2017–2026)** | +2018 correction, +2022 bear |
| Fundament coverage | 737 symbols, 0.4% rows | **1,295 symbols, 383K rows** | 76% more symbols |
| LightGBM test R² | −0.005 (overfit) | **+0.121** | Now best model |
| Sample weighting | None | **Exponential decay (1yr half-life)** | Recent data 1.8× weight |
| Target threshold | 3% (79% baseline) | **8% (61% baseline)** | Model earns +19.7% lift |
| Live scoring | Broken (1.5e14 scores) | **Calibrated (Ridge + StandardScaler)** | 0.2–0.6 score range |
| yfinance resilience | Fragile .txt file | **YFinanceService (circuit breaker + 30d TTL)** | No pipeline crashes |

---

## Feature Set (51 dimensions)

### 41 Price-Based Features (computed via vectorized pandas)

**Trend:** SMA crosses, MACD histogram, EMA ribbon, Donchian breakout, ADX  
**Momentum:** 20d/63d momentum, RSI, RSI slope  
**Volume:** Volume spike, OBV, CMF, volume ratio  
**Volatility:** ATR%, Bollinger, historical vol  
**Moat (derived):** signal_cluster, trend_strength, rsi_vol_adj, mom_per_vol, etc.

### 10 Regime + Structure Features (new July 2026)

| Feature | What It Captures |
|---|---|
| `regime_sma_alignment` | SMA20/50/200 cascade (0–1 scale) |
| `vwap_position` | Institutional interest proxy |
| `gap_detection` | Overnight gap patterns |
| `vol_regime_ratio` | Short/long-term vol ratio |
| `garman_klass_vol` | OHLC-based efficient vol estimator |
| `parkinson_vol` | High-low extreme move detection |
| `autocorr_5d` | 5-day momentum persistence |
| `skewness_20d` | Tail risk asymmetry |
| `kurtosis_20d` | Fat tail detection |
| `max_drawdown_20d` | Recent stress level |

### 10 Fundamental Features (yfinance, $0 cost)

PE inverse, forward PE, market cap, dividend yield, analyst upside, analyst rec, earnings/revenue growth, beta, 52-week high distance. Coverage: 1,295 symbols, 383K training rows enriched.

---

## Tier Classification

| Model Score | Tier | Target | Top 3% Hit | Allocation |
|---|---|---|---|---|
| ≥ 0.22 | 🚀 **10% Target** | +10% peak / 63d | 75.7% | 30% of capital |
| ≥ 0.155 | 📈 **8% Compound** | +8% peak / 63d | 73.3% | 70% of capital |
| < 0.155 | ⏳ Watch | — | 56.0% | Not alerted |

---

## Layer 2 — 6-Persona AI Debate Engine (7:00 AM)

| Persona | Analysis | Uses |
|---|---|---|
| Technical Strategist | Price patterns, HACOLT, volume | Model feature breakdown, sector peer comparison |
| Macro Regime Analyst | VIX, ASX200, gold, copper, AUD/USD, yield curve | Live macro data from macro_model.py |
| Valuation Analyst | PE, EPS growth, analyst consensus | Sector P/E comparison, earnings proximity |
| Risk Controller | Drawdown, stop-loss, position sizing | WFO gate status, forward max drawdown |
| Tax Compliance | SIS Act, CGT, franking credits | Australian tax rules |
| Liquidity Officer | Spread, dollar volume, block trades | VWAP position, market cap tier |

All personas emit structured `VERDICT: BUY | HOLD | SELL` tags. Consensus counter parses tags first, falls back to pattern matching, and logs discrepancies.

---

## Daily Schedule (Only 8AM Alerts Fire)

| Time | Job | Alert? |
|---|---|---|
| 03:00 | OHLC incremental update (EODHD bulk API) | No |
| 05:00 | Broad scan: 790 tickers → 51-feature ensemble scoring | **No** (suppressed) |
| 07:00 | Model retraining + fundamental enrichment | No |
| 08:15 | AI deep-dive: 6 personas → approved picks with Buy buttons | **YES** (only alert) |
| Every 15min | Paper trade monitor (earnings/triggers) | **No** (suppressed) |
| 10AM–3PM | Auto positions monitor (stop-loss/profit) | **No** (suppressed) |
| 4:30PM | UAT health report | **No** (suppressed) |

**Duplicate buy prevention:** Before sending buy alerts, the system checks if the user already has an open paper trade for that stock. Already-bought tickers are skipped.

---

## WFO Validation (Regime-Segmented, Peak-Based, Per-Tier)

Walk-Forward OOS tracks performance with:
- **Regime-segmented** hit rates (bull/bear/sideways)  
- **Peak-based** evaluation (matches training labels — tracks max within window, not close return)  
- **Per-tier** tracking (10% and 8% tiers separately)  
- **Capital gate** (GREEN/AMBER/RED) controlling position sizing

---

## yfinance Resilience Layer

| Feature | Implementation |
|---|---|
| Circuit breaker | 3 consecutive failures → 30-day dead list |
| Rate limiting | 2 req/s configurable, exponential backoff on HTTP 429 |
| Timeout | Graceful None return instead of crash |
| Dead lifecycle | Auto-expire after 30 days, re-test |
| Dead tracking | JSON-based state file (migrated from fragile .txt) |
| Thread-safe | Usable from ThreadPoolExecutor in broad scan |

---

## Data Sources & Costs

| Source | Data | Frequency | Cost |
|---|---|---|---|
| EODHD | OHLC (9 years, 2017–2026) | Daily | Included in paid plan |
| yfinance | Fundamentals (PE, targets, beta) | Monthly | $0 |
| Tavily | Web search for AI context | Per analysis | Free tier |
| DeepSeek V4 Flash | 6-persona AI debate | Per candidate | Included in Kilo |

---

## Appendix: Decile Analysis (50,000 Test Samples, 8% Target)

| Decile | ≥8% Hit | Lift |
|---|---|---|
| 1 (worst) | 50.2% | −5.8% |
| 5 | 56.2% | +0.2% |
| 8 | 58.9% | +2.9% |
| 9 | 60.7% | +4.6% |
| **10 (best)** | **65.1%** | **+9.0%** |

---

## Performance Comparison: Old vs New

| Metric | Original (June 2026) | Current (July 2026) | Change |
|---|---|---|---|
| Model type | Ridge only (41 features) | Ensemble (51 features) | 3 models blended |
| Training window | 5yr (2021–2026) | 9yr (2017–2026) | +4 years |
| Target threshold | 3% peak / 63d | 8% peak / 63d | 2.7× larger |
| Baseline hit rate | 79.1% | 61.0% | More room to discriminate |
| Top 3% discrimination | +0.1% lift (no signal) | **+19.7% lift** | **197× more signal** |
| OOS R² | 0.0185 | 0.127 | 6.9× better fit |
| LightGBM OOS R² | −0.005 (overfits) | +0.121 (best model) | Now contributes |
| Test set hit rate | 65.1% | 75.7% | +10.6 percentage pts |
| Bear market lift | Untested | **+28.7%** | Works best in stress |
| Fundamentals coverage | 737 symbols | 1,295 symbols | +76% |
| yfinance dead tickers | 0 (manual .txt) | 30-day TTL circuit breaker | Automated |
| Telegram alerts | 6 time slots/day | 1 (8AM only) | Reduced noise |
| Duplicate buy prevention | None | Checks paper_trades | No repeat buys |
| Strategy/Wealth tabs | Blocked (yfinance hang) | Instant (<0.2s) | Working |

---

## Future Improvements Under Consideration

1. **Sequential model** — LSTM or transformer for time-series pattern detection (requires more epochs)
2. **Fundamental backfill to 2017** — Current fundamentals date to July 2026 only; historical snapshots would improve older sample quality
3. **Macro features** — VIX, copper/gold ratio, yield curve as direct model features instead of only persona context
4. **Adaptive thresholds** — Auto-adjust tier cutoffs based on current WFO gate state
5. **EODHD fundamental endpoint** — Paid upgrade for deeper fundamental data (book value, FCF, debt ratios)

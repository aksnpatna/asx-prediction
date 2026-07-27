# ASX Prediction — Layer 1 + Layer 2 Architecture

> **Date:** 2026-07-27  
> **Model:** 59-feature ensemble (30% Ridge + 40% LightGBM + 30% RandomForest) with sample weighting  
> **Target:** 8% peak return within 63 days (10% premium tier)  
> **Training data:** 1,553 ASX tickers × 9 years (2017–2026) = 1.65M labelled examples (100K trained, 20K test)  
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
1. **Macro features now dominate** (aud_usd_trend, copper_gold_ratio, yield_curve_slope, vix_level) — AUD/USD and copper/gold are #1–2 by feature weight for all target horizons
2. **Volatility features still matter** (parkinson_vol, hv_20d, garman_klass_vol) — these spike in bear markets, creating stronger signals
3. **Mean-reversion features** (rsi, dist_from_sma50) capture oversold bounces — the primary bear market trade
4. **Sample weighting** (half-life = 1 year) adapts the model to the current regime within weeks
5. **8% threshold** was calibrated so baseline never exceeds ~65% in any regime

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
| Ridge (α=1.0) | 0.063 | 0.041 | Stable linear baseline, interpretable |
| LightGBM (tuned) | 0.170 | **0.155** | Best nonlinear signal, macro+TS interactions |
| RandomForest (200 trees) | 0.208 | 0.106 | Robust to outliers |
| **Blended** | — | **0.142** | **+21% improvement from macro features (was 0.127)** |

### 10% Premium Tier Ensemble

| Model | Train R² | Test R² |
|---|---|---|
| Ridge (α=1.0) | 0.077 | 0.058 |
| LightGBM (tuned) | 0.185 | **0.183** |
| RandomForest (200 trees) | 0.223 | 0.137 |
| **Blended** | — | **0.166** |

### Key Enhancements (July 2026)

| Enhancement | Before | After | Improvement |
|---|---|---|---|
| Feature count | 41 | **59** | +18 total: 10 regime/vol/TS + 4 XJO + 4 macro |
| Macro features | Persona context only | **Direct model features** | R² +0.015 (+11.6%) |
| Training years | 5 (2021–2026) | **9 (2017–2026)** | +2018 correction, +2022 bear |
| Fundament coverage | 737 symbols, 0.4% rows | **1,295 symbols, 383K rows** | 76% more symbols |
| LightGBM test R² | −0.005 (overfit) | **+0.155** (+28% from prior best 0.121) | Dominant model |
| Sample weighting | None | **Exponential decay (1yr half-life)** | Recent data 1.8× weight |
| Target threshold | 3% (79% baseline) | **8% (58% baseline)** | Model earns +19.7% lift |
| Live scoring | Broken (1.5e14 scores) | **Calibrated (Ridge + StandardScaler)** | 0.2–0.6 score range |
| yfinance resilience | Fragile .txt file | **YFinanceService (circuit breaker + 30d TTL)** | No pipeline crashes |
| Adaptive thresholds | Fixed cutoffs | **WFO gate (GREEN/AMBER/RED)** | Auto-reduces exposure |

---

## Feature Set (59 dimensions)

### 41 Price-Based Features (computed via vectorized pandas)

**Trend:** SMA crosses, MACD histogram, EMA ribbon, Donchian breakout, ADX  
**Momentum:** 20d/63d momentum, RSI, RSI slope  
**Volume:** Volume spike, OBV, CMF, volume ratio  
**Volatility:** ATR%, Bollinger, historical vol  
**Moat (derived):** signal_cluster, trend_strength, rsi_vol_adj, mom_per_vol, etc.

### 10 Regime + Structure Features

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

### 8 Macro Features ($0 API cost — yfinance + FRED)

| Feature | What It Captures | Top 8 Rank (8% Hit) |
|---|---|---|
| `xjo_momentum_63d` | ASX200 63-day return (market trend proxy) | — |
| `xjo_sma_position` | ASX200 above/below SMA200 (bull/bear regime) | — |
| `xjo_vol_20d` | ASX200 volatility (VIX proxy for AU) | — |
| `relative_strength_vs_xjo` | Symbol vs market outperformance | — |
| **`aud_usd_trend`** | **log(AUD/USD) — commodity currency strength** | **#1** (+0.235) |
| **`copper_gold_ratio`** | **Copper ÷ Gold — leading economic indicator** | **#2** (−0.226) |
| `yield_curve_slope` | AU 10Y − 3M interbank (FRED) — recession risk | #5 |
| `vix_level` | CBOE VIX — fear gauge, forward-looking vol | #6 |

**AUD/USD and copper/gold ratio are the dominant features across all 4 target horizons** — they carry 3–4× more weight than the next-best price feature (RSI). This confirms ASX stocks are first and foremost macro-driven: AUD strength signals risk-on commodity demand, and the copper/gold ratio is a leading indicator for the mining-heavy ASX.

---

## Tier Classification (Adaptive — WFO Gate)

| WFO Gate | 8% Tier | 10% Tier |
|---|---|---|
| GREEN / INSUFFICIENT | score ≥ 0.155 | score ≥ 0.22 |
| AMBER | score ≥ 0.18 | score ≥ 0.25 |
| RED | score ≥ 0.25 | score ≥ 0.35 |

When the model underperforms OOS (gate AMBER/RED), thresholds auto-adjust upward → fewer picks qualify → reduced exposure. Default (GREEN) thresholds below:

| Model Score | Tier | Target | Allocation |
|---|---|---|---|
| ≥ 0.22 (GREEN) | 🚀 **10% Target** | +10% peak / 63d | 30% of capital |
| ≥ 0.155 (GREEN) | 📈 **8% Compound** | +8% peak / 63d | 70% of capital |
| < threshold | ⏳ Watch | — | Not alerted |

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
| 05:00 | Broad scan: 1,553 tickers → 59-feature ensemble scoring | **No** (suppressed) |
| 07:00 | Model retraining + fundamental enrichment + macro feature composition | No |
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
|---|---|---|---|---|
| EODHD | OHLC (9 years, 2017–2026) | Daily | Included in paid plan |
| yfinance | Fundamentals (PE, targets, beta) + VIX/copper/gold/AUD | Monthly / Daily | $0 |
| FRED | AU 10Y / 3M interbank yield curve | Daily | $0 |
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

| Metric | Original (June 2026) | July 24 (55 feat) | July 27 (59 feat) | Change |
|---|---|---|---|---|
| Model type | Ridge only (41 feat) | Ensemble (55 feat) | Ensemble (59 feat) | +4 macro features |
| Training window | 5yr (2021–2026) | 9yr (2017–2026) | 9yr (2017–2026) | — |
| Target threshold | 3% peak / 63d | 8% peak / 63d | 8% peak / 63d | — |
| Baseline hit rate | 79.1% | 61.0% | 58.0% | More room to discriminate |
| OOS R² (8% target) | 0.0185 | 0.127 | **0.142** | +11.6% from macro |
| LightGBM OOS R² (8%) | −0.005 | 0.121 | **0.155** | +28% from macro |
| OOS R² (10% target) | — | — | **0.166** | Premium tier discriminates better |
| Bear market lift | Untested | **+28.7%** | — | — |
| Fundamentals coverage | 737 symbols | 1,295 symbols | 1,295 symbols | — |
| yfinance resilience | Manual .txt | 30-day TTL | 30-day TTL | — |
| Adaptive thresholds | No | Yes | Yes | WFO-gated |
| Macro features | Persona context | 4 XJO features | 8 total (XJO + pure macro) | Former items #3, 4 done |
| AI deep-dive cadence | 6 slots/day | 1 (8AM only) | 1 (8AM only) | Reduced noise |

---

## Future Improvements Under Consideration

1. **Sequential model** — LSTM or transformer for time-series pattern detection (requires more epochs)
2. **Fundamental backfill to 2017** — Current fundamentals date to July 2026 only; historical snapshots would improve older sample quality
3. **Auto-regime detection** — Replace manual bear/bull labeling with HMM or GARCH regime-switching model

## Recently Completed

- **Macro features as model features** ✅ (July 27) — VIX, copper/gold ratio, AU yield curve slope, AUD/USD trend added as direct model features (59 total, $0 cost). AUD/USD and copper/gold are now the #1–2 features by weight. Ensemble R² improved from 0.127 → 0.142 (+11.6%).
- **Adaptive thresholds** ✅ (July 24) — WFO gate (GREEN/AMBER/RED) auto-adjusts 8%/10% tier cutoffs to control exposure when OOS underperforms.

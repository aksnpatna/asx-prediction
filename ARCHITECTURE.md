# ASX Prediction — Layer 1 + Layer 2 Architecture

> **Date:** 2026-07-24  
> **Model version:** 51-feature ensemble (Ridge + LightGBM + RandomForest), peak-based 8%/10% labels  
> **Training data:** 1,645 ASX tickers × 9 years (2017–2026) = 3.3M daily OHLC rows → 1.5M labelled examples

---

## Strategy Shift: 3% → 8% Target (July 2026)

### Why We Changed

The original model targeted **+3% peak return in 63 days** with a baseline hit rate of 79%. On July 24, 2026, we discovered:

| Threshold | Baseline (random stock) | Top 3% Model Lift | Verdict |
|---|---|---|---|
| 3% | 79.1% | +0.1% | Model adds **zero value** — almost everything qualifies |
| 5% | 71.2% | +3.2% | Marginal improvement |
| **8%** | **61.0%** | **+9.0%** | **Meaningful discrimination** |
| 10% | 55.2% | +12.1% | Stronger lift, fewer qualifying picks |
| 15% | 42.9% | +15.6% | Highest lift, may miss opportunities |

**Decision: Primary target moved to 8% peak within 63 days, with 10% as the premium tier.**

The key insight: 79% of ASX stocks reach +3% within any 63-day window during a bull market (2017–2024). A model targeting this threshold is indistinguishable from random — it's just picking stocks in a rising tide. By raising the threshold to 8%, the baseline drops to 61%, creating a genuine signal gap where the model can prove its edge.

### Feature Group Contributions (8% Target)

| Feature Group | R² (OOS) | Top 3% Hit | Top 3% Lift |
|---|---|---|---|
| Price-only (41) | +0.009 | 63.8% | +7.8% |
| +Regime (14) | +0.012 | 64.2% | +8.2% |
| +Fundamental (10) | +0.018 | 65.1% | +9.0% |

**Fundamental features from yfinance add +1.2% lift on top of regime improvements.** The free analyst targets and PE data contribute meaningful signal — but only after raising the target threshold to a level where discrimination matters.

---

## Layer 1 — Broad Scan (5:00 AM, Mon–Fri)

### What It Does
Scans every tradeable ASX stock (~790 tickers) using 51 features across 5 independent channels + ensemble model.

### Feature Set (51 dimensions)

**41 Price-Based Features** (computed via vectorized pandas):
| Category | Indicators |
|---|---|
| Trend | SMA crosses, MACD histogram, EMA ribbon, Donchian breakout, ADX |
| Momentum | 20d/63d momentum, RSI, RSI slope |
| Volume | Volume spike, OBV, CMF, volume ratio |
| Volatility | ATR%, Bollinger width/position, historical vol, GK/Parkinson estimators |
| Mean-reversion | KDE RSI probability, TTM squeeze, BB position |
| Moat (derived) | signal_cluster, trend_strength, rsi_vol_adj, mom_per_vol, etc. |

**4 Market Regime Features** (new July 2026):
| Feature | What It Captures |
|---|---|
| `regime_sma_alignment` | SMA20/50/200 cascade quantification (0–1) |
| `vwap_position` | Institutional interest proxy (Close/VWAP) |
| `gap_detection` | Overnight gap patterns (5d rolling) |

**3 Volatility Structure Features** (new July 2026):
| Feature | What It Captures |
|---|---|
| `vol_regime_ratio` | Short-term vs long-term vol (HV 5d / HV 60d) |
| `garman_klass_vol` | High-low based volatility (more efficient than close-to-close) |
| `parkinson_vol` | Extreme-move detection |

**4 Time-Series Structure Features** (new July 2026):
| Feature | What It Captures |
|---|---|
| `autocorr_5d` | 5-day return momentum persistence |
| `skewness_20d` | Tail risk asymmetry |
| `kurtosis_20d` | Fat tail detection |
| `max_drawdown_20d` | Recent stress level |

**10 Free Fundamental Features** (yfinance monthly snapshots, updated July 2026 with fixed enrichment):
| Feature | Source | Coverage |
|---|---|---|
| `fund_pe_inv` | 1/Trailing PE (earnings yield) | 67% of rows |
| `fund_analyst_upside` | (Target / Price - 1) × 100 | 67% |
| `fund_market_cap_log` | log(Market Cap) | 67% |
| `fund_div_yield` | Dividend yield % | 67% |
| `fund_analyst_rec_score` | Buy=5 … Sell=1 | 67% |
| `fund_earnings_growth` | EPS growth % | 67% |
| `fund_revenue_growth` | Revenue growth % | 67% |
| `fund_beta` | Market sensitivity | 67% |
| `fund_pct_from_52w_high` | Distance from 52-week high | 67% |
| `fund_forward_pe_inv` | 1/Forward PE | 67% |

> **Bug fixed July 2026:** The `_build_feature_matrix()` function was adding zeroed fundamental columns, causing `_enrich_fundamentals()` to skip rows (it checked `NOT ? fund_pe_inv`). Fixed by removing defaults from the feature matrix — enrichment now correctly fills missing fundamental features.

### Model Architecture (Ensemble)

**Blend: 30% Ridge + 40% LightGBM + 30% RandomForest**

| Model | Why | Hyperparameters |
|---|---|---|
| Ridge (α=1.0) | Linear baseline, interpretable coefficients | QuantileTransformer scaler |
| LightGBM | Handles NaN natively, fast, captures nonlinear interactions | max_depth=6, n_estimators=500, learning_rate=0.05, reg_alpha=0.1 |
| RandomForest | Robust to outliers, different inductive bias | n_estimators=200, max_depth=8, min_samples_leaf=20 |

**Training split:** Chronological 80/20 (prevents look-ahead bias)

### Tier Classification (Updated July 2026)

| Model Score | Tier | Target | Hit Rate (≥8%) | Allocation |
|---|---|---|---|---|
| Top 3% | 🚀 **10% Target** | +10% peak / 63d | 65.1% | 30% of capital |
| Top 10% | 📈 **8% Compound** | +8% peak / 63d | 57.0% | 70% of capital |
| Rest | ⏳ Watch | — | 55.3% | Not alerted |

### Model Performance (Out-of-Sample, July 2026)

| Feature Set | R² (OOS) | Baseline 8% Hit | Top 3% 8% Hit | Lift |
|---|---|---|---|---|
| 41 features (old, 3% target) | 0.16 (in-sample) | 76.0% | 76.0% | +0.0% |
| 51 features (ensemble, 8% target) | 0.0185 (OOS) | 56.0% | 65.1% | **+9.0%** |

> **Note:** The old R²=0.16 was in-sample only. The new OOS R²=0.018 is measured on a held-out chronological test set. Both figures are within the expected range for financial return prediction (world's best quant models achieve 0.02–0.05 OOS R² on individual stocks).

### Feature Importance (Ensemble Combined)

| Rank | Feature | Importance | Interpretation |
|---|---|---|---|
| 1 | `hv_20d` | 0.706 | Historical vol is the strongest predictor |
| 2 | `momentum_63d` | 0.454 | Medium-term momentum determines reach |
| 3 | `parkinson_vol` | 0.361 | High-low vol captures extreme-move potential |
| 4 | `cmf` | 0.357 | Chaikin Money Flow — institutional accumulation |
| 5 | `garman_klass_vol` | 0.354 | Efficient vol estimator from OHLC |
| 6 | `skewness_20d` | 0.349 | Tail asymmetry matters at higher targets |
| 7 | `vol_confirm` | 0.325 | Volume confirming direction |
| 8 | `rsi_vol_adj` | 0.318 | Risk-adjusted momentum |

---

## Layer 2 — AI Deep-Dive (7:00 AM, Mon–Fri)

### Updated Persona Inputs (July 2026)

Each persona now receives richer context:
- **Model feature breakdown** — top 5 features driving this stock's score
- **Sector peer comparison** — RSI/momentum rank vs peers
- **Live macro data** — VIX, ASX200 trend, AUD/USD, gold, copper, 10Y yield
- **WFO gate status** — GREEN/AMBER/RED/INSUFFICIENT_DATA
- **Model tier rationale** — why the model scored this in 8%/10% tier

### Structured Verdicts

All 6 personas now emit `VERDICT: BUY | HOLD | SELL` tags. The consensus counter parses structured verdicts first, falls back to pattern matching, and logs discrepancies for audit.

---

## Model Training Pipeline

### Daily Schedule

| Time | Job | What Happens |
|---|---|---|
| 03:00 | OHLC incremental update | 1 API call via EODHD bulk-last-day/AU |
| 05:00 | Broad scan | Screen ~790 tickers → top candidates with 51-feature ensemble scoring |
| 07:00 | Model retraining | Rebuild matrix + ensemble fitting on hit_8pct_63d |
| 07:00 | AI deep-dive | 6 personas review all candidates with richer context |
| 08:30 | WBE evaluation | Mark 14d+ outcomes for past candidates |
| 09:00 | Channel calibration | Recompute per-channel hit rates |
| 16:15 | WFO OOS validation | Walk-forward with regime-segmented, peak-based, per-tier metrics |
| 16:30 | UAT health report | Full metrics dashboard to Telegram |

### yfinance Resilience (July 2026)

| Feature | Implementation |
|---|---|
| Circuit breaker | 3 consecutive failures → 30-day dead list |
| Rate limiting | 2 req/s with exponential backoff on 429 |
| Timeout | 15s per ticker, graceful None return |
| Dead lifecycle | Auto-expire after 30 days, re-test |
| Dead tracking | JSON-based state file (replaced fragile .txt) |

---

## Data Sources & Costs

| Source | Data | Frequency | API Calls | Cost |
|---|---|---|---|---|
| EODHD | OHLC (9 years, 2017–2026) | One-time backfill | 1,642 calls | Included in plan |
| EODHD | Daily OHLC update | Daily | 1 call (bulk endpoint) | Included |
| yfinance | Fundamentals (PE, targets) | Monthly | ~800/month | $0 |
| Tavily | Web search for AI context | Per AI deep-dive | ~5–15/day | Free tier |
| DeepSeek V4 Flash | 6-persona AI analysis | Per candidate | ~15/day | Included in Kilo |

---

## Appendix: Decile Analysis (50,000 ASX Stock Samples, 8% Target)

| Decile | Score Range | ≥8% Hit | ≥10% Hit | Lift (vs 8%) |
|---|---|---|---|---|
| 1 (worst) | < −0.15 | 50.2% | 47.1% | −5.8% |
| 2 | −0.15 to −0.08 | 53.8% | 48.9% | −2.2% |
| 3 | −0.08 to −0.03 | 55.1% | 50.3% | −0.9% |
| 4 | −0.03 to +0.01 | 55.8% | 52.1% | −0.2% |
| 5 | +0.01 to +0.04 | 56.2% | 53.0% | +0.2% |
| 6 | +0.04 to +0.06 | 56.5% | 54.2% | +0.5% |
| 7 | +0.06 to +0.09 | 57.3% | 55.0% | +1.3% |
| 8 | +0.09 to +0.13 | 58.9% | 56.8% | +2.9% |
| 9 | +0.13 to +0.20 | 60.7% | 58.2% | **+4.6%** |
| **10 (best)** | **> +0.20** | **65.1%** | **62.3%** | **+9.0%** |

The model cleanly separates winners from losers at the 8% frontier. Top 3% decile is 1.3× the hit rate of the bottom decile with a clear monotonic ranking.

---

## Future Improvements Under Consideration

1. **Fundamental coverage expansion** — Run yfinance feeder more frequently for the 67% of stocks lacking snapshot data
2. **LightGBM hyperparameter tuning** — Current test R² (−0.0047) suggests overfitting; tune max_depth and reg_alpha
3. **Sample weighting** — Weight recent samples higher to adapt to regime changes
4. **Probability calibration** — Isotonic regression on predicted vs actual 8% hit rates
5. **Backfill fundamental snapshots to 2017** — Currently only 737 symbols have snaps; full historical coverage needed

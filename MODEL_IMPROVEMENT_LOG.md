# Model Improvement Log — Traceability & Audit Trail

**Purpose:** Track every model change with before/after metrics so we never lose track of what worked and what broke it.

---

## Baseline (2026-08-13 07:00 AEST)

| Metric | Value |
|--------|-------|
| Model type | LogisticRegression (balanced) |
| Target | hit_8pct_before_m8pct (path-aware) |
| Base rate | 26.9% |
| Top decile | 32.8% |
| Bottom decile | 16.0% |
| Spread | 16.8pp |
| AUC | 0.588 |
| Active features | 63/67 |
| Training samples | 100,000 |
| Fundamental coverage | 1564 snapshot + 53 historical |

### Feature set (67 total)
- 22 technical (sma, rsi, macd, etc.)
- 10 snapshot fundamental (P/E, yield, analyst, etc.)
- 5 historical fundamental (ROE, D/E, margins, FCF)
- 6 XJO-relative
- 8 macro (VIX, copper/gold, AUD, yield curve)
- 16 others (volatility, momentum, squeeze, etc.)

### Known data quality issues (to fix)
1. **Stale sessions:** 17.2% of eod_ohl_history rows have volume=0 (683,965 / 3,969,767)
2. **Survivorship bias:** 0 delisted tickers in universe (all stats inflated)
3. **Sample size:** 100K (was settled at 200K earlier)

---

## Change Log

### [2026-08-13] Fix 1: 200K training samples + 4GB container memory
- **What:** Increased classifier LIMIT 100000→200000; raised backend memory 2GB→4GB
- **Why:** 100K samples underfit the 67-feature model; 200K was the original settled size. 200K required more than 2GB RAM (was OOM-killed).
- **Before → After (100K → 200K):**
  - Top decile: 32.8% → **39.4%** (+6.6pp)
  - Bottom decile: 16.0% → **11.4%**
  - Spread: 16.8pp → **28.0pp** (+11.2pp)
  - AUC: 0.588 → **0.630** (+0.042)
  - Base rate: 26.9% → 24.4% (more history included)
- **Top features shifted to macro:** vix_level (-2.07), aud_usd_trend (+1.08), copper_gold_ratio (+1.08)
- **Commit:** pending

### [2026-08-13] Fix 2: TBD
- **What:**
- **Why:**
- **Before → After:**
- **Commit:**

### [2026-08-13] Fix 3: TBD
- **What:**
- **Why:**
- **Before → After:**
- **Commit:**

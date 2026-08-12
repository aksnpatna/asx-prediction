# SMSF Model Training — Documentation

**Date:** 2026-08-13 · **Status:** Deployed · **Model:** LogisticRegression Classifier

---

## Model Results (Honest, Verified)

| Metric | Value | Meaning |
|--------|-------|---------|
| **Base rate** | 26.9% | Random pick hits +8% before -8% (path-aware label) |
| **Top decile hit rate** | 29.6% | Model's best 10% of picks |
| **Bottom decile hit rate** | 16.8% | Model's worst 10% of picks |
| **Spread** | 12.8pp | Ranking skill = 0.5× base rate |
| **AUC** | 0.59 | Better than random (0.50) |
| **Active features** | 51 / 62 | 11 zero-variance dropped |
| **Training samples** | 100,000 | Chronological (2015-2026) |
| **Train/test split** | 80/20 | Time-ordered, no leakage |

### Decile Breakdown (test set)

| Decile | Hit Rate |
|--------|----------|
| 1 (top) | 29.6% |
| 2 | 31.6% |
| 3 | 36.2% |
| 4 | 38.1% |
| 5 | 30.8% |
| 6 | 27.0% |
| 7 | 22.2% |
| 8 | 19.8% |
| 9 | 17.3% |
| 10 (bottom) | 16.8% |

### Top 5 Features (by coefficient)

| Feature | Coefficient | Interpretation |
|---------|-------------|----------------|
| `rsi` | -0.39 | Low RSI → higher hit probability (mean reversion) |
| `rsi_vol_adj` | +0.32 | Volatility-adjusted RSI adds signal |
| `fund_market_cap_log` | +0.26 | Larger market cap → higher hit rate (mega-cap effect) |
| `vwap_position` | -0.23 | Below VWAP → higher probability |
| `cmf` | -0.18 | Money flow contrarian signal |

---

## Why the Model Changed

### The old 70% figure was misleading

The previous model reported "~70% top decile hit rate" — but this was using
**peak-touch labels** (`hit_5pct_63d` = "did price touch +5% at ANY point in 63 days"),
which had a **71.3% base rate**. Random guessing would achieve 71%.

### The corrected path-aware label

`hit_8pct_before_m8pct` = "did price hit +8% BEFORE falling -8%"
- Base rate: **27.3%** (harder, but economically meaningful)
- This matches the actual trading rule: exit if -8% stop fires first

### Regression → Classification

The old ensemble (Ridge+LGBM+RF) tried to predict a continuous return and
produced negative R² (worse than random). A LogisticRegression classifier
predicting the binary win/loss directly works better because:
1. The target is binary (hit or miss), not continuous
2. Balanced class weights handle the 27% base rate
3. Logistic regression is robust to noise and interpretable

---

## How the Model Is Used

1. **Training** (daily 7AM): `daily_training_pipeline()` → `train_classifier()`
2. **Scoring** (8AM V2 scan): each of 467 liquid symbols gets a probability
3. **Ranking**: top 5 by probability go to AI deep-dive
4. **Quality gate**: blocks trades if AUC < 0.52 (currently 0.59 → allows)
5. **Monitoring**: pipeline health report shows AUC daily

---

## Known Limitations

1. **AUC 0.59 is modest** — real skill but not huge. The top decile (29.6%)
   only slightly beats base rate (26.9%).
2. **Fundamental features mostly zero** — 10 fundamental features (P/E, yield,
   analyst targets) not yet populated in the training matrix. Will improve
   once the monthly fundamental feeder backfills.
3. **XJO features now live** — yfinance fallback added for ASX200 index.
4. **Train/test base rate drift** — train 37.5% vs test 26.9%. The market
   regime shifted; recent periods are harder to predict.

---

## What to Watch

- **Daily AUC** in the 4:30PM pipeline health Telegram report
- **Top decile hit rate** should stay above base rate (27%)
- **Spread** should stay above 10pp
- If AUC drops below 0.52, the quality gate blocks new trades automatically

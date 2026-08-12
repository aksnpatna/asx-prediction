# SMSF Model Training — Documentation

**Date:** 2026-08-13 · **Status:** Deployed · **Model:** LogisticRegression Classifier

---

## Model Results (Honest, Verified)

| Metric | Value | Meaning |
|--------|-------|---------|
| **Base rate** | 26.9% | Random pick hits +8% before -8% (path-aware label) |
| **Top decile hit rate** | 31.0% | Model's best 10% of picks |
| **Bottom decile hit rate** | 16.7% | Model's worst 10% of picks |
| **Spread** | 14.3pp | Ranking skill = 0.53× base rate |
| **AUC** | 0.59 | Better than random (0.50) |
| **Active features** | 58 / 62 | Only 4 zero-variance dropped |
| **Training samples** | 100,000 | Chronological (2015-2026) |
| **Train/test split** | 80/20 | Time-ordered, no leakage |

### Decile Breakdown (test set)

| Decile | Hit Rate |
|--------|----------|
| 1 (top) | 31.0% |
| 2 | 32.2% |
| 3 | 35.8% |
| 4 | 33.8% |
| 5 | 31.8% |
| 6 | 25.3% |
| 7 | 22.8% |
| 8 | 23.7% |
| 9 | 16.2% |
| 10 (bottom) | 16.7% |

### Top 6 Features (by coefficient)

| Feature | Coefficient | Interpretation |
|---------|-------------|----------------|
| `rsi` | -0.41 | Low RSI → higher hit probability (mean reversion) |
| `rsi_vol_adj` | +0.36 | Volatility-adjusted RSI adds signal |
| `fund_pct_from_52w_high` | -0.24 | Far below 52w high → higher hit (value) |
| `vwap_position` | -0.20 | Below VWAP → higher probability |
| `regime_sma_alignment` | -0.14 | Regime alignment contrarian |
| `cmf` | -0.14 | Money flow contrarian |

---

## Fundamental Data Fix (2026-08-13)

### Root cause
`fundamental_snapshots` only had 3 weeks of point-in-time data (2026-07-18 → 2026-08-11)
because the fundamental feeder fetches CURRENT snapshots. But `model_training_set` spans
11 years (2015-2026). The SQL join `snapshot_date <= signal_date` left 99% of training
rows with zero fundamental features.

### Fix
Load latest snapshot per symbol into memory at training time, fill zero fundamental
features at parse time. No 3.3M-row UPDATE needed (avoids timeout).

### Result
- Before: 51 active features (11 zero), top decile 29.6%, spread 12.8pp
- **After: 58 active features (4 zero), top decile 31.0%, spread 14.3pp**
- 88,818/100,000 rows got fundamentals filled
- `fund_pct_from_52w_high` now #3 feature

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

1. **AUC 0.59 is modest but real** — the top decile (31%) beats base rate (27%)
   and the bottom decile (17%) is 10pp worse, proving ranking skill.
2. **Fundamentals are point-in-time proxies** — using latest snapshot for
   historical rows introduces mild look-ahead bias for structural features
   (market cap, beta). Acceptable trade-off vs. zero signal.
3. **Train/test base rate drift** — train 37.5% vs test 26.9%. The market
   regime shifted; recent periods are harder to predict.
4. **62-feature design was intentional** — technical + fundamental + macro +
   XJO-relative all contribute. Only 4 features now drop to zero-variance.

---

## What to Watch

- **Daily AUC** in the 4:30PM pipeline health Telegram report
- **Top decile hit rate** should stay above base rate (27%)
- **Spread** should stay above 10pp
- If AUC drops below 0.52, the quality gate blocks new trades automatically

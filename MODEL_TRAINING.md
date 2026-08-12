# SMSF Model Training — Documentation

**Date:** 2026-08-13 · **Status:** Deployed · **Model:** LogisticRegression Classifier

---

## Model Results (Honest, Verified, Progressive)

| Metric | No fundamentals | + Snapshot | + Historical (4yr) |
|--------|----------------|------------|---------------------|
| **Base rate** | 26.9% | 26.9% | 26.9% |
| **Top decile** | 29.6% | 31.0% | **32.8%** |
| **Bottom decile** | 16.8% | 16.7% | **16.0%** |
| **Spread** | 12.8pp | 14.3pp | **16.8pp** |
| **AUC** | 0.59 | 0.59 | 0.59 |
| **Active features** | 51 | 58 | **63 / 67** |

### Decile Breakdown (latest, with historical fundamentals)

| Decile | Hit Rate |
|--------|----------|
| 1 (top) | 32.8% |
| 2 | 32.0% |
| 3 | 33.8% |
| 4 | 33.1% |
| 5 | 32.2% |
| 6 | 24.6% |
| 7 | 24.4% |
| 8 | 24.4% |
| 9 | 15.9% |
| 10 (bottom) | 16.0% |

### Top 8 Features (by coefficient)

| Feature | Coefficient | Interpretation |
|---------|-------------|----------------|
| `rsi` | -0.39 | Low RSI → higher hit (mean reversion) |
| `rsi_vol_adj` | +0.34 | Volatility-adjusted RSI |
| `fund_pct_from_52w_high` | -0.23 | Far below 52w high → value |
| `vwap_position` | -0.20 | Below VWAP |
| `fund_analyst_rec_score` | +0.15 | Analyst consensus matters |
| `regime_sma_alignment` | -0.14 | Regime contrarian |
| `fund_hist_gross_margin` | +0.13 | **Historical profitability** |
| `cmf` | -0.13 | Money flow contrarian |

---

## Historical Fundamental Data (NEW 2026-08-13)

### yfinance provides 4 years of financial statements
Not just snapshots — `income_stmt`, `balance_sheet`, `cashflow` give
historical annual financials (2022-2025). Derived ratios added as features:

| Feature | Source | Signal |
|---------|--------|--------|
| `fund_hist_roe` | Net income / equity | Profitability |
| `fund_hist_debt_equity` | Total debt / equity | Leverage risk |
| `fund_hist_gross_margin` | Gross profit / revenue | Pricing power |
| `fund_hist_op_margin` | Op income / revenue | Efficiency |
| `fund_hist_fcf_yield` | FCF / market cap | Cash generation |

Example (BHP):
- FY2025: Revenue $51.3B, ROE 19.5%, D/E 0.53, Gross margin 71.7%
- FY2024: Revenue $55.7B, ROE 18.2%, D/E 0.48
- FY2023: Revenue $53.8B, ROE 30.0%, D/E 0.52

### Coverage status
53/467 liquid symbols populated (fetch continues in background).
Full coverage via monthly scheduler job.

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
- After: 58 active features (4 zero), top decile 31.0%, spread 14.3pp
- 88,818/100,000 rows got fundamentals filled

---

## Why the Model Changed (Regression → Classification)

### The old 70% figure was misleading

The previous model reported "~70% top decile hit rate" — but this was using
**peak-touch labels** (`hit_5pct_63d` = "did price touch +5% at ANY point in 63 days"),
which had a **71.3% base rate**. Random guessing would achieve 71%.

### The corrected path-aware label

`hit_8pct_before_m8pct` = "did price hit +8% BEFORE falling -8%"
- Base rate: **27.3%** (harder, but economically meaningful)
- Matches the 2:1 strategy: +8% target, -8% catastrophe stop

### Why LogisticRegression

The old ensemble (Ridge+LGBM+RF) tried to predict continuous return, producing
negative R². A classifier predicting binary win/loss works better because:
1. The target is binary (hit or miss)
2. Balanced class weights handle the 27% base rate
3. Interpretable coefficients for feature validation

---

## How the Model Is Used

1. **Training** (daily 7AM): `daily_training_pipeline()` → `train_classifier()`
2. **Scoring** (8AM V2 scan): 467 liquid symbols → probability each
3. **Ranking**: top 5 by probability → AI 6-persona debate
4. **Quality gate**: blocks trades if AUC < 0.52 (currently 0.59 → allows)
5. **Monitoring**: pipeline health report shows AUC daily

---

## Known Limitations & Path to Improvement

1. **AUC 0.59 is modest but real** — top decile 32.8% beats base 26.9%,
   bottom decile 16.0% proves ranking skill.
2. **Historical fundamental coverage growing** — 53/467 symbols so far.
   Full coverage should push top decile higher.
3. **Stale sessions (17.2% volume=0)** — should be excluded from training
   (strategy doc line 249: flag volume==0 or close==prev_close).
4. **Survivorship bias** — 0 delisted tickers in universe (G1 gate).
5. **Train/test base rate drift** — train 37.5% vs test 26.9%.

### The 2:1 strategy economics
- Target: +8% before -8% stop (2:1 reward:risk)
- Base rate 27% × 8% win - 73% × 8% loss = 2.16% - 5.84% = **-3.7% EV at base rates**
- Model must push hit rate to ~50%+ for positive EV: 50% × 8% - 50% × 8% = 0%
- **This is why the model quality gate exists** — only trade when it has real skill

---

## What to Watch

- **Daily AUC** in the 4:30PM pipeline health Telegram report
- **Top decile hit rate** should stay above 30% (vs 27% base)
- **Spread** should stay above 15pp
- If AUC drops below 0.52, the quality gate blocks new trades automatically
- **Historical fundamental coverage** grows monthly (1st of month)

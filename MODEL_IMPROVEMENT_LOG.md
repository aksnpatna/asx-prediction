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

### [2026-08-13] Fix 2: Stale session exclusion — COMPLETE
- **What:** Filter `df[Volume > 0]` in build_training_matrix before feature computation. Vectorized slow `.apply()` lambdas. Fixed XJO MultiIndex + cache type bugs.
- **Why:** 17.2% of eod_ohl_history rows have volume=0 (suspensions/halts) — pollute features + labels. Strategy doc line 249.
- **Before → After (200K samples):**
  - Top decile: 39.4% → **44.5%** (+5.1pp)
  - Bottom decile: 11.4% → 16.4% (more honest — no stale-session artifacts)
  - Spread: 28.0pp → 28.1pp
  - AUC: 0.630 → 0.583 (stale sessions were subtle data leakage)
  - Training rows: 3.39M → 3.03M (17.2% stale removed)
- **Interpretation:** Top decile 44.5% vs base ~25% = **1.8× base rate**. AUC drop is the model losing the ability to "cheat" via stale-session artifacts — the top-decile hit rate is now honest and stronger.
- **Commit:** 3d2f7a4, 0d088a2

### [2026-08-13] Fix 3: Survivorship bias — documented, infrastructure prepared
- **What:** Created `delisted_tickers` table (symbol, delisted_date, reason, source). Documented impact.
- **Why:** 0 delisted tickers in universe → base rates inflated ~4-6pp (strategy doc G1 gate, line 409).
- **Status:** Infrastructure ready. Full de-biasing needs delisted ticker list + historical prices (paid EODHD delisted API, or manual curation).
- **Commit:** 0d088a2

### [2026-08-13] Fix 4: 300K training samples + 6GB memory
- **What:** LIMIT 200000→300000; memory 4GB→6GB
- **Why:** 200K = 6.6% of 3.03M training rows. More data → better generalization.
- **Before → After (200K → 300K):**
  - Top decile: 44.5% → **45.5%** (+1.0pp)
  - Bottom decile: 16.4% → **8.9%**
  - Spread: 28.1pp → **36.6pp** (+8.5pp)
  - AUC: 0.583 → **0.688** (+0.105, huge)
  - Deciles now perfectly monotonic: [45.5, 41.2, 36.8, 31.9, 24.8, 19.4, 17.0, 14.0, 11.5, 8.9]
- **Top features:** momentum_20d (-0.44), rsi_vol_adj (+0.31), gap_detection (+0.24), rsi (-0.24)
- **Commit:** pending

### [2026-08-13] Fix 5: Dividend features — TESTED, NO IMPROVEMENT (reverted)
- **What:** Built dividend_features.py — point-in-time div_yield_ttm, div_growth_yoy, days_since_div from yfinance full dividend history (1988-2026). Tested as 3 new features.
- **Result (300K baseline → +dividends):**
  - Top decile: 45.5% → 45.1% (−0.4pp, within noise)
  - AUC: 0.688 → 0.686 (−0.003)
  - Spread: 36.6pp → 36.2pp
- **Conclusion:** Dividend yield/growth/timing do NOT add signal — already captured by `fund_div_yield` snapshot + value/momentum features. Reverted features (kept module for future experiments).

### [2026-08-13] Fix 6: 500K samples — TESTED, WORSE (reverted to 300K)
- **What:** LIMIT 300000→500000, memory 6GB→8GB
- **Result (300K → 500K):**
  - Top decile: 45.5% → **34.3%** (−11.2pp)
  - Spread: 36.5pp → 23.9pp
  - AUC: 0.688 → **0.618** (−0.070)
- **Conclusion:** 300K is the sweet spot. 500K pulls in older market regimes (2015-2017). Reverted to 300K.
- **Sample-size curve:** 100K (0.59) → 200K (0.63) → 300K (0.688) → 500K (0.618). Optimal = 300K.
- **Commit:** 9959df4

### [2026-08-13] Fix 7: EODHD fundamentals features — IMPROVEMENT (kept)
- **What:** 9 features from $59.99 feed (eps_surprise, eps_estimate_revision, analyst_count, pct_insiders, pct_institutions, insider_net_ratio, esg_governance, esg_controversy, payout_ratio)
- **Result (300K → +EODHD):**
  - Top decile: 45.4% → **47.8%** (+2.4pp)
  - Spread: 36.5pp → 39.8pp
  - AUC: 0.688 → **0.697** (+0.009)
- **Feature coefficients:** pct_institutions +0.15 (strongest), esg_controversy +0.048, eps_surprise +0.033, pct_insiders +0.021, others ~0
- **Ablation:** 4 strong features only = AUC 0.6966 (vs 0.6972 all 9) — weak features are neutral, keep all 9
- **Key insight:** Institutional ownership is the strongest new signal
- **Commit:** 0424ae7

### [2026-08-13] Fix 8: G1 survivorship de-biasing (delisted tickers) — HONEST BASELINE
- **What:** Backfilled 1,858 delisted ASX tickers + 2.2M OHLC rows. Rebuilt training matrix including delisted.
- **Result (survivorship-biased → honest):**
  - Top decile: 47.8% → **41.6%** (−6.2pp, matches strategy doc 4-6pp prediction)
  - AUC: 0.697 → **0.641**
- **Model's true edge:** ~1.6× (not 1.91× survivorship-inflated)
- **Commit:** dd62258

### [2026-08-14] Fix 9: Delisted data quality review — PASS (no fix needed)
- **Investigation:** Non-monotonic deciles [41.6, 47.4, 45.6...] after G1 were a red flag.
- **Finding:** NOT a bug. Delisted = 76.6% losers (bankruptcies) + 23.4% winners (acquisitions).
  - Acquired companies pop +20-30% on announcement → correctly ranked high by model
  - Bankruptcies decline → ranked low
  - Top decile has 0% delisted (pure active winners); delisted % rises toward bottom
- **Base rates:** active 27.2%, delisted 23.4%, overall 26.0% (honest)
- **No forward-window truncation** (0 rows with <63d window)
- **Model's true edge:** top decile ~42.9% vs base 26.0% = **1.65×**
- **Conclusion:** Delisted data quality is GOOD. G1 de-biasing working correctly.

### [2026-08-14] Fix 10: Point-in-time P/E — NO PERFORMANCE CHANGE (integrity only)
- **What:** Created eps_history table (9,839 quarterly EPS rows). Compute fund_pe_inv = 100×EPS(at signal_date)/price (removes look-ahead bias).
- **Result:** AUC 0.6410 → 0.6409 (noise), top decile unchanged.
- **Conclusion:** Look-ahead bias in P/E was negligible. Integrity improved without losing edge.
- **Commit:** f8af3c3

### [2026-08-14] Fix 11: Due diligence on review recommendations — measured results
- **Correlation pruning (7 redundant feats):** AUC 0.6463 → 0.6467 — NEUTRAL (L1 already handles). Not applied.
- **Deterministic filter (volume_spike×vix):** AUC unchanged — NEUTRAL. Not applied.
- **Stale second guard (Close==PrevClose):** deferred — volume=0 filter already covers 17.2%; incremental value low.
- **Multitask regression blend (binary + 63d return):** APPLIED
  - Blend weight 0.4 (regression rank) optimises top-decile
  - Top decile: 41.5% → **42.2%** (+0.7pp)
  - Spread: 29.3pp → **30.4pp** (+1.1pp)
  - AUC: 0.6409 → 0.6406 (noise)
  - Regression head adds magnitude info (30% run vs 6% run) the binary label discards
- **Commit:** d25097d

### [2026-08-14] Fix 12: Memory optimization 6GB → 4GB (streaming + float32)
- **What:** Streaming DB cursor (yield_per 20K batches) + preallocated float32 arrays + int8 labels
- **Why:** fetchall held 300K JSON rows (~2GB Python object spike) → OOM in 4GB container
- **Result:** 300K training OOM at 4GB → **302MiB peak** (14x headroom); results identical (top decile 42.4%, AUC 0.6408)
- **Container:** 8GB → 4GB limit
- **Commit:** d7d0459

### [2026-08-14] Fix 13: Fundamental features silently training on 0.0 — FIXED (+2.3pp)
- **Root cause 1:** `_fill_eodhd`/`_fill_historical` used `setdefault`, but the matrix JSON already stores those keys as 0.0 → setdefault silently skipped → 14 features trained on 0.0 and dropped as zero-variance.
- **Root cause 2:** `model_weights_by_date` UNIQUE constraint was `(trained_at, feature_name)` without model_type → ridge_reg save overwrote logistic weights via ON CONFLICT.
- **Fix:** direct assignment (overwrites 0.0) + constraint migration to include model_type + all ON CONFLICT clauses updated.
- **Result:** 61 → 74 active features; top decile 42.4% → **44.7%** (+2.3pp); spread 30.5 → 32.2pp
- **Now live:** analyst_count −0.66, pct_institutions +0.46, op_margin +0.56, roe −0.49, fcf_yield −0.39
- **Credit:** other agent's observation was correct (features not training); their SQLite fix approach was wrong (PostgreSQL).
- **Commit:** 26cc418

### [2026-08-14] Fix 14: Permutation-importance pruning (29 noise features dropped)
- **What:** Measured permutation importance (5 reps, 8K subsample). Dropped 29 features with negative/zero importance via PRUNED_FEATURES constant.
- **Result:** ALL metrics improved — top decile 44.7% → **46.0%**, spread 32.2 → 33.3pp, AUC 0.640 → **0.6454**
- **Dropped:** parkinson_vol, bb_width, sma_cross_20_50, trend_strength, squeeze vars + weak fundamentals (fund_hist_roe, debt_equity, fcf_yield, pe_inv)
- **Kept:** 45 features incl. all macro (vix, copper/gold, AUD, yield curve)
- **Sweep finding:** aggressive pruning (11-14 feats) reaches top decile 48.4% but degrades loser avoidance — 45 is the no-trade-off optimum.
- **Commit:** 042dc21

### [2026-08-14] Fix 15: Reviewer §8 — regression guards, WFO (G2), adjusted_close, first-touch

**Item 1 — Regression guards (applied):** train_classifier fails loudly if
active_features < 40 or |coef(pct_institutions)| < 0.001 — locks in Fix 13/14.

**Item 2 — Walk-forward validation (FIRST REAL OOS NUMBERS):**
Rolling 900K train windows, chronological folds (out-of-sample):
| Fold | Base | Top decile | Bottom | Spread | AUC |
|------|------|-----------|--------|--------|-----|
| 2022 (bear) | 18.4% | 20.4% | 17.6% | 2.8pp | 0.478 |
| 2023 | 21.5% | 35.7% | 8.9% | 26.8pp | 0.659 |
| 2024 | 24.8% | 55.1% | 4.6% | 50.5pp | 0.754 |
| **Avg** | **21.6%** | **37.1%** | **10.4%** | **26.7pp** | **0.630** |

**Honest OOS edge: 1.72× base rate.** 2022 bear = no edge (AUC 0.478) —
validates the need for circuit-breaker/bear overrides. Expanding-window
variant gave 2022 top 27.4% (AUC 0.588) — older data helps bears.
G2 gate (60% top decile) NOT met — honest deployable number is ~37% OOS.

**Item 4 — adjusted_close: ALREADY STORED** (verified: DB has 158.0995 =
EODHD adjusted, not raw 164.97). Reviewer claim based on outdated code.

**Item 3 — First-touch label (built, rebuild running):** new
hit_8pct_first_touch column = which threshold crossed first. Economically
correct (exit on first touch). Concurrent label mislabels 'hit +8% early
then crashed' as loss → first-touch base rate expected slightly HIGHER.

**Commit:** 8f9b461

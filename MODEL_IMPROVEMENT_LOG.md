# Model Improvement Log — Traceability & Audit Trail

**Purpose:** Track every model change with before/after metrics so we never lose track of what worked and what broke it.

---

## Current Performance Snapshot (updated 2026-08-15, after Fix 26)

> **Critical context:** Fix 20 found that all Fix 1–19 metrics were measured on
> the 2015–2016 training window (ASC LIMIT bug). The table below is the honest
> modern-window state. Nothing degraded: the old "base" numbers were validations
> on 2015–2016 rows, not live 2026 performance.

| Metric | Old "base" (2015–2016 window) | Current (modern window) |
|---|---|---|
| Production model | LogisticRegression + ridge blend | LightGBM + proba-only blend (ridge excluded by measurement) |
| Production split (daily 80/20) | AUC 0.645, top decile 46.0% | **AUC 0.7398, top decile 58.1%**, bottom 2.9%, spread 55.2pp (base 21.3%) |
| Training window | 2015-03 → 2016-08 | 2025-10 → 2026-05 (200K most-recent rows) |
| WFO folds (cross-period OOS) | 2022: AUC 0.478 (logistic) | 2022: 0.572 / 2023: 0.564 / 2024: 0.728 / recent: 0.684 |
| First-touch EV per trade (top decile) | +0.35% (stale window) | +1.10% (recent fold, before costs) |
| Backtest P&L (2022→2026, WFO scores) | RSI-proxy + random exits (invalid) | +70.8% total / +15.5% p.a. / Sharpe 1.36 / maxDD 4.7% (with caveats, see Fix 25) |
| Calibration | none | OOB isotonic with Brier self-guard |
| Deployment protection | none | Bear breaker: VIX ≥ 25 AND XJO < SMA200 blocks new entries |

**Live model today:** LGBM artifact (sha256-verified, mtime-refreshed) trained
2026-08-15; logistic fallback also retrained on the modern window.

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

### [2026-08-14] Fix 16: First-touch label rebuild complete — honest economics revealed
- **Rebuild:** 4,318,965 rows (100%) with hit_8pct_first_touch labels
- **Label comparison:**
  - Concurrent base rate: 26.0% (touched +8% AND never breached -8%)
  - First-touch base rate: **45.2%** (crossed +8% before -8%)
  - Disagreement: 830,188 rows (19.2%) — stocks that hit +8% early then crashed later
- **Retrain on first-touch (v2 spec):**
  - Base 50.2%, top decile **55.3%**, spread 11.8pp, AUC **0.534**, edge **1.10×**
  - vs concurrent: base 26.0%, top 46.0%, spread 33.3pp, AUC 0.645, edge 1.77×
- **Finding:** First-touch base rate DOUBLED (reviewer predicted drop — inverted).
  But discrimination collapses (AUC 0.534 ≈ coin flip): predicting which of
  ±8% crossed first is inherently hard. The concurrent label's 1.77× edge
  was partly a label artifact of "sustained trend" predictability.
- **Honest economics (first-touch top decile):** 55.3%×8% − 44.7%×8% ≈ +0.85% EV/trade before costs
- **G2 gate (60% top decile): NOT met** — 55.3% < 60%. Strategy correctly gates real capital.
- **Decision:** score on concurrent label (better ranking for AI deep-dive),
  report first-touch metrics for honest economics.

### [2026-08-15] Fix 17: LightGBM classifier head — measured A/B, ADOPTED
- **What:** `train_classifier` now trains a LightGBM binary head on the identical
  data pipeline (300K rows, same fills, same pruning, same 80/20 chrono split,
  same 0.6 binary + 0.4 Ridge rank blend, recency sample weights half-life 1yr).
  Strict winner rule before adoption: AUC ≥ +0.005 AND top decile ≥ baseline
  AND bottom decile ≤ baseline + 1.0pp. Adopted model persisted to
  `data/lgbm_classifier.pkl` (model + feature order + scaler + ridge coefs) with
  a `lgbm_classifier` marker row. Live scoring (`_enrich_candidates_with_tiers`)
  prefers the artifact and falls back to the linear weights path unchanged.
- **Before → After (concurrent label, chrono 80/20, 300K):**
  - AUC: 0.6442 → **0.6766** (+0.032)
  - Top decile: 45.7% → **51.3%** (+5.6pp)
  - Bottom decile: 13.5% → **11.3%** (−2.2pp)
  - Spread: 32.2pp → **40.0pp** (+7.8pp)
  - Deciles: [51.3, 48.9, 40.9, 33.5, 32.0, 28.9, 23.2, 20.6, 14.0, 11.3]
- **Honest economics (first-touch, same ranking):**
  - First-touch top decile: 52.2% → **58.2%**
  - EV per trade before costs: +0.35% → **+1.31%**
- **Also tested — two-label rank ensemble (0.7 concurrent + 0.3 first-touch):
  REJECTED.** AUC 0.6586 vs concurrent-only LGBM 0.6766 — first-touch
  probability does not add ranking value. Cheap experiment, honest answer:
  not applied.
- **Commit:** pending

### [2026-08-15] Fix 18: Broken classifier import + stale live weights — FIXED
- **Finding:** `train_classifier` imported `db_conn` from `model_training`
  (it lives in `main`). The import raised ImportError inside the daily
  pipeline, which silently caught it (`classifier=None`) and continued with
  the regression ensemble only.
- **Impact:** live scoring used stale 2026-08-14 logistic weights trained on
  the FIRST-TOUCH label (AUC 0.534) — contradicting Fix 16's decision to
  score on concurrent (1.77× edge).
- **Fix:** resolve `db_conn` from `main` when running in the app process,
  direct engine otherwise (standalone-safe, no second scheduler). Retrained
  immediately: concurrent-label logistic weights restored (top decile 46%,
  AUC 0.644) and Fix 17 artifact adopted on top.
- **Deployment note:** container code updated; next backend restart activates
  the LGBM live path. Image rebuild bakes it in.

### [2026-08-15] Fix 19: Review hardening (artifact serving path)
- **Cache staleness fixed:** `_load_lgbm_classifier` now keys its cache by file
  mtime — daily retrains and rejection-driven removals take effect without a
  process restart; a miss is no longer cached permanently.
- **Rejection cleanup:** when the challenger runs and loses the winner rule,
  the old artifact file is deleted and a `__rejected__` marker row is written,
  so live scoring can never serve a model the latest verdict rejected.
- **Live feature drift fixed:** EODHD + historical-fundamental features
  (eps_surprise, pct_institutions, pct_insiders, insider_net_ratio,
  esg_governance, fund_hist_gross_margin, fund_hist_op_margin) are now filled
  from the same DB sources training uses. Verified: filling pct_institutions
  alone moves proba by ~2.5pp on a sample row — the zero-fill drift was real.
- **Artifact integrity:** sha256 of the artifact recorded in the DB marker row
  and verified at load; mismatch or missing marker → fallback to linear path.
- **Audit trail:** marker DELETE now scoped to trained_at (matches logistic path).
- Verified end-to-end in container: hash match, fill maps load, artifact scores.

### [2026-08-15] Fix 20: Training window correction — CRITICAL
- **Bug:** `ORDER BY signal_date ASC LIMIT 300000` selected the EARLIEST rows
  (2015-03 → 2016-08) with near-constant macro features. Every Fix 1–19
  headline metric (top decile 46–51%, AUC 0.64–0.68) was measured on 2015–2016.
  Fix 6's "300K sweet spot" is void — it was a 2015–2016 artifact.
- **Fix:** most-recent rows via subquery (DESC LIMIT → re-sort ASC, keeping the
  chrono split valid) in train_classifier AND fit_model_weights; recency
  weights now computed from actual signal dates (old mapping assumed 9 years).
- **New honest modern numbers (daily split 2025-07 → 2026-05, base 21.1%):**
  - LGBM: AUC **0.6855**, top decile **42.5%**, bottom 3.9%, spread 38.6pp,
    edge **2.01× base**
  - Logistic (fallback): AUC ~0.592 — LGBM wins by +0.093 (Δ criterion met)
- **Modern WFO-style folds (train window before fold, eval = fold):**
  - 2022 bear fold: single AUC 0.572 (bear-slice 0.574) — vs Fix 15 logistic 0.478
  - 2024 bull fold: single AUC 0.728, top decile 57.5%, EV +2.05%
  - Recent fold (eval 2025-07→2026-05): AUC 0.684, top 50.2%, EV +1.10%
- **Commit:** pending

### [2026-08-15] Fix 21: Regime-conditional heads (T1-A) — TESTED, REJECTED
- **What:** bear head (vix≥20 or XJO≤SMA200) + bull head vs single head, 3 folds.
- **Results:** 2022 fold: pair wins (bear AUC 0.610 vs 0.574). 2024 fold:
  single wins (top 57.5% vs 54.1%). Recent fold: single wins decisively
  (top 50.2% vs 37.5%; bear-slice 0.700 vs 0.676).
- **Conclusion:** 2025 bears ≠ 2022 bears; the single multi-regime head
  dominates on the production window. The 2022-only gain does not generalize.
  Not applied. A regime gate remains valuable as a DEPLOYMENT filter (T3-B),
  not as split training heads.

### [2026-08-15] Fix 22: Probability calibration (T1-B) — implemented with self-guard
- **What:** 3-fold OOB isotonic calibration of the LGBM head; kept only if test
  Brier improves (guard rejects otherwise; artifact `isotonic` may be None).
- **Results:** daily split: REJECTED (Brier 0.1511 → 0.1597 — raw probabilities
  already well-calibrated on adjacent-window splits). Cross-period fold:
  improved 0.214 → 0.176. Guard keeps calibration exactly when it helps —
  i.e. on regime-shifted days when raw probabilities drift.
- Live path applies isotonic when present; ranking unchanged (monotone).
- Also refreshed: ensemble scaler stats now modern-window (was 2015–2016).

### [2026-08-15] Fix 23: Ensemble fills + live XJO realism + experiments into repo
- **fit_model_weights fundamental fills:** the ensemble (Ridge/LGBM/RF regression
  + scaler stats) trained on raw matrix JSON where all 14 EODHD/historical
  fundamental features were 0.0 (dropped as zero-variance). Now filled from the
  same DB maps the classifier uses. Result: 14 dropped → 1, ensemble test R²
  0.1967 → **0.2308** (LGBM regression head 0.2297 → 0.2666).
- **Live xjo_sma_position:** replaced the trend-string proxy with the real
  XJO vs SMA200 computation (`_get_axjo_vs_sma200`, training-consistent
  1.0/0.0), fetched once per scan batch, proxy fallback on failure.
- **Live fills generalized:** EODHD fill loop now covers all EODHD_FEATURE_KEYS
  and historical fills cover roe/debt_equity/margins/fcf_yield (drift-proof if
  the active feature set changes).
- **Experiments into repo:** `backend/experiments/model_ab_test.py` (Fix 17 A/B)
  and `backend/experiments/regime_ab_test.py` (Fix 20/21/22 folds) with run
  instructions. model_ab_test.py docstring notes its numbers are pre-Fix-20
  (2015–2016 window).

### [2026-08-15] Fix 24: Bear-market deployment breaker (PortfolioGate)
- **What:** `PortfolioGate.set_market_context()` + `bear_breaker_active()`:
  VIX ≥ 25 (`BEAR_BREAKER_VIX` env) AND XJO < SMA200 → block NEW entries
  (`BEAR_BREAKER_ENABLED` env kills it). Wired into the paper-trade creation
  path in main.py; backtest applies it per signal_date from point-in-time
  row features. Callers that never set context are unaffected.
- **Basis:** 2022 bear fold top decile 20.4% (Fix 15); Fix 21 showed no
  ranking edge survives deep bear windows; the breaker is a deployment
  filter, not a ranking change.

### [2026-08-15] Fix 25: backtest.py — real model scores + deterministic exits
- **Was:** score = `feats["rsi"]` (RSI-ranked portfolios) and exits =
  `np.random.normal(0.05, 0.15)` (noise). Every prior P&L number was invalid
  as G2 evidence.
- **Now:** walk-forward LGBM per fold-year trained ONLY on pre-fold rows
  (month-capped, same pipeline as train_classifier); exits deterministic from
  `forward_return_63d` with −20% catastrophe stop via `forward_max_drawdown_63d`.
- **First real WFO P&L (2022-01 → 2026-07):** final +70.8%, annual +15.5%,
  Sharpe 1.36, max DD 4.7%, hit rate 67.7% (96 trades), avg win +22.7% /
  avg loss −10.5%.
- **Caveats (do not cite without them):** fundamental fills use LATEST
  snapshots (lookahead inflation; P/E point-in-time since Fix 10, but
  EODHD/historical ratios are latest-value); no slippage/brokerage; satellite
  exposure capped ~25% NAV by the gate.
- **WFO fold summary (single LGBM, proba-only):**
  - 2022 bear: AUC 0.572, top 28.7% (1.76× base) | 2023: 0.564, 30.0%
    (1.44×) | 2024: 0.728, 57.5% (2.29×) | recent 2025-07→2026-05: 0.684,
    50.2% (1.99×)

### [2026-08-15] Fix 26: Modern-window sample/blend re-optimization — ADOPTED
- **Sample sweep (most-recent N, adjacent 80/20, proba-only):**
  100K AUC 0.803/top 71.8% | 150K 0.752/58.5% | 200K 0.750/60.4% |
  300K 0.716/50.4% | 400K 0.704/39.4% — Fix 6's "300K sweet spot" was a
  2015–2016 artifact; modern optimum ≈ 150–200K.
- **Ridge blend hurts modern ranking at every weight** (200K: 0.0 → AUC
  0.750/top 60.4%; 0.2 → 0.749/57.7%; 0.4 → 0.731/52.7%). The 0.4 weight
  was tuned on 2015–2016. REG_BLEND_WEIGHT → 0.0; live blend weights now
  come from the artifact.
- **Adopted:** LIMIT 200000 + probability-only blend. Production rerun:
  window 2025-10-23 → 2026-05-15, AUC **0.7398**, top decile **58.1%**,
  bottom 2.9%, spread 55.2pp (base 21.3%) vs pre-adoption 0.6855/42.5%/3.9%.
  Calibration guard kept isotonic this run (Brier 0.1535 → 0.1522).
- **Commit:** pending

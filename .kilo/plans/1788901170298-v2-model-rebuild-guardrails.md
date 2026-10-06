# V2 Model Rebuild Plan — Root Cause Fixes + Guardrails

## Executive Summary

The v2 model regressed from **AUC 0.74 / top-decile 58-60%** (frozen model trained on 200K rows, ~2,386 symbols) to **AUC 0.58-0.66 / top-decile 35-50%** (trained on 37K rows, 82 symbols). The root cause is a **Python indentation bug that turned the label-computation code into dead code** — no new training rows were generated for ~1 month. Combined with a `from main import db_conn` import chain that hangs standalone rebuild scripts, the training matrix was never repopulated after the DB was rebuilt. This plan fixes all root causes, rebuilds the full training matrix (~3-4M rows, ~1,616 symbols), retrains the model, and adds guardrails to prevent recurrence.

---

## Root Causes (4 bugs, ordered by impact)

### Bug 1 — DEAD CODE in `build_training_matrix` (CRITICAL)
**File:** `backend/model_training.py` ~line 635-687
**What:** The entire label-computation block (fwd_return, hit_8pct_before_m8pct, first_touch, etc.) was indented INSIDE `if len(future) < 5: continue`. When `len(future) >= 5` (normal case), the code is skipped. When `len(future) < 5`, `continue` skips it. Result: **zero rows ever generated**.
**Impact:** Training set stuck at 37K rows / 82 symbols (from a pre-bug run). The frozen AUC 0.74 model was trained on 200K rows / ~2,386 symbols.
**Status:** ALREADY FIXED in current session — the indentation was corrected. But the training set still needs a full rebuild.

### Bug 2 — `from main import db_conn` hangs standalone scripts (CRITICAL)
**File:** `backend/eodhd_backfill.py` line 385, `backend/model_training.py` lines 466/513/756/1022
**What:** `get_ohlc_for_symbol()` does `from main import db_conn` at line 385 inside the function body. Importing `main.py` starts the scheduler/web server, which blocks forever in a standalone script. `_get_xjo_data()` (model_training.py:302) calls `get_ohlc_for_symbol("XJO")`, so `_add_macro_features()` also hangs.
**Chain:** `rebuild_training_matrix.py` → `_add_macro_features()` → `_get_xjo_data()` → `eodhd_backfill.get_ohlc_for_symbol()` → `from main import db_conn` → **HANG**
**Impact:** The rebuild script I created (`rebuild_training_matrix.py`) also hung because it imports `_add_macro_features` from `model_training.py`, which transitively calls `from main import db_conn`. Even the standalone `get_ohlc_for_symbol` I wrote in the rebuild script doesn't help because `_add_macro_features` calls the ORIGINAL `eodhd_backfill.get_ohlc_for_symbol`.
**Fix needed:** Create standalone-safe versions of `_get_xjo_data()` and `_get_macro_series()` that read XJO OHLC from the DB directly (no `main` import) and cache macro series (or load from DB if available).

### Bug 3 — PostgreSQL-specific SQL in SQLite environment (MEDIUM)
**File:** `backend/model_training.py` `_enrich_fundamentals()` (line 470-496), `backend/smsf_classifier.py` `_load_historical_fundamentals()` (line 118-130)
**What:** 
- `_enrich_fundamentals` uses `jsonb_build_object`, `?`, `->>` — PostgreSQL JSONB operators not in SQLite
- `_load_historical_fundamentals` uses `DISTINCT ON (symbol)` — PostgreSQL extension, not in SQLite
**Impact:** `_enrich_fundamentals` silently fails (caught exception, returns 0). Historical fundamentals never load. This is why the training log showed `[FundHist] Load failed: near "ON": syntax error`.
**Fix needed:** Rewrite `_enrich_fundamentals` to do Python-side JSON merge (parse features JSON, update dict, serialize back). Rewrite `_load_historical_fundamentals` to use a subquery with `ROW_NUMBER()` or `GROUP BY` pattern compatible with SQLite.

### Bug 4 — `MODEL_LOCK_IN=0` (MEDIUM)
**File:** `.env` line 12
**What:** The model freeze flag is disabled. `train_classifier` can re-adopt the LGBM artifact daily, which destabilizes WFO validation (the model keeps changing while paper trades try to resolve 63-day windows).
**Impact:** WFO needs another month of data because the model keeps changing. The strategy doc (Part 13, ASX_STRATEGY_DEEP_DIVE.md) says the model should be frozen until WFO produces decision-relevant results.
**Fix needed:** Re-enable `MODEL_LOCK_IN=1` after the model is rebuilt and retrained.

---

## What Was NOT a Bug (do not re-introduce)

### Fix 64 (PIT-safe training) — CORRECT, do not touch
EODHD snapshots (Sep 2026 only) and latest fundamental/historical snapshots are lookahead for 2015-2025 training rows. Filling them would inflate AUC with data that didn't exist at signal time. The regression guard (`active_features < 40`, `|coef(pct_institutions)| < 0.001`) **will fail** during PIT-safe training because EODHD features are intentionally zeroed. This is correct behavior — the guard is checking for the WRONG condition in PIT-safe mode.

**The guard should be updated** to check training set ROW COUNT and SYMBOL COUNT instead of EODHD feature coefficients when running PIT-safe training. The real signal that training is broken is "37K rows / 82 symbols" not "pct_institutions coefficient is 0".

---

## Implementation Plan (7 steps)

### Step 1: Fix standalone-safe macro data functions
**File to create:** `backend/macro_data_standalone.py`
Create standalone-safe versions of `_get_xjo_data()` and `_get_macro_series()`:
- `_get_xjo_data()`: Read XJO OHLC directly from `eod_ohl_history` table (symbol='XJO'), no `main` import
- `_get_macro_series()`: Try loading from DB tables first (if macro_cache table exists), fall back to yfinance with a 30s timeout per ticker. Cache results to avoid repeated network calls.
- Monkey-patch or pass these into the rebuild script so `_add_macro_features` uses the standalone versions

**Alternative simpler approach:** Modify `_get_xjo_data()` in `model_training.py` to try reading from DB first (direct `engine.connect()` without importing main), falling back to the current `from main import db_conn` path only when running inside the app process. Use `if "main" in sys.modules:` check (same pattern already used in `smsf_classifier.py:480`).

### Step 2: Fix the rebuild script
**File:** `rebuild_training_matrix.py` (already created, needs macro function fix)
- Replace the import `from model_training import _add_macro_features` with a version that uses the standalone macro functions from Step 1
- OR: copy `_add_macro_features` into the rebuild script with the standalone `_get_xjo_data` / `_get_macro_series`
- Test on 3 symbols first (CBA, WOW, BHP) to verify end-to-end: OHLC → features → labels → DB insert
- Verify rows appear in `model_training_set` (expect ~2,500-2,900 rows per symbol)

### Step 3: Full training matrix rebuild
- Clear `model_training_set` (already done — table is empty or has old 37K rows)
- Run `rebuild_training_matrix.py` for all ~1,616 eligible symbols
- Expected: ~3-4M rows (3.7M OHLC rows × ~80% eligible after volume filtering)
- Expected runtime: ~30-60 min (1,616 symbols × ~1-2s each)
- Monitor: check row count every 100 symbols via DB query

### Step 4: Fix PostgreSQL-specific SQL for SQLite
**File:** `backend/model_training.py` `_enrich_fundamentals()` (line 458-503)
- Rewrite to Python-side: SELECT symbol, entry_price, features, signal_date from model_training_set, JOIN fundamental_snapshots in Python, update features dict, batch UPDATE with json.dumps
- OR: skip `_enrich_fundamentals` entirely for now (it only fills snapshot fundamentals which are lookahead anyway per Fix 64) — the matrix JSON already stores these as 0.0 and the PIT-safe training path correctly zeroes them

**File:** `backend/smsf_classifier.py` `_load_historical_fundamentals()` (line 112-143)
- Replace `DISTINCT ON (symbol)` with SQLite-compatible subquery:
  ```sql
  SELECT symbol, fiscal_year, roe_pct, debt_equity, gross_margin_pct, op_margin_pct, fcf, market_cap
  FROM fundamental_history fh
  WHERE fiscal_year = (SELECT MAX(fiscal_year) FROM fundamental_history WHERE symbol = fh.symbol)
  ```

### Step 5: Update regression guards for PIT-safe mode
**File:** `backend/smsf_classifier.py` lines 712-726
- Replace the EODHD coefficient check with training-set SIZE checks:
  - `active_features < 35` (PIT-safe mode has fewer features — 35 is the honest count)
  - `len(X) < 100000` (training set should have 200K rows from 200K LIMIT, but ≥100K is the floor)
  - `len(set(symbols)) < 500` (should have ~1,600 symbols, not 82)
- Keep the existing guard as a WARNING (not a failure) when running PIT-safe mode

### Step 6: Retrain the model
- Run `train_classifier()` on the rebuilt training set (200K most-recent rows)
- Expected: AUC ~0.74, top decile ~58-60% (matching the frozen model performance)
- Verify regression guards pass (updated guards from Step 5)
- Verify LGBM challenger — it should now beat logistic on the full dataset

### Step 7: Re-enable model freeze + add guardrails
**File:** `.env` — set `MODEL_LOCK_IN=1`

**Guardrails to add:**

1. **Training set size assertion** in `build_training_matrix()`:
   After build completes, assert `inserted > 100000`. If not, raise an error (not a silent return).

2. **Symbol coverage check** in `train_classifier()`:
   After loading training rows, check `len(set(symbols)) > 500`. If < 500, print a WARNING with the symbol count and suggest rebuilding the matrix.

3. **Standalone-safe db_conn pattern:**
   Add to `eodhd_backfill.py:get_ohlc_for_symbol()` the same `if "main" in sys.modules:` check used in `smsf_classifier.py:480`:
   ```python
   if "main" in sys.modules:
       from main import db_conn
   else:
       from sqlalchemy import create_engine
       _engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///data/shares.db"))
       db_conn = lambda: _engine.connect()
   ```

4. **Smoke test script** (`backend/tests/test_training_matrix_smoke.py`):
   - Build matrix for 3 symbols
   - Assert > 1000 rows generated
   - Assert labels are non-zero (hit_8pct_before_m8pct has both True and False values)
   - Assert features JSON has > 50 keys

5. **Daily training health check:**
   Add to the existing `pipeline_health` job: check `SELECT COUNT(*) FROM model_training_set` and alert if < 100,000 or `SELECT COUNT(DISTINCT symbol) FROM model_training_set` < 500.

---

## Validation Checklist

After implementation, verify ALL of these pass:

- [ ] `rebuild_training_matrix.py` completes without hanging (test on 3 symbols first)
- [ ] Training set has > 1,000,000 rows after full rebuild
- [ ] Training set has > 1,000 distinct symbols
- [ ] `train_classifier()` produces AUC > 0.70
- [ ] `train_classifier()` produces top-decile > 55%
- [ ] Regression guards pass (updated version — no false EODHD check)
- [ ] LGBM challenger is evaluated (adopted or rejected with metrics)
- [ ] `MODEL_LOCK_IN=1` in `.env`
- [ ] Smoke test script passes
- [ ] `_load_historical_fundamentals()` works on SQLite (no syntax error)

---

## Files to Modify

| File | Change |
|------|--------|
| `backend/eodhd_backfill.py` | Add standalone-safe db_conn pattern to `get_ohlc_for_symbol()` (line 385) |
| `backend/model_training.py` | Fix `_get_xjo_data()` to try DB-first without main import (line 302); fix `_enrich_fundamentals()` PostgreSQL SQL (line 470); add size assertion after build (line 740) |
| `backend/smsf_classifier.py` | Update regression guards (line 712-726); fix `_load_historical_fundamentals()` SQLite SQL (line 118) |
| `rebuild_training_matrix.py` | Fix to use standalone macro functions; verify end-to-end on 3 symbols before full run |
| `.env` | Set `MODEL_LOCK_IN=1` |
| `backend/tests/test_training_matrix_smoke.py` | NEW — smoke test for training matrix generation |

## Files NOT to modify

- `backend/model_training.py` `build_training_matrix()` label computation (lines 635-687) — already fixed (indentation corrected)
- `backend/smsf_classifier.py` Fix 64 PIT-safe training logic — correct, do not re-introduce EODHD lookahead
- `backend/model_training.py` `PRUNED_FEATURES` / `REG_BLEND_WEIGHT` — validated parameters, keep

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Rebuild takes too long (>2hr) | Test on 3 symbols first, measure per-symbol time, estimate total. If >2hr, parallelize with multiprocessing. |
| Macro features (VIX, copper/gold) unavailable offline | Cache macro series to DB or JSON file on first successful fetch; rebuild script reads from cache. Macro features are 8 of 79 columns — training still works if they're 0. |
| SQLite performance on 3-4M rows | Use `PRAGMA journal_mode=WAL` and batch INSERTs (500 rows per transaction). Already done in the existing INSERT loop. |
| EODHD feature guard still fails | Expected in PIT-safe mode — the updated guards check row count + symbol count, not EODHD coefficients. |
| Model AUC doesn't recover to 0.74 | The 0.74 was on 200K most-recent rows. With 3-4M total rows, the LIMIT 200000 subquery should select the most recent 200K. If AUC is still <0.70, investigate feature distribution drift. |

---

## Context: Why this happened

The DB was rebuilt from scratch (new SQLite database with fresh EODHD data). The training matrix rebuild was triggered but the dead-code bug (Bug 1) meant no rows were generated. The 37K existing rows were from a pre-bug incremental run. Nobody noticed because:
1. `train_classifier()` doesn't check row count or symbol count — it just trains on whatever is there
2. The regression guard checks EODHD coefficient, not training set size
3. The dead code was inside an `if` block that looked correct at a glance
4. Standalone rebuild scripts hung (Bug 2), so manual rebuild attempts failed silently

The 1 month of "training" was the daily `build_training_matrix` job running, hitting the dead code, inserting 0 rows, and returning `{"rows_inserted": 0}` — which nobody checked because the model kept "working" on the stale 37K rows.

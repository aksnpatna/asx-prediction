# Plan: SGP Trade Audit, WFO Feed Timeline, and Training Staleness

**Created:** 2026-08-23
**Goal:** Confirm SGP trades are not duplicates, establish WFO feed start date, assess training data gap — all read-only first.

---

## Findings (Read-Only Investigation Complete)

### 1. SGP Trades — ✅ NOT Duplicates
- 4 open SGP trades belong to **4 different users** (each has exactly 1 SGP position)
- Per-user duplicate guard is working correctly
- No action needed — false alarm

### 2. WFO Feed Start Date — Confirmed
- First `10pct`/`8pct` tier scan: **2026-08-12 10:54 AM UTC**
- WFO uses 30/63/90 **calendar** day horizons (`timedelta(days=horizon_days)`)
- First h30d cutoff passes: **~Sep 11, 2026** (30 calendar days after Aug 12)
- **BUT**: The WFO also needs the training set to include forward outcomes for those signals. Training set max date is currently **May 20** — so outcomes for Aug signals won't exist until the training set is rebuilt with data through ~Nov 2026 (Aug + 63 days)
- The "~Sep 23" in the strategy doc is a trading-day-converted estimate (mixed formula) — actual calendar cutoff is ~Sep 11, but outcome availability depends on training set rebuild

### 3. Training Set Staleness — Real Issue
- Max `signal_date`: **2026-05-20**
- EOD data max: **2026-08-20**
- **Gap: 92 days** (3 months of recent data missing from training)
- This means the model hasn't seen the most recent quarter's market regimes

---

## Proposed Actions (Safe, Non-Breaking)

### Step 1: Rebuild Training Set (Low Risk, High Value)
- **What**: Extend `model_training_set` to include data through Aug 20
- **How**: Run `build_training_matrix` with updated date range, or let next weekly training pick up new data incrementally
- **Risk**: LOW — training is a background job, doesn't affect live scoring until next model adoption (which is gated by `MODEL_LOCK_IN`)
- **Rollback**: Previous model artifact is still frozen; if new training produces worse model, the challenger loses the winner rule and old artifact persists
- **Benefit**: Once training set includes Aug+ data, WFO can compute forward outcomes for frozen-model signals

### Step 2: Verify WFO Countdown Logic (No Code Change, Just Clarity)
- **What**: Document the exact WFO timeline in one place
- **How**: Add a health endpoint note showing: first frozen scan date → first h30d outcome date → trading days remaining
- **Risk**: ZERO — read-only display
- **Benefit**: Everyone sees the same honest countdown

### Step 3: No Action on SGP
- Confirmed per-user positions, not duplicates

---

## What We Are NOT Doing
- ❌ Not touching the duplicate position code (it's working correctly)
- ❌ Not unfreezing the model (MODEL_LOCK_IN stays active)
- ❌ Not changing the WFO horizon (30/63/90 calendar days is correct)
- ❌ Not rebuilding anything live — training is background-only
- ❌ Not breaking any existing functionality

---

## Backup / Rollback
- Training set rebuild is additive (INSERT-only, no DELETE)
- If new data causes issues, the frozen model artifact is unchanged (winner rule protects it)
- EODHD data persists in `eod_ohl_history` table — can always re-derive training rows

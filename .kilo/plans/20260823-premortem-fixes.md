# Plan: Premortem Fixes — Improvements While WFO Is Training (v2, Refined)

**Created:** 2026-08-23
**Goal:** Fix high-risk issues found in premortem analysis. All fixes are safe to deploy while model is frozen (MODEL_LOCK_IN). None touch the frozen LGBM artifact.

**Constraint:** User confirmed EODHD plan active for this month only — next month moving to yfinance for fundamentals. Don't build new EODHD-dependent features.

---

## Premortem Findings Summary

| # | Issue | Risk | Impact if WFO goes live | Fixable Frozen? |
|---|-------|------|------------------------|-----------------|
| 1 | Paper trade monitor alerts but never auto-closes | **HIGH** | SGP at +13.3% sits unclosed; losses deepen without exit | YES |
| 2 | WFO countdown: dashboard says Sep 24, engine says Sep 11 | **MEDIUM** | User waits 13 extra days; RED result unread | YES |
| 3 | Training set goes stale under MODEL_LOCK_IN | **HIGH** | Retrain on stale data when unlocked | YES (new job) |
| 4 | Live eval measures wrong metric (PnL>0 vs +8%) | **HIGH** | FREEZE triggers on wrong signal; masks model degradation | YES |
| 5 | Announcement features: only 31 rows | **MEDIUM** | NLP pipeline dead weight; no signal | YES (low priority) |
| 6 | EV formula shows ALL picks negative (-4.5%) | **MEDIUM** | Dashboard makes model look terrible; undermines confidence | YES |
| 7 | V2 daily scan not in catchup engine | **HIGH** | Every container restart loses that day's scan; no paper trades created | YES |
| 8 | Paper trade creation stalled since Aug 17 | **HIGH** | No new data for WFO; only 4 open trades, all same symbol | YES |

---

## Fix 1: Paper Trade Auto-Exit (HIGH) — SGP Proof

**Evidence:** 4 SGP paper trades at +13.3% P&L, target = 4.482, current = 4.7. Stage = `trim_signal`. Target WAS hit (4.7 ≥ 4.482) but trades remain OPEN. Zero trades ever closed with `target_retained` or `take_profit`. All 32 v2 closes are `legacy_model_retired` (24) or `manual_remove_*` (8).

**Root cause:** `_scheduled_paper_trade_monitor` (line 12154-12167) detects target/stop hits and sets `stage_update = 'trim_signal'` + sends Telegram alert. But NEVER calls `_auto_close_paper_trade()` (defined at line 15033). The function exists and works — it's only called from Telegram `SELL` command, SMSF pipeline on BROKEN verdict, and announcement monitor on CRITICAL_ANNOUNCEMENT.

**Fix approach — Two-stage grace period:**
1. **First trigger** (current behavior): Set `stage_update = 'trim_signal'`, send alert
2. **Second trigger** (15 min later): If condition STILL true AND stage already `trim_signal`/`exit_signal`, call `_auto_close_paper_trade(uid, symbol, market, cp)` to actually close

This gives a 15-minute grace period for human override (via Telegram SELL) before auto-execution.

**Exact code change location:** `backend/main.py` lines ~12154-12167 (take-profit block) and ~12161-12167 (stop-loss block)

```python
# In take-profit block (line ~12154):
if take_profit_price and cp >= float(take_profit_price):
    if position_stage != 'trim_signal':
        # First trigger — alert only
        stage_update = 'trim_signal'
        alert = _rich_alert("🎯 TAKE PROFIT TRIGGER", ...)
    else:
        # Second trigger (15 min later) — auto-close
        _auto_close_paper_trade(user_id, symbol, market, cp)
        stage_update = 'closed'
        alert = _rich_alert("🎯 AUTO-EXCEEDED", f"Target hit, auto-closed at {cp:.2f}")

# In stop-loss block (line ~12161):
if stop_loss_price and cp <= float(stop_loss_price):
    if position_stage != 'exit_signal':
        # First trigger — alert only
        stage_update = 'exit_signal'
        alert = _rich_alert("🛑 STOP LOSS TRIGGER", ...)
    else:
        # Second trigger — auto-close (loss protection can't wait)
        _auto_close_paper_trade(user_id, symbol, market, cp)
        stage_update = 'closed'
        alert = _rich_alert("🛑 AUTO-STOPPED", f"Catastrophe stop hit, auto-closed at {cp:.2f}")
```

**Auto-close notes update:** Modify `_auto_close_paper_trade` to accept an optional `reason` parameter so the exit_reason reflects the actual trigger (e.g., "auto_take_profit", "auto_catastrophe_stop").

**Verification:** After deploy, check that SGP trades (already at trim_signal) get auto-closed within 15 min. New trades should close within 30 min of hitting target.

---

## Fix 2: WFO Countdown Consistency (MEDIUM)

**Evidence:** Morning-brief shows `first_result_calendar: "2026-09-24"` (23 trading days × 7/5 = 32 calendar days from today). But WFO engine at line 13680 uses `cutoff_date = today - timedelta(days=30)` — 30 CALENDAR days. First frozen scan: Aug 12. First WFO result: Sep 11 (Aug 12 + 30 calendar days).

**Root cause:** The morning-brief countdown converts calendar days → trading days → back to calendar days using `5/7` and `7/5` factors, giving a different result than the WFO engine's pure calendar-day cutoff.

**Fix:** Use the SAME formula as the WFO engine:

```python
# backend/main.py line ~15718-15728
# OLD (trading-day conversion):
#   _elapsed = int((_dt_date.today() - _fd).days * 5 / 7)
#   _remain = max(0, 30 - _elapsed)
#   first_result = _dt_date.today() + timedelta(days=int(_remain * 7 / 5))

# NEW (calendar days, matches WFO engine exactly):
out["wfo_gate"]["first_result_calendar"] = str(_frozen_min + timedelta(days=30))
out["wfo_gate"]["days_remaining"] = max(0, (_frozen_min + timedelta(days=30) - _dt_date.today()).days)
out["wfo_gate"]["note"] = "WFO engine uses 30 calendar days from first frozen scan"
```

This will show `"first_result_calendar": "2026-09-11"` matching the WFO engine.

---

## Fix 3: Training Set Incremental Update Job (HIGH)

**Evidence:** Fix 55 rebuilt training set to May 22. `_scheduled_model_training` (line 16080) returns immediately under MODEL_LOCK_IN — no matrix build. Training set will stay at May 22 until model is unlocked (weeks/months).

**Fix:** New function `_scheduled_training_matrix_update` that runs daily and calls ONLY `build_training_matrix(incremental=True)` — never `fit_model_weights`. Does not touch the frozen model artifact.

```python
def _scheduled_training_matrix_update():
    """Daily training MATRIX refresh (no model retrain) under MODEL_LOCK_IN.
    Keeps training data fresh without touching the frozen LGBM artifact."""
    jid = _log_job_start("training_matrix_update")
    started = datetime.utcnow()
    try:
        from model_training import build_training_matrix
        result = build_training_matrix(incremental=True)
        rows = result.get("rows_inserted", 0)
        _log_job_finish(jid, rows_affected=rows, started_at=started)
        print(f"[TrainMatrix] Incremental update: {rows} rows inserted")
    except Exception as e:
        _log_job_finish(jid, status="error", error=str(e), started_at=started)
        print(f"[TrainMatrix] Failed: {e}")
```

**Schedule:** Daily at 6:35 AM (after incremental EOD update at 6:30). Runs BEFORE the 8AM scan so fresh data is available.

**Scheduler registration:**
```python
scheduler.add_job(
    _scheduled_training_matrix_update, "cron",
    hour=6, minute=35, id="training_matrix_update"
)
```

**Note:** This job runs under MODEL_LOCK_IN (it's a matrix build, not a model retrain). The frozen model artifact is NOT affected.

---

## Fix 4: Live Eval Metric Alignment (HIGH)

**Evidence:** `evaluate_signal_outcomes` (model_health.py:47) counts a "win" as `pnl_pct > 0 AND score > 50`. But the model was trained to predict `hit_8pct_before_m8pct` (+8% peak before -8% drawdown within 63 days). The FREEZE_THRESHOLD (0.35) triggers based on the wrong metric.

**Concrete example:** SGP at +13.3% counts as a "win" (pnl > 0, score > 50). But if the model predicted +8% and the trade was stopped at -20% catastrophe stop, it counts as a "loss" — even though the model's +8% prediction was correct.

**Fix:** Change win definition to match training target:

```python
# backend/model_health.py line ~47
# OLD:
if pnl_pct > 0 and (score is not None and score > 50):
    wins += 1

# NEW: Match model's training target — did it hit +8%?
if pnl_pct >= 8.0:
    wins += 1
```

**Impact:** The FREEZE_THRESHOLD (35%) now measures the right thing. If the model's +8% predictions are hitting 35%+ of the time, we continue. If below, we freeze — which is the correct behavior.

**Side effect:** The "evaluated" count will decrease (fewer trades hit +8% than hit >0%), so the live eval will show a lower hit rate initially. This is HONEST.

---

## Fix 5: Announcement Features Coverage (MEDIUM) — DEFERRED

**Evidence:** Only 31 rows in announcement_features. Features default to 0.0 for all symbols.

**Root cause:** `_scheduled_announcement_features` only processes open paper positions + latest scan picks (~20 symbols).

**Why deferred:** User confirmed EODHD plan ending this month — moving to yfinance for fundamentals. Announcement scraping depends on EODHD's ASX announcements feed. Building more coverage on a deprecated data source is wasted effort.

**Action:** Revisit AFTER yfinance transition. Consider alternative announcement sources (ASX API direct, web scraping).

---

## Fix 6: EV Formula Display (MEDIUM)

**Evidence:** ALL 15 screener candidates show negative EV (-0.04 to -0.05 = -4% to -5%). Example: BOQ tier=10pct score=18.6 ev=-0.04. This makes the model look like it expects to lose money on every pick.

**Root cause:** The EV formula uses raw LGBM proba which is rank-compressed (max ~0.25):
```python
ev = 0.08 * proba_f - 0.20 * (1 - proba_f) * 0.35
# With proba_f = 0.186: ev = 0.0149 - 0.0571 = -0.042
```
The model's actual top-decile hit rate is 58%, not 18.6%. The raw proba is NOT a calibrated probability of hitting +8% — it's a rank score.

**Fix:** Use the EMPIRICAL hit rate for the tier instead of raw proba:
```python
# backend/main.py line ~15233
# Tier-based empirical hit rates (from in-sample validation)
_TIER_HIT_RATE = {"10pct": 0.58, "8pct": 0.45, "watch": 0.21}
_empirical_hit = _TIER_HIT_RATE.get(_tier, 0.21)
ev = 0.08 * _empirical_hit - 0.20 * (1 - _empirical_hit) * 0.35
# With 10pct tier: ev = 0.046 - 0.029 = +0.017 (+1.7% EV)
# With 8pct tier: ev = 0.036 - 0.038 = -0.002 (~0% EV)
```

This shows top-decile picks with POSITIVE EV, which matches the model's actual performance.

**Also update the morning-brief watchlist EV formula** (line ~15743) to use the same tier-based empirical hit rate.

---

## Fix 7: V2 Daily Scan Missing from Catchup Engine (HIGH)

**Evidence:** The `v2_daily_scan_8am` job is registered (next run: Aug 24 8AM) but has ZERO job runs in the last 7 days. Scans in the database were created by the catchup engine or manual runs, not by the scheduled job. Every container restart during market hours loses that day's scan.

**Root cause:** The startup catchup engine (line 16755-16765) runs `broad_scan_precompute`, `inc_update`, `paper_trade_monitor`, `wfo`, and `smsf_pipeline` — but NOT `_scheduled_v2_daily_scan`. The catchup only runs the broad scan precompute (which builds the universe), not the v2 scan (which creates paper trades from tiered picks).

**Fix:** Add v2 scan to catchup engine:
```python
# backend/main.py catchup section (~line 16765)
# ── 4. V2 daily scan (if missed) ─────────────────────────────────
try:
    _v2_scan_age = None
    _v2_row = conn.execute(text(
        "SELECT MAX(generated_at) FROM wealth_scan_history WHERE scan_mode='broad'"
    )).fetchone()
    if _v2_row and _v2_row[0]:
        _v2_gen = _v2_row[0] if isinstance(_v2_row[0], datetime) else datetime.fromisoformat(str(_v2_row[0]))
        _v2_scan_age = (now_utc - _v2_gen.replace(tzinfo=None)).total_seconds() / 3600
    # V2 scan runs at 8AM on weekdays — if it's after 8AM and scan is from yesterday or earlier
    if _v2_scan_age is None or _v2_scan_age > 20:
        print(f"[StartupCatchup] V2 scan stale ({_v2_scan_age:.1f}h old) — running now …")
        _scheduled_v2_daily_scan()
        tasks_run.append("v2_scan")
    else:
        print(f"[StartupCatchup] V2 scan fresh ({_v2_scan_age:.1f}h old) — skipping.")
except Exception as _e:
    print(f"[StartupCatchup] v2_scan check failed: {_e}")
```

---

## Fix 8: Paper Trade Creation Stalled (HIGH)

**Evidence:** Paper trades were created Aug 11-17 (4-12 per day) but ZERO from Aug 18-22. The scans on Aug 18-22 all have 10pct/8pct picks that should trigger `_auto_create_paper_trade`. The 4 SGP trades from Aug 17 are still open (at +13.3%).

**Root cause analysis:**
1. The `_auto_create_paper_trade` model-quality gate (line 14812-14824) checks `_get_symbol_scan_tier(symbol)` against the LATEST scan
2. If a symbol's tier drops from 10pct to "watch" between scans, new trades are blocked
3. But the symbols in the Aug 18-22 scans (BOQ, AUB, MTS) ARE in 10pct/8pct tiers
4. The real blocker is likely that the v2 daily scan hasn't been RUNNING on schedule (Fix 7)

**Fix:** This should resolve automatically once Fix 7 is deployed (v2 scan runs on schedule → creates paper trades from tiered picks). If not, investigate `_get_symbol_scan_tier` to ensure it correctly identifies picks from the latest scan.

**Verification:** After Fix 7 deploy, check that new paper trades appear within 15 min of the 8AM scan on the next weekday.

---

## Implementation Order (Updated)

| Step | Fix | Files Changed | Risk | Blocks WFO data? |
|------|-----|---------------|------|------------------|
| 1 | Fix 4: Live eval metric | `backend/model_health.py` (1 line) | LOW | No |
| 2 | Fix 2: WFO countdown | `backend/main.py` (~5 lines) | LOW | No |
| 3 | Fix 6: EV formula | `backend/main.py` (~4 lines × 2 locations) | LOW | No |
| 4 | Fix 3: Training matrix job | `backend/main.py` (new func + scheduler) | MEDIUM | No |
| 5 | Fix 1: Auto-exit | `backend/main.py` (~20 lines) | MEDIUM | No |
| 6 | Fix 7: V2 scan catchup | `backend/main.py` (~15 lines catchup) | MEDIUM | **YES — unblocks paper trades** |
| — | Fix 5: Announcements | DEFERRED | — | — |
| — | Fix 8: Paper trade creation | Resolved by Fix 7 | — | — |

---

## Pre-Deployment Checklist

- [ ] Current SGP open trades (4 × +13.3%) — confirm they get auto-closed by Fix 1
- [ ] Training set state: May 22 max, 4.32M rows (baseline before Fix 3)
- [ ] Live eval baseline: 27% hit rate on 37 evaluated (before Fix 4)
- [ ] WFO countdown: currently showing Sep 24 (before Fix 2)
- [ ] All containers healthy, no errors in logs

---

## Post-Deployment Verification

After deploying each fix:
1. **Fix 4:** Check `evaluate_signal_outcomes` returns new hit rate (likely lower, more honest)
2. **Fix 2:** Dashboard shows `first_result_calendar: "2026-09-11"` (was Sep 24)
3. **Fix 3:** Training set max date advances daily (check after next 6:35 AM run)
4. **Fix 1:** SGP trades at trim_signal auto-closed within 15 min

---

## Rollback Plan

- **Fix 4:** Revert single line in model_health.py, rebuild
- **Fix 2:** Revert ~5 lines in morning-brief, rebuild
- **Fix 3:** Remove scheduler job (comment out `add_job` line), rebuild
- **Fix 1:** Revert monitor changes, rebuild. Auto-exit stops; alerts continue.

All fixes are independent — can deploy and rollback one at a time.

---

## What These Fixes Do NOT Touch

- Frozen LGBM model artifact (unchanged — sha256 verified)
- Training pipeline model weights (not retrained)
- WFO engine logic (only display consistency)
- Database schema (no new tables/Columns)
- Frontend (no UI changes needed — countdown auto-updates on next API call)
- EODHD integration (not touched)

---

## Future Improvements (Not in this plan)

These are identified but deferred for separate planning:
1. **Announcement NLP pipeline** — After yfinance transition, rebuild with alternative data source
2. **Survivorship bias completion** — Delisted ticker list needs manual curation or paid API
3. **Feature store** — The 23 lookahead-contaminated features (zeroed in backtest, live in scoring) still need PIT-safe alternatives
4. **Position sizing model** — Current EV formula uses fixed 35% catastrophe probability; could be calibrated from WFO data
5. **CGT-aware exit timing** — Paper trades don't track hold duration for 12-month discount threshold
6. **AI gate statistical power** — With 5% coverage (8/155), the segmented WFO can't measure AI value. Need 30%+ coverage for significance
7. **EODHD → yfinance transition** — User confirmed plan ending this month. Need to rebuild fundamental data pipeline with yfinance as source
8. **WFO confidence interval calibration** — The 95% CI thresholds (GREEN ≥ 0, AMBER ≥ 0) need backtesting against actual outcomes
9. **Paper trade position sizing** — Current `_smsf_position_size` uses 1.5% NAV at risk; not calibrated to model confidence
10. **Scan staleness detection** — No alert if v2 scan hasn't run by 9AM on a weekday (silent data gap)

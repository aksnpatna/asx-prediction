# Plan: Dashboard WFO Watchlist & Strategy Representation

**Created:** 2026-08-23
**Goal:** Show the strategy's full pipeline state on the Dashboard — not just paper trades, but what the model is watching, WFO status, and when the first honest result lands.

---

## Current State — What Each Screen Shows

| Screen | Data Source | What's Shown | What's Missing |
|---|---|---|---|
| **Dashboard** | `/api/dashboard/morning-brief` | Paper trades (SGP etc.), circuit breaker, regime, model AUC, announcements | ❌ WFO state, watchlist, countdown, tier breakdown |
| **Screener** | `/api/screener/scan` | 15 candidates with tiers, AI verdicts | ❌ WFO gate link, how many are deployable now |
| **Portfolio** | `/api/portfolio/nav-breakdown` | Core/satellite split, CGT alerts | ❌ WFO capital gate status |
| **Wealth** | `/api/wealth/projection` | Projection + `LiveValidationPanel` (only screen with WFO) | ⚠️ WFO only on Wealth tab |

## Gap Analysis

The **Dashboard** is the first screen users see. It shows SGP as an open trade but does NOT answer:
1. "What else is the model watching?" → **No watchlist/candidates strip**
2. "Is the strategy allowed to open new trades right now?" → **No WFO capital gate**
3. "When will we know if this works?" → **No countdown to first WFO result**
4. "What's the model's hit-rate on recent picks?" → **No live validation teaser**

The WFO data exists in the backend (`/api/walk-forward/oos`, `/api/model/health-summary`) but only surfaces on the Wealth tab.

---

## Proposed Changes (Non-Breaking)

### Change 1: Dashboard — Add "MODEL WATCHLIST" strip
**File:** `frontend/src/smsfUplift.jsx` `DashboardScreen`

Add a compact strip below "MY ACTIVE TRADES" showing:
- Top 5 model picks (10pct/8pct tier) from latest scan
- Each pick: symbol, score, tier badge, EV%, days to CGT discount if held
- WFO capital gate status pill (INSUFFICIENT_DATA / AMBER / GREEN)
- "Next WFO result: ~Sep 11" countdown teaser

**API:** Extend `/api/dashboard/morning-brief` to include:
- `watchlist`: top 5 picks (symbol, score, tier, ev_est_pct, reachable)
- `wfo_gate`: `{ state, allow_new_positions, description, first_result_calendar }`
- `model_eval`: `{ evaluated, hit_rate, freeze }` from `evaluate_signal_outcomes`

### Change 2: Dashboard — Add WFO gate pill to circuit breaker area
**File:** `frontend/src/smsfUplift.jsx` `DashboardScreen`

Below or beside the `CircuitBreakerBanner`, add a compact WFO status pill:
- "📊 MODEL EDGE: ACCUMULATING (0/20 frozen signals aged)" when INSUFFICIENT_DATA
- "📊 MODEL EDGE: AMBER — unproven, max 6 positions" when AMBER
- "📊 MODEL EDGE: GREEN — confirmed (Sharpe X.XX)" when GREEN

This directly answers "can I trust this strategy right now?"

### Change 3: Screener — Show deployable count
**File:** `frontend/src/smsfUplift.jsx` `ScreenerScreen`

In the hero stats area, add:
- "DEPLOYABLE NOW: X" (count of 10pct + 8pct tier picks that pass WFO gate)
- Link to WFO countdown: "First WFO result ~Sep 11"

### Change 4: Backend — Add watchlist to morning-brief
**File:** `backend/main.py` `_scheduled_dashboard_morning_brief` (or inline)

Add to the morning-brief response:
```python
out["wfo_gate"] = get_current_wfo_state()
with db_conn() as conn:
    latest_picks = conn.execute(text(
        "SELECT picks FROM wealth_scan_cache ORDER BY generated_at DESC LIMIT 1"
    )).fetchone()
    if latest_picks:
        picks = json.loads(latest_picks[0])
        top = [p for p in picks if p.get("_target_tier") in ("10pct", "8pct")][:5]
        out["watchlist"] = [{"symbol": p.get("symbol"), "score": p.get("_model_confidence"), "tier": p.get("_target_tier")} for p in top]
```

---

## Rollback Plan
- All changes are additive (new fields in API responses, new UI sections)
- Frontend: can revert `smsfUplift.jsx` to previous build (git)
- Backend: new API fields are ignored by old frontend
- No database schema changes
- No model or config changes

---

## Verification Checklist
- [x] Dashboard shows WFO gate status without breaking existing layout
- [x] Dashboard watchlist strip renders top 5 picks with tier badges
- [x] Morning-brief API returns `wfo_gate` and `watchlist` fields
- [x] Screener shows deployable count
- [x] Existing screens (Portfolio, Wealth, Screener) still work
- [x] No new 500 errors in backend logs
- [x] Frontend build succeeds
- [x] All containers healthy

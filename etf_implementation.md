# ETF Momentum — Signal Layer for the SMSF Satellite
## Using ETF momentum as a regime filter, not as a holding

> **Current mode: SIGNAL ONLY — no ETF positions held**
> The AI satellite targets 14–18%+ net. Holding ETFs at ~10% net would drag the blended return.
> The ETF framework earns its place as a **free regime filter** that fires earlier than the portfolio circuit breaker.
> Decision to hold ETF positions deferred until post-validation (after G-gates G2/G3/G4 pass).

> **IMPLEMENTED (2026-08-17):** The signal layer is now live in code:
> - `compute_etf_regime()` in `backend/etf_pipeline.py` — regime + sector tilt + satellite cap
> - Wired into `GET /api/smsf/dashboard` under `etf_regime`
> - Strengthened `PortfolioGate`: NEUTRAL caps satellite at 4, RISK_OFF suspends satellite entries
> - XJO (ASX200 index) + 22 ETFs backfilled into `eod_ohl_history` via `backfill_etf_universe()`

---

## PART 1 — WHY SIGNALS, NOT POSITIONS

### The Arithmetic

| Allocation | Expected net return | Contribution on $200K |
|------------|--------------------|-----------------------|
| 100% AI satellite | ~14–18%/yr net | $28,000–$36,000/yr |
| 60% ETF core + 40% satellite | ~10% core + 14–18% satellite | blended ~11.6–13.2%/yr |
| **Signal-only** (100% satellite, ETF regime gates applied) | ~14–18%/yr with regime protection | **same alpha, lower risk** |

Holding ETF positions during the AI validation phase would mean:
- Putting $120K to work at 10%/yr net while the AI model goes undeployed on that capital
- Blending down the portfolio return unnecessarily
- Adding rebalancing complexity with no alpha benefit over the signal alone

**The ETF signal is free** — it only requires reading XJO's daily close against its 200-day SMA and calculating monthly price returns on 5 tickers. Both are already in the EODHD local database.

### When to Revisit ETF Positions

| Condition | Action |
|-----------|--------|
| G-gates G2/G3/G4 not yet passed | Signal only — current state |
| Satellite live < 6 months | Signal only |
| Satellite Sharpe < 0.50 after 12 months live | Reintroduce ETF core as a floor |
| Satellite Sharpe ≥ 0.65 after 12 months live | Continue signal-only |
| Drawdown > −15% on satellite with no circuit breaker trigger | Review adding ETF floor |

---

## PART 2 — THE SIGNAL: WHAT IT IS AND WHY IT WORKS

### Backtest Evidence (Why This Signal Has Edge)

The following results are from the ETF *position* strategy (non-lookahead, 2020–2026). They are presented here to validate that the underlying signals have predictive power — not to argue for holding ETF positions.

| Metric | ETF Signal Strategy | VAS Buy & Hold | ASX200 |
|--------|--------------------|--------------------|--------|
| CAGR | 11.89% | 9.84% | 8.93% |
| Annualised vol | 10.84% | 14.92% | 15.21% |
| **Sharpe** | **0.73** | 0.49 | 0.42 |
| **Max drawdown** | **−15.21%** | −29.8% | −36.1% |
| **2022 bear max DD** | **−10.65%** | −19.4% | −17.8% |

**The 2022 number is the key proof.** The ASX200 drew down −17–20% in the 2022 rate-rise bear. The ETF signal rotated to defensive positioning in **February 2022** — before the peak — purely from momentum turning negative on TECH before XJO broke its SMA200. That is the early-warning property we want applied to satellite entries.

### The 2022 Signal Sequence (What Fired and When)

```
Jan 2022:  RISK_ON — TECH + BNKS top momentum
           XJO still above SMA200
           → Satellite: normal operations

Feb 2022:  TECH momentum score turns negative
           GOLD momentum score goes positive
           XJO approaches (but not yet below) SMA200
           → Signal: NEUTRAL — tighten satellite stops, no new entries in growth sectors

Mar 2022:  XJO breaks below SMA200
           → Signal: RISK_OFF — suspend all new satellite entries
           (ASX200 peak-to-trough: approximately −17% from here)

Jul 2022:  XJO recovers above SMA200
           BNKS momentum recovers (rate-rise beneficiary)
           → Signal: RISK_ON — resume satellite entries, favour financials
```

The signal fired **4–6 weeks before** the worst of the drawdown — without any macro prediction. Purely mechanical momentum + trend.

---

## PART 3 — THE TWO SIGNAL OUTPUTS

### Signal 1 — Regime Gate (monthly, 2 minutes)

**Input:** XJO daily close vs its 200-day SMA  
**Output:** satellite permission level

```
RISK_ON   (XJO ≥ SMA200)
  → Satellite runs normally
  → Up to 8 positions, all sectors eligible
  → New entries permitted in any calendar-gated month

NEUTRAL   (SMA200 × 0.90 ≤ XJO < SMA200)
  → Satellite reduced
  → Maximum 4 open positions
  → No new entries in growth/momentum sectors
  → Tighten existing stops to −15% (from catastrophe −20%)

RISK_OFF  (XJO < SMA200 × 0.90)
  → Satellite suspended
  → No new entries at all
  → Existing positions: hold unless thesis-broken or stop hit
  → Circuit breaker L2/L3 almost certainly already active
```

This fires **before** the portfolio circuit breaker (which requires −8% NAV drawdown). The regime gate is a pre-emptive filter.

### Signal 2 — Sector Tilt (monthly, 5 minutes)

**Input:** 3M/6M/12M price momentum on 5 reference ETFs  
**Output:** sector weighting guidance for satellite stock selection

```
Top ETF by momentum score  →  Sector preference for satellite
─────────────────────────────────────────────────────────────
TECH   (BetaShares Global Tech)   →  Favour tech, software, SaaS picks
CURE   (BetaShares Healthcare)    →  Favour healthcare, pharma, biotech
BNKS   (BetaShares Global Banks)  →  Favour financials, insurance
VAS    (Vanguard ASX 300)         →  Broad market — no sector tilt
GOLD   (BetaShares Gold)          →  CAUTION — defensive rotation in progress
                                      Tighten stops on all positions
                                      GOLD reaching top = early bear signal
```

This does not override the AI model's stock scoring. It adjusts the **deployment priority** when the model returns multiple candidates at similar scores.

### Live Signal Today (August 2026)

| Input | Value | Output |
|-------|-------|--------|
| XJO vs SMA200 | Above | RISK_ON — satellite runs normally |
| Top ETF by momentum | TECH | Favour tech/growth satellite picks |
| Second ETF | CURE | Healthcare sector also favoured |
| GOLD momentum | Low | Not defensive — green light |
| Satellite permission | ✅ Full | Up to 8 positions, all sectors |

---

## PART 4 — INTEGRATION INTO THE EXISTING SYSTEM

### Where This Fires in the Dashboard

The regime gate and sector tilt should appear in **Morning Briefing (Dashboard tab)** as part of the existing circuit breaker strip:

```
Current display:   RISK STATUS: NORMAL — VIX 14.2 · safe to open new trades
Proposed addition: RISK STATUS: NORMAL · REGIME: RISK_ON (XJO +4.1% above SMA200)
                   SECTOR TILT: TECH > CURE > BNKS · GOLD not defensive
```

### Where This Feeds the Circuit Breaker

The existing circuit breaker in `main.py` triggers on NAV drawdown (−8%/−15%/−25%). The ETF regime signal should be a **pre-emptive layer** that fires earlier:

```
ETF Regime    →  Circuit Breaker interaction
─────────────────────────────────────────────────────────────
RISK_ON       →  No change to circuit breaker
NEUTRAL       →  Equivalent to Yellow (L1): cap satellite at 4 positions
RISK_OFF      →  Equivalent to Orange (L2): suspend new entries
               (This fires BEFORE −15% NAV drawdown in most cases)
```

### Wiring to main.py (minimal change)

The regime check needs one new function and one addition to the dashboard payload:

```python
def compute_etf_regime(conn) -> dict:
    """
    Compute ETF regime signal from XJO SMA200 and ETF momentum scores.
    Returns regime, sector_tilt, satellite_permission, scores.
    Requires: eod_ohl_history table has XJO, VAS.AX, TECH.AX, CURE.AX, BNKS.AX, GOLD.AX
    """
    import pandas as pd
    from datetime import date, timedelta

    # XJO SMA200
    xjo_rows = conn.execute(text(
        "SELECT date, close FROM eod_ohl_history WHERE symbol='^AXJO' "
        "ORDER BY date DESC LIMIT 210"
    )).fetchall()
    if len(xjo_rows) < 200:
        return {"regime": "UNKNOWN", "reason": "insufficient XJO history"}
    ...
```

> **IMPLEMENTATION NOTE (data-model correction):** the above pseudocode assumed
> `^AXJO` and `.AX`-suffixed ETF tickers in `eod_ohl_history`. The real table
> stores ETFs as **bare symbols** (`VAS`, `TECH`, `CURE`, `BNKS`, `GOLD`) and the
> XJO index is stored as **`XJO`** (backfilled from the EODHD `XJO.INDX` code, NOT
> `.AU`-suffixed). The implemented `compute_etf_regime()` (no `conn` arg — it reads
> via `get_ohlc_for_symbol` / `load_ohlc`) handles these correctly.

Add `etf_regime` to the `smsf_dashboard` endpoint payload (done).

### Signal strengthening in PortfolioGate (implemented)

`PortfolioGate.set_market_context()` now accepts `etf_regime`, and
`can_open_position()` enforces it as a pre-emptive satellite gate:

| ETF regime | Satellite cap | Effect on new satellite entries |
|---|---|---|
| RISK_ON | 12 (default) | unchanged |
| NEUTRAL | 4 | capped |
| RISK_OFF | 0 | **suspended** (core buy-and-hold untouched) |

This fires *before* the NAV circuit breaker, which only triggers on realised
drawdown. Backward-compatible: call sites that omit `etf_regime` are unaffected.

---

## PART 5 — MONTHLY CHECKLIST (10 MINUTES)

No trading. No brokerage. Just read and act.

```
MONTHLY CHECKLIST (last trading day of each month):

1. Is XJO above its 200-day SMA?
   → Check: /api/smsf/dashboard → etf_regime.regime
   → or: Google "XJO 200 day moving average" → ASX website

2. What is the top ETF by 12-month momentum?
   → Check: etf_regime.sector_tilt
   → Adjust satellite stock selection priority accordingly

3. Is GOLD in the top-2 by momentum?
   → If YES: early defensive warning — tighten all satellite stops
   → Do NOT wait for the circuit breaker to fire

4. Has regime changed since last month?
   → RISK_ON → NEUTRAL: reduce satellite to 4 positions before adding new ones
   → NEUTRAL → RISK_OFF: suspend new entries immediately
   → Any improvement: resume satellite at new permitted level

5. Log: date, regime, top_etf, gold_rank, action_taken
```

---

## PART 6 — WHEN TO RECONSIDER HOLDING ETF POSITIONS

This document will be revisited if **any** of the following occur after live deployment:

| Trigger | Action |
|---------|--------|
| Satellite Sharpe < 0.50 after 12+ months live | Add VAS as 20–30% of NAV core floor |
| Satellite max drawdown > −20% in any 6-month window | Add VAS + GOLD as defensive floor |
| Model health (AUC) drops below 0.62 for 8+ weeks | Reduce satellite, add ETF floor while rebuilding |
| G-gate G5 fails (inconsistent risk limits) | Pause satellite, park in VAS temporarily |

In those cases, refer to the archived position-sizing section in the git history of this file, or the original version which included full ETF position sizing for the core sleeve.

---

## PART 7 — ONE-PAGE SUMMARY

**What the ETF framework delivers right now (signal-only):**

- ✅ Regime gate (RISK_ON / NEUTRAL / RISK_OFF) from XJO vs SMA200 — fires before −8% NAV drawdown
- ✅ Sector tilt for satellite stock selection — TECH / CURE / BNKS / GOLD momentum ranked monthly
- ✅ Early bear warning — GOLD reaching top momentum is a proven 4–6 week leading indicator
- ✅ Zero cost — no brokerage, no ETF positions, no MER drag
- ✅ Already in EODHD DB — XJO and ETF tickers available in `eod_ohl_history`

**What it does NOT do right now:**
- ❌ Hold any ETF positions (deferred until post-G-gate validation)
- ❌ Generate alpha directly — it protects the satellite's alpha from macro headwinds
- ❌ Replace the AI model's stock selection — it is a regime filter, not a picker

**The one thing to implement this week:**
Add `compute_etf_regime()` to the `smsf_dashboard` API endpoint. The regime signal and sector tilt then appear in the Morning Briefing automatically. Total backend change: ~60 lines of code.

**Status:** ✅ DONE (2026-08-17). `compute_etf_regime()` implemented, wired to
`/api/smsf/dashboard` (`etf_regime` key), and enforced in `PortfolioGate` as a
pre-emptive satellite gate. XJO + 22 ETFs backfilled. Live signal today:
**RISK_ON**, XJO +3.18% above SMA200, sector tilt **FINANCIALS** (BNKS), no
defensive warning.

### Files touched
- `backend/etf_pipeline.py` — `compute_etf_regime()`, `REFERENCE_ETFS`, `SECTOR_TILT_MAP`, `SATELLITE_CAP_BY_REGIME`
- `backend/portfolio_gate.py` — `set_market_context(etf_regime=...)`, `satellite_cap()`, RISK_OFF suspension
- `backend/main.py` — `etf_regime` in `smsf_dashboard`, `etf_regime` passed to `PortfolioGate` at entry time
- `backend/eodhd_backfill.py` — `backfill_etf_universe()` now also backfills XJO (`XJO.INDX`)

### Backfill command
```bash
python backend/eodhd_backfill.py --mode etf   # populates 22 ETFs + XJO
```
Note: the code changes above run in the container via `docker cp`; rebuild the
`asx-backend` image to make them permanent.

---

*Updated August 2026: reframed from ETF position strategy to signal-only regime filter.*
*ETF position sizing content archived — available in git history if satellite underperforms.*
*Signal validation evidence retained (2022 bear rotation) as proof the underlying signals have edge.*

# SMSF Implementation Plan — v2 (Peer-Reviewed, Corrected Build Order)
## Corrected after independent review against live PostgreSQL database

> **Key changes from v1:** Build order completely reordered. Weeks 1–4 fix *blocking* structural bugs before any new features are added. September full deployment retired. November deployment on a validated pipeline is the target. Core–Satellite architecture replaces 25-position momentum. Path-aware label replaces peak-touch label. −7% stop retired. PortfolioGate is the single source of truth for all risk limits.

---

## THE HONEST STATE OF THE PIPELINE RIGHT NOW (August 2026)

Before building anything, understand what actually exists:

| Component | Required | Actual State |
|---|---|---|
| `wfo_metrics` (WFO gate) | 8+ weeks of OOS evaluations | **0 rows — never run** |
| `wealth_scan_history` | Hundreds of daily scans | **5 scans total (Jul 18–23)** |
| `paper_trades` | 100+ clean trades, ≥52% hit rate | **11 trades ever (5 open, 6 closed)** |
| Duplicate position guard | Working | **FAILING: 3× SCG + 2× PPT opened same day** |
| `tracking_windows` | Mature accuracy stats | **19 rows** |
| `backtest.py` | Referenced in v1 Week 7 | **Does not exist** |

**The pipeline is not ready for real capital.** This is not a criticism — it is a starting point. The corrected plan gets it ready for November.

---

## PRE-IMPLEMENTATION CHECKLIST

- [ ] SMSF bank account open (CBA for ComSec integration)
- [ ] ComSec SMSF brokerage account linked
- [ ] EODHD API: **1,000,000 calls/month** (not 100K — corrected)
- [ ] OpenAI / DeepSeek API key active
- [ ] Docker stack running: `docker-compose up -d` without errors
- [ ] PostgreSQL accessible; `eod_ohl_history` has expected row counts
- [ ] Telegram bot delivering alerts

---

## WEEK 1 — BLOCKING: DATA INTEGRITY + DUPLICATE BUG FIX

**These must be done before anything else. They corrupt all downstream work if left unfixed.**

### W1-T1: Store `adjusted_close` in Backfill

**File to modify:** `backend/eodhd_backfill.py` (~line 187)

**Problem:** Training uses raw `close`; production uses `adjusted_close`. ASX banks yield 5–6%+ — ex-dividend drops appear as price crashes in training. This is a systematic distortion of every feature around every ex-div date, on the highest-yielding developed market in the world.

**Fix:** EODHD returns `adjusted_close` in the same payload at zero extra API cost.

```python
# In eodhd_backfill.py — add adjusted_close to INSERT statement
# The column already exists in the payload: response["adjusted_close"]
# Change the INSERT to store it alongside raw close

# Also: flag stale sessions before feature computation
# Stale = volume == 0 OR close == previous_close (11.4% of rows currently stale)
df["stale_session"] = (df["volume"] == 0) | (df["close"] == df["close"].shift(1))
# Exclude stale rows from RSI, momentum, Bollinger, drawdown calculations
```

**Acceptance criteria:**
- [ ] `adjusted_close` column populated in `eod_ohl_history` for all symbols going forward
- [ ] Historical backfill run to re-populate `adjusted_close` for existing rows (use EODHD bulk endpoint — costs ~2,400 calls for all symbols)
- [ ] `stale_session` flag column added to `eod_ohl_history`
- [ ] Feature computation skips stale rows

---

### W1-T2: Fix Duplicate Position Bug

**File to modify:** `backend/` — wherever `wealth_builder_bootstrap` opens positions

**Problem:** 3× SCG and 2× PPT opened on the same day at the same price in paper trades. This is a live bug in production right now.

```python
# Add unique constraint to paper_trades table
# ALTER TABLE paper_trades ADD CONSTRAINT uq_open_position UNIQUE (symbol, status)
# WHERE status = 'OPEN';

# In the position-opening code:
def open_position(symbol, ...):
    # Check before inserting
    existing = db.query("SELECT id FROM paper_trades WHERE symbol = %s AND status = 'OPEN'", symbol)
    if existing:
        logger.warning(f"DUPLICATE BLOCKED: {symbol} already has open paper position")
        return None
    # Proceed with insert
```

**Acceptance criteria:**
- [ ] Duplicate constraint added to `paper_trades` table
- [ ] All existing duplicates reviewed and resolved (keep one, close others at current price)
- [ ] Test: attempt to open duplicate position → confirm rejection with log message
- [ ] Run for 5 days: confirm zero new duplicates appear

---

### W1-T3: Rebuild `model_training_set` on Clean Data

After adjusted_close is backfilled and stale sessions are flagged:

```bash
# Rebuild the training matrix from scratch on clean data
python3 backend/model_training.py --rebuild-training-set --use-adjusted-close --skip-stale
# This produces a new model_training_set table with corrected features
# Takes: ~30-60 minutes depending on hardware
```

**Acceptance criteria:**
- [ ] `model_training_set` rebuilt on `adjusted_close` (not raw `close`)
- [ ] Stale-session rows excluded from training labels
- [ ] Row count within 10% of original (stale filtering should remove ~10–12% of rows)
- [ ] Feature distributions checked: RSI, momentum, drawdown should all shift slightly (no longer ex-div-contaminated)

---

## WEEK 2 — BLOCKING: PATH-AWARE LABEL + MODEL RETRAINING

**This is the single most important fix. The current label is the wrong target.**

### W2-T1: Why the Current Label Is Wrong

```python
# CURRENT (wrong) label in model_training.py ~line 566:
hit_5pct_63d = fwd_peak >= 5.0   # touches +5% at ANY POINT in 63 days

# Base rate: 71.3% — nearly every stock "wins" on this metric
# With a -7% stop: only ~30-45% of those wins are captured before the stop fires
# EV with -7% stop: -3.2% per trade (proven from 2.4M rows in model_training_set)
```

### W2-T2: The Corrected Label

```python
def compute_path_aware_label(peak, trough, close_63d, target=8.0, stop=-8.0):
    """
    Path-aware label: did the stock hit +target% BEFORE falling -stop%?
    Returns: +1 (win), -1 (loss), 0 (timeout — use close for regression)
    
    This matches the catastrophe stop + profit zone in the exit discipline.
    """
    if peak >= target and trough > stop:
        return 1   # hit target before stop
    elif trough <= stop:
        return -1  # stop fired first (or simultaneously with target in bad cases)
    else:
        return 0   # neither hit within 63 days — use close-based return

# Secondary regression head (close-based, all windows):
label_return_63d = clip(close_63d / entry_close - 1, -0.60, 0.60)

# Remove hit_5pct_63d and hit_8pct_63d as TARGETS (keep as features if useful)
```

### W2-T3: Retrain and Validate

```python
# Report model quality on path-aware metric by score decile:
for decile in range(1, 11):
    scores_in_decile = test_set[test_set['score_decile'] == decile]
    path_hit_rate = (scores_in_decile['label_path'] == 1).mean()
    avg_return = scores_in_decile['label_return_63d'].mean()
    print(f"Decile {decile}: path_hit_rate={path_hit_rate:.1%}, avg_return={avg_return:.1%}")

# PASS criteria for G2 (required before real capital):
# Top decile: P(+8% before -8%) >= 60%
# Bottom decile: P(+8% before -8%) < 40%
# The decile spread proves the model has real selection skill
```

**Acceptance criteria:**
- [ ] `label_path` column added to `model_training_set`
- [ ] Model retrained on path-aware labels with `adjusted_close` features
- [ ] Decile report generated: top decile ≥ 55% path hit rate (≥ 60% for G2 full pass)
- [ ] 2022 bear market period included in validation (must not show severe degradation)
- [ ] `hit_5pct_63d` removed as training target (retained as optional feature only)

---

## WEEK 3 — BLOCKING: PORTFOLIO GATE (SINGLE SOURCE OF TRUTH)

**Eliminates the three conflicting risk limit sets from v1.**

### W3-T1: The One True PortfolioGate

**File to create:** `backend/portfolio_gate.py`

```python
# ALL risk limits live here. No other file specifies position sizing.
# If another file has position sizing, delete it and import from here.

RISK_CONFIG = {
    # Satellite (model-driven, 63-day cycle)
    "satellite_max_positions": 8,        # 4-8 open satellite positions
    "satellite_max_single_pct": 0.07,   # 7% NAV max per satellite position
    "satellite_total_max_pct": 0.40,    # satellite sleeve ≤ 40% NAV total
    
    # Core (buy-and-hold, mega/large-cap)
    "core_max_positions": 10,           # 6-10 core positions
    "core_total_pct": 0.60,             # Core targets 60-70% NAV
    
    # Universal
    "max_position_pct_of_20d_adv": 0.02,  # ≤ 2% of 20-day daily dollar volume
    "cash_floor": 0.15,                   # minimum 15% cash always
    "sector_cap_default": 0.25,
    "sector_cap_materials": 0.30,
    
    # Catastrophe stop (satellite positions)
    "catastrophe_stop_pct": -0.20,         # -20% from entry OR:
    "catastrophe_stop_atr_mult": 2.5,      # 2.5x stock's 20-day ATR% (whichever is wider)
    
    # Circuit breaker levels
    "breaker_yellow": 0.08,   # -8% NAV from peak
    "breaker_orange": 0.15,   # -15% NAV from peak
    "breaker_red": 0.25,      # -25% NAV from peak
}

class PortfolioGate:
    def can_open_satellite(self, proposed, portfolio) -> dict:
        """Hard gate: returns (approved, reason). Cannot be overridden by AI debate."""
        nav = portfolio["total_value"]
        satellite_positions = [p for p in portfolio["open_positions"] if p["sleeve"] == "satellite"]
        pv = proposed["position_value"]
        
        checks = [
            (len(satellite_positions) >= RISK_CONFIG["satellite_max_positions"],
             f"SATELLITE_MAX_POSITIONS: {RISK_CONFIG['satellite_max_positions']} reached"),
            (pv / nav > RISK_CONFIG["satellite_max_single_pct"],
             f"POSITION_TOO_LARGE: max {RISK_CONFIG['satellite_max_single_pct']:.0%} NAV"),
            ((portfolio["cash"] - pv) / nav < RISK_CONFIG["cash_floor"],
             f"CASH_FLOOR: would drop below {RISK_CONFIG['cash_floor']:.0%}"),
            (pv > proposed.get("adv_20d", 0) * RISK_CONFIG["max_position_pct_of_20d_adv"],
             "LIQUIDITY: exceeds 2% of 20d average daily volume"),
            (any(p["symbol"] == proposed["symbol"] for p in portfolio["open_positions"]),
             "DUPLICATE: position already open in any sleeve"),
            (proposed.get("days_to_earnings", 999) < 14,
             "EARNINGS_GATE: <14 days to earnings"),
            (proposed.get("adv_20d", 0) < 1_000_000,
             "LIQUIDITY_FLOOR: ADV20 < $1M"),
        ]
        for fail, reason in checks:
            if fail:
                return {"approved": False, "reason": reason}
        
        # Sector check
        sector = proposed["sector"]
        cap = RISK_CONFIG.get(f"sector_cap_{sector.lower()}", RISK_CONFIG["sector_cap_default"])
        sector_val = sum(p["value"] for p in portfolio["open_positions"] if p["sector"] == sector)
        if (sector_val + pv) / nav > cap:
            return {"approved": False, "reason": f"SECTOR_CAP: {sector} would exceed {cap:.0%}"}
        
        return {"approved": True, "reason": "ALL_GATES_PASSED"}
    
    def get_catastrophe_stop(self, entry_price, atr_20d_pct) -> float:
        """Returns catastrophe stop price"""
        stop_pct_atr = -(RISK_CONFIG["catastrophe_stop_atr_mult"] * atr_20d_pct)
        stop_pct = max(RISK_CONFIG["catastrophe_stop_pct"], stop_pct_atr)  # wider of the two
        return entry_price * (1 + stop_pct)
    
    def check_circuit_breaker(self, current_nav, peak_nav) -> dict:
        dd = (peak_nav - current_nav) / peak_nav
        if dd >= RISK_CONFIG["breaker_red"]:
            return {"level": "RED", "dd": dd, "new_satellite_entries": False,
                    "satellite_max": 0, "cash_floor": 1.0, "sell_all_satellite": True}
        elif dd >= RISK_CONFIG["breaker_orange"]:
            return {"level": "ORANGE", "dd": dd, "new_satellite_entries": False,
                    "satellite_max": 4, "cash_floor": 0.60}
        elif dd >= RISK_CONFIG["breaker_yellow"]:
            return {"level": "YELLOW", "dd": dd, "new_satellite_entries": False,
                    "satellite_max": 8, "cash_floor": 0.30}
        return {"level": "NORMAL", "dd": dd}
```

**Acceptance criteria:**
- [ ] `RISK_CONFIG` is the single definition of all risk limits
- [ ] `portfolio_gate.py` imported by paper trade engine and (later) live trade engine
- [ ] All references to risk limits in other files (`agentic_brain.py`, any config files) removed and redirected to `PortfolioGate`
- [ ] Unit tests: all 7 satellite rejection conditions tested and passing
- [ ] Catastrophe stop correctly computed: wider of −20% or 2.5×ATR
- [ ] Circuit breaker tested at 8%, 15%, 25% simulated drawdown
- [ ] Paper trade engine now rejects duplicates (wired from W1-T2 fix)

---

## WEEK 4 — BLOCKING: EXIT ENGINE + ANNOUNCEMENT MONITOR

### W4-T1: Exit Engine (Catastrophe + Time + CGT)

**File to create:** `backend/exit_engine.py`

```python
class ExitEngine:
    """
    Manages all exit decisions for satellite positions.
    Priority order: Thesis Stop > Catastrophe Stop > Time Stop > CGT Override.
    Circuit breaker is managed by PortfolioGate separately.
    """
    
    def check_position(self, position, current_price, atr_20d_pct, 
                       sentinel_verdict, announcement_flags) -> dict:
        entry = position["entry_price"]
        entry_date = position["entry_date"]
        days_held = (date.today() - entry_date).days
        pnl_pct = (current_price - entry) / entry * 100
        
        # Priority 1: Thesis Stop (announcement or BROKEN sentinel)
        if announcement_flags:
            return {"action": "EXIT", "reason": "ANNOUNCEMENT", "urgency": "IMMEDIATE"}
        if sentinel_verdict == "BROKEN":
            return {"action": "EXIT", "reason": "THESIS_BROKEN", "urgency": "NEXT_OPEN"}
        
        # Priority 2: Catastrophe Stop
        cat_stop = PortfolioGate().get_catastrophe_stop(entry, atr_20d_pct)
        if current_price <= cat_stop:
            return {"action": "EXIT", "reason": "CATASTROPHE_STOP", "urgency": "LIMIT_ORDER"}
        
        # Priority 3: Time Stop (day 63 horizon)
        if days_held >= 63:
            # CGT override: if within 30 days of 12-month mark and profit > 8%
            days_to_discount = max(0, 365 - days_held)
            if pnl_pct > 8 and 0 < days_to_discount <= 30:
                return {"action": "HOLD", "reason": "CGT_DEFERRAL",
                        "note": f"Hold {days_to_discount} more days for 10% CGT rate"}
            return {"action": "EXIT", "reason": "TIME_STOP_63D", "urgency": "EOD"}
        
        # Priority 4: Weakened — tighten stop (no exit yet)
        if sentinel_verdict == "WEAKENED":
            new_stop = current_price * 0.985  # tighten by 1.5%
            return {"action": "TIGHTEN_STOP", "new_stop": new_stop, "reason": "SENTINEL_WEAKENED"}
        
        return {"action": "HOLD", "reason": "NO_EXIT_CONDITION"}
```

### W4-T2: ASX Announcement Monitor

**File to create:** `backend/announcement_monitor.py`

```python
CRITICAL_KEYWORDS = [
    "capital raising", "placement", "rights issue", "entitlement offer",
    "trading halt", "voluntary suspension", "cease trading",
    "administration", "receivership", "wind up",
    "profit warning", "downgrade", "guidance cut", "earnings revision",
]

class AnnouncementMonitor:
    """Polls ASX JSON feed every 60 min for all open positions"""
    
    def check_symbol(self, code: str) -> dict:
        import requests
        from datetime import datetime, timedelta
        
        url = f"https://www.asx.com.au/asx/1/company/{code}/announcements"
        r = requests.get(url, params={"count": 20}, timeout=10)
        if r.status_code != 200:
            return {"status": "API_ERROR", "code": code}
        
        data = r.json().get("data", [])
        cutoff = datetime.now() - timedelta(hours=24)
        flags = []
        
        for ann in data:
            try:
                ann_date = datetime.strptime(ann.get("document_date",""),
                                             "%Y-%m-%dT%H:%M:%S+00:00")
                if ann_date < cutoff: continue
            except: continue
            
            title = ann.get("header","").lower()
            for kw in CRITICAL_KEYWORDS:
                if kw in title:
                    flags.append({"keyword": kw, "title": ann.get("header")})
        
        if flags:
            return {"status": "CRITICAL", "code": code, "flags": flags}
        return {"status": "CLEAR", "code": code}
```

**Acceptance criteria:**
- [ ] Exit engine handles all 5 exit conditions in correct priority order
- [ ] Catastrophe stop computed from `PortfolioGate.get_catastrophe_stop()` (single source)
- [ ] Time stop fires at day 63; CGT override correctly defers when conditions met
- [ ] Announcement monitor polls every 60 min during market hours (10AM–4:30PM AEST)
- [ ] Critical keywords trigger Telegram alert within 5 minutes
- [ ] Tested on simulated capital raise: WA1 (placed announcement in test), verified IMMEDIATE exit triggered
- [ ] Exit engine wired to paper trades: BROKEN and CATASTROPHE exits update `paper_trades` status

---

## WEEK 5 — POSITION SENTINEL + CALENDAR GATE AS TILT

### W5-T1: Position Sentinel

**File to create:** `backend/position_sentinel.py`

```python
SENTINEL_PROMPT = """
You are a portfolio risk monitor for an SMSF.
Classify the investment thesis:

INTACT   — Original thesis valid. Hold.
WEAKENED — One negative factor emerged. System will tighten stop automatically.
BROKEN   — Thesis invalidated. EXIT regardless of P&L.
            (capital raise at discount, guidance cut, CEO exit, halt for bad reason,
             balance sheet blow-up, regulatory action)

Position: {symbol} ({sector})
Entry: {entry_date} @ ${entry_price} | Current: ${current_price} ({pnl_pct:+.1f}%)
Days held: {days_held} | Earnings in: {days_to_earnings} days

Recent announcements (24h):
{announcements}

RSI: {rsi} | Volume vs 20d avg: {vol_ratio:.1f}x | {pct_from_high:.1f}% from 52w high
Macro: {macro_regime} | Sector momentum: {sector_signal}

Original thesis: {buy_thesis}

Respond EXACTLY:
VERDICT: [INTACT|WEAKENED|BROKEN]
REASON: [1-2 sentences max]
ACTION: [HOLD|TIGHTEN_STOP|EXIT]
"""
```

### W5-T2: Calendar Gate as Score Tilt (Not Entry Block)

**File to create:** `backend/calendar_gate.py`

```python
class CalendarGate:
    """
    Adjusts candidate scores based on seasonal signal.
    This is a TILT (±10-25%), NOT a hard block (except Friday and bear-market override).
    
    Justification: 9 years = 9 observations per month. Too few for absolute blocking rules.
    The data supports directional tilts; it does not support month-specific hard stops.
    """
    
    MONTHLY_MULTIPLIERS = {
        1:  {"m": 1.00, "note": "Neutral"},
        2:  {"m": 0.85, "note": "Earnings variance — tilt bearish"},
        3:  {"m": 0.75, "note": "Worst month −1.74% — strongest tilt"},
        4:  {"m": 1.20, "note": "Post-earnings clean window"},
        5:  {"m": 0.95, "note": "Slight fade"},
        6:  {"m": 0.85, "note": "EOFY tax-loss selling"},
        7:  {"m": 1.20, "note": "Strong +4.48%, 62% win rate"},
        8:  {"m": 1.10, "note": "Full-year results positive"},
        9:  {"m": 0.80, "note": "Median −1.26%, 41.6% win — tilt bearish"},
        10: {"m": 0.90, "note": "Re-entry — slight bearish tilt"},
        11: {"m": 1.25, "note": "BEST MONTH: +5.97%, 59% win — upgraded from v1"},
        12: {"m": 1.05, "note": "Christmas rally"},
    }
    
    def get_score_multiplier(self, wfo_gate, axjo_vs_sma200, vix) -> dict:
        from datetime import date
        today = date.today()
        
        # Bear market override — this IS a hard block
        if wfo_gate == "RED" and axjo_vs_sma200 < -0.10 and vix > 30:
            return {"multiplier": 0.0, "block_new_entries": True,
                    "reason": "BEAR_MARKET_OVERRIDE: WFO RED + ASX below SMA200 + VIX>30"}
        
        # Friday: block satellite entries (weekend risk uncompensated)
        if today.weekday() == 4:
            return {"multiplier": 0.0, "block_new_entries": True, "reason": "FRIDAY_BLOCK"}
        
        # September with strong macro: lift the tilt
        if today.month == 9:
            if wfo_gate == "GREEN" and axjo_vs_sma200 > 0.05 and vix < 18:
                return {"multiplier": 1.0, "block_new_entries": False,
                        "reason": "SEP_MACRO_OVERRIDE: macro too strong to tilt"}
        
        cfg = self.MONTHLY_MULTIPLIERS[today.month]
        return {"multiplier": cfg["m"], "block_new_entries": False, "note": cfg["note"]}
    
    def apply_to_score(self, model_score, multiplier) -> float:
        """Apply seasonal tilt to model score (not as a gate, as a score adjustment)"""
        return model_score * multiplier
```

**Acceptance criteria:**
- [ ] Sentinel running for all paper positions at 8:00 AM daily
- [ ] BROKEN verdict → immediate EXIT queue + Telegram alert
- [ ] WEAKENED verdict → auto-tighten catastrophe stop by 1.5% (not position exit)
- [ ] Buy thesis stored from AI debate entry, retrieved for sentinel context
- [ ] Calendar multiplier applied to score before AI debate ranking (not as a separate gate)
- [ ] November multiplier = 1.25 (upgraded from v1's 1.2)

---

## WEEK 6 — PROOF: DE-BIASED UNIVERSE + WALK-FORWARD

### W6-T1: G1 — De-Biased Return Distribution (Delisted Tickers)

```python
# Fetch inactive/delisted ASX tickers from EODHD
# GET /api/exchange-symbol-list/AU?type=Common Stock&delisted=1&api_token=...
# (This is the same call as the active list but with delisted=1 parameter)

# Run the return distribution analysis (same as Phase 2) including delisted stocks
# Expected: return distribution means drop by 2-5%/yr, medians less affected
# Produces: honest base rates for planning, freed from survivorship inflation

# Budget: ~3,000 API calls (1,500 delisted × 2 calls each for 90d + 9yr data)
# This is 0.3% of your 1M monthly limit — negligible
```

**Acceptance criteria:**
- [ ] Delisted ticker list fetched from EODHD
- [ ] Re-run of return distribution including delisted stocks
- [ ] Side-by-side table: survivor-only vs de-biased returns published in strategy doc
- [ ] Projections updated with de-biased base rates (may reduce mean estimates by 2–5%/yr)

### W6-T2: G2 — Walk-Forward Validation on Fixed Labels + 2022 Bear

```python
# Build backend/backtest.py (this file does not yet exist)
# Walk-forward validation using the path-aware label (W2)
# Test period: 2022-01-01 to 2026-07-31 (includes 2022 bear market)
# Universe: core 216 stocks by liquidity
# Exit rules: catastrophe stop + time stop (from W4)
# Costs: 0.8% round-trip

def run_walk_forward(universe, start, end, exit_engine, costs=0.008):
    """
    Rolling 6-month train / 3-month test windows
    Returns: decile hit rates, average returns, Sharpe, max drawdown by period
    """
    pass  # implement in Week 6

# G2 PASS criteria:
# - Top score decile: P(+8% before -8%) >= 60% net of costs
# - Bottom decile: < 40%
# - 2022 bear period: max drawdown < 25% with circuit breaker active
# - Sharpe >= 0.8 over full period
```

**Acceptance criteria:**
- [ ] `backend/backtest.py` created and functional
- [ ] Walk-forward run completes on core 216 for 2022–2026
- [ ] Results table: hit-rate by score decile, avg return, Sharpe, max drawdown
- [ ] 2022 period specifically reviewed: circuit breaker behaviour validated
- [ ] G2 pass/fail determination documented

---

## WEEK 7 — CORE SLEEVE + FRANKING CALENDAR

### W7-T1: Core Sleeve Implementation

The Core sleeve (60–70% NAV) is the portfolio anchor. It is NOT model-driven — it is a quality filter applied once per semi-annual review.

**Core candidates (6–10 positions):**

| Symbol | Type | Yield | Franked | Why Core |
|---|---|---|---|---|
| CBA | Mega financial | 3.5% | 100% | Median +9.8%/yr tier, franking |
| BHP | Mega materials | 4–6% | Variable | Commodity anchor, portfolio hedge |
| WES | Mega consumer | 3% | 100% | Most consistent consumer |
| WOW | Mega consumer | 3% | 100% | Defensive, franked |
| CSL | Mega healthcare | 1% | Partial | Healthcare anchor, low correlation |
| GMG | Mega REIT | 1% | Partial | Industrial REIT, growth profile |
| VAS | ETF | — | — | Broad ASX exposure, reduces stock-specific risk |
| VGS | ETF | — | — | Global developed exposure |

**Entry:** Buy once, hold. Not traded on 63-day signals. Exit only on:
- Portfolio circuit breaker Red
- Thesis break (capital raise, sustained earnings deterioration, management failure)
- Semi-annual review says position no longer meets quality criteria

**Acceptance criteria:**
- [ ] Core sleeve positions tracked in separate `core_positions` table
- [ ] Core sleeve excluded from satellite gate checks (different rules)
- [ ] Semi-annual review dates scheduled (February, August)
- [ ] Core + Satellite combined ≤ 85% NAV (15% cash floor always)

### W7-T2: Franking Credit Calendar + Ex-Div Tracker

```python
def get_upcoming_dividends(symbol):
    """Fetch from EODHD dividend endpoint"""
    data = eodhd_get(f"div/{symbol}.AU", {"from": date.today().isoformat()})
    if not data: return None
    upcoming = [d for d in data if date.fromisoformat(d["date"]) > date.today()]
    if not upcoming: return None
    next_div = upcoming[0]
    ex_date = date.fromisoformat(next_div["date"])
    return {
        "symbol": symbol,
        "ex_div_date": str(ex_date),
        "days_to_ex_div": (ex_date - date.today()).days,
        "dividend_amount": next_div.get("value", 0),
    }
```

---

## WEEK 8 — WFO GATE v2 + REPORTING

### W8-T1: WFO Gate v2 (Corrected Thresholds)

**Current problem:** WFO evaluates at 30/63/90 days but the label matures at 63 days. A 30-day gate on a 63-day signal measures noise. Also, Sharpe ≥ 0.25 is too low for retirement capital.

**Corrected WFO gate for GREEN status:**
```python
WFO_GREEN_CRITERIA = {
    "min_signals_evaluated": 100,           # not 8 weeks of weekly scans
    "min_hit_rate": 0.55,                   # 55% path-aware label hit rate
    "min_sharpe_ci_lower_bound": 0.50,      # CI lower bound ≥ 0.5 (not just > 0)
    "evaluation_horizon": 63,               # must match label horizon
    "min_weeks_consistent": 8,              # 8 consecutive weeks above threshold
}
# Note: earliest a 63-day WFO row can exist is ~2026-09-19 (first scan was 2026-07-18)
# This is why September full deployment is not achievable under the plan's own gate
```

**Acceptance criteria:**
- [ ] WFO gate updated to use 63-day evaluation horizon
- [ ] GREEN criteria updated to: ≥100 signals AND ≥55% hit AND CI lower ≥ 0.5
- [ ] `wfo_metrics` table begins populating (first eligible date: ~Sep 19, 2026)

---

## WEEK 9 — MICRO-LIVE PROTOCOL (G4)

### W9-T1: Micro-Live at $500–$1K/position

```
MICRO-LIVE PROTOCOL (4 weeks):
  Position size: $500–$1,000 per satellite entry (not the full SMSF size)
  Purpose: measure actual execution quality vs paper trade assumptions
  
  Measure per trade:
  - Entry slippage: (actual fill price - signal price) / signal price
  - Exit slippage: (signal price - actual fill price) / signal price
  - Order fill time from signal generation to execution
  
  Target: average slippage ≤ 0.35%/side (0.70% round-trip)
  
  If slippage > 0.5%/side on 5+ trades:
  → Use limit orders tighter to the spread
  → Consider time-of-day execution optimisation (first 30 min typically wider spreads)
```

**Acceptance criteria:**
- [ ] 4 weeks of micro-live data: ≥8 satellite trades opened and closed
- [ ] Slippage measured and documented per trade
- [ ] Average slippage ≤ 0.35%/side confirmed
- [ ] All portfolio gate checks confirmed working on live ComSec execution
- [ ] Stop-loss execution tested: one deliberate small loss to confirm stop fires correctly

---

## WEEK 10 — GO/NO-GO + NOVEMBER DEPLOYMENT

### W10-T1: G1–G5 Gate Review

| Gate | Minimum | Status | Decision |
|---|---|---|---|
| G1: De-biased base rates | Completed, results documented | [ ] Pass / [ ] Fail | |
| G2: Walk-forward P(+8% before −8%) | Top decile ≥ 60% net of costs | [ ] Pass / [ ] Fail | |
| G3: Live paper | ≥60 trades, ≥52% hit, 0 bugs, 0 missed announcements | [ ] Pass / [ ] Fail | |
| G4: Micro-live | Slippage ≤ 0.35%/side, execution confirmed | [ ] Pass / [ ] Fail | |
| G5: Gate consistency | One risk-limit file, WFO CI-lower ≥ 0.5 | [ ] Pass / [ ] Fail | |

**If all G1–G5 pass:** November full satellite deployment (best month: +5.97% avg, 59% win rate).  
**If any gate fails:** Extend paper + micro-live. Identify specific failure. Do not override gates.

### W10-T2: November Deployment Ramp

```
November 1: Deploy core sleeve (6-8 positions, ~60% NAV)
            Use limit orders; allow 3-5 business days to fill all positions
            
November 1+: Satellite begins at FULL SMSF sizing
             First positions: 4-6 highest-score candidates from updated core 216
             Position size: per PortfolioGate.compute_satellite_size()
             Max: 8 satellite positions, 40% NAV total
             
December onwards: Full operation
                  System runs daily autonomous scan → paper debate → Telegram alert → manual order
```

---

## OPERATIONAL RUNBOOK (CORRECTED)

### Daily Schedule (AEST)

| Time | Task | Component |
|---|---|---|
| 4:55 AM | Macro data fetch (AUD/USD, copper, gold, VIX) | `macro_rotation.py` |
| 5:00 AM | Full scan: 335 broad + 216 core | `scanner.py` |
| 5:30 AM | Feature computation (adjusted_close, no stale rows) | `model_training.py` |
| 6:00 AM | Score + calendar tilt + percentile ranking | `calendar_gate.py` |
| 7:00 AM | AI debate for top-tier satellite candidates | `agentic_brain.py` |
| 7:30 AM | PortfolioGate validation for approved entries | `portfolio_gate.py` |
| 7:45 AM | Telegram signal report | Alert system |
| 8:00 AM | Position sentinel for all open positions | `position_sentinel.py` |
| 8:15 AM | Announcement check for all open positions | `announcement_monitor.py` |
| **8:30 AM** | **MANUAL: Review Telegram, place limit orders in ComSec** | Human |
| Every 60 min | Announcement re-check (10AM–4:30PM) | `announcement_monitor.py` |
| 4:30 PM | Market close: update P&L + circuit breaker check | `portfolio_gate.py` |
| 4:40 PM | CGT timer check | `tax_tracker.py` |
| 4:45 PM | Daily summary Telegram | Alert system |

### Weekly

| Day | Task |
|---|---|
| **Sunday 6PM** | Macro rotation dashboard + regime label + sector weights |
| **Sunday 8PM** | Model hit rate check (8-week rolling actuals vs path-aware label) |
| **Sunday 9PM** | Weekly portfolio summary Telegram: P&L, open positions, regime, calendar signal |

### Monthly

| Task | When |
|---|---|
| EODHD universe re-rank (re-run Phase 1 liquidity scan) | 1st of month |
| Core 216 list refresh | 1st of month |
| SMSF trade records CSV export for accountant | 1st of month |
| CGT review: positions approaching 12-month threshold | 1st of month |
| Model retraining with latest 3 months of data | Quarterly |
| SMSF tax rule review | July + January |
| Core sleeve semi-annual review | February + August |

---

## SMSF TRADE RECORD KEEPING (ATO REQUIREMENT)

```
REQUIRED FIELDS PER TRADE:
  - Buy date, sell date
  - Symbol + company name
  - Shares purchased, entry price, ComSec brokerage paid
  - Shares sold, exit price, ComSec brokerage paid
  - Gross profit/loss
  - CGT rate: 15% (hold <12 months) or 10% effective (hold ≥12 months)
  - Net after-tax profit/loss
  - Sleeve: CORE or SATELLITE
  - Exit reason: catastrophe-stop / time-stop / thesis-broken / CGT-deferral / capital-raise / circuit-breaker
  - AI debate verdict (stored as text log, not required to be kept but useful for review)

STORAGE: PostgreSQL trades table
EXPORT: Quarterly CSV → SMSF fund accountant
ATO: SMSF annual return requires all trades with dates and amounts
```

---

## CORRECTED COST REALITY

| Activity | Cost | Annual Total |
|---|---|---|
| Satellite brokerage: 12 positions × 3 round-trips × ~$45 avg | $1,620 | 0.81% of $200K NAV |
| Entry slippage: ~0.3%/side on satellite entries | ~$1,440 | ~0.72% |
| Exit slippage | ~$1,440 | ~0.72% |
| **Total friction estimate** | — | **~2.3%/yr** |

**Net of friction and 15% tax, 5%/quarter gross ≈ 15.9–16.9%/yr net.** This is lower than the "18.3%" in the top-line projection because turnover compounds friction. Budget ~2%/yr friction in your expectations.

---

## 3-MONTH LIVE SUCCESS CRITERIA

After 3 months of full-size satellite trading (i.e., by February 2027):

| Metric | Target | Action if Below |
|---|---|---|
| Satellite hit rate (path-aware) | ≥ 52% | Raise score threshold; check model drift |
| Avg return per closed satellite position | ≥ 4.0% net of costs | Review entry threshold (raise to 50+) |
| Catastrophe stop frequency | ≤ 1 per quarter expected (≤18% of positions) | Review: systematic? Single sector? |
| Core sleeve return | ≥ 8%/yr (in-line with mega-cap median) | Review core holdings; not the model |
| Missed announcements | 0 | Fix polling frequency immediately |
| Circuit breaker triggered | Record and review — correct action? | Yes = normal; No = fix the trigger |
| Quarterly portfolio return (after tax + costs) | ≥ 3.5% | Full strategy review if 2 consecutive quarters miss |

---

*v2 implementation plan prepared August 2026. Corrects build order, eliminates conflicting risk specs, retires −7% mechanical stop, pushes to November deployment with validated pipeline. References existing backend files: `agentic_brain.py`, `model_training.py`, `scanner.py`. Note: `backend/backtest.py` does not exist and must be built in Week 6.*

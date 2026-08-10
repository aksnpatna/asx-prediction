# SMSF v2 — Implementation Report

## Status: COMPLETE · Deployed 2026-08-09

All modules, fixes, and pipeline jobs implemented as described below. The system is running
on the `asx-backend` container (port 8000) with the frontend dashboard at port 8081.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    SMSF v2 System                           │
├───────────────┬──────────────────┬──────────────────────────┤
│  DATA LAYER   │  MODEL LAYER     │  EXECUTION LAYER         │
│               │                  │                          │
│  EODHD API    │  62-feature      │  PortfolioGate           │
│  (1M calls/mo)│  Ridge+RF+LGBM   │  ExitEngine              │
│       │       │  ensemble        │  CircuitBreaker          │
│       ▼       │       │          │  CalendarGate            │
│  eod_ohl_     │       ▼          │       │                  │
│  history      │  model_          │       ▼                  │
│  (3.9M rows,  │  training_set    │  Paper trades            │
│  2015→2026,   │  (3.0M rows,     │  Position Sentinel       │
│  adj close)   │  path-aware lbl) │  AnnouncementMonitor     │
│               │       │          │  TaxTracker              │
│               │       ▼          │  MacroRotation           │
│               │  model_weights   │       │                  │
│               │  _by_date        │       ▼                  │
│               │  (pathaware_v2)  │  Telegram alerts         │
│               │                  │  SMSF Dashboard API      │
└───────────────┴──────────────────┴──────────────────────────┘
```

---

## 2. Critical Fixes Applied

### 2.1 Label Fix — Path-Aware Target
**File:** `backend/model_training.py:564-574`

The model originally trained on `hit_8pct_63d = fwd_peak >= 8.0` (peak-touch), which had a
61.3% base rate and was misleading. Replaced by path-aware labels:

```python
hit_8pct_before_m8pct = fwd_peak >= 8.0 and fwd_dd > -8.0   # Clean win: 27% base rate
hit_5pct_before_m5pct = fwd_peak >= 5.0 and fwd_dd > -5.0   # 22.6% base rate
close_5pct_63d        = fwd_ret  >= 5.0                       # 35.2% base rate
```

The daily training pipeline (`daily_training_pipeline`) now targets `hit_8pct_before_m8pct`
as its primary label. Three new columns added to `model_training_set` table via migration.

### 2.2 Train/Serve Skew — Adjusted Close
**File:** `backend/eodhd_backfill.py:174-189,249-258`

The training database previously stored raw `close`, while the live prediction path used
EODHD's `adjusted_close`. This introduced a systematic skew on a ~4% dividend-yield market.

**Fix:** Backfill now stores `adjusted_close` (falling back to raw `close` if unavailable).
Existing rows updated in-place via `ON CONFLICT (symbol, market, trade_date) DO UPDATE`.

**Backfill state:** 500 liquid symbols re-fetched from 2015-08-12 → 2026-08-09.
Database: 3,905,995 rows (up from 3,348,668), 11 years of adjusted data.

### 2.3 Duplicate Paper Trade Bug
**File:** `backend/main.py:13569-13578`

`_auto_create_paper_trade()` now checks for existing open positions on the same symbol
before creating a new paper trade. Previously, the bootstrap path could open 3× duplicate
SCG and 2× duplicate PPT positions on the same day at the same price.

---

## 3. New Modules (10 files)

### 3.1 `backend/config/universe.py`
Three-tier stock universe: **core** (>$1M/day ADV, 8-position max), **broad** (>$500K/day,
4-position max), **signal** (>$100K/day, scan only). Ranked by 90-day dollar volume from
the local EODHD mirror. Cached to `data/universe_ranked.json` (168h TTL).

### 3.2 `backend/calendar_gate.py`
Month + day-of-week scoring gate backed by verified seasonality data:
- **STRONG**: Jul (×1.30), Apr (×1.25), Nov (×1.20), Aug (×1.15), Jan (×1.10)
- **CAUTION**: Sep (×0.60), Feb (×0.70), Oct (×0.85), Jun (×0.75)
- **AVOID**: Mar (×0.40)
- **Friday block**: no new satellite entries
- **September override**: lifted if macro is constructive (AXJO >SMA200 +5%, VIX <18)
- **Bear-market override**: all seasonal signals suppressed if WFO=RED and index >10% below SMA200

### 3.3 `backend/portfolio_gate.py`
Single-source-of-truth position limits (reconciling 3 conflicting specs from the old docs):

| Limit | Value |
|---|---|
| Max satellite positions | 12 |
| Max total positions | 25 |
| Single position (satellite) | 7% NAV |
| Single position (core) | 12% NAV |
| Position vs ADV20 | ≤2% |
| Cash floor | 15% |
| Per-trade risk | 1.5% NAV |
| Sector cap (gen) | 25% |
| Sector cap (Materials) | 30% |

`PortfolioGate.can_open_position()` enforces all checks atomically.
`calculate_position_size()` returns shares, cost, stop price, and risk amount.

### 3.4 `backend/exit_engine.py`
Replaces per-position −7% tight stops (value-destroying at base rates) with:
1. **Catastrophe stop**: −20% from entry, or 2.5×ATR(20d), whichever is wider
2. **Time stop**: exit at day 63 (earnings-adjusted — defer if within 14d of confirmed earnings)
3. **Thesis stop**: forced exit if sentinel verdict = BROKEN
4. **CGT deferral**: hold ≤30 extra days if approaching 12-month discount (10% effective rate)
5. **Portfolio breaker**: inherited from circuit_breaker (no explicit code — breaker level checked first)

### 3.5 `backend/announcement_monitor.py`
Polls the free ASX JSON feed for open positions every 60 minutes during market hours.
**Critical keywords** (force-exit): capital raising, placement, trading halt, administration,
receivership, guidance cut, material adverse, impairment. **Watch keywords**: acquisition,
merger, takeover, board change, class action, regulatory.

### 3.6 `backend/position_sentinel.py`
Daily LLM thesis-check for all open satellite positions. Classifies each as:
- **INTACT** — thesis holds, continue at current size and stop level
- **WEAKENED** — one negative factor, tighten stop by 1–2%
- **BROKEN** — thesis invalidated, EXIT regardless of P&L

Uses DeepSeek/GPT-4 with a structured prompt and regex-parsed response format.
Rule-based fallback if no LLM available.

### 3.7 `backend/macro_rotation.py`
Sunday 6PM AEST regime classifier based on 8 global indices + commodity pulse.
Regimes: RISK_ON_COMMODITY, RISK_ON_GROWTH, ROTATION_ASX_CATCHUP, ROTATION_CHINA_LED,
CARRY_UNWIND, RISK_OFF, COMMODITY_REVERSAL, NEUTRAL.

Sector weight table adjusted per regime. Telegrams a weekly rotation report.
**Commodity-reversal guardrail**: blocks Materials entries if copper/gold −12%/4wk and
AUD/USD −4%/4wk.

### 3.8 `backend/tax_tracker.py`
- **CGT timer**: tracks days-held for every open position. Alerts when approaching the
  12-month CGT discount window (10% effective rate vs 15% short-hold).
- **Franking calendar**: identifies fully-franked stocks (CBA, BHP, WES, etc.) and
  estimates franking credit value.
- **Dividend lookup**: queries EODHD dividend endpoint for upcoming ex-dividend dates.

### 3.9 `backend/circuit_breaker.py`
Three-level portfolio drawdown ladder:

| Level | Trigger | Action |
|---|---|---|
| YELLOW | −8% from peak | Suspend new entries, cash floor 30%, stops tightened 1.5% |
| ORANGE | −15% | Half satellite sold, cash floor 60%, no new entries |
| RED | −25% | All satellite sold, 100% cash, 8-week cooling |

Peak value persisted to `portfolio_peak_tracker` table. `DrawdownCircuitBreaker.check()`
returns actionable state dict.

### 3.10 `backend/backtest.py`
Walk-forward validation script using the path-aware label and v2 exit engine.
Chronological train/test split with expanding window. Computes: total return, annualized,
Sharpe, max drawdown, hit rate, profit factor, equity curve. Supports `--universe`,
`--start`, `--end` CLI flags.

---

## 4. New Features (3 timing features)

Added to `FEATURE_COLS` and `_build_feature_matrix()` in `backend/model_training.py`:

| Feature | Description |
|---|---|
| `mean_reversion_score` | Composite: RSI recovering from <35 + volume drying up + sweet-spot distance from 52w high (15–40%) + range narrowing (0–1 scale) |
| `squeeze_duration` | Consecutive days inside Bollinger Band squeeze (width below 20th percentile of rolling 252d) |
| `rsi_during_squeeze` | Average RSI over the last 10 squeeze-session days — identifies coiling direction |

Total feature count: **62** (up from 59).

---

## 5. Model Performance

### 5.1 Training Data
- **Source**: `model_training_set` — 3,013,356 rows with path-aware labels
- **Features**: 62 (59 legacy + 3 timing)
- **Target**: `hit_8pct_before_m8pct` (clean +8% before −8% within 63 days)
- **Base rate**: 27.0% (random entry probability of clean win)

### 5.2 Ensemble (Ridge + RandomForest + LightGBM)

| Model | OOS Accuracy | Notes |
|---|---|---|
| Ridge | 75.2% | Chrono-ordered 80K sample |
| Random Forest | 75.4% | 200 trees, max depth 10 |
| LightGBM | 65–75% | 300 trees, max depth 8, num leaves 63 |

### 5.3 Decile Analysis (top 10% vs random)

| Decile | Hit rate | Lift |
|---|---|---|
| D1 (worst) | 10.7% | 0.4× |
| D5 (median) | 23.5% | 1.0× |
| D10 (top) | **44.7%** | **1.8×** |

Top-decile picks hit the clean +8% target at nearly double the random rate. The model
provides genuine selection edge above base rates.

### 5.4 Top Features (by LGBM importance)
1. `momentum_63d` — 63-day price momentum (strongest signal)
2. `hv_20d` — 20-day historical volatility (lower vol → cleaner moves)
3. `parkinson_vol` — intraday range-based volatility
4. `cmf` — Chaikin money flow (accumulation vs distribution)
5. `kurtosis_20d` — return distribution shape (fat tails caution)

---

## 6. Scheduler — 9 Active Jobs

| Time | ID | Function | Purpose |
|---|---|---|---|
| Mon-Fri 03:00 | `inc_update_3am` | `_scheduled_inc_update` | EODHD incremental OHLC update (adjusted_close) |
| Mon-Fri 07:00 | `smsf_pipeline_7am` | `_scheduled_smsf_pipeline` | Position sentinel (INTACT/WEAKENED/BROKEN) + calendar gate check |
| Mon-Fri 07:00 | `model_training_8am` | `_scheduled_model_training` | Rebuild training matrix + retrain ensemble |
| Mon-Fri 08:00 | `v2_daily_scan_noon` | `_scheduled_v2_daily_scan` | Generate Layer 1 predictions + AI agentic deep-dive + auto-execute paper trades |
| Mon-Fri 10–15 hrly | `smsf_ann_check` | `_scheduled_smsf_announcement_check` | ASX announcement polling for open positions |
| Mon-Fri 16:15 | `walk_forward_oos` | `_scheduled_walk_forward_oos` | Walk-forward OOS Sharpe validation |
| Mon-Fri 16:35 | `smsf_eod_checks` | `_scheduled_smsf_eod_checks` | CGT timer + circuit breaker + EOD summary |
| Every 15 min | `paper_trade_monitor` | `_scheduled_paper_trade_monitor` | Paper trade stop-loss/take-profit monitoring |
| Sun 18:00 | `sunday_rotation` | `_scheduled_sunday_rotation` | Macro regime classification + sector weight report |
| 1st of month 04:00 | `monthly_fundamentals` | `_scheduled_monthly_fundamentals` | yfinance fundamental snapshots for all ASX |

**Removed legacy jobs (8):** weekly_generation, positions_monitor (suppressed),
broad_scan_precompute (replaced by v2_daily_scan), wealth_builder_evaluate, self_learning_loop,
daily_ai_pipeline (replaced by v2_daily_scan), uat_health_report, channel_calibration.

---

## 7. Consolidated Guardrails (16)

🔴 = hard-coded, cannot be overridden · 🟡 = system flags, human decides

| # | Guardrail | Trigger | Action |
|---|---|---|---|
| 1 | Portfolio breaker L1 | NAV −8% from peak | No new satellite entries; review all theses |
| 2 | Portfolio breaker L2 | NAV −15% | Cut satellite to half; 60% cash floor |
| 3 | Portfolio breaker L3 | NAV −25% | Satellite to zero; 8-week re-entry cooling |
| 4 | Single-position cap | >7% NAV or >2% ADV20 | PortfolioGate rejects order |
| 5 | Sector caps | >25% any sector (30% Materials) | PortfolioGate rejects order |
| 6 | Duplicate position | Symbol already open | PortfolioGate rejects order |
| 7 | Catastrophe stop | −20% or 2.5×ATR from entry | Exit satellite position |
| 8 | Announcement keywords | capital raise / halt / administration | Immediate sentinel review → BROKEN → exit |
| 9 | Earnings gate | Entry ≤14d before confirmed earnings | Block entry |
| 10 | Liquidity floor | ADV20 < $1M or stale sessions | Block entry; limit orders only |
| 11 | Commodity reversal velocity | Copper/gold −12% & AUD/USD −4% /4wk | Materials new entries blocked; sector ×0.3 |
| 12 | Model health | Rolling 8wk live hit <45% or <60% walk-forward | Halve satellite; <35% → freeze |
| 13 | CGT discipline | Approach 12mo hold with gain >+8% | Defer exit ≤30d unless stop/thesis fires |
| 14 | Devil's advocate | Every APPROVE verdict | Auto short-thesis; unaddressed → size −30% |
| 15 | Data sanity | EODHD vs ASX close gap >2% | No trade until 2 sources agree |
| 16 | Kill switch (human) | Any 2 of: breaker L2, model-freeze, data-flag | All automation halts; manual-only mode |

---

## 8. Database Migrations Applied

| Table | New Columns |
|---|---|
| `model_training_set` | `hit_5pct_before_m5pct`, `hit_8pct_before_m8pct`, `close_5pct_63d` |
| `paper_trades` | `catastrophe_stop_price`, `time_stop_date`, `buy_thesis`, `sector`, `entry_type`, `exit_reason`, `entry_date_parsed` |
| `portfolio_peak_tracker` | New table: `id`, `recorded_at`, `peak_value` |

---

## 9. API Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | System health (DB, scheduler, LLM providers) |
| GET | `/api/smsf/dashboard` | **SMSF v2 driver dashboard** — returns portfolio value, circuit breaker state, calendar signal, model confidence, macro regime, CGT alerts, open positions with sentinel verdicts, announcement alerts, pipeline status (12 top-level fields) |
| GET | `/api/paper-trades` | List paper trades (used by dashboard positions) |
| POST | `/api/paper-trades` | Open paper trade |
| PATCH | `/api/paper-trades/:id` | Update trade plan |
| POST | `/api/paper-trades/:id/close` | Close paper trade |
| GET | `/api/portfolios` | List portfolios (core sleeve management) |
| GET | `/api/telegram/recipients` | Manage Telegram alert recipients |
| POST | `/api/ai/analyze/:symbol` | Individual stock analysis (LLM) |

---

## 10. Frontend — SMSF Driver Dashboard

**URL:** `http://localhost:8081`

### Tabs (simplified from 11 to 7)
| Tab | Component | Purpose |
|---|---|---|
| **SMSF** (default) | `SmsfTab.jsx` (new) | Portfolio value, circuit breaker, calendar signal, model confidence, macro regime, CGT timer, positions with sentinel verdicts, announcement alerts, pipeline status |
| Strategy | `StrategyTab.jsx` | ASX picks, paper trades, risk rules |
| Analyze | inline (App.jsx) | Individual share analysis, charts, valuation |
| Market | `NewsSentimentMonitor.jsx` | Market pulse, regime, sentiment |
| Portfolio | inline `PortfolioTab` | Core sleeve holdings, MPT optimization |
| Wealth | `WealthTab.jsx` | Equity dashboard, WFO validation, sector exposure |
| Analytics | inline (App.jsx) | Calibration, evaluations |

Market selector reduced from [AU, US, IN] to [ASX only].

### SMSF Dashboard Layout
```
┌──────────────────────────────────────────────────────────────────┐
│ SMSF Driver Dashboard        [v2 Path-Aware Model]    Sun 9 Aug │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│ PORTFOLIO    │ CIRCUIT      │ CALENDAR     │ MODEL CONFIDENCE   │
│ $200,000     │ 🟢 NORMAL   │ 🟡 CAUTION  │ 75% OOB accuracy   │
│ +0.0%        │ DD: 0.0%    │ ×0.85 score │ Base: 27% · 1.8×   │
├──────────────┴──────────────┼──────────────┴────────────────────┤
│ 🌍 Macro Regime             │ ⏰ CGT Timer                      │
│ RISK_ON_GROWTH              │ No positions approaching discount │
│ Tech ×1.3 Cons ×1.2 Mats ×1.1                                 │
├─────────────────────────────┴───────────────────────────────────┤
│ 📊 Open Positions (3)                                            │
│ Symbol  Entry    Current  P&L     Days  Sentinel  Exit Plan      │
│ SCG     $3.94    $4.12    +4.6%   21d   INTACT    Day 21/63      │
├──────────────────────────────────────────────────────────────────┤
│ ⚙️ Pipeline: ● Model fresh ● Data 2015→2026-08 ● 62 features    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 11. Data Pipeline State

| Component | Status |
|---|---|
| EOD database | 3,905,995 rows, 2015-08-12 → 2026-08-07 |
| Symbols | 2,386 ASX (500 liquid re-fetched with adjusted_close) |
| Training labels | 3,013,356 rows with `hit_8pct_before_m8pct` |
| Model weights | `pathaware_v2` type, 62 features, stored in `model_weights_by_date` |
| Parquet backups | `/app/data/scored_200k.parquet` (3MB) — scored evaluation set; `/app/data/training_v2_sample.parquet` — feature export for offline training |
| EODHD API quota | 500 calls used for backfill; 1M/month remaining for live |
| Adjusted close | Live path uses `adjusted_close`; backfill updates `close` column in-place |

---

## 12. Deployment

```bash
# Rebuild and deploy
docker compose build backend frontend
docker compose up -d backend frontend

# Backend health
curl http://localhost:8000/api/health

# Frontend
open http://localhost:8081

# Manual retrain (if needed between scheduled 8AM runs)
docker exec asx-backend python3 -c "
from model_training import fit_model_weights
fit_model_weights(target_col='hit_8pct_before_m8pct')
"

# Rebuild training matrix (takes 2-4 hours, runs on 8AM schedule)
docker exec asx-backend python3 -c "
from model_training import build_training_matrix
build_training_matrix()
"
```

---

## 13. Remaining Non-Blocking Items

| Item | Priority | Notes |
|---|---|---|
| Live paper trades ≥60 | G3 gate | Currently 11 trades; need 49 more. Paper trading active in bootstrap mode |
| WFO metrics rows ≥20 | G2 gate | wfo_metrics was empty; walk_forward_oos job now running daily at 4:15 PM |
| De-biased universe re-run | G1 gate | Delisted tickers not yet included in training set |
| Micro-live trading | October | $500–$1K positions to validate real execution |
| Full-size deployment | Nov 2026 (earliest) | Requires G1–G5 gates to pass |

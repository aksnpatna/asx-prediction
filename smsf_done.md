# SMSF v2 — Final Implementation Summary

**Deployed:** 2026-08-12 · **Status:** Running · **System:** asx-backend + asx-frontend + asx-db (Docker)

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                    SMSF v2 (Core-Satellite)                │
├────────────────┬─────────────────┬─────────────────────────┤
│   DATA LAYER   │   MODEL LAYER   │    EXECUTION LAYER      │
│                │                 │                         │
│  EODHD API     │  62-feature     │  PortfolioGate          │
│  (100K/day)    │  Ridge 50% +    │  (7% single pos)       │
│       │        │  LGBM 30% +     │  ExitEngine             │
│       ▼        │  RF 20%         │  (catastrophe/time/CGT) │
│  eod_ohl_      │       │         │  CircuitBreaker         │
│  history       │       ▼         │  (YELLOW/ORANGE/RED)    │
│  (3.97M rows,  │  path-aware     │  CalendarGate           │
│  2015→2026,    │  labels         │  (month+Friday block)   │
│  adj close)    │  (hit_8pct_     │  KillSwitch             │
│                │  before_m8pct)  │  (2-of-3 trigger)       │
│  ┌──────────┐  │       │         │  Devil's Advocate       │
│  │167 liquid│  │       ▼         │  (counter-thesis)       │
│  │core +    │  │  model_weights  │  DataSanity             │
│  │500 broad │  │  _by_date       │  (EODHD vs Yahoo >2%)   │
│  │+ signal  │  │  (47 active)    │  ModelHealth            │
│  └──────────┘  │                 │  (8wk rolling hit rate) │
│                │                 │  StaleHeartbeat         │
│  core_sleeve   │                 │  (5% stale → freeze)    │
│  (12 candidates)│                │  Manual 2% NAV cap      │
│  CBA,BHP,WES,  │                 │  AnnouncementMonitor    │
│  WOW,CSL,GMG,  │                 │  (1hr during market)    │
│  NAB,WBC,MQG,  │                 │                         │
│  TLS,VAS,VGS   │                 │                         │
└────────────────┴─────────────────┴─────────────────────────┘
```

---

## The 6 Guardrails

| # | Guardrail | What it does | File |
|---|-----------|-------------|------|
| 1 | **Portfolio Gate** | 7% single position cap, 8 max satellite, 25% sector cap, 15% cash floor, 2% ADV limit | `portfolio_gate.py` |
| 2 | **Exit Engine** | Catastrophe stop (-20%/2.5×ATR), time stop (day 63), CGT deferral (+8% gain near 12mo) | `exit_engine.py` |
| 3 | **Circuit Breaker** | YELLOW -8% (no new entries), ORANGE -15% (halve exposure), RED -25% (liquidate) | `circuit_breaker.py` |
| 4 | **Calendar Gate** | Month multipliers (Jul +30%, Mar -60%), Friday block, September macro override | `calendar_gate.py` |
| 5 | **Position Sentinel** | Weekly LLM evaluation: INTACT / WEAKENED / BROKEN per open position | `position_sentinel.py` |
| 6 | **Kill Switch** | Halts all automation when any 2 of: circuit breaker L2+, model freeze, data quality | `kill_switch.py` |

Additional automated checks:
- **Devil's Advocate** — LLM counter-thesis on every APPROVE, -30% position size if unaddressed risk
- **Data Sanity** — EODHD close vs Yahoo close must agree within 2%
- **Model Health** — rolling 8-week hit-rate alerts (freeze if <35%)
- **Stale Heartbeat** — >5% core-universe sessions stale = freeze
- **Manual Trade Cap** — Telegram BUY limited to 2% NAV

---

## Daily Pipeline (3 AM – 8 AM)

| Time | Job | What happens |
|------|-----|-------------|
| 03:00 | `inc_update_3am` | EODHD incremental OHLC (latest trading day) |
| 05:00 | `broad_scan_5am` | Score 467 liquid symbols (alphabetical, deterministic) → `wealth_scan_cache` |
| 07:00 | `smsf_pipeline_7am` | Position sentinel — LLM verdicts on open positions |
| 07:00 | `model_training_7am` | Incremental matrix rebuild + ensemble fit (200K samples) |
| 08:00 | `v2_daily_scan_8am` | Top candidates → 6-persona AI debate → APPROVE/REJECT → auto paper trades |
| 10:00–15:00 | `smsf_ann_check` | Hourly ASX announcement monitoring for open positions |
| 15:00 | `paper_trade_monitor` | 15-min interval: check stops, targets, trailing stops |
| 16:15 | `walk_forward_oos` | WFO validation vs cached scan predictions |
| 16:30 | `pipeline_health_1630` | 8-stage Telegram report: Data, Scan, Model, AI Scan, Positions, Scheduler, Kill, Heartbeat |
| 16:35 | `smsf_eod_checks` | CGT timer, circuit breaker, portfolio summary, model health, kill switch |
| Sun 18:00 | `sunday_rotation` | Weekly macro regime report |

**All 12 jobs persist in PostgreSQL** (`apscheduler_jobs` table) and survive Docker restarts. Missed jobs auto-fire on startup via 24-hour misfire grace.

---

## Share Selection Flow (deterministic)

```
467 liquid symbols (alphabetical, same every run)
  ↓
config/universe.py: 90-day dollar volume filter
  core (>$1M/day, max 8)  |  broad (>$500K/day)  |  signal (>$100K/day)
  ↓
EODHD OHLC → calculate_technical_indicators() → 43 indicators
  ↓
model_weights_by_date: 62-feature ensemble score (Ridge 50% + LGBM 30% + RF 20%)
  ↓
Top 5 candidates → 6-persona AI debate (Quant, Fundamental, Macro, Momentum, Risk, Lead PM)
  ↓
Devil's Advocate LLM check (-30% size if unaddressed risk)
  ↓
7 Guardrail checks: PortfolioGate → CircuitBreaker → DataSanity → KillSwitch → CalendarGate
  ↓
Position sizing: 7% single cap, per-user, 1.5% risk per trade
  ↓
Auto paper trade created → Telegram Buy button → monitoring starts
```

**Why same result every time:** alphabetical sort of symbol pool, deterministic EODHD data, no random.shuffle().

---

## Exit Discipline (priority order)

| Priority | Trigger | Action |
|----------|---------|--------|
| 1 | Thesis break (position_sentinel=BROKEN) | EXIT immediately |
| 2 | Catastrophe stop (-20% or 2.5×ATR, whichever wider) | EXIT |
| 3 | Time stop (day 63, flat) | Close |
| 4 | Circuit breaker RED (-25% portfolio drawdown) | Close all satellite |
| 5 | CGT deferral (>8% gain near 12 months) | Hold for discount |

Trailing stop: 3% from peak. Take-profit: +8% target or day 63 close.

---

## Model

- **Features:** 62 technical + macro features
- **Target:** `hit_8pct_before_m8pct` (path-aware, not peak-touch)
- **Base rate:** 27.3% of 63-day windows hit +8% before -8%
- **Architecture:** Ridge 50% + LightGBM 30% + RandomForest 20% (with 20-round early stopping)
- **Samples:** 200K chronological rows (2015-2026), 80/20 train/test split
- **Sample weighting:** Exponential recency decay (half-life 1 year)
- **Regularization:** ElasticNetCV auto-tunes L1/L2 ratio, alpha, and pruning threshold

Top 5 features (by weight): copper/gold ratio (2.05), VIX (-1.13), AUD/USD trend (-0.90), RSI (-0.07), CMF (-0.06)

---

## Tax Efficiency

- **CGT tracking:** 12-month timer on every position; alert at day 350; deferral if >+8% gain
- **Franking credits:** Recorded per position; 45% SMSF benefit rate
- **Manual trades:** 2% NAV hard cap on Telegram BUY commands

---

## Files

| File | Purpose |
|------|---------|
| `backend/main.py` | API server, all 12 cron jobs, catchup engine, 15,000+ lines |
| `backend/model_training.py` | 62-feature matrix build + ensemble fit |
| `backend/config/universe.py` | 467-symbol tier system (core/broad/signal) |
| `backend/eodhd_backfill.py` | EODHD OHLC incremental + bulk fetch |
| `backend/portfolio_gate.py` | Position sizing and risk limits |
| `backend/exit_engine.py` | Catastrophe/time/thesis stop + CGT |
| `backend/circuit_breaker.py` | Drawdown-based portfolio protection |
| `backend/calendar_gate.py` | Month/seasonality entry filters |
| `backend/position_sentinel.py` | LLM position health monitoring |
| `backend/devils_advocate.py` | Counter-thesis LLM on every APPROVE |
| `backend/kill_switch.py` | Emergency halt on 2-of-3 triggers |
| `backend/model_health.py` | Rolling 8-week hit-rate tracking |
| `backend/data_sanity.py` | EODHD vs Yahoo price gap check |
| `backend/stale_heartbeat.py` | Stale session detection |
| `backend/core_sleeve.py` | Core holding management (12 candidates) |
| `backend/announcement_monitor.py` | ASX announcement monitoring |

---

## What runs tomorrow morning (Wednesday Aug 13)

| Time (AEST) | Job | Expected result |
|-------------|-----|----------------|
| 03:00 | inc_update | ~2,300 new OHLC rows from yesterday |
| 05:00 | broad_scan | 467 symbols scored, top 5 identified |
| 07:00 | model_training | Fresh weights from 200K samples |
| 08:00 | v2_daily_scan | 6-persona AI debate, auto paper trades |
| 16:30 | pipeline_health | Telegram: "All systems healthy" or failures |
| 16:35 | smsf_eod_checks | CGT alerts, circuit breaker status |

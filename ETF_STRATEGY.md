# ETF Pipeline — Separate Strategy (Target 12%+ p.a.)

A self-contained ETF strategy, kept **separate** from the stock satellite so
neither pollutes the other. ETFs are beta instruments, not 63-day right-tail
hunters — the stock model's path-aware +8%/−8% label is the wrong tool for
them, and ~25 of the stock model's 45 features (earnings, announcements,
franking-adjusted fundamentals) don't exist for ETFs.

## Why a separate pipeline

| | Stock satellite (458) | ETF pipeline |
|---|---|---|
| Goal | Hunt mid-cap right tail (multi-baggers) | Capture broad/global beta + sector momentum |
| Horizon | 63-day cycles | ~monthly rebalance, trend-following |
| Model | LGBM ensemble, path-aware label | SMA200 trend filter + relative momentum |
| Risk | High idiosyncratic | Low, diversified |
| Tax edge | CGT 10% (>12mo) + franking | Franking on AU ETFs only |

## Strategy (transparent, non-lookahead)

Three levers, all computed from local `eod_ohl_history` (adjusted close):

1. **Trend filter** — price vs 200-day SMA. Above → risk-on; below → de-risk.
   This is the single highest-value crash filter.
2. **Momentum rank** — cross-sectional rank of blended 63d + 126d returns
   among the ROTATE universe; hold the top-3 with positive momentum *and*
   price above trend.
3. **Regime allocation** — a fixed weight matrix, not a prediction:

   | Regime | Core | Rotate | Bond | Cash |
   |---|---|---|---|---|
   | risk_on  | 55% | 40% | 0% | 5% |
   | neutral  | 55% | 30% | 10% | 5% |
   | risk_off | 35% | 0% | 45% | 20% |

4. **Crash vehicle** — when trend breaks hard (price < SMA200 *and* 20-day
   drawdown −8%), rotate into BOND + CASH (VGB/VAF/IAF + AAA/BILL).

## Universe (22 ASX-listed ETFs, all AUD → no FX drag)

- **CORE** (8): VAS, IOZ, A200, VGS, VTS, IVV, NDQ, HNDQ — broad domestic +
  global/US beta. Held through the cycle as the anchor.
- **ROTATE** (9): TECH, ASIA, FANG, RBTZ, CURE, BNKS, FUEL, GOLD, OZF —
  sector/theme momentum vehicles.
- **BOND** (3): VGB, VAF, IAF — flight-to-safety.
- **CASH** (2): AAA, BILL — parking.

## Backtest (honest, non-lookahead)

Signals use prior month-end close; returns measured over the following month.
Fee drag 0.4%/yr (MER + round-trip brokerage). Franking **not** modelled →
conservative (AU ETFs add ~0.3–0.6%/yr after SMSF refund).

**2020-01 → 2026-08 (6.58y):**

| Metric | Value |
|---|---|
| CAGR | **11.89%** |
| Total return | +109.5% |
| Annualised vol | 10.84% |
| Sharpe | 0.73 |
| Max drawdown | −15.21% |

**2022 bear stress (2022-01 → 2026-08):** CAGR 12.48%, max drawdown **−10.65%**
vs ASX200 ~−15–20% peak-trough. Risk-off fired Feb 2022, rotated into GOLD/FUEL
(the commodity winners that year).

## The honest caveat on "12%"

11.9% CAGR is **before SMSF tax and before franking**, over a period (2020–2026)
that includes a strong post-COVID bull. It is *not* a promise of 12% every
year — like the stock strategy, it has sequence risk. What it delivers
honestly:

- ~12% gross CAGR at **10.8% vol and −15% max drawdown** — roughly the same
  gross return as the stock model's *optimistic* projection, at ~half the
  assumed volatility, with fully mechanical (no-AI, no-debate) rules.
- Comparable-to-better than an all-ASX buy-and-hold, with materially lower
  drawdown and zero reliance on the fragile stock-model alpha.

**Use case:** this is the natural vehicle for the **core sleeve** (Part 5 of
the deep-dive) and for the "can't beat the index, want 12% without the single-
stock risk" objective. It is NOT a replacement for the satellite's right-tail
hunt — it is the calm, mechanical half of the portfolio.

## Files

- `backend/etf_universe.py` — instrument catalog + tiers
- `backend/etf_pipeline.py` — indicators, regime, signals, backtest
- `backend/eodhd_backfill.py` — `backfill_etf_universe()` OHLC population
- `backend/main.py` — `etf_signals`/`etf_positions` tables, `_scheduled_etf_signal`
  (4:30pm mon-fri), API endpoints below

## API

- `GET /api/etf/universe` — full ETF catalog
- `GET /api/etf/regime` — current risk_on/neutral/risk_off + VAS state
- `GET /api/etf/signals` — today's weights + momentum picks
- `GET /api/etf/backtest?start=2020-01-01` — run the strategy backtest
- `POST /api/etf/backfill` — populate ETF OHLC from EODHD

## Ops

```bash
# one-time + refresh ETF OHLC
python backend/eodhd_backfill.py --mode etf

# run signals / backtest standalone
python backend/etf_pipeline.py --signals
python backend/etf_pipeline.py --backtest --start 2020-01-01
python backend/etf_pipeline.py --regime
```

Daily 4:30pm scheduler job persists a signal snapshot to `etf_signals`.

# EODHD Fundamentals Data — Complete Inventory

**Date:** 2026-08-13 · **Plan:** $59.99 Fundamentals Data Feed

---

## Data Available (from /api/fundamentals/{TICKER}.AU)

| Section | Key fields | Status | Use |
|---------|-----------|--------|-----|
| **General** | Code, Name, ISIN, Exchange, Currency | ✅ | Identity (have) |
| **Highlights** | MarketCap, PERatio, PEGRatio, WallStreetTargetPrice, DividendYield, EarningsShare | ✅ | Have (yfinance) |
| **Valuation** | TrailingPE, ForwardPE, PriceSales, PriceBook, EnterpriseValue | ✅ | Have (yfinance) |
| **SharesStats** | SharesOutstanding, PercentInsiders, PercentInstitutions, ShortRatio | ⚠️ Short=NULL for ASX | **NEW: ownership** |
| **Technicals** | Beta, 52WeekHigh/Low, 50/200DayMA | ✅ | Have (computed) |
| **SplitsDividends** | ForwardDividendYield, PayoutRatio, ExDividendDate | ✅ | **NEW: payout ratio** |
| **InsiderTransactions** | director buy/sell, shares, price | ✅ ASX has data | **NEW: insider signal** |
| **ESGScores** | TotalEsg, GovernanceScore, ControversyLevel | ✅ | **NEW: ESG** |
| **Earnings.History** | 82 qtrs epsActual vs epsEstimate (1986-2026) | ✅ | **NEW: EPS surprise** |
| **Earnings.Trend** | EPS estimate revisions, growth, # analysts | ✅ | **NEW: estimate momentum** |
| **Earnings.Annual** | 40yr annual epsActual | ✅ | **NEW: EPS trend** |
| **Financials** | Balance_Sheet / Cash_Flow / Income_Statement (quarterly 40yr + yearly 39yr) | ✅ | **NEW: point-in-time** |

### Delisted Data (separate endpoint)
- `GET /api/exchange-symbol-list/AU?delisted=1&type=common_stock` → **1,858 delisted ASX stocks**
- Then `/api/eod/{TICKER}.AU` for their historical OHLC
- **Purpose:** G1 survivorship-bias fix (strategy doc line 21/409)

---

## What We'll USE (final list)

### A. Integrity fixes (not features — data quality)
1. **Delisted OHLC backfill** (1,858 stocks) → G1 survivorship de-biasing
2. **Point-in-time financials** (40yr) → replace yfinance 4yr + look-ahead proxy

### B. New candidate features to TEST (measure-and-revert)

| # | Feature | Source | Hypothesis |
|---|---------|--------|-----------|
| 1 | `eps_surprise` | Earnings.History (actual vs estimate %) | Earnings beat → positive catalyst |
| 2 | `eps_estimate_revision` | Earnings.Trend (growth %) | Rising estimates → momentum |
| 3 | `analyst_count` | Earnings.Trend (# analysts) | More coverage → better priced |
| 4 | `pct_insiders` | SharesStats.PercentInsiders | High insider ownership → alignment |
| 5 | `pct_institutions` | SharesStats.PercentInstitutions | Institutional support |
| 6 | `insider_net_ratio` | InsiderTransactions (net buy/sell) | Directors buying → conviction |
| 7 | `esg_governance` | ESGScores.GovernanceScore | Governance quality |
| 8 | `esg_controversy` | ESGScores.ControversyLevel | Controversy → risk |
| 9 | `payout_ratio` | SplitsDividends.PayoutRatio | Dividend sustainability |

### C. NOT usable (documented)
- **Short interest** → `SharesShort` = NULL for all ASX (not available)
- **Intraday** → irrelevant for 63-day swing strategy
- **US Ticks** → US-only

---

## Current Model (before EODHD fundamentals)

| Metric | Value |
|--------|-------|
| Features | 62 (technical + macro + snapshot fundamentals) |
| Top decile | 45.5% |
| AUC | 0.688 |
| Samples | 300K (optimal sweet spot) |

## Expected honest impact of G1 + point-in-time

- **Base rates drop 4-6pp** (survivorship de-biasing) — strategy doc line 409
- **Top decile** likely 45.5% → ~40% (honest, not a regression)
- **New features** may add signal back on top of the honest baseline

---

## Test Discipline (applied to every new feature)

```
Hypothesis → build feature → retrain 300K → measure decile delta → keep only if it helps
```

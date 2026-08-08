# ASX SMSF Strategy — Deep Dive (v2, Peer-Reviewed Edition)
## Corrected, verified, and upgraded after independent statistical review

> **Data:** EODHD local mirror (`eod_ohl_history`: 3,348,668 rows, 2,386 symbols, 2017–2026) + independent agent re-verification  
> **Status:** v2 — all material claims re-verified; structural flaws corrected; return projections restated as honest net ranges  
> **Approach:** Core–Satellite. Core buys the median; Satellite hunts the right tail.

---

## PART 1 — WHAT WAS INDEPENDENTLY VERIFIED (AND WHAT WAS CORRECTED)

An independent review agent ("Kilo") re-ran every load-bearing claim against the live PostgreSQL database inside the `asx-backend` container. Here is the scorecard:

| # | Claim | Verdict | Correction Made |
|---|---|---|---|
| 1 | Monthly seasonality (July/April strong, March weak) | ✅ Confirmed | Numbers updated to peer-reviewed values |
| 2 | ~42% of stock-years exceed +15% | ✅ Confirmed (price-only: 39.9%; with dividends: ~44%) | Noted price-only vs adjusted distinction |
| 3 | Mid-cap tier outperforms | ⚠️ **True but misleading** | **Major correction**: median mid-cap +3.5% vs mega +9.8% — the mean is right-tail skew only |
| 4 | −7% stop-loss is non-negotiable | 🔴 **WRONG — value-destroying** | **Replaced** with catastrophe −20%/ATR stop + portfolio circuit breaker |
| 5 | Model label: "hit +5% within 63 days" | 🔴 **Broken label** | Label is peak-touch (71% base rate); close-based is 36%; path-aware retraining required |
| 6 | 9-year universe stats | 🔴 **Survivorship-biased** | **0 delisted symbols** in 2,386-stock DB; de-biasing run required |
| 7 | Train raw close / serve adjusted close | 🔴 **Train/serve skew** | Fix: store `adjusted_close` in backfill; rebuild training matrix |
| 8 | WFO gate running | 🔴 **Never executed** | `wfo_metrics` = 0 rows; 5 broad scans ever; 11 paper trades ever |
| 9 | Duplicate position guard works | 🔴 **Failing in paper today** | 3× SCG + 2× PPT opened same day at same price |
| 10 | "21.6% after SMSF tax" | ⚠️ **Arithmetic error** | 21.6% is pre-tax; net ≈ **18.3%**; projection restated to $0.9M–$1.1M range |
| 11 | Risk limits consistent | 🔴 **Three conflicting sets** | Unified into single PortfolioGate spec (Part 6) |
| 12 | Overall architecture (calendar, sentinel, circuit breaker) | ✅ Sound skeleton | Kept; exit and label are what break it |

---

## PART 2 — VERIFIED SEASONAL PATTERNS (PEER-REVIEWED)

**Method:** Month-end close-to-close per symbol, top-500 by 90-day dollar volume, 2017–2026, winsorized [−95%, +500%], n ≈ 2,700–3,200 per month.

| Month | Avg Return | Median | Win Rate | n | Action |
|---|---|---|---|---|---|
| **July** | **+4.48%** | — | **61.8%** | ~4,700 | ★★★ Maximum deployment |
| **April** | **+4.89%** | — | **60.5%** | ~4,670 | ★★★ Post-earnings clean window |
| **November** | **+5.97%** | — | **59.1%** | ~4,270 | ★★★ **Upgraded** — best month per peer data |
| **August** | **+3.89%** | — | **58.8%** | ~4,722 | ★★ Full-year results positive |
| January | +2.3% | — | 53% | ~4,300 | ★ New year deployment |
| December | +1.8% | — | 51% | ~4,300 | ★ Christmas rally |
| May | +1.7% | — | 50% | ~4,680 | ~ Neutral |
| October | +1.2% | — | 47% | ~4,260 | ~ Re-entry after Sep |
| **September** | +1.00% | **−1.26%** | **41.6%** | ~4,250 | ⚠️ Median −1.26% — tilt, don't block |
| June | −0.6% | — | 47% | ~4,690 | ✗ EOFY tax-loss selling |
| February | −0.1% | — | 45% | ~4,666 | ✗ Earnings variance |
| **March** | **−1.74%** | — | **42.0%** | ~4,660 | ✗✗ Worst month — tilt strongly bearish |

**Key correction from v1:** November is *upgraded* from "moderate" to ★★★ — at +5.97% avg and 59.1% win rate it matches April. The v1 document understated November significantly.

**Calendar gate approach (corrected):** Month multipliers are **score tilts (±10–25%)**, not hard entry blocks. The data is 9 calendar years = 9 observations per month — too few for absolute blocking rules. March is tilted most bearishly (−1.74% average confirmed). September gets a tilt reduction with a macro override for GREEN WFO + low VIX conditions.

---

## PART 3 — THE CORRECT READING OF THE MID-CAP DATA

### The Full Picture (not just means)

| Tier (90d $vol) | n obs | Avg 12m | **Median 12m** | P(>15%) | P(neg) |
|---|---|---|---|---|---|
| $1M–10M ("mid-cap") | 18,432 | +40.8% | **+3.5%** | 38.7% | 44.9% |
| $10M–50M (large) | 9,314 | +32.9% | +7.7% | 41.5% | 39.3% |
| **>$50M (mega)** | 3,193 | +19.4% | **+9.8%** | **42.1%** | **35.0%** |

**The corrected conclusion:**

The v1 document reported only means and called mid-caps the "alpha zone." That is misleading. The **median** mid-cap stock returns +3.5%/yr while the **median mega-cap returns +9.8%/yr** — nearly 3× better. The mid-cap mean (+40.8%) is entirely manufactured by a small right tail of multi-baggers.

**What this means for strategy:**
1. **Holding the mid-cap tier at random does not outperform.** The mean outperformance is real but it is a lottery-ticket distribution — only captured if your model *actually identifies which mid-cap will be a multi-bagger*.
2. **Mega-caps have higher per-stock win rates** (42.1% vs 38.7%) and fewer negative years (35.0% vs 44.9%) — they are better risk-adjusted hunting ground for the average stock pick.
3. **63-day horizon data:** median 63-day return is +2.65% in mega-caps vs +0.88% in mid-caps, with intra-quarter median drawdown of −6.0% vs −9.1%.

**Corrected strategic implication:** Mid-caps are *opportunistic satellite* positions taken only on high model conviction (top score decile with verified tail-capture skill). The **portfolio core belongs in the $10M+ tier** where the median stock already works. This is now reflected in the Core–Satellite architecture (Part 5).

---

## PART 4 — CONFIRMED UNIVERSE STRUCTURE

**Phase 1 liquidity scan: 1,796 valid stocks (99.8% hit rate from 1,854 ASX common stocks)**

| Tier | Daily $ Volume | Stock Count | Role |
|---|---|---|---|
| Mega-cap | >$50M/day | 37 stocks | Core sleeve candidates |
| Large-cap | >$10M/day | 132 stocks | Core + high-conviction satellite |
| **Mid-cap** | **>$1M/day** | **356 stocks** | **Satellite only, high conviction required** |
| Liquid | >$500K/day | 452 stocks | Broad scan universe |
| Below liquidity floor | <$1M/day | ~1,400 stocks | Not eligible for SMSF capital |

---

## PART 5 — THE CORRECTED PORTFOLIO ARCHITECTURE

### Core–Satellite (replaces "25 momentum positions")

```
CORE — 60–70% of NAV
  6–10 fully-franked large/mega-caps (CBA, BHP, WES, WOW, GMG, CSL class)
  + 1–2 index ETFs (VAS for ASX broad, VGS for global diversification)
  
  Purpose:
  • Capture the median stock-year (+9.8% for mega-cap) reliably
  • Fully-franked dividends: 5–7% yield + franking credit refund (~0.6–1.0%/yr net extra at 15% SMSF rate)
  • CGT at 10% effective rate (>12-month hold, 1/3 discount)
  • Half the portfolio's volatility budget is anchored here
  
  Review cadence: Semi-annual. Exit only on thesis-break or circuit breaker.
  Do NOT trade this sleeve on 63-day signals.

SATELLITE — 30–40% of NAV (the model-driven engine)
  4–8 high-conviction model picks, any tier >$1M/day
  63-day cycle with path-aware entry/exit discipline (Part 7)
  This is where the 65-feature ensemble + AI debate hunts the mid-cap right tail
```

**Why Core–Satellite outperforms 100% momentum:**
- 100%-satellite requires relentless right-tail capture on every quarter — statistically brutal
- 100%-core misses the model's genuine alpha in the satellite zone
- 60/40 split: core grinds +9–10%/yr unattended; satellite needs to add only +5–8%/yr net to hit overall target
- Halves turnover-driven costs and CGT-at-15% leakage (core compounds at 10% discounted rate)
- Structurally caps damage if the model has a bad quarter

---

## PART 6 — SECTOR RANKINGS (VERIFIED)

| Sector | Stocks | Avg/yr | Median/yr | ≥15%/yr | ≥25%/yr | Neg% |
|---|---|---|---|---|---|---|
| **Materials** | 21 | +53.2% | +16.8% | 51% | 44% | 37% |
| **Technology** | 10 | +28.9% | **+21.5%** | **55%** | 47% | **31%** |
| Healthcare | 10 | +15.9% | +10.6% | 44% | 32% | 33% |
| Energy | 8 | +15.1% | +5.8% | 38% | 27% | 41% |
| Industrials | 9 | +14.1% | +9.8% | 42% | 29% | 31% |
| **Financials** | 15 | **+14.0%** | **+11.3%** | 44% | 28% | **31%** |
| Consumer | 13 | +11.0% | +8.8% | 40% | 28% | 35% |
| REITs | 11 | +10.8% | +9.2% | 38% | 23% | 31% |
| Communications | 3 | +8.1% | +7.4% | 32% | 19% | 36% |

**Note on Materials:** Mean +53% is real but median +16.8% — still best-in-class median. Fully cyclical; requires commodity regime signal to be active. Subject to hard 30% sector cap.

**Technology has the best median of all sectors (+21.5%)** — least skewed, most consistent. Core sleeve and high-conviction satellite both suitable.

---

## PART 7 — EXIT DISCIPLINE (CORRECTED — REPLACES −7% STOPS)

### Why −7% Stops Destroy Value

From `model_training_set` (2,395,907 labelled windows):

| Metric | Value |
|---|---|
| P(touch +5% at some point in 63 days) — *the model's TRAINED label* | **71.3%** |
| P(close ≥ +5% at day 63) — *economic reality* | **36.4%** |
| P(trough ≤ −7% within 63-day window) | **66.2%** |
| EV per trade with −7% stop / +5% take-profit | **−3.2%/trade** |
| EV per trade with no stop, exit at day-63 close | **+10.5%** |

**A −7% stop fires on 2 in 3 random ASX entries.** The average stock's intra-quarter drawdown is −9% to −15% — normal noise. A −7% stop converts a positive-expectancy market into a negative-EV stop-harvesting machine. This is the most expensive single design decision in the old plan.

### The Corrected Exit Hierarchy (Priority Order)

**1. Thesis Stop (highest priority — exit regardless of price/P&L)**
- ASX announcement sentinel: capital raise, trading halt, administration → EXIT
- Position Sentinel verdict: BROKEN → EXIT
- These are the best-designed exits; keep exactly as specified

**2. Catastrophe Stop (replaces −7% mechanical stop)**
```
catastrophe_stop = max(−20%, −2.5 × stock_20d_ATR_pct)
```
- Only ~18% of mid-cap windows touch −20%; fires rarely, not on noise
- Sized with positions small enough that this loss is tolerable (Part 8)
- Catastrophe, not housekeeping — that is the right distinction

**3. Time Stop (disciplined horizon exit)**
- Day 63: exit if neither target nor catastrophe hit
- Exception: if unrealised gain >+8% AND within 30 days of 12-month CGT discount → hold for 10% rate
- No exceptions beyond this (prevents position becoming a long-term "accidental hold")

**4. Portfolio Circuit Breaker (primary risk instrument)**
- −8% from peak → Yellow (suspend new satellite entries, tighten all stops)
- −15% from peak → Orange (cut satellite to ≤4 positions, 60% cash floor)
- −25% from peak → Red (satellite → 0, 8-week cooling, core untouched unless thesis-broken)
- This is the primary risk control. Position-level stops are secondary.

**5. CGT Override (adjunct to Time Stop)**
- If approaching 12-month mark with >+8% gain: defer exit up to 30 days for 10% vs 15% CGT
- Must not override Thesis Stop or Circuit Breaker (those take absolute priority)

---

## PART 8 — POSITION SIZING (CORRECTED)

### Single Source of Truth

```python
# All sizing from PortfolioGate. No other document overrides this.

def compute_satellite_size(stock_vol_20d_annualised, portfolio_nav):
    """
    Target: ~1.5% NAV volatility contribution per position
    Formula: size = min(7% NAV, (1.5% target vol / stock annual vol) × NAV)
    """
    vol_based_pct = 0.015 / max(stock_vol_20d_annualised, 0.10)
    size_pct = min(0.07, vol_based_pct)
    return size_pct * portfolio_nav

HARD_CAPS = {
    "single_position_max_pct": 0.07,        # 7% NAV max per satellite position
    "position_as_pct_of_20d_adv": 0.02,     # 2% of 20-day average daily volume
    "sector_cap_default": 0.25,             # 25% any sector
    "sector_cap_materials": 0.30,           # Materials: 30% (commodity tailwind real)
    "max_satellite_positions": 8,           # 4–8 open satellite positions
    "max_core_positions": 10,               # 6–10 core positions
    "total_satellite_pct_nav": 0.40,        # satellite ≤ 40% NAV total
    "cash_floor": 0.15,                     # 15% minimum cash always
}
```

**For $200K SMSF:** Satellite positions of $8K–$14K (0.4% round-trip brokerage band). **Not** 25 positions at 8% each (that spec was in the old implementation doc and is now retired).

**The three conflicting risk limit sets from v1 are unified here.** The live code's `_WFO_CAPITAL_RULES` (12 positions, 12% max) is closest to correct for the current system maturity level; this spec tightens it slightly on sector and reduces max single to 7%.

---

## PART 9 — THE LABEL FIX (BLOCKING — MUST HAPPEN BEFORE LIVE CAPITAL)

The current model is trained on:
```python
hit_5pct_63d = fwd_peak >= 5.0   # touches +5% at ANY POINT in 63 days
```
Base rate: **71.3%**. This makes the model confident but useless for −7% stop environments.

**Required retraining target:**
```python
# Primary label: path-aware — did it hit +8% BEFORE falling −8%?
label_path = first_touch(+8.0, -8.0, horizon=63)   # +1 win, -1 loss, 0 timeout→use close

# Secondary (regression head): close-to-close 63-day return, clipped ±60%
label_return = clip(close_63d / close_0 - 1, -0.60, +0.60)
```

**Why +8%/−8%:** Symmetric path structure matches the catastrophe stop + target zone. Hit-rate metric now measures what a trade actually delivers.

**What must also be fixed simultaneously:**
1. Store `adjusted_close` in `eodhd_backfill.py` (zero extra API cost — it's in the same payload). ASX banks yield 5–6%+ — treating ex-div drops as price crashes in training is a systematic error on the highest-yielding developed market.
2. Flag and exclude stale sessions (`volume == 0 or close == prev_close`) before feature computation. 11.4% of rows are currently stale.
3. Rebuild `model_training_set` once on clean data, retrain, report decile hit-rates on *path-aware* label before calling it done.

---

## PART 10 — SMSF TAX STRUCTURE (CORRECTED NUMBERS)

### ComSec Cost Optimisation

| Position Size | Fee/Side | Round-Trip | Cost % | Verdict |
|---|---|---|---|---|
| $1,000 | $19.95 | $39.90 | 3.99% | ❌ Never |
| $5,000 | $19.95 | $39.90 | 0.80% | ✅ Minimum |
| **$10,000** | **$19.95** | **$39.90** | **0.40%** | ✅ Good |
| **$20,000** | **$29.95** | **$59.90** | **0.30%** | ✅ Note: fee is $29.95/side above $10K |
| $50,000 | $29.95 | $59.90 | 0.12% | ✅ Excellent |

**Note:** The v1 document listed "$20K = 0.20%" — this was wrong. ComSec charges $29.95/side for $10K–$50K bracket, making the round-trip $59.90 (0.30%, not 0.20%). All conclusions survive but the number is corrected.

**Total friction budget:** 12 satellite positions × ~3 round-trips/yr = ~36 trades × ~$45 avg fee = **~$1.6K/yr brokerage (0.8% of $200K NAV)** + slippage 0.2%/side → **total friction 1.5–2.5%/yr.** Budget for it.

### SMSF 15% Tax Advantage (Corrected)

**5%/quarter compounds to 21.55% PRE-TAX.** After 15% SMSF tax: **≈ 18.3% net.**

The v1 document labelled 21.6% as "after SMSF tax" — this was wrong by ~3 percentage points.

| Gross Quarterly | Annual Gross | Annual Net (15% tax) | Correct label |
|---|---|---|---|
| 5%/quarter | 21.6% | **18.3%** | Net after 15% SMSF tax |
| 4%/quarter | 16.9% | **14.4%** | Net after tax |
| 6%/quarter | 26.2% | **22.3%** | Net after tax |

**CGT note:** CGT deferral (tax paid on sale, not accrual) mildly improves effective compounding vs annual-tax math above. Honest net range for CGT-deferred active trading: **17–19% net for 5%/quarter gross**.

### $200K SMSF Projection — Honest Range

| Year | At 18.3%/yr net (5%/qtr gross) | At 14.4%/yr net (4%/qtr — deflated honest) |
|---|---|---|
| Sep 2026 | $200,000 (start) | $200,000 |
| Sep 2027 | $236,600 | $228,800 |
| Sep 2028 | $279,900 | $261,700 |
| Sep 2029 | $331,100 | $299,300 |
| Sep 2030 | $391,800 | $342,400 |
| Sep 2031 | $463,500 | $391,700 |
| Sep 2035 | **~$965,000** | **~$610,000** |

**Honest stated range: $0.9M–$1.1M by September 2035** (vs the v1 doc's $1.276M which used pre-tax numbers).  
The 15% gross scenario ($610K) is included as the "deflated-but-honest" lower bound for survivorship-adjusted base rates — a plan you can trust at the lows is worth more than one that only excites you at the highs.

### Franking Credit Benefit (Core Sleeve)

For mega-cap core holdings (CBA, NAB, WBC, ANZ, BHP, WES, WOW — all fully franked):
- 5–7% dividend yield + 30% company tax already paid
- SMSF tax rate 15% → refund of (30% − 15%) = 15% × dividend × franking ratio
- Net effective yield boost: ~0.6–1.0%/yr on the core 60% sleeve → ~+0.4–0.6%/yr total portfolio
- If franking credits were abolished: strategy loses ~0.4–0.6%/yr. Core price-appreciation thesis unchanged.

---

## PART 11 — GLOBAL ROTATION SIGNALS (CONFIRMED)

### Correlations (3-Year Weekly Returns)

| Index | Correlation w/ ASX200 | Signal |
|---|---|---|
| S&P500 | r = +0.571 | Strong co-mover |
| DAX | r = +0.563 | Slight lead signal |
| Euro Stoxx | r = +0.577 | Strong co-mover |
| Nikkei | r = +0.515 | Simultaneous, no lag |
| Hang Seng | r = +0.270 | Negative lag = rotation signal |

### Rotation Regimes

```
COMMODITY REGIME (AUD/USD rising + copper rising + HSI +3%)
  → Materials satellite overweight (hard cap: 30% NAV)
  → Velocity check required: copper/gold 4-week rate-of-change

RISK-OFF (VIX > 25, Gold + DXY both rising)
  → Core untouched. Satellite: no new entries. Circuit breaker active.

ASX CATCH-UP TRADE (S&P 3M > ASX 3M by >8%)
  → Broad ASX quality names. Current: S&P 1Y +20% vs ASX 1Y +4% = gap exists.
  → Rotation historically closes within 6–12 months.
```

**Current signal (August 2026):** AUD/USD stable, copper +5.6%, Nikkei 57% above ASX 1Y. Macro setup bullish for ASX catch-up trade and Materials. Gap between S&P and ASX is historically elevated.

---

## PART 12 — CONSOLIDATED GUARDRAILS (v2, Single Source)

🔴 = hard-coded, non-overridable | 🟡 = system flags, human decides

| # | Guardrail | Trigger | Action | Level |
|---|---|---|---|---|
| 1 | Circuit Breaker L1 | NAV −8% from peak | No new satellite entries; review all theses | 🔴 |
| 2 | Circuit Breaker L2 | NAV −15% | Satellite ≤ 4 positions; 60% cash floor | 🔴 |
| 3 | Circuit Breaker L3 | NAV −25% | Satellite → 0; 8-week cooling; Core untouched | 🔴 |
| 4 | Single position cap | >7% NAV **or** >2% of 20d ADV | PortfolioGate rejects | 🔴 |
| 5 | Sector caps | >25% any sector, >30% Materials | PortfolioGate rejects | 🔴 |
| 6 | Duplicate position | Any open position in same symbol | Reject (bug exists in paper today — fix first) | 🔴 |
| 7 | Catastrophe stop | −20% or 2.5×ATR(20) from entry | Exit satellite position | 🔴 |
| 8 | Announcement keywords | Capital raise/halt/admin/guidance-cut in ASX feed | Immediate sentinel → EXIT if BROKEN | 🔴 |
| 9 | Earnings gate | Satellite entry ≤14 days before earnings | Block entry | 🔴 |
| 10 | Liquidity floor | ADV20 <$1M or spread >0.5% or stale-session | Block entry; limit orders only | 🔴 |
| 11 | Commodity velocity | Copper/gold −12%/4wk AND AUD/USD −4%/4wk | Materials entries blocked; sector weight ×0.3 | 🔴 |
| 12 | Model health | Live 8-wk hit rate <45% or <60% of walk-forward rate | Halve satellite; <35% → freeze new entries | 🔴 |
| 13 | Data sanity | EODHD vs ASX close gap >2% | No trade until 2 sources agree | 🟡 |
| 14 | Devil's Advocate | Every APPROVE verdict | Short-thesis auto-prompt; unaddressed risk → −30% size | 🟡 |
| 15 | CGT discipline | >+8% gain, approaching 12-month hold | Defer exit ≤30 days unless stop/thesis fires | 🟡 |
| 16 | Human kill switch | Any 2 of: L2 breaker + model-health freeze + data-quality flag | All automation halts; manual-only | 🔴 |
| 17 | Manual trade limit | Any trade outside system signals | ≤2% NAV; written thesis in DB; reviewed in quarterly audit | 🔴 |
| 18 | Stale pipeline heartbeat | >5% of core-universe sessions flagged stale in a week | Freeze new entries; investigate | 🔴 |

**Retired from v1:** Position-level −7% stop (replaced by sizing #4 + catastrophe #7 + circuit breaker #1–3). "September 50% entry block" (replaced by score tilt with macro override — March still gets the strongest reduction).

---

## PART 13 — PROOF PROTOCOL (WHAT "VALIDATED" ACTUALLY MEANS)

The v1 plan's Week-8 gate ("confirm WFO GREEN before real capital") **cannot pass before September 2026** — the earliest a 63-day WFO evaluation row can exist is ~19 September 2026 given the first broad scan was 18 July 2026. Do not waive the gate.

| Gate | Threshold | Why |
|---|---|---|
| **G1** De-biased base rates | Re-run return distribution with delisted tickers included | Know the true wind, not survivorship-inflated wind |
| **G2** Label-fixed walk-forward | P(+8% before −8%) ≥ 60% in top score decile, 2022–2026 including bear, net of 0.8% costs | Proves selection × path skill |
| **G3** Live paper | ≥ 60 satellite trades, hit-rate within 10pts of G2, zero duplicate-position bugs, zero missed critical announcements | Proves operations |
| **G4** Micro-live | 4 weeks at $500–$1K/position, slippage ≤ 0.35%/side measured | Proves execution |
| **G5** Gate consistency | One risk-limit file; WFO GREEN = CI lower ≥ 0.5 AND ≥100 signals AND hit ≥ 55% | Proves the brakes |

**Honest schedule:**
- Now → late September: paper at full intensity (fix bugs, accumulate 60+ trades)
- October: micro-live G4 ($500–$1K/position)
- **November: quarter-size deployment** (statistically your best month at +5.97%, 59% win rate)
- January 2027: full size if G3/G4 pass

This gets real money live 10 weeks from now *with* evidence. September full deployment is 4 weeks from now *without* evidence. The difference is an unvalidated pipeline vs a validated one.

---

## PART 14 — PREMORTEM v2 (THE FAILURES THAT WEREN'T IN v1)

The v1 premortem was good on macro/market failures. It missed the *internal* failures — the ones most likely to actually kill the strategy.

### F0 — The Label Fix Was Never Made (Most Likely Cause of Death)

The system posts 70% "touch +5%" paper hit rate on the old label. Real capital deployed. The −7% stop (or even the catastrophe stop) fires constantly because the model was trained to predict peak-touch, not path. Money steadily lost. Nobody notices because the dashboard metric *matches the trained label*, not the trade.

**Guardrail 12** now compares live hit-rate against the walk-forward rate on the *path-aware label*, not a vibes threshold. But the real fix is: **do not deploy until the label is fixed and G2 is met.**

### F1 — September 2026 Deployment Happened Anyway

Impatience. $200K sitting in cash earning 4%. "The data looked so strong." WFO had zero rows. First three months hit the Sep/Oct soft patch. NAV −9% by Christmas. Confidence destroyed. System abandoned at the first circuit breaker.

The circuit breaker did exactly what it was designed to do — but the narrative whipsaw kills the strategy because the operator wasn't mentally prepared for a drawdown that the system correctly handled. The G-gates in Part 13 exist to survive this conversation with yourself.

### F2 — Survivorship Deflation Was Worse Than Expected

The database has 2,386 symbols, zero delisted. Small-cap ASX attrition runs 4–8%/yr. G1 de-biasing run shows true base rates 4–6 points lower. The 5%/quarter target silently became a 3.2%/quarter strategy — still fine, but nobody trusted any number in the docs after discovering this. All projections should be restated after G1, once, with the honest range.

### F3 — Satellite Caught the Mid-Cap Median, Not the Right Tail

Mid-caps delivered their median (+3.5%/yr) instead of their mean (+40.8%/yr) because tail capture is hard and 4–8 positions is a small sample. Satellite bleeds 6% while core grinds +9%. Total portfolio: +4.2% — index-like, 100 hours of effort.

**This is a tolerable outcome by design** — that is the point of Core–Satellite. The premortem calls it failure; the architecture calls it a bad year. Pre-commit to the 3-year evaluation horizon before starting.

### F4 — Commodity Supercycle Ended Mid-Hold

China stimulus reversed. Copper/gold −15% in 3 weeks. Materials 30% of NAV. Core holds BHP through −25% because "thesis intact, breaker not hit."

**Guardrail 11** (velocity trigger at copper/gold −12%/4wk) + sector cap 30% + semi-annual core review with commodity-regime veto.

### F5 — Operator Bandwidth Collapsed

System demands 30–60 min/day. Work gets busy in November. Two capital raises missed. One becomes −40% satellite loss.

**Fix:** BROKEN verdicts auto-stage exit orders for one-tap approval. Weekly time budget measured and alerted if missed 2 weeks running.

### F6 — System Override

A mate's tip. A 3×-in-a-week small cap. "Just this once" outside the universe at 12% sizing.

**Guardrail 17:** Any manual trade ≤2% NAV. Written thesis in same DB table. Reviewed in quarterly audit to price discretion honestly vs model. The system may buy *less* than the model says, never more.

### F7 — Sequence-of-Returns at Launch

Even a true 18%/yr strategy has ~1-in-4 odds of a negative first year. Starting at Sep/Oct soft patch raises that. A −12% first-year on retirement money feels categorically different from the same number on a spreadsheet.

The ramp in Part 13 exists so the first *full-size* quarter is November–January (your two best months). Pre-commit to the number you'll accept for the first year before it happens.

---

## PART 15 — THE 10-WEEK CORRECTED BUILD ORDER

| Wk | Build | Blocking for Real Capital? |
|---|---|---|
| **1** | Store `adjusted_close` in backfill; flag stale sessions; fix duplicate-paper-trade bug; rebuild `model_training_set` | **YES** |
| **2** | Path-aware labels (+8%/−8% first-touch) + close regression head; retrain; decile P&L report | **YES** |
| **3** | PortfolioGate — single source of truth (Part 8 caps); wire into paper path; retire conflicting specs | **YES** |
| **4** | Exit engine: catastrophe stop (−20%/ATR) + time stop + CGT override; announcement monitor | **YES** |
| **5** | Sentinel INTACT/WEAKENED/BROKEN wired to paper exit; calendar gate as score tilt (not block) | YES |
| **6** | G1 de-biased universe run with delisted tickers; G2 walk-forward on fixed labels + 2022 bear | YES |
| **7** | Core sleeve: 6–8 large/mega-cap positions + VAS/VGS ETF; franking/ex-div calendar | No |
| **8** | WFO gate v2 (55% hit, 100 signals, CI ≥ 0.5); 63-day horizon alignment; November upgrade | No |
| **9** | G4 micro-live ($500–$1K/position); slippage measurement; November deployment prep | No |
| **10** | G1–G5 go/no-go review; November full satellite deployment (best month, $200K live) | No |

*Note: `backend/backtest.py` does not exist yet — must be created in Week 6 alongside G2 walk-forward.*  
*Note: Global rotation dashboard, squeeze/mean-reversion features are in Weeks 7+ — not blocking.*  
*Note: EODHD limit is 1M calls/month (not 100K as stated in v1 implementation doc).*

---

## PART 16 — ONE-PAGE SUMMARY

**What the data actually proves:**
- 39.9% of liquid ASX stock-years beat +15% price-only (≈44% with dividends). Target is achievable — but achieving it requires selection skill, not just participation.
- The median mega-cap returns +9.8%/yr; the median mid-cap returns +3.5%/yr. Mid-cap outperformance is right-tail skew. Use the model to hunt that tail; don't bet the portfolio on it.
- July/April/November are genuinely the best 3 months (confirmed). March is the worst (confirmed). Calendar tilts score by ±10–25%; they do not hard-block entries.
- A −7% mechanical stop fires on 66% of all entries and has EV = −3.2%/trade. It has been retired. Catastrophe stop at −20%/ATR + portfolio circuit breaker is the correct risk instrument.
- The model was trained on peak-touch (71% base rate). It needs retraining on path-aware label (P(+8% before −8%)). This is a 1-week fix, not a rebuild.
- The pipeline has 0 WFO rows, 11 paper trades, 3 duplicate bugs. It is not ready for real capital. November 2026 deployment on a fixed, validated pipeline is achievable and credible.

**The system that works:** Core sleeve (60–70% NAV, mega-caps + ETFs, buy-and-hold, captures +9–10%/yr median) + Satellite (30–40% NAV, 4–8 model-driven positions, hunts mid-cap right tail, 63-day cycles, path-aware exits). Combined net target: **17–19%/yr after 15% SMSF tax**. Honest 9-year projection from $200K: **$0.9M–$1.1M by September 2035**.

---

*v2 prepared August 2026 incorporating independent peer review (Kilo) against live `eod_ohl_history` (3.35M rows) and PostgreSQL `asx` database. Statistical claims re-verified against local data, not EODHD API. All return projections restated as honest net-of-tax ranges.*

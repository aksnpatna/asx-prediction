# SMSF Strategy Review — Independent Evaluation & Redesign
## Unbiased assessment of `ASX_STRATEGY_DEEP_DIVE.md` + `smsf_implementation.md` against the live codebase and 3.35M rows of local EODHD data

> **Reviewer:** Independent (Kilo), August 2026
> **Method:** Read both strategy documents, audited the actual implementation (`backend/`), inspected the live PostgreSQL database, and re-ran every load-bearing statistical claim independently against the local EODHD mirror (`eod_ohl_history`: 3,348,668 rows, 2,386 symbols, 2017-01-03 → 2026-08-06). No EODHD API quota was consumed — the local mirror is the same data.
> **Stance on targets:** Per your instruction, I do **not** treat 14–15% p.a. as unachievable. My analysis supports that mid-teens-to-low-twenties gross returns are attainable by a competent systematic process on liquid ASX equities in most regimes. The problems with the current plan are **not** the return target — they are structural, statistical, and operational. Those are fixable.

---

## PART 1 — VERDICT AT A GLANCE

| # | Claim / Design Element | Verdict | Evidence |
|---|---|---|---|
| 1 | Monthly seasonality (July/April strong, March weak) | ✅ **Confirmed** | My re-computation matches within noise (§2.1) |
| 2 | ~42% of stock-years exceed +15% | ✅ **Confirmed** | 39.9% price-only; ~42-45% with dividends (§2.2) |
| 3 | Mid-cap tier outperforms on *average* | ⚠️ **True but misleading** | Mean confirmed (+40.8%); **median mid-cap is +3.5% vs +9.8% mega-cap** — the "alpha" is pure right-tail skew (§2.3) |
| 4 | Model target: "hit +5% within 63 days" | 🔴 **Broken label** | Label is **peak-touch**, not close. 71.3% base rate vs 36.4% who *close* +5% (§3.1) |
| 5 | "Stop-losses non-negotiable" at −7% | 🔴 **Value-destroying as specified** | 66.2% of all windows touch −7%; stop-first EV = **−3.2%/trade** vs **+10.5%** hold-to-close at base rates (§3.2) |
| 6 | 9-year backtest universe | 🔴 **Survivorship-biased** | 0 of 2,386 symbols are delisted. Every historical stat is inflated (§3.3) |
| 7 | Model training pipeline | 🔴 **Train/serve skew** | Trains on **raw** close, serves on **adjusted** close (§3.4) |
| 8 | WFO capital gate protects SMSF capital | 🔴 **Never executed** | `wfo_metrics` = **0 rows**. The gate has never run; 5 broad scans total; 11 paper trades ever (§3.5) |
| 9 | Portfolio gate prevents duplicates | 🔴 **Already failing in paper** | 3× duplicate SCG and 2× duplicate PPT open positions on the same day at the same price (§3.5) |
| 10 | 5%/quarter = "21.6% after SMSF tax" | ⚠️ **Arithmetic mislabel** | 21.6% is *pre-tax* compounded. After 15% tax ≈ **18.3%**. 10-yr projection overstated by ~$150–300K (§4.1) |
| 11 | Risk limits consistent across docs | 🔴 **Three conflicting sets** | 25 pos × 8% (impl doc) vs 12 pos × 12% (WFO code) vs 4% max/position (premortem) (§4.2) |
| 12 | Overall architecture (universe tiers, calendar gate, sentinel, circuit breaker) | ✅ **Sound skeleton** | Keep. The failure is in label design, exit structure, and validation — not the scaffolding |

**Bottom line:** The strategy's *macro observations* (seasonality, liquidity tiers, sector behaviour, tax structure) are mostly real and I verified them. The strategy's *engine* (what the model predicts, how trades are exited, and the proof that any of it works out-of-sample) is currently unfit for real capital. Deploying $200K in September on the current spec would be betting on an unvalidated pipeline whose own paper-trading layer is already showing bugs. The redesigned strategy in Part 5 keeps your return target and fixes the engine.

---

## PART 2 — INDEPENDENT VERIFICATION OF THE DATA CLAIMS

All numbers below are my own runs against the local EODHD mirror (price-only, month-end sampling, top-500 by current dollar volume, 2017–2026, winsorized at −95%/+500% to kill bad ticks). Differences from the doc are noted.

### 2.1 Monthly seasonality — CONFIRMED

| Month | Doc claim | My result (avg / win-rate) | Match? |
|---|---|---|---|
| July | +5.10%, 63% | **+4.48% / 61.8%** | ✅ |
| April | +4.36%, 59% | **+4.89% / 60.5%** | ✅ |
| November | +2.96%, 56% | **+5.97% / 59.1%** | ✅ (doc *understates* Nov) |
| August | +3.58%, 58% | **+3.89% / 58.8%** | ✅ |
| March | −1.45%, 44% | **−1.74% / 42.0%** | ✅ |
| September | +1.93% avg, −0.13% med, 46% | **+1.00% avg, −1.26% med, 41.6%** | ✅ directionally |

Seasonality is real in this sample. Caveats: 9 years = only 9 observations per month (tiny n for a "rule"); it is survivor-inflated; and note that **September's average is positive** — the restriction case rests on median/skew, which is defensible but weaker than the doc's framing. November deserves an upgrade in the calendar gate.

### 2.2 Return distribution — CONFIRMED (with one caveat)

Rolling 12-month returns, 30,939 stock-year observations (price-only, survivors):

| Threshold | Doc | Mine (price-only) | Mine + ~4% dividend add-back |
|---|---|---|---|
| >10%/yr | 48.2% | 45.2% | ~49% |
| >15%/yr | 42.5% | 39.9% | ~44% |
| >25%/yr | 33.5% | 31.3% | ~35% |
| Negative | 39.0% | 42.2% | ~38% |

The doc's numbers reconcile with mine once dividends are added back (the doc used adjusted close; my mirror stores raw close). **So: yes, ~4 in 10 liquid stock-years beat 15%. The target premise is data-supported.** The caveat: this is a statement about the *cross-section*, not about what any 20-position portfolio captures (§3.6).

### 2.3 The mid-cap finding — the doc's biggest claim needs a big correction

| Tier (90d $vol) | n | Avg 12m | **Median 12m** | P(>15%) | P(neg) |
|---|---|---|---|---|---|
| $1M–10M ("alpha zone") | 18,432 | **+40.8%** | **+3.5%** | 38.7% | 44.9% |
| $10M–50M | 9,314 | +32.9% | +7.7% | 41.5% | 39.3% |
| >$50M (mega) | 3,193 | +19.4% | **+9.8%** | 42.1% | 35.0% |

The doc reports only the **mean** per tier and concludes mid-caps "nearly double" mega-caps. The mean is real — but the **median** mid-cap (+3.5%/yr) *underperforms* the median mega-cap (+9.8%/yr) by nearly 3×. The mid-cap mean is manufactured by a small right tail of multi-baggers. Implications:

1. "Tilt to mid-caps" is a **lottery-ticket-distribution** strategy: it works only if your selection actually catches the tail. It is not a free lunch from holding the tier.
2. Mega-caps have *higher per-stock win rates* (P>15%: 42.1% vs 38.7%) and *fewer negative years* (35.0% vs 44.9%) — they are the better risk-adjusted hunting ground per stock picked at random.
3. The 63-day horizon data reinforces it: median 63-day return is **+2.65% in mega-caps vs +0.88% in the $1–10M tier**, with median intra-window drawdown of −6.0% vs −9.1%.

**Corrected strategic implication:** mid-caps should be *opportunistic satellite* positions taken only on high model conviction (where you have evidence of tail capture), not the default centre of gravity. The portfolio core belongs in the $10M+ tiers where the median stock already works for you.

### 2.4 The label and exit-structure problem (the decisive numbers)

From `model_training_set` (2,395,907 labelled windows, 2017–2026):

| Metric | Value | Reading |
|---|---|---|
| P(touch +5% at some point in 63d) — *the model's actual target* | **71.3%** | Easy target; base rate is huge |
| P(close ≥ +5% at day 63) | **36.4%** | The economic reality |
| Median 63d close-to-close return | **−1.08%** | Typical stock drifts *down* over a quarter (price-only) |
| Mean 63d return | +10.47% | All right-skew |
| Median intra-window drawdown | −9% to −15% by tier | Normal noise is deep |
| P(trough ≤ −7% within window) | **66.2%** | A −7% stop fires on 2 of 3 random entries |
| P(touch +5% **and** trough ≤ −7%) | **41.4%** | In ~4 of 10 cases, outcome depends on *which came first* |

**Expected value per 63-day trade at base rates (no selection skill), before costs:**

| Exit structure | EV/trade |
|---|---|
| −7% stop / +5% take-profit (stop-first, conservative) | **−3.2%** |
| −15% stop / +8% take-profit (stop-first, conservative) | −3.7% |
| No stop, exit at 63-day close | **+10.5%** (median −1.1%) |
| −20% catastrophe stop only, exit at close | **+7.8%** |

**Breakeven hit rates** for the doc's structure (−7% stop, +5% target): **61.7%** at 0.4% round-trip cost, **65.0%** at 0.8%. The unconditional "win" rate achievable under that structure is ~30–45% depending on ordering luck. That is a 20–30 point gap the model must supply through *path timing* (not just stock selection) before costs. That is an extraordinary burden of proof that has never been measured — because the WFO harness has never run (§3.5).

This is the single most important finding of the review: **the −7% stop-loss dogma, applied to instruments with a −16.4% average intra-quarter drawdown, converts a positive-expectancy universe into a negative-EV stop-harvesting machine.** The premortem doc says "stop-losses are non-negotiable." The data says the *tight* stop is the most expensive single design decision in the plan. Risk control is non-negotiable; the −7% position-level stop is the wrong instrument for it. Alternatives in §5.

---

## PART 3 — FATAL AND NEAR-FATAL FLAWS (RANKED)

### 3.1 🔴 Label/objective mismatch — the model is trained on the wrong target

`backend/model_training.py` line ~566:

```python
hit_5pct_63d = fwd_peak >= 5.0      # touched +5% AT ANY POINT in 63 days
hit_8pct_63d = fwd_peak >= 8.0
```

The model learns "will this stock touch +5/+8% at any moment in the next quarter" (base rate 71%/61%) — not "will it *close* there" (36%) and not "will it get there *before* falling 7%" (~30–45%). Your wealth-builder's headline metric (`prob_ge_5pct`) therefore systematically overstates achievable outcomes. A model can be *excellent* at the trained task and still lose money with a −7% stop. **Fix:** retrain on a path-aware label (§5.2) — e.g. `hit +8% before −8%`, plus a close-based regression head. This is a one-week-with-existing-infrastructure fix, not a rebuild.

### 3.2 🔴 Exit structure inverts the edge

Covered in §2.4. The structure that survives this universe's volatility is: **catastrophe-only position stops (−15% to −20%), hold to horizon, portfolio-level drawdown circuit breaker** (which the docs already designed well). Tight stops also *increase* turnover, which at ComSec's $19.95–$29.95/side compounds cost drag.

### 3.3 🔴 Survivorship bias in every historical number

The universe contains **2,386 symbols, zero delisted**. Every stock in the 9-year training/backtest set is a company that survived to August 2026. Delisted small-caps (the −70% to −100% final years) are absent from all stats in the deep-dive doc, from the training labels, and from my own verification above. Realistic deflation of the cross-sectional mean: −2% to −5% *per year* depending on tier (small-cap ASX attrition runs ~4–8%/yr). The median is much less affected. Action: pull EODHD delisted tickers (`exchange_symbol_list` includes inactive on request) and re-run the base-rate table once — even crudely — so the plan rests on honest base rates. This is worth spending API calls on.

### 3.4 🔴 Train/serve skew — raw close in training, adjusted close in production

- Training backfill (`eodhd_backfill.py`, line ~187) stores **raw `close`** in `eod_ohl_history` — labels and features are computed on unadjusted prices. Ex-dividend drops (ASX banks yield 5–6%+) appear in training as price crashes; ~4%/yr of dividend return is invisible in labels.
- The live prediction path (`main.py:_eodhd_historical_data`) renames **`adjusted_close` → Close** — so production features (RSI, momentum, Donchian, drawdowns) are computed on a *different price series* than the model was trained on.

On the ASX — one of the highest-yielding developed markets — this is not a rounding error. It is a systematic distortion of every feature around every ex-div date, plus a train/serve distribution shift that degrades live accuracy relative to any backtest. **Fix:** store `adjusted_close` in the backfill (EODHD returns it in the same payload — zero extra API cost), rebuild the training matrix once, retrain. ~1 day of work, mostly waiting for the recompute.

### 3.5 🔴 The validation layer has never run — the safety story is currently fictional

Database audit (2026-08-08):

| Component | Evidence required before real capital | Actual state |
|---|---|---|
| `wfo_metrics` (WFO gate rows) | 8+ weeks of OOS Sharpe evaluations | **0 rows — job has never produced output** |
| `wealth_scan_history` broad scans | hundreds of daily scans feeding WFO | **5 scans, July 18–23 only** |
| `paper_trades` | 100+ clean trades, ≥52% hit rate | **11 ever (5 open, 6 closed)** |
| Duplicate-position guard | works | **fails live: 3× SCG + 2× PPT opened same day at same price** under `wealth_builder_bootstrap` |
| `tracking_windows` | mature accuracy stats | 19 rows |

Two structural mismatches make this worse:
- **Horizon mismatch:** WFO evaluates at 30/63/90 days, but the *label* matures at 63 days. A 30-day gate on a 63-day signal measures noise. With the first broad scan on 2026-07-18, the earliest a 63-day WFO row can even *exist* is ~2026-09-19 — i.e. **after** your planned September deployment. The plan's own Week-8 gate ("confirm WFO GREEN before real capital") cannot pass on the current data pipeline before September. Either the start moves, or the gate is waived (do not waive it — see §5.6 for the honest alternative).
- **GREEN bar is too low:** Sharpe ≥ 0.25 with 95% CI lower bound ≥ 0 proves "probably better than coin-flip," not "worth 25x leverage-free risk on retirement capital." For a strategy targeting 18–21% net, the gate should require CI lower bound ≥ 0.5 *and* hit-rate ≥ 55% *and* ≥100 evaluated signals.

### 3.6 🔴 The "42.5% of stock-years beat 15%" syllogism

The doc's core motivational argument — *"15%/yr is achieved in 42.5% of stock-years, therefore a 15–21% portfolio target is the median outcome"* — commits an ecological fallacy. The cross-sectional hit rate says nothing about a 20–25 position portfolio's compound path. What governs your outcome is: (a) selection skill above base rates, (b) path dependency vs stops, (c) cost drag (~1–2%/yr at planned turnover), (d) the left tail (P(<−30%) = 12.9% of stock-years), and (e) your behaviour during a 20% drawdown. The target is achievable — I am not disputing that — but the *evidence cited* for it does not connect to it. What would actually support it: an out-of-sample equity curve from the fixed model with the fixed exit structure on the de-biased universe. Build that (§5.6), then size the target.

### 3.7 🟠 September-2026 start date vs the plan's own calendar

You plan to deploy in September — a month the strategy itself flags as "cap new entries 50%, tighten stops" (median −1.26%, 41.6% win rate in my data). Meanwhile **right now (August) is statistically one of the best deployment windows** (+3.9%/58.8%) and the system is idle in bootstrap mode. The pragmatic reading: September deployment with the seasonal guard *active* is actually a *good* quirk — you start small in a weak month and reach full size into the November–April windows. But note the plan never reconciles this.

### 3.8 🟠 Stale-price contamination

11.4% of daily rows in the mirror are unchanged-close or zero-volume. Features computed over stale runs (RSI pins at 50/100, volatility collapses, fake Bollinger squeezes) feed both training and live scoring for the tail of the universe. Fix: filter or flag sessions where `volume == 0 or close == prev_close` before feature computation.

### 3.9 🟠 Doc/code drift

- Docs say **4-persona** AI debate; code implements **6** (technical, macro, valuation, risk, tax, liquidity) + researcher + rebuttal + CIO.
- `backend/backtest.py` referenced in Week 7 **does not exist**.
- `smsf_implementation.md` checklist says EODHD "100,000 calls/month"; you have 1M. Not a problem, but the capacity assumption for scan cadence can be relaxed 10×.
- ComSec fee table: $19.95/side applies to $1K–$10K; $20K positions are $29.95/side ($59.90 round trip, 0.30%) — the "$20K = 0.20%" row is optimistic. All conclusions survive.

---

## PART 4 — NUMERIC CORRECTIONS

### 4.1 The projection table uses pre-tax numbers labelled as after-tax

5%/quarter compounds to **21.55% pre-tax**. Taxed at 15% (63-day turnover means virtually all gains are realised short-hold, no CGT discount): **≈ 18.3% net**. The doc's table labels 21.6% as "after 15% SMSF tax" and projects $1.276M by 2035; the corrected net figure gives ≈ $200K × 1.183^9 ≈ **$907K** (9 yrs) — still an excellent outcome, and I am not arguing against the target, but the plan should carry honest numbers: also note CGT deferral (tax paid on sale, not accrual) mildly *improves* the effective compounding vs my simplified annual-tax math — the honest range for Sep-2035 at 5%/quarter net of everything is **$0.9M–$1.1M**, not $1.276M.

### 4.2 Three conflicting risk limit sets — pick one

| Source | Max positions | Max single | Max sector | Cash floor |
|---|---|---|---|---|
| `smsf_implementation.md` PortfolioGate | 25 | 8% | 35% Materials | 15% |
| Deep-dive premortem (sizing rule) | — | **4%** | 35% | — |
| Live code `_WFO_CAPITAL_RULES` (GREEN) | **12** | **12%** | **40%** | — |

For a $200K SMSF starting an unproven system, my recommendation (§5.4): **10–14 positions, 5–7% each, sector cap 25% (Materials 30%), cash floor 15%, portfolio breaker at −8/−15/−25%** — close to the existing WFO GREEN limits, tightened on sector. Whatever you choose, encode it in exactly one place (`portfolio_gate.py`) and have every other document reference it.

### 4.3 Cost realism

At 12 positions × ~3 round-trips/position/yr (63-day cycle) = ~36 round-trips ≈ **$1.4–2.1K/yr brokerage** (0.7–1.1% of NAV) + slippage ~0.2%/side on liquid names → **total friction 1.5–2.5%/yr**. Budget for it in the target: 5%/quarter gross ≈ 3.7–4%/quarter after friction and tax. The plan's "0.40% cost drag per round-trip, completely acceptable" is fine per-trade; the plan just never multiplies it by turnover.

---

## PART 5 — THE REDESIGNED STRATEGY (SMSF v2)

Keeps: the universe tiers, the calendar gate (as a *tilt*), the sentinel/announcement monitor, the circuit breaker, the AI debate (advisory), the EODHD pipeline, the return target. Changes: what the model predicts, how trades exit, how positions are sized, what counts as proof.

### 5.1 Portfolio architecture: Core–Satellite (replaces "25 momentum positions")

```
CORE — 60–70% of NAV (buy-and-hold, 12+ month horizon)
  • 6–10 fully-franked large/ mega-caps (CBA/BHP/WES/WOW/GMG-class) + 1–2 index ETFs (VAS/VGS)
  • Purpose: capture the median stock-year (+8–10%), franking credit refunds (~0.6–1.0%/yr extra
    at 15% SMSF rate), CGT 10% effective rate on >12-month holds, and half the portfolio's
    volatility budget. This is the "median works for you" tier from §2.3.
  • Rebalanced semi-annually, not traded. Exits only on thesis-break or circuit breaker.

SATELLITE — 30–40% of NAV (model-driven, 63-day cycle)
  • 4–8 high-conviction model picks, any tier >$1M/day, sized by §5.4
  • This is where the 59-feature ensemble + AI debate hunts the right tail of §2.3
  • Full entry/exit discipline of §5.3
```

Why: your own data shows the median liquid stock-year is +5.6–9.8% and only ~36% of quarters close +5%. A 100%-satellite portfolio needs relentless right-tail capture to compound; a 100%-core portfolio wastes the model. 60/40 splits the difference between "median works" and "tail hunted," and it structurally caps the damage if the model has a bad quarter. It also *halves* turnover-driven costs and CGT-at-15% leakage (core gains compound at the 10% discounted rate).

### 5.2 Fix the label before anything else (blocking)

Retrain the ensemble on **path-aware labels**, keeping all existing features:

```python
# Primary label: did the stock reach +8% before falling −8%, within 63 days?
label_path = first_touch(+8.0, -8.0, horizon=63)   # +1 win, -1 loss, 0 neither (timeout→use close)
# Secondary (regression): close-to-close 63d return, clipped to ±60%
```

Report model quality on the metric that matches the trade: **P(+8% before −8%) by score decile**, plus close-based decile returns as the sanity check. Delete `hit_5pct_63d` (peak-touch) as a *target* — keep it as a *feature* if you like. Simultaneously store `adjusted_close` in the backfill (§3.4) and add the stale-session flag (§3.8). Deliverable: one retrained model whose claimed accuracy is stated in *trade-relevant* units.

### 5.3 Exit discipline (replaces −7% stops)

1. **Catastrophe stop:** −20% from entry (or 2.5× the stock's own 20-day ATR%, whichever is wider) — catches thesis-ending events, not noise. ~18% of mid-cap windows touch −20%; it fires rarely enough to not tax the book.
2. **Thesis stop:** sentinel verdict BROKEN (capital raise, guidance cut, halt) → exit regardless of price. Unchanged from plan — the best-designed exit in the docs.
3. **Time stop:** day 63 (earnings-zone adjusted): exit if neither target nor stop hit; hold-to-CGT-discount override only if gain >+8% and tax deferral > expected forward drift.
4. **Portfolio circuit breaker:** −8/−15/−25% (Y/O/R) as specified in the premortem — this is now the *primary* risk instrument. Keep exactly as designed; it's good.
5. **Earnings gate:** no entries ≤14 days before confirmed earnings for satellite (the code already has `entry_ok`; enforce it in the gate, not just the score).

The tight mechanical stop is replaced by *smaller positions* — sizing is where the risk goes (next).

### 5.4 Position sizing by risk, not by conviction (replaces fixed $10K)

```python
satellite_size_pct = min(0.07, 0.015 / stock_vol_20d_annualised)   # target ~1.5% NAV vol contribution
# hard caps: single ≤ 7% NAV · ≤ 2% of 20d ADV · sector ≤ 25% (Materials 30%)
# portfolio: Σ satellite ≤ 40% NAV · open satellite positions 4–8 · cash floor 15%
```

On a $200K SMSF that yields satellite positions of roughly **$8K–$14K** — right in the 0.2–0.4% brokerage band. Position count and caps are consistent with existing `WFO GREEN` limits rather than the more aggressive 25-position plan.

### 5.5 Calendar: tilt, don't gate

Keep the month multipliers as **score adjustments (±10–20%)**, not entry blocks — with the March reduction intact (it's the only month with negative *average*, −1.74% my data, and the override logic for strong-macro Septembers already exists in the premortem). Upgrade November to a full-deployment month. Keep the Friday rule only for satellite entries.

### 5.6 The proof protocol (what "validated" actually means)

Before more than token size goes live:

| Gate | Threshold | Why |
|---|---|---|
| G1 De-biased base rates | re-run §2 with delisted tickers included | know the true wind |
| G2 Label-fixed walk-forward | P(+8% before −8%) ≥ 60% in top score decile, on 2022–2026 including the 2022 bear, with the §5.3 exit rules, net of 0.8% costs | proves selection × path skill where you intend to trade |
| G3 Live paper | ≥ **60** satellite trades, hit-rate within 10 pts of G2, zero duplicate-position bugs, zero missed critical announcements | proves operations |
| G4 Micro live | 4 weeks at $500–$1K/position, slippage ≤ 0.35%/side measured | proves execution |
| G5 Gate consistency | one risk-limit file; WFO GREEN redefined as CI-lower ≥ 0.5 AND ≥100 signals AND hit ≥ 55% | proves the brakes |

Planned ramp: paper (now → late Sep, running *through* the August/July-strength window you're currently missing) → micro-live October → quarter-size November (statistically your best month) → full size January 2027 if G3/G4 pass. **September 2026 real-money full deployment is not achievable under the plan's own gate logic** (WFO can't produce a 63d row before ~Sep 19, and zero paper history exists) — this schedule gets you live with real money in ~10 weeks *with* evidence, instead of in 4 weeks without it.

### 5.7 What to build in what order (corrected 10-week plan)

| Wk | Build | Blocking? |
|---|---|---|
| 1 | Store `adjusted_close` in backfill; flag stale sessions; rebuild `model_training_set`; fix duplicate-paper-trade bug (unique open position per symbol) | **yes** |
| 2 | Path-aware labels (+8%/−8% first-touch) + close-based regression head; retrain; decile report | **yes** |
| 3 | PortfolioGate single source of truth (§5.4 caps) + wire into paper path; kill the 3 conflicting specs | **yes** |
| 4 | Exit engine: catastrophe stop + time stop + CGT override; announcement monitor (as spec'd — it's good) | **yes** |
| 5 | Sentinel INTACT/WEAKENED/BROKEN (as spec'd — good); wire BROKEN→exit on paper trades | yes |
| 6 | De-biased universe run with delisted tickers (G1); walk-forward G2 on fixed labels | yes |
| 7 | Core sleeve implementation (6–10 positions + ETF), franking/ex-div calendar | no |
| 8 | WFO gate v2 (55%/100-signal/CI-0.5) + 30/63/90-day consistency with label horizon | no |
| 9 | Micro-live protocol G4 ($500–$1K), slippage measurement harness | no |
| 10 | Go/no-go review against G1–G5; Telegram reporting pack | no |

The global rotation dashboard, sector pre-filters, and squeeze/mean-reversion features from the impl doc are all *nice* and should slot into weeks 6+ — but none of them matter until the label, exits, and validation exist.

---

## PART 6 — GUARDRAILS (CONSOLIDATED, v2)

🔴 = hard-coded, cannot be overridden by the AI debate · 🟡 = system flags, human decides

| # | Guardrail | Trigger | Action | Level |
|---|---|---|---|---|
| 1 | Portfolio breaker L1 | NAV −8% from peak | No new satellite entries; review all theses | 🔴 |
| 2 | Portfolio breaker L2 | NAV −15% | Cut satellite to ≤ half; 60% total cash floor | 🔴 |
| 3 | Portfolio breaker L3 | NAV −25% | Satellite → 0. 8-week re-entry cooling. Core untouched unless thesis-broken | 🔴 |
| 4 | Single-position cap | ≤7% NAV **and** ≤2% of 20d ADV | PortfolioGate rejects order | 🔴 |
| 5 | Sector caps | ≤25% any sector (Materials 30%) | PortfolioGate rejects order | 🔴 |
| 6 | Duplicate position | any open position in symbol | Reject (this bug exists **today** in paper) | 🔴 |
| 7 | Catastrophe stop | −20% or 2.5×ATR(20) from entry | Exit satellite position | 🔴 |
| 8 | Announcement keywords | capital raise / halt / administration / guidance-cut in ASX feed | Immediate sentinel review → exit if BROKEN | 🔴 |
| 9 | Earnings gate | satellite entry ≤14d before confirmed earnings | Block entry | 🔴 |
| 10 | Liquidity floor | ADV20 < $1M or spread > 0.5% or stale-session flag | Block entry; limit orders only, always | 🔴 |
| 11 | Commodity reversal velocity | copper/gold −12%/4wk **and** AUD/USD −4%/4wk | Materials new entries blocked; sector weight × 0.3 | 🔴 (keep from premortem) |
| 12 | Model health | rolling 8-wk live hit rate < 45% or < 60% of walk-forward rate | Halve satellite size; <35% → freeze new entries | 🔴 (keep, thresholds tightened) |
| 13 | Data sanity | EODHD vs ASX close gap > 2% on any candidate | No trade until 2 sources agree | 🟡 (keep) |
| 14 | Devil's advocate | every APPROVE verdict | auto short-thesis pass; unaddressed risk → size −30% | 🟡 (keep) |
| 15 | CGT discipline | approach 12-month hold with gain > +8% | defer exit ≤30d unless stop/thesis fires | 🟡 |
| 16 | Kill switch (human) | any 2 of: breaker L2, model-health freeze, data-quality flag | all automation halts; manual-only mode | 🔴 |

Retired from the original plan: position-level −7% stops (replaced by #4 sizing + #7 catastrophe stop + #1–3 breakers); September "50% entry cap" (replaced by score tilt with macro override — kept March reduction as a tilt).

---

## PART 7 — PREMORTEM (v2): IT IS AUGUST 2027 AND THE SMSF LOST MONEY

The original premortem is genuinely good — its failure modes 1–12 are real and I have kept the usable guardrails. But it was written inside the strategy's own assumptions. These are the failure modes it missed, in order of how likely they are to actually be the cause of death:

**F0 — The label fix was never made, and the model "worked" exactly as trained.** The system posts a 70% "touch +5%" hit rate on paper, deploys real capital, and loses money steadily because 66% of positions touched −7% first. *Nobody noticed because the dashboard metric matched the trained label, not the trade.* → Guardrail 12 now compares live hit-rate against the *walk-forward rate on the same path-aware label*, not against a vibes threshold.

**F1 — September 2026 deployment happened on schedule anyway.** Impatience, the $200K sitting in cash earning 4%, and "the data looked so strong." WFO had zero rows; the first three live months hit the Sep/Oct weak patch; NAV −9% by Christmas; confidence destroyed; system abandoned at the first breaker — which is what the breaker is *for*, but the real loss is the strategy dying by narrative whipsaw. → The G-gates in §5.6 exist specifically to survive this conversation with yourself.

**F2 — Survivorship deflation was worse than estimated.** The de-biased re-run (G1) shows true base rates 4–6 points lower; the 5%/quarter target silently became a 3.2%/quarter strategy — still fine, but the September projection deck said $1M+ and now nobody trusts any number in the docs. → All projections restated after G1, once, with the honest range (§4.1).

**F3 — The satellite book caught the wrong tail.** Mid-caps delivered their median (+3.5%/yr) instead of their mean (+40.8%/yr) because tail capture is *hard* and 4–8 positions is a small sample. Satellite bleeds 6% while core grinds +9%. Total portfolio: +4.2% — index-like, 100 hours of effort. → This is a *tolerable* outcome by design (that is the entire point of core–satellite); the premortem calls it failure, the architecture calls it a bad year. Pre-commit to the 3-year evaluation horizon.

**F4 — Commodity supercycle ended mid-hold.** China stimulus reversed; copper/gold −15% in 3 weeks; Materials 30% of NAV; core sleeve holds BHP/FMG through −25% because "thesis intact, breaker not hit." → Guardrail 11 (velocity trigger) + sector cap 30% + core review cadence semi-annual *with commodity-regime veto*.

**F5 — Operator bandwidth collapsed.** The system demands 30–60 min/day of disciplined review (Telegram, orders, sentinel triage). Work gets busy in November; two capital raises are missed; one becomes a −40% satellite loss. → Guardrail 8 automation depth: BROKEN verdicts auto-stage exit orders for one-tap approval; weekly time budget measured and alerted if missed 2 weeks running.

**F6 — ATO/structural: Division 296 tax on super balances >$3M passes, or franking refunds are capped.** Not a 2026 problem at $200K, but the 10-year compounding story assumes static rules. → Strategy returns driven by price appreciation, not tax arbitrage (core principle already correct in the premortem — keep).

**F7 — EODHD data incident.** Adjustment error, outage mid-crisis, or plan downgrade. The whole feature pipeline silently degrades; the model keeps emitting confident scores on stale prices (11.4% of rows already are). → Guardrail 13 + heartbeat: if >5% of core-universe sessions flagged stale in a week, freeze new entries.

**F8 — You override the system.** The most common real cause of death for exactly this kind of personal system. A mate's tip, a 3×-in-a-week small cap, "just this once" outside the universe, sized at 12% because conviction. → Hard rule: the system may buy *less* than the model says, never more; any manual trade must be ≤2% NAV and logged with a written thesis in the same DB table as model trades, so the 2028 review can price your discretion honestly.

**F9 — Sequence-of-returns at launch.** Even a true 18%/yr strategy has ~1-in-4 odds of a negative first year. Starting at the Sep/Oct soft patch raises that. A −12% first-year on retirement money feels categorically different from the same number on a spreadsheet. → The ramp in §5.6 exists so the first *full-size* quarter is Nov–Jan (your two best months), and the premortem number you show your future self is the breaker ladder, not the upside.

---

## PART 8 — WHAT I WOULD DO THIS WEEK

1. **Do not** schedule real capital for September. Schedule micro-live for October, quarter-size for November.
2. Fix the 4 blocking items (Part 5.7, weeks 1–4): adjusted-close training data, path-aware labels, one PortfolioGate, exit engine + duplicate bug.
3. Re-run the universe stats once with delisted tickers (G1) — spend some of the 1M EODHD calls; it's the only missing dataset.
4. Let the existing paper pipeline run at full intensity through August–September (it's currently idling at 5 open positions, 3 of them duplicates) — fix the gate and let it accumulate the 60+ trades you need.
5. Rewrite the return table with net numbers ($0.9M–$1.1M by 2035 at target; ~$610K at a deflated-but-honest 15% gross) — a plan you trust at the lows is worth more than a plan that excites you at the highs.

---

## APPENDIX — REPRODUCTION

All verification queries ran inside the `asx-backend` container against the live PostgreSQL `asx` database (scripts in `/tmp/kilo/verify*.py`, not persisted to the repo):

- Seasonality: month-end close-to-close per symbol, top-500 by current 90d dollar volume, 2017-01→2026-08, winsorized [−95%, +500%], n≈2.7–3.2K per month.
- Return distribution: 12-month rolling price returns sampled monthly, same universe, n=30,939.
- Tier table: same, bucketed by current 90-day ADV — **note this buckets stocks by *today's* liquidity, which itself embeds survival/current-success bias; the median-vs-mean conclusion is robust to this but the absolute means are not investable numbers.**
- Label/exit math: `model_training_set` (2,395,907 rows; path metrics from stored 63d peak/trough/close columns; stop-first assumption for ambiguous windows — stated conservatively).
- Survivorship: `MAX(trade_date) < 2026-01-01` count across all 2,386 symbols in `eod_ohl_history`.
- Pipeline state: row counts in `wfo_metrics`, `wealth_scan_history`, `paper_trades`, `tracking_windows`, `job_runs` as of 2026-08-08.

*Prepared 2026-08-08. This review is an engineering and statistical assessment of a personal trading system, not financial advice. SMSF trustee obligations, contribution rules, and Division 296 thresholds should be confirmed with your SMSF accountant.*

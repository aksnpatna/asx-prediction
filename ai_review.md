# AI Review — ASX Prediction System
**Date:** 2026-08-18 | **Reviewer:** Antigravity AI  
**Scope:** Holistic review of the role AI plays in the pipeline, with a specific focus on whether AI *rejection* adds measurable value on top of a 60.5% model top-decile hit rate. This addresses the "lukewarm feedback" that the system is model-driven and examines what AI can realistically contribute next.

---

## Executive Summary

This system is **correctly architected**: the model is the primary signal generator and the AI layer is a secondary filter. That is not a weakness — it is sound engineering. The "lukewarm feedback" that it is model-driven is actually a compliment, because the model (AUC 0.74–0.75, top-decile 60.5%) outperforms every honest published ASX benchmark found (compare: LSTM+FinBERT public GitHub AUC 0.64, Trading Agent live 51.5% directional).

The real question is: **can the 6-persona AI debate selectively reject bad top-decile picks, raising the hit rate above 60.5% on the sub-set it approves — without destroying throughput?**

The honest answer as of today: **unknown but structurally plausible.** Fix 43 (2026-08-18) just wired the mechanism to answer this empirically (~Aug 29 for the first real data point). This review sets out what the answer means and what comes next.

---

## 1. Current State — What the Model Does Well

| Metric | Value | Context |
|---|---|---|
| AUC (modern window, in-sample) | **0.7516** | Best of any honest published ASX benchmark |
| Top-decile hit rate (in-sample) | **60.5%** | vs 21.3% base rate = **2.84× lift** |
| WFO 2024 fold (genuine OOS) | **57.5%**, AUC 0.728 | Pure out-of-sample |
| WFO recent fold (OOS, 22d) | **50.2%**, AUC 0.684 | Still accumulating — first h30d result Aug 29 |
| Honest backtest P&L (PIT-safe) | **+0.8% p.a.** gross | After lookahead features zeroed (Fix 28) |
| Consensus gate (R2 — 3-head) | **~68% hit rate** | On ~36% of top-decile candidates where LGBM+Ridge+RF agree |
| Model status | **FROZEN** (MODEL_LOCK_IN) | No re-adoption until paper trades resolve |

**Key model strengths:**
- LGBM with 200K modern-window rows captures non-linear macro × technical interactions that a linear model cannot
- The R2 consensus gate already demonstrates that filtering "3 heads agree" → 68% beats raw top-decile 60.5% — the same logic should apply to AI consensus on top
- Bear-market breaker (VIX ≥ 25 AND XJO < SMA200) correctly gates out the 2022 regime (AUC 0.572 — no deployable edge)
- Announcement NLP features are live but < 60 days of coverage — will activate automatically as data accrues

---

## 2. The AI Layer — What It Currently Does

```
5AM  → Broad scan: 1,553 tickers scored by LGBM ensemble → percentile-tiered
8AM  → 6-persona AI debate on top-8 top-decile candidates (sorted by LGBM proba)
       → APPROVE / REJECT / UNSEEN verdict persisted to ai_verdicts table
       → StockCard shows ✅ AI APPROVED / ⛔ AI REJECTED / 🤖 REVIEW PENDING
```

### The 6 Personas and What They Challenge

| Persona | What It Adds Beyond the Model |
|---|---|
| Technical Strategist | Price pattern narrative vs model's quantitative feature drivers |
| Macro Regime Analyst | Live macro interpretation (VIX / XJO / AUD / copper) vs model's lagged feature values |
| Valuation Analyst | PE context, EPS growth trend, analyst consensus vs model's snapshot fundamentals |
| Risk Controller | Stop sizing, drawdown risk, WFO gate status — qualitative position sizing logic |
| Tax Compliance | CGT, SIS Act, franking credits — purely qualitative, model is blind here |
| Liquidity Officer | Spread, depth, block trades, market cap tier — model sees 20-day avg vol, not today's order book |

**Model-aware context (Fix 29) is the most important AI improvement to date.** Each persona now sees:
- Top 5 LGBM feature drivers with the candidate's **actual feature values** (not just feature names)
- 3 nearest historical setups for this symbol from the training matrix with real outcomes
- RSI/momentum percentile vs the last 180 days of market rows

This is the difference between "AI vs model" and "AI working with model." The AI can now say: *"The model is bullish because pct_institutions=0.38 and momentum_20d=+0.12, but today's ASX announcement shows a major institutional holder reduced their stake. Vote: REJECT."*

---

## 3. Honest Assessment — What AI Rejection Can and Cannot Do

### 3A. Structural Case FOR AI Rejection

The model is trained purely on **quantitative features**. It cannot read:

| Blind Spot | Why It Matters |
|---|---|
| ASX announcement text | Guidance downgrades, capital raises, regulatory decisions — often appear in text before price reflects them |
| Management tone / confidence | Qualitative signal of insider optimism/pessimism |
| Event proximity risk | Earnings in 3 days = binary risk event the model doesn't weight as narrative |
| Sector-specific regulatory risk | Mining EPA rulings, bank APRA constraints — require context the model doesn't have |
| Liquidity traps | Model sees 20d avg volume; AI can be told today's real spread and market depth |
| "Value trap" narrative | A stock may look cheap on PE but have deteriorating fundamentals the model's snapshot can't distinguish from genuine value |

These are exactly the domains where a language model with 6 independent analytical angles can catch a "looks good on paper" setup. **If AI approval correlates with stocks where these qualitative signals are benign, the AI-approved sub-set should beat 60.5%.**

### 3B. Structural Case AGAINST Over-Relying on AI

1. **FLT precedent (Fix 41 — cautionary case study):** The AI debate APPROVED FLT while the model's true LGBM proba was 17.8% (below base rate 21.3%). The AI was being fed a heuristic score, not the real model proba. **AI is only as good as the data fed to it.** Fix 41 hardened the prompt: "P(+8% before −8%): X% (base 21%) — do NOT approve if below base rate." This class of failure is now blocked.

2. **Zero validated track record:** The WFO split by AI decision (Fix 43) has 0 events as of today. Until ~Aug 29, the AI gate's value is entirely theoretical.

3. **Throughput tax:** Top decile ≈ 155 candidates/scan. AI debate covers 8/day. That is **5% coverage**. AI rejection cannot improve portfolio-level hit rate unless coverage scales or the 8 picks are selected more strategically (e.g., the 8 *most borderline* top-decile picks, not the 8 *highest-ranked*).

4. **Consistency problem:** A language model gives different verdicts on the same stock on different days. The model's 60.5% is deterministic. AI verdicts have variance. Currently there is no calibration feedback loop anchoring the AI to its own prior verdicts.

5. **LLM can be "wowed" by the same narrative the market already priced:** An AI that sees "strong institutional ownership + bullish momentum + analyst upgrades" may approve the same stocks the market has already bid up. The model, by construction, is trained to find mispricing; the AI may simply ratify consensus.

---

## 4. The Right Mental Model — AI as a Conviction Amplifier, Not a Gatekeeper

```
LAYER 1: Model ranking (top-decile selection)
         → 60.5% hit rate on ~155 stocks/scan
         
LAYER 2: Consensus gate (R2 — LGBM decile + Ridge q75 + RF q75 agree)
         → ~68% hit rate on ~56 stocks/scan
         
LAYER 3: AI debate (6-persona deep-dive on top 8 by LGBM proba)
         → Target: ≥ 65–70% on the 8 approved stocks/day
         → Target: ≤ 50% on the 8 rejected stocks/day (must show discrimination)
         
LAYER 4: Entry timing and position sizing
         → Realised P&L via deterministic stop-loss / take-profit
```

The AI's job is a **conviction amplifier** for the stocks that already passed Layers 1 and 2. It should:
- **Kill** a pick that passed the model but has a visible narrative red flag
- **Raise conviction** (position size signal) on picks where all 6 personas align
- **Never** be used to recover stocks that failed the model tier gate (FLT failure mode)

This is exactly "AI rejection on top of model, not against model."

---

## 5. What the Data Will Tell Us (Aug 29 Onwards)

Fix 43 segments the WFO by `ai_decision`. When h30d results land:

| Segment | If AI adds genuine value | If AI is noise |
|---|---|---|
| AI-approved hit rate | **> raw 60.5%** by ≥ 5pp | ≈ 60.5% |
| AI-rejected hit rate | **< raw 60.5%** by ≥ 5pp | ≈ 60.5% |
| Spread (approved − rejected) | **≥ 10pp** | < 3pp |

### Suggested Decision Rules

| Evidence | Action |
|---|---|
| ≥ 20 approved events, approved − rejected spread ≥ 5pp | AI gate validated — expand daily budget from 8 to 15 candidates, consider mandatory gate for auto-trade |
| ≥ 20 approved events, spread < 2pp | AI gate is noise on this data — retire as hard gate; keep as advisory/display only |
| < 20 events | Do NOT decide — wait for more data |

> [!IMPORTANT]
> Do not change anything structural in the AI prompt or model before Aug 29. Every change made before that data arrives is optimising noise. The model is frozen for this exact reason.

---

## 6. Action Plan — How to Strengthen AI's Contribution (Prioritised)

### Priority 1 (Now → Aug 29): Wait and Measure

**Action:** Do nothing structural. Monitor `wfo_metrics.notes` starting Aug 29 for the AI-approved vs AI-rejected split. The infrastructure is correct; the measurement instrument is running.

**Checkpoints:**
- [ ] **Aug 29:** Pull WFO segmented hit rates. Check if ≥ 20 events in any segment
- [ ] **Sep 15:** If ≥ 20 approved events, apply decision rules above
- [ ] **Oct 1:** G3 accumulation check — need 60 v2-model paper trades with 63d outcomes

---

### Priority 2 (Now, Low Effort): Improve AI Input Quality

The AI debate is only as good as what it's fed. Current fixable gaps:

| Gap | Specific Fix | Effort |
|---|---|---|
| kNN shows 3 setups; may be stale | Increase `k` to 5, restrict to last 12 months in `model_context.py` | 15 min |
| `days_to_earnings` not in AI context | Add from existing valuation dict to `_compact_features` / model context block | 30 min |
| `short_ratio` absent | Pull from EODHD or yfinance valuation dict, add to model_context | 1 hr |
| AI doesn't know yesterday's verdict | Look up `ai_verdicts` for last 3 trading days per candidate, pass as `prior_verdicts` to `_format_data_blob` | 2 hrs |
| AI confidence not calibrated | After Aug 29, fit isotonic regression on `ai_confidence` → actual hit rate | After data |

**None of these touch the model or the prompt fundamentally — they improve the information the AI receives.**

---

### Priority 3 (Post Sep 15, if AI validated): Scale Coverage Selectively

Currently 8 stocks/day out of ~155 top-decile. Options:

1. **Lightweight pre-filter (recommended):** Run a fast single-LLM "flag or pass" on all 155 using only the model_context block (no web search, no debate). Flag the 15 most anomalous; run the full 6-persona debate only on those 15. Triples coverage for ~1.5× LLM cost.

2. **Target borderline picks:** Currently the 8 picked are highest-LGBM-proba. Consider switching to the **8 with the largest gap between LGBM proba and Ridge/RF predictions** — these are the most uncertain picks where AI disambiguation has highest expected value.

3. **Retrospective analysis:** For AI-unseen stocks that closed as wins/losses, do post-hoc analysis to understand what the AI would have said. Builds calibration dataset without capital cost.

---

### Priority 4 (Post Sep 15): Close the AI Consistency Gap

Introduce a **verdict stability check:**
- If a stock was AI-REJECTED yesterday, today's prompt should explicitly state: *"This stock was REJECTED yesterday with reason [X]. Has that reason resolved? If not, reject again."*
- If a stock was AI-APPROVED yesterday but LGBM proba has dropped, flag this delta.

This grounds the AI's evaluation in continuity, not blank-slate re-analysis each morning.

---

### Priority 5 (Oct onwards): AI-as-Exit-Signal Experiment

The model targets entry. The AI debate currently evaluates entry. But the AI may be **better at flagging exit events** than entry, because exits are often narrative-driven:
- Management downgrade in ASX announcement
- Competitor negative announcement in same sector
- Macro regime shift confirmed (RBA surprise rate move)
- Open position approaching a known event risk (AGM, earnings)

**Proposal:** Run a lightweight 2-persona debate (Risk Controller + Macro Regime Analyst) on all **open paper trades** every morning. Flag any where the AI says "conditions for this trade have materially deteriorated." Feed the result into the 15-minute paper trade monitor as a soft exit signal.

**Why this is better than improving entry selection right now:**
- Universe is small (~8 open trades) → near-zero LLM cost
- Feedback loop is faster (trade closes within 63 days)
- Qualitative exit signals are the largest unmeasured alpha source in the current system

---

## 7. What AI Cannot Fix — Be Explicit

| Problem | Why AI cannot fix it |
|---|---|
| Honest backtest P&L is only +0.8% p.a. | This is a lookahead data problem (Fix 28). AI narration cannot change historical return math |
| WFO 2022 bear fold AUC 0.572 | The model genuinely has less edge in bear regimes. Regime-conditional AI debate risks over-optimising noise |
| G3 gate requires 60 closed paper trades | This is a calendar-time problem. 63-day horizon = ~3 months minimum. AI cannot accelerate time |
| Fundamental data is snapshot-only (since Jul 2026) | AI cannot reconstruct point-in-time fundamental data that was never stored |
| Model's honest OOS edge is ~50.2% top-decile (recent fold) | If OOS edge is 50%, AI approval of a sub-set of those picks cannot reliably exceed 50% without genuine qualitative edge — which is unproven |

---

## 8. Summary Scorecard — AI Contribution Today vs Dec 2026 Target

| AI Role | Today (Aug 2026) | Target (Dec 2026) |
|---|---|---|
| Entry filtering (top decile → entry) | 8 stocks/day reviewed; 0 validated events | 15 stocks/day; ≥ 5pp lift over model base confirmed |
| Model context enrichment | Live — top features, kNN, market percentile | + short ratio, days_to_earnings, prior verdicts |
| Announcement NLP | Live — 50 symbols/day, < 60d coverage | 6+ months coverage → features activated in model |
| Exit signal | Not implemented | 2-persona daily exit-flag on open trades |
| AI-vs-model validation | Wired (Fix 43), 0 events | 20+ events; empirical decision made on gate status |
| Consistency (prior verdicts) | Blank-slate daily | Prior-verdict anchoring implemented |
| AI confidence calibration | Not calibrated | Isotonic calibration on ai_confidence |

---

## 9. The Bottom Line

> The model is right to be the centre of gravity. AUC 0.75 with a 60.5% top-decile hit rate in a market where the honest published benchmark is 0.64 is a genuine, documented edge. The "lukewarm feedback" that this is model-driven is correct — and **correct is good**.

> AI adds value in this system **not by overriding the model, but by auditing its qualitative blind spots**: narrative risk, event proximity, management tone, liquidity traps, and exit timing. The 6-persona debate is the right architecture for this. Fix 29 (model-aware context) made it materially smarter. Fix 43 (segmented WFO) is the right measurement instrument.

> **The single most important action right now is: wait for Aug 29 data before changing anything structural.** Every change before that arrives is optimising noise on an instrument that has never been calibrated. The model is frozen. The paper trades are accumulating. The WFO is running. Let it cook.

---

*Sources: MODEL_IMPROVEMENT_LOG.md (Fixes 1–43), ARCHITECTURE.md, ASX_STRATEGY_DEEP_DIVE.md, backend/model_context.py, GROQ_INTEGRATION_ANALYSIS.md*

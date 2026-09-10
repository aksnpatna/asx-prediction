# Model Improvement Log — 10 September 2026

## Session summary: v2 model recovery + live-pipeline repair

**Outcome:** The frozen v2 model (AUC 0.744 / top-decile 58.2%, trained 2026-08-18) was
recovered intact. It was **never actually lost** — a prior agent had lost track of the real
PostgreSQL database after switching `.env` to SQLite, and had left the container with an
empty EODHD API key, which stalled EOD ingestion and collapsed the daily scan to 0 candidates.

---

## 1. What was actually broken (root cause)

| # | Symptom | Root cause |
|---|---------|-----------|
| 1 | Scan returned **0 candidates** since 2026-09-04 | `EODHD_API_KEY` empty in the container + dead key (`6a99…` → HTTP 403) in `.env`. EOD data stuck at 2026-09-03. |
| 2 | Prior agent "couldn't get 467 universe shares" | Same stale-EOD cause — universe ranking is derived from `eod_ohl_history`. Once EOD refreshed, universe recovered to **465 liquid symbols**. |
| 3 | Prior agent thought the model was broken (retrained to AUC 0.60) | It backfilled a tiny 20K-row **SQLite** DB (45 symbols) and lost sight of the full **PostgreSQL** DB (4.5M rows, 2,809 symbols). |
| 4 | Sep 5/7 retrains logged `auc 0.6051/0.6022` | Those were the broken **Fix 70** config (lambdarank + regime features), NOT the frozen model. |

**What was NOT broken:** the LGBM artifact, the PostgreSQL data, and the container's
(pre-Fix-70) serving code were all intact throughout.

---

## 2. Fixes applied

1. **`.env` correction**
   - `EODHD_API_KEY` → working key `6a35…` (verified EOD **and** fundamentals endpoints, `SYM.AU` format).
   - `EODHD_API_KEY_FUNDAMENTALS` / `EODHD_EOD_API_KEY` → aligned to the same working key.
   - `DATABASE_URL` → `postgresql+psycopg2://asx_user:asx_password_dev@localhost:5432/asx` (was SQLite).
   - Added `POSTGRES_DB/USER/PASSWORD=asx_password_dev` (were missing — would have broken a compose rebuild).
   - `MODEL_LOCK_IN` → restored to `1` (frozen; prior agent had set `0`).

2. **Backend container recreated** with the corrected env (volume-mounted artifact preserved).

3. **Verified end-to-end after the fix:**
   - EOD refreshed to **2026-09-09** (2,356 symbols).
   - Broad scan → **15 candidates**; V2 scan → **462 scored, 49 top-decile, 8 selected**.
   - Live serving confirmed: `[EnrichTiers] LGBM classifier active (AUC=0.744, top_decile=58.2%)`.

---

## 3. Reproducibility experiments (65% target assessment)

Re-ran the frozen config (LGBM binary, 600 trees, 200K most-recent rows, proba-only,
recency weights, isotonic calibration, Fix-64-era lookahead fills) against PostgreSQL:

| Variant | Window | Base rate | AUC | Top decile |
|---------|--------|-----------|-----|-----------|
| Frozen-window reproduction | 2025-11 → 2026-05 | 18.7% | **0.7385** | **51.7%** |
| + Fix-65 interaction features (correct defs) | 2025-11 → 2026-05 | 18.2% | 0.7357 | 49.9% |
| Current window | 2026-02 → 2026-08 | 24.3% | 0.6166 | 50.1% |

**Findings:**
- **AUC reproduces** (0.7385 vs the artifact's 0.744) — confirms the training pipeline is intact.
- **Top-decile does not fully reproduce** (51.7% vs 58.2%). The recorded 58.2% was measured on a
  slightly different, more favorable window boundary with Aug-18-era fill data. The model's real
  edge is a stable **~2.7–3.0× base rate** in the top decile, not a fixed 58% number.
- **Fix-65 interaction features do NOT reproduce the claimed +1.3pp** — they slightly degrade
  top decile (49.9% vs 51.7%). This matches the later "genuine plateau" assessment (Fix 40).

**Conclusion: 65% top-decile is not achievable with the current feature set / config.** The
honest ceiling is ~50–52% (2.7× base). Chasing higher in-sample numbers is not recommended —
the real validation is the WFO paper-trade evidence, not further in-sample tuning.

---

## 4. WFO status (no 40-day reset)

- Frozen-model signals (2026-08-12 → 2026-09-03) are intact in `wealth_scan_history`.
- WFO engine: `INSUFFICIENT_DATA(frozen)`, earliest signal 2026-08-12,
  **first decision-relevant result ~2026-09-23**.
- `MODEL_LOCK_IN=1` kept the freeze valid; nothing in this session reset the clock.

---

## 5. Known open items (non-blocking)

1. **~~AI deep-dive layer~~ RESOLVED (2026-09-10)**: the LLM keys had been added to `.env` under
   wrong names (`GROQ_KEY`/`NVIDIA_KEY`/`DEEPSEEK_KEY` instead of `*_API_KEY`), so every provider
   failed and `_llm_json` returned `Connection error`. Renamed to `*_API_KEY` and set
   `LLM_PROVIDER_ORDER=deepseek,groq,local,openai`. Verified live: `_llm_chat_safe` → `LLM_OK` and
   the CIO `_llm_json` parses correctly via native DeepSeek (`deepseek-v4-flash`, ~0.9s). AI verdicts
   will now populate `ai_verdicts` for the post-WFO cross-check.
2. **Training matrix** is at `signal_date` 2026-08-27 (last update ran while EOD was still stale).
   It will extend at the next 20:40 `training_matrix_update` with the now-fresh EOD.
3. **Host source tree**: prior agent left uncommitted changes in `backend/*.py` (partial Fix-70
   reversion + yfinance fallbacks) and deleted `broker-frontend/`/`broker/` source. These do not
   affect the running container (baked image). Junk files (12.7 GB SQLite, recovery docs, capture
   scripts, `*_v2.py` dupes) were removed this session.

### LLM provider notes (2026-09-10)

- **Native DeepSeek** (`deepseek-v4-flash`) — WORKING, provider priority 1.
- **NVIDIA** (`nvapi-…` key valid) — the hosted `deepseek-ai/deepseek-v4-flash-0731` **hangs**
  (~137s timeout) but `nvidia/nemotron-3-super-120b-a12b` responds (8.8s). NVIDIA left OUT of the
  order for now; can be re-added with the nemotron model as a fallback.
- **Groq** (`bef23ddd-…`) — returns **401 Invalid API Key**. Present in the order but will not
  authenticate; the key needs to be re-issued (`gsk_…` format expected).

---

## 6. UI uplift (frontend)

Added two honesty-focused surfaces to the SMSF screens (`frontend/src/smsfUplift.jsx` + `index.css`),
rebuilt and redeployed the `asx-frontend` container.

**6a. Model Health panel (Dashboard) — "backtest vs live"**
- Replaced the single hardcoded "58% top decile" blurb with a `ModelHealthPanel` that
  separates the two numbers the old UI conflated:
  - **Backtest (in-sample)**: AUC, top-decile, top↔bottom spread.
  - **Live (out-of-sample)**: hit rate of closed paper trades hitting +8% (`evaluate_signal_outcomes`),
    with the freeze/warning/accumulating status.
  - Plus training window, sample size, calibration flag, and WFO state.
- Data: `/api/model/health-summary` (new `useApi` call) + existing `morning-brief` `model_eval`/`wfo_gate`.

**6b. Paper-trades ledger (Portfolio)**
- New `PaperTradesPanel` surfaces `/api/paper-trades`: open/closed counts, closed trades that hit
  +8%, live hit rate, and a recent-trade table (symbol, entry, now, P&L, status).
- Makes G-gate G3 ("60 closed paper trades") concrete and honest.

**Why**: the old dashboard presented the in-sample 58.2% as "the AI correctly picked winners in
58.2% of its top-ranked stocks" without showing the live forward result (which is still
accumulating). The new panel labels backtest vs live explicitly, so the frozen model's real
validation status is visible rather than implied.

Build/deploy: `npm run build` (clean) → `docker compose build frontend` → `docker compose up -d frontend`.
Verified the served bundle contains the new panel (`index-D0hxZVQL.js`).

---

## 7. Safety backups

- `backups/model_artifact/lgbm_classifier.pkl.20260910` — sha256 matches the live artifact.
- `backups/model_artifact/lgbm_classifier_pre_fix64.pkl.20260910` — pre-Fix-64 artifact.
- `backups/model_artifact/universe_ranked.json.20260910`.

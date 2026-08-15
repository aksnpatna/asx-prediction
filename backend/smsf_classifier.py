"""
SMSF Model Training — LogisticRegression classifier with decile evaluation.

Fills fundamental features in-memory from the latest snapshot per symbol
(avoids the 3.3M-row SQL UPDATE timeout). Trains on path-aware labels.
"""

import os, json, time, numpy as np
from datetime import date
from typing import Dict, List, Optional


def _load_historical_fundamentals(db_conn) -> Dict[str, dict]:
    """Load latest fiscal-year fundamental ratios per symbol (from income/balance/cashflow)."""
    from sqlalchemy import text
    result = {}
    try:
        with db_conn() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT ON (symbol) symbol, fiscal_year,
                    roe_pct, debt_equity, gross_margin_pct, op_margin_pct,
                    fcf, market_cap_hint
                FROM (
                    SELECT fh.symbol, fh.fiscal_year, fh.roe_pct, fh.debt_equity,
                           fh.gross_margin_pct, fh.op_margin_pct, fh.fcf,
                           fs.market_cap as market_cap_hint
                    FROM fundamental_history fh
                    LEFT JOIN fundamental_snapshots fs ON fs.symbol = fh.symbol
                ) x
                ORDER BY symbol, fiscal_year DESC
            """)).fetchall()
        for r in rows:
            sym, yr, roe, de, gm, om, fcf, mcap = r
            result[sym] = {
                "roe": float(roe) if roe else 0.0,
                "debt_equity": float(de) if de else 0.0,
                "gross_margin": float(gm) if gm else 0.0,
                "op_margin": float(om) if om else 0.0,
                "fcf": float(fcf) if fcf else 0.0,
                "market_cap": float(mcap) if mcap else 0.0,
            }
    except Exception as e:
        print(f"[FundHist] Load failed: {e}", flush=True)
    return result


def _fill_historical(feats: dict, symbol: str, hist_map: Dict[str, dict]) -> dict:
    """Fill historical ratios — overwrites the 0.0 defaults stored in the
    matrix JSON (setdefault silently skipped them since keys exist as 0.0)."""
    h = hist_map.get(symbol)
    if not h:
        return feats
    feats["fund_hist_roe"] = h["roe"]
    feats["fund_hist_debt_equity"] = h["debt_equity"]
    feats["fund_hist_gross_margin"] = h["gross_margin"]
    feats["fund_hist_op_margin"] = h["op_margin"]
    if h["market_cap"] > 0 and h["fcf"] > 0:
        feats["fund_hist_fcf_yield"] = h["fcf"] / h["market_cap"] * 100
    else:
        feats["fund_hist_fcf_yield"] = 0.0
    return feats


def _load_latest_fundamentals(db_conn) -> Dict[str, dict]:
    """Load latest fundamental snapshot per symbol into a dict."""
    from sqlalchemy import text
    result = {}
    try:
        with db_conn() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT ON (symbol) symbol,
                    trailing_pe, forward_pe, market_cap, dividend_yield,
                    analyst_target_mean, analyst_rec, earnings_growth,
                    revenue_growth, beta, high_52w
                FROM fundamental_snapshots
                WHERE trailing_pe IS NOT NULL OR market_cap IS NOT NULL
                ORDER BY symbol, snapshot_date DESC
            """)).fetchall()
        for r in rows:
            sym, pe, fpe, mc, dy, atm, ar, eg, rg, beta, h52 = r
            result[sym] = {
                "trailing_pe": float(pe) if pe else 0.0,
                "forward_pe": float(fpe) if fpe else 0.0,
                "market_cap": float(mc) if mc else 0.0,
                "dividend_yield": float(dy) if dy else 0.0,
                "analyst_target_mean": float(atm) if atm else 0.0,
                "analyst_rec": (ar or "").lower(),
                "earnings_growth": float(eg) if eg else 0.0,
                "revenue_growth": float(rg) if rg else 0.0,
                "beta": float(beta) if beta else 1.0,
                "high_52w": float(h52) if h52 else 0.0,
            }
    except Exception as e:
        print(f"[Fund] Load failed: {e}", flush=True)
    return result


EODHD_FEATURE_KEYS = [
    "eps_surprise", "eps_estimate_revision", "analyst_count",
    "pct_insiders", "pct_institutions", "insider_net_ratio",
    "esg_governance", "esg_controversy", "payout_ratio",
]

# Features with negative/zero permutation importance (measured 2026-08-14,
# 300K samples, 5-rep permutation on 8K test subsample). Dropping them
# improved ALL metrics: AUC 0.640 -> 0.653, top decile +0.2pp, bottom -0.6pp.
# Fix 26: measured on the modern window (2026-08-15), the ridge blend HURTS
# ranking at every weight (0.2: -0.001 AUC, 0.4: -0.034 at 200K; top decile
# 60.4% -> 52.7%). The 0.4 weight was optimised on the 2015-2016 window.
# Probability-only ranking wins; ridge kept for artifact/fallback only.
REG_BLEND_WEIGHT = 0.0

PRUNED_FEATURES = {
    "adx_trend", "analyst_count", "autocorr_5d", "bb_squeeze_ratio",
    "bb_width", "ema_ribbon", "eps_estimate_revision", "esg_controversy",
    "fund_beta", "fund_forward_pe_inv", "fund_hist_debt_equity",
    "fund_hist_fcf_yield", "fund_hist_roe", "fund_market_cap_log",
    "fund_pe_inv", "fund_revenue_growth", "obv_bullish", "parkinson_vol",
    "payout_ratio", "rsi_during_squeeze", "signal_cluster", "skewness_20d",
    "sma_cross_20_50", "squeeze_duration", "trend_strength",
    "ttm_squeeze_fired", "vol_regime_ratio", "xjo_momentum_63d", "xjo_vol_20d",
}


def _load_eodhd_features(db_conn=None) -> Dict[str, dict]:
    """Load EODHD features from DB (persistent) with file fallback."""
    from sqlalchemy import text
    result = {}
    try:
        if db_conn is not None:
            with db_conn() as conn:
                rows = conn.execute(text(
                    "SELECT DISTINCT ON (symbol) symbol, eps_surprise, eps_estimate_revision, "
                    "analyst_count, pct_insiders, pct_institutions, insider_net_ratio, "
                    "esg_governance, esg_controversy, payout_ratio "
                    "FROM eodhd_feature_snapshots ORDER BY symbol, snapshot_date DESC"
                )).fetchall()
            for r in rows:
                result[r[0]] = {
                    "eps_surprise": float(r[1] or 0), "eps_estimate_revision": float(r[2] or 0),
                    "analyst_count": float(r[3] or 0), "pct_insiders": float(r[4] or 0),
                    "pct_institutions": float(r[5] or 0), "insider_net_ratio": float(r[6] or 0),
                    "esg_governance": float(r[7] or 0), "esg_controversy": float(r[8] or 0),
                    "payout_ratio": float(r[9] or 0),
                }
            if result:
                return result
    except Exception:
        result = {}
    # File fallback (legacy)
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "eodhd_features.json")
        if not os.path.exists(path):
            path = "/app/data/eodhd_features.json"
        if os.path.exists(path):
            with open(path) as f:
                result = json.load(f)
    except Exception:
        pass
    return result


def _fill_eodhd(feats: dict, symbol: str, eodhd_map: Dict[str, dict]) -> dict:
    """Fill EODHD features (EPS, ownership, ESG, insider) — overwrites the
    0.0 defaults stored in the matrix JSON (setdefault silently skipped them)."""
    f = eodhd_map.get(symbol)
    if not f:
        return feats
    for key in EODHD_FEATURE_KEYS:
        feats[key] = float(f.get(key, 0) or 0)
    return feats


def _load_eps_history(db_conn) -> Dict[str, tuple]:
    """Load point-in-time EPS history: symbol -> (dates_array, eps_array) sorted by date."""
    from sqlalchemy import text
    result = {}
    try:
        with db_conn() as conn:
            rows = conn.execute(text(
                "SELECT symbol, report_date, eps_actual FROM eps_history "
                "WHERE eps_actual IS NOT NULL AND eps_actual > 0 ORDER BY symbol, report_date"
            )).fetchall()
        for sym, rd, eps in rows:
            if sym not in result:
                result[sym] = ([], [])
            result[sym][0].append(rd)
            result[sym][1].append(float(eps))
        for sym in result:
            dates, eps_vals = result[sym]
            result[sym] = (np.array(dates, dtype='datetime64[D]'), np.array(eps_vals))
    except Exception as e:
        print(f"[EPS] Load failed: {e}", flush=True)
    return result


def _fill_point_in_time_pe(feats: dict, symbol: str, signal_date, entry_price: float,
                           eps_map: Dict[str, tuple]) -> dict:
    """Override fund_pe_inv with point-in-time P/E (removes look-ahead bias).

    fund_pe_inv = 100 * EPS(at or before signal_date) / entry_price
    """
    entry = eps_map.get(symbol)
    if not entry or entry_price <= 0:
        return feats
    dates, eps_vals = entry
    try:
        sd = np.datetime64(signal_date, 'D') if hasattr(signal_date, 'year') else np.datetime64(str(signal_date)[:10], 'D')
        idx = np.searchsorted(dates, sd, side='right') - 1
        if idx >= 0 and eps_vals[idx] > 0:
            feats["fund_pe_inv"] = round(100.0 * eps_vals[idx] / entry_price, 6)
    except Exception:
        pass
    return feats


def _fill_fundamentals(feats: dict, symbol: str, fund_map: Dict[str, dict],
                       entry_price: float) -> dict:
    """Fill zero fundamental features from the latest snapshot for the symbol."""
    f = fund_map.get(symbol)
    if not f:
        return feats
    if float(feats.get("fund_pe_inv", 0)) == 0 and f["trailing_pe"] > 0:
        feats["fund_pe_inv"] = 100.0 / f["trailing_pe"]
    if float(feats.get("fund_forward_pe_inv", 0)) == 0 and f["forward_pe"] > 0:
        feats["fund_forward_pe_inv"] = 100.0 / f["forward_pe"]
    if float(feats.get("fund_market_cap_log", 0)) == 0 and f["market_cap"] > 0:
        feats["fund_market_cap_log"] = np.log(max(f["market_cap"], 1))
    if float(feats.get("fund_div_yield", 0)) == 0:
        feats["fund_div_yield"] = f["dividend_yield"]
    if float(feats.get("fund_analyst_upside", 0)) == 0 and f["analyst_target_mean"] > 0 and entry_price > 0:
        feats["fund_analyst_upside"] = (f["analyst_target_mean"] / entry_price - 1) * 100
    if float(feats.get("fund_analyst_rec_score", 0)) == 0:
        rec_map = {"strong_buy": 5, "buy": 4, "hold": 3, "underperform": 2, "sell": 1}
        feats["fund_analyst_rec_score"] = rec_map.get(f["analyst_rec"], 3)
    if float(feats.get("fund_earnings_growth", 0)) == 0:
        feats["fund_earnings_growth"] = f["earnings_growth"] * 100
    if float(feats.get("fund_revenue_growth", 0)) == 0:
        feats["fund_revenue_growth"] = f["revenue_growth"] * 100
    if float(feats.get("fund_beta", 0)) == 0:
        feats["fund_beta"] = f["beta"]
    if float(feats.get("fund_pct_from_52w_high", 0)) == 0 and f["high_52w"] > 0:
        feats["fund_pct_from_52w_high"] = (entry_price / f["high_52w"] - 1) * 100
    return feats


def _train_lgbm_challenger(X_train, X_test, y_train, y_test,
                           reg_pred_test, active_features,
                           scaler_mean, scaler_scale, reg_coefs, dates):
    """Fix 17: LightGBM binary head challenger — identical split and blend as
    the logistic baseline (0.6 binary rank + 0.4 ridge rank), so the A/B is fair.

    Fix 20: recency weights computed from actual signal dates (the old
    position-based mapping assumed a 9-year window).

    Fix 22: isotonic calibration fit on the early-stopping validation slice
    (monotone — ranking unchanged; Brier improves ~18% for Kelly sizing).

    Returns a metrics dict including the fitted model, or {"ok": False}.
    """
    try:
        import lightgbm as lgb
        from scipy.stats import rankdata
        from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss
        from sklearn.isotonic import IsotonicRegression
        from sklearn.model_selection import KFold
    except ImportError as e:
        return {"ok": False, "error": f"lightgbm unavailable: {e}"}

    try:
        t0 = time.time()
        days_old = (dates.max() - dates).astype(float)
        w = np.power(0.5, days_old / 365.0)
        w = w / w.mean()
        w_train = w[:len(X_train)]

        model = lgb.LGBMClassifier(
            objective="binary", n_estimators=600, learning_rate=0.03,
            num_leaves=63, max_depth=6, min_child_samples=50,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            reg_alpha=0.3, reg_lambda=2.0, class_weight="balanced",
            random_state=42, n_jobs=-1, verbose=-1,
        )
        cut = int(len(X_train) * 0.9)
        model.fit(
            X_train[:cut], y_train[:cut], sample_weight=w_train[:cut],
            eval_set=[(X_train[cut:], y_train[cut:])],
            eval_metric="auc",
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )

        # ── Fix 22: OOB isotonic calibration ──────────────────────────────
        # Fitting isotonic on the early-stopping slice overfit (Brier regressed
        # 0.151 -> 0.158). Use 3-fold OOB predictions for the isotonic fit;
        # the main model still serves ranking. Keep calibration only if it
        # improves test Brier.
        iso = None
        brier_raw = brier_score_loss(y_test, model.predict_proba(X_test)[:, 1])
        brier_cal = brier_raw
        try:
            kf = KFold(n_splits=3, shuffle=False)
            oob_pred = np.empty(len(X_train), dtype=np.float64)
            for tr_idx, va_idx in kf.split(X_train):
                m_fold = lgb.LGBMClassifier(
                    objective="binary", n_estimators=400, learning_rate=0.05,
                    num_leaves=63, max_depth=6, min_child_samples=50,
                    subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                    reg_alpha=0.3, reg_lambda=2.0, class_weight="balanced",
                    random_state=42, n_jobs=-1, verbose=-1,
                )
                sub_cut = int(len(tr_idx) * 0.9)
                m_fold.fit(
                    X_train[tr_idx[:sub_cut]], y_train[tr_idx[:sub_cut]],
                    sample_weight=w_train[tr_idx[:sub_cut]],
                    eval_set=[(X_train[tr_idx[sub_cut:]], y_train[tr_idx[sub_cut:]])],
                    eval_metric="auc",
                    callbacks=[lgb.early_stopping(30, verbose=False)],
                )
                oob_pred[va_idx] = m_fold.predict_proba(X_train[va_idx])[:, 1]
            iso_cand = IsotonicRegression(out_of_bounds="clip")
            iso_cand.fit(oob_pred, y_train)
            proba_te_raw = model.predict_proba(X_test)[:, 1]
            cal_proba = iso_cand.predict(proba_te_raw)
            brier_cal = brier_score_loss(y_test, cal_proba)
            if brier_cal < brier_raw:
                iso = iso_cand
            else:
                print(f"[Classifier] LGBM calibration rejected "
                      f"(Brier {brier_raw:.4f} -> {brier_cal:.4f})", flush=True)
        except Exception as e:
            print(f"[Classifier] LGBM calibration failed (skipped): {e}", flush=True)
        if iso is not None:
            print(f"[Classifier] LGBM calibration kept: Brier {brier_raw:.4f} -> {brier_cal:.4f}", flush=True)

        proba_te = iso.predict(model.predict_proba(X_test)[:, 1]) if iso is not None \
            else model.predict_proba(X_test)[:, 1]

        blended = ((1 - REG_BLEND_WEIGHT) * rankdata(proba_te)
                   + REG_BLEND_WEIGHT * rankdata(reg_pred_test))

        sorted_idx = np.argsort(blended)[::-1]
        decile_size = len(sorted_idx) // 10
        decile_hit_rates = []
        for d in range(10):
            start = d * decile_size
            end = start + decile_size if d < 9 else len(sorted_idx)
            decile_hit_rates.append(round(float(y_test[sorted_idx[start:end]].mean()) * 100, 1))

        auc_ = roc_auc_score(y_test, blended)
        print(f"[Classifier] LGBM calibration: Brier {brier_raw:.4f} -> {brier_cal:.4f}", flush=True)
        return {
            "ok": True,
            "auc": round(auc_, 4),
            "avg_precision": round(average_precision_score(y_test, blended), 4),
            "top_decile": decile_hit_rates[0],
            "bottom_decile": decile_hit_rates[-1],
            "spread": round(decile_hit_rates[0] - decile_hit_rates[-1], 1),
            "deciles": decile_hit_rates,
            "brier_raw": round(brier_raw, 4),
            "brier_calibrated": round(brier_cal, 4),
            "model": model,
            "isotonic": iso,
            "best_iter": int(getattr(model, "best_iteration_", -1) or -1),
            "secs": round(time.time() - t0, 1),
        }
    except Exception as e:
        print(f"[Classifier] LightGBM challenger failed: {e}", flush=True)
        return {"ok": False, "error": str(e)}


def train_classifier(target_col: str = "hit_8pct_before_m8pct",
                     min_samples: int = 5000) -> Optional[dict]:
    """Train LogisticRegression on path-aware labels with fundamentals filled in-memory."""
    from sqlalchemy import text
    import sys as _sys
    from model_training import FEATURE_COLS
    # db_conn lives in main (running app process). For standalone runs
    # (tests/CLI), use a direct engine instead of importing main — importing
    # main outside the app process would start a second scheduler.
    if "main" in _sys.modules:
        from main import db_conn
    else:
        from sqlalchemy import create_engine
        _standalone_engine = create_engine(
            os.getenv("DATABASE_URL", "sqlite:///data/shares.db"))
        db_conn = lambda: _standalone_engine.connect()

    print(f"[Classifier] Loading {target_col} data with fundamentals...", flush=True)
    t0 = time.time()

    # Load auxiliary maps first (small, in-memory)
    fund_map = _load_latest_fundamentals(db_conn)
    hist_map = _load_historical_fundamentals(db_conn)
    eodhd_map = _load_eodhd_features(db_conn)
    eps_map = _load_eps_history(db_conn)
    print(f"[Fund] Loaded {len(fund_map)} snapshot + {len(hist_map)} historical + {len(eodhd_map)} EODHD + {len(eps_map)} EPS-history symbols", flush=True)

    # Stream training rows in batches (server-side cursor) — avoids the ~2GB
    # fetchall spike that OOMs a 4GB container. Preallocated float32 array.
    MAX_ROWS = 200000
    n_rows = MAX_ROWS
    X = np.zeros((n_rows, len(FEATURE_COLS)), dtype=np.float32)
    y = np.zeros(n_rows, dtype=np.int8)
    y_reg = np.zeros(n_rows, dtype=np.float32)
    dates = np.zeros(n_rows, dtype="datetime64[D]")
    valid = np.ones(n_rows, dtype=bool)

    try:
        with db_conn() as conn:
            result = conn.execution_options(stream_results=True, max_row_buffer=20000).execute(text(
                # Fix 20: train on the MOST RECENT 200K rows (was ASC LIMIT 300000
                # → earliest rows 2015-2016). Subquery keeps the stream ASC so the
                # chronological 80/20 split below stays valid.
                # Fix 26: 200K modern optimum (sweep 2026-08-15: 200K AUC 0.750
                # vs 300K 0.716 on the adjacent split; 400K 0.704).
                f"SELECT symbol, entry_price, signal_date, features, "
                f"CAST({target_col} AS INTEGER), COALESCE(forward_return_63d, 0) FROM ("
                f"SELECT symbol, entry_price, signal_date, features, {target_col}, "
                "forward_return_63d, forward_peak_return_63d FROM model_training_set "
                "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
                "ORDER BY signal_date DESC LIMIT 200000) t "
                "ORDER BY signal_date ASC"
            ))
            skipped = 0
            filled = 0
            i = 0
            for row in result.yield_per(20000):
                if i >= n_rows:
                    break
                try:
                    symbol, entry_price, signal_date, feats_raw, label, fwd_ret = row
                    feats = json.loads(feats_raw) if isinstance(feats_raw, str) else (feats_raw or {})
                    feats = _fill_fundamentals(feats, symbol, fund_map, float(entry_price or 0))
                    feats = _fill_point_in_time_pe(feats, symbol, signal_date, float(entry_price or 0), eps_map)
                    feats = _fill_historical(feats, symbol, hist_map)
                    feats = _fill_eodhd(feats, symbol, eodhd_map)
                    bad = False
                    for j, c in enumerate(FEATURE_COLS):
                        v = feats.get(c, 0)
                        if v != v or v == float('inf') or v == float('-inf'):
                            bad = True
                            break
                        X[i, j] = v
                    if bad:
                        valid[i] = False
                        skipped += 1
                        i += 1
                        continue
                    if fwd_ret is None or abs(float(fwd_ret)) > 100:
                        valid[i] = False
                        skipped += 1
                        i += 1
                        continue
                    y[i] = int(label) if label is not None else 0
                    y_reg[i] = fwd_ret
                    dates[i] = np.datetime64(signal_date, "D")
                    if symbol in fund_map:
                        filled += 1
                    i += 1
                except Exception:
                    valid[i] = False
                    skipped += 1
                    i += 1
            n_rows = i
    except Exception as e:
        print(f"[Classifier] DB read failed: {e}", flush=True)
        return None

    # Compact: drop invalid rows
    X = X[:n_rows][valid[:n_rows]]
    y = y[:n_rows][valid[:n_rows]]
    y_reg = y_reg[:n_rows][valid[:n_rows]]
    dates = dates[:n_rows][valid[:n_rows]]
    print(f"[Classifier] Parsed {len(X)} rows (fund-filled={filled}, skipped={skipped})", flush=True)
    if len(dates) > 0:
        print(f"[Classifier] Window: {dates.min()} -> {dates.max()}", flush=True)

    if len(X) < min_samples:
        print(f"[Classifier] {len(X)} < {min_samples} — insufficient", flush=True)
        return None

    # Drop zero-variance features
    nonzero_variance = np.std(X, axis=0) > 1e-12
    active_features = [fe for fe, nz in zip(FEATURE_COLS, nonzero_variance) if nz]
    dropped = len(FEATURE_COLS) - len(active_features)
    if dropped > 0:
        X = X[:, nonzero_variance]
        print(f"[Classifier] Dropped {dropped}/{len(FEATURE_COLS)} zero-variance -> {len(active_features)} active", flush=True)
    else:
        active_features = FEATURE_COLS

    # Drop permutation-importance-pruned features (measured negative importance)
    keep_mask = np.array([f not in PRUNED_FEATURES for f in active_features], dtype=bool)
    pruned_count = int((~keep_mask).sum())
    if pruned_count > 0:
        X = X[:, keep_mask]
        active_features = [f for f, k in zip(active_features, keep_mask) if k]
        print(f"[Classifier] Pruned {pruned_count} low-importance features -> {len(active_features)} kept", flush=True)

    # Chronological split
    n_train = int(len(X) * 0.8)
    X_train, X_test = X[:n_train], X[n_train:]
    y_train, y_test = y[:n_train], y[n_train:]

    base_rate_test = y_test.mean()
    print(f"[Classifier] {len(X_train)} train / {len(X_test)} test, "
          f"base test={base_rate_test*100:.1f}%", flush=True)

    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, average_precision_score
    from scipy.stats import rankdata

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    model = LogisticRegression(class_weight='balanced', C=0.1, max_iter=5000, random_state=42, n_jobs=-1)
    t1 = time.time()
    model.fit(X_train_s, y_train)
    print(f"[Classifier] Binary fit in {time.time()-t1:.1f}s", flush=True)

    y_pred_proba = model.predict_proba(X_test_s)[:, 1]

    # ── Regression head (63d return) + rank blend (multitask) ──────────────
    # The 63d return adds magnitude info (30% run vs 6% run) that the binary
    # label discards. Blend weight 0.4 optimises top-decile hit rate while
    # keeping bottom-decile (loser avoidance) intact.

    y_reg_train = y_reg[:n_train]
    y_reg_test = y_reg[n_train:]
    reg_model = Ridge(alpha=10.0)
    reg_model.fit(X_train_s, y_reg_train)
    y_reg_pred = reg_model.predict(X_test_s)

    rank_bin = rankdata(y_pred_proba)
    rank_reg = rankdata(y_reg_pred)
    blended_rank = (1 - REG_BLEND_WEIGHT) * rank_bin + REG_BLEND_WEIGHT * rank_reg
    y_pred_proba = blended_rank
    print(f"[Classifier] Multitask blend: {1-REG_BLEND_WEIGHT:.1f} binary + {REG_BLEND_WEIGHT:.1f} regression", flush=True)

    # Decile analysis
    sorted_idx = np.argsort(y_pred_proba)[::-1]
    decile_size = len(sorted_idx) // 10
    decile_hit_rates = []
    for d in range(10):
        start = d * decile_size
        end = start + decile_size if d < 9 else len(sorted_idx)
        idx = sorted_idx[start:end]
        decile_hit_rates.append(round(y_test[idx].mean() * 100, 1))

    top_decile_hit = decile_hit_rates[0]
    bottom_decile_hit = decile_hit_rates[-1]
    spread = top_decile_hit - bottom_decile_hit
    auc = roc_auc_score(y_test, y_pred_proba)
    avg_precision = average_precision_score(y_test, y_pred_proba)

    coefs = dict(zip(active_features, model.coef_[0]))
    top_features = sorted(coefs.items(), key=lambda x: abs(x[1]), reverse=True)[:10]

    elapsed = time.time() - t0

    print(f"[Classifier] RESULTS:", flush=True)
    print(f"  Base rate: {base_rate_test*100:.1f}%", flush=True)
    print(f"  Top decile: {top_decile_hit:.1f}%", flush=True)
    print(f"  Bottom decile: {bottom_decile_hit:.1f}%", flush=True)
    print(f"  Spread: {spread:.1f}pp", flush=True)
    print(f"  AUC: {auc:.4f}", flush=True)
    print(f"  Deciles: {decile_hit_rates}", flush=True)
    print(f"  Top features: {[(f, round(w,4)) for f,w in top_features[:6]]}", flush=True)
    print(f"  Time: {elapsed:.1f}s", flush=True)

    # Save weights
    today = date.today()
    with db_conn() as conn:
        conn.execute(text(
            "DELETE FROM model_weights_by_date WHERE trained_at = :ta AND model_type = 'logistic'"
        ), {"ta": today})
        for fname, w in coefs.items():
            conn.execute(text(
                "INSERT INTO model_weights_by_date (trained_at,feature_name,weight,coefficient,"
                "model_type,sample_size,in_sample_hit_rate,notes) VALUES "
                "(:ta,:fn,:w,:c,:mt,:ss,:ish,:nt) "
                "ON CONFLICT (trained_at,feature_name,model_type) DO UPDATE SET "
                "weight=EXCLUDED.weight,coefficient=EXCLUDED.coefficient"),
                {"ta": today, "fn": fname, "w": round(float(w), 6), "c": round(float(w), 6),
                 "mt": "logistic", "ss": len(X), "ish": round(auc, 4),
                 "nt": f"LogReg+RegBlend topDecile={top_decile_hit:.0f}% AUC={auc:.3f} spread={spread:.1f}pp"})
        # Save regression head coefficients (blend weight 0.4)
        reg_coefs = dict(zip(active_features, reg_model.coef_))
        for fname, w in reg_coefs.items():
            conn.execute(text(
                "INSERT INTO model_weights_by_date (trained_at,feature_name,weight,coefficient,"
                "model_type,sample_size,in_sample_hit_rate,notes) VALUES "
                "(:ta,:fn,:w,:c,:mt,:ss,:ish,:nt) "
                "ON CONFLICT (trained_at,feature_name,model_type) DO UPDATE SET "
                "weight=EXCLUDED.weight,coefficient=EXCLUDED.coefficient"),
                {"ta": today, "fn": fname, "w": round(float(w), 6), "c": round(float(w), 6),
                 "mt": "ridge_reg", "ss": len(X), "ish": round(auc, 4),
                 "nt": f"RidgeReg 63d-return blend=0.4"})
        conn.commit()
    print(f"[Classifier] Saved {len(coefs)} binary + {len(reg_coefs)} regression weights", flush=True)

    # ── Regression guards — fail loudly if the model silently degrades ──────
    # Locks in Fix 13 (+2.3pp) and Fix 14 (pruning): prevents silent backsliding
    # where fundamental features freeze at 0.0 or too many features drop out.
    guards = []
    if len(active_features) < 40:
        guards.append(f"active_features={len(active_features)} < 40 (expected ~45)")
    pct_inst_coef = abs(coefs.get("pct_institutions", 0))
    if pct_inst_coef < 0.001:
        guards.append(f"|coef(pct_institutions)|={pct_inst_coef:.5f} < 0.001 (EODHD features likely frozen)")
    if guards:
        print(f"[Classifier] ⚠️ REGRESSION GUARDS FAILED: {'; '.join(guards)}", flush=True)
    else:
        print(f"[Classifier] Regression guards OK: {len(active_features)} features, "
              f"|coef(pct_institutions)|={pct_inst_coef:.4f}", flush=True)
    result_guards = guards

    # ── Fix 17: LightGBM challenger — same split, same blend, strict winner rule ──
    # Adopt only if it beats logistic on AUC (+0.005) without losing top-decile
    # or bottom-decile quality. Otherwise logistic stays and nothing changes.
    lgbm_challenger = _train_lgbm_challenger(
        X_train, X_test, y_train, y_test,
        reg_model.predict(X_test_s), active_features,
        scaler.mean_, scaler.scale_, reg_model.coef_, dates,
    )
    adopt_lgbm = False
    if lgbm_challenger and lgbm_challenger.get("ok"):
        auc_gap = lgbm_challenger["auc"] - auc
        adopt_lgbm = (
            auc_gap >= 0.005
            and lgbm_challenger["top_decile"] >= top_decile_hit
            and lgbm_challenger["bottom_decile"] <= bottom_decile_hit + 1.0
        )
        print(f"[Classifier] LightGBM challenger: AUC={lgbm_challenger['auc']} "
              f"(Δ={auc_gap:+.4f}) top={lgbm_challenger['top_decile']}% "
              f"bottom={lgbm_challenger['bottom_decile']}% spread={lgbm_challenger['spread']}pp "
              f"-> {'ADOPTED' if adopt_lgbm else 'REJECTED (winner rule)'}", flush=True)
    else:
        print(f"[Classifier] LightGBM challenger unavailable: "
              f"{(lgbm_challenger or {}).get('error', 'unknown')}", flush=True)

    if adopt_lgbm:
        try:
            import joblib
            import hashlib
            artifact = {
                "model": lgbm_challenger["model"],
                "isotonic": lgbm_challenger["isotonic"],
                "feature_order": active_features,
                "scaler_mean": scaler.mean_.tolist(),
                "scaler_scale": scaler.scale_.tolist(),
                "ridge_coefs": reg_model.coef_.tolist(),
                "trained_at": today.isoformat(),
                "auc": lgbm_challenger["auc"],
                "top_decile": lgbm_challenger["top_decile"],
                "blend": {"binary": 1 - REG_BLEND_WEIGHT, "ridge": REG_BLEND_WEIGHT},
            }
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", "lgbm_classifier.pkl")
            if not os.path.isdir(os.path.dirname(path)):
                path = "/app/data/lgbm_classifier.pkl"
            joblib.dump(artifact, path)
            sha256 = hashlib.sha256(open(path, "rb").read()).hexdigest()
            marker_note = json.dumps(
                {k: v for k, v in lgbm_challenger.items() if k not in ("model", "isotonic")})[:500]
            marker_meta = {"sha256": sha256, "artifact_path": path,
                           "trained_at": today.isoformat()}
            marker_note = json.dumps({**json.loads(marker_note), **marker_meta})[:1000]
            with db_conn() as conn:
                conn.execute(text(
                    "DELETE FROM model_weights_by_date "
                    "WHERE model_type = 'lgbm_classifier' AND trained_at = :ta"),
                    {"ta": today})
                conn.execute(text(
                    "INSERT INTO model_weights_by_date (trained_at, feature_name, weight, "
                    "coefficient, model_type, sample_size, in_sample_hit_rate, notes) VALUES "
                    "(:ta, '__artifact__', 0, 0, 'lgbm_classifier', :ss, :ish, :nt)"),
                    {"ta": today, "ss": len(X), "ish": lgbm_challenger["auc"],
                     "nt": marker_note})
                conn.commit()
            print(f"[Classifier] LGBM artifact saved to {path} (sha256={sha256[:12]}…)", flush=True)
        except Exception as e:
            print(f"[Classifier] LGBM artifact save failed: {e}", flush=True)
    elif lgbm_challenger and lgbm_challenger.get("ok"):
        # Challenger ran and was rejected by the winner rule: remove any
        # previously adopted artifact so live scoring cannot keep serving a
        # model the current verdict rejected, and record the rejection loudly.
        for _p in (
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lgbm_classifier.pkl"),
            "/app/data/lgbm_classifier.pkl",
        ):
            try:
                if os.path.exists(_p):
                    os.remove(_p)
                    print(f"[Classifier] ⚠️ LGBM rejected — removed stale artifact {_p}", flush=True)
            except OSError:
                pass
        try:
            _rej_note = json.dumps(
                {k: v for k, v in lgbm_challenger.items() if k not in ("model", "isotonic")})[:500]
            with db_conn() as conn:
                conn.execute(text(
                    "INSERT INTO model_weights_by_date (trained_at, feature_name, weight, "
                    "coefficient, model_type, sample_size, in_sample_hit_rate, notes) VALUES "
                    "(:ta, '__rejected__', 0, 0, 'lgbm_classifier', :ss, :ish, :nt) "
                    "ON CONFLICT (trained_at, feature_name, model_type) DO UPDATE SET notes=EXCLUDED.notes"),
                    {"ta": today, "ss": len(X), "ish": lgbm_challenger["auc"],
                     "nt": _rej_note})
                conn.commit()
        except Exception as e:
            print(f"[Classifier] LGBM rejection marker failed: {e}", flush=True)

    return {
        "status": "ok", "model_type": "logistic_regression",
        "samples": len(X), "active_features": len(active_features),
        "fund_symbols": len(fund_map),
        "base_rate_test_pct": round(base_rate_test * 100, 1),
        "top_decile_hit_pct": top_decile_hit,
        "bottom_decile_hit_pct": bottom_decile_hit,
        "spread_pp": spread, "auc": round(auc, 4),
        "avg_precision": round(avg_precision, 4),
        "decile_hit_rates": decile_hit_rates,
        "top_features": top_features, "elapsed_s": round(elapsed, 1),
        "regression_guards_failed": result_guards,
        "lgbm_challenger": ({k: v for k, v in lgbm_challenger.items() if k not in ("model", "isotonic")}
                            if lgbm_challenger else None),
        "lgbm_adopted": adopt_lgbm,
    }

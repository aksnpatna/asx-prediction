"""
SMSF Model Training — LogisticRegression classifier with decile evaluation.

Fills fundamental features in-memory from the latest snapshot per symbol
(avoids the 3.3M-row SQL UPDATE timeout). Trains on path-aware labels.
"""

import json, time, numpy as np
from datetime import date
from typing import Dict, List, Optional


FUNDAMENTAL_FEATURES = [
    "fund_pe_inv", "fund_forward_pe_inv", "fund_market_cap_log",
    "fund_div_yield", "fund_analyst_upside", "fund_analyst_rec_score",
    "fund_earnings_growth", "fund_revenue_growth", "fund_beta",
    "fund_pct_from_52w_high",
]


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


def train_classifier(target_col: str = "hit_8pct_before_m8pct",
                     min_samples: int = 5000) -> Optional[dict]:
    """Train LogisticRegression on path-aware labels with fundamentals filled in-memory."""
    from sqlalchemy import text
    from model_training import FEATURE_COLS, db_conn

    print(f"[Classifier] Loading {target_col} data with fundamentals...", flush=True)
    t0 = time.time()

    try:
        with db_conn() as conn:
            rows = conn.execute(text(
                f"SELECT symbol, entry_price, features, CAST({target_col} AS INTEGER) FROM model_training_set "
                "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
                "ORDER BY signal_date ASC LIMIT 100000"
            )).fetchall()
    except Exception as e:
        print(f"[Classifier] DB read failed: {e}", flush=True)
        return None

    if len(rows) < min_samples:
        print(f"[Classifier] {len(rows)} < {min_samples} — insufficient", flush=True)
        return None

    # Load latest fundamentals per symbol (in-memory, fast)
    fund_map = _load_latest_fundamentals(db_conn)
    print(f"[Fund] Loaded {len(fund_map)} symbol fundamentals", flush=True)

    # Parse features + fill fundamentals
    X_list, y_list = [], []
    skipped = 0
    filled = 0
    for row in rows:
        try:
            symbol, entry_price, feats_raw, label = row
            feats = json.loads(feats_raw) if isinstance(feats_raw, str) else (feats_raw or {})
            feats = _fill_fundamentals(feats, symbol, fund_map, float(entry_price or 0))
            x_row = [float(feats.get(c, 0)) for c in FEATURE_COLS]
            if any(np.isnan(v) or np.isinf(v) for v in x_row):
                skipped += 1; continue
            X_list.append(x_row)
            y_list.append(int(label) if label is not None else 0)
            if symbol in fund_map:
                filled += 1
        except Exception:
            skipped += 1
    print(f"[Classifier] Parsed {len(X_list)} rows (fund-filled={filled}, skipped={skipped})", flush=True)

    X = np.array(X_list)
    y = np.array(y_list)

    # Drop zero-variance features
    nonzero_variance = np.std(X, axis=0) > 1e-12
    active_features = [fe for fe, nz in zip(FEATURE_COLS, nonzero_variance) if nz]
    dropped = len(FEATURE_COLS) - len(active_features)
    if dropped > 0:
        X = X[:, nonzero_variance]
        print(f"[Classifier] Dropped {dropped}/{len(FEATURE_COLS)} zero-variance -> {len(active_features)} active", flush=True)
    else:
        active_features = FEATURE_COLS

    # Chronological split
    n_train = int(len(X) * 0.8)
    X_train, X_test = X[:n_train], X[n_train:]
    y_train, y_test = y[:n_train], y[n_train:]

    base_rate_test = y_test.mean()
    print(f"[Classifier] {len(X_train)} train / {len(X_test)} test, "
          f"base test={base_rate_test*100:.1f}%", flush=True)

    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, average_precision_score

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    model = LogisticRegression(class_weight='balanced', C=0.1, max_iter=5000, random_state=42, n_jobs=-1)
    t1 = time.time()
    model.fit(X_train_s, y_train)
    print(f"[Classifier] Fit in {time.time()-t1:.1f}s", flush=True)

    y_pred_proba = model.predict_proba(X_test_s)[:, 1]

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
                "ON CONFLICT (trained_at,feature_name) DO UPDATE SET "
                "weight=EXCLUDED.weight,coefficient=EXCLUDED.coefficient,model_type='logistic'"),
                {"ta": today, "fn": fname, "w": round(float(w), 6), "c": round(float(w), 6),
                 "mt": "logistic", "ss": len(X), "ish": round(auc, 4),
                 "nt": f"LogReg topDecile={top_decile_hit:.0f}% AUC={auc:.3f} spread={spread:.1f}pp"})
        conn.commit()
    print(f"[Classifier] Saved {len(coefs)} weights", flush=True)

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
    }

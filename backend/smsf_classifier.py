"""
SMSF Model Training — Classification with Decile-Based Evaluation.

Replaces the old regression ensemble with a LogisticRegression classifier.
Reports per-decile hit rates to prove the model has real selection skill.
Auto-drops zero-variance features. Fast (<2 min on 100K rows).
"""

import json, time, numpy as np
from datetime import date
from typing import Dict, List, Optional


def train_classifier(target_col: str = "hit_8pct_before_m8pct", min_samples: int = 5000) -> Optional[dict]:
    """Train a LogisticRegression classifier on path-aware labels.

    Returns per-decile hit rates, feature importance, AUC, and calibration stats.
    Stores weights in model_weights_by_date.
    """
    from sqlalchemy import text
    from model_training import FEATURE_COLS, db_conn

    print(f"[Classifier] Loading {target_col} data...", flush=True)
    t0 = time.time()

    try:
        with db_conn() as conn:
            rows = conn.execute(text(
                f"SELECT features, CAST({target_col} AS INTEGER) FROM model_training_set "
                "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
                "ORDER BY signal_date ASC LIMIT 100000"
            )).fetchall()
    except Exception as e:
        print(f"[Classifier] DB read failed: {e}", flush=True)
        return None

    if len(rows) < min_samples:
        print(f"[Classifier] {len(rows)} < {min_samples} — insufficient", flush=True)
        return None

    # Parse features
    X_list, y_list = [], []
    skipped = 0
    for row in rows:
        try:
            feats = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
            x_row = [float(feats.get(c, 0)) for c in FEATURE_COLS]
            if any(np.isnan(v) or np.isinf(v) for v in x_row):
                skipped += 1; continue
            X_list.append(x_row)
            y_list.append(int(row[1]) if row[1] is not None else 0)
        except Exception:
            skipped += 1
    print(f"[Classifier] Parsed {len(X_list)} valid rows (skipped {skipped})", flush=True)

    X = np.array(X_list)
    y = np.array(y_list)

    # Drop zero-variance features
    nonzero_variance = np.std(X, axis=0) > 1e-12
    active_features = [fe for fe, nz in zip(FEATURE_COLS, nonzero_variance) if nz]
    dropped = len(FEATURE_COLS) - len(active_features)
    if dropped > 0:
        X = X[:, nonzero_variance]
        print(f"[Classifier] Dropped {dropped}/{len(FEATURE_COLS)} zero-variance features "
              f"-> {len(active_features)} active", flush=True)
    else:
        active_features = FEATURE_COLS

    # Chronological split: 80% train / 20% test
    n_train = int(len(X) * 0.8)
    X_train, X_test = X[:n_train], X[n_train:]
    y_train, y_test = y[:n_train], y[n_train:]

    base_rate_train = y_train.mean()
    base_rate_test = y_test.mean()
    print(f"[Classifier] {len(X_train)} train / {len(X_test)} test, "
          f"base_rate train={base_rate_train*100:.1f}% test={base_rate_test*100:.1f}%", flush=True)

    # Train LogisticRegression with balanced class weights
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, precision_recall_curve, average_precision_score

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    model = LogisticRegression(
        class_weight='balanced',
        C=0.1,
        max_iter=5000,
        random_state=42,
        n_jobs=-1,
    )
    t1 = time.time()
    model.fit(X_train_s, y_train)
    fit_time = time.time() - t1
    print(f"[Classifier] Fit in {fit_time:.1f}s, C={model.C}", flush=True)

    # Predict probabilities
    y_pred_proba = model.predict_proba(X_test_s)[:, 1]

    # ── Decile-based hit rate ─────────────────────────────────────────────
    # Sort predictions, split into deciles, compute hit rate per decile
    sorted_idx = np.argsort(y_pred_proba)[::-1]
    decile_size = len(sorted_idx) // 10
    decile_hit_rates = []
    decile_counts = []
    for d in range(10):
        start = d * decile_size
        end = start + decile_size if d < 9 else len(sorted_idx)
        idx = sorted_idx[start:end]
        hit_rate = y_test[idx].mean() * 100
        decile_hit_rates.append(round(hit_rate, 1))
        decile_counts.append(end - start)

    top_decile_hit = decile_hit_rates[0]
    bottom_decile_hit = decile_hit_rates[-1]
    spread = top_decile_hit - bottom_decile_hit

    # ── Additional metrics ─────────────────────────────────────────────────
    auc = roc_auc_score(y_test, y_pred_proba)
    avg_precision = average_precision_score(y_test, y_pred_proba)

    # ── Feature importance ─────────────────────────────────────────────────
    coefs = dict(zip(active_features, model.coef_[0]))
    top_features = sorted(coefs.items(), key=lambda x: abs(x[1]), reverse=True)[:10]

    # ── Risk calibration ──────────────────────────────────────────────────
    # In top decile: what % of predictions hit?
    # In bottom decile: what % miss?
    from sklearn.calibration import calibration_curve
    prob_true, prob_pred = calibration_curve(y_test, y_pred_proba, n_bins=10)
    calibration = [{"expected": round(float(p)*100,1), "actual": round(float(t)*100,1)}
                   for p, t in zip(prob_pred, prob_true)]

    elapsed = time.time() - t0

    # ── Report ─────────────────────────────────────────────────────────────
    print(f"[Classifier] RESULTS:", flush=True)
    print(f"  Base rate: {base_rate_test*100:.1f}% (random pick)", flush=True)
    print(f"  Top decile hit rate: {top_decile_hit:.1f}%", flush=True)
    print(f"  Bottom decile hit rate: {bottom_decile_hit:.1f}%", flush=True)
    print(f"  Spread: {spread:.1f}pp (skill = {spread/max(base_rate_test*100,1):.1f}x base rate)", flush=True)
    print(f"  AUC: {auc:.4f}", flush=True)
    print(f"  Avg Precision: {avg_precision:.4f}", flush=True)
    print(f"  Deciles: {decile_hit_rates}", flush=True)
    print(f"  Top features: {[(f, round(w,4)) for f,w in top_features[:5]]}", flush=True)
    print(f"  Time: {elapsed:.1f}s", flush=True)

    # ── Save weights ───────────────────────────────────────────────────────
    today = date.today()
    baseline = base_rate_train * 100
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
                 "nt": f"LogReg topDecile={top_decile_hit:.0f}% AUC={auc:.3f}"})
        conn.commit()
    print(f"[Classifier] Weights saved: {len(coefs)} features", flush=True)

    return {
        "status": "ok",
        "model_type": "logistic_regression",
        "samples": len(X),
        "active_features": len(active_features),
        "base_rate_train_pct": round(base_rate_train * 100, 1),
        "base_rate_test_pct": round(base_rate_test * 100, 1),
        "top_decile_hit_pct": top_decile_hit,
        "bottom_decile_hit_pct": bottom_decile_hit,
        "spread_pp": spread,
        "auc": round(auc, 4),
        "avg_precision": round(avg_precision, 4),
        "decile_hit_rates": decile_hit_rates,
        "top_features": top_features,
        "calibration": calibration,
        "elapsed_s": round(elapsed, 1),
    }

"""
Model A/B experiment — read-only, mirrors smsf_classifier.train_classifier.

Run inside the backend container:
    docker cp backend/experiments/model_ab_test.py asx-backend:/tmp/ && \
    docker exec asx-backend python3 /tmp/model_ab_test.py

Or locally with DB access (DATABASE_URL env) from the backend dir.

NOTE: this experiment predates Fix 20 (training window). It loads
ORDER BY signal_date ASC LIMIT 300000 — i.e. the 2015-2016 window. Its
A/B conclusions (LGBM > logistic, two-label ensemble rejected) still held
on the modern window, but the absolute metrics here are 2015-2016 numbers.
For modern-window numbers use regime_ab_test.py.

Compares on the identical data pipeline (300K rows, same fills, same pruning,
same 80/20 chronological split, same 0.6/0.4 rank blend with the Ridge head):

  A. LogisticRegression (production baseline, Fix 16)
  B. LightGBM classifier head
  C. LightGBM + recency sample weights (half-life 1yr)
  D. Two-label rank ensemble: 0.7*rank(LGBM concurrent) + 0.3*rank(LGBM first-touch)
     — Fix 16 decision says score on concurrent, report first-touch. D tests
       whether first-touch probability adds ranking/economic value.

Every variant is evaluated on BOTH labels:
  - concurrent hit_8pct_before_m8pct (ranking quality)
  - first-touch hit_8pct_first_touch (honest economics: EV = 0.08*hit - 0.08*miss)

No DB writes. Results also saved to /tmp/ab_results.json.
"""
import os, sys, json, time
import numpy as np

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import create_engine, text
from smsf_classifier import (
    _load_latest_fundamentals, _load_historical_fundamentals,
    _load_eodhd_features, _load_eps_history, _fill_fundamentals,
    _fill_point_in_time_pe, _fill_historical, _fill_eodhd,
    PRUNED_FEATURES,
)
from model_training import FEATURE_COLS

DATABASE_URL = os.getenv("DATABASE_URL",
                         "postgresql+psycopg2://asx_user:asx_password@db:5432/asx")
engine = create_engine(DATABASE_URL, pool_size=3, max_overflow=3)


def db_conn():
    return engine.connect()


def load_data():
    t0 = time.time()
    fund_map = _load_latest_fundamentals(db_conn)
    hist_map = _load_historical_fundamentals(db_conn)
    eodhd_map = _load_eodhd_features(db_conn)
    eps_map = _load_eps_history(db_conn)
    print(f"[AB] aux maps: {len(fund_map)} snap + {len(hist_map)} hist + "
          f"{len(eodhd_map)} eodhd + {len(eps_map)} eps ({time.time()-t0:.1f}s)", flush=True)

    MAX_ROWS = 300000
    X = np.zeros((MAX_ROWS, len(FEATURE_COLS)), dtype=np.float32)
    y_c = np.zeros(MAX_ROWS, dtype=np.int8)
    y_ft = np.zeros(MAX_ROWS, dtype=np.int8)
    y_reg = np.zeros(MAX_ROWS, dtype=np.float32)
    valid = np.ones(MAX_ROWS, dtype=bool)
    i = 0
    with db_conn() as conn:
        result = conn.execution_options(
            stream_results=True, max_row_buffer=20000).execute(text(
            "SELECT symbol, entry_price, signal_date, features, "
            "CAST(hit_8pct_before_m8pct AS INTEGER), "
            "CAST(hit_8pct_first_touch AS INTEGER), "
            "COALESCE(forward_return_63d, 0) "
            "FROM model_training_set "
            "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
            "ORDER BY signal_date ASC LIMIT 300000"
        ))
        for row in result.yield_per(20000):
            if i >= MAX_ROWS:
                break
            try:
                symbol, entry_price, signal_date, feats_raw, lab_c, lab_ft, fwd = row
                feats = json.loads(feats_raw) if isinstance(feats_raw, str) else (feats_raw or {})
                feats = _fill_fundamentals(feats, symbol, fund_map, float(entry_price or 0))
                feats = _fill_point_in_time_pe(feats, symbol, signal_date,
                                               float(entry_price or 0), eps_map)
                feats = _fill_historical(feats, symbol, hist_map)
                feats = _fill_eodhd(feats, symbol, eodhd_map)
                bad = False
                for j, c in enumerate(FEATURE_COLS):
                    v = feats.get(c, 0)
                    if v != v or v == float("inf") or v == float("-inf"):
                        bad = True
                        break
                    X[i, j] = v
                if bad or fwd is None or abs(float(fwd)) > 100:
                    valid[i] = False
                else:
                    y_c[i] = int(lab_c) if lab_c is not None else 0
                    y_ft[i] = int(lab_ft) if lab_ft is not None else 0
                    y_reg[i] = float(fwd)
                i += 1
            except Exception:
                valid[i] = False
                i += 1
    X = X[:i][valid[:i]]
    y_c = y_c[:i][valid[:i]]
    y_ft = y_ft[:i][valid[:i]]
    y_reg = y_reg[:i][valid[:i]]
    print(f"[AB] rows parsed: {len(X)} ({time.time()-t0:.1f}s)", flush=True)
    return X, y_c, y_ft, y_reg


def deciles(scores, y):
    order = np.argsort(scores)[::-1]
    size = len(order) // 10
    out = []
    for d in range(10):
        lo = d * size
        hi = lo + size if d < 9 else len(order)
        out.append(round(float(y[order[lo:hi]].mean()) * 100, 1))
    return out


def main():
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, average_precision_score
    from scipy.stats import rankdata

    X, y_c, y_ft, y_reg = load_data()

    # Same preprocessing as train_classifier
    nz = np.std(X, axis=0) > 1e-12
    X = X[:, nz]
    active = [f for f, k in zip(FEATURE_COLS, nz) if k]
    keep = np.array([f not in PRUNED_FEATURES for f in active], dtype=bool)
    X = X[:, keep]
    active = [f for f, k in zip(active, keep) if k]
    print(f"[AB] active features after pruning: {len(active)}", flush=True)

    n_train = int(len(X) * 0.8)
    X_tr, X_te = X[:n_train], X[n_train:]
    yc_tr, yc_te = y_c[:n_train], y_c[n_train:]
    yft_tr, yft_te = y_ft[:n_train], y_ft[n_train:]
    yr_tr, yr_te = y_reg[:n_train], y_reg[n_train:]
    print(f"[AB] train={len(X_tr)} test={len(X_te)} | base conc={yc_te.mean()*100:.1f}% "
          f"base ft={yft_te.mean()*100:.1f}%", flush=True)

    scaler = StandardScaler().fit(X_tr)
    X_tr_s, X_te_s = scaler.transform(X_tr), scaler.transform(X_te)

    # Recency weights (mirror fit_model_weights: half-life 1yr)
    n_all = len(X)
    positions = np.arange(n_all)[::-1]
    days_old = positions / n_all * (9 * 365)
    w = np.power(0.5, days_old / 365.0)
    w = w / w.mean()
    w_tr = w[:n_train]

    # Shared Ridge regression head (production blend partner)
    ridge = Ridge(alpha=10.0).fit(X_tr_s, yr_tr)
    rank_reg_te = rankdata(ridge.predict(X_te_s))

    def blend(proba_te):
        return 0.6 * rankdata(proba_te) + 0.4 * rank_reg_te

    results = {}

    # ── A. LogisticRegression (production baseline) ────────────────────────
    t0 = time.time()
    lr = LogisticRegression(class_weight="balanced", C=0.1, max_iter=5000,
                            random_state=42, n_jobs=-1)
    lr.fit(X_tr_s, yc_tr)
    a_score = blend(lr.predict_proba(X_te_s)[:, 1])
    results["A_logistic"] = {
        "auc_c": roc_auc_score(yc_te, a_score),
        "ap_c": average_precision_score(yc_te, a_score),
        "deciles_c": deciles(a_score, yc_te),
        "deciles_ft": deciles(a_score, yft_te),
        "secs": round(time.time() - t0, 1),
    }

    # ── LightGBM head factory ──────────────────────────────────────────────
    import lightgbm as lgb

    def lgbm_head(Xa, ya, weights=None):
        t0 = time.time()
        m = lgb.LGBMClassifier(
            objective="binary", n_estimators=600, learning_rate=0.03,
            num_leaves=63, max_depth=6, min_child_samples=50,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            reg_alpha=0.3, reg_lambda=2.0, class_weight="balanced",
            random_state=42, n_jobs=-1, verbose=-1,
        )
        cut = int(len(Xa) * 0.9)
        fit_kw = dict(eval_set=[(Xa[cut:], ya[cut:])],
                      eval_metric="auc",
                      callbacks=[lgb.early_stopping(30, verbose=False)])
        if weights is not None:
            fit_kw["sample_weight"] = weights[:cut]
        m.fit(Xa[:cut], ya[:cut], **fit_kw)
        return m, time.time() - t0, getattr(m, "best_iteration_", None)

    # ── B. LightGBM concurrent head ────────────────────────────────────────
    mB, sB, itB = lgbm_head(X_tr, yc_tr)
    b_score = blend(mB.predict_proba(X_te)[:, 1])
    results["B_lgbm"] = {
        "auc_c": roc_auc_score(yc_te, b_score),
        "ap_c": average_precision_score(yc_te, b_score),
        "deciles_c": deciles(b_score, yc_te),
        "deciles_ft": deciles(b_score, yft_te),
        "secs": round(sB, 1), "best_iter": itB,
    }

    # ── C. LightGBM + recency weights ──────────────────────────────────────
    mC, sC, itC = lgbm_head(X_tr, yc_tr, weights=w_tr)
    c_score = blend(mC.predict_proba(X_te)[:, 1])
    results["C_lgbm_weighted"] = {
        "auc_c": roc_auc_score(yc_te, c_score),
        "ap_c": average_precision_score(yc_te, c_score),
        "deciles_c": deciles(c_score, yc_te),
        "deciles_ft": deciles(c_score, yft_te),
        "secs": round(sC, 1), "best_iter": itC,
    }

    # ── First-touch LightGBM head (for the two-label ensemble) ─────────────
    mFT, sFT, itFT = lgbm_head(X_tr, yft_tr)
    ft_proba = mFT.predict_proba(X_te)[:, 1]

    # ── D. Two-label rank ensemble (0.7 concurrent + 0.3 first-touch) ──────
    d_score = 0.7 * rankdata(mB.predict_proba(X_te)[:, 1]) + 0.3 * rankdata(ft_proba)
    d_score = 0.6 * rankdata(d_score) + 0.4 * rank_reg_te  # then reg blend
    results["D_lgbm_two_label"] = {
        "auc_c": roc_auc_score(yc_te, d_score),
        "ap_c": average_precision_score(yc_te, d_score),
        "deciles_c": deciles(d_score, yc_te),
        "deciles_ft": deciles(d_score, yft_te),
        "secs": round(sFT, 1), "best_iter": itFT,
    }

    # ── Print table ────────────────────────────────────────────────────────
    print("\n=== RESULTS (chrono 80/20, 300K, blend 0.6bin+0.4reg) ===", flush=True)
    header = f"{'variant':22} {'AUC_c':>7} {'AP_c':>6} {'topD_c':>7} {'botD_c':>7} {'spr_c':>6} {'topD_ft':>7} {'EV_ft%':>6} {'secs':>6}"
    print(header, flush=True)
    for name, r in results.items():
        d_c = r["deciles_c"]
        d_ft = r["deciles_ft"]
        top_ft = d_ft[0]
        ev_ft = 0.08 * top_ft - 0.08 * (100 - top_ft)
        print(f"{name:22} {r['auc_c']:7.4f} {r['ap_c']:6.4f} {d_c[0]:7.1f} "
              f"{d_c[-1]:7.1f} {d_c[0]-d_c[-1]:6.1f} {top_ft:7.1f} {ev_ft:6.2f} "
              f"{r['secs']:6.1f}", flush=True)
        print(f"    deciles_c: {d_c}", flush=True)
        print(f"    deciles_ft: {d_ft}", flush=True)

    with open("/tmp/ab_results.json", "w") as f:
        json.dump({"active_features": active, "n_train": len(X_tr),
                   "n_test": len(X_te), "base_c": float(yc_te.mean()),
                   "base_ft": float(yft_te.mean()), "results": results}, f, indent=2)
    print("\nSaved /tmp/ab_results.json", flush=True)


if __name__ == "__main__":
    main()

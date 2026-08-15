"""
Consensus filter experiment (T1-C) — production pipeline, modern window.

Measures whether requiring multi-model agreement improves precision beyond
LGBM-only ranking. Trains LGBM (production head), Ridge (63d return), and
RandomForest on the same 200K-recent split, then evaluates candidate rules on
the test set:

  R0: LGBM proba top decile (production baseline)
  R1: LGBM top-decile AND (ridge OR rf in top-quartile)
  R2: LGBM top-decile AND (ridge AND rf in top-quartile)
  R3: all three top-quartile (agreement only)

Metrics per rule: concurrent hit rate, first-touch hit rate, EV/ft, coverage.

Run inside the container:
    docker cp backend/experiments/consensus_ab.py asx-backend:/tmp/ && \
    docker exec asx-backend python3 /tmp/consensus_ab.py

No DB writes. Saves /tmp/consensus_results.json
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


def load_recent(n_rows):
    fund_map = _load_latest_fundamentals(db_conn)
    hist_map = _load_historical_fundamentals(db_conn)
    eodhd_map = _load_eodhd_features(db_conn)
    eps_map = _load_eps_history(db_conn)
    X, y, y_ft, y_reg, dates = [], [], [], [], []
    with db_conn() as conn:
        result = conn.execution_options(
            stream_results=True, max_row_buffer=20000).execute(text(
            "SELECT symbol, entry_price, signal_date, features, "
            "CAST(hit_8pct_before_m8pct AS INTEGER), "
            "CAST(hit_8pct_first_touch AS INTEGER), "
            "COALESCE(forward_return_63d, 0) FROM ("
            "SELECT symbol, entry_price, signal_date, features, "
            "hit_8pct_before_m8pct, hit_8pct_first_touch, forward_return_63d, "
            "forward_peak_return_63d "
            "FROM model_training_set "
            "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
            "ORDER BY signal_date DESC LIMIT :n) t ORDER BY signal_date ASC"
        ), {"n": n_rows})
        for row in result.yield_per(20000):
            try:
                symbol, entry_price, signal_date, feats_raw, lab, lab_ft, fwd = row
                feats = json.loads(feats_raw) if isinstance(feats_raw, str) else (feats_raw or {})
                feats = _fill_fundamentals(feats, symbol, fund_map, float(entry_price or 0))
                feats = _fill_point_in_time_pe(feats, symbol, signal_date,
                                               float(entry_price or 0), eps_map)
                feats = _fill_historical(feats, symbol, hist_map)
                feats = _fill_eodhd(feats, symbol, eodhd_map)
                x = np.array([feats.get(c, 0) for c in FEATURE_COLS], dtype=np.float32)
                if not np.isfinite(x).all() or fwd is None or abs(float(fwd)) > 100:
                    continue
                X.append(x)
                y.append(int(lab) if lab is not None else 0)
                y_ft.append(int(lab_ft) if lab_ft is not None else 0)
                y_reg.append(float(fwd))
                dates.append(signal_date)
            except Exception:
                continue
    X = np.asarray(X, dtype=np.float32)
    return X, np.asarray(y, dtype=np.int8), np.asarray(y_ft, dtype=np.int8), \
        np.asarray(y_reg, dtype=np.float32), np.array(dates, dtype="datetime64[D]")


def main():
    import lightgbm as lgb
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    from scipy.stats import rankdata

    X, y, y_ft, y_reg, dates = load_recent(200000)
    nz = np.std(X, axis=0) > 1e-12
    X = X[:, nz]
    active = [f for f, k in zip(FEATURE_COLS, nz) if k]
    keep = np.array([f not in PRUNED_FEATURES for f in active], dtype=bool)
    X = X[:, keep]
    active = [f for f, k in zip(active, keep) if k]

    n_train = int(len(X) * 0.8)
    X_tr, X_te = X[:n_train], X[n_train:]
    y_tr, y_te = y[:n_train], y[n_train:]
    yft_te = y_ft[n_train:]
    yr_tr, yr_te = y_reg[:n_train], y_reg[n_train:]
    d_tr = dates[:n_train]

    days_old = (d_tr.max() - d_tr).astype(float)
    w = np.power(0.5, days_old / 365.0)
    w = w / w.mean()

    t0 = time.time()
    m = lgb.LGBMClassifier(
        objective="binary", n_estimators=600, learning_rate=0.03,
        num_leaves=63, max_depth=6, min_child_samples=50,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
        reg_alpha=0.3, reg_lambda=2.0, class_weight="balanced",
        random_state=42, n_jobs=-1, verbose=-1,
    )
    cut = int(len(X_tr) * 0.9)
    m.fit(X_tr[:cut], y_tr[:cut], sample_weight=w[:cut],
          eval_set=[(X_tr[cut:], y_tr[cut:])], eval_metric="auc",
          callbacks=[lgb.early_stopping(30, verbose=False)])
    proba = m.predict_proba(X_te)[:, 1]
    print(f"[Consensus] LGBM fit {time.time()-t0:.0f}s, AUC={roc_auc_score(y_te, proba):.4f}", flush=True)

    t0 = time.time()
    scaler = StandardScaler().fit(X_tr)
    ridge = Ridge(alpha=10.0).fit(scaler.transform(X_tr), yr_tr)
    ridge_s = ridge.predict(scaler.transform(X_te))
    print(f"[Consensus] Ridge fit {time.time()-t0:.0f}s", flush=True)

    t0 = time.time()
    rf = RandomForestClassifier(
        n_estimators=100, max_depth=12, min_samples_leaf=50,
        class_weight="balanced", random_state=42, n_jobs=-1,
    )
    rf.fit(X_tr, y_tr)
    rf_s = rf.predict_proba(X_te)[:, 1]
    print(f"[Consensus] RF fit {time.time()-t0:.0f}s", flush=True)

    n_te = len(y_te)
    top_dec = np.zeros(n_te, dtype=bool)
    order = np.argsort(proba)[::-1][: n_te // 10]
    top_dec[order] = True

    r_q75 = rankdata(ridge_s) >= 0.75 * n_te
    rf_q75 = rankdata(rf_s) >= 0.75 * n_te
    all_q75 = rankdata(proba) >= 0.75 * n_te

    rules = {
        "R0_lgbm_top_decile": top_dec,
        "R1_lgbm_dec_AND_ridgeORrf_q75": top_dec & (r_q75 | rf_q75),
        "R2_lgbm_dec_AND_ridgeANDrf_q75": top_dec & (r_q75 & rf_q75),
        "R3_all3_q75": all_q75 & r_q75 & rf_q75,
    }
    results = {}
    for name, mask in rules.items():
        n_sel = int(mask.sum())
        hit_c = float(y_te[mask].mean() * 100) if n_sel else 0.0
        hit_ft = float(yft_te[mask].mean() * 100) if n_sel else 0.0
        ev = 0.08 * hit_ft - 0.08 * (100 - hit_ft)
        results[name] = {
            "n": n_sel,
            "hit_concurrent_pct": round(hit_c, 1),
            "hit_firsttouch_pct": round(hit_ft, 1),
            "ev_ft_pct": round(ev, 2),
        }
        print(f"  {name:36} n={n_sel:>6} conc={hit_c:>5.1f}% ft={hit_ft:>5.1f}% EV={ev:+.2f}%",
              flush=True)

    print(f"  base conc={y_te.mean()*100:.1f}% ft={yft_te.mean()*100:.1f}%", flush=True)
    with open("/tmp/consensus_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("saved /tmp/consensus_results.json", flush=True)


if __name__ == "__main__":
    main()

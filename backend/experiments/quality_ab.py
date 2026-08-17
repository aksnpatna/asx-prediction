"""
Quality-check experiment (v5): focal reweighting, label smoothing, seed ensemble.

Production baseline on the same 200K adjacent split (V0: AUC ~0.746, top ~58.7%).

Variants:
  Q1: focal-style reweighting (two-pass: fit once, downweight easy samples γ=2, refit)
  Q2: label smoothing (y in {0.05, 0.95})
  Q3: 3-seed proba ensemble (average of random_state 42/7/2026)
  Q4: Q1 + Q3 combined

Run inside the container:
    docker cp backend/experiments/quality_ab.py asx-backend:/tmp/ && \
    docker exec asx-backend python3 /tmp/quality_ab.py

No DB writes. Saves /tmp/quality_ab_results.json
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


def load():
    t0 = time.time()
    fund_map = _load_latest_fundamentals(db_conn)
    hist_map = _load_historical_fundamentals(db_conn)
    eodhd_map = _load_eodhd_features(db_conn)
    eps_map = _load_eps_history(db_conn)
    X, y, dates = [], [], []
    with db_conn() as conn:
        result = conn.execution_options(
            stream_results=True, max_row_buffer=20000).execute(text(
            "SELECT symbol, entry_price, signal_date, features, "
            "CAST(hit_8pct_before_m8pct AS INTEGER), "
            "COALESCE(forward_return_63d, 0) FROM ("
            "SELECT symbol, entry_price, signal_date, features, "
            "hit_8pct_before_m8pct, forward_return_63d, forward_peak_return_63d "
            "FROM model_training_set "
            "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
            "ORDER BY signal_date DESC LIMIT 200000) t ORDER BY signal_date ASC"
        ))
        for row in result.yield_per(20000):
            try:
                symbol, entry_price, signal_date, feats_raw, lab, fwd = row
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
                dates.append(signal_date)
            except Exception:
                continue
    X = np.asarray(X, dtype=np.float32)
    nz = np.std(X, axis=0) > 1e-12
    X = X[:, nz]
    active = [f for f, k in zip(FEATURE_COLS, nz) if k]
    keep = np.array([f not in PRUNED_FEATURES for f in active], dtype=bool)
    X = X[:, keep]
    print(f"[load] {len(X)} rows x {X.shape[1]} feats in {time.time()-t0:.0f}s", flush=True)
    return X, np.asarray(y, dtype=np.int8), np.array(dates, dtype="datetime64[D]")


def _fit(X_tr, y_tr, w_tr, seed, y_smooth=None):
    import lightgbm as lgb
    cut = int(len(X_tr) * 0.9)
    if y_smooth is not None:
        # lgb.train accepts continuous labels under the binary objective
        dtrain = lgb.Dataset(X_tr[:cut], label=y_smooth[:cut], weight=w_tr[:cut])
        dval = lgb.Dataset(X_tr[cut:], label=y_smooth[cut:], reference=dtrain)
        params = dict(objective="binary", learning_rate=0.03, num_leaves=63,
                      max_depth=6, min_child_samples=50, subsample=0.8,
                      subsample_freq=1, colsample_bytree=0.8, reg_alpha=0.3,
                      reg_lambda=2.0, seed=seed, n_jobs=-1, verbose=-1)
        return lgb.train(params, dtrain, num_boost_round=600, valid_sets=[dval],
                         callbacks=[lgb.early_stopping(30, verbose=False)]), "raw"
    m = lgb.LGBMClassifier(
        objective="binary", n_estimators=600, learning_rate=0.03,
        num_leaves=63, max_depth=6, min_child_samples=50,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
        reg_alpha=0.3, reg_lambda=2.0, class_weight="balanced",
        random_state=seed, n_jobs=-1, verbose=-1,
    )
    m.fit(X_tr[:cut], y_tr[:cut], sample_weight=w_tr[:cut],
          eval_set=[(X_tr[cut:], y_tr[cut:])], eval_metric="auc",
          callbacks=[lgb.early_stopping(30, verbose=False)])
    return m, "proba"


def _eval(name, proba_te, y_te):
    from sklearn.metrics import roc_auc_score
    order = np.argsort(proba_te)[::-1]
    size = len(order) // 10
    deciles = [round(float(y_te[order[d*size:(d+1)*size if d < 9 else len(order)]].mean()) * 100, 1)
               for d in range(10)]
    out = {"name": name, "auc": round(roc_auc_score(y_te, proba_te), 4),
           "top_decile": deciles[0], "bottom_decile": deciles[-1],
           "spread": round(deciles[0] - deciles[-1], 1), "deciles": deciles}
    print(f"  {name:26} AUC={out['auc']:.4f} top={out['top_decile']}% "
          f"bot={out['bottom_decile']}% spr={out['spread']}", flush=True)
    return out


if __name__ == "__main__":
    X, y, dates = load()
    n_train = int(len(X) * 0.8)
    X_tr, X_te = X[:n_train], X[n_train:]
    y_tr, y_te = y[:n_train], y[n_train:]
    d_tr = dates[:n_train]
    days_old = (d_tr.max() - d_tr).astype(float)
    w = np.power(0.5, days_old / 365.0)
    w = w / w.mean()

    results = {}

    def _proba(mt):
        m, kind = mt
        if kind == "raw":
            return 1.0 / (1.0 + np.exp(-m.predict(X_te)))
        return m.predict_proba(X_te)[:, 1]

    # Q0: baseline (production config)
    m0 = _fit(X_tr, y_tr, w, 42)
    m0_model = m0[0]
    results["Q0_baseline"] = _eval("Q0_baseline", _proba(m0), y_te)

    # Q1: focal-style reweighting with 2-fold OOF predictions (in-sample
    # probabilities collapse to 0/1 and degenerate the weights)
    half = len(X_tr) // 2
    oof = np.zeros(len(X_tr), dtype=np.float64)
    mA = _fit(X_tr[:half], y_tr[:half], w[:half], 42)[0]
    oof[half:] = mA.predict_proba(X_tr[half:])[:, 1]
    mB = _fit(X_tr[half:], y_tr[half:], w[half:], 42)[0]
    oof[:half] = mB.predict_proba(X_tr[:half])[:, 1]
    p_t = np.where(y_tr == 1, oof, 1 - oof)
    focal_w = w * np.power(1 - p_t, 2.0)
    focal_w = focal_w / focal_w.mean()
    m1 = _fit(X_tr, y_tr, focal_w, 42)
    results["Q1_focal"] = _eval("Q1_focal", _proba(m1), y_te)

    # Q2: label smoothing
    y_smooth = np.where(y_tr == 1, 0.95, 0.05)
    m2 = _fit(X_tr, y_tr, w, 42, y_smooth=y_smooth)
    results["Q2_smooth"] = _eval("Q2_smooth", _proba(m2), y_te)

    # Q3: 3-seed ensemble
    probas = []
    for seed in (42, 7, 2026):
        ms = _fit(X_tr, y_tr, w, seed)
        probas.append(_proba(ms))
    results["Q3_seed3"] = _eval("Q3_seed3", np.mean(probas, axis=0), y_te)

    # Q4: focal + seed3
    probas_f = []
    for seed in (42, 7, 2026):
        mf = _fit(X_tr, y_tr, focal_w, seed)
        probas_f.append(_proba(mf))
    results["Q4_focal_seed3"] = _eval("Q4_focal_seed3", np.mean(probas_f, axis=0), y_te)

    with open("/tmp/quality_ab_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("saved /tmp/quality_ab_results.json", flush=True)

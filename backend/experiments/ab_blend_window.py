"""
Production-config A/B: sample size N x ridge blend weight, modern window.

Production baseline (Fix 20 run): N=300K, blend=0.4 -> AUC 0.6855, top 42.5%,
bottom 3.9%. The proba-only sweep suggested N=150-200K and that the 0.4 ridge
blend may be hurting ranking on modern data. This measures the full production
pipeline (fills, prune, ridge head, rank blend, isotonic N/A here) across
N in {200K, 300K} x blend in {0.0, 0.2, 0.4}.

Run inside the container:
    docker cp backend/experiments/ab_blend_window.py asx-backend:/tmp/ && \
    docker exec asx-backend python3 /tmp/ab_blend_window.py

No DB writes. Saves /tmp/blend_ab_results.json
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
    X, y, y_reg, dates = [], [], [], []
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
            "ORDER BY signal_date DESC LIMIT :n) t ORDER BY signal_date ASC"
        ), {"n": n_rows})
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
                y_reg.append(float(fwd))
                dates.append(signal_date)
            except Exception:
                continue
    X = np.asarray(X, dtype=np.float32)
    y = np.asarray(y, dtype=np.int8)
    y_reg = np.asarray(y_reg, dtype=np.float32)
    dates = np.array(dates, dtype="datetime64[D]")
    return X, y, y_reg, dates


def run(n_rows, blend_w):
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    from scipy.stats import rankdata
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    t0 = time.time()
    X, y, y_reg, dates = load_recent(n_rows)
    nz = np.std(X, axis=0) > 1e-12
    X = X[:, nz]
    active = [f for f, k in zip(FEATURE_COLS, nz) if k]
    keep = np.array([f not in PRUNED_FEATURES for f in active], dtype=bool)
    X = X[:, keep]
    active = [f for f, k in zip(active, keep) if k]

    n_train = int(len(X) * 0.8)
    X_tr, X_te = X[:n_train], X[n_train:]
    y_tr, y_te = y[:n_train], y[n_train:]
    yr_tr, yr_te = y_reg[:n_train], y_reg[n_train:]
    d_tr = dates[:n_train]

    days_old = (d_tr.max() - d_tr).astype(float)
    w = np.power(0.5, days_old / 365.0)
    w = w / w.mean()

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

    scaler = StandardScaler().fit(X_tr)
    ridge = Ridge(alpha=10.0).fit(scaler.transform(X_tr), yr_tr)
    r_proba = ridge.predict(scaler.transform(X_te))
    blended = (1 - blend_w) * rankdata(proba) + blend_w * rankdata(r_proba)

    order = np.argsort(blended)[::-1]
    size = len(order) // 10
    deciles = []
    for d in range(10):
        lo = d * size
        hi = lo + size if d < 9 else len(order)
        deciles.append(round(float(y_te[order[lo:hi]].mean()) * 100, 1))

    out = {
        "n": n_rows, "blend": blend_w,
        "window": [str(dates.min()), str(dates.max())],
        "base_pct": round(float(y_te.mean()) * 100, 1),
        "auc": round(roc_auc_score(y_te, blended), 4),
        "top_decile": deciles[0], "bottom_decile": deciles[-1],
        "spread": round(deciles[0] - deciles[-1], 1),
        "deciles": deciles,
        "secs": round(time.time() - t0, 1),
    }
    print(f"  N={n_rows:>6} blend={blend_w} | base {out['base_pct']}% | AUC {out['auc']} | "
          f"top {out['top_decile']}% | bot {out['bottom_decile']}% | spr {out['spread']} | "
          f"{out['secs']}s", flush=True)
    return out


if __name__ == "__main__":
    results = {}
    for n in (200000, 300000):
        for bw in (0.0, 0.2, 0.4):
            results[f"n{n}_b{bw}"] = run(n, bw)
    with open("/tmp/blend_ab_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("saved /tmp/blend_ab_results.json", flush=True)

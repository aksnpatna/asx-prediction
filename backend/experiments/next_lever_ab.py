"""
Next-lever experiment (v4): recency half-life, interaction features, EPS growth.

Production baseline: 200K most-recent rows, h=365d, proba-only, 46 features
(AUC ~0.75, top decile ~60% on the adjacent split).

Variants (same pipeline, chrono 80/20):
  V0: baseline (h=365)                      — reproduction check
  V1: h=180 (steeper recency)
  V2: h=270
  V3: + 4 macro-interaction features
  V4: + 2 PIT EPS-growth features (eps_qoq_growth, eps_yoq_growth)
  V5: V3 + V4

Adoption rule: challenger needs AUC >= baseline+0.004 AND top decile >=
baseline AND bottom decile <= baseline+1.0.

Run inside the container:
    docker cp backend/experiments/next_lever_ab.py asx-backend:/tmp/ && \
    docker exec asx-backend python3 /tmp/next_lever_ab.py

No DB writes. Saves /tmp/next_lever_results.json
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


# ── Feature builders ──────────────────────────────────────────────────────────

def interaction_features(X):
    """Append macro-interaction columns (product terms)."""
    i = {c: FEATURE_COLS.index(c) for c in FEATURE_COLS if c in (
        "vix_level", "momentum_20d", "copper_gold_ratio", "macd_hist",
        "atr_pct", "yield_curve_slope", "momentum_63d")}
    extra = np.column_stack([
        X[:, i["vix_level"]] * X[:, i["momentum_20d"]],
        X[:, i["copper_gold_ratio"]] * X[:, i["macd_hist"]],
        X[:, i["vix_level"]] * X[:, i["atr_pct"]],
        X[:, i["yield_curve_slope"]] * X[:, i["momentum_63d"]],
    ]).astype(np.float32)
    names = ["ix_vix_mom20", "ix_cg_macd", "ix_vix_atr", "ix_yc_mom63"]
    return np.hstack([X, extra]), names


def eps_growth_features(X, symbols, signal_dates, eps_map):
    """PIT quarterly EPS growth from eps_history (0 when insufficient history)."""
    qoq = np.zeros(len(X), dtype=np.float32)
    yoq = np.zeros(len(X), dtype=np.float32)
    for idx, (sym, sd) in enumerate(zip(symbols, signal_dates)):
        entry = eps_map.get(sym)
        if not entry:
            continue
        dates, eps_vals = entry
        d64 = np.datetime64(sd)
        pos = int(np.searchsorted(dates, d64, side="right"))  # entries <= signal date
        if pos < 2:
            continue
        latest, prev = eps_vals[pos - 1], eps_vals[pos - 2]
        if prev > 0:
            qoq[idx] = (latest - prev) / prev
        if pos >= 5:
            y_ago = eps_vals[pos - 5]
            if y_ago > 0:
                yoq[idx] = (latest - y_ago) / y_ago
    return np.hstack([X, qoq.reshape(-1, 1), yoq.reshape(-1, 1)]), \
        ["eps_qoq_growth", "eps_yoq_growth"]


# ── Data loading (200K most recent, production fills) ─────────────────────────

def load():
    t0 = time.time()
    fund_map = _load_latest_fundamentals(db_conn)
    hist_map = _load_historical_fundamentals(db_conn)
    eodhd_map = _load_eodhd_features(db_conn)
    eps_map = _load_eps_history(db_conn)
    X, y, dates, symbols = [], [], [], []
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
                symbols.append(symbol)
            except Exception:
                continue
    X = np.asarray(X, dtype=np.float32)
    print(f"[load] {len(X)} rows in {time.time()-t0:.0f}s", flush=True)
    return X, np.asarray(y, dtype=np.int8), np.array(dates, dtype="datetime64[D]"), \
        symbols, eps_map


def run_variant(name, X, y, dates, half_life, extra_fn=None, extra_eps=False,
                symbols=None, eps_map=None):
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score

    Xv = X
    if extra_fn is not None:
        Xv, names = extra_fn(X)
    if extra_eps:
        Xv, names2 = eps_growth_features(Xv, symbols, dates, eps_map)
    nz = np.std(Xv, axis=0) > 1e-12
    Xv = Xv[:, nz]
    all_names = FEATURE_COLS + (["ix_vix_mom20", "ix_cg_macd", "ix_vix_atr", "ix_yc_mom63"]
                                if extra_fn is not None else []) + \
        (["eps_qoq_growth", "eps_yoq_growth"] if extra_eps else [])
    active = [f for f, k in zip(all_names, nz) if k]
    keep = np.array([f not in PRUNED_FEATURES for f in active], dtype=bool)
    Xv = Xv[:, keep]
    kept = [f for f, k in zip(active, keep) if k]

    n_train = int(len(Xv) * 0.8)
    X_tr, X_te = Xv[:n_train], Xv[n_train:]
    y_tr, y_te = y[:n_train], y[n_train:]
    d_tr = dates[:n_train]

    days_old = (d_tr.max() - d_tr).astype(float)
    w = np.power(0.5, days_old / float(half_life))
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
    order = np.argsort(proba)[::-1]
    size = len(order) // 10
    deciles = [round(float(y_te[order[d*size:(d+1)*size if d < 9 else len(order)]].mean()) * 100, 1)
               for d in range(10)]
    out = {
        "name": name, "auc": round(roc_auc_score(y_te, proba), 4),
        "top_decile": deciles[0], "bottom_decile": deciles[-1],
        "spread": round(deciles[0] - deciles[-1], 1),
        "n_features": len(kept),
        "new_features_kept": [f for f in kept if f not in FEATURE_COLS],
        "deciles": deciles,
    }
    print(f"  {name:26} AUC={out['auc']:.4f} top={out['top_decile']}% "
          f"bot={out['bottom_decile']}% spr={out['spread']} | feats={len(kept)} "
          f"new={out['new_features_kept']}", flush=True)
    return out


if __name__ == "__main__":
    X, y, dates, symbols, eps_map = load()
    results = {}
    results["V0_baseline"] = run_variant("V0_baseline", X, y, dates, 365)
    results["V1_h180"] = run_variant("V1_h180", X, y, dates, 180)
    results["V2_h270"] = run_variant("V2_h270", X, y, dates, 270)
    results["V3_interactions"] = run_variant("V3_interactions", X, y, dates, 365,
                                             extra_fn=interaction_features)
    results["V4_eps_growth"] = run_variant("V4_eps_growth", X, y, dates, 365,
                                           extra_eps=True, symbols=symbols, eps_map=eps_map)
    results["V5_combo"] = run_variant("V5_combo", X, y, dates, 365,
                                      extra_fn=interaction_features, extra_eps=True,
                                      symbols=symbols, eps_map=eps_map)
    with open("/tmp/next_lever_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("saved /tmp/next_lever_results.json", flush=True)

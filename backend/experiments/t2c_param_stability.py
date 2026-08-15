"""
T2-C: LightGBM hyperparameter stability across WFO folds.

Tests whether the production params (num_leaves=63, max_depth=6) are stable
across regimes or whether folds want different trees (evidence for
regime-conditional params). 4 configs x 3 folds (2022 bear, 2024 bull,
recent production window), single LGBM, proba-only, same pipeline as
regime_ab_test.py.

Run inside the container:
    docker cp backend/experiments/t2c_param_stability.py asx-backend:/tmp/ && \
    docker exec asx-backend python3 /tmp/t2c_param_stability.py

No DB writes. Saves /tmp/t2c_results.json
"""
import os, sys, json, time, argparse
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


def load_rows(lo, hi, cap):
    fund_map = _load_latest_fundamentals(db_conn)
    hist_map = _load_historical_fundamentals(db_conn)
    eodhd_map = _load_eodhd_features(db_conn)
    eps_map = _load_eps_history(db_conn)
    X, y = [], []
    month_count = {}
    with db_conn() as conn:
        result = conn.execution_options(
            stream_results=True, max_row_buffer=20000).execute(text(
            "SELECT symbol, entry_price, signal_date, features, "
            "CAST(hit_8pct_before_m8pct AS INTEGER) "
            "FROM model_training_set "
            "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
            "AND signal_date >= :lo AND signal_date < :hi "
            "ORDER BY signal_date ASC"
        ), {"lo": lo, "hi": hi})
        for row in result.yield_per(20000):
            try:
                symbol, entry_price, signal_date, feats_raw, lab = row
                mkey = signal_date.strftime("%Y-%m")
                if month_count.get(mkey, 0) >= cap:
                    continue
                feats = json.loads(feats_raw) if isinstance(feats_raw, str) else (feats_raw or {})
                feats = _fill_fundamentals(feats, symbol, fund_map, float(entry_price or 0))
                feats = _fill_point_in_time_pe(feats, symbol, signal_date,
                                               float(entry_price or 0), eps_map)
                feats = _fill_historical(feats, symbol, hist_map)
                feats = _fill_eodhd(feats, symbol, eodhd_map)
                x = np.array([feats.get(c, 0) for c in FEATURE_COLS], dtype=np.float32)
                if not np.isfinite(x).all():
                    continue
                X.append(x)
                y.append(int(lab) if lab is not None else 0)
                month_count[mkey] = month_count.get(mkey, 0) + 1
            except Exception:
                continue
    X = np.asarray(X, dtype=np.float32)
    nz = np.std(X, axis=0) > 1e-12
    X = X[:, nz]
    active = [f for f, k in zip(FEATURE_COLS, nz) if k]
    keep = np.array([f not in PRUNED_FEATURES for f in active], dtype=bool)
    X = X[:, keep]
    return X, np.asarray(y, dtype=np.int8)


def run_fold(fold):
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    cfg = {
        "2022": dict(lo="2019-01-01", hi_train="2022-01-01", lo_eval="2022-01-01", hi_eval="2022-11-01", cap=8000),
        "2024": dict(lo="2021-01-01", hi_train="2024-01-01", lo_eval="2024-01-01", hi_eval="2024-12-01", cap=8000),
        "recent": dict(lo="2023-07-01", hi_train="2025-07-01", lo_eval="2025-07-01", hi_eval="2026-05-16", cap=9000),
    }[fold]
    X, y = load_rows(cfg["lo"], cfg["hi_eval"], cfg["cap"])
    X_tr, y_tr = X, y  # rows in [lo, hi_eval) — split below by loading separate? reuse harness split:
    # This script loads the full window; to keep it simple we approximate the
    # harness split by using the SAME windows but training on [lo, hi_train)
    # only. Reload with date split instead:
    return cfg


def main():
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score

    folds = {
        "2022": ("2019-01-01", "2022-01-01", "2022-01-01", "2022-11-01", 8000),
        "2024": ("2021-01-01", "2024-01-01", "2024-01-01", "2024-12-01", 8000),
        "recent": ("2023-07-01", "2025-07-01", "2025-07-01", "2026-05-16", 9000),
    }
    configs = [
        ("prod_63_6", 63, 6),
        ("shallow_31_4", 31, 4),
        ("deep_127_8", 127, 8),
        ("flat_63_4", 63, 4),
    ]
    results = {}
    for fold, (t_lo, t_hi, e_lo, e_hi, cap) in folds.items():
        X_all, y_all = load_rows(t_lo, e_hi, cap)
        # need date info to split — load again with dates
        fund_map = _load_latest_fundamentals(db_conn)
        hist_map = _load_historical_fundamentals(db_conn)
        eodhd_map = _load_eodhd_features(db_conn)
        eps_map = _load_eps_history(db_conn)
        X, y, dates = [], [], []
        month_count = {}
        with db_conn() as conn:
            result = conn.execution_options(
                stream_results=True, max_row_buffer=20000).execute(text(
                "SELECT symbol, entry_price, signal_date, features, "
                "CAST(hit_8pct_before_m8pct AS INTEGER) "
                "FROM model_training_set "
                "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
                "AND signal_date >= :lo AND signal_date < :hi "
                "ORDER BY signal_date ASC"
            ), {"lo": t_lo, "hi": e_hi})
            for row in result.yield_per(20000):
                try:
                    symbol, entry_price, signal_date, feats_raw, lab = row
                    mkey = signal_date.strftime("%Y-%m")
                    if month_count.get(mkey, 0) >= cap:
                        continue
                    feats = json.loads(feats_raw) if isinstance(feats_raw, str) else (feats_raw or {})
                    feats = _fill_fundamentals(feats, symbol, fund_map, float(entry_price or 0))
                    feats = _fill_point_in_time_pe(feats, symbol, signal_date,
                                                   float(entry_price or 0), eps_map)
                    feats = _fill_historical(feats, symbol, hist_map)
                    feats = _fill_eodhd(feats, symbol, eodhd_map)
                    x = np.array([feats.get(c, 0) for c in FEATURE_COLS], dtype=np.float32)
                    if not np.isfinite(x).all():
                        continue
                    X.append(x)
                    y.append(int(lab) if lab is not None else 0)
                    dates.append(signal_date)
                    month_count[mkey] = month_count.get(mkey, 0) + 1
                except Exception:
                    continue
        X = np.asarray(X, dtype=np.float32)
        y = np.asarray(y, dtype=np.int8)
        dates = np.array(dates, dtype="datetime64[D]")
        nz = np.std(X, axis=0) > 1e-12
        X = X[:, nz]
        active = [f for f, k in zip(FEATURE_COLS, nz) if k]
        keep = np.array([f not in PRUNED_FEATURES for f in active], dtype=bool)
        X = X[:, keep]

        tr = (dates >= np.datetime64(t_lo)) & (dates < np.datetime64(t_hi))
        ev = (dates >= np.datetime64(e_lo)) & (dates < np.datetime64(e_hi))
        X_tr, y_tr = X[tr], y[tr]
        X_ev, y_ev = X[ev], y[ev]
        d_tr = dates[tr]
        days_old = (d_tr.max() - d_tr).astype(float)
        w = np.power(0.5, days_old / 365.0)
        w = w / w.mean()

        for cname, leaves, depth in configs:
            m = lgb.LGBMClassifier(
                objective="binary", n_estimators=600, learning_rate=0.03,
                num_leaves=leaves, max_depth=depth, min_child_samples=50,
                subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                reg_alpha=0.3, reg_lambda=2.0, class_weight="balanced",
                random_state=42, n_jobs=-1, verbose=-1,
            )
            cut = int(len(X_tr) * 0.9)
            m.fit(X_tr[:cut], y_tr[:cut], sample_weight=w[:cut],
                  eval_set=[(X_tr[cut:], y_tr[cut:])], eval_metric="auc",
                  callbacks=[lgb.early_stopping(30, verbose=False)])
            proba = m.predict_proba(X_ev)[:, 1]
            auc = roc_auc_score(y_ev, proba)
            order = np.argsort(proba)[::-1][: len(y_ev) // 10]
            top = float(y_ev[order].mean() * 100)
            results[f"{fold}_{cname}"] = {"auc": round(auc, 4), "top_decile": round(top, 1)}
            print(f"  {fold} {cname:14} AUC={auc:.4f} top={top:.1f}%", flush=True)
    with open("/tmp/t2c_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("saved /tmp/t2c_results.json", flush=True)


if __name__ == "__main__":
    main()

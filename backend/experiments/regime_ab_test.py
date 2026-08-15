"""
Regime + calibration experiment (v3) — read-only, modern training window.

Run inside the backend container:
    docker cp backend/experiments/regime_ab_test.py asx-backend:/tmp/ && \
    docker exec asx-backend python3 /tmp/regime_ab_test.py --fold recent

Or locally with DB access (DATABASE_URL env) from the backend dir.

Background: production trained on ORDER BY signal_date ASC LIMIT 300000 —
the EARLIEST rows (2015-03 -> 2016-08). This experiment evaluates on
modern windows:

  fold 2022:   train [2019-01, 2022-01)  eval [2022-01, 2022-11)   (bear)
  fold 2023:   train [2020-01, 2023-01)  eval [2023-01, 2023-12)   (chop/recovery)
  fold 2024:   train [2021-01, 2024-01)  eval [2024-01, 2024-12)   (bull)
  fold recent: train [2023-07, 2025-07)  eval [2025-07, 2026-05)   (production window)

Models compared on each fold eval:
  S: single LGBM (current production challenger config)
  P: regime pair — bear head trained on bear rows (vix>=20 or XJO<=SMA200),
     bull head on bull rows; eval rows scored by their own point-in-time regime
  L: logistic (for the log baseline, fold recent only)

Calibration (fold recent): isotonic CV on the better binary head; Brier before/after.

No DB writes. Saves /tmp/regime_results_<fold>.json
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


def load_rows(lo: str, hi: str, cap_per_month: int):
    """Stream rows in [lo, hi), cap per calendar month, return arrays."""
    t0 = time.time()
    fund_map = _load_latest_fundamentals(db_conn)
    hist_map = _load_historical_fundamentals(db_conn)
    eodhd_map = _load_eodhd_features(db_conn)
    eps_map = _load_eps_history(db_conn)

    X, y_c, y_ft, y_reg, dates = [], [], [], [], []
    month_count = {}
    n_seen = 0
    with db_conn() as conn:
        result = conn.execution_options(
            stream_results=True, max_row_buffer=20000).execute(text(
            "SELECT symbol, entry_price, signal_date, features, "
            "CAST(hit_8pct_before_m8pct AS INTEGER), "
            "CAST(hit_8pct_first_touch AS INTEGER), "
            "COALESCE(forward_return_63d, 0) "
            "FROM model_training_set "
            "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
            "AND signal_date >= :lo AND signal_date < :hi "
            "ORDER BY signal_date ASC"
        ), {"lo": lo, "hi": hi})
        for row in result.yield_per(20000):
            n_seen += 1
            try:
                symbol, entry_price, signal_date, feats_raw, lab_c, lab_ft, fwd = row
                mkey = signal_date.strftime("%Y-%m")
                if month_count.get(mkey, 0) >= cap_per_month:
                    continue
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
                y_c.append(int(lab_c) if lab_c is not None else 0)
                y_ft.append(int(lab_ft) if lab_ft is not None else 0)
                y_reg.append(float(fwd))
                dates.append(signal_date)
                month_count[mkey] = month_count.get(mkey, 0) + 1
            except Exception:
                continue
    X = np.asarray(X, dtype=np.float32)
    y_c = np.asarray(y_c, dtype=np.int8)
    y_ft = np.asarray(y_ft, dtype=np.int8)
    y_reg = np.asarray(y_reg, dtype=np.float32)
    dates = np.array(dates, dtype="datetime64[D]")
    print(f"[load] {lo}->{hi}: streamed {n_seen}, kept {len(X)} "
          f"(cap {cap_per_month}/month) in {time.time()-t0:.0f}s", flush=True)
    return X, y_c, y_ft, y_reg, dates


def prep(X):
    nz = np.std(X, axis=0) > 1e-12
    X = X[:, nz]
    active = [f for f, k in zip(FEATURE_COLS, nz) if k]
    keep = np.array([f not in PRUNED_FEATURES for f in active], dtype=bool)
    X = X[:, keep]
    active = [f for f, k in zip(active, keep) if k]
    return X, active


def regime_mask(X, active):
    """bear = (vix >= 20) OR (XJO <= SMA200). Point-in-time values."""
    iv = active.index("vix_level")
    ix = active.index("xjo_sma_position")
    bear = (X[:, iv] >= 20.0) | (X[:, ix] < 0.5)
    return bear


def lgbm_head(Xa, ya, w=None):
    import lightgbm as lgb
    m = lgb.LGBMClassifier(
        objective="binary", n_estimators=600, learning_rate=0.03,
        num_leaves=63, max_depth=6, min_child_samples=50,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
        reg_alpha=0.3, reg_lambda=2.0, class_weight="balanced",
        random_state=42, n_jobs=-1, verbose=-1,
    )
    cut = int(len(Xa) * 0.9)
    kw = dict(eval_set=[(Xa[cut:], ya[cut:])], eval_metric="auc",
              callbacks=[lgb.early_stopping(30, verbose=False)])
    if w is not None:
        kw["sample_weight"] = w[:cut]
    m.fit(Xa[:cut], ya[:cut], **kw)
    return m


def recency_weights(dates_train, half_life=365):
    mx = dates_train.max()
    days_old = (mx - dates_train).astype(float)
    w = np.power(0.5, days_old / half_life)
    return w / w.mean()


def deciles(scores, y):
    order = np.argsort(scores)[::-1]
    size = len(order) // 10
    out = []
    for d in range(10):
        lo = d * size
        hi = lo + size if d < 9 else len(order)
        out.append(round(float(y[order[lo:hi]].mean()) * 100, 1))
    return out


def evaluate(name, scores, y_c, y_ft, bear):
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score(y_c, scores)
    d = deciles(scores, y_c)
    d_ft = deciles(scores, y_ft)
    top_ft = d_ft[0]
    ev = 0.08 * top_ft - 0.08 * (100 - top_ft)
    auc_bear = roc_auc_score(y_c[bear], scores[bear]) if bear.sum() >= 30 else float("nan")
    auc_bull = roc_auc_score(y_c[~bear], scores[~bear]) if (~bear).sum() >= 30 else float("nan")
    print(f"  {name:28} AUC={auc:.4f} top={d[0]:.1f} bot={d[-1]:.1f} "
          f"spr={d[0]-d[-1]:.1f} | AUC_bear={auc_bear:.3f} (n={int(bear.sum())}) "
          f"AUC_bull={auc_bull:.3f} (n={int((~bear).sum())}) | ft_top={top_ft:.1f} EV={ev:+.2f}%",
          flush=True)
    return {"auc": round(auc, 4), "deciles_c": d, "deciles_ft": d_ft,
            "auc_bear": None if np.isnan(auc_bear) else round(auc_bear, 4),
            "auc_bull": None if np.isnan(auc_bull) else round(auc_bull, 4),
            "ev_ft_top": round(ev, 2), "n_bear": int(bear.sum()), "n_bull": int((~bear).sum())}


def run_fold(fold):
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.preprocessing import StandardScaler
    from scipy.stats import rankdata

    cfg = {
        "2022": dict(lo="2019-01-01", hi_train="2022-01-01", lo_eval="2022-01-01", hi_eval="2022-11-01", cap=8000),
        "2023": dict(lo="2020-01-01", hi_train="2023-01-01", lo_eval="2023-01-01", hi_eval="2024-01-01", cap=8000),
        "2024": dict(lo="2021-01-01", hi_train="2024-01-01", lo_eval="2024-01-01", hi_eval="2024-12-01", cap=8000),
        "recent": dict(lo="2023-07-01", hi_train="2025-07-01", lo_eval="2025-07-01", hi_eval="2026-05-16", cap=9000),
    }[fold]
    c = cfg
    X, y_c, y_ft, y_reg, dates = load_rows(c["lo"], c["hi_eval"], c["cap"])
    X, active = prep(X)
    bear = regime_mask(X, active)

    tr = (dates >= np.datetime64(c["lo"])) & (dates < np.datetime64(c["hi_train"]))
    ev = (dates >= np.datetime64(c["lo_eval"])) & (dates < np.datetime64(c["hi_eval"]))
    print(f"[fold {fold}] train={int(tr.sum())} eval={int(ev.sum())} | "
          f"train bear={int(bear[tr].sum())} ({bear[tr].mean()*100:.0f}%) | "
          f"eval bear={int(bear[ev].sum())} ({bear[ev].mean()*100:.0f}%) | "
          f"base_c(eval)={y_c[ev].mean()*100:.1f}%", flush=True)

    X_tr, y_tr = X[tr], y_c[tr]
    d_tr = dates[tr]
    w_tr = recency_weights(d_tr)

    results = {}

    # ── S: single LGBM ──────────────────────────────────────────────────────
    t0 = time.time()
    mS = lgbm_head(X_tr, y_tr, w_tr)
    s_proba = mS.predict_proba(X[ev])[:, 1]
    results["S_single"] = evaluate("S_single", s_proba, y_c[ev], y_ft[ev], bear[ev])
    results["S_single"]["fit_s"] = round(time.time() - t0, 1)

    # ── P: regime pair ──────────────────────────────────────────────────────
    t0 = time.time()
    bear_tr = bear[tr]
    X_b, y_b = X_tr[bear_tr], y_tr[bear_tr]
    X_u, y_u = X_tr[~bear_tr], y_tr[~bear_tr]
    w_b = recency_weights(d_tr[bear_tr]) if bear_tr.sum() > 1000 else None
    w_u = recency_weights(d_tr[~bear_tr])
    pair_ok = len(X_b) >= 2000 and len(X_u) >= 2000
    if pair_ok:
        mB = lgbm_head(X_b, y_b, w_b)
        mU = lgbm_head(X_u, y_u, w_u)
        p_proba = np.zeros(ev.sum())
        p_proba[bear[ev]] = mB.predict_proba(X[ev][bear[ev]])[:, 1]
        p_proba[~bear[ev]] = mU.predict_proba(X[ev][~bear[ev]])[:, 1]
        results["P_pair"] = evaluate("P_pair", p_proba, y_c[ev], y_ft[ev], bear[ev])
        results["P_pair"]["fit_s"] = round(time.time() - t0, 1)
        results["P_pair"]["n_bear_train"] = int(len(X_b))
        results["P_pair"]["n_bull_train"] = int(len(X_u))
    else:
        print(f"  P_pair: insufficient regime rows (bear={len(X_b)}, bull={len(X_u)}) — skipped", flush=True)
        results["P_pair"] = None

    # ── L: logistic baseline (fold recent only, for the log) ────────────────
    if fold == "recent":
        t0 = time.time()
        scaler = StandardScaler().fit(X_tr)
        X_tr_s, X_ev_s = scaler.transform(X_tr), scaler.transform(X[ev])
        lr = LogisticRegression(class_weight="balanced", C=0.1, max_iter=5000,
                                random_state=42, n_jobs=-1)
        lr.fit(X_tr_s, y_tr)
        lr_proba = lr.predict_proba(X_ev_s)[:, 1]
        rr = Ridge(alpha=10.0).fit(X_tr_s, y_reg[tr])
        l_score = 0.6 * rankdata(lr_proba) + 0.4 * rankdata(rr.predict(X_ev_s))
        results["L_logistic"] = evaluate("L_logistic", l_score, y_c[ev], y_ft[ev], bear[ev])
        results["L_logistic"]["fit_s"] = round(time.time() - t0, 1)

    # ── Calibration (T1-B): isotonic on the better single head (recent) ────
    if fold == "recent":
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.metrics import brier_score_loss
        t0 = time.time()
        # use a modest clone for calibration speed
        import lightgbm as lgb
        mCal = lgb.LGBMClassifier(
            objective="binary", n_estimators=400, learning_rate=0.05,
            num_leaves=63, max_depth=6, min_child_samples=50,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            reg_alpha=0.3, reg_lambda=2.0, class_weight="balanced",
            random_state=42, n_jobs=-1, verbose=-1,
        )
        cal = CalibratedClassifierCV(mCal, method="isotonic", cv=3, n_jobs=-1)
        cal.fit(X_tr, y_tr)
        raw = mS.predict_proba(X[ev])[:, 1]
        calp = cal.predict_proba(X[ev])[:, 1]
        results["calibration"] = {
            "brier_raw": round(brier_score_loss(y_c[ev], raw), 4),
            "brier_isotonic": round(brier_score_loss(y_c[ev], calp), 4),
            "secs": round(time.time() - t0, 1),
        }
        print(f"  Calibration: Brier raw={results['calibration']['brier_raw']} "
              f"-> isotonic={results['calibration']['brier_isotonic']}", flush=True)

    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fold", choices=["2022", "2023", "2024", "recent"], required=True)
    args = ap.parse_args()
    out = run_fold(args.fold)
    path = f"/tmp/regime_results_{args.fold}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"saved {path}", flush=True)

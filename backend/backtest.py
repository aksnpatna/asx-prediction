"""
SMSF Strategy Backtest — walk-forward with REAL model scores (Fix 25).

Prior problems (fixed 2026-08-15):
  - Scoring used `feats["rsi"]` as a proxy — every P&L number was measuring
    RSI-ranked portfolios, not the model.
  - Exits were `np.random.normal(0.05, 0.15)` — pure noise.

Now:
  - Scoring: walk-forward LGBM per fold-year, trained ONLY on rows before the
    fold (month-capped), same pipeline as train_classifier (fundamental fills,
    permutation pruning, same hyperparameters). No lookahead.
  - Exits: deterministic DB outcomes — 63d close uses forward_return_63d;
    catastrophe stop: forward_max_drawdown_63d <= -20 -> exit at -20%.
  - Bear breaker: PortfolioGate market context per signal_date from the row's
    point-in-time vix_level / xjo_sma_position (VIX >= 25 AND XJO < SMA200
    blocks new entries).

Usage:
    python backend/backtest.py --start 2022-01-01 --end 2026-07-31
"""

import json
import os
import sys
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
ENGINE = create_engine(DATABASE_URL, pool_size=3, max_overflow=3)


def db_conn():
    return ENGINE.connect()


def load_model_training_data(from_date: str, to_date: str) -> pd.DataFrame:
    """Legacy pandas loader (kept for API compat; the sim uses streaming)."""
    with ENGINE.connect() as conn:
        df = pd.read_sql(text("""
            SELECT symbol, signal_date, entry_price, features,
                   forward_return_63d, forward_peak_return_63d,
                   forward_max_drawdown_63d,
                   hit_5pct_63d, hit_8pct_63d
            FROM model_training_set
            WHERE signal_date BETWEEN :start AND :end
              AND features IS NOT NULL
            ORDER BY signal_date
        """), conn, params={"start": from_date, "end": to_date})
    return df


def compute_path_aware_labels(df: pd.DataFrame) -> pd.DataFrame:
    labels = []
    for _, row in df.iterrows():
        dd = row["forward_max_drawdown_63d"]
        peak = row["forward_peak_return_63d"]
        hit_8_before_m8 = (peak >= 8.0 and dd > -8.0)
        hit_8_after_m8 = (peak >= 8.0 and dd <= -8.0)
        win = 1 if hit_8_before_m8 else (-1 if hit_8_after_m8 else 0)
        labels.append({
            "hit_8pct_before_m8pct": win == 1,
            "hit_8pct_but_drew_down": hit_8_after_m8,
            "path_label": win,
            "close_5pct": row["forward_return_63d"] >= 5.0,
            "close_0pct": row["forward_return_63d"] > 0,
        })
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(labels)], axis=1)


# ── Fix 28: historically-safe feature subset for the backtest ───────────────
# EODHD snapshots only exist from 2026-08-14 (464 rows) and fundamental
# snapshots from 2026-07-18 — filling 2022-2025 rows from them is lookahead
# inflation. These features are zeroed in the backtest (auto-dropped as
# zero-variance), so fold models train only on features that are genuinely
# point-in-time: price-derived technicals, XJO-relative, macro series, and
# PIT P/E from eps_history.
BACKTEST_DROP_FEATURES = {
    "eps_surprise", "eps_estimate_revision", "analyst_count",
    "pct_insiders", "pct_institutions", "insider_net_ratio",
    "esg_governance", "esg_controversy", "payout_ratio",
    "fund_hist_roe", "fund_hist_debt_equity", "fund_hist_gross_margin",
    "fund_hist_op_margin", "fund_hist_fcf_yield",
    "fund_div_yield", "fund_analyst_upside", "fund_analyst_rec_score",
    "fund_earnings_growth", "fund_revenue_growth", "fund_beta",
    "fund_pct_from_52w_high", "fund_market_cap_log", "fund_forward_pe_inv",
}


# ── Shared preprocessing (mirrors train_classifier) ────────────────────────

def _load_aux():
    fund_map = _load_latest_fundamentals(db_conn)
    hist_map = _load_historical_fundamentals(db_conn)
    eodhd_map = _load_eodhd_features(db_conn)
    eps_map = _load_eps_history(db_conn)
    return fund_map, hist_map, eodhd_map, eps_map


def _fill_row(feats_raw, symbol, entry_price, signal_date, aux):
    _, _, _, eps_map = aux
    feats = json.loads(feats_raw) if isinstance(feats_raw, str) else (feats_raw or {})
    # Fix 28: PIT P/E only — zero the stored (build-time, anachronistic) value
    # first, then let _fill_point_in_time_pe set it from eps_history when
    # point-in-time EPS exists. No latest-snapshot fills for historical rows.
    feats["fund_pe_inv"] = 0.0
    feats = _fill_point_in_time_pe(feats, symbol, signal_date, float(entry_price or 0), eps_map)
    x = np.array([feats.get(c, 0) for c in FEATURE_COLS], dtype=np.float32)
    for f in BACKTEST_DROP_FEATURES:
        x[FEATURE_COLS.index(f)] = 0.0
    if not np.isfinite(x).all():
        return None
    return x


def _prune(X, active_keep=None):
    nz = np.std(X, axis=0) > 1e-12
    cols = [f for f, k in zip(FEATURE_COLS, nz) if k]
    X = X[:, nz]
    keep = np.array([f not in PRUNED_FEATURES for f in cols], dtype=bool)
    X = X[:, keep]
    cols = [f for f, k in zip(cols, keep) if k]
    return X, cols


def _train_fold_model(X_train, y_train):
    import lightgbm as lgb
    days_old = np.arange(len(X_train))[::-1] / len(X_train) * (3 * 365)
    w = np.power(0.5, days_old / 365.0)
    w = w / w.mean()
    model = lgb.LGBMClassifier(
        objective="binary", n_estimators=600, learning_rate=0.03,
        num_leaves=63, max_depth=6, min_child_samples=50,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
        reg_alpha=0.3, reg_lambda=2.0, class_weight="balanced",
        random_state=42, n_jobs=-1, verbose=-1,
    )
    cut = int(len(X_train) * 0.9)
    model.fit(
        X_train[:cut], y_train[:cut], sample_weight=w[:cut],
        eval_set=[(X_train[cut:], y_train[cut:])],
        eval_metric="auc",
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )
    return model


def _train_wfo_scorers(start_date: str, end_date: str, aux, cap_per_month: int = 8000):
    """Train one LGBM per fold-year on month-capped rows BEFORE the fold.
    Also trains the consensus heads (ridge + RF) per fold (Fix 27)."""
    start_dt = pd.Timestamp(start_date)
    train_lo = (start_dt - pd.DateOffset(years=3)).strftime("%Y-%m-%d")
    first_year = start_dt.year
    last_year = pd.Timestamp(end_date).year
    scorers = {}  # year -> {"model", "cols", "ridge", "scaler", "rf"}

    for year in range(first_year, last_year + 1):
        fold_hi = f"{year}-01-01"
        print(f"[Backtest] Training fold model for {year} on rows < {fold_hi}...", flush=True)
        X_list, y_list, yr_list = [], [], []
        month_count = {}
        with db_conn() as conn:
            result = conn.execution_options(
                stream_results=True, max_row_buffer=20000).execute(text(
                "SELECT symbol, entry_price, signal_date, features, "
                "CAST(hit_8pct_before_m8pct AS INTEGER), "
                "COALESCE(forward_return_63d, 0) "
                "FROM model_training_set "
                "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
                "AND signal_date >= :lo AND signal_date < :hi "
                "ORDER BY signal_date ASC"
            ), {"lo": max(train_lo, "2019-01-01"), "hi": fold_hi})
            for row in result.yield_per(20000):
                try:
                    symbol, entry_price, signal_date, feats_raw, lab, fwd = row
                    mkey = signal_date.strftime("%Y-%m")
                    if month_count.get(mkey, 0) >= cap_per_month:
                        continue
                    x = _fill_row(feats_raw, symbol, entry_price, signal_date, aux)
                    if x is None:
                        continue
                    X_list.append(x)
                    y_list.append(int(lab) if lab is not None else 0)
                    yr_list.append(float(fwd))
                    month_count[mkey] = month_count.get(mkey, 0) + 1
                except Exception:
                    continue
        if len(X_list) < 5000:
            print(f"[Backtest] Fold {year}: {len(X_list)} train rows — reusing previous model", flush=True)
            if scorers:
                scorers[year] = scorers[max(scorers)]
            continue
        X = np.asarray(X_list, dtype=np.float32)
        y = np.asarray(y_list, dtype=np.int8)
        yr = np.asarray(yr_list, dtype=np.float32)
        X, cols = _prune(X)
        model = _train_fold_model(X, y)
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
        from sklearn.ensemble import RandomForestClassifier
        scaler = StandardScaler().fit(X)
        ridge = Ridge(alpha=10.0).fit(scaler.transform(X), yr)
        rf = RandomForestClassifier(
            n_estimators=100, max_depth=12, min_samples_leaf=50,
            class_weight="balanced", random_state=42, n_jobs=-1,
        ).fit(X, y)
        scorers[year] = {"model": model, "cols": cols,
                         "ridge": ridge, "scaler": scaler, "rf": rf}
        print(f"[Backtest] Fold {year}: {len(X)} rows, {len(cols)} features "
              f"(+ridge+rf consensus heads) trained", flush=True)
    return scorers


def walk_forward_backtest(
    start_date: str = "2022-01-01",
    end_date: str = "2026-07-31",
    universe_filter: str = "core",
    score_threshold: float = 0.5,
) -> Dict:
    from portfolio_gate import PortfolioGate

    print(f"[Backtest] Training WFO scorers {start_date} → {end_date}...", flush=True)
    aux = _load_aux()
    scorers = _train_wfo_scorers(start_date, end_date, aux)
    if not scorers:
        raise RuntimeError("No WFO scorers trained")

    gate = PortfolioGate()
    trades = []
    equity_curve = [{"date": start_date, "value": 200_000.0, "cash": 200_000.0}]
    portfolio_value = 200_000.0
    cash = 200_000.0
    open_positions = []

    day_X, day_meta = [], []
    cur_date = None
    n_rows = 0

    def process_day(sig_date):
        nonlocal cash, portfolio_value
        if not day_X:
            return
        X = np.asarray(day_X, dtype=np.float32)
        year = sig_date.year
        scorer = scorers.get(year)
        if scorer is None:
            scorer = scorers.get(max(scorers))
            if scorer is None:
                return
        cols = scorer["cols"]
        X_sel = np.zeros((len(X), len(cols)), dtype=np.float32)
        for j, c in enumerate(cols):
            idx = FEATURE_COLS.index(c)
            X_sel[:, j] = X[:, idx]
        proba = scorer["model"].predict_proba(X_sel)[:, 1]

        # Fix 27: day-level consensus gate (R2: LGBM top-decile AND ridge-q75
        # AND RF-q75). Applied only when the day batch is large enough.
        from scipy.stats import rankdata
        r_scores = scorer["ridge"].predict(scorer["scaler"].transform(X_sel))
        rf_scores = scorer["rf"].predict_proba(X_sel)[:, 1]
        n_day = len(X_sel)
        if n_day >= 40:
            dec = rankdata(proba) >= 0.9 * n_day
            rfq = rankdata(rf_scores) >= 0.75 * n_day
            rgq = rankdata(r_scores) >= 0.75 * n_day
            eligible = dec & rfq & rgq
        else:
            eligible = np.ones(n_day, dtype=bool)

        # Bear breaker context from point-in-time row features
        vix_vals = [m["vix"] for m in day_meta]
        xjo_vals = [m["xjo_pos"] for m in day_meta]
        gate.set_market_context(
            vix_level=float(np.mean(vix_vals)) if vix_vals else None,
            xjo_above_sma200=(float(np.mean(xjo_vals)) >= 0.5) if xjo_vals else None,
        )

        # Close positions at 63-day horizon (deterministic outcomes)
        for pos in list(open_positions):
            if (sig_date - pos["entry_date"]).days >= 63:
                ret = pos["fwd_ret"]
                if pos["max_dd"] <= -20.0:
                    ret = -20.0  # catastrophe stop
                exit_price = pos["entry_price"] * (1 + ret / 100.0)
                pnl = (exit_price / pos["entry_price"] - 1) * pos["value"]
                cash += pos["value"] + pnl
                trades.append({
                    "symbol": pos["symbol"],
                    "entry_date": str(pos["entry_date"]),
                    "exit_date": str(sig_date),
                    "entry_price": round(pos["entry_price"], 4),
                    "exit_price": round(exit_price, 4),
                    "pnl_pct": round(ret, 2),
                    "days_held": (sig_date - pos["entry_date"]).days,
                })
                open_positions.remove(pos)

        # Open: top-5 by proba above threshold, consensus-eligible only
        order = np.argsort(proba)[::-1]
        opened = 0
        for i in order:
            if opened >= 5:
                break
            if proba[i] < score_threshold:
                continue
            if not eligible[i]:
                continue
            meta = day_meta[i]
            pos_value = portfolio_value * 0.05
            if pos_value > cash:
                continue
            gate_result = gate.can_open_position(
                proposed_symbol=meta["symbol"],
                proposed_sector="DEFAULT",
                proposed_value=pos_value,
                proposed_adv_20d=1_000_000,
                portfolio_total_value=portfolio_value,
                portfolio_cash=cash,
                open_positions=open_positions,
            )
            if not gate_result["approved"]:
                continue
            cash -= pos_value
            open_positions.append({
                "symbol": meta["symbol"],
                "entry_date": sig_date,
                "entry_price": meta["entry_price"],
                "value": pos_value,
                "sector": "DEFAULT",
                "fwd_ret": meta["fwd_ret"],
                "max_dd": meta["max_dd"],
            })
            opened += 1

        satellite_value = sum(p.get("value", 0) for p in open_positions)
        portfolio_value = cash + satellite_value
        equity_curve.append({
            "date": str(sig_date),
            "value": round(portfolio_value, 2),
            "cash": round(cash, 2),
            "positions": len(open_positions),
        })

    print(f"[Backtest] Simulating {start_date} → {end_date}...", flush=True)
    with db_conn() as conn:
        result = conn.execution_options(
            stream_results=True, max_row_buffer=20000).execute(text(
            "SELECT symbol, entry_price, signal_date, features, "
            "COALESCE(forward_return_63d, 0), COALESCE(forward_max_drawdown_63d, 0) "
            "FROM model_training_set "
            "WHERE features IS NOT NULL AND ABS(forward_peak_return_63d) <= 500 "
            "AND signal_date >= :lo AND signal_date < :hi "
            "ORDER BY signal_date ASC"
        ), {"lo": start_date, "hi": end_date})
        for row in result.yield_per(20000):
            try:
                symbol, entry_price, signal_date, feats_raw, fwd_ret, max_dd = row
                if float(entry_price or 0) <= 0.01:
                    continue
                x = _fill_row(feats_raw, symbol, entry_price, signal_date, aux)
                if x is None:
                    continue
                if cur_date is None:
                    cur_date = signal_date
                elif signal_date != cur_date:
                    process_day(cur_date)
                    day_X, day_meta = [], []
                    cur_date = signal_date
                day_X.append(x)
                day_meta.append({
                    "symbol": symbol,
                    "entry_price": float(entry_price),
                    "fwd_ret": float(fwd_ret),
                    "max_dd": float(max_dd),
                    "vix": float(x[FEATURE_COLS.index("vix_level")]),
                    "xjo_pos": float(x[FEATURE_COLS.index("xjo_sma_position")]),
                })
                n_rows += 1
            except Exception:
                continue
    if day_X:
        process_day(cur_date)

    print(f"[Backtest] Simulated {n_rows} rows, {len(trades)} trades", flush=True)

    final_value = equity_curve[-1]["value"]
    total_return = (final_value / 200_000 - 1) * 100
    years = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days / 365.25

    wins = [t for t in trades if t["pnl_pct"] > 0]
    hit_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]) \
        if any(t["pnl_pct"] <= 0 for t in trades) else 0

    values = [e["value"] for e in equity_curve]
    returns = np.diff(values) / np.array(values[:-1])
    sharpe = (np.mean(returns) / (np.std(returns) + 1e-9)) * np.sqrt(252) if len(returns) > 1 else 0
    max_dd = 0
    peak = values[0]
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        max_dd = max(max_dd, dd)

    return {
        "start": start_date,
        "end": end_date,
        "score_source": "wfo_lgbm_proba",
        "exit_source": "forward_return_63d + -20pct catastrophe stop",
        "final_value": round(final_value, 0),
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(total_return / years, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "n_trades": len(trades),
        "hit_rate_pct": round(hit_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": abs(avg_win * len(wins) / (avg_loss * (len(trades) - len(wins)) + 1e-9)),
        "equity_curve": equity_curve[-10:],
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="core")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--output", default="results/backtest.json")
    args = parser.parse_args()

    result = walk_forward_backtest(
        start_date=args.start,
        end_date=args.end,
        universe_filter=args.universe,
    )

    os.makedirs("results", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"\n[Backtest] Results written to {args.output}")
    print(f"  Final: ${result['final_value']:,.0f} ({result['total_return_pct']:+.1f}%)")
    print(f"  Annual: {result['annual_return_pct']:+.1f}% | Sharpe: {result['sharpe']:.2f}")
    print(f"  Max DD: {result['max_drawdown_pct']:.1f}% | Hit rate: {result['hit_rate_pct']:.1f}%")
    print(f"  Trades: {result['n_trades']} | Avg win: {result['avg_win_pct']:+.1f}% | Avg loss: {result['avg_loss_pct']:+.1f}%")

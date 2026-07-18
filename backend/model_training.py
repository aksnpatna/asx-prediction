"""Model Training — Daily logistic regression pipeline for ASX feature → outcome learning.

Workflow (called daily by scheduler):
    1. build_training_matrix()    — compute features + outcomes from eod_ohl_history
    2. fit_model_weights()        — logistic regression: features → hit_3pct label
    3. apply_trained_weights()    — persist model coefficients for live prediction

Feature set computed entirely in vectorized pandas (O(n), not O(n²)).
"""
import json
import os
import sys
import time
from datetime import date, timedelta
from typing import Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FORWARD_WINDOW_DAYS = 63
HIT_THRESHOLD_PCT = 3.0
MIN_TRAINING_SAMPLES = 200

FEATURE_COLS = [
    "sma_cross_20_50", "sma_cross_50_200", "rsi", "rsi_slope", "kde_rsi_prob",
    "macd_hist", "momentum_20d", "momentum_63d", "donchian_breakout",
    "volume_spike", "volume_ratio", "obv_bullish", "cmf", "cmf_bullish",
    "atr_pct", "bb_width", "bb_position", "hv_20d",
    "adx", "adx_trend", "ema_ribbon", "ttm_squeeze_on", "ttm_squeeze_fired",
    # Derived moat features
    "signal_cluster", "trend_strength", "rsi_vol_adj", "mom_per_vol",
    "dist_from_sma50", "rsi_macd_div", "vol_confirm", "bb_squeeze_ratio",
    # Free fundamental features (yfinance monthly snapshots, no EODHD cost)
    "fund_pe_inv", "fund_forward_pe_inv", "fund_market_cap_log",
    "fund_div_yield", "fund_analyst_upside", "fund_analyst_rec_score",
    "fund_earnings_growth", "fund_revenue_growth", "fund_beta",
    "fund_pct_from_52w_high",
]


def _build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized: compute all 23 features for every row in df in one pass. O(n)."""
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df.get("Volume", pd.Series(0, index=df.index)).astype(float)
    n = len(df)

    fm = pd.DataFrame(index=df.index)

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean().fillna(sma50)
    fm["sma_cross_20_50"] = (sma20 > sma50).astype(float)
    fm["sma_cross_50_200"] = (sma50 > sma200).astype(float)

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    rsi_series = 100 - (100 / (1 + rs))
    fm["rsi"] = rsi_series
    rsi_20_ago = rsi_series.shift(20)
    fm["rsi_slope"] = rsi_series - rsi_20_ago.fillna(rsi_series)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    fm["macd_hist"] = (macd_line - signal_line) / (close + 1e-9)

    fm["momentum_20d"] = close.pct_change(20) * 100
    fm["momentum_63d"] = close.pct_change(63) * 100

    donchian_20 = high.rolling(20).max().shift(1)
    fm["donchian_breakout"] = (close >= donchian_20).astype(float)

    vol_avg20 = vol.rolling(20).mean().shift(1)
    fm["volume_spike"] = vol / (vol_avg20 + 1e-9)
    fm["volume_ratio"] = (vol - vol_avg20).abs() / (vol_avg20 + 1e-9)

    obv_delta = np.sign(close.diff()).fillna(0) * vol
    obv = obv_delta.cumsum()
    obv_sma = obv.rolling(20).mean()
    fm["obv_bullish"] = (obv > obv_sma).astype(float)

    mf = ((close - low) - (high - close)) / (high - low + 1e-9)
    mf_vol = mf * vol
    cmf_series = mf_vol.rolling(20).sum() / (vol.rolling(20).sum() + 1e-9)
    fm["cmf"] = cmf_series
    fm["cmf_bullish"] = (cmf_series > 0.10).astype(float)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    fm["atr_pct"] = atr / (close + 1e-9)

    bb_ma = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_ma + 2 * bb_std
    bb_lower = bb_ma - 2 * bb_std
    fm["bb_width"] = (bb_upper - bb_lower) / (bb_ma + 1e-9)
    fm["bb_position"] = (close - bb_lower) / (bb_upper - bb_lower + 1e-9)

    rets = close.pct_change()
    fm["hv_20d"] = rets.rolling(20).std() * np.sqrt(252)

    up_move = high.diff().clip(lower=0)
    dn_move = (-low.diff()).clip(lower=0)
    dmp = up_move.where((up_move > dn_move) & (up_move > 0), 0)
    dmm = dn_move.where((dn_move > up_move) & (dn_move > 0), 0)
    atr_adx = tr.rolling(14).mean()
    smooth_dmp = dmp.rolling(14).mean()
    smooth_dmm = dmm.rolling(14).mean()
    di_sum = smooth_dmp + smooth_dmm
    dx = (abs(smooth_dmp - smooth_dmm) / (di_sum + 1e-9)) * 100
    fm["adx"] = dx
    fm["adx_trend"] = (smooth_dmp > smooth_dmm).astype(float)

    ema5 = close.ewm(span=5, adjust=False).mean()
    ema30 = close.ewm(span=30, adjust=False).mean()
    fm["ema_ribbon"] = (ema5 > ema30).astype(float)

    kc_upper = sma20 + (1.5 * atr)
    kc_lower = sma20 - (1.5 * atr)
    squeeze_on = (bb_upper < kc_upper) & (bb_lower > kc_lower)
    fm["ttm_squeeze_on"] = squeeze_on.astype(float)
    squeeze_fired = (~squeeze_on.fillna(False)) & (fm["momentum_20d"].fillna(0) > 0)
    fm["ttm_squeeze_fired"] = squeeze_fired.astype(float)

    kde_rank = rsi_series.expanding(min_periods=30).rank(pct=True)
    fm["kde_rsi_prob"] = kde_rank

    # ── Moat-derived features (interactions, not raw indicators) ──────────────
    bullish_signals = (
        fm["sma_cross_20_50"].fillna(0) +
        fm["sma_cross_50_200"].fillna(0) +
        (fm["rsi"].fillna(50) < 40).astype(float) * 0.5 +
        (fm["macd_hist"].fillna(0) > 0).astype(float) +
        fm["ema_ribbon"].fillna(0) +
        fm["cmf_bullish"].fillna(0) +
        fm["obv_bullish"].fillna(0)
    )
    fm["signal_cluster"] = bullish_signals
    fm["trend_strength"] = fm["momentum_20d"].abs().fillna(0) * fm["adx"].fillna(20) / 100.0
    fm["rsi_vol_adj"] = fm["rsi"].fillna(50) / (fm["hv_20d"].fillna(0.2) + 1.0)
    fm["mom_per_vol"] = fm["momentum_20d"].fillna(0) / (fm["hv_20d"].fillna(0.2) + 1e-9)
    fm["dist_from_sma50"] = (close - sma50.fillna(close)) / (sma50.fillna(close) + 1e-9) * 100
    rsi_z = (fm["rsi"].fillna(50) - 50) / fm["rsi"].rolling(63).std().fillna(15)
    macd_z = fm["macd_hist"].fillna(0) / fm["macd_hist"].rolling(63).std().fillna(0.01)
    fm["rsi_macd_div"] = rsi_z - macd_z
    fm["vol_confirm"] = fm["volume_spike"].fillna(1) * np.sign(fm["momentum_20d"].fillna(0))
    fm["bb_squeeze_ratio"] = fm["bb_width"].fillna(0.05) / (fm["atr_pct"].fillna(0.01) + 1e-9)

    # Fill NaN values with reasonable defaults (occur before enough data accumulates)
    fm["sma_cross_20_50"] = fm["sma_cross_20_50"].fillna(0.5)
    fm["sma_cross_50_200"] = fm["sma_cross_50_200"].fillna(0.5)
    fm["rsi"] = fm["rsi"].fillna(50)
    fm["rsi_slope"] = fm["rsi_slope"].fillna(0)
    fm["macd_hist"] = fm["macd_hist"].fillna(0)
    fm["momentum_20d"] = fm["momentum_20d"].fillna(0)
    fm["momentum_63d"] = fm["momentum_63d"].fillna(0)
    fm["donchian_breakout"] = fm["donchian_breakout"].fillna(0)
    fm["volume_spike"] = fm["volume_spike"].fillna(1)
    fm["volume_ratio"] = fm["volume_ratio"].fillna(0)
    fm["obv_bullish"] = fm["obv_bullish"].fillna(0)
    fm["cmf"] = fm["cmf"].fillna(0)
    fm["cmf_bullish"] = fm["cmf_bullish"].fillna(0)
    fm["atr_pct"] = fm["atr_pct"].fillna(0)
    fm["bb_width"] = fm["bb_width"].fillna(0.05)
    fm["bb_position"] = fm["bb_position"].fillna(0.5)
    fm["hv_20d"] = fm["hv_20d"].fillna(0)
    fm["adx"] = fm["adx"].fillna(20)
    fm["adx_trend"] = fm["adx_trend"].fillna(0.5)
    fm["ema_ribbon"] = fm["ema_ribbon"].fillna(0.5)
    fm["ttm_squeeze_on"] = fm["ttm_squeeze_on"].fillna(0)
    fm["ttm_squeeze_fired"] = fm["ttm_squeeze_fired"].fillna(0)
    fm["kde_rsi_prob"] = fm["kde_rsi_prob"].fillna(0.5)
    fm["signal_cluster"] = fm["signal_cluster"].fillna(0)
    fm["trend_strength"] = fm["trend_strength"].fillna(0)
    fm["rsi_vol_adj"] = fm["rsi_vol_adj"].fillna(50)
    fm["mom_per_vol"] = fm["mom_per_vol"].fillna(0)
    fm["dist_from_sma50"] = fm["dist_from_sma50"].fillna(0)
    fm["rsi_macd_div"] = fm["rsi_macd_div"].fillna(0)
    fm["vol_confirm"] = fm["vol_confirm"].fillna(0)
    fm["bb_squeeze_ratio"] = fm["bb_squeeze_ratio"].fillna(5.0)

    return fm


def _enrich_fundamentals():
    """Batch-update model_training_set rows with nearest prior fundamental snapshot.

    Fills fund_pe_inv, fund_market_cap_log, fund_analyst_upside, etc.
    from the fundamental_snapshots table using the most recent snapshot
    before each signal_date. Single UPDATE pass — no per-row loop.
    """
    from sqlalchemy import text
    from main import db_conn

    try:
        with db_conn() as conn:
            updated = conn.execute(text("""
                UPDATE model_training_set mts
                SET features = features || jsonb_build_object(
                    'fund_pe_inv', CASE WHEN f.trailing_pe > 0 THEN (100.0 / f.trailing_pe)::double precision ELSE 0 END,
                    'fund_forward_pe_inv', CASE WHEN f.forward_pe > 0 THEN (100.0 / f.forward_pe)::double precision ELSE 0 END,
                    'fund_market_cap_log', CASE WHEN f.market_cap > 0 THEN LN(GREATEST(f.market_cap, 1)) ELSE 0 END,
                    'fund_div_yield', COALESCE(f.dividend_yield, 0),
                    'fund_analyst_upside', CASE WHEN f.analyst_target_mean > 0 AND mts.entry_price > 0
                        THEN (f.analyst_target_mean / mts.entry_price - 1) * 100 ELSE 0 END,
                    'fund_analyst_rec_score', CASE
                        WHEN f.analyst_rec = 'strong_buy' THEN 5
                        WHEN f.analyst_rec = 'buy' THEN 4
                        WHEN f.analyst_rec = 'hold' THEN 3
                        WHEN f.analyst_rec = 'underperform' THEN 2
                        WHEN f.analyst_rec = 'sell' THEN 1
                        ELSE 3 END,
                    'fund_earnings_growth', COALESCE(f.earnings_growth * 100, 0),
                    'fund_revenue_growth', COALESCE(f.revenue_growth * 100, 0),
                    'fund_beta', COALESCE(f.beta, 1),
                    'fund_pct_from_52w_high', CASE WHEN f.high_52w > 0
                        THEN (mts.entry_price / f.high_52w - 1) * 100 ELSE 0 END
                )
                FROM fundamental_snapshots f
                WHERE f.symbol = mts.symbol
                  AND NOT (mts.features ? 'fund_pe_inv')
            """)).rowcount
            conn.commit()
            if updated > 0:
                print(f"[FundEnrich] Enriched {updated} rows with fundamental features.")
            return updated
    except Exception as e:
        print(f"[FundEnrich] Failed: {e}")
        return 0


def build_training_matrix(market: str = "AU", lookback_days: int = 1260) -> dict:
    """Build the full model_training_set from eod_ohl_history.

    Vectorized: pre-computes all 23 features in one pandas pass per symbol.
    Each symbol takes ~0.1s instead of ~15s.
    """
    from sqlalchemy import text
    from main import db_conn

    inserted = 0
    errors = 0

    try:
        with db_conn() as conn:
            cutoff_days_ago = date.today() - timedelta(days=30)
            symbols = conn.execute(
                text(
                    """SELECT DISTINCT symbol FROM eod_ohl_history o
                       WHERE o.market = :mkt
                         AND o.trade_date >= :lookback
                         AND EXISTS (
                           SELECT 1 FROM eod_ohl_history o2
                           WHERE o2.symbol = o.symbol
                             AND o2.trade_date >= :recent
                           LIMIT 1
                         )
                       ORDER BY symbol""",
                ),
                {
                    "mkt": market,
                    "lookback": (date.today() - timedelta(days=lookback_days)).isoformat(),
                    "recent": cutoff_days_ago.isoformat(),
                },
            ).fetchall()

            dead_count = conn.execute(
                text(
                    """SELECT COUNT(DISTINCT symbol) FROM eod_ohl_history
                       WHERE market = :mkt
                         AND trade_date >= :lookback
                         AND symbol NOT IN (
                           SELECT DISTINCT symbol FROM eod_ohl_history
                           WHERE market = :mkt AND trade_date >= :recent
                         )""",
                ),
                {"mkt": market, "lookback": (date.today() - timedelta(days=lookback_days)).isoformat(),
                 "recent": cutoff_days_ago.isoformat()},
            ).fetchone()
            if dead_count and dead_count[0] > 0:
                print(f"[Train] Skipping {dead_count[0]} dead tickers (no data in last 30 days).")

            yf_dead_path = os.path.join(os.path.dirname(__file__), "yfinance_dead_tickers.txt")
            yf_dead = set()
            if os.path.exists(yf_dead_path):
                with open(yf_dead_path) as f:
                    yf_dead = set(line.strip() for line in f if line.strip())
            symbols = [s for s in symbols if s[0] not in yf_dead]
            if yf_dead:
                print(f"[Train] Skipping {len(yf_dead)} tickers from yfinance dead-list.")
    except Exception as e:
        print(f"[Train] Failed loading symbol list: {e}")
        return {"rows_inserted": 0, "errors": 1}

    total_symbols = len(symbols)
    print(f"[Train] Build training matrix: {total_symbols} symbols, {lookback_days}d lookback (vectorized).")

    for sym_idx, (symbol,) in enumerate(symbols):
        t0 = time.time()
        try:
            from eodhd_backfill import get_ohlc_for_symbol

            df = get_ohlc_for_symbol(symbol)
            if df.empty or len(df) < FORWARD_WINDOW_DAYS + 50:
                continue

            fm = _build_feature_matrix(df)
            close = df["Close"].astype(float)

            rows_to_insert = []
            max_date_idx = len(fm) - FORWARD_WINDOW_DAYS - 1
            warmup = 50

            for idx in range(warmup, min(max_date_idx, len(fm) - 5)):
                signal_date = fm.index[idx].date()
                entry_price = float(close.iloc[idx])

                feat_row = {}
                for col in FEATURE_COLS:
                    v = fm[col].iloc[idx]
                    feat_row[col] = float(v) if not pd.isna(v) else 0.0

                max_i = min(idx + FORWARD_WINDOW_DAYS + 1, len(close))
                future = close.iloc[idx + 1 : max_i]
                if len(future) < 5:
                    continue

                fwd_close_63d = float(close.iloc[max_i - 1]) if max_i - 1 < len(close) else float(close.iloc[-1])
                fwd_peak_14d = float(future.iloc[:min(14, len(future))].max())
                fwd_peak_30d = float(future.iloc[:min(30, len(future))].max())
                fwd_peak_63d = float(future.max())
                fwd_trough_63d = float(future.min())

                fwd_ret = round((fwd_close_63d / entry_price - 1) * 100, 2)
                fwd_peak = round((fwd_peak_63d / entry_price - 1) * 100, 2)
                fwd_dd = round((fwd_trough_63d / entry_price - 1) * 100, 2)
                peak_14d_pct = round((fwd_peak_14d / entry_price - 1) * 100, 2)
                peak_30d_pct = round((fwd_peak_30d / entry_price - 1) * 100, 2)

                if abs(fwd_ret) < 0.001 and abs(fwd_peak) < 0.001:
                    continue

                hit_3pct = fwd_peak >= 3.0
                hit_3pct_14d = peak_14d_pct >= 3.0
                hit_3pct_30d = peak_30d_pct >= 3.0
                hit_5pct_63d = fwd_peak >= 5.0
                direction_correct = fwd_ret > 0

                rows_to_insert.append((symbol, market, signal_date, entry_price,
                    json.dumps(feat_row), fwd_ret, fwd_peak, fwd_dd,
                    hit_3pct, hit_3pct_14d, hit_3pct_30d, hit_5pct_63d, direction_correct))

            if rows_to_insert:
                try:
                    with db_conn() as conn:
                        for row in rows_to_insert:
                            conn.execute(text("""
                                INSERT INTO model_training_set
                                (symbol, market, signal_date, entry_price, features,
                                 forward_return_63d, forward_peak_return_63d, forward_max_drawdown_63d,
                                 hit_3pct, hit_3pct_14d, hit_3pct_30d, hit_5pct_63d, direction_correct)
                                VALUES (:s,:m,:d,:p,:f,:fr,:fp,:fd,:h,:h14,:h30,:h5,:dc)
                                ON CONFLICT (symbol, market, signal_date) DO UPDATE SET
                                entry_price=EXCLUDED.entry_price, features=EXCLUDED.features,
                                forward_return_63d=EXCLUDED.forward_return_63d,
                                forward_peak_return_63d=EXCLUDED.forward_peak_return_63d,
                                forward_max_drawdown_63d=EXCLUDED.forward_max_drawdown_63d,
                                hit_3pct=EXCLUDED.hit_3pct,
                                hit_3pct_14d=EXCLUDED.hit_3pct_14d,
                                hit_3pct_30d=EXCLUDED.hit_3pct_30d,
                                hit_5pct_63d=EXCLUDED.hit_5pct_63d,
                                direction_correct=EXCLUDED.direction_correct
                            """), {"s": row[0], "m": row[1], "d": row[2], "p": row[3], "f": row[4],
                                   "fr": row[5], "fp": row[6], "fd": row[7], "h": row[8],
                                   "h14": row[9], "h30": row[10], "h5": row[11], "dc": row[12]})
                        conn.commit()
                    inserted += len(rows_to_insert)
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        print(f"[Train] DB write failed for {symbol}: {e}")

            elapsed = time.time() - t0
            if (sym_idx + 1) % 100 == 0 or elapsed > 1.0:
                rate = (sym_idx + 1) / (time.time() - t0 + 1) * 3600
                est = (total_symbols - sym_idx - 1) / max(rate, 1)
                print(f"[Train] {sym_idx + 1}/{total_symbols} ({elapsed:.1f}s this) ~{rate:.0f}/hr, ETA {est:.0f}h")

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"[Train] Failed for {symbol}: {e}")

    print(f"[Train] Complete: {inserted} rows, {errors} errors, {total_symbols} symbols.")

    if inserted > 0:
        _enrich_fundamentals()

    return {"rows_inserted": inserted, "errors": errors, "symbols_processed": total_symbols}


def fit_model_weights(target_col: str = "forward_peak_return_63d", min_samples: int = MIN_TRAINING_SAMPLES) -> Optional[dict]:
    """Fit Ridge regression to predict peak return as continuous value.

    target_col: 'forward_peak_return_63d' (primary), or hit columns for classification fallback.
    """
    from sqlalchemy import text
    from main import db_conn

    try:
        with db_conn() as conn:
            rows = conn.execute(
                text(f"SELECT features, {target_col} FROM model_training_set "
                     "WHERE features IS NOT NULL AND forward_peak_return_63d IS NOT NULL "
                     "ORDER BY signal_date DESC LIMIT 50000")
            ).fetchall()
    except Exception as e:
        print(f"[Fit] DB read: {e}")
        return None

    if len(rows) < min_samples:
        print(f"[Fit] {len(rows)} < {min_samples} — insufficient")
        return None

    X_list, y_list = [], []
    for row in rows:
        try:
            feats = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
            x_row = [float(feats.get(c, 0)) for c in FEATURE_COLS]
            if any(np.isnan(v) for v in x_row):
                continue
            X_list.append(x_row)
            y_list.append(float(row[1]) if row[1] is not None else 0.0)
        except Exception:
            continue

    if len(X_list) < min_samples:
        print(f"[Fit] {len(X_list)} < {min_samples} — insufficient")
        return None

    X = np.array(X_list)
    y = np.array(y_list)
    y_mean = y.mean()
    y_std = y.std()

    print(f"[Fit] Regression: {len(X)} samples, mean peak return={y_mean:.2f}%, std={y_std:.2f}%")
    print(f"[Fit] Baseline: P(≥3%)={(y >= 3.0).mean()*100:.1f}%, P(≥5%)={(y >= 5.0).mean()*100:.1f}%")

    try:
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        model = Ridge(alpha=1.0)
        model.fit(X_scaled, y)
        y_pred = model.predict(X_scaled)
        r2 = float(1 - np.sum((y - y_pred)**2) / np.sum((y - y_mean)**2))

        coefs = dict(zip(FEATURE_COLS, model.coef_))
        model_type = "ridge_regression"

        if r2 <= 0:
            print(f"[Fit] R² = {r2:.4f} — features have no linear signal. Trying RandomForest.")
            from sklearn.ensemble import RandomForestRegressor
            rf = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1)
            rf.fit(X, y)
            y_pred = rf.predict(X)
            r2 = float(1 - np.sum((y - y_pred)**2) / np.sum((y - y_mean)**2))
            importances = rf.feature_importances_
            coefs = dict(zip(FEATURE_COLS, importances))
            model_type = "random_forest_regressor"

        today = date.today()
        with db_conn() as conn:
            for fname, coef in coefs.items():
                conn.execute(text("INSERT INTO model_weights_by_date "
                    "(trained_at,feature_name,weight,coefficient,model_type,sample_size,in_sample_hit_rate,notes) "
                    "VALUES (:ta,:fn,:w,:c,:mt,:ss,:ish,:nt) "
                    "ON CONFLICT (trained_at,feature_name) DO UPDATE SET "
                    "weight=EXCLUDED.weight,coefficient=EXCLUDED.coefficient,model_type=EXCLUDED.model_type,"
                    "sample_size=EXCLUDED.sample_size,in_sample_hit_rate=EXCLUDED.in_sample_hit_rate"),
                    {"ta": today, "fn": fname, "w": round(coef, 6), "c": round(coef, 6),
                     "mt": model_type, "ss": len(X), "ish": round(r2, 4),
                     "nt": f"{model_type}, R²={r2:.4f}, mean_ret={y_mean:.1f}%"})
            conn.commit()

        top = sorted(coefs.items(), key=lambda x: abs(x[1]), reverse=True)[:8]
        return {"status": "ok", "samples": len(X), "mean_return_pct": round(y_mean, 2),
                "r2": round(r2, 4),
                "baseline_hit_3pct": round((y >= 3.0).mean() * 100, 1),
                "baseline_hit_5pct": round((y >= 5.0).mean() * 100, 1),
                "top_features": top, "model_type": model_type}

    except ImportError:
        corr_w = {}
        for i, c in enumerate(FEATURE_COLS):
            if X[:, i].std() > 0:
                corr = np.corrcoef(X[:, i], y)[0, 1]
                corr_w[c] = round(corr, 6) if not np.isnan(corr) else 0.0
            else:
                corr_w[c] = 0.0

        today = date.today()
        with db_conn() as conn:
            for fn, w in corr_w.items():
                conn.execute(text("INSERT INTO model_weights_by_date "
                    "(trained_at,feature_name,weight,coefficient,sample_size,in_sample_hit_rate,notes) "
                    "VALUES (:ta,:fn,:w,:c,:ss,:ish,:nt) "
                    "ON CONFLICT (trained_at,feature_name) DO UPDATE SET "
                    "weight=EXCLUDED.weight,coefficient=EXCLUDED.coefficient,"
                    "sample_size=EXCLUDED.sample_size,in_sample_hit_rate=EXCLUDED.in_sample_hit_rate"),
                    {"ta": today, "fn": fn, "w": w, "c": w, "ss": len(X), "ish": 0,
                     "nt": "Correlation fallback (no sklearn)"})
            conn.commit()
        return {"status": "correlation_fallback", "samples": len(X), "mean_return_pct": round(y_mean, 2)}

    except Exception as e:
        print(f"[Fit] Failed: {e}")
        return None


def get_latest_weights() -> dict:
    from sqlalchemy import text
    from main import db_conn
    try:
        with db_conn() as conn:
            rows = conn.execute(text(
                "SELECT feature_name, weight FROM model_weights_by_date "
                "WHERE trained_at=(SELECT MAX(trained_at) FROM model_weights_by_date) "
                "ORDER BY ABS(weight) DESC")).fetchall()
        return {r[0]: r[1] for r in rows} if rows else {}
    except Exception:
        return {}


def daily_training_pipeline() -> dict:
    print("[TrainPipeline] Starting daily training pipeline...")
    t0 = time.time()
    matrix = build_training_matrix()
    results = {}
    for target, label in [("forward_peak_return_63d", "63d peak %"),
                           ("forward_peak_return_63d", "63d peak %"),
                           ("hit_3pct_14d", "14d hit"),
                           ("hit_3pct_30d", "30d hit")]:
        print(f"[TrainPipeline] Fitting {label}...")
        results[label] = fit_model_weights(target_col=target)
    elapsed = round(time.time() - t0, 1)
    print(f"[TrainPipeline] Done in {elapsed}s.")
    return {"matrix": matrix, "models": results, "elapsed_s": elapsed}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["matrix","fit","pipeline","weights"], default="pipeline")
    ap.add_argument("--lookback", type=int, default=1260)
    args = ap.parse_args()
    if args.mode == "matrix":
        r = build_training_matrix(lookback_days=args.lookback)
    elif args.mode == "fit":
        r = fit_model_weights()
    elif args.mode == "pipeline":
        r = daily_training_pipeline()
    elif args.mode == "weights":
        r = get_latest_weights()
    print(json.dumps(r, indent=2, default=str))

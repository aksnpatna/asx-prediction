"""Model Training — Daily regression pipeline for ASX feature → outcome learning.

Workflow (called daily by scheduler):
    1. build_training_matrix()    — compute features + outcomes from eod_ohl_history
    2. fit_model_weights()        — ensemble regression: Ridge + LightGBM + RF
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
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yfinance_service import YFinanceService

FORWARD_WINDOW_DAYS = 63
HIT_THRESHOLD_PCT = 8.0
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
    # Historical fundamental ratios (yfinance income/balance/cashflow, 4yr)
    "fund_hist_roe", "fund_hist_debt_equity", "fund_hist_gross_margin",
    "fund_hist_op_margin", "fund_hist_fcf_yield",
    # Market regime features
    "regime_sma_alignment", "vwap_position", "gap_detection",
    # Volatility structure features
    "vol_regime_ratio", "garman_klass_vol", "parkinson_vol",
    # Time-series structure features
    "autocorr_5d", "skewness_20d", "kurtosis_20d", "max_drawdown_20d",
    # Macro-adjacent features (computed from XJO ASX200 data, zero API cost)
    "xjo_momentum_63d", "xjo_sma_position", "xjo_vol_20d",
    "relative_strength_vs_xjo",
    # Pure macro features (VIX, copper/gold, yield curve, AUD/USD) — $0 API cost
    "vix_level", "copper_gold_ratio", "yield_curve_slope", "aud_usd_trend",
    # New timing features (SMSF v2)
    "mean_reversion_score", "squeeze_duration", "rsi_during_squeeze",
]


def _build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized: compute all features for every row in df in one pass. O(n)."""
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    open_ = df["Open"].astype(float)
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
    # Clip volume ratios to prevent extreme outliers (e.g. $0 avg_vol for
    # delisted / micro-cap stocks) from corrupting the scaler and model weights.
    fm["volume_spike"] = (vol / (vol_avg20 + 1e-9)).clip(0.01, 50.0)
    fm["volume_ratio"] = ((vol - vol_avg20).abs() / (vol_avg20 + 1e-9)).clip(0.0, 50.0)

    obv_delta = np.sign(close.diff()).fillna(0) * vol
    obv = obv_delta.cumsum()
    obv_sma = obv.rolling(20).mean()
    fm["obv_bullish"] = (obv > obv_sma).astype(float)

    mf = ((close - low) - (high - close)) / (high - low + 1e-9)
    mf_vol = mf * vol
    cmf_series = (mf_vol.rolling(20).sum() / (vol.rolling(20).sum() + 1e-9)).clip(-5.0, 5.0)
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
    fm["rsi_vol_adj"] = (fm["rsi"].fillna(50) / (fm["hv_20d"].fillna(0.2) + 1.0)).clip(0, 1000)
    fm["mom_per_vol"] = (fm["momentum_20d"].fillna(0) / (fm["hv_20d"].fillna(0.2) + 1e-9)).clip(-200, 200)
    fm["dist_from_sma50"] = (close - sma50.fillna(close)) / (sma50.fillna(close) + 1e-9) * 100
    rsi_z = (fm["rsi"].fillna(50) - 50) / (fm["rsi"].rolling(63).std().fillna(15) + 1e-9)
    macd_z = fm["macd_hist"].fillna(0) / (fm["macd_hist"].rolling(63).std().fillna(0.01) + 1e-9)
    fm["rsi_macd_div"] = (rsi_z - macd_z).clip(-100, 100)
    fm["vol_confirm"] = fm["volume_spike"].fillna(1) * np.sign(fm["momentum_20d"].fillna(0))
    fm["bb_squeeze_ratio"] = (fm["bb_width"].fillna(0.05) / (fm["atr_pct"].fillna(0.01) + 1e-9)).clip(0.1, 50.0)

    # ── Mean Reversion Score (SMSF v2) ─────────────────────────────────────
    rsi_low_10d = rsi_series.rolling(10).min()
    rsi_now = rsi_series.iloc[-1] if len(rsi_series) > 0 else 50
    rsi_recovering = ((rsi_low_10d < 35) & (rsi_now > 40)).astype(float)
    vol_20d_avg = vol.rolling(20).mean().shift(1)
    vol_dry_up = (1 - vol / (vol_20d_avg + 1e-9)).clip(0, 1)
    high_52w = close.rolling(252).max()
    pct_from_high = (close / (high_52w + 1e-9) - 1) * 100
    in_sweet_spot = ((-40 <= pct_from_high) & (pct_from_high <= -15)).astype(float)
    daily_range = (high - low) / (close + 1e-9)
    range_narrowing = (daily_range.rolling(5).mean() < daily_range.rolling(60).mean() * 0.7).astype(float)
    fm["mean_reversion_score"] = (
        rsi_recovering * 0.35 + vol_dry_up * 0.25 +
        in_sweet_spot * 0.25 + range_narrowing * 0.15
    ).fillna(0).clip(0, 1)

    # ── Squeeze Duration + RSI during squeeze (SMSF v2) ────────────────────
    bb_w = fm["bb_width"].fillna(0.05)
    bb_w_threshold = bb_w.rolling(252).quantile(0.20).fillna(bb_w.median())
    in_squeeze_s = (bb_w < bb_w_threshold).astype(float)
    squeeze_dur = in_squeeze_s * (in_squeeze_s.groupby(in_squeeze_s.diff().ne(0).cumsum()).cumsum())
    fm["squeeze_duration"] = squeeze_dur.fillna(0)
    fm["rsi_during_squeeze"] = (fm["rsi"].fillna(50) * in_squeeze_s).rolling(10, min_periods=1).mean().fillna(50)


    # ── Market regime features ────────────────────────────────────────────────
    sma_alignment = np.where(close > sma20, 0.33, 0) + np.where(sma20 > sma50, 0.33, 0) + np.where(sma50 > sma200, 0.34, 0)
    fm["regime_sma_alignment"] = sma_alignment

    vwap = (close * vol).rolling(20).sum() / (vol.rolling(20).sum() + 1e-9)
    fm["vwap_position"] = close / (vwap + 1e-9)

    overnight_gap = (open_ - close.shift(1)) / (close.shift(1) + 1e-9)
    fm["gap_detection"] = overnight_gap.rolling(5).mean().fillna(0)

    # ── Volatility structure features ─────────────────────────────────────────
    hv_5d = rets.rolling(5).std() * np.sqrt(252)
    fm["vol_regime_ratio"] = hv_5d / (fm["hv_20d"] + 1e-9)

    gk_var = 0.5 * (np.log(high / low)) ** 2 - (2 * np.log(2) - 1) * (np.log(close / open_)) ** 2
    fm["garman_klass_vol"] = np.sqrt(np.maximum(gk_var.rolling(20).mean() * 252, 0))

    parkinson_var = (1 / (4 * np.log(2))) * (np.log(high / low)) ** 2
    fm["parkinson_vol"] = np.sqrt(np.maximum(parkinson_var.rolling(20).mean() * 252, 0))

    # ── Time-series structure features ────────────────────────────────────────
    fm["autocorr_5d"] = rets.rolling(20).corr(rets.shift(5))

    fm["skewness_20d"] = rets.rolling(20).skew()
    fm["kurtosis_20d"] = rets.rolling(20).kurt()
    fm["max_drawdown_20d"] = (close.rolling(20).min() / close.shift(19) - 1) * 100

    # ── Fill NaN values with reasonable defaults ─────────────────────────────
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
    fm["regime_sma_alignment"] = fm["regime_sma_alignment"].fillna(0.5)
    fm["vwap_position"] = fm["vwap_position"].fillna(1.0)
    fm["gap_detection"] = fm["gap_detection"].fillna(0)
    fm["vol_regime_ratio"] = fm["vol_regime_ratio"].fillna(1.0)
    fm["garman_klass_vol"] = fm["garman_klass_vol"].fillna(0)
    fm["parkinson_vol"] = fm["parkinson_vol"].fillna(0)
    fm["autocorr_5d"] = fm["autocorr_5d"].fillna(0)
    fm["skewness_20d"] = fm["skewness_20d"].fillna(0)
    fm["kurtosis_20d"] = fm["kurtosis_20d"].fillna(0)
    fm["max_drawdown_20d"] = fm["max_drawdown_20d"].fillna(0)

    return fm


_XJO_CACHE = None
_MACRO_SERIES_CACHE = None
import threading as _thr
_xjo_cache_lock = _thr.Lock()
_macro_series_lock = _thr.Lock()

def _get_xjo_data():
    """Fetch ASX200 (XJO) benchmark data once per training run. Thread-safe cache.
    Tries EODHD first, falls back to yfinance ^AXJO.AX."""
    global _XJO_CACHE
    with _xjo_cache_lock:
        if _XJO_CACHE is not None:
            return _XJO_CACHE
    try:
        from eodhd_backfill import get_ohlc_for_symbol
        xjo = get_ohlc_for_symbol("XJO")
        if not xjo.empty:
            xjo = xjo.sort_index()
            _XJO_CACHE = xjo
            return xjo
    except Exception:
        pass
    # Fallback: yfinance ^AXJO (ASX200 index)
    try:
        import yfinance as yf
        xjo_yf = yf.download("^AXJO", period="10y", progress=False)
        if not xjo_yf.empty:
            # Flatten MultiIndex columns (auto_adjust=True creates (Close, ^AXJO))
            if isinstance(xjo_yf.columns, pd.MultiIndex):
                xjo_yf.columns = xjo_yf.columns.get_level_values(0)
            xjo_yf = xjo_yf[["Open", "High", "Low", "Close", "Volume"]].sort_index()
            _XJO_CACHE = xjo_yf
            return xjo_yf
    except Exception:
        pass
    return pd.DataFrame()


def _get_macro_series() -> dict:
    """Fetch VIX, copper, gold, AUD/USD, AU yield curve once per training run.
    
    Returns dict with keys: vix, copper, gold, aud_usd, au_yield_slope
    Each value is a pd.Series aligned by date index.
    Thread-safe cache.
    """
    global _MACRO_SERIES_CACHE
    with _macro_series_lock:
        if _MACRO_SERIES_CACHE is not None:
            return _MACRO_SERIES_CACHE

    result = {}
    end = date.today()
    start = end - timedelta(days=9 * 365 + 30)

    tickers = {"vix": "^VIX", "copper": "HG=F", "gold": "GC=F", "aud_usd": "AUDUSD=X"}
    for key, ticker in tickers.items():
        try:
            df = yf.download(ticker, start=start, end=end, progress=False)
            if not df.empty and not df["Close"].empty:
                s = df["Close"].squeeze()
                s = s.resample("D").ffill()
                s.index = pd.to_datetime(s.index).normalize()
                result[key] = s
        except Exception:
            pass

    if "copper" in result and "gold" in result:
        aligned = pd.concat([result["copper"].rename("copper"), result["gold"].rename("gold")], axis=1)
        ratio = aligned["copper"] / (aligned["gold"] + 1e-9)
        ratio = ratio.dropna()
        result["copper_gold_ratio"] = ratio

    try:
        from fredapi import Fred
        import os
        fred_key = os.getenv("FRED_API_KEY", "55689724e13717f08cb5c36bf7d20921")
        fred = Fred(api_key=fred_key)
        
        au_10y = fred.get_series("IRLTLT01AUM156N", observation_start=start.isoformat(), observation_end=end.isoformat())
        au_3m = fred.get_series("IR3TIB01AUM156N", observation_start=start.isoformat(), observation_end=end.isoformat())
        
        if len(au_10y) > 0 and len(au_3m) > 0:
            au_10y.index = pd.to_datetime(au_10y.index).normalize()
            au_3m.index = pd.to_datetime(au_3m.index).normalize()
            slope = au_10y.subtract(au_3m, fill_value=None)
            slope = slope.resample("D").ffill().fillna(method="ffill")
            result["yield_curve_slope"] = slope
    except Exception:
        pass

    _MACRO_SERIES_CACHE = result
    return result


def _add_macro_features(fm: pd.DataFrame, symbol_df: pd.DataFrame) -> pd.DataFrame:
    """Enrich feature matrix with XJO benchmark-relative and pure macro features."""
    MACRO_COLS = [
        "xjo_momentum_63d", "xjo_sma_position", "xjo_vol_20d",
        "relative_strength_vs_xjo",
        "vix_level", "copper_gold_ratio", "yield_curve_slope", "aud_usd_trend",
    ]
    
    xjo = _get_xjo_data()
    macro = _get_macro_series()

    fm_dates = pd.to_datetime(fm.index).normalize()

    xjo_aligned = None
    if not xjo.empty:
        xjo_close = xjo["Close"].astype(float)
        xjo_aligned = xjo_close.reindex(fm.index, method="ffill")
        xjo_aligned = xjo_aligned.fillna(method="bfill")

        fm["xjo_momentum_63d"] = xjo_aligned.pct_change(63).fillna(0).astype(float) * 100
        xjo_sma200 = xjo_close.rolling(200).mean()
        xjo_sma_aligned = xjo_sma200.reindex(fm.index, method="ffill").fillna(xjo_aligned)
        fm["xjo_sma_position"] = (xjo_aligned > xjo_sma_aligned).astype(float)
        fm["xjo_vol_20d"] = xjo_aligned.pct_change().rolling(20).std().fillna(0).astype(float) * np.sqrt(252)

        xjo_mom = xjo_aligned.pct_change(63).fillna(0) * 100
        sym_close = symbol_df["Close"].astype(float)
        sym_mom = sym_close.pct_change(63).fillna(0) * 100
        fm["relative_strength_vs_xjo"] = (sym_mom - xjo_mom).fillna(0).astype(float)
    else:
        fm["xjo_momentum_63d"] = 0.0
        fm["xjo_sma_position"] = 0.0
        fm["xjo_vol_20d"] = 0.0
        fm["relative_strength_vs_xjo"] = 0.0

    if "vix" in macro:
        vix_aligned = macro["vix"].reindex(fm_dates, method="ffill")
        vix_aligned = vix_aligned.fillna(method="bfill").fillna(20).values
        fm["vix_level"] = vix_aligned.astype(float)
    else:
        fm["vix_level"] = 20.0

    if "copper_gold_ratio" in macro:
        cg_aligned = macro["copper_gold_ratio"].reindex(fm_dates, method="ffill")
        cg_aligned = cg_aligned.fillna(method="bfill").fillna(0.004).values
        fm["copper_gold_ratio"] = cg_aligned.astype(float)
    else:
        fm["copper_gold_ratio"] = 0.004

    if "yield_curve_slope" in macro:
        yc_aligned = macro["yield_curve_slope"].reindex(fm_dates, method="ffill")
        yc_aligned = yc_aligned.fillna(method="bfill").fillna(0).values
        fm["yield_curve_slope"] = yc_aligned.astype(float)
    else:
        fm["yield_curve_slope"] = 0.0

    if "aud_usd" in macro:
        aud_aligned = macro["aud_usd"].reindex(fm_dates, method="ffill")
        aud_aligned = aud_aligned.fillna(method="bfill").fillna(0.65).values
        fm["aud_usd_trend"] = np.log(aud_aligned).astype(float)
    else:
        fm["aud_usd_trend"] = np.log(0.65)

    for col in MACRO_COLS:
        fm[col] = fm[col].fillna(0)

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
                  AND (NOT (mts.features ? 'fund_pe_inv') OR (mts.features->>'fund_pe_inv')::numeric = 0)
                  AND f.trailing_pe IS NOT NULL
            """)).rowcount
            conn.commit()
            if updated > 0:
                print(f"[FundEnrich] Enriched {updated} rows with fundamental features.")
            return updated
    except Exception as e:
        print(f"[FundEnrich] Failed: {e}")
        return 0


def build_training_matrix(market: str = "AU", lookback_days: int = 2268, incremental: bool = True) -> dict:
    """Build the full model_training_set from eod_ohl_history.

    Vectorized: pre-computes all features in one pandas pass per symbol.
    Default lookback: 2268 days (~9 years, covers 2017-2026).
    """
    from sqlalchemy import text
    from main import db_conn

    inserted = 0
    errors = 0

    try:
        with db_conn() as conn:
            cutoff_days_ago = date.today() - timedelta(days=30)
            
            # Fetch max dates for incremental mode
            max_dates = {}
            if incremental:
                max_date_rows = conn.execute(text(
                    "SELECT symbol, MAX(signal_date) FROM model_training_set WHERE market = :mkt GROUP BY symbol"
                ), {"mkt": market}).fetchall()
                for row in max_date_rows:
                    if row[1]:
                        max_dates[row[0]] = row[1] if isinstance(row[1], date) else date.fromisoformat(str(row[1]).split()[0])

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

            yf_dead = YFinanceService.get_dead_set()
            symbols = [s for s in symbols if s[0] not in yf_dead]
            if yf_dead:
                print(f"[Train] Skipping {len(yf_dead)} tickers from yfinance dead-list.")
    except Exception as e:
        print(f"[Train] Failed loading symbol list: {e}")
        return {"rows_inserted": 0, "errors": 1}

    total_symbols = len(symbols)
    print(f"[Train] Build training matrix: {total_symbols} symbols, {lookback_days}d lookback (incremental={incremental}).")

    for sym_idx, (symbol,) in enumerate(symbols):
        t0 = time.time()
        try:
            from eodhd_backfill import get_ohlc_for_symbol

            df = get_ohlc_for_symbol(symbol)
            if df.empty or len(df) < FORWARD_WINDOW_DAYS + 50:
                continue

            # ── Exclude stale sessions (volume==0) — strategy doc line 249 ──
            # Non-trading days (suspensions/halts) pollute features + labels.
            # 17.2% of eod_ohl_history rows have volume=0.
            if "Volume" in df.columns:
                df = df[df["Volume"] > 0]
                if len(df) < FORWARD_WINDOW_DAYS + 50:
                    continue

            fm = _build_feature_matrix(df)
            fm = _add_macro_features(fm, df)
            close = df["Close"].astype(float)

            rows_to_insert = []
            max_date_idx = len(fm) - FORWARD_WINDOW_DAYS - 1
            warmup = 50

            cutoff_date = None
            if incremental and symbol in max_dates:
                # We want to re-process the last 65 days of known signals to update forward-looking labels (like 63d returns)
                cutoff_date = max_dates[symbol] - timedelta(days=65)

            for idx in range(warmup, min(max_date_idx, len(fm) - 5)):
                signal_date = fm.index[idx].date()
                if cutoff_date and signal_date < cutoff_date:
                    continue

                signal_date = fm.index[idx].date()
                entry_price = float(close.iloc[idx])

                feat_row = {}
                for col in FEATURE_COLS:
                    if col not in fm.columns:
                        feat_row[col] = 0.0
                        continue
                    v = fm[col].iloc[idx]
                    if pd.isna(v) or np.isinf(v):
                        feat_row[col] = 0.0
                    else:
                        feat_row[col] = round(float(v), 8)

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

                # Cap extreme returns from penny stocks / bad data
                if abs(fwd_ret) > 500 or abs(fwd_peak) > 500:
                    continue
                if abs(fwd_ret) < 0.001 and abs(fwd_peak) < 0.001:
                    continue

                hit_3pct = fwd_peak >= 3.0
                hit_3pct_14d = peak_14d_pct >= 3.0
                hit_3pct_30d = peak_30d_pct >= 3.0
                hit_5pct_63d = fwd_peak >= 5.0
                hit_8pct_63d = fwd_peak >= 8.0
                hit_10pct_63d = fwd_peak >= 10.0
                direction_correct = fwd_ret > 0

                # ── Path-aware labels (SMSF v2): hit target BEFORE hitting stop ──
                hit_5pct_before_m5pct = fwd_peak >= 5.0 and fwd_dd > -5.0
                hit_8pct_before_m8pct = fwd_peak >= 8.0 and fwd_dd > -8.0
                close_5pct_63d = fwd_ret >= 5.0

                rows_to_insert.append((symbol, market, signal_date, entry_price,
                    json.dumps(feat_row), fwd_ret, fwd_peak, fwd_dd,
                    hit_3pct, hit_3pct_14d, hit_3pct_30d, hit_5pct_63d,
                    hit_8pct_63d, hit_10pct_63d, direction_correct,
                    hit_5pct_before_m5pct, hit_8pct_before_m8pct, close_5pct_63d))

            if rows_to_insert:
                try:
                    with db_conn() as conn:
                        for row in rows_to_insert:
                            conn.execute(text("""
                                INSERT INTO model_training_set
                                (symbol, market, signal_date, entry_price, features,
                                 forward_return_63d, forward_peak_return_63d, forward_max_drawdown_63d,
                                 hit_3pct, hit_3pct_14d, hit_3pct_30d, hit_5pct_63d,
                                 hit_8pct_63d, hit_10pct_63d, direction_correct,
                                 hit_5pct_before_m5pct, hit_8pct_before_m8pct, close_5pct_63d)
                                VALUES (:s,:m,:d,:p,:f,:fr,:fp,:fd,:h,:h14,:h30,:h5,:h8,:h10,:dc,
                                        :h5m5,:h8m8,:c5)
                                ON CONFLICT (symbol, market, signal_date) DO UPDATE SET
                                entry_price=EXCLUDED.entry_price, features=EXCLUDED.features,
                                forward_return_63d=EXCLUDED.forward_return_63d,
                                forward_peak_return_63d=EXCLUDED.forward_peak_return_63d,
                                forward_max_drawdown_63d=EXCLUDED.forward_max_drawdown_63d,
                                hit_3pct=EXCLUDED.hit_3pct,
                                hit_3pct_14d=EXCLUDED.hit_3pct_14d,
                                hit_3pct_30d=EXCLUDED.hit_3pct_30d,
                                hit_5pct_63d=EXCLUDED.hit_5pct_63d,
                                hit_8pct_63d=EXCLUDED.hit_8pct_63d,
                                hit_10pct_63d=EXCLUDED.hit_10pct_63d,
                                direction_correct=EXCLUDED.direction_correct,
                                hit_5pct_before_m5pct=EXCLUDED.hit_5pct_before_m5pct,
                                hit_8pct_before_m8pct=EXCLUDED.hit_8pct_before_m8pct,
                                close_5pct_63d=EXCLUDED.close_5pct_63d
                            """), {"s": row[0], "m": row[1], "d": row[2], "p": row[3], "f": row[4],
                                   "fr": row[5], "fp": row[6], "fd": row[7], "h": row[8],
                                   "h14": row[9], "h30": row[10], "h5": row[11],
                                   "h8": row[12], "h10": row[13], "dc": row[14],
                                   "h5m5": row[15], "h8m8": row[16], "c5": row[17]})
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


def fit_model_weights(target_col: str = "hit_8pct_before_m8pct", min_samples: int = MIN_TRAINING_SAMPLES) -> Optional[dict]:
    """Fit ensemble regression (Ridge + LightGBM + RandomForest) on +8% before -8% path-aware label.

    Uses chronological train/test split: earliest 80% train, most recent 20% validate.
    Blends predictions: 0.30×Ridge + 0.40×LightGBM + 0.30×RandomForest.
    Falls back to correlation if sklearn unavailable.
    """
    from sqlalchemy import text
    from main import db_conn

    try:
        with db_conn() as conn:
            rows = conn.execute(
                text(f"SELECT features, CAST({target_col} AS INTEGER) FROM model_training_set "
                     "WHERE features IS NOT NULL AND forward_peak_return_63d IS NOT NULL "
                     "AND ABS(forward_peak_return_63d) <= 500 "
                     "AND ABS(forward_return_63d) <= 500 "
                     "ORDER BY signal_date ASC LIMIT 100000")
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
            if any(np.isnan(v) or np.isinf(v) for v in x_row):
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

    # ── Drop zero-variance features (fundamental + XJO not populated yet) ──
    nonzero_variance = np.std(X, axis=0) > 1e-12
    active_features = [fe for fe, nz in zip(FEATURE_COLS, nonzero_variance) if nz]
    dropped = len(FEATURE_COLS) - len(active_features)
    if dropped > 0:
        X = X[:, nonzero_variance]
        dropped_names = [f for f in FEATURE_COLS if f not in active_features]
        print(f"[Fit] Dropped {dropped}/{len(FEATURE_COLS)} zero-variance features "
              f"(kept {len(active_features)}): {', '.join(dropped_names[:8])}")
    else:
        active_features = FEATURE_COLS
    FEATURE_COLS_ACTIVE = active_features

    n_train = int(len(X) * 0.8)
    X_train, X_test = X[:n_train], X[n_train:]
    y_train, y_test = y[:n_train], y[n_train:]

    # ── Sample weighting by recency (exponential decay, half-life = 1 year) ──
    # Rows are sorted by signal_date ASC. Compute weight based on position
    # relative to the full training span (not raw array index).
    n_all = len(X)
    total_days = 9 * 365  # approximate 9yr span
    half_life = 365  # 1 calendar year
    positions = np.arange(n_all)[::-1]  # newest sample = 0 position, oldest = n_all
    # Map position to approximate days old
    days_old = positions / n_all * total_days
    sample_weights = np.power(0.5, days_old / half_life)
    sample_weights = sample_weights / sample_weights.mean()
    w_train = sample_weights[:n_train]
    w_test = sample_weights[n_train:]

    y_mean = y_train.mean()
    y_std = y_train.std()

    print(f"[Fit] Ensemble: {len(X_train)} train, {len(X_test)} test samples, "
          f"hit_8pct rate={y_train.mean()*100:.1f}%, std={y_std:.2f}")
    print(f"[Fit] Sample weights: recent10=%s, oldest10=%s" %
          (",".join(f"{w:.2f}" for w in w_train[-10:]),
           ",".join(f"{w:.2f}" for w in w_train[:10])))

    try:
        from sklearn.linear_model import Ridge
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.preprocessing import StandardScaler

        ensemble_details = {}
        blend_weights = {"ridge": 0.30, "lightgbm": 0.40, "random_forest": 0.30}

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        # ── Ridge ──────────────────────────────────────────────────────────
        ridge = Ridge(alpha=1.0)
        ridge.fit(X_train_scaled, y_train)
        ridge_pred_train = ridge.predict(X_train_scaled)
        ridge_pred_test = ridge.predict(X_test_scaled)
        ridge_coefs = dict(zip(FEATURE_COLS_ACTIVE, ridge.coef_))
        ensemble_details["ridge"] = {
            "train_r2": round(float(1 - np.sum((y_train - ridge_pred_train)**2) / np.sum((y_train - y_mean)**2)), 4),
            "test_r2": round(float(1 - np.sum((y_test - ridge_pred_test)**2) / np.sum((y_test - y_test.mean())**2)), 4),
        }

        # ── LightGBM (tuned, with sample weights and validation) ───────────
        lgbm_train_r2, lgbm_test_r2 = 0.0, 0.0
        lgbm_importances = {}
        lgbm_pred_test = np.zeros_like(y_test)
        try:
            import lightgbm as lgb
            # Tuned params: reduced depth, more leaves, stronger regularization
            lgbm = lgb.LGBMRegressor(
                max_depth=5, num_leaves=31, n_estimators=300,
                learning_rate=0.03, reg_alpha=0.3, reg_lambda=2.0,
                min_child_samples=30, min_split_gain=0.001,
                subsample=0.7, colsample_bytree=0.7,
                random_state=42, n_jobs=-1, verbose=-1,
            )
            lgbm.fit(X_train, y_train, sample_weight=w_train)
            lgbm_pred_train = lgbm.predict(X_train)
            lgbm_pred_test = lgbm.predict(X_test)
            lgbm_train_r2 = float(1 - np.sum((y_train - lgbm_pred_train)**2) / np.sum((y_train - y_mean)**2))
            lgbm_test_r2 = float(1 - np.sum((y_test - lgbm_pred_test)**2) / np.sum((y_test - y_test.mean())**2))
            lgbm_importances = dict(zip(FEATURE_COLS_ACTIVE, lgbm.feature_importances_))
            ensemble_details["lightgbm"] = {
                "train_r2": round(lgbm_train_r2, 4),
                "test_r2": round(lgbm_test_r2, 4),
            }
            print(f"[Fit] LightGBM train R²={lgbm_train_r2:.4f}, test R²={lgbm_test_r2:.4f}")
        except ImportError:
            print("[Fit] LightGBM not installed — using Ridge for that weight share.")
            blend_weights["ridge"] += blend_weights["lightgbm"]
            blend_weights["lightgbm"] = 0.0

        # ── RandomForest ───────────────────────────────────────────────────
        rf = RandomForestRegressor(
            n_estimators=200, max_depth=8, min_samples_leaf=20,
            random_state=42, n_jobs=-1,
        )
        rf.fit(X_train, y_train)
        rf_pred_train = rf.predict(X_train)
        rf_pred_test = rf.predict(X_test)
        rf_train_r2 = float(1 - np.sum((y_train - rf_pred_train)**2) / np.sum((y_train - y_mean)**2))
        rf_test_r2 = float(1 - np.sum((y_test - rf_pred_test)**2) / np.sum((y_test - y_test.mean())**2))
        rf_importances = dict(zip(FEATURE_COLS_ACTIVE, rf.feature_importances_))
        ensemble_details["random_forest"] = {
            "train_r2": round(rf_train_r2, 4),
            "test_r2": round(rf_test_r2, 4),
        }

        # ── Blended prediction ─────────────────────────────────────────────
        blend_pred_test = (
            blend_weights["ridge"] * ridge_pred_test +
            blend_weights["lightgbm"] * lgbm_pred_test +
            blend_weights["random_forest"] * rf_pred_test
        )
        blend_train_r2 = (
            blend_weights["ridge"] * float(ensemble_details["ridge"]["train_r2"]) +
            blend_weights["lightgbm"] * float(ensemble_details.get("lightgbm", {}).get("train_r2", 0)) +
            blend_weights["random_forest"] * float(ensemble_details["random_forest"]["train_r2"])
        )
        blend_test_r2 = float(1 - np.sum((y_test - blend_pred_test)**2) / np.sum((y_test - y_test.mean())**2))

        # ── Combined feature importance ────────────────────────────────────
        ridge_max = max(abs(v) for v in ridge_coefs.values()) or 1e-9
        lgbm_max = max(abs(v) for v in lgbm_importances.values()) or 1e-9
        rf_max = max(abs(v) for v in rf_importances.values()) or 1e-9

        combined_coefs = {}
        for fname in FEATURE_COLS_ACTIVE:
            r_w = abs(ridge_coefs.get(fname, 0))
            l_w = abs(lgbm_importances.get(fname, 0))
            rf_w = abs(rf_importances.get(fname, 0))
            combined_coefs[fname] = round(
                blend_weights["ridge"] * r_w / ridge_max +
                blend_weights["lightgbm"] * l_w / lgbm_max +
                blend_weights["random_forest"] * rf_w / rf_max, 6
            )

        model_type = "ensemble_ridge_lgbm_rf"
        r2 = blend_test_r2

        print(f"[Fit] Ensemble test R²={blend_test_r2:.4f} (Ridge={ensemble_details['ridge']['test_r2']}, "
              f"LightGBM={ensemble_details.get('lightgbm', {}).get('test_r2', 'N/A')}, "
              f"RF={ensemble_details['random_forest']['test_r2']})")

        today = date.today()
        note_text = f"Ens, OOS R²={blend_test_r2:.2f}"[:200]
        blend_note = f"R:{blend_weights['ridge']:.2f}/L:{blend_weights['lightgbm']:.2f}/RF:{blend_weights['random_forest']:.2f}"[:200]
        with db_conn() as conn:
            for fname, coef in ridge_coefs.items():
                conn.execute(text("INSERT INTO model_weights_by_date "
                    "(trained_at,feature_name,weight,coefficient,model_type,sample_size,in_sample_hit_rate,notes) "
                    "VALUES (:ta,:fn,:w,:c,:mt,:ss,:ish,:nt) "
                    "ON CONFLICT (trained_at,feature_name) DO UPDATE SET "
                    "weight=EXCLUDED.weight,coefficient=EXCLUDED.coefficient,model_type=EXCLUDED.model_type,"
                    "sample_size=EXCLUDED.sample_size,in_sample_hit_rate=EXCLUDED.in_sample_hit_rate"),
                    {"ta": today, "fn": fname, "w": round(coef, 6), "c": round(coef, 6),
                     "mt": "ridge"[:20], "ss": len(X), "ish": round(blend_test_r2, 4),
                     "nt": note_text})
            conn.commit()

            scaler_stats = json.dumps({
                "mean": scaler.mean_.tolist(),
                "scale": scaler.scale_.tolist(),
                "feature_order": FEATURE_COLS_ACTIVE,
            })
            conn.execute(text(
                "INSERT INTO model_weights_by_date (trained_at, feature_name, weight, coefficient, model_type, notes) "
                "VALUES (:ta, '__scaler_stats__', 0, 0, 'scaler', :nt) "
                "ON CONFLICT (trained_at, feature_name) DO UPDATE SET notes=EXCLUDED.notes, model_type='scaler'"
            ), {"ta": today, "nt": scaler_stats})
            conn.commit()

        top = sorted(ridge_coefs.items(), key=lambda x: abs(x[1]), reverse=True)[:8]
        return {"status": "ok", "samples": len(X), "hit_8pct_rate": round(y.mean() * 100, 1),
                "r2": round(r2, 4),
                "test_r2": round(blend_test_r2, 4),
                "baseline_hit_8pct": round((y >= 1).mean() * 100, 1),
                "sample_weights": {"recent10_mean": round(w_train[-10:].mean(), 3), "oldest10_mean": round(w_train[:10].mean(), 3)},
                "top_features": top, "model_type": model_type,
                "ensemble_details": ensemble_details,
                "blend_weights": blend_weights}

    except ImportError:
        corr_w = {}
        for i, c in enumerate(FEATURE_COLS_ACTIVE):
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
            # Prefer logistic classifier weights (newest), fallback to any
            rows = conn.execute(text(
                "SELECT feature_name, weight FROM model_weights_by_date "
                "WHERE model_type='logistic' "
                "AND trained_at=(SELECT MAX(trained_at) FROM model_weights_by_date WHERE model_type='logistic') "
                "ORDER BY ABS(weight) DESC")).fetchall()
            if not rows:
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
    matrix = build_training_matrix(incremental=True)
    results = {}
    # Primary classifier: LogisticRegression with decile-based evaluation
    try:
        from smsf_classifier import train_classifier
        print("[TrainPipeline] Fitting LogisticRegression classifier...")
        results["classifier"] = train_classifier(target_col="hit_8pct_before_m8pct")
    except Exception as e:
        print(f"[TrainPipeline] Classifier failed: {e}")
        results["classifier"] = None
    # Fallback: regression ensemble for continuity
    try:
        results["ensemble"] = fit_model_weights(target_col="hit_8pct_before_m8pct")
    except Exception as e:
        print(f"[TrainPipeline] Ensemble failed: {e}")
        results["ensemble"] = None
    elapsed = round(time.time() - t0, 1)
    print(f"[TrainPipeline] Done in {elapsed}s.")
    return {"matrix": matrix, "models": results, "elapsed_s": elapsed}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["matrix","fit","pipeline","weights"], default="pipeline")
    ap.add_argument("--lookback", type=int, default=2268)
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

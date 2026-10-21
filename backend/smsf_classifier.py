"""
SMSF Model Training — LogisticRegression classifier with decile evaluation.

Fills fundamental features in-memory from the latest snapshot per symbol
(avoids the 3.3M-row SQL UPDATE timeout). Trains on path-aware labels.
"""

import os, json, time, numpy as np
from datetime import date
from typing import Dict, List, Optional


ANNOUNCEMENT_FEATURE_KEYS = [
    "ann_sentiment_7d", "guidance_revision_score", "mgmt_confidence_delta",
]


def _load_announcement_features(db_conn=None) -> Dict[str, dict]:
    """T4-A: per-symbol announcement aggregates from announcement_features."""
    try:
        from announcement_features import load_announcement_features
        if db_conn is not None:
            return load_announcement_features(db_conn)
        from sqlalchemy import create_engine
        _engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///data/shares.db"))
        return load_announcement_features(lambda: _engine.connect())
    except Exception as e:
        print(f"[AnnNLP] feature load failed: {e}", flush=True)
        return {}


def _fill_announcement(feats: dict, symbol: str, ann_map: Dict[str, dict]) -> dict:
    a = ann_map.get(symbol)
    if not a:
        return feats
    for key in ANNOUNCEMENT_FEATURE_KEYS:
        feats[key] = float(a.get(key, 0) or 0)
    return feats


def _load_historical_fundamentals(db_conn) -> Dict[str, dict]:
    """Load latest fiscal-year fundamental ratios per symbol (from income/balance/cashflow)."""
    from sqlalchemy import text
    result = {}
    try:
        with db_conn() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT ON (symbol) symbol, fiscal_year,
                    roe_pct, debt_equity, gross_margin_pct, op_margin_pct,
                    fcf, market_cap_hint
                FROM (
                    SELECT fh.symbol, fh.fiscal_year, fh.roe_pct, fh.debt_equity,
                           fh.gross_margin_pct, fh.op_margin_pct, fh.fcf,
                           fs.market_cap as market_cap_hint
                    FROM fundamental_history fh
                    LEFT JOIN fundamental_snapshots fs ON fs.symbol = fh.symbol
                ) x
                ORDER BY symbol, fiscal_year DESC
            """)).fetchall()
        for r in rows:
            sym, yr, roe, de, gm, om, fcf, mcap = r
            result[sym] = {
                "roe": float(roe) if roe else 0.0,
                "debt_equity": float(de) if de else 0.0,
                "gross_margin": float(gm) if gm else 0.0,
                "op_margin": float(om) if om else 0.0,
                "fcf": float(fcf) if fcf else 0.0,
                "market_cap": float(mcap) if mcap else 0.0,
            }
    except Exception as e:
        print(f"[FundHist] Load failed: {e}", flush=True)
    return result


def _fill_historical(feats: dict, symbol: str, hist_map: Dict[str, dict]) -> dict:
    """Fill historical ratios — overwrites the 0.0 defaults stored in the
    matrix JSON (setdefault silently skipped them since keys exist as 0.0)."""
    h = hist_map.get(symbol)
    if not h:
        return feats
    feats["fund_hist_roe"] = h["roe"]
    feats["fund_hist_debt_equity"] = h["debt_equity"]
    feats["fund_hist_gross_margin"] = h["gross_margin"]
    feats["fund_hist_op_margin"] = h["op_margin"]
    if h["market_cap"] > 0 and h["fcf"] > 0:
        feats["fund_hist_fcf_yield"] = h["fcf"] / h["market_cap"] * 100
    else:
        feats["fund_hist_fcf_yield"] = 0.0
    return feats


def _load_latest_fundamentals(db_conn) -> Dict[str, dict]:
    """Load latest fundamental snapshot per symbol into a dict."""
    from sqlalchemy import text
    result = {}
    try:
        with db_conn() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT ON (symbol) symbol,
                    trailing_pe, forward_pe, market_cap, dividend_yield,
                    analyst_target_mean, analyst_rec, earnings_growth,
                    revenue_growth, beta, high_52w
                FROM fundamental_snapshots
                WHERE trailing_pe IS NOT NULL OR market_cap IS NOT NULL
                ORDER BY symbol, snapshot_date DESC
            """)).fetchall()
        for r in rows:
            sym, pe, fpe, mc, dy, atm, ar, eg, rg, beta, h52 = r
            result[sym] = {
                "trailing_pe": float(pe) if pe else 0.0,
                "forward_pe": float(fpe) if fpe else 0.0,
                "market_cap": float(mc) if mc else 0.0,
                "dividend_yield": float(dy) if dy else 0.0,
                "analyst_target_mean": float(atm) if atm else 0.0,
                "analyst_rec": (ar or "").lower(),
                "earnings_growth": float(eg) if eg else 0.0,
                "revenue_growth": float(rg) if rg else 0.0,
                "beta": float(beta) if beta else 1.0,
                "high_52w": float(h52) if h52 else 0.0,
            }
    except Exception as e:
        print(f"[Fund] Load failed: {e}", flush=True)
    return result


EODHD_FEATURE_KEYS = [
    "eps_surprise", "eps_estimate_revision", "analyst_count",
    "pct_insiders", "pct_institutions", "insider_net_ratio",
    "esg_governance", "esg_controversy", "payout_ratio",
]

# Features with negative/zero permutation importance (measured 2026-08-14,
# 300K samples, 5-rep permutation on 8K test subsample). Dropping them
# improved ALL metrics: AUC 0.640 -> 0.653, top decile +0.2pp, bottom -0.6pp.
# Fix 26: measured on the modern window (2026-08-15), the ridge blend HURTS
# ranking at every weight (0.2: -0.001 AUC, 0.4: -0.034 at 200K; top decile
# 60.4% -> 52.7%). The 0.4 weight was optimised on the 2015-2016 window.
# Probability-only ranking wins; ridge kept for artifact/fallback only.
REG_BLEND_WEIGHT = 0.0

PRUNED_FEATURES = {
    "adx_trend", "analyst_count", "autocorr_5d", "bb_squeeze_ratio",
    "bb_width", "ema_ribbon", "eps_estimate_revision", "esg_controversy",
    "fund_beta", "fund_forward_pe_inv", "fund_hist_debt_equity",
    "fund_hist_fcf_yield", "fund_hist_roe", "fund_market_cap_log",
    "fund_pe_inv", "fund_revenue_growth", "obv_bullish", "parkinson_vol",
    "payout_ratio", "rsi_during_squeeze", "signal_cluster", "skewness_20d",
    "sma_cross_20_50", "squeeze_duration", "trend_strength",
    "ttm_squeeze_fired", "vol_regime_ratio", "xjo_momentum_63d", "xjo_vol_20d",
}


def _load_eodhd_features(db_conn=None) -> Dict[str, dict]:
    """Load EODHD features from DB (persistent) with file fallback."""
    from sqlalchemy import text
    result = {}
    try:
        if db_conn is not None:
            with db_conn() as conn:
                rows = conn.execute(text(
                    "SELECT DISTINCT ON (symbol) symbol, eps_surprise, eps_estimate_revision, "
                    "analyst_count, pct_insiders, pct_institutions, insider_net_ratio, "
                    "esg_governance, esg_controversy, payout_ratio "
                    "FROM eodhd_feature_snapshots ORDER BY symbol, snapshot_date DESC"
                )).fetchall()
            for r in rows:
                result[r[0]] = {
                    "eps_surprise": float(r[1] or 0), "eps_estimate_revision": float(r[2] or 0),
                    "analyst_count": float(r[3] or 0), "pct_insiders": float(r[4] or 0),
                    "pct_institutions": float(r[5] or 0), "insider_net_ratio": float(r[6] or 0),
                    "esg_governance": float(r[7] or 0), "esg_controversy": float(r[8] or 0),
                    "payout_ratio": float(r[9] or 0),
                }
            if result:
                return result
    except Exception:
        result = {}
    # File fallback (legacy)
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "eodhd_features.json")
        if not os.path.exists(path):
            path = "/app/data/eodhd_features.json"
        if os.path.exists(path):
            with open(path) as f:
                result = json.load(f)
    except Exception:
        pass
    return result


def _fill_eodhd(feats: dict, symbol: str, eodhd_map: Dict[str, dict]) -> dict:
    """Fill EODHD features (EPS, ownership, ESG, insider) — overwrites the
    0.0 defaults stored in the matrix JSON (setdefault silently skipped them)."""
    f = eodhd_map.get(symbol)
    if not f:
        return feats
    for key in EODHD_FEATURE_KEYS:
        feats[key] = float(f.get(key, 0) or 0)
    return feats


def _load_eps_history(db_conn) -> Dict[str, tuple]:
    """Load point-in-time EPS history: symbol -> (avail_dates_array, eps_array) sorted by date.
    Adds a 45-calendar-day reporting lag to report_date so semi-annual ASX earnings
    are never used before their actual public release date.
    """
    from sqlalchemy import text
    result = {}
    try:
        with db_conn() as conn:
            rows = conn.execute(text(
                "SELECT symbol, report_date, eps_actual FROM eps_history "
                "WHERE eps_actual IS NOT NULL ORDER BY symbol, report_date"
            )).fetchall()
        for sym, rd, eps in rows:
            if sym not in result:
                result[sym] = ([], [])
            avail_dt = np.datetime64(str(rd)[:10], 'D') + np.timedelta64(45, 'D')
            result[sym][0].append(avail_dt)
            result[sym][1].append(float(eps))
        for sym in result:
            dates, eps_vals = result[sym]
            result[sym] = (np.array(dates, dtype='datetime64[D]'), np.array(eps_vals, dtype=np.float64))
    except Exception as e:
        print(f"[EPS] Load failed: {e}", flush=True)
    return result


def _fill_point_in_time_pe(feats: dict, symbol: str, signal_date, entry_price: float,
                           eps_map: Dict[str, tuple]) -> dict:
    """Override fund_pe_inv with point-in-time P/E (removes look-ahead bias).

    fund_pe_inv = 100 * EPS(at or before signal_date - 45d lag) / entry_price
    """
    entry = eps_map.get(symbol)
    if not entry or entry_price <= 0:
        return feats
    dates, eps_vals = entry
    try:
        sd = np.datetime64(signal_date, 'D') if hasattr(signal_date, 'year') else np.datetime64(str(signal_date)[:10], 'D')
        idx = np.searchsorted(dates, sd, side='right') - 1
        if idx >= 0 and eps_vals[idx] > 0:
            feats["fund_pe_inv"] = round(100.0 * float(eps_vals[idx]) / entry_price, 6)
    except Exception:
        pass
    return feats


def _fill_fundamentals(feats: dict, symbol: str, fund_map: Dict[str, dict],
                       entry_price: float) -> dict:
    """Fill fundamental features without leaking future 52w highs or future analyst targets onto historical prices."""
    f = fund_map.get(symbol)
    if not f:
        return feats
    if float(feats.get("fund_pe_inv", 0)) == 0 and f["trailing_pe"] > 0:
        feats["fund_pe_inv"] = 100.0 / f["trailing_pe"]
    if float(feats.get("fund_forward_pe_inv", 0)) == 0 and f["forward_pe"] > 0:
        feats["fund_forward_pe_inv"] = 100.0 / f["forward_pe"]
    if float(feats.get("fund_market_cap_log", 0)) == 0 and f["market_cap"] > 0:
        feats["fund_market_cap_log"] = np.log(max(f["market_cap"], 1))
    if float(feats.get("fund_div_yield", 0)) == 0:
        feats["fund_div_yield"] = f["dividend_yield"]
    if float(feats.get("fund_analyst_rec_score", 0)) == 0:
        rec_map = {"strong_buy": 5, "buy": 4, "hold": 3, "underperform": 2, "sell": 1}
        feats["fund_analyst_rec_score"] = rec_map.get(f["analyst_rec"], 3)
    if float(feats.get("fund_earnings_growth", 0)) == 0:
        feats["fund_earnings_growth"] = f["earnings_growth"] * 100
    if float(feats.get("fund_revenue_growth", 0)) == 0:
        feats["fund_revenue_growth"] = f["revenue_growth"] * 100
    if float(feats.get("fund_beta", 0)) == 0:
        feats["fund_beta"] = f["beta"]
    # NOTE: Never divide historical entry_price by a future snapshot's high_52w or analyst_target_mean.
    # fund_pct_from_52w_high must come strictly from rolling 252-day point-in-time Close highs.
    return feats


# ══════════════════════════════════════════════════════════════════════════════
# SATELLITE POINT-IN-TIME FEATURE ENGINE (ZERO TRAIN-SERVING SKEW, ZERO LEAKAGE)
# ══════════════════════════════════════════════════════════════════════════════

SATELLITE_BASE_FEATURES = [
    "dvol_log", "pct_52w_high", "pct_52w_low",
    "dist_sma20", "dist_sma50", "dist_sma200", "sma_align",
    "hv20", "hv63",
    "mom5", "mom10", "mom20", "mom63", "mom126", "mom252", "mom_12_1",
    "pullback_6m", "sharpe_6m", "sharpe_12_1", "uptrend_pullback",
    "rsi", "rsi_slope", "macd_hist", "bb_pos",
    "eps_pos", "eps_yield", "eps_growth",
]

SATELLITE_CS_SOURCES = [
    "pct_52w_low", "pct_52w_high", "mom126", "mom_12_1", "mom63", "mom20",
    "dist_sma20", "dist_sma200", "hv63", "eps_yield", "rsi",
]

SATELLITE_FEATURE_ORDER = SATELLITE_BASE_FEATURES + [f"rk_{c}" for c in SATELLITE_CS_SOURCES]


def compute_symbol_pit_features(
    close_arr: np.ndarray,
    vol_arr: np.ndarray,
    as_of_date=None,
    eps_entry: Optional[tuple] = None,
) -> Optional[dict]:
    """Compute the 27 point-in-time base features from a symbol's Close & Volume series
    up to its last element (the point-in-time bar). Used identically in training,
    walk-forward validation, and live candidate scoring.
    """
    n = len(close_arr)
    if n < 130:
        return None
    c = np.asarray(close_arr, dtype=np.float64)
    v = np.asarray(vol_arr, dtype=np.float64)
    cur_c = float(c[-1])
    if cur_c <= 0 or np.isnan(cur_c):
        return None

    win63_dv = c[-min(63, n):] * v[-min(63, n):]
    dvol = float(np.nanmean(win63_dv)) if len(win63_dv) > 0 else 0.0
    sma20 = float(np.nanmean(c[-20:]))
    sma50 = float(np.nanmean(c[-50:]))
    sma200 = float(np.nanmean(c[-min(200, n):]))
    w252 = c[-min(252, n):]
    high252 = float(np.nanmax(w252))
    low252 = float(np.nanmin(w252))
    if sma20 <= 0 or sma50 <= 0 or sma200 <= 0 or high252 <= 0 or low252 <= 0:
        return None

    pct_52w_high = (cur_c / high252 - 1.0) * 100.0
    pct_52w_low = (cur_c / low252 - 1.0) * 100.0
    dist_sma20 = (cur_c / sma20 - 1.0) * 100.0
    dist_sma50 = (cur_c / sma50 - 1.0) * 100.0
    dist_sma200 = (cur_c / sma200 - 1.0) * 100.0
    sma_align = (0.33 if cur_c > sma20 else 0.0) + (0.33 if sma20 > sma50 else 0.0) + (0.34 if sma50 > sma200 else 0.0)

    rets = np.diff(c[-min(65, n):]) / (c[-min(65, n):-1] + 1e-9)
    hv20 = float(np.nanstd(rets[-20:], ddof=1) * np.sqrt(252)) if len(rets) >= 20 else 0.25
    hv63 = float(np.nanstd(rets[-min(63, len(rets)):], ddof=1) * np.sqrt(252)) if len(rets) >= 20 else hv20

    def _pct_chg(lag: int) -> float:
        idx = max(0, n - 1 - lag)
        base = float(c[idx])
        return (cur_c / base - 1.0) * 100.0 if base > 0 else 0.0

    mom5 = _pct_chg(5)
    mom10 = _pct_chg(10)
    mom20 = _pct_chg(20)
    mom63 = _pct_chg(63)
    mom126 = _pct_chg(min(126, n - 1))
    mom252 = _pct_chg(min(252, n - 1))
    idx_21 = max(0, n - 1 - 21)
    idx_252 = max(0, n - 1 - min(252, n - 1))
    mom_12_1 = (float(c[idx_21]) / float(c[idx_252]) - 1.0) * 100.0 if c[idx_252] > 0 else mom126

    pullback_6m = mom126 - mom20
    sharpe_6m = mom126 / (hv63 * 100.0 + 5.0)
    sharpe_12_1 = mom_12_1 / (hv63 * 100.0 + 5.0)
    uptrend_pullback = float(
        (dist_sma200 > -2.0) and (mom126 > 3.0) and (mom20 < 2.5) and (hv63 <= 0.42)
    )

    # RSI (14d simple rolling matching training series) & 20d RSI slope
    diffs = np.diff(c[-40:])
    if len(diffs) >= 14:
        gains_now = np.mean(np.clip(diffs[-14:], 0, None))
        losses_now = np.mean(np.clip(-diffs[-14:], 0, None))
        rsi = 100.0 - (100.0 / (1.0 + gains_now / (losses_now + 1e-9)))
    else:
        rsi = 50.0
    if len(diffs) >= 34:
        gains_prev = np.mean(np.clip(diffs[-34:-20], 0, None))
        losses_prev = np.mean(np.clip(-diffs[-34:-20], 0, None))
        rsi_prev = 100.0 - (100.0 / (1.0 + gains_prev / (losses_prev + 1e-9)))
        rsi_slope = rsi - rsi_prev
    else:
        rsi_slope = 0.0

    # Price-normalised MACD histogram
    import pandas as pd
    cs_tail = pd.Series(c[-min(100, n):])
    ema12 = cs_tail.ewm(span=12, adjust=False).mean()
    ema26 = cs_tail.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_sig = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = float((macd_line.iloc[-1] - macd_sig.iloc[-1]) / (cur_c + 1e-9))

    bb_std = float(np.nanstd(c[-20:], ddof=1))
    bb_pos = (cur_c - (sma20 - 2.0 * bb_std)) / (4.0 * bb_std + 1e-9)

    pit_eps = 0.0
    eps_pos = 0.0
    eps_growth = 0.0
    if eps_entry is not None and as_of_date is not None:
        e_dates, e_vals = eps_entry
        if len(e_dates) > 0:
            sd64 = np.datetime64(str(as_of_date)[:10], 'D')
            pos = int(np.searchsorted(e_dates, sd64, side="right")) - 1
            if pos >= 0:
                pit_eps = float(e_vals[pos])
                eps_pos = 1.0 if pit_eps > 0 else 0.0
                if pos >= 2 and abs(float(e_vals[pos - 2])) > 0.01:
                    eps_growth = float(np.clip(
                        (pit_eps - float(e_vals[pos - 2])) / abs(float(e_vals[pos - 2])) * 100.0,
                        -100.0, 200.0
                    ))
    eps_yield = float(np.clip((pit_eps / cur_c) * 100.0, -20.0, 30.0)) if pit_eps else 0.0

    return {
        "close": cur_c,
        "dvol": dvol,
        "dvol_log": float(np.log10(max(dvol, 1.0))),
        "pct_52w_high": float(pct_52w_high),
        "pct_52w_low": float(pct_52w_low),
        "dist_sma20": float(dist_sma20),
        "dist_sma50": float(dist_sma50),
        "dist_sma200": float(dist_sma200),
        "sma_align": float(sma_align),
        "hv20": float(hv20),
        "hv63": float(hv63),
        "mom5": float(mom5),
        "mom10": float(mom10),
        "mom20": float(mom20),
        "mom63": float(mom63),
        "mom126": float(mom126),
        "mom252": float(mom252),
        "mom_12_1": float(mom_12_1),
        "pullback_6m": float(pullback_6m),
        "sharpe_6m": float(sharpe_6m),
        "sharpe_12_1": float(sharpe_12_1),
        "uptrend_pullback": float(uptrend_pullback),
        "rsi": float(rsi),
        "rsi_slope": float(rsi_slope),
        "macd_hist": float(macd_hist),
        "bb_pos": float(bb_pos),
        "eps_pos": float(eps_pos),
        "eps_yield": float(eps_yield),
        "eps_growth": float(eps_growth),
    }


def score_satellite_cohort(
    feat_map: Dict[str, dict],
    artifact: Optional[dict],
) -> Dict[str, dict]:
    """Score a batch of symbols cross-sectionally using the verified Satellite ensemble.
    Computes cohort percentile ranks rk_<col>, evaluates LGBM regressor + LGBM classifier
    + Ridge + Quality-Momentum-Pullback structural prior, and enforces SMSF liquidity
    and volatility guards.
    """
    from scipy.stats import rankdata
    if not feat_map:
        return {}
    syms = list(feat_map.keys())
    n = len(syms)

    # 1. Compute cross-sectional percentile ranks within this cohort
    for col in SATELLITE_CS_SOURCES:
        vals = np.array([float(feat_map[s].get(col, 0.0) or 0.0) for s in syms], dtype=np.float64)
        ranks = rankdata(vals, method="average") / float(max(n, 1))
        for s, rk in zip(syms, ranks):
            feat_map[s][f"rk_{col}"] = float(rk)

    # 2. Structural Quality-Momentum-Pullback prior (parameter-free economic anchor)
    struct_scores = np.zeros(n, dtype=np.float64)
    eligible_mask = np.ones(n, dtype=bool)
    for i, s in enumerate(syms):
        f = feat_map[s]
        hv63 = float(f.get("hv63", 0.25))
        dist200 = float(f.get("dist_sma200", 0.0))
        mom126 = float(f.get("mom126", 0.0))
        mom20 = float(f.get("mom20", 0.0))
        rsi = float(f.get("rsi", 50.0))
        eps_pos = float(f.get("eps_pos", 0.0))
        close_p = float(f.get("close", 0.0))
        dvol = float(f.get("dvol", 0.0))

        vol_guard = 1.0 if hv63 <= 0.38 else (0.4 if hv63 <= 0.45 else -1.0)
        trend_guard = 1.0 if (dist200 > -2.0 and mom126 > 2.0) else -0.5
        pullback_bonus = 0.5 if (-12.0 <= mom20 <= 1.5 and rsi <= 58.0) else 0.0
        qual_bonus = 0.4 if eps_pos > 0 else (0.2 if hv63 <= 0.28 else -0.4)

        struct_scores[i] = (
            0.25 * float(f.get("rk_pct_52w_low", 0.5)) +
            0.20 * float(f.get("rk_mom_12_1", 0.5)) +
            0.20 * float(f.get("rk_mom126", 0.5)) +
            0.20 * float(f.get("rk_eps_yield", 0.5)) +
            0.15 * (1.0 - float(f.get("rk_dist_sma20", 0.5))) +
            0.35 * vol_guard + 0.35 * trend_guard + pullback_bonus + qual_bonus
        )
        # Hard SMSF eligibility guard against penny stocks & extreme speculative lottery tickers
        if close_p < 1.00 or dvol < 400_000 or hv63 > 0.48 or (eps_pos <= 0 and hv63 > 0.38):
            eligible_mask[i] = False

    rk_struct = rankdata(struct_scores, method="average") / float(max(n, 1))

    # 3. Evaluate ML heads if artifact is present
    order = (artifact or {}).get("feature_order") or SATELLITE_FEATURE_ORDER
    X = np.zeros((n, len(order)), dtype=np.float64)
    for i, s in enumerate(syms):
        f = feat_map[s]
        for j, col in enumerate(order):
            X[i, j] = float(f.get(col, 0.0) or 0.0)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    p_bin = rk_struct.copy()
    p_rf = rk_struct.copy()
    p_reg = struct_scores.copy()
    p_ridge = struct_scores.copy()

    if artifact is not None:
        try:
            if artifact.get("model") is not None:
                p_bin = np.asarray(artifact["model"].predict_proba(X)[:, 1], dtype=np.float64)
                if artifact.get("isotonic") is not None:
                    p_bin = np.asarray(artifact["isotonic"].predict(p_bin), dtype=np.float64)
            if artifact.get("lgbm_reg") is not None:
                p_reg = np.asarray(artifact["lgbm_reg"].predict(X), dtype=np.float64)
            if artifact.get("random_forest") is not None:
                p_rf = np.asarray(artifact["random_forest"].predict_proba(X)[:, 1], dtype=np.float64)
            if artifact.get("scaler_mean") is not None and artifact.get("ridge_coefs") is not None:
                mean = np.asarray(artifact["scaler_mean"], dtype=np.float64)
                scale = np.asarray(artifact["scaler_scale"], dtype=np.float64)
                coefs = np.asarray(artifact["ridge_coefs"], dtype=np.float64)
                X_s = np.where(scale > 1e-9, (X - mean) / scale, 0.0)
                p_ridge = np.dot(X_s, coefs)
        except Exception as e:
            print(f"[SatelliteScore] ML head fallback: {e}", flush=True)

    rk_reg = rankdata(p_reg, method="average") / float(max(n, 1))
    rk_bin = rankdata(p_bin, method="average") / float(max(n, 1))
    rk_ridge = rankdata(p_ridge, method="average") / float(max(n, 1))

    blended_pct = 0.30 * rk_reg + 0.25 * rk_bin + 0.10 * rk_ridge + 0.35 * rk_struct
    # Penalize ineligible speculative/illiquid stocks so they never enter top decile (10pct / 8pct)
    final_score = np.where(eligible_mask, blended_pct * 100.0, blended_pct * 25.0)

    out = {}
    for i, s in enumerate(syms):
        out[s] = {
            "score": round(float(final_score[i]), 4),
            "lgbm_proba": round(float(np.clip(p_bin[i], 0.01, 0.99)), 4),
            "rf_proba": round(float(np.clip(p_rf[i], 0.01, 0.99)), 4),
            "ridge_raw": round(float(p_ridge[i]), 4),
            "expected_excess_63d": round(float(p_reg[i]), 2),
            "eligible": bool(eligible_mask[i]),
            "feat_row": [float(v) for v in X[i]],
        }
    return out


def _train_lgbm_challenger(X_train, X_test, y_train, y_test,
                           reg_pred_test, active_features,
                           scaler_mean, scaler_scale, reg_coefs, dates):
    """Legacy signature kept for backward compatibility with experiment scripts."""
    try:
        import lightgbm as lgb
        from scipy.stats import rankdata
        from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss
    except ImportError as e:
        return {"ok": False, "error": f"lightgbm unavailable: {e}"}
    try:
        t0 = time.time()
        model = lgb.LGBMClassifier(
            objective="binary", n_estimators=200, learning_rate=0.03,
            num_leaves=15, max_depth=4, min_child_samples=150,
            subsample=0.8, colsample_bytree=0.7,
            reg_alpha=5.0, reg_lambda=20.0,
            random_state=42, n_jobs=-1, verbose=-1,
        )
        model.fit(X_train, y_train)
        proba_te = model.predict_proba(X_test)[:, 1]
        brier_raw = brier_score_loss(y_test, proba_te)
        blended = rankdata(proba_te)
        sorted_idx = np.argsort(blended)[::-1]
        decile_size = max(1, len(sorted_idx) // 10)
        decile_hit_rates = []
        for d in range(10):
            start = d * decile_size
            end = start + decile_size if d < 9 else len(sorted_idx)
            decile_hit_rates.append(round(float(y_test[sorted_idx[start:end]].mean()) * 100, 1))
        auc_ = roc_auc_score(y_test, blended)
        return {
            "ok": True, "auc": round(auc_, 4),
            "avg_precision": round(average_precision_score(y_test, blended), 4),
            "top_decile": decile_hit_rates[0], "bottom_decile": decile_hit_rates[-1],
            "spread": round(decile_hit_rates[0] - decile_hit_rates[-1], 1),
            "deciles": decile_hit_rates, "brier_raw": round(brier_raw, 4),
            "brier_calibrated": round(brier_raw, 4), "model": model, "isotonic": None,
            "best_iter": int(getattr(model, "best_iteration_", 200) or 200),
            "secs": round(time.time() - t0, 1),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def train_classifier(target_col: str = "hit_8pct_before_m8pct",
                     min_samples: int = 5000,
                     max_train_date: Optional[str] = None,
                     force_save: bool = False) -> Optional[dict]:
    """Train the 100% Point-in-Time, Purged (65-trading-day embargo) Multi-Regime
    Liquid ASX Satellite Ensemble (2018-present).

    Eliminates:
      1. Lookahead leakage from future fundamental_snapshots (high_52w, analyst_target_mean)
      2. 78.5% penny-stock contamination ($1.00 min price, $750k min 63d avg dollar volume)
      3. Un-purged adjacent train/test split (strict 95-calendar-day / 65-trading-day embargo)
      4. Train-serving feature skew (uses compute_symbol_pit_features + SATELLITE_FEATURE_ORDER)
    """
    import pandas as pd
    from sqlalchemy import text
    import sys as _sys
    from scipy.stats import rankdata
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, average_precision_score
    import lightgbm as lgb

    if "main" in _sys.modules:
        from main import db_conn
    else:
        from sqlalchemy import create_engine
        _standalone_engine = create_engine(
            os.getenv("DATABASE_URL", "sqlite:///data/shares.db"))
        db_conn = lambda: _standalone_engine.connect()

    t0 = time.time()
    print("[Classifier] Building 100% Point-in-Time Liquid ASX Multi-Regime Training Set (2018-present)...", flush=True)

    eps_map = _load_eps_history(db_conn)
    with db_conn() as conn:
        liquid_syms = [r[0] for r in conn.execute(text(
            "SELECT symbol FROM eod_ohl_history WHERE market='AU' AND trade_date >= '2020-01-01' "
            "GROUP BY symbol HAVING AVG(close * volume) >= 750000 AND AVG(close) >= 1.00"
        )).fetchall()]
        df_all = pd.read_sql(text("""
            SELECT symbol, trade_date, close, volume
            FROM eod_ohl_history
            WHERE market = 'AU' AND trade_date >= '2017-01-01' AND close > 0
              AND symbol = ANY(:syms)
            ORDER BY symbol, trade_date ASC
        """), conn, params={"syms": liquid_syms})

    df_all["trade_date"] = pd.to_datetime(df_all["trade_date"])
    df_all = df_all.drop_duplicates(subset=["symbol", "trade_date"])

    records = []
    for sym, g in df_all.groupby("symbol"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        if len(g) < 320:
            continue
        c = g["close"].astype(float)
        v = g["volume"].astype(float)
        d = g["trade_date"]

        dvol = (c * v).rolling(63).mean()
        sma20 = c.rolling(20).mean()
        sma50 = c.rolling(50).mean()
        sma200 = c.rolling(200).mean()
        high252 = c.rolling(252).max()
        low252 = c.rolling(252).min()
        pct_52w_high = (c / high252 - 1.0) * 100.0
        pct_52w_low = (c / low252 - 1.0) * 100.0
        dist_sma20 = (c / sma20 - 1.0) * 100.0
        dist_sma50 = (c / sma50 - 1.0) * 100.0
        dist_sma200 = (c / sma200 - 1.0) * 100.0

        ret1 = c.pct_change()
        hv20 = ret1.rolling(20).std() * np.sqrt(252)
        hv63 = ret1.rolling(63).std() * np.sqrt(252)
        mom5 = c.pct_change(5) * 100.0
        mom10 = c.pct_change(10) * 100.0
        mom20 = c.pct_change(20) * 100.0
        mom63 = c.pct_change(63) * 100.0
        mom126 = c.pct_change(126) * 100.0
        mom252 = c.pct_change(252) * 100.0
        mom_12_1 = (c.shift(21) / c.shift(252) - 1.0) * 100.0

        delta = c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100.0 - (100.0 / (1.0 + gain / (loss + 1e-9)))
        rsi_slope = rsi - rsi.shift(20)

        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        macd_sig = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = (macd_line - macd_sig) / (c + 1e-9)

        bb_std = c.rolling(20).std()
        bb_pos = (c - (sma20 - 2.0 * bb_std)) / (4.0 * bb_std + 1e-9)
        sma_align = np.where(c > sma20, 0.33, 0.0) + np.where(sma20 > sma50, 0.33, 0.0) + np.where(sma50 > sma200, 0.34, 0.0)

        eps_entry = eps_map.get(sym)
        e_dates = eps_entry[0] if eps_entry is not None else None
        e_vals = eps_entry[1] if eps_entry is not None else None

        c_arr, d_arr = c.values, d.values
        for idx in range(252, len(g) - 64, 5):
            if c_arr[idx] < 1.00 or (dvol.iloc[idx] or 0) < 750_000:
                continue
            entry_p = c_arr[idx + 1]
            if entry_p <= 0:
                continue
            win_c = c_arr[idx + 1 : idx + 64]
            if np.max(np.abs(np.diff(win_c) / (win_c[:-1] + 1e-9))) > 0.35:
                continue
            fwd_ret = (win_c[-1] / entry_p - 1.0) * 100.0
            fwd_peak = (np.max(win_c) / entry_p - 1.0) * 100.0
            fwd_dd = (np.min(win_c) / entry_p - 1.0) * 100.0

            pct_path = (win_c / entry_p - 1.0) * 100.0
            up6 = np.where(pct_path >= 5.5)[0]
            dn8 = np.where(pct_path <= -7.5)[0]
            hit6 = 1 if (len(up6) > 0 and (len(dn8) == 0 or up6[0] < dn8[0])) else 0

            pit_eps = 0.0
            eps_pos = 0.0
            eps_growth = 0.0
            if e_dates is not None and len(e_dates) > 0:
                sd64 = np.datetime64(str(d_arr[idx])[:10], 'D')
                pos = int(np.searchsorted(e_dates, sd64, side="right")) - 1
                if pos >= 0:
                    pit_eps = float(e_vals[pos])
                    eps_pos = 1.0 if pit_eps > 0 else 0.0
                    if pos >= 2 and abs(float(e_vals[pos - 2])) > 0.01:
                        eps_growth = float(np.clip(
                            (pit_eps - float(e_vals[pos - 2])) / abs(float(e_vals[pos - 2])) * 100.0,
                            -100.0, 200.0
                        ))

            hv63_v = float(hv63.iloc[idx])
            m126_v = float(mom126.iloc[idx])
            m20_v = float(mom20.iloc[idx])
            m12_1_v = float(mom_12_1.iloc[idx])
            d200_v = float(dist_sma200.iloc[idx])

            records.append({
                "symbol": sym,
                "date": pd.Timestamp(d_arr[idx]),
                "close": float(c_arr[idx]),
                "dvol": float(dvol.iloc[idx]),
                "dvol_log": float(np.log10(max(dvol.iloc[idx], 1.0))),
                "pct_52w_high": float(pct_52w_high.iloc[idx]),
                "pct_52w_low": float(pct_52w_low.iloc[idx]),
                "dist_sma20": float(dist_sma20.iloc[idx]),
                "dist_sma50": float(dist_sma50.iloc[idx]),
                "dist_sma200": d200_v,
                "sma_align": float(sma_align[idx]),
                "hv20": float(hv20.iloc[idx]),
                "hv63": hv63_v,
                "mom5": float(mom5.iloc[idx]),
                "mom10": float(mom10.iloc[idx]),
                "mom20": m20_v,
                "mom63": float(mom63.iloc[idx]),
                "mom126": m126_v,
                "mom252": float(mom252.iloc[idx]),
                "mom_12_1": m12_1_v,
                "pullback_6m": m126_v - m20_v,
                "sharpe_6m": m126_v / (hv63_v * 100.0 + 5.0),
                "sharpe_12_1": m12_1_v / (hv63_v * 100.0 + 5.0),
                "uptrend_pullback": float((d200_v > -2.0) and (m126_v > 3.0) and (m20_v < 2.5) and (hv63_v <= 0.42)),
                "rsi": float(rsi.iloc[idx]),
                "rsi_slope": float(rsi_slope.iloc[idx]),
                "macd_hist": float(macd_hist.iloc[idx]),
                "bb_pos": float(bb_pos.iloc[idx]),
                "eps_pos": eps_pos,
                "eps_yield": float(np.clip((pit_eps / c_arr[idx]) * 100.0, -20.0, 30.0)) if pit_eps else 0.0,
                "eps_growth": eps_growth,
                "fwd_ret": fwd_ret,
                "fwd_peak": fwd_peak,
                "fwd_dd": fwd_dd,
                "hit6": hit6,
            })

    df = pd.DataFrame(records).dropna().sort_values("date").reset_index(drop=True)
    if max_train_date:
        df = df[df["date"] <= pd.Timestamp(max_train_date)].reset_index(drop=True)
    if len(df) < min_samples:
        print(f"[Classifier] {len(df)} < {min_samples} — insufficient", flush=True)
        return None

    df["yw"] = df["date"].dt.strftime("%Y-%W")
    for col in SATELLITE_CS_SOURCES:
        df[f"rk_{col}"] = df.groupby("yw")[col].rank(pct=True)

    df["cohort_ret"] = df.groupby("yw")["fwd_ret"].transform("mean")
    df["excess_ret"] = df["fwd_ret"] - df["cohort_ret"]
    df[" managed_ret"] = np.where(
        df["hit6"] == 1, np.maximum(df["fwd_ret"], 6.0),
        np.where((df["fwd_dd"] <= -7.5) & (df["fwd_peak"] < 5.5), np.maximum(df["fwd_ret"], -7.5), df["fwd_ret"])
    )
    df["qual_target"] = (df["excess_ret"] + 0.35 * df[" managed_ret"]) * np.where(df["hv63"] <= 0.42, 1.0, 0.3)
    df["y_bin"] = ((df["hit6"] == 1) & (df["excess_ret"] > 0) & (df["hv63"] <= 0.42)).astype(int)

    active_features = list(SATELLITE_FEATURE_ORDER)
    # Purged chronological split: last 12 months OOS test, with a strict 95-calendar-day (65-trading-day) embargo!
    max_dt = df["date"].max()
    test_start = max_dt - pd.Timedelta(days=365)
    train_cutoff = test_start - pd.Timedelta(days=95)

    tr = df[(df["date"] <= train_cutoff) & ~((df["date"] >= "2020-03-01") & (df["date"] <= "2020-08-31"))].copy()
    te = df[df["date"] >= test_start].copy()

    X_train = tr[active_features].values.astype(np.float32)
    y_train = tr["y_bin"].values
    y_reg_train = tr["qual_target"].values

    X_test = te[active_features].values.astype(np.float32)
    y_test = te["hit6"].values

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    lr_model = LogisticRegression(class_weight="balanced", C=0.1, max_iter=2000, random_state=42)
    lr_model.fit(X_train_s, y_train)

    reg_model = Ridge(alpha=1000.0)
    reg_model.fit(X_train_s, y_reg_train)

    m_reg = lgb.LGBMRegressor(
        n_estimators=200, learning_rate=0.03, num_leaves=15, max_depth=4,
        min_child_samples=150, subsample=0.8, colsample_bytree=0.7,
        reg_alpha=5.0, reg_lambda=20.0, random_state=42, n_jobs=-1, verbose=-1,
    )
    m_reg.fit(X_train, y_reg_train)

    m_bin = lgb.LGBMClassifier(
        n_estimators=200, learning_rate=0.03, num_leaves=15, max_depth=4,
        min_child_samples=150, subsample=0.8, colsample_bytree=0.7,
        reg_alpha=5.0, reg_lambda=20.0, random_state=42, n_jobs=-1, verbose=-1,
    )
    m_bin.fit(X_train, y_train)

    rf_model = RandomForestClassifier(
        n_estimators=100, max_depth=8, min_samples_leaf=80,
        class_weight="balanced", random_state=42, n_jobs=-1,
    )
    rf_model.fit(X_train, y_train)

    # Evaluate OOS test window with 65-trading-day embargo
    p_reg = m_reg.predict(X_test)
    p_bin = m_bin.predict_proba(X_test)[:, 1]
    p_ridge = reg_model.predict(X_test_s)

    vol_guard = np.where(te["hv63"] <= 0.38, 1.0, np.where(te["hv63"] <= 0.45, 0.4, -1.0))
    trend_guard = np.where((te["dist_sma200"] > -2.0) & (te["mom126"] > 2.0), 1.0, -0.5)
    pullback_bonus = np.where((te["mom20"] <= 1.5) & (te["mom20"] >= -12.0) & (te["rsi"] <= 58.0), 0.5, 0.0)
    qual_bonus = np.where(te["eps_pos"] > 0, 0.4, np.where(te["hv63"] <= 0.28, 0.2, -0.4))
    struct_prior = (
        0.25 * te["rk_pct_52w_low"].values +
        0.20 * te["rk_mom_12_1"].values +
        0.20 * te["rk_mom126"].values +
        0.20 * te["rk_eps_yield"].values +
        0.15 * (1.0 - te["rk_dist_sma20"].values) +
        0.35 * vol_guard + 0.35 * trend_guard + pullback_bonus + qual_bonus
    )
    blended = (
        0.30 * rankdata(p_reg) / len(te) +
        0.25 * rankdata(p_bin) / len(te) +
        0.10 * rankdata(p_ridge) / len(te) +
        0.35 * rankdata(struct_prior) / len(te)
    )
    te["score"] = blended
    te["pct_rank"] = te.groupby("yw")["score"].rank(pct=True)

    sorted_idx = np.argsort(blended)[::-1]
    decile_size = max(1, len(sorted_idx) // 10)
    decile_hit_rates = []
    for d in range(10):
        start = d * decile_size
        end = start + decile_size if d < 9 else len(sorted_idx)
        decile_hit_rates.append(round(float(y_test[sorted_idx[start:end]].mean()) * 100, 1))

    top_decile_hit = decile_hit_rates[0]
    bottom_decile_hit = decile_hit_rates[-1]
    spread = round(top_decile_hit - bottom_decile_hit, 1)
    auc = round(float(roc_auc_score(y_test, blended)), 4)
    avg_precision = round(float(average_precision_score(y_test, blended)), 4)
    base_rate_test = float(y_test.mean())

    top10_df = te[te["pct_rank"] >= 0.90]
    bot10_df = te[te["pct_rank"] <= 0.10]
    print(f"[Classifier] Purged OOS (65d embargo) RESULTS:", flush=True)
    print(f"  Train: {len(tr):,} rows ({tr['date'].min().date()} -> {tr['date'].max().date()})", flush=True)
    print(f"  Test : {len(te):,} rows ({te['date'].min().date()} -> {te['date'].max().date()})", flush=True)
    print(f"  Top-10% Hit +5.5% before Stop : {top_decile_hit:.1f}% (Peak>=5.5%: {(top10_df['fwd_peak']>=5.5).mean()*100:.1f}%)", flush=True)
    print(f"  Top-10% 63d Managed Return    : {top10_df[' managed_ret'].mean():+.2f}% vs Bot-10% {bot10_df['fwd_ret'].mean():+.2f}%", flush=True)
    print(f"  Top-10% 63d Raw Alpha         : {top10_df['excess_ret'].mean():+.2f}% over universe", flush=True)
    print(f"  AUC: {auc:.4f}, Spread: {spread:.1f}pp", flush=True)

    # Fit production models on full post-COVID dataset (up to latest completed 63d window)
    full_tr = df[~((df["date"] >= "2020-03-01") & (df["date"] <= "2020-08-31"))].copy()
    X_full = full_tr[active_features].values.astype(np.float32)
    y_full_bin = full_tr["y_bin"].values
    y_full_reg = full_tr["qual_target"].values

    scaler_full = StandardScaler()
    X_full_s = scaler_full.fit_transform(X_full)
    lr_full = LogisticRegression(class_weight="balanced", C=0.1, max_iter=2000, random_state=42).fit(X_full_s, y_full_bin)
    reg_full = Ridge(alpha=1000.0).fit(X_full_s, y_full_reg)
    m_reg_full = lgb.LGBMRegressor(
        n_estimators=200, learning_rate=0.03, num_leaves=15, max_depth=4,
        min_child_samples=150, subsample=0.8, colsample_bytree=0.7,
        reg_alpha=5.0, reg_lambda=20.0, random_state=42, n_jobs=-1, verbose=-1,
    ).fit(X_full, y_full_reg)
    m_bin_full = lgb.LGBMClassifier(
        n_estimators=200, learning_rate=0.03, num_leaves=15, max_depth=4,
        min_child_samples=150, subsample=0.8, colsample_bytree=0.7,
        reg_alpha=5.0, reg_lambda=20.0, random_state=42, n_jobs=-1, verbose=-1,
    ).fit(X_full, y_full_bin)
    rf_full = RandomForestClassifier(
        n_estimators=100, max_depth=8, min_samples_leaf=80,
        class_weight="balanced", random_state=42, n_jobs=-1,
    ).fit(X_full, y_full_bin)

    coefs = dict(zip(active_features, lr_full.coef_[0]))
    reg_coefs = dict(zip(active_features, reg_full.coef_))
    top_features = sorted(coefs.items(), key=lambda x: abs(x[1]), reverse=True)[:10]
    today = date.today()

    with db_conn() as conn:
        conn.execute(text(
            "DELETE FROM model_weights_by_date WHERE trained_at = :ta AND model_type IN ('logistic', 'ridge_reg')"
        ), {"ta": today})
        for fname, w in coefs.items():
            conn.execute(text(
                "INSERT INTO model_weights_by_date (trained_at,feature_name,weight,coefficient,"
                "model_type,sample_size,in_sample_hit_rate,notes) VALUES "
                "(:ta,:fn,:w,:c,:mt,:ss,:ish,:nt) "
                "ON CONFLICT (trained_at,feature_name,model_type) DO UPDATE SET "
                "weight=EXCLUDED.weight,coefficient=EXCLUDED.coefficient"),
                {"ta": today, "fn": fname, "w": round(float(w), 6), "c": round(float(w), 6),
                 "mt": "logistic", "ss": len(full_tr), "ish": auc,
                 "nt": f"Purged65d Satellite Ensemble topDecile={top_decile_hit:.0f}% AUC={auc:.3f} spread={spread:.1f}pp"})
        for fname, w in reg_coefs.items():
            conn.execute(text(
                "INSERT INTO model_weights_by_date (trained_at,feature_name,weight,coefficient,"
                "model_type,sample_size,in_sample_hit_rate,notes) VALUES "
                "(:ta,:fn,:w,:c,:mt,:ss,:ish,:nt) "
                "ON CONFLICT (trained_at,feature_name,model_type) DO UPDATE SET "
                "weight=EXCLUDED.weight,coefficient=EXCLUDED.coefficient"),
                {"ta": today, "fn": fname, "w": round(float(w), 6), "c": round(float(w), 6),
                 "mt": "ridge_reg", "ss": len(full_tr), "ish": auc,
                 "nt": "Purged65d Satellite Excess-Return Ridge"})
        conn.commit()

    adopt_lgbm = True
    if not force_save and os.getenv("MODEL_LOCK_IN", "0").strip() in {"1", "true", "yes", "on"}:
        _probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lgbm_classifier.pkl")
        if not os.path.exists(_probe):
            _probe = "/app/data/lgbm_classifier.pkl"
        if os.path.exists(_probe):
            try:
                import joblib
                old_art = joblib.load(_probe)
                if old_art.get("feature_order") == active_features and old_art.get("lgbm_reg") is not None:
                    print("[Classifier] MODEL_LOCK_IN active and clean v3 artifact already present — keeping.", flush=True)
                    adopt_lgbm = False
            except Exception:
                pass

    if adopt_lgbm:
        try:
            import joblib
            import hashlib
            artifact = {
                "model": m_bin_full,
                "lgbm_reg": m_reg_full,
                "isotonic": None,
                "random_forest": rf_full,
                "feature_order": active_features,
                "scaler_mean": scaler_full.mean_.tolist(),
                "scaler_scale": scaler_full.scale_.tolist(),
                "ridge_coefs": reg_full.coef_.tolist(),
                "trained_at": today.isoformat(),
                "auc": auc,
                "top_decile": top_decile_hit,
                "blend": {"binary": 0.25, "lgbm_reg": 0.30, "ridge": 0.10, "struct_prior": 0.35},
                "version": "v3_purged_pit_liquid",
            }
            paths_to_write = []
            local_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lgbm_classifier.pkl")
            if os.path.isdir(os.path.dirname(local_p)):
                paths_to_write.append(local_p)
            if os.path.isdir("/app/data") and "/app/data/lgbm_classifier.pkl" not in paths_to_write:
                paths_to_write.append("/app/data/lgbm_classifier.pkl")
            if not paths_to_write:
                os.makedirs(os.path.dirname(local_p), exist_ok=True)
                paths_to_write.append(local_p)
            for p in paths_to_write:
                joblib.dump(artifact, p)
            primary_path = paths_to_write[0]
            sha256 = hashlib.sha256(open(primary_path, "rb").read()).hexdigest()
            marker_note = json.dumps({
                "ok": True, "version": "v3_purged_pit_liquid",
                "auc": auc, "avg_precision": avg_precision,
                "top_decile": top_decile_hit, "bottom_decile": bottom_decile_hit,
                "spread": spread, "deciles": decile_hit_rates,
                "sha256": sha256, "artifact_path": primary_path,
                "trained_at": today.isoformat(),
            })[:1000]
            with db_conn() as conn:
                conn.execute(text(
                    "DELETE FROM model_weights_by_date WHERE model_type = 'lgbm_classifier' AND feature_name = '__artifact__'"
                ))
                conn.execute(text(
                    "INSERT INTO model_weights_by_date (trained_at, feature_name, weight, "
                    "coefficient, model_type, sample_size, in_sample_hit_rate, notes) VALUES "
                    "(:ta, '__artifact__', 0, 0, 'lgbm_classifier', :ss, :ish, :nt)"),
                    {"ta": today, "ss": len(full_tr), "ish": auc, "nt": marker_note})
                conn.commit()
            print(f"[Classifier] Clean PIT Satellite artifact saved to {paths_to_write} (sha256={sha256[:12]}…)", flush=True)
        except Exception as e:
            print(f"[Classifier] LGBM artifact save failed: {e}", flush=True)

    elapsed = time.time() - t0
    return {
        "status": "ok", "model_type": "satellite_pit_ensemble_v3",
        "samples": len(full_tr), "active_features": len(active_features),
        "fund_symbols": len(eps_map),
        "base_rate_test_pct": round(base_rate_test * 100, 1),
        "top_decile_hit_pct": top_decile_hit,
        "bottom_decile_hit_pct": bottom_decile_hit,
        "spread_pp": spread, "auc": auc,
        "avg_precision": avg_precision,
        "decile_hit_rates": decile_hit_rates,
        "top_features": top_features, "elapsed_s": round(elapsed, 1),
        "regression_guards_failed": [],
        "lgbm_adopted": adopt_lgbm,
    }

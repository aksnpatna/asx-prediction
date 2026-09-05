"""
ETF Pipeline — separate, self-contained trend/momentum strategy.

Design goals (why this is NOT bolted into the stock satellite):
  1. ETFs are beta instruments, not 63-day right-tail hunters. The stock
     model (path-aware +8%/-8% first touch) is the wrong tool for them.
  2. Trend-following + relative momentum is the classic, well-evidenced
     approach that delivers index-like-to-better returns with far lower
     drawdown than buy-and-hold — the honest path to the 12%+ target.
  3. Kept fully separate from the satellite so neither pollutes the other:
     ETFs have no franking-adjusted fundamentals, no earnings, no ASX
     announcements → ~25 of the 45 stock features would zero-fill.

Strategy (the "12% target" logic):
  A. TREND FILTER  — price vs 200-day SMA. Above → risk-on; below → risk-off
     (flight to bonds/cash). This is the single highest-value crash filter.
  B. MOMENTUM RANK — cross-sectional rank of 126-day & 63-day returns among
     the ROTATE universe; hold the top-N with positive momentum.
  C. CORE ANCHOR   — CORE-tier ETFs (VAS/VGS/VTS) held through the cycle for
     broad beta + diversification; reduced (not zeroed) when risk-off.
  D. CRASH VEHICLE — when trend breaks hard, rotate into BOND + CASH tiers.

All signals are computed from local eod_ohl_history (market='AU') and are
dead-simple, transparent, and non-lookahead (SMA/return use prior close).

This module is standalone-safe: it reads OHLC via eodhd_backfill's DB query
path and never imports heavy LLM/gate dependencies.
"""
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from etf_universe import ETFS, etfs_by_tier, all_etf_symbols, get_etf, is_etf

# ── Strategy parameters (single source of truth) ──────────────────────────
SMA_TREND = 200            # trend filter lookback
MOM_FAST = 63              # fast momentum window (trading days)
MOM_SLOW = 126             # slow momentum window
TOP_N = 3                  # number of ROTATE ETFs to hold (momentum picks)
REBALANCE_DAYS = 21        # rebalance ~monthly (21 trading days)
CRASH_SMA = 200            # same as trend filter — drop below = de-risk
RISK_OFF_LOOKBACK = 20     # drawdown-from-peak window for flight signal

# Allocation targets by regime (fractions of NAV)
ALLOC = {
    #           CORE   ROTATE  BOND   CASH
    "risk_on":  (0.55,  0.40,  0.00,  0.05),
    "neutral":  (0.55,  0.30,  0.10,  0.05),
    "risk_off": (0.35,  0.00,  0.45,  0.20),
}

FEE_DRAG_PA = 0.001 + 0.003   # MER + round-trip brokerage/slippage estimate


# ── Data access ────────────────────────────────────────────────────────────
# ETFs and the XJO index are stored under market='ETF' (isolated from the
# stock universe, which reads market='AU'). Read both so this pipeline never
# depends on the stock screen's table layout.
ETF_MARKETS = ("ETF", "AU")


def load_ohlc(symbol: str, years: int = 6) -> pd.DataFrame:
    """Load OHLC from local DB (adjusted_close already stored as close)."""
    from sqlalchemy import text as sqltext
    from main import db_conn

    start = (date.today() - timedelta(days=int(years * 365))).isoformat()
    with db_conn() as conn:
        rows = conn.execute(sqltext(
            "SELECT trade_date, close FROM eod_ohl_history "
            "WHERE symbol = :s AND market IN :mkt AND trade_date >= :d "
            "ORDER BY trade_date ASC"
        ), {"s": symbol, "mkt": ETF_MARKETS, "d": start}).fetchall()

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates(subset="date").set_index("date").sort_index()
    df["close"] = df["close"].astype(float)
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add SMA200, momentum, drawdown columns (non-lookahead)."""
    if df.empty:
        return df
    out = df.copy()
    out["sma200"] = out["close"].rolling(SMA_TREND).mean()
    out["mom63"] = out["close"].pct_change(MOM_FAST)
    out["mom126"] = out["close"].pct_change(MOM_SLOW)
    out["peak"] = out["close"].rolling(RISK_OFF_LOOKBACK).max()
    out["drawdown"] = out["close"] / out["peak"] - 1.0
    out["above_trend"] = out["close"] > out["sma200"]
    return out


# ── Regime detection ───────────────────────────────────────────────────────
def detect_regime(proxy_symbol: str = "VAS") -> Tuple[str, Dict]:
    """Classify regime using the broad market proxy (VAS = ASX300)."""
    df = compute_indicators(load_ohlc(proxy_symbol))
    if df.empty or df["sma200"].isna().all():
        return "neutral", {}
    last = df.iloc[-1]
    above = bool(last.get("above_trend", False))
    dd = float(last.get("drawdown", 0) or 0)
    if not above and dd < -0.08:
        return "risk_off", {"close": float(last["close"]), "sma200": float(last["sma200"]), "drawdown": dd}
    if not above:
        return "neutral", {"close": float(last["close"]), "sma200": float(last["sma200"]), "drawdown": dd}
    return "risk_on", {"close": float(last["close"]), "sma200": float(last["sma200"]), "drawdown": dd}


# ── ETF regime signal (signal-only layer for the SMSF satellite) ───────────
# Reference ETFs for the sector-tilt momentum signal (bare symbols, stored in
# eod_ohl_history without .AX suffix). XJO index is NOT in the OHLC table; it is
# fetched via eodhd_backfill.get_ohlc_for_symbol("XJO") like _get_axjo_vs_sma200.
REFERENCE_ETFS = ["VAS", "TECH", "CURE", "BNKS", "GOLD"]

SECTOR_TILT_MAP = {
    "VAS":  "BROAD",
    "TECH": "TECHNOLOGY",
    "CURE": "HEALTHCARE",
    "BNKS": "FINANCIALS",
    "GOLD": "DEFENSIVE",
}

MOMENTUM_WEIGHTS = {"r3m": 0.20, "r6m": 0.30, "r12m": 0.50}

SATELLITE_CAP_BY_REGIME = {"RISK_ON": 8, "NEUTRAL": 4, "RISK_OFF": 0}


def compute_etf_regime() -> Dict:
    """Signal-only ETF regime filter for satellite deployment.

    Returns regime (RISK_ON/NEUTRAL/RISK_OFF), XJO vs SMA200 ratio, satellite
    position cap, sector tilt (from 3/6/12-month momentum of reference ETFs),
    and a defensive warning when GOLD is the top momentum ETF.

    This is the "signal strengthening" layer: it gates satellite deployment
    BEFORE the portfolio circuit breaker (which only fires on NAV drawdown),
    using the early-warning property proven in the 2022 bear backtest.
    """
    # ── XJO vs 200-day SMA (regime) ──
    xjo_df = load_ohlc("XJO", years=3)
    if xjo_df.empty or len(xjo_df) < 200:
        return {"regime": "UNKNOWN", "reason": "insufficient XJO history",
                "satellite_max_positions": None}
    xjo_close = xjo_df["close"].astype(float)
    current_xjo = float(xjo_close.iloc[-1])
    sma200 = float(xjo_close.rolling(200).mean().iloc[-1])
    if sma200 <= 0:
        return {"regime": "UNKNOWN", "reason": "no valid SMA200"}
    ratio = current_xjo / sma200

    if ratio >= 1.00:
        regime = "RISK_ON"
    elif ratio >= 0.90:
        regime = "NEUTRAL"
    else:
        regime = "RISK_OFF"

    # ── Sector tilt from reference ETF momentum ──
    scores = {}
    for sym in REFERENCE_ETFS:
        df = load_ohlc(sym, years=2)
        if df.empty or len(df) < 130:
            continue
        c = df["close"].astype(float)
        p_now = float(c.iloc[-1])
        p_3m = float(c.iloc[max(0, len(c) - 65)])
        p_6m = float(c.iloc[max(0, len(c) - 130)])
        p_12m = float(c.iloc[0])
        if p_now <= 0 or p_3m <= 0 or p_6m <= 0 or p_12m <= 0:
            continue
        r3m = p_now / p_3m - 1.0
        r6m = p_now / p_6m - 1.0
        r12m = p_now / p_12m - 1.0
        scores[sym] = (MOMENTUM_WEIGHTS["r3m"] * r3m
                       + MOMENTUM_WEIGHTS["r6m"] * r6m
                       + MOMENTUM_WEIGHTS["r12m"] * r12m)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_etf = ranked[0][0] if ranked else None
    gold_rank = next((i for i, (t, _) in enumerate(ranked) if t == "GOLD"), len(ranked))

    return {
        "regime": regime,
        "xjo_vs_sma200": round(ratio, 4),
        "xjo_above_sma200": bool(ratio >= 1.00),
        "xjo_sma200_pct": round((ratio - 1.0) * 100, 2),
        "satellite_max_positions": SATELLITE_CAP_BY_REGIME.get(regime),
        "satellite_permission": "FULL" if regime == "RISK_ON"
                                else "REDUCED" if regime == "NEUTRAL" else "SUSPENDED",
        "sector_tilt": SECTOR_TILT_MAP.get(top_etf, "BROAD"),
        "top_etf": top_etf,
        "gold_rank": gold_rank + 1 if top_etf else None,
        "defensive_warning": bool(top_etf and gold_rank <= 1),
        "etf_momentum_scores": {sym: round(sc, 4) for sym, sc in ranked},
    }


# ── Signal generation ──────────────────────────────────────────────────────
def generate_signals(regime: Optional[str] = None) -> Dict:
    """Produce today's target allocation + momentum picks.

    Returns a dict with regime, weights, buy list, hold list, and defensive
    allocations. Pure function of local DB data.
    """
    regime = regime or detect_regime()[0]
    weights = dict(zip(["core", "rotate", "bond", "cash"], ALLOC[regime]))

    momentum_rank = []
    for sym in etfs_by_tier("ROTATE"):
        df = compute_indicators(load_ohlc(sym))
        if df.empty:
            continue
        last = df.iloc[-1]
        mom = 0.5 * (last.get("mom63", 0) or 0) + 0.5 * (last.get("mom126", 0) or 0)
        above = bool(last.get("above_trend", False))
        momentum_rank.append({
            "symbol": sym,
            "name": get_etf(sym)["name"],
            "mom_63": round((last.get("mom63", 0) or 0), 4),
            "mom_126": round((last.get("mom126", 0) or 0), 4),
            "composite_mom": round(mom, 4),
            "above_trend": above,
            "close": round(float(last["close"]), 4),
        })

    momentum_rank.sort(key=lambda x: x["composite_mom"], reverse=True)
    picks = [r for r in momentum_rank if r["above_trend"] and r["composite_mom"] > 0][:TOP_N]

    core = etfs_by_tier("CORE")
    bonds = etfs_by_tier("BOND")
    cash = etfs_by_tier("CASH")

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "regime": regime,
        "weights": weights,
        "core": core,
        "momentum_picks": [p["symbol"] for p in picks],
        "momentum_rank": momentum_rank,
        "defensive": {"bond": bonds, "cash": cash},
        "target_pa": 0.12,   # the stated 12%+ objective (honest target, see doc)
    }


# ── Backtest ───────────────────────────────────────────────────────────────
def backtest(start: str = "2020-01-01", nav: float = 200_000.0) -> Dict:
    """Monthly-rebalance trend + momentum backtest.

    Rules (non-lookahead, using prior-day closes implied by shifted signals):
      - Each month-end, compute regime from VAS trend.
      - risk_on:  55% core (equal-weight CORE), 40% rotate (top-3 momentum),
      - neutral:  55% core, 30% rotate, 10% bond,
      - risk_off: 35% core, 45% bond, 20% cash.
      - Returns measured close-to-close month-over-month; fee drag applied.

    Honest: no lookahead (signals use month-end close, return measured over
    NEXT month), MER + brokerage drag, and gross-of-franking (franking is
    additive alpha not modelled here → conservative).
    """
    # Load all series once, resample to month-end closes
    series = {}
    for s in all_etf_symbols():
        df = load_ohlc(s, years=8)
        if not df.empty:
            series[s] = df

    if len(series) < 8:
        return {"error": "insufficient ETF OHLC data; run etf backfill first",
                "symbols_loaded": len(series)}

    # Build aligned monthly close matrix
    closes = pd.DataFrame({s: d["close"] for s, d in series.items()})
    monthly = closes.resample("ME").last().ffill()

    # Indicator matrix at month-end (used to set NEXT month's allocation)
    sma200 = closes.rolling(SMA_TREND).mean()
    above_daily = closes > sma200
    mom63 = closes.pct_change(MOM_FAST)
    mom126 = closes.pct_change(MOM_SLOW)

    start_ts = pd.Timestamp(start)
    monthly = monthly[monthly.index >= start_ts]

    # Reindex indicators to monthly index (forward-fill: signal uses last
    # available daily value at/before each month-end — no lookahead).
    above = above_daily.reindex(monthly.index, method="ffill")
    mom63_m = mom63.reindex(monthly.index, method="ffill")
    mom126_m = mom126.reindex(monthly.index, method="ffill")
    dd_daily = (closes / closes.rolling(RISK_OFF_LOOKBACK).max()) - 1.0
    dd_m = dd_daily.reindex(monthly.index, method="ffill")

    dates = monthly.index
    values = [nav]
    regimes = []
    holdings_log = []

    for i in range(1, len(dates)):
        # signals decided at i-1 (prior month-end), return measured over [i-1, i]
        sig_date = dates[i - 1]
        cur = dates[i]
        prev_close = monthly.iloc[i - 1]
        cur_close = monthly.iloc[i]

        # ── regime from VAS at sig_date ──
        vas_above = bool(above.loc[sig_date, "VAS"]) if "VAS" in above.columns else True
        regime = "risk_on" if vas_above else "neutral"
        # risk-off refinement: broad drawdown from 20d peak at sig date
        if not vas_above:
            vas_dd = dd_m.loc[sig_date, "VAS"] if "VAS" in dd_m.columns else 0.0
            if (vas_dd or 0.0) < -0.08:
                regime = "risk_off"

        w = ALLOC[regime]
        regimes.append(regime)

        # ── momentum picks at sig_date among ROTATE ──
        rotate = etfs_by_tier("ROTATE")
        mom_score = {}
        for s in rotate:
            if s in monthly.columns:
                m63 = mom63_m.loc[sig_date, s] if pd.notna(mom63_m.loc[sig_date, s]) else 0.0
                m126 = mom126_m.loc[sig_date, s] if pd.notna(mom126_m.loc[sig_date, s]) else 0.0
                a = above.loc[sig_date, s] if pd.notna(above.loc[sig_date, s]) else False
                composite = 0.5 * (m63 or 0.0) + 0.5 * (m126 or 0.0)
                mom_score[s] = composite if (a and composite > 0) else -0.01
        picks = sorted(mom_score, key=mom_score.get, reverse=True)[:TOP_N] if regime != "risk_off" else []

        # ── build portfolio return for the month ──
        def _ret(s):
            c_prev = prev_close[s]
            c_cur = cur_close[s]
            if c_prev and c_prev > 0 and pd.notna(c_prev) and pd.notna(c_cur):
                return c_cur / c_prev - 1.0
            return 0.0

        port = 0.0
        core_syms = [s for s in etfs_by_tier("CORE") if s in monthly.columns]
        bond_syms = [s for s in etfs_by_tier("BOND") if s in monthly.columns]
        cash_syms = [s for s in etfs_by_tier("CASH") if s in monthly.columns]

        n_core = max(1, len(core_syms))
        n_bond = max(1, len(bond_syms))
        n_cash = max(1, len(cash_syms))

        # CORE sleeve return (equal weight)
        core_ret = sum(_ret(s) for s in core_syms) / n_core
        # ROTATE sleeve return (equal weight of picks)
        if picks:
            rotate_ret = sum(_ret(s) for s in picks if s in monthly.columns) / len([s for s in picks if s in monthly.columns])
        else:
            rotate_ret = 0.0
        # BOND sleeve
        bond_ret = sum(_ret(s) for s in bond_syms) / n_bond
        # CASH sleeve
        cash_ret = sum(_ret(s) for s in cash_syms) / n_cash

        port_ret = (w[0] * core_ret + w[1] * rotate_ret + w[2] * bond_ret + w[3] * cash_ret)
        port_ret -= FEE_DRAG_PA / 12.0

        values.append(values[-1] * (1.0 + port_ret))
        holdings_log.append({
            "date": str(cur.date()),
            "regime": regime,
            "picks": picks,
            "month_ret_pct": round(port_ret * 100, 2),
        })

    # ── performance summary ──
    eq = pd.Series(values, index=dates)
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1.0
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1.0
    monthly_ret = eq.pct_change().dropna()
    vol = monthly_ret.std() * np.sqrt(12)
    sharpe = (monthly_ret.mean() * 12 - 0.04) / vol if vol > 0 else 0.0
    peak = eq.cummax()
    dd = (eq / peak - 1.0).min()

    # regime stats
    from collections import Counter
    rc = Counter(regimes)

    return {
        "start": str(dates[0].date()),
        "end": str(dates[-1].date()),
        "years": round(years, 2),
        "final_value": round(eq.iloc[-1], 2),
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "ann_vol_pct": round(vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(dd * 100, 2),
        "regime_distribution": dict(rc),
        "monthly_log": holdings_log,
    }


def compute_target_size(nav: float, sleeve: str, weight: float) -> Dict:
    """Helper: dollar size for a sleeve at a given weight."""
    return {"sleeve": sleeve, "weight": weight, "aud": round(nav * weight, 2)}


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, json
    p = argparse.ArgumentParser(description="ETF momentum strategy")
    p.add_argument("--signals", action="store_true")
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--regime", action="store_true")
    args = p.parse_args()

    if args.regime:
        r, info = detect_regime()
        print(json.dumps({"regime": r, **info}, indent=2))
    elif args.backtest:
        print(json.dumps(backtest(start=args.start), indent=2, default=str))
    else:
        print(json.dumps(generate_signals(), indent=2, default=str))

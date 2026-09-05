"""
Model-Aware Debate Context (T3-C) — build_model_context.

Turns the 6-persona debate from generic analysis into model-aware challenge:
feeds each persona (via _format_data_blob in agentic_brain.py):
  1. top_features: the LGBM's most important features WITH the candidate's
     actual values — so a persona can say "scored high because pct_institutions
     is elevated, but the news says they reduced their stake".
  2. knn_setups: the 3 most similar historical setups for this symbol from the
     training matrix (same-symbol rows, nearest by euclidean distance on the
     top-importance technical/macro features) with their actual outcomes.
  3. market_percentile: the candidate's RSI/momentum percentile vs all rows
     scored in the last 60 days (anomaly signal).

All sources are local DB/artifact — no LLM calls in this module. Failures
degrade to empty context; the debate still runs.
"""
import json
import time
from typing import Any, Dict, List, Optional

import numpy as np

# RSI/momentum distribution cache for market percentile (15 min TTL)
_MKT_CACHE = {"ts": 0.0, "rsi": None, "mom": None}

# Features excluded from kNN similarity (non-technical; raw matrix JSON
# values for these are 0.0 or build-time fills — similarity on them is
# meaningless for historical rows)
_KNN_EXCLUDE_PREFIXES = ("eps_", "pct_", "insider_", "esg_", "payout_",
                         "fund_", "analyst_count", "xjo_", "vix_", "copper_",
                         "yield_curve_", "aud_usd_")


def _load_artifact():
    try:
        from main import _load_lgbm_classifier
        return _load_lgbm_classifier()
    except Exception:
        return None


def _market_percentile(db_conn) -> Dict[str, Any]:
    now = time.time()
    if _MKT_CACHE["ts"] and now - _MKT_CACHE["ts"] < 900:
        return _MKT_CACHE
    try:
        from sqlalchemy import text as _text
        rsis, moms = [], []
        with db_conn() as conn:
            result = conn.execution_options(
                stream_results=True, max_row_buffer=20000).execute(_text(
                # The matrix lags ~63d behind today (forward-window needs
                # completed outcomes), so use a 180-day window.
                "SELECT features->>'rsi', features->>'momentum_20d' "
                "FROM model_training_set "
                "WHERE signal_date >= CURRENT_DATE - INTERVAL '180 days' "
                "AND features IS NOT NULL"
            ))
            for r, m in result.yield_per(20000):
                try:
                    rsis.append(float(r))
                    moms.append(float(m))
                except Exception:
                    continue
        _MKT_CACHE["ts"] = now
        _MKT_CACHE["rsi"] = np.asarray(rsis)
        _MKT_CACHE["mom"] = np.asarray(moms)
    except Exception:
        pass
    return _MKT_CACHE


def _percentile(arr, v):
    if arr is None or len(arr) == 0 or v is None:
        return None
    return round(float((arr <= v).mean() * 100), 1)


def _knn_setups(symbol: str, feat_values: Dict[str, float], artifact: Dict,
                db_conn=None, k: int = 5) -> List[Dict]:
    if db_conn is None or not artifact:
        return []
    try:
        from sqlalchemy import text as _text
        order = artifact["feature_order"]
        importances = None
        try:
            importances = np.asarray(artifact["model"].feature_importances_)
        except Exception:
            pass
        if importances is not None:
            top_idx = np.argsort(importances)[::-1]
            sim_feats = []
            for i in top_idx:
                f = order[i]
                if f in feat_values and not f.startswith(_KNN_EXCLUDE_PREFIXES):
                    sim_feats.append(f)
                if len(sim_feats) >= 10:
                    break
        else:
            sim_feats = [f for f in order if not f.startswith(_KNN_EXCLUDE_PREFIXES)][:10]

        rows = []
        with db_conn() as conn:
            result = conn.execution_options(
                stream_results=True, max_row_buffer=20000).execute(_text(
                "SELECT signal_date, features, hit_8pct_before_m8pct, "
                "hit_8pct_first_touch, COALESCE(forward_return_63d, 0) "
                "FROM model_training_set "
                "WHERE symbol = :sym AND features IS NOT NULL "
                "AND signal_date >= CURRENT_DATE - INTERVAL '12 months' "
                "ORDER BY signal_date DESC LIMIT 400"
            ), {"sym": symbol})
            for r in result.yield_per(20000):
                try:
                    d, feats_raw, hit_c, hit_ft, fwd = r
                    feats = json.loads(feats_raw) if isinstance(feats_raw, str) else (feats_raw or {})
                    vec = np.array([float(feats.get(f, 0) or 0) for f in sim_feats])
                    rows.append((str(d), vec, hit_c, hit_ft, float(fwd)))
                except Exception:
                    continue
        if not rows:
            return []

        cur = np.array([float(feat_values.get(f, 0) or 0) for f in sim_feats])
        dists = np.array([float(np.linalg.norm(v - cur)) for _, v, _, _, _ in rows])
        order_n = np.argsort(dists)[:k]

        out = []
        for i in order_n:
            d, _, hit_c, hit_ft, fwd = rows[i]
            out.append({
                "signal_date": d,
                "distance": round(float(dists[i]), 3),
                "hit_8pct_before_m8pct": bool(hit_c),
                "hit_8pct_first_touch": bool(hit_ft),
                "forward_return_63d_pct": round(fwd, 1),
            })
        return out
    except Exception:
        return []


def _prior_verdicts(symbol: str, db_conn=None, days: int = 3) -> List[Dict]:
    """Recent AI verdicts for this symbol (last N trading days) — anchors the
    debate to prior decisions instead of blank-slate re-analysis each morning.

    Priority 2 (ai_review.md): the AI currently has no continuity. Feeding the
    last 3 days of verdicts lets it say "REJECTED yesterday for X — has X
    resolved?" rather than re-judging from scratch.
    """
    if db_conn is None:
        return []
    try:
        from sqlalchemy import text as _text
        with db_conn() as conn:
            rows = conn.execute(_text(
                "SELECT run_date, decision, confidence "
                "FROM ai_verdicts "
                "WHERE symbol = :sym AND run_date >= CURRENT_DATE - INTERVAL '7 days' "
                "ORDER BY run_date DESC LIMIT :d"
            ), {"sym": symbol.upper(), "d": days}).fetchall()
        out = []
        for r in rows:
            out.append({"date": str(r[0]), "decision": r[1], "confidence": r[2]})
        return out
    except Exception:
        return []


def _compact_features(symbol: str, technicals: Dict, valuation: Dict,
                      candidate: Optional[Dict]) -> Dict[str, float]:
    """Approximate feature values when the enriched _feat_row is unavailable."""
    tech = technicals or {}
    val = valuation or {}
    cand = candidate or {}
    macro = {}
    try:
        from main import _get_macro_data_cached
        macro = _get_macro_data_cached() or {}
    except Exception:
        pass
    feats = {
        "rsi": float(tech.get("rsi", 50) or 50),
        "momentum_20d": float(cand.get("rel_strength_3m", 0) or 0) * 0.3,
        "momentum_63d": float(cand.get("rel_strength_3m", 0) or 0),
        "hv_20d": float(tech.get("volatility", 0.25) or 0.25),
        "atr_pct": float(tech.get("atr_pct", 0.02) or 0.02),
        "adx": float(tech.get("adx", 18) or 18),
        "macd_hist": 0.5 if tech.get("macd", 0) > tech.get("macd_signal", 0) else -0.5,
        "volume_spike": 1.5 if tech.get("block_volume_detected") else 1.0,
        "vix_level": float(macro.get("vix", {}).get("current", 20) or 20),
        "fund_pe_inv": round(100.0 / float(val.get("trailing_pe", 0)), 2) if float(val.get("trailing_pe", 0) or 0) > 0 else 0.0,
        "pct_institutions": float(val.get("pct_institutions", 0) or 0),
    }
    return feats


def build_model_context(symbol: str, market: str, technicals: Optional[Dict],
                        valuation: Optional[Dict], candidate: Optional[Dict] = None,
                        db_conn=None, artifact: Optional[Dict] = None) -> Dict[str, Any]:
    """Build the model-aware context block for the AI debate. Never raises."""
    ctx: Dict[str, Any] = {}
    try:
        if artifact is None:
            artifact = _load_artifact()
        if candidate:
            ctx["model_score"] = candidate.get("_model_score")
            ctx["tier_label"] = candidate.get("_tier_label")
            ctx["model_tier"] = candidate.get("_target_tier")

        feat_values = None
        if candidate and candidate.get("_feat_row") and artifact:
            order = artifact["feature_order"]
            feat_values = dict(zip(order, candidate["_feat_row"]))
        if feat_values is None:
            feat_values = _compact_features(symbol, technicals, valuation, candidate)

        if artifact:
            importances = None
            try:
                importances = np.asarray(artifact["model"].feature_importances_)
            except Exception:
                importances = None
            top_features = []
            if importances is not None:
                order = artifact["feature_order"]
                for i in np.argsort(importances)[::-1][:8]:
                    fname = order[i]
                    val = feat_values.get(fname)
                    if val is None or not np.isfinite(val):
                        continue
                    top_features.append((f"{fname} (imp {importances[i]:.3f})", float(val)))
            if top_features:
                ctx["top_features"] = top_features[:5]

        ctx["knn_setups"] = _knn_setups(symbol, feat_values, artifact, db_conn)

        # ── Priority 2 input-quality enrichments (ai_review.md) ─────────────
        # days_to_earnings + short_ratio + prior_verdicts — qualitative event &
        # continuity context the model is blind to but the debate should see.
        val = valuation or {}
        try:
            if val.get("days_to_earnings") is not None:
                ctx["days_to_earnings"] = val.get("days_to_earnings")
            if val.get("next_earnings_date"):
                ctx["next_earnings_date"] = val.get("next_earnings_date")
        except Exception:
            pass
        try:
            sr = val.get("short_pct_float") or val.get("short_ratio")
            if sr is not None:
                ctx["short_ratio"] = sr
        except Exception:
            pass
        try:
            pv = _prior_verdicts(symbol, db_conn)
            if pv:
                ctx["prior_verdicts"] = pv
        except Exception:
            pass

        mkt = _market_percentile(db_conn) if db_conn else _MKT_CACHE
        ctx["sector_peer_comparison"] = {
            "rsi_rank": _percentile(mkt.get("rsi"), feat_values.get("rsi")),
            "momentum_rank": _percentile(mkt.get("mom"), feat_values.get("momentum_20d")),
        }
    except Exception:
        pass
    return ctx

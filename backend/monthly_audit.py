"""
SMSF v4 Monthly Macro & Red-Flag News Audit Engine.

Separates monthly read-only monitoring (macro regime + per-holding news/earnings
red-flag check) from quarterly portfolio rebalancing (63-trading-day cadence).

News sources (in priority order):
  1. Tavily Search API (when TAVILY_API_KEY is configured in .env)
  2. EODHD Financial News API (using existing EODHD_API_KEY) + Google News RSS
"""

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import text


RED_FLAG_KEYWORDS = (
    "guidance downgrade", "profit warning", "trading halt", "capital raise",
    "placement", "class action", "asic investigation", "ceo resign", "ceo exit",
    "earnings miss", "downgrades fy", "cuts guidance", "impairment",
)

POSITIVE_KEYWORDS = (
    "guidance upgrade", "raises guidance", "reaffirms guidance", "beats expectations",
    "ebitda up", "profit rises", "buyback", "contract win", "record revenue",
    "dividend increase", "strong growth",
)


def ensure_monthly_audit_table(db_conn: Callable) -> None:
    with db_conn() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS monthly_macro_audits (
                id SERIAL PRIMARY KEY,
                run_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                user_id TEXT,
                trigger_source VARCHAR(32) DEFAULT 'manual_ui',
                news_source VARCHAR(64) DEFAULT 'eodhd+rss',
                regime VARCHAR(32) DEFAULT 'neutral',
                rebalance_action VARCHAR(48) DEFAULT 'HOLD',
                macro_summary TEXT,
                macro_bullets_json TEXT,
                holdings_json TEXT,
                next_quarterly_rebalance DATE,
                red_flag_count INTEGER DEFAULT 0
            )
        """))
        conn.commit()


def _next_quarterly_date(today: Optional[date] = None) -> date:
    d = today or date.today()
    for m in (2, 5, 8, 11):
        cand = date(d.year, m, 23)
        if cand > d:
            return cand
    return date(d.year + 1, 2, 23)


def _fetch_tavily(query: str, max_results: int = 4) -> List[Dict[str, str]]:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        res = client.search(query=query, max_results=max_results, search_depth="basic")
        items = []
        for r in (res.get("results") or [])[:max_results]:
            items.append({
                "title": str(r.get("title") or "")[:180],
                "snippet": str(r.get("content") or "")[:360],
                "url": str(r.get("url") or ""),
                "source": "tavily",
            })
        return items
    except Exception as e:
        print(f"[MonthlyAudit] Tavily search fallback ({query[:40]}): {e}", flush=True)
        return []


def _fetch_eodhd_news(symbol: str, limit: int = 4) -> List[Dict[str, str]]:
    api_key = os.getenv("EODHD_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        ticker = f"{symbol.upper()}.AU"
        url = f"https://eodhd.com/api/news?s={urllib.parse.quote(ticker)}&limit={limit}&api_token={api_key}&fmt=json"
        req = urllib.request.Request(url, headers={"User-Agent": "ASX-SMSF-Audit/4.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        items = []
        if isinstance(data, list):
            for r in data[:limit]:
                items.append({
                    "title": str(r.get("title") or "")[:180],
                    "snippet": str(r.get("content") or "")[:320],
                    "url": str(r.get("link") or ""),
                    "date": str(r.get("date") or "")[:10],
                    "source": "eodhd",
                })
        return items
    except Exception:
        return []


def _fetch_google_news_rss(query: str, limit: int = 4) -> List[Dict[str, str]]:
    try:
        q = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={q}&hl=en-AU&gl=AU&ceid=AU:en"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)
        items = []
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            items.append({
                "title": title[:180],
                "snippet": f"{title} ({pub[:16]})" if pub else title,
                "url": link,
                "source": "google_rss",
            })
        return items
    except Exception:
        return []


def _gather_news(query: str, symbol: Optional[str] = None, limit: int = 4) -> tuple[List[Dict[str, str]], str]:
    tav = _fetch_tavily(query, max_results=limit)
    if tav:
        return tav, "tavily"
    items: List[Dict[str, str]] = []
    sources = []
    if symbol:
        eod = _fetch_eodhd_news(symbol, limit=limit)
        if eod:
            items.extend(eod)
            sources.append("eodhd")
    if len(items) < limit:
        rss = _fetch_google_news_rss(query, limit=limit - len(items))
        if rss:
            items.extend(rss)
            sources.append("google_rss")
    return items[:limit], ("+".join(sources) if sources else "db_only")


def _call_llm_json(prompt: str) -> Optional[Dict[str, Any]]:
    """Invoke the existing LLM provider chain (Local / NVIDIA / DeepSeek / Groq) and parse JSON."""
    try:
        import agentic_brain
        model_name = getattr(agentic_brain, "CIO_MODEL", None) or getattr(agentic_brain, "ANALYST_MODEL", None) or os.getenv("LOCAL_LLM_MODEL", "")
        llm = agentic_brain._make_llm(model_name, 0.1)
        if llm is None:
            return None
        resp = llm.invoke(prompt)
        content = getattr(resp, "content", str(resp)) or ""
        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        print(f"[MonthlyAudit] LLM synthesis fallback: {e}", flush=True)
    return None


def _heuristic_verdict(symbol: str, sleeve: str, pnl_pct: float, above_sma200: Optional[bool], news_items: List[Dict[str, str]]) -> Dict[str, Any]:
    blob = " ".join((n.get("title", "") + " " + n.get("snippet", "")) for n in news_items).lower()
    red_hits = [k for k in RED_FLAG_KEYWORDS if k in blob]
    pos_hits = [k for k in POSITIVE_KEYWORDS if k in blob]

    if sleeve == "core":
        if above_sma200 is False and pnl_pct < -8.0:
            verdict = "KEEP_WATCH"
            summary = "Core ETF below 200-day trend; hold core allocation and review weight at quarterly rebalance."
        else:
            verdict = "STRONG_KEEP"
            summary = "Core ETF compounding anchor — hold untouched until quarterly contribution rebalance."
    else:
        if red_hits:
            verdict = "RED_FLAG_EXIT"
            summary = f"Red-flag news detected ({', '.join(red_hits[:2])}). Review for early exit."
        elif above_sma200 is False or pnl_pct <= -10.0:
            verdict = "KEEP_WATCH"
            summary = "Momentum softening or approaching -12% stop-loss; monitor closely."
        elif pos_hits:
            verdict = "STRONG_KEEP"
            summary = f"Positive fundamental catalysts ({', '.join(pos_hits[:2])}) with intact uptrend."
        else:
            verdict = "KEEP_WATCH" if pnl_pct < -4.0 else "STRONG_KEEP"
            summary = "No fundamental red flags detected in latest headlines; hold for 63-day quarterly cycle."

    return {
        "verdict": verdict,
        "summary": summary,
        "red_flags": red_hits[:3],
    }


def run_monthly_audit(
    db_conn: Callable,
    user_id: Optional[str] = None,
    trigger_source: str = "manual_ui",
) -> Dict[str, Any]:
    ensure_monthly_audit_table(db_conn)

    # 1. Load ETF regime signal + user's open positions + 200d SMA status
    with db_conn() as conn:
        etf_row = conn.execute(text(
            "SELECT run_date, regime, payload FROM etf_signals ORDER BY run_date DESC LIMIT 1"
        )).fetchone()
        etf_regime = str(etf_row[1] or "neutral").lower() if etf_row else "neutral"
        etf_payload = {}
        if etf_row and etf_row[2]:
            try:
                etf_payload = json.loads(etf_row[2]) if isinstance(etf_row[2], str) else etf_row[2]
            except Exception:
                etf_payload = {}

        if not user_id:
            u_row = conn.execute(text("""
                SELECT id FROM users
                WHERE LOWER(email) NOT LIKE '%@example.com'
                  AND LOWER(email) NOT LIKE '%@test.com'
                  AND LOWER(email) NOT LIKE '%@wealth.local'
                  AND LOWER(email) NOT LIKE '%@smsf.com'
                  AND LOWER(email) NOT LIKE 'test%'
                ORDER BY created_at ASC LIMIT 1
            """)).fetchone()
            user_id = str(u_row[0]) if u_row else "default"

        pos_rows = conn.execute(text("""
            SELECT id, symbol, COALESCE(sleeve, 'satellite'), COALESCE(market, 'AU'),
                   COALESCE(sector, ''), quantity, entry_price, current_price,
                   stop_loss_price, time_stop_date
            FROM paper_trades
            WHERE user_id = :u AND status = 'open'
            ORDER BY CASE WHEN COALESCE(sleeve,'satellite')='core' THEN 0 ELSE 1 END,
                     (entry_price * quantity) DESC
        """), {"u": user_id}).fetchall()

        sma_map: Dict[str, Optional[float]] = {}
        px_map: Dict[str, float] = {}
        for r in pos_rows:
            sym, mkt = str(r[1]).upper(), str(r[3] or "AU")
            rows_200 = conn.execute(text(
                "SELECT close FROM eod_ohl_history WHERE symbol=:s AND market=:m AND close > 0 ORDER BY trade_date DESC LIMIT 200"
            ), {"s": sym, "m": mkt}).fetchall()
            closes = [float(x[0]) for x in rows_200 if x[0]]
            if closes:
                px_map[sym] = closes[0]
                sma_map[sym] = (sum(closes) / len(closes)) if len(closes) >= 150 else None

    # 2. Gather live macro news + per-holding news (Tavily if key set, else EODHD + Google News RSS)
    used_sources = set()
    macro_news, m_src = _gather_news("ASX 200 RBA interest rates US equities gold market outlook Australia", limit=5)
    used_sources.add(m_src)

    holdings_audit: List[Dict[str, Any]] = []
    for r in pos_rows:
        tid, sym, sleeve, mkt, sector, qty, ep, cp, sl, ts_dt = r
        sym_u = str(sym).upper()
        ep_f = float(ep or 0)
        live_p = float(px_map.get(sym_u) or cp or ep_f or 0)
        pnl_pct = round(((live_p - ep_f) / ep_f * 100.0), 2) if ep_f > 0 else 0.0
        sma200 = sma_map.get(sym_u)
        above_sma200 = (live_p > sma200) if (sma200 and live_p > 0) else None

        if sleeve == "satellite":
            news_items, src = _gather_news(f"{sym_u} ASX earnings guidance news", symbol=sym_u, limit=3)
        else:
            news_items, src = _gather_news(f"{sym_u} ETF ASX outlook", symbol=None, limit=2)
        used_sources.add(src)

        h = _heuristic_verdict(sym_u, sleeve, pnl_pct, above_sma200, news_items)
        holdings_audit.append({
            "trade_id": str(tid),
            "symbol": sym_u,
            "sleeve": sleeve,
            "sector": sector,
            "entry_price": round(ep_f, 3),
            "current_price": round(live_p, 3),
            "pnl_pct": pnl_pct,
            "alloc_aud": round(live_p * float(qty or 0), 0),
            "above_sma200": above_sma200,
            "stop_loss_price": round(float(sl or 0), 3),
            "time_stop_date": str(ts_dt) if ts_dt else None,
            "verdict": h["verdict"],
            "summary": h["summary"],
            "red_flags": h["red_flags"],
            "headlines": [{"title": n.get("title", ""), "url": n.get("url", ""), "source": n.get("source", "")} for n in news_items[:3]],
        })

    # 3. Synthesize with LLM (if available) for clean macro regime + per-holding red-flag refinement
    macro_headlines_txt = "\n".join(f"- {n['title']}" for n in macro_news[:5]) or "- No macro headlines retrieved"
    sat_ctx_lines = []
    for h in holdings_audit:
        if h["sleeve"] == "satellite":
            hl = "; ".join(x["title"] for x in h["headlines"][:3]) or "No recent headlines"
            sat_ctx_lines.append(
                f"- {h['symbol']} ({h['sector']}, P&L {h['pnl_pct']:+.1f}%, >200dSMA={h['above_sma200']}): {hl}"
            )
    sat_ctx_txt = "\n".join(sat_ctx_lines) or "- No satellite positions open"

    prompt = (
        "You are an institutional Australian SMSF portfolio risk officer conducting a MONTHLY READ-ONLY AUDIT.\n"
        f"Internal ETF Regime Signal: {etf_regime.upper()}\n"
        f"Latest Macro Headlines:\n{macro_headlines_txt}\n\n"
        f"Open Satellite Positions & Headlines:\n{sat_ctx_txt}\n\n"
        "Respond ONLY with valid JSON matching this schema:\n"
        "{\n"
        '  "regime": "risk_on" | "neutral" | "defensive",\n'
        '  "macro_summary": "2 concise sentences summarizing RBA/ASX, Global/US equities, and Gold regime.",\n'
        '  "macro_bullets": ["Australia/RBA bullet", "Global/US Tech bullet", "Gold/Commodities bullet"],\n'
        '  "satellite_verdicts": {"SYMBOL": {"verdict": "STRONG_KEEP"|"KEEP_WATCH"|"RED_FLAG_EXIT", "summary": "1 concise sentence"}}\n'
        "}\n"
        "Only assign RED_FLAG_EXIT if there is a genuine fundamental red flag (guidance downgrade, capital raise, trading halt, CEO exit, fraud/litigation). Never recommend selling for minor market noise."
    )

    llm_out = _call_llm_json(prompt)
    final_regime = etf_regime
    macro_summary = (
        f"Market regime is {etf_regime.upper()}. Core 80% Global+ASX ETF sleeve remains anchored for quarterly compounding; "
        "Satellite holdings are monitored monthly for fundamental red flags only."
    )
    macro_bullets = [
        f"Internal ETF Regime Model: {etf_regime.upper()} (next quarterly rebalance {_next_quarterly_date().isoformat()}).",
        f"Macro headlines scanned: {macro_news[0]['title']}" if macro_news else "Macro feed: using internal ETF trend & volatility regime.",
        "Rule: Hold Core ETFs & Satellite for 63 trading days unless a fundamental red flag or -12% stop triggers.",
    ]

    if isinstance(llm_out, dict):
        if llm_out.get("regime") in ("risk_on", "neutral", "defensive"):
            final_regime = llm_out["regime"]
        if llm_out.get("macro_summary"):
            macro_summary = str(llm_out["macro_summary"])[:600]
        if isinstance(llm_out.get("macro_bullets"), list) and llm_out["macro_bullets"]:
            macro_bullets = [str(b)[:260] for b in llm_out["macro_bullets"][:4]]
        sv = llm_out.get("satellite_verdicts") or {}
        if isinstance(sv, dict):
            for h in holdings_audit:
                if h["symbol"] in sv and isinstance(sv[h["symbol"]], dict):
                    v_new = sv[h["symbol"]].get("verdict")
                    if v_new in ("STRONG_KEEP", "KEEP_WATCH", "RED_FLAG_EXIT"):
                        h["verdict"] = v_new
                    if sv[h["symbol"]].get("summary"):
                        h["summary"] = str(sv[h["symbol"]]["summary"])[:280]

    red_flags = [h for h in holdings_audit if h["verdict"] == "RED_FLAG_EXIT"]
    next_qtr = _next_quarterly_date()
    days_to_qtr = (next_qtr - date.today()).days

    if red_flags:
        rebalance_action = "REVIEW_RED_FLAG"
    elif days_to_qtr <= 5:
        rebalance_action = "QUARTERLY_REBALANCE_DUE"
    else:
        rebalance_action = "HOLD"

    news_source_str = "tavily" if "tavily" in used_sources else ("+".join(sorted(used_sources - {"db_only"})) or "db_only")

    with db_conn() as conn:
        row = conn.execute(text("""
            INSERT INTO monthly_macro_audits
                (user_id, trigger_source, news_source, regime, rebalance_action,
                 macro_summary, macro_bullets_json, holdings_json,
                 next_quarterly_rebalance, red_flag_count)
            VALUES
                (:u, :ts, :ns, :reg, :act, :ms, :mb, :hj, :nq, :rf)
            RETURNING id, run_at
        """), {
            "u": user_id,
            "ts": trigger_source,
            "ns": news_source_str,
            "reg": final_regime,
            "act": rebalance_action,
            "ms": macro_summary,
            "mb": json.dumps(macro_bullets),
            "hj": json.dumps(holdings_audit),
            "nq": next_qtr,
            "rf": len(red_flags),
        }).fetchone()
        conn.commit()

    return {
        "id": row[0],
        "run_at": row[1].isoformat() if row[1] else datetime.utcnow().isoformat(),
        "trigger_source": trigger_source,
        "news_source": news_source_str,
        "tavily_configured": bool(os.getenv("TAVILY_API_KEY", "").strip()),
        "regime": final_regime,
        "rebalance_action": rebalance_action,
        "macro_summary": macro_summary,
        "macro_bullets": macro_bullets,
        "holdings": holdings_audit,
        "next_quarterly_rebalance": next_qtr.isoformat(),
        "days_to_quarterly_rebalance": days_to_qtr,
        "red_flag_count": len(red_flags),
    }


def get_latest_monthly_audit(db_conn: Callable, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    ensure_monthly_audit_table(db_conn)
    with db_conn() as conn:
        if user_id:
            row = conn.execute(text("""
                SELECT id, run_at, trigger_source, news_source, regime, rebalance_action,
                       macro_summary, macro_bullets_json, holdings_json,
                       next_quarterly_rebalance, red_flag_count
                FROM monthly_macro_audits
                WHERE user_id = :u
                ORDER BY run_at DESC LIMIT 1
            """), {"u": user_id}).fetchone()
        else:
            row = None
        if not row:
            row = conn.execute(text("""
                SELECT id, run_at, trigger_source, news_source, regime, rebalance_action,
                       macro_summary, macro_bullets_json, holdings_json,
                       next_quarterly_rebalance, red_flag_count
                FROM monthly_macro_audits
                ORDER BY run_at DESC LIMIT 1
            """)).fetchone()
    if not row:
        return None
    nq = row[9]
    days_to_qtr = (nq - date.today()).days if isinstance(nq, date) else 90
    return {
        "id": row[0],
        "run_at": row[1].isoformat() if row[1] else None,
        "trigger_source": row[2],
        "news_source": row[3],
        "tavily_configured": bool(os.getenv("TAVILY_API_KEY", "").strip()),
        "regime": row[4],
        "rebalance_action": row[5],
        "macro_summary": row[6],
        "macro_bullets": json.loads(row[7]) if row[7] else [],
        "holdings": json.loads(row[8]) if row[8] else [],
        "next_quarterly_rebalance": str(nq) if nq else None,
        "days_to_quarterly_rebalance": days_to_qtr,
        "red_flag_count": int(row[10] or 0),
    }

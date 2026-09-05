"""
ASX Announcement NLP Features (T4-A) — announcement_features.py.

Novel alpha EODHD does not have: guidance revisions, management confidence,
and sentiment extracted from ASX announcement headlines.

Pipeline (daily job `_scheduled_announcement_features` in main.py):
  1. Symbols: open paper positions + top-tier candidates from the latest
     wealth scan (capped by ANNOUNCEMENT_NLP_MAX_SYMBOLS, default 25).
  2. Fetches the free ASX JSON feed (same endpoint as announcement_monitor).
  3. One LLM call per symbol scores all recent announcements:
     sentiment [-1,1], guidance_revision [-1,0,1], mgmt_confidence_delta [-1,0,1].
  4. Persists to announcement_features; per-symbol aggregates feed the model
     via smsf_classifier._load_announcement_features / _fill_announcement.

Training integration is self-guarding: the 3 feature keys were added to
FEATURE_COLS; historical rows have no announcement data so they are dropped
as zero-variance until the table accumulates coverage. No regression risk.

LLM: reuses the provider stack from agentic_brain (deepseek/groq/nvidia/
local per LLM_PROVIDER_ORDER) — free tier at this volume.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

ANNOUNCEMENT_CHECK_URL = "https://asx.api.markitdigital.com/asx-research/1.0/companies/{code}/announcements"

# ── Configuration ────────────────────────────────────────────────────────────
MAX_SYMBOLS = int(os.getenv("ANNOUNCEMENT_NLP_MAX_SYMBOLS", "50"))
LOOKBACK_DAYS = int(os.getenv("ANNOUNCEMENT_NLP_LOOKBACK_DAYS", "7"))
MAX_ANNS_PER_SYMBOL = 10

_JSON_RE = re.compile(r"\[[\s\S]*\]")


def _make_llm():
    from agentic_brain import _make_llm as _brain_llm, GROQ_MODEL_STANDARD
    return _brain_llm(GROQ_MODEL_STANDARD, temperature=0.0)


def _ensure_table(db_conn) -> None:
    from sqlalchemy import text
    with db_conn() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS announcement_features (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                ann_date DATE NOT NULL,
                title TEXT NOT NULL,
                sentiment REAL DEFAULT 0,
                guidance_revision INTEGER DEFAULT 0,
                mgmt_confidence_delta INTEGER DEFAULT 0,
                raw_json TEXT,
                scored_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (symbol, ann_date, title)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_ann_features_symbol_date "
            "ON announcement_features (symbol, ann_date DESC)"))
        conn.commit()


def _fetch_announcements(code: str) -> List[Dict]:
    try:
        r = requests.get(ANNOUNCEMENT_CHECK_URL.format(code=code.upper()),
                         params={"count": MAX_ANNS_PER_SYMBOL},
                         headers={"Accept": "application/json"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        # markitdigital response: {"data": {"items": [ {...} ]}}
        anns = (data.get("data", {}) or {}).get("items", []) if isinstance(data, dict) else []
        cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
        out = []
        for ann in anns:
            try:
                ann_date_str = ann.get("date", "")
                # e.g. "2026-07-15T22:39:08.000Z"
                if "T" in ann_date_str:
                    ann_dt = datetime.strptime(ann_date_str[:19], "%Y-%m-%dT%H:%M:%S")
                else:
                    ann_dt = datetime.strptime(ann_date_str[:10], "%Y-%m-%d")
                if ann_dt < cutoff:
                    continue
                title = (ann.get("headline", "") or ann.get("title", "")).strip()
                if not title:
                    continue
                out.append({"date": ann_dt.strftime("%Y-%m-%d"),
                            "title": title,
                            "url": ann.get("url", "") or ann.get("pdf_url", "") or ""})
            except Exception:
                continue
        return out
    except Exception:
        return []


def _score_with_llm(symbol: str, anns: List[Dict]) -> List[Dict]:
    if not anns:
        return []
    llm = _make_llm()
    listing = "\n".join(f"{i+1}. [{a['date']}] {a['title']}" for i, a in enumerate(anns))
    prompt = (
        f"Score these ASX company announcements for {symbol}. "
        "For each, output JSON with keys: idx (number), sentiment (float -1..1, "
        "negative=dilution/warnings, positive=upgrades/partnerships), "
        "guidance_revision (-1 downgrade, 0 none, +1 upgrade), "
        "mgmt_confidence_delta (-1 reduced confidence, 0 neutral, +1 raised).\n"
        "Respond with a JSON array only, no prose.\n\n" + listing
    )
    try:
        resp = llm.invoke(prompt)
        text = resp.content if hasattr(resp, "content") else str(resp)
        m = _JSON_RE.search(text)
        if not m:
            print(f"[AnnNLP] {symbol}: no JSON in LLM reply", flush=True)
            return []
        scored = json.loads(m.group(0))
        out = []
        for s in scored:
            if not isinstance(s, dict) or "idx" not in s:
                continue
            i = int(s["idx"]) - 1
            if 0 <= i < len(anns):
                out.append({
                    **anns[i],
                    "sentiment": max(-1.0, min(1.0, float(s.get("sentiment", 0) or 0))),
                    "guidance_revision": int(max(-1, min(1, int(s.get("guidance_revision", 0) or 0)))),
                    "mgmt_confidence_delta": int(max(-1, min(1, int(s.get("mgmt_confidence_delta", 0) or 0)))),
                })
        return out
    except Exception as e:
        print(f"[AnnNLP] {symbol} scoring failed: {e}", flush=True)
        return []


def process_symbols(symbols: List[str], db_conn) -> Dict:
    _ensure_table(db_conn)
    from sqlalchemy import text
    results = {"scored": 0, "symbols": 0, "errors": 0}
    for sym in symbols[:MAX_SYMBOLS]:
        anns = _fetch_announcements(sym)
        if not anns:
            continue
        scored = _score_with_llm(sym, anns)
        if not scored:
            results["errors"] += 1
            continue
        try:
            with db_conn() as conn:
                for s in scored:
                    conn.execute(text(
                        "INSERT INTO announcement_features "
                        "(symbol, ann_date, title, sentiment, guidance_revision, "
                        "mgmt_confidence_delta, raw_json) VALUES "
                        "(:sym, :d, :t, :s, :g, :m, :r) "
                        "ON CONFLICT (symbol, ann_date, title) DO UPDATE SET "
                        "sentiment=EXCLUDED.sentiment, "
                        "guidance_revision=EXCLUDED.guidance_revision, "
                        "mgmt_confidence_delta=EXCLUDED.mgmt_confidence_delta"),
                        {"sym": sym, "d": s["date"], "t": s["title"],
                         "s": s["sentiment"], "g": s["guidance_revision"],
                         "m": s["mgmt_confidence_delta"], "r": json.dumps(s)})
                conn.commit()
            results["scored"] += len(scored)
            results["symbols"] += 1
            print(f"[AnnNLP] {sym}: {len(scored)} announcements scored", flush=True)
        except Exception as e:
            print(f"[AnnNLP] {sym} persist failed: {e}", flush=True)
            results["errors"] += 1
        time.sleep(0.3)
    return results


# ── Feature accessors (used by smsf_classifier + live scoring) ───────────────

def load_announcement_features(db_conn) -> Dict[str, Dict[str, float]]:
    """symbol -> {ann_sentiment_7d, guidance_revision_score, mgmt_confidence_delta}
    aggregates over the last 7 days (guidance over 30)."""
    from sqlalchemy import text
    out: Dict[str, Dict[str, float]] = {}
    try:
        with db_conn() as conn:
            rows = conn.execute(text(
                "SELECT symbol, "
                "AVG(sentiment) FILTER (WHERE ann_date >= CURRENT_DATE - 7) AS sent7, "
                "SUM(guidance_revision) FILTER (WHERE ann_date >= CURRENT_DATE - 30) AS guid30, "
                "AVG(mgmt_confidence_delta) FILTER (WHERE ann_date >= CURRENT_DATE - 7) AS mgmt7 "
                "FROM announcement_features GROUP BY symbol"
            )).fetchall()
            for sym, s7, g30, m7 in rows:
                out[sym] = {
                    "ann_sentiment_7d": round(float(s7 or 0), 3),
                    "guidance_revision_score": max(-3.0, min(3.0, float(g30 or 0))),
                    "mgmt_confidence_delta": round(float(m7 or 0), 3),
                }
    except Exception as e:
        print(f"[AnnNLP] load failed: {e}", flush=True)
    return out

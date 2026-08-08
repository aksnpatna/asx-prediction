"""
ASX Announcement Monitor — polls the free ASX JSON feed for open positions.

Runs every 60 minutes during market hours (10:00–16:30 AEST).
Critical keywords trigger immediate sentinel review.
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional

CRITICAL_KEYWORDS = [
    "capital raising", "placement", "rights issue", "entitlement offer",
    "trading halt", "voluntary suspension", "cease trading",
    "administration", "receivership", "wind up",
    "profit warning", "downgrade", "guidance cut",
    "material adverse", "impairment", "write-down",
]

WATCH_KEYWORDS = [
    "acquisition", "merger", "takeover", "divestment",
    "board change", "ceo", "chairman", "director resignation",
    "class action", "regulatory", "asx query",
    "substantial holder", "becoming substantial",
]

ANNOUNCEMENT_CHECK_URL = "https://www.asx.com.au/asx/1/company/{code}/announcements"


def check_symbol_announcements(code: str) -> Dict:
    results = {
        "status": "CLEAR",
        "code": code.upper(),
        "critical_flags": [],
        "watch_flags": [],
        "recent_count": 0,
    }

    try:
        r = requests.get(
            ANNOUNCEMENT_CHECK_URL.format(code=code.upper()),
            params={"count": 20},
            timeout=10,
        )
        if r.status_code != 200:
            results["status"] = "API_ERROR"
            return results

        data = r.json()
        announcements = data.get("data", []) if isinstance(data, dict) else data if isinstance(data, list) else []

        cutoff = datetime.now() - timedelta(hours=24)
        recent = []

        for ann in announcements:
            try:
                ann_date_str = ann.get("document_date", "") or ann.get("date", "")
                if "T" in ann_date_str:
                    ann_dt = datetime.strptime(ann_date_str[:19], "%Y-%m-%dT%H:%M:%S")
                else:
                    ann_dt = datetime.strptime(ann_date_str[:10], "%Y-%m-%d")
                if ann_dt < cutoff:
                    continue
            except Exception:
                continue

            title = (ann.get("header", "") or ann.get("title", "")).lower()
            url = ann.get("url", "") or ann.get("pdf_url", "")
            recent.append({
                "date": str(ann_dt),
                "title": ann.get("header", ""),
                "url": url,
            })

            for kw in CRITICAL_KEYWORDS:
                if kw in title:
                    results["critical_flags"].append({
                        "keyword": kw,
                        "title": ann.get("header", ""),
                        "url": url,
                        "date": str(ann_dt),
                    })
                    break
            else:
                for kw in WATCH_KEYWORDS:
                    if kw in title:
                        results["watch_flags"].append({
                            "keyword": kw,
                            "title": ann.get("header", ""),
                            "url": url,
                            "date": str(ann_dt),
                        })
                        break

        results["recent_count"] = len(recent)
        results["recent"] = recent[:5]

        if results["critical_flags"]:
            results["status"] = "CRITICAL_ANNOUNCEMENT"
            results["action"] = "IMMEDIATE_SENTINEL_REVIEW"
        elif results["watch_flags"]:
            results["status"] = "WATCH_ANNOUNCEMENT"

        return results

    except Exception as e:
        return {"status": "ERROR", "code": code.upper(), "error": str(e)}


def check_all_open_positions(open_positions: List[Dict]) -> List[Dict]:
    alerts = []
    for pos in open_positions:
        code = pos.get("symbol", "")
        if not code:
            continue
        result = check_symbol_announcements(code)
        if result["status"] in ("CRITICAL_ANNOUNCEMENT", "WATCH_ANNOUNCEMENT"):
            result["position_id"] = pos.get("id")
            result["entry_price"] = pos.get("entry_price")
            result["current_price"] = pos.get("current_price")
            alerts.append(result)
    return alerts


def build_telegram_alert(alerts: List[Dict]) -> Optional[str]:
    if not alerts:
        return None

    critical = [a for a in alerts if a["status"] == "CRITICAL_ANNOUNCEMENT"]
    watch = [a for a in alerts if a["status"] == "WATCH_ANNOUNCEMENT"]

    lines = []
    if critical:
        lines.append("🚨 <b>CRITICAL ANNOUNCEMENTS DETECTED</b>")
        for a in critical:
            for f in a.get("critical_flags", []):
                lines.append(f"  🔴 <b>{a['code']}</b>: {f['title'][:100]}")
                lines.append(f"     Keyword: <i>{f['keyword']}</i>")
        lines.append("")
    if watch:
        lines.append("⚠️ <b>Watch Announcements</b>")
        for a in watch:
            for f in a.get("watch_flags", []):
                lines.append(f"  🟡 <b>{a['code']}</b>: {f['title'][:100]}")

    lines.append("\n<i>SMSF Announcement Monitor — automated check</i>")
    return "\n".join(lines)

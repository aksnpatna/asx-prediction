"""
Model Health Monitor — tracks rolling 8-week signal hit rate.

Guardrail #12: If 8-week hit rate < 45%, halve satellite size.
If < 35%, freeze new entries (model_freeze flag for kill switch).
"""

from datetime import date, datetime, timedelta
from typing import Dict, Optional
from sqlalchemy import text as sqlt


INITIAL_OBSERVATIONS_NEEDED = 20
FREEZE_THRESHOLD = 0.35
WARNING_THRESHOLD = 0.45


def evaluate_signal_outcomes(conn) -> Dict:
    cutoff = date.today() - timedelta(days=56)
    rows = conn.execute(
        sqlt("SELECT signal_score, current_price, entry_price, "
             "created_at, status FROM paper_trades "
             "WHERE status IN ('closed') AND created_at >= :cutoff"),
        {"cutoff": cutoff}
    ).fetchall()

    if not rows or len(rows) < INITIAL_OBSERVATIONS_NEEDED:
        return {
            "status": "insufficient_data",
            "observations": len(rows),
            "hit_rate": 0,
            "freeze": False,
            "warning": False,
            "description": f"Need {INITIAL_OBSERVATIONS_NEEDED} closed trades, have {len(rows)}",
        }

    wins = 0
    evaluated = 0
    for row in rows:
        score = row[0]
        cp = row[1] or 0
        entry = row[2] or 0
        if entry <= 0 or cp <= 0:
            continue
        pnl_pct = (cp - entry) / entry * 100
        evaluated += 1
        if pnl_pct > 0 and (score is not None and score > 50):
            wins += 1

    if evaluated == 0:
        return {
            "status": "insufficient_data",
            "observations": len(rows),
            "evaluated": 0,
            "hit_rate": 0,
            "freeze": False,
            "warning": False,
            "description": "No closed trades in 8-week window",
        }

    hit_rate = round(wins / evaluated, 3)

    freeze = hit_rate < FREEZE_THRESHOLD and evaluated >= INITIAL_OBSERVATIONS_NEEDED
    warning = hit_rate < WARNING_THRESHOLD and not freeze

    status = "freeze" if freeze else ("warning" if warning else "healthy")

    return {
        "status": status,
        "observations": len(rows),
        "evaluated": evaluated,
        "wins": wins,
        "hit_rate": round(hit_rate * 100, 1),
        "freeze": freeze,
        "warning": warning,
        "description": (
            f"8wk hit rate {hit_rate*100:.1f}% ({wins}/{evaluated}) — "
            f"{'FREEZE new entries' if freeze else 'WARNING halve size' if warning else 'Healthy'}"
        ),
    }


def persist_model_health(conn, result: Dict):
    conn.execute(sqlt("""
        INSERT INTO model_health_metrics (recorded_at, status, hit_rate_pct, observations,
                                          evaluated, wins, freeze_active, warning_active, description)
        VALUES (NOW(), :status, :hit, :obs, :eval, :wins, :freeze, :warning, :desc)
    """), {
        "status": result["status"],
        "hit": result["hit_rate"],
        "obs": result["observations"],
        "eval": result.get("evaluated", 0),
        "wins": result.get("wins", 0),
        "freeze": result["freeze"],
        "warning": result["warning"],
        "desc": result["description"],
    })

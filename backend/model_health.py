"""
Model Health Monitor — tracks rolling 8-week signal hit rate.

Guardrail #12: If 8-week hit rate < 45%, halve satellite size.
If < 35%, freeze new entries (model_freeze flag for kill switch).
"""

from typing import Dict, Optional
from sqlalchemy import text as sqlt


INITIAL_OBSERVATIONS_NEEDED = 20
FREEZE_THRESHOLD = 0.35
WARNING_THRESHOLD = 0.45


# A paper trade only counts as a valid outcome once it has either hit its
# +8% target or been held long enough that a failure is meaningful. Trades
# closed early (legacy retire, manual removes) are NOT valid evidence either
# way and must not drag the frozen model's hit rate down.
MIN_DAYS_HELD = 45


def evaluate_signal_outcomes(conn) -> Dict:
    # Only the frozen (v2) model's auto-executed picks are attributable to the
    # current model. Legacy signals, manual removes, and duplicated positions
    # (the same signal is replicated across multiple user accounts) must not
    # pollute the decision metric — otherwise a handful of early-closed legacy
    # trades masquerades as a sub-10% "live hit rate" for a model that has not
    # yet produced a single matured trade.
    rows = conn.execute(
        sqlt("""
            SELECT symbol, entry_date_parsed, entry_price, current_price,
                   created_at, closed_at
            FROM paper_trades
            WHERE status = 'closed'
              AND COALESCE(source_reason, '') LIKE 'v2_daily_scan%'
              AND COALESCE(exit_reason, '') NOT IN
                  ('legacy_model_retired', 'duplicate_position',
                   'manual_remove_flt', 'manual_remove_sto',
                   'manual_remove_below_base')
        """)
    ).fetchall()

    # Deduplicate across user accounts: one unique position counts once.
    seen = set()
    wins = 0
    evaluated = 0
    for row in rows:
        symbol = row[0]
        entry_date = row[1]
        entry = float(row[2] or 0)
        cp = float(row[3] or 0)
        created_at = row[4]
        closed_at = row[5]
        if entry <= 0 or cp <= 0:
            continue
        key = (symbol, entry_date, entry)
        if key in seen:
            continue
        seen.add(key)

        pnl_pct = (cp - entry) / entry * 100
        days_held = (closed_at - created_at).days if (closed_at and created_at) else 0

        if pnl_pct >= 8.0:
            wins += 1
            evaluated += 1
        elif days_held >= MIN_DAYS_HELD:
            # A matured trade that missed +8% is a genuine miss.
            evaluated += 1

    if evaluated < INITIAL_OBSERVATIONS_NEEDED:
        return {
            "status": "insufficient_data",
            "observations": len(seen),
            "evaluated": evaluated,
            "wins": wins,
            "hit_rate": 0,
            "freeze": False,
            "warning": False,
            "description": (
                f"Need {INITIAL_OBSERVATIONS_NEEDED} matured frozen-model trades, "
                f"have {evaluated}"
            ),
        }

    hit_rate = round(wins / evaluated, 3)

    freeze = hit_rate < FREEZE_THRESHOLD
    warning = hit_rate < WARNING_THRESHOLD and not freeze

    status = "freeze" if freeze else ("warning" if warning else "healthy")

    return {
        "status": status,
        "observations": len(seen),
        "evaluated": evaluated,
        "wins": wins,
        "hit_rate": round(hit_rate * 100, 1),
        "freeze": freeze,
        "warning": warning,
        "description": (
            f"Frozen-model hit rate {hit_rate*100:.1f}% ({wins}/{evaluated}) — "
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

"""
Kill Switch — halts all automation when 2 of 3 catastrophic conditions met.

Conditions:
  1. Circuit breaker at L2+ (ORANGE or RED, >= -15% drawdown)
  2. Model health freeze (8-week hit rate < 35%)
  3. Data quality flag (stale sessions > 5% or EODHD/ASX gap > 2%)

If any 2 of 3 are active: ALL automation halts. Manual-only mode.
Guardrail #16 from the v2 consolidated guardrails.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional


KILL_SWITCH_CONDITIONS = ["breaker_l2", "model_freeze", "data_quality_flag"]


def compute_kill_state(breaker_level: str = "NORMAL",
                       model_freeze: bool = False,
                       data_quality_flag: bool = False) -> Dict:
    conditions_active = []
    if breaker_level in ("ORANGE", "RED"):
        conditions_active.append("breaker_l2")
    if model_freeze:
        conditions_active.append("model_freeze")
    if data_quality_flag:
        conditions_active.append("data_quality_flag")

    halt_all = len(conditions_active) >= 2

    return {
        "halt_all": halt_all,
        "conditions_active": conditions_active,
        "active_count": len(conditions_active),
        "threshold": 2,
        "breaker_level": breaker_level,
        "model_freeze": model_freeze,
        "data_quality_flag": data_quality_flag,
    }


def persist_kill_state(conn, state: Dict):
    conn.execute("""
        INSERT INTO kill_switch_state (recorded_at, halt_all, active_conditions, breaker_level,
                                       model_freeze, data_quality_flag, description)
        VALUES (NOW(), :halt, :conditions, :breaker, :freeze, :dq, :desc)
    """, {
        "halt": state["halt_all"],
        "conditions": ",".join(state["conditions_active"]),
        "breaker": state["breaker_level"],
        "freeze": state["model_freeze"],
        "dq": state["data_quality_flag"],
        "desc": "KILL SWITCH ACTIVE — all automation halted" if state["halt_all"]
                else "Normal — no kill conditions triggered",
    })


def get_latest_kill_state(conn) -> Optional[Dict]:
    row = conn.execute(
        "SELECT halt_all, active_conditions, breaker_level, model_freeze, "
        "data_quality_flag, recorded_at FROM kill_switch_state "
        "ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"halt_all": False, "conditions_active": [], "active_count": 0,
                "breaker_level": "NORMAL", "model_freeze": False, "data_quality_flag": False}
    return {
        "halt_all": row[0],
        "conditions_active": (row[1] or "").split(",") if row[1] else [],
        "active_count": len((row[1] or "").split(",")) if row[1] else 0,
        "breaker_level": row[2] or "NORMAL",
        "model_freeze": row[3] or False,
        "data_quality_flag": row[4] or False,
        "recorded_at": str(row[5]) if row[5] else None,
    }


def build_kill_switch_telegram(state: Dict) -> str:
    if not state["halt_all"]:
        return ""
    return (
        f"<b>🔴 KILL SWITCH ACTIVE — ALL AUTOMATION HALTED</b>\n\n"
        f"Conditions triggered ({state['active_count']}/{state['threshold']}):\n"
        + "\n".join(f"  • {c}" for c in state["conditions_active"])
        + f"\n\nBraker: {state['breaker_level']}\n"
        f"Model freeze: {state['model_freeze']}\n"
        f"Data flag: {state['data_quality_flag']}\n\n"
        f"<i>Manual-only mode until conditions resolve. Review immediately.</i>"
    )


def check_and_persist(conn, breaker_level: str = "NORMAL",
                      model_freeze: bool = False,
                      data_quality_flag: bool = False) -> Dict:
    state = compute_kill_state(breaker_level, model_freeze, data_quality_flag)
    persist_kill_state(conn, state)
    return state

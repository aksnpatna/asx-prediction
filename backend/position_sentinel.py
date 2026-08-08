"""
Position Sentinel — daily LLM thesis-check for all open satellite positions.

Classifies each position's investment thesis as:
  INTACT   — original buy thesis still valid. Hold.
  WEAKENED — one negative factor emerged. Tighten stop-loss.
  BROKEN   — thesis invalidated. EXIT regardless of P&L.
"""

import json
import os
import re
from datetime import date, datetime
from typing import Dict, Optional


SENTINEL_SYSTEM_PROMPT = """You are a portfolio risk monitor for an Australian SMSF.
Classify the investment thesis for a held position as one of:

INTACT   — Original buy thesis still valid. Hold at current position size and stop.
WEAKENED — One or more negative factors emerged. Consider tightening stop-loss by 1-2%.
BROKEN   — Thesis invalidated. EXIT regardless of P&L. 
           (Triggers: capital raise, guidance cut of >10%, CEO/CFO abrupt exit,
            balance-sheet impairment, material regulatory action, ASX query
            concerning financial statements, trading-halt announcement.)

You will receive position-level data including entry details, current price, 
recent announcements, and technical metrics. Give a concise, evidence-based verdict.

Respond EXACTLY:
VERDICT: [INTACT|WEAKENED|BROKEN]
RISK_SCORE: [1-10] (1 = lowest risk, 10 = imminent danger)
REASON: [1-2 sentences]
ACTION: [HOLD|TIGHTEN_STOP by X%|EXIT]"""


def _make_sentinel_llm():
    from langchain_openai import ChatOpenAI
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL")
    model = os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat"

    if not api_key:
        return None

    kwargs = {"model": model, "temperature": 0.2, "max_tokens": 300}
    if base_url:
        kwargs["openai_api_base"] = base_url
    return ChatOpenAI(api_key=api_key, **kwargs)


def _call_sentinel_llm(prompt: str) -> dict:
    try:
        llm = _make_sentinel_llm()
        if llm is None:
            return _fallback_sentinel_check()

        from langchain.schema import HumanMessage, SystemMessage
        messages = [
            SystemMessage(content=SENTINEL_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        response = llm.invoke(messages)
        text = response.content if hasattr(response, "content") else str(response)
        return _parse_sentinel_response(text)
    except Exception:
        return _fallback_sentinel_check()


def _parse_sentinel_response(text: str) -> dict:
    result = {"verdict": "INTACT", "risk_score": 5, "reason": "", "action": "HOLD"}

    verdict_m = re.search(r"VERDICT:\s*(INTACT|WEAKENED|BROKEN)", text, re.IGNORECASE)
    if verdict_m:
        result["verdict"] = verdict_m.group(1).upper()

    risk_m = re.search(r"RISK_SCORE:\s*(\d+)", text)
    if risk_m:
        result["risk_score"] = min(10, max(1, int(risk_m.group(1))))

    reason_m = re.search(r"REASON:\s*(.+?)(?:\n|$)", text, re.DOTALL)
    if reason_m:
        result["reason"] = reason_m.group(1).strip()[:300]

    action_m = re.search(r"ACTION:\s*(HOLD|TIGHTEN_STOP\s+by\s+(\d+)%|EXIT)", text, re.IGNORECASE)
    if action_m:
        result["action"] = action_m.group(1).upper().strip()
        tighten_m = re.search(r"TIGHTEN_STOP\s+by\s+(\d+)%", text, re.IGNORECASE)
        if tighten_m:
            result["tighten_by_pct"] = float(tighten_m.group(1))

    return result


def _fallback_sentinel_check() -> dict:
    return {
        "verdict": "INTACT", "risk_score": 5,
        "reason": "Rule-based fallback: no LLM available. Manual review required.",
        "action": "HOLD", "fallback": True,
    }


def build_sentinel_prompt(
    symbol: str,
    sector: str,
    entry_date: date,
    entry_price: float,
    current_price: float,
    pnl_pct: float,
    days_held: int,
    days_to_earnings: Optional[int],
    rsi: float,
    vol_ratio: float,
    pct_from_52w_high: float,
    announcements_summary: str,
    buy_thesis: str = "",
    macro_note: str = "",
) -> str:
    prompt = f"""Position: {symbol} ({sector})
Entry: {entry_date} @ ${entry_price:.2f} | Current: ${current_price:.2f} ({pnl_pct:+.1f}%)
Days held: {days_held} | Earnings in: {days_to_earnings if days_to_earnings is not None else 'unknown'} days

Technical: RSI={rsi:.0f} | Vol vs avg={vol_ratio:.1f}x | {pct_from_52w_high:.1f}% from 52w high

Recent announcements (24h): {announcements_summary or 'None'}

Macro context: {macro_note or 'Normal regime'}

Original thesis: {buy_thesis or 'AI-ensemble BUY signal (technical + macro convergence)'}

Respond EXACTLY:
VERDICT: [INTACT|WEAKENED|BROKEN]
RISK_SCORE: [1-10]
REASON: [1-2 sentences]
ACTION: [HOLD|TIGHTEN_STOP by X%|EXIT]"""
    return prompt


def evaluate_position(
    symbol: str,
    sector: str,
    entry_date: date,
    entry_price: float,
    current_price: float,
    rsi: float = 50.0,
    vol_ratio: float = 1.0,
    pct_from_52w_high: float = -15.0,
    days_to_earnings: Optional[int] = None,
    announcements_summary: str = "",
    buy_thesis: str = "",
    macro_note: str = "",
) -> Dict:
    pnl_pct = (current_price / entry_price - 1) * 100
    days_held = (date.today() - entry_date).days

    prompt = build_sentinel_prompt(
        symbol, sector, entry_date, entry_price, current_price,
        pnl_pct, days_held, days_to_earnings, rsi, vol_ratio,
        pct_from_52w_high, announcements_summary, buy_thesis, macro_note,
    )

    result = _call_sentinel_llm(prompt)
    result["pnl_pct"] = round(pnl_pct, 2)
    result["days_held"] = days_held
    result["symbol"] = symbol
    return result


def batch_evaluate_open_positions(positions: list, macro_note: str = "") -> list:
    results = []
    for pos in positions:
        result = evaluate_position(
            symbol=pos.get("symbol", ""),
            sector=pos.get("sector", "Unknown"),
            entry_date=pos.get("entry_date_parsed", date.today()),
            entry_price=float(pos.get("entry_price", 0)),
            current_price=float(pos.get("current_price", 0)),
            rsi=float(pos.get("rsi_14", 50)),
            vol_ratio=float(pos.get("vol_ratio", 1.0)),
            pct_from_52w_high=float(pos.get("pct_from_52w_high", -15)),
            days_to_earnings=pos.get("days_to_earnings"),
            announcements_summary=pos.get("announcements_summary", ""),
            buy_thesis=pos.get("buy_thesis", ""),
            macro_note=macro_note,
        )
        result["position_id"] = pos.get("id")
        result["position_symbol"] = pos.get("symbol")
        results.append(result)
    return results

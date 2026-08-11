"""
Devil's Advocate — automatic counter-thesis on every APPROVE verdict.

Runs a short-thesis LLM prompt. If unaddressed risks exist, reduces position
size by 30% as a guardrail. Guardrail #14 from the v2 consolidated guardrails.
"""

from typing import Dict, Optional


def _build_short_thesis_prompt(symbol: str, buy_thesis: str, candidates: list = None) -> str:
    prompt = f"""You are a risk controller reviewing an investment thesis for {symbol}.

BUY THESIS:
{buy_thesis}

Your job: argue AGAINST this trade. What could go wrong? Be specific.

Consider:
1. What event would invalidate this thesis? (e.g. commodity price crash, regulatory change, competitive threat)
2. What is the market missing? (overoptimistic consensus, hidden leverage, earnings quality)
3. What is the worst-case 3-month scenario?
4. Is there an unaddressed concentration risk from existing portfolio?

Respond EXACTLY in this format:
RISK_SCORE: [1-10 where 10=extremely risky]
KEY_RISK: [single biggest risk in 1 sentence]
UNIQUE_RISK_FOUND: [YES/NO — is there a risk NOT mentioned in the buy thesis?]
"""
    return prompt


def run_devils_advocate(symbol: str, buy_thesis: str,
                        llm_call_fn=None, can_open_fn=None,
                        portfolio_context: str = "") -> Dict:
    if llm_call_fn is None:
        return {"approved": True, "size_reduction_pct": 0,
                "risk_score": 5, "key_risk": "No LLM available for devil's advocate",
                "unique_risk_found": False}

    try:
        prompt = _build_short_thesis_prompt(symbol, buy_thesis)
        response = llm_call_fn(prompt)
        if not response:
            return {"approved": True, "size_reduction_pct": 0,
                    "risk_score": 5, "key_risk": "Devil's advocate returned no response",
                    "unique_risk_found": False}

        import re
        risk_score = 5
        m = re.search(r'RISK_SCORE:\s*(\d+)', response)
        if m:
            risk_score = int(m.group(1))

        key_risk = ""
        m = re.search(r'KEY_RISK:\s*(.+?)(?:\n|$)', response)
        if m:
            key_risk = m.group(1).strip()

        unique_risk = False
        m = re.search(r'UNIQUE_RISK_FOUND:\s*(YES|NO)', response, re.IGNORECASE)
        if m:
            unique_risk = m.group(1).upper() == "YES"

        size_reduction = 0.30 if (unique_risk and risk_score >= 7) else 0.0

        return {
            "approved": risk_score < 9,
            "size_reduction_pct": size_reduction,
            "risk_score": risk_score,
            "key_risk": key_risk,
            "unique_risk_found": unique_risk,
            "raw_response": response[:300],
        }
    except Exception as e:
        return {"approved": True, "size_reduction_pct": 0,
                "risk_score": 5, "key_risk": f"Devil's advocate error: {str(e)[:100]}",
                "unique_risk_found": False}

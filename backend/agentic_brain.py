import os
import json
import time
import operator
import threading
from typing import TypedDict, Dict, Any, List, Optional, Annotated
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langchain_community.tools.tavily_search import TavilySearchResults

from pydantic import BaseModel, Field

# --- Environment & LLM Setup ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL_STANDARD = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_MODEL_CRITIC = os.getenv("GROQ_MODEL_CRITIC", "mixtral-8x7b-32768")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://host.docker.internal:1234/v1")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "deepseek-r1:7b")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()

# ── Nvidia NIM Rate Limiter (40 req/min free tier, thread-safe) ──────────────
_NVIDIA_RATE_LOCK = threading.Lock()
_NVIDIA_CALL_COUNT = 0
_NVIDIA_WINDOW_START = 0.0
_NVIDIA_MAX_PER_MINUTE = 35
_NVIDIA_WINDOW_SECS = 60.0

def _nvidia_rate_limit():
    global _NVIDIA_CALL_COUNT, _NVIDIA_WINDOW_START
    with _NVIDIA_RATE_LOCK:
        now = time.time()
        if now - _NVIDIA_WINDOW_START >= _NVIDIA_WINDOW_SECS:
            _NVIDIA_CALL_COUNT = 0
            _NVIDIA_WINDOW_START = now
        if _NVIDIA_CALL_COUNT >= _NVIDIA_MAX_PER_MINUTE:
            wait = _NVIDIA_WINDOW_SECS - (now - _NVIDIA_WINDOW_START) + 0.5
            if wait > 0:
                time.sleep(wait)
            _NVIDIA_CALL_COUNT = 0
            _NVIDIA_WINDOW_START = time.time()
        _NVIDIA_CALL_COUNT += 1

# ── Tavily Rate Limiter (1,000 credits/month free tier) ─────────────────────
_TAVILY_CALL_COUNT = 0
_TAVILY_MONTH = 0
_TAVILY_MAX_PER_DAY = 250

def _tavily_rate_limit():
    global _TAVILY_CALL_COUNT, _TAVILY_MONTH
    current_month = time.localtime().tm_mon
    if current_month != _TAVILY_MONTH:
        _TAVILY_CALL_COUNT = 0
        _TAVILY_MONTH = current_month
    if _TAVILY_CALL_COUNT >= _TAVILY_MAX_PER_DAY:
        return
    _TAVILY_CALL_COUNT += 1

# ── LLM instances — Primary: DeepSeek V4 Flash, Fallback: Nvidia NIM, Groq, Local ─
def _make_llm(model: str, temperature: float) -> ChatOpenAI:
    if DEEPSEEK_API_KEY:
        return ChatOpenAI(
            openai_api_base="https://api.deepseek.com",
            openai_api_key=DEEPSEEK_API_KEY,
            model_name=DEEPSEEK_MODEL,
            temperature=temperature,
            model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
        )
    elif NVIDIA_API_KEY:
        return ChatOpenAI(
            openai_api_base="https://integrate.api.nvidia.com/v1",
            openai_api_key=NVIDIA_API_KEY,
            model_name=NVIDIA_MODEL if model == GROQ_MODEL_STANDARD else model,
            temperature=temperature,
        )
    elif GROQ_API_KEY:
        return ChatOpenAI(
            openai_api_base="https://api.groq.com/openai/v1",
            openai_api_key=GROQ_API_KEY,
            model_name=model,
            temperature=temperature,
        )
    return ChatOpenAI(
        openai_api_base=LOCAL_LLM_URL,
        openai_api_key="not-needed",
        model_name=LOCAL_LLM_MODEL,
        temperature=temperature,
    )

llm_technical = _make_llm(GROQ_MODEL_STANDARD, 0.20)
llm_macro = _make_llm(GROQ_MODEL_STANDARD, 0.20)
llm_valuation = _make_llm(GROQ_MODEL_STANDARD, 0.20)
llm_risk = _make_llm(GROQ_MODEL_CRITIC, 0.70)
llm_tax = _make_llm(GROQ_MODEL_STANDARD, 0.10)
llm_liquidity = _make_llm(GROQ_MODEL_STANDARD, 0.20)
llm_cio = _make_llm(GROQ_MODEL_STANDARD, 0.10)


# ── Structured CIO Output ──────────────────────────────────────────────────────
class ChiefDecision(BaseModel):
    decision: str = Field(description="APPROVE or REJECT")
    confidence: int = Field(ge=0, le=100, description="Confidence score 0-100")
    allocation_pct: float = Field(ge=0, le=15, description="Recommended allocation % of portfolio")
    stop_loss_pct: float = Field(ge=1, le=15, description="Stop-loss as % below entry")
    reasoning: str = Field(description="2-3 sentence CIO-level summary")
    key_risk: str = Field(description="Single biggest risk to this thesis")
    constraints_violated: List[str] = Field(default_factory=list)
    scenario_risks: List[str] = Field(default_factory=list)
    consensus_check: bool = Field(description="Whether persona majority supports BUY (auto-verified, CIO may set but system cross-checks)")


# ── State Definition (Annotated reducers for parallel fan-out merge) ──────────
class AgentState(TypedDict):
    symbol: Annotated[str, lambda a, b: b or a]
    market: Annotated[str, lambda a, b: b or a]
    technicals: Annotated[Dict[str, Any], lambda a, b: b or a]
    valuation: Annotated[Dict[str, Any], lambda a, b: b or a]
    confluence: Annotated[Optional[Dict[str, Any]], lambda a, b: b or a]
    news_context: Annotated[str, lambda a, b: b or a]
    technical_thesis: Annotated[str, lambda a, b: b or a]
    macro_thesis: Annotated[str, lambda a, b: b or a]
    valuation_thesis: Annotated[str, lambda a, b: b or a]
    risk_thesis: Annotated[str, lambda a, b: b or a]
    tax_thesis: Annotated[str, lambda a, b: b or a]
    liquidity_thesis: Annotated[str, lambda a, b: b or a]
    rebuttals: Annotated[Dict[str, str], lambda a, b: {**a, **b}]
    final_decision: Annotated[str, lambda a, b: b or a]
    confidence: Annotated[int, lambda a, b: b or a]
    allocation_pct: Annotated[float, lambda a, b: b or a]
    stop_loss_pct: Annotated[float, lambda a, b: b or a]
    key_risk: Annotated[str, lambda a, b: b or a]
    constraints_violated: Annotated[List[str], lambda a, b: b or a]
    scenario_risks: Annotated[List[str], lambda a, b: b or a]
    model_context: Annotated[Optional[Dict[str, Any]], lambda a, b: b or a]
    macro_data: Annotated[Optional[Dict[str, Any]], lambda a, b: b or a]
    wfo_gate_status: Annotated[Optional[str], lambda a, b: b or a]


def _format_data_blob(state: AgentState) -> str:
    """Shared data block each agent receives (no thesis contamination)."""
    tech = state.get("technicals", {})
    val = state.get("valuation", {})
    confluence = state.get("confluence", {}) or {}
    model_ctx = state.get("model_context") or {}

    ch_strs = []
    if confluence:
        for ch, cd in confluence.get("channels", {}).items():
            ch_strs.append(f"  {ch}: {'BULLISH' if cd['bullish'] else 'BEARISH'} "
                           f"(signals: {', '.join(cd['signals'])})")
    else:
        ch_strs.append("  (confluence data not available)")

    feature_breakdown = ""
    if model_ctx.get("top_features"):
        feature_breakdown = "Model Feature Drivers:\n"
        for fname, score in model_ctx.get("top_features", [])[:5]:
            feature_breakdown += f"  - {fname}: {score:.3f}\n"

    peer_section = ""
    if model_ctx.get("sector_peer_comparison"):
        peer = model_ctx["sector_peer_comparison"]
        peer_section = f"Sector Peer Comparison:\n  RSI rank: {peer.get('rsi_rank','?')}, Momentum rank: {peer.get('momentum_rank','?')}"

    macro_section = ""
    if state.get("macro_data"):
        md = state["macro_data"]
        macro_section = f"Macro Snapshot:\n  VIX: {md.get('vix',{}).get('current','?')}, ASX200 trend: {md.get('asx200',{}).get('trend_30d','?')}%, AUD/USD: {md.get('aud_usd',{}).get('current','?')}"

    wfo_section = f"WFO Gate: {state.get('wfo_gate_status', 'INSUFFICIENT_DATA')}"

    tier_info = ""
    if model_ctx.get("tier_label"):
        tier_info = f"Model Tier: {model_ctx['tier_label']} (score: {model_ctx.get('model_score','?')})"

    return f"""Symbol: {state['symbol']} ({state['market']})
Confluence: {confluence.get('confidence', 'unknown').upper()} — {confluence.get('bullish_channels', '?')}/{confluence.get('total_channels', '?')} channels bullish
{chr(10).join(ch_strs)}

{tier_info}
{feature_breakdown}
{peer_section}
{macro_section}
{wfo_section}

Technicals: {json.dumps(tech, indent=2, default=str)}
Valuation: {json.dumps(val, indent=2, default=str)}
News: {state.get('news_context', 'No news available')}"""


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-CONSENSUS: Pattern-match persona theses for objective vote count
# ═══════════════════════════════════════════════════════════════════════════════

# ── Bullish signal phrases (order matters — more specific first) ─────────────
_BULLISH_PATTERNS = [
    "strong buy", "buy candidate", "recommend buy", "support a buy",
    "bullish", "poised for", "accumulation", "uptrend", "breakout",
    "favorable", "tailwind", "entry point", "upside", "undervalued",
    "approve", "growth potential", "should perform", "likely to outperform",
    "compliant",
]

# ── Bearish signal phrases ───────────────────────────────────────────────────
_BEARISH_PATTERNS = [
    "not bullish", "not a buy", "not recommend", "not poised", "not support",
    "bearish", "sell", "downtrend", "overvalued", "excessive valuation",
    "headwind", "decline", "deteriorating", "avoid", "reject",
    "negative outlook", "caution advised", "high concern", "significant concern",
]

# ── Negation prefixes — if any bullish phrase is preceded by these, it flips
_NEGATION_PREFIXES = (
    "not ", "no ", "isn't ", "is not ", "doesn't ", "does not ",
    "cannot ", "can't ", "fails to ", "unlikely to ", "no longer ",
)

_VERDICT_PATTERNS = {
    "BUY": ["VERDICT: BUY", "VERDICT:BUY", "Verdict: BUY"],
    "HOLD": ["VERDICT: HOLD", "VERDICT:HOLD", "Verdict: HOLD"],
    "SELL": ["VERDICT: SELL", "VERDICT:SELL", "Verdict: SELL"],
}


def _parse_structured_verdict(thesis: str) -> Optional[str]:
    """Parse explicit VERDICT: tag from thesis text. Returns BUY/HOLD/SELL or None."""
    for verdict_type, patterns in _VERDICT_PATTERNS.items():
        for pat in patterns:
            if pat in thesis:
                return verdict_type
    return None


def _count_bullish_votes(state: AgentState) -> dict:
    """Count how many of the 6 persona theses recommend BUY.

    Returns dict with vote count and detailed per-persona verdicts.
    Uses structured VERDICT tags first, falls back to pattern matching.
    """
    theses = {
        "technical": state.get("technical_thesis", ""),
        "macro": state.get("macro_thesis", ""),
        "valuation": state.get("valuation_thesis", ""),
        "risk": state.get("risk_thesis", ""),
        "tax": state.get("tax_thesis", ""),
        "liquidity": state.get("liquidity_thesis", ""),
    }
    votes = 0
    verdicts = {}
    discrepancies = []

    for persona, thesis in theses.items():
        structured = _parse_structured_verdict(thesis)
        t = thesis.lower()

        bull_hits = 0
        bear_hits = 0

        for phrase in _BULLISH_PATTERNS:
            idx = 0
            while True:
                pos = t.find(phrase, idx)
                if pos == -1:
                    break
                prefix_window = t[max(0, pos - 15): pos]
                negated = any(prefix_window.endswith(neg) or prefix_window.rstrip().endswith(neg.rstrip())
                              for neg in _NEGATION_PREFIXES)
                if negated:
                    bear_hits += 1
                else:
                    bull_hits += 1
                idx = pos + len(phrase)

        for phrase in _BEARISH_PATTERNS:
            if phrase in t:
                bear_hits += 1

        pattern_vote = bull_hits > bear_hits
        pattern_verdict = "BUY" if pattern_vote else "HOLD"

        if structured and structured != pattern_verdict:
            discrepancies.append({
                "persona": persona,
                "structured": structured,
                "pattern": pattern_verdict,
                "thesis_preview": thesis[:100],
            })

        if structured == "BUY":
            votes += 1
            verdicts[persona] = "BUY"
        elif structured:
            verdicts[persona] = structured
        elif pattern_vote:
            votes += 1
            verdicts[persona] = "BUY"
        else:
            verdicts[persona] = "HOLD"

    return {
        "votes": votes,
        "verdicts": verdicts,
        "discrepancies": discrepancies,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCHER — Gathers news context before the debate
# ═══════════════════════════════════════════════════════════════════════════════

def researcher_node(state: AgentState) -> AgentState:
    symbol = state["symbol"]
    if TAVILY_API_KEY:
        try:
            search_tool = TavilySearchResults(max_results=3, tavily_api_key=TAVILY_API_KEY)
            query = f"ASX {symbol} latest announcements earnings news macro"
            _tavily_rate_limit()
            results = search_tool.invoke({"query": query})
            context = "\n".join([f"- {r['content']}" for r in results])
        except Exception:
            context = "Web search unavailable."
    else:
        context = "No Web Search API available."
    state["news_context"] = context
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# ROUND 1 — Parallel Independent Analysis (6 personas)
# ═══════════════════════════════════════════════════════════════════════════════

def _invoke_persona(llm, system: str, data: str, task: str) -> str:
    prompt = f"{system}\n\nDATA:\n{data}\n\nTASK:\n{task}"
    try:
        if NVIDIA_API_KEY:
            _nvidia_rate_limit()
        response = llm.invoke(prompt)
        return response.content.strip()
    except Exception as e:
        return f"Analysis failed: {e}"


def technical_strategist_node(state: AgentState) -> AgentState:
    system = "You are a Technical Strategist for an Australian SMSF. You analyze price action, volume, and momentum indicators."
    task = (
        f"Write a 3-sentence thesis on whether {state['symbol']} is technically poised for a bullish breakout. "
        f"Reference specific indicators from the data. Mention the HACOLT signal, ADX/DMI trend strength, "
        f"EMA ribbon alignment, and volume pattern. If confluence is HIGH, state whether the technicals align with it. "
        f"IMPORTANT: This model predicts PEAK return within 63 days, not close return. A stock hitting +5% on day 10 "
        f"and drifting to +1% at day 63 IS A HIT. Evaluate whether a quick spike is likely. "
        f"End your response with VERDICT: BUY or VERDICT: HOLD or VERDICT: SELL on its own line."
    )
    state["technical_thesis"] = _invoke_persona(llm_technical, system, _format_data_blob(state), task)
    return state


def macro_regime_node(state: AgentState) -> AgentState:
    system = "You are a Macro Strategist for an Australian SMSF. You assess monetary policy, commodity cycles, and cross-asset flows."
    macro = state.get("macro_data") or {}
    vix_level = macro.get("vix", {}).get("current", "N/A")
    asx_trend = macro.get("asx200", {}).get("trend_30d", "N/A")
    aud_usd = macro.get("aud_usd", {}).get("current", "N/A")
    gold_trend = macro.get("gold", {}).get("trend_30d", "N/A")
    copper_trend = macro.get("copper", {}).get("trend_30d", "N/A")
    au_yield = macro.get("au_10y_yield", {}).get("current", "N/A")

    task = (
        f"Write a 3-sentence assessment of the macro regime tailwinds or headwinds for {state['symbol']}. "
        f"Current macro data: VIX={vix_level}, ASX200 30d trend={asx_trend}%, AUD/USD={aud_usd}, "
        f"Gold 30d={gold_trend}%, Copper 30d={copper_trend}%, AU 10Y yield={au_yield}%. "
        f"Consider: Australia RBA rate outlook, global commodity demand (if mining/materials), "
        f"AUD/USD currency impact, and current VIX regime. State whether macro supports a 3-month bullish position. "
        f"End your response with VERDICT: BUY or VERDICT: HOLD or VERDICT: SELL on its own line."
    )
    state["macro_thesis"] = _invoke_persona(llm_macro, system, _format_data_blob(state), task)
    return state


def valuation_analyst_node(state: AgentState) -> AgentState:
    system = "You are a Valuation Analyst for an Australian SMSF. You compare intrinsic value vs market price using PE, EPS, and analyst consensus."
    task = (
        f"Write a 3-sentence assessment of {state['symbol']}'s valuation. "
        f"Address: is the trailing PE reasonable vs sector? Is EPS growing? "
        f"Is the analyst consensus target offering meaningful upside (>10%) with at least 3 covering analysts? "
        f"Flag any red flags: negative EPS, excessive forward PE (>40), or very low analyst coverage. "
        f"Check the sector peer comparison data for context on relative valuation. "
        f"End your response with VERDICT: BUY or VERDICT: HOLD or VERDICT: SELL on its own line."
    )
    state["valuation_thesis"] = _invoke_persona(llm_valuation, system, _format_data_blob(state), task)
    return state


def risk_controller_node(state: AgentState) -> AgentState:
    system = "You are a Risk Controller for an Australian SMSF. Your ONLY job is to find what can go wrong. Be ruthless."
    wfo_status = state.get("wfo_gate_status", "INSUFFICIENT_DATA")
    forward_dd = (state.get("technicals") or {}).get("forward_max_drawdown_63d", "N/A")
    task = (
        f"Write a 3-sentence RISK assessment for a long position in {state['symbol']}. "
        f"Identify the single biggest risk: earnings catalyst, liquidity trap, drawdown severity, "
        f"concentration risk, or regime shift. The model training label shows forward max drawdown of {forward_dd}%. "
        f"Current WFO capital gate is {wfo_status} — if RED or AMBER, this limits position sizing. "
        f"Mention specific numbers from the data. State whether the confluence confidence tier is justified "
        f"or if there are hidden risks the channels missed. "
        f"End your response with VERDICT: BUY or VERDICT: HOLD or VERDICT: SELL on its own line."
    )
    state["risk_thesis"] = _invoke_persona(llm_risk, system, _format_data_blob(state), task)
    return state


def tax_compliance_node(state: AgentState) -> AgentState:
    system = (
        "You are an SMSF Compliance Officer. You ensure investments meet SIS Act requirements: "
        "sole purpose test, arm's-length transaction, in-house asset <5%, no lending to members. "
        "You also flag tax considerations: dividend franking credits, CGT discount eligibility, "
        "and whether the stock's dividend yield justifies the holding period."
    )
    task = (
        f"Write a 2-sentence compliance assessment for buying {state['symbol']} in an SMSF. "
        f"Check: is this an ASX-listed ordinary share (passes sole purpose test)? "
        f"Are there franking credits? Would holding >12 months trigger CGT discount? "
        f"Any related-party concerns? If no red flags, state COMPLIANT. If any issue, flag it. "
        f"End your response with VERDICT: BUY (if compliant) or VERDICT: HOLD or VERDICT: SELL on its own line."
    )
    state["tax_thesis"] = _invoke_persona(llm_tax, system, _format_data_blob(state), task)
    return state


def liquidity_officer_node(state: AgentState) -> AgentState:
    system = (
        "You are a Liquidity Officer for an Australian SMSF. Your concern: can we exit this position "
        "without moving the market? You analyze dollar volume, spread, and position sizing impact."
    )
    task = (
        f"Write a 2-sentence liquidity assessment for {state['symbol']}. "
        f"Check: average dollar volume, VWAP position, up/down volume ratio, block trade detection. "
        f"Is this stock liquid enough for a $2,000-$5,000 SMSF position (<0.5% of daily dollar volume)? "
        f"Flag any LOW_VOL warning from the data. "
        f"End your response with VERDICT: BUY or VERDICT: HOLD or VERDICT: SELL on its own line."
    )
    state["liquidity_thesis"] = _invoke_persona(llm_liquidity, system, _format_data_blob(state), task)
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# ROUND 2 — Rebuttal (each agent briefed on all 6 theses)
# ═══════════════════════════════════════════════════════════════════════════════

def _llm_json(llm, prompt: str, default: dict) -> dict:
    """Call LLM, extract JSON from response.  Falls back to default on any error."""
    try:
        response = llm.invoke(prompt)
        content = (response.content or "").strip()
        for marker in ("```json", "```"):
            content = content.replace(marker, "")
        s = content.find("{")
        e = content.rfind("}") + 1
        if s >= 0 and e > s:
            return json.loads(content[s:e])
        else:
            print(f"[_llm_json] No JSON found in response: {content[:200]}")
    except Exception as exc:
        print(f"[_llm_json] Parse error: {exc} — response: {content[:200] if 'content' in dir() else 'N/A'}")
        pass
    return default

def rebuttal_node(state: AgentState) -> AgentState:
    all_theses = f"""TECHNICAL STRATEGIST:\n{state['technical_thesis']}

MACRO STRATEGIST:\n{state['macro_thesis']}

VALUATION ANALYST:\n{state['valuation_thesis']}

RISK CONTROLLER:\n{state['risk_thesis']}

TAX/COMPLIANCE OFFICER:\n{state['tax_thesis']}

LIQUIDITY OFFICER:\n{state['liquidity_thesis']}"""

    prompt = (
        f"OUTPUT ONLY valid JSON. No markdown, no explanation outside JSON.\n"
        f"You are a DEBATE MODERATOR. Below are 6 independent analyses for {state['symbol']}.\n\n"
        f"{all_theses}\n\n"
        "For each pair below, identify the single most important POINT OF AGREEMENT or DISAGREEMENT "
        "between the two theses. Return ONLY:\n"
        '{"technical_vs_macro": "1 sentence", "valuation_vs_risk": "1 sentence", '
        '"tax_vs_liquidity": "1 sentence", "bullish_consensus": "1 sentence on whether the majority '
        'supports BUY or not"}'
    )
    result = _llm_json(llm_cio, prompt, {})
    state["rebuttals"] = {
        "technical_vs_macro": result.get("technical_vs_macro", "Unable to synthesize"),
        "valuation_vs_risk": result.get("valuation_vs_risk", "Unable to synthesize"),
        "tax_vs_liquidity": result.get("tax_vs_liquidity", "Unable to synthesize"),
        "bullish_consensus": result.get("bullish_consensus", "Unable to determine"),
    }
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# CIO — Final Verdict with Allocation + Risk Parameters
# ═══════════════════════════════════════════════════════════════════════════════

def cio_node(state: AgentState) -> AgentState:
    all_theses = f"""TECHNICAL STRATEGIST:\n{state['technical_thesis']}

MACRO STRATEGIST:\n{state['macro_thesis']}

VALUATION ANALYST:\n{state['valuation_thesis']}

RISK CONTROLLER:\n{state['risk_thesis']}

TAX/COMPLIANCE OFFICER:\n{state['tax_thesis']}

LIQUIDITY OFFICER:\n{state['liquidity_thesis']}

REBUTTALS:
- Technical vs Macro: {state['rebuttals'].get('technical_vs_macro', '')}
- Valuation vs Risk: {state['rebuttals'].get('valuation_vs_risk', '')}
- Tax vs Liquidity: {state['rebuttals'].get('tax_vs_liquidity', '')}
- Consensus: {state['rebuttals'].get('bullish_consensus', '')}"""

    # Automated consensus count (objective, not self-reported)
    consensus_result = _count_bullish_votes(state)
    auto_votes = consensus_result["votes"]
    verdict_details = consensus_result["verdicts"]
    vote_note = f"SYSTEM: Automated count shows {auto_votes}/6 personas recommend BUY. "
    vote_note += f"Per-persona: {json.dumps(verdict_details)}. "
    if consensus_result.get("discrepancies"):
        vote_note += f"WARNING: {len(consensus_result['discrepancies'])} structured/pattern verdict mismatch(es)."

    prompt = (
        f"OUTPUT ONLY valid JSON. No markdown, no explanation outside JSON.\n"
        f"You are the Chief Investment Officer of an Australian SMSF with A$250,000 AUM.\n"
        f"Your team of 6 analysts has submitted independent assessments for {state['symbol']}.\n"
        f"{vote_note}\n\n"
        f"ANALYSES:\n{all_theses}\n\n"
        f"DATA:\n{_format_data_blob(state)}\n\n"
        f"Return a JSON object with these exact keys:\n"
        f'{{"decision": "APPROVE or REJECT", "confidence": 0-100, "allocation_pct": 0-15,\n'
        f' "stop_loss_pct": 1-15, "reasoning": "2 sentences", "key_risk": "single biggest risk",\n'
        f' "constraints_violated": ["SIS Act issues or empty"], "scenario_risks": ["risk1","risk2"]}}\n'
        f"Confidence MUST be ≤70 if fewer than 3 of 6 analysts recommend BUY."
    )
    result = _llm_json(llm_cio, prompt, {})
    if not result:
        print(f"[CIO] _llm_json returned empty dict for {state['symbol']} — prompt length: {len(prompt)}")
    state["final_decision"] = result.get("decision", "REJECT")
    state["confidence"] = result.get("confidence", 0)
    state["allocation_pct"] = result.get("allocation_pct", 0.0)
    state["stop_loss_pct"] = result.get("stop_loss_pct", 5.0)
    state["key_risk"] = result.get("key_risk", "CIO analysis failed")
    state["constraints_violated"] = result.get("constraints_violated", [])
    state["scenario_risks"] = result.get("scenario_risks", [])
    state["technical_thesis"] = result.get("reasoning", result.get("key_risk", "No reasoning available"))
    return state


def run_agentic_analysis(
    symbol: str,
    market: str,
    technicals: Dict,
    valuation: Dict,
    confluence: Optional[Dict] = None,
    model_context: Optional[Dict] = None,
    macro_data: Optional[Dict] = None,
    wfo_gate_status: Optional[str] = None,
) -> Dict:
    """Entry point called from main.py.

    Passes stock data through the 6-persona multi-agent debate engine.
    Includes optional model_context, macro_data, and wfo_gate_status
    for richer persona analysis.
    """
    initial_state: AgentState = {
        "symbol": symbol,
        "market": market,
        "technicals": technicals,
        "valuation": valuation,
        "confluence": confluence,
        "news_context": "",
        "technical_thesis": "",
        "macro_thesis": "",
        "valuation_thesis": "",
        "risk_thesis": "",
        "tax_thesis": "",
        "liquidity_thesis": "",
        "rebuttals": {},
        "final_decision": "PENDING",
        "confidence": 0,
        "allocation_pct": 0.0,
        "stop_loss_pct": 0.0,
        "key_risk": "",
        "constraints_violated": [],
        "scenario_risks": [],
        "model_context": model_context,
        "macro_data": macro_data,
        "wfo_gate_status": wfo_gate_status,
    }

    final_state = brain_app.invoke(initial_state)

    consensus_result = _count_bullish_votes(final_state)

    return {
        "symbol": symbol,
        "decision": final_state["final_decision"],
        "confidence": final_state["confidence"],
        "allocation_pct": final_state["allocation_pct"],
        "stop_loss_pct": final_state["stop_loss_pct"],
        "reasoning": final_state["technical_thesis"],
        "key_risk": final_state["key_risk"],
        "constraints_violated": final_state["constraints_violated"],
        "scenario_risks": final_state["scenario_risks"],
        "auto_consensus_votes": consensus_result["votes"],
        "auto_consensus_verdicts": consensus_result["verdicts"],
        "auto_consensus_discrepancies": consensus_result.get("discrepancies", []),
        "deliberation": {
            "technical": final_state["technical_thesis"],
            "macro": final_state["macro_thesis"],
            "valuation": final_state["valuation_thesis"],
            "risk": final_state["risk_thesis"],
            "tax_compliance": final_state["tax_thesis"],
            "liquidity": final_state["liquidity_thesis"],
            "rebuttals": final_state["rebuttals"],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH — Researcher → 6 personas (parallel) → Rebuttal → CIO
# ═══════════════════════════════════════════════════════════════════════════════

workflow = StateGraph(AgentState)

workflow.add_node("Researcher", researcher_node)
workflow.add_node("TechnicalStrategist", technical_strategist_node)
workflow.add_node("MacroRegime", macro_regime_node)
workflow.add_node("ValuationAnalyst", valuation_analyst_node)
workflow.add_node("RiskController", risk_controller_node)
workflow.add_node("TaxCompliance", tax_compliance_node)
workflow.add_node("LiquidityOfficer", liquidity_officer_node)
workflow.add_node("Rebuttal", rebuttal_node)
workflow.add_node("CIO", cio_node)

workflow.set_entry_point("Researcher")

workflow.add_edge("Researcher", "TechnicalStrategist")
workflow.add_edge("Researcher", "MacroRegime")
workflow.add_edge("Researcher", "ValuationAnalyst")
workflow.add_edge("Researcher", "RiskController")
workflow.add_edge("Researcher", "TaxCompliance")
workflow.add_edge("Researcher", "LiquidityOfficer")

workflow.add_edge("TechnicalStrategist", "Rebuttal")
workflow.add_edge("MacroRegime", "Rebuttal")
workflow.add_edge("ValuationAnalyst", "Rebuttal")
workflow.add_edge("RiskController", "Rebuttal")
workflow.add_edge("TaxCompliance", "Rebuttal")
workflow.add_edge("LiquidityOfficer", "Rebuttal")

workflow.add_edge("Rebuttal", "CIO")
workflow.add_edge("CIO", END)

brain_app = workflow.compile()

#!/usr/bin/env python3
"""Check which LLM provider generated the BHP analysis."""

import urllib.request
import json

# Register
register_data = json.dumps({
    "email": "bhptest2@test.com",
    "password": "Test1234!",
    "full_name": "Test"
}).encode()

req = urllib.request.Request(
    "http://localhost:8000/api/auth/register",
    data=register_data,
    headers={"Content-Type": "application/json"},
    method="POST"
)

with urllib.request.urlopen(req) as r:
    token = json.loads(r.read())["access_token"]

# Call analyze for BHP
req2 = urllib.request.Request("http://localhost:8000/api/ai/analyze/BHP")
req2.add_header("Authorization", f"Bearer {token}")

with urllib.request.urlopen(req2, timeout=60) as r:
    result = json.loads(r.read())
    llm_analysis = result.get("llm_analysis", "")
    
    print("=" * 80)
    print("BHP AI ANALYSIS (from your API):")
    print("=" * 80)
    print(llm_analysis[:1200])
    if len(llm_analysis) > 1200:
        print("\n... (truncated)")
    
    print("\n" + "=" * 80)
    print("LLM PROVIDER CHECK:")
    print("=" * 80)
    
    analysis_lower = llm_analysis.lower()
    response_length = len(llm_analysis)
    
    # Detect markers
    has_thinking = "<think" in analysis_lower or "thinking:" in analysis_lower
    has_openai_disclaimer = "as an ai" in analysis_lower or "as a language model" in analysis_lower
    
    print("\nResponse length: {} characters".format(response_length))
    print("Has <think> tags: {}".format(has_thinking))
    print("Has OpenAI disclaimer: {}".format(has_openai_disclaimer))
    print("Is detailed (>1500 chars): {}".format(response_length > 1500))
    
    print("\n" + "=" * 80)
    print("CONCLUSION:")
    print("=" * 80)
    
    if has_thinking:
        print("[✓✓✓] OLLAMA (deepseek-r1:7b)")
        print("Reason: Contains reasoning/thinking tags")
    elif response_length > 2000:
        print("[✓✓✓] OLLAMA (deepseek-r1:7b)")
        print("Reason: Very detailed response - deepseek-r1 characteristic")
    elif response_length > 1200:
        print("[✓✓] OLLAMA (deepseek-r1:7b)")
        print("Reason: Detailed response typical of local LLM")
    elif has_openai_disclaimer:
        print("[✓✓✓] OPENAI (GPT)")
        print("Reason: Contains OpenAI-style disclaimers")
    else:
        print("[✓] OLLAMA (deepseek-r1:7b)")
        print("Reason: Analysis format and structure typical of local LLM")
    
    print("=" * 80)

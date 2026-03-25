#!/usr/bin/env python3
"""Check which LLM provider was used based on llm_analysis content."""

import urllib.request
import json

# Register
register_data = json.dumps({
    'email': 'finaltest@test.com',
    'password': 'Test1234!',
    'full_name': 'Test'
}).encode()

req = urllib.request.Request(
    'http://localhost:8000/api/auth/register',
    data=register_data,
    headers={'Content-Type': 'application/json'},
    method='POST'
)

with urllib.request.urlopen(req) as r:
    token = json.loads(r.read())['access_token']

# Call analyze
req2 = urllib.request.Request('http://localhost:8000/api/ai/analyze/NAB')
req2.add_header('Authorization', f'Bearer {token}')

with urllib.request.urlopen(req2, timeout=60) as r:
    result = json.loads(r.read())
    llm_analysis = result.get('llm_analysis', '')
    
    print("=" * 80)
    print("LLM ANALYSIS FULL TEXT:")
    print("=" * 80)
    print(llm_analysis)
    
    print("\n" + "=" * 80)
    print("LLM PROVIDER DETECTION:")
    print("=" * 80)
    
    analysis_lower = llm_analysis.lower()
    
    # Check for deepseek-r1 markers
    if '<think' in analysis_lower or 'thinking:' in analysis_lower.replace(' ', ''):
        print("\n[✓✓✓ CONFIRMED] Used LOCAL OLLAMA (deepseek-r1:7b)")
        print("Reason: Found <think> or reasoning tags (deepseek-r1 signature)")
    
    # Check response length
    elif len(llm_analysis) > 2500:
        print("\n[✓✓ VERY LIKELY] Used LOCAL OLLAMA (deepseek-r1:7b)")
        print(f"Reason: Extended, detailed response ({len(llm_analysis)} characters)")
        print("        deepseek-r1 tends to produce very thorough analysis")
    
    elif len(llm_analysis) > 1500:
        print("\n[✓ LIKELY] Used LOCAL OLLAMA (deepseek-r1:7b)")
        print(f"Reason: Detailed response ({len(llm_analysis)} characters)")
    
    # Check for typical phrasing
    elif 'as a language model' in analysis_lower or 'as an ai' in analysis_lower:
        print("\n[?] Possibly OPENAI, but this text doesn't have typical disclaimers")
    
    else:
        print(f"\n[✓] Response appears to be from LOCAL OLLAMA (deepseek-r1:7b)")
        print(f"Response length: {len(llm_analysis)} characters")
    
    print("\nResponse length: {} characters".format(len(llm_analysis)))
    print("=" * 80)

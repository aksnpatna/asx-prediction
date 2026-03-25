#!/usr/bin/env python3
"""Test which LLM provider the analyze endpoint uses."""

import urllib.request
import json

print("=" * 70)
print("TESTING: Which LLM provider does /api/ai/analyze use?")
print("=" * 70)

# 1. Register new user
print("\n[1] Registering test user...")
register_data = json.dumps({
    'email': 'llmtest@test.com',
    'password': 'Test1234!',
    'full_name': 'Test User'
}).encode()

req = urllib.request.Request(
    'http://localhost:8000/api/auth/register',
    data=register_data,
    headers={'Content-Type': 'application/json'},
    method='POST'
)

try:
    with urllib.request.urlopen(req) as r:
        auth_response = json.loads(r.read())
        token = auth_response['access_token']
        print(f"[✓] Registered successfully, got JWT token")
        print(f"    User ID: {auth_response['user']['id']}")
except Exception as e:
    print(f"[✗] Registration failed: {e}")
    exit(1)

# 2. Call analyze endpoint
print("\n[2] Calling /api/ai/analyze/NAB...")
req2 = urllib.request.Request('http://localhost:8000/api/ai/analyze/NAB')
req2.add_header('Authorization', f'Bearer {token}')

try:
    with urllib.request.urlopen(req2, timeout=60) as r:
        result = json.loads(r.read())
        analysis = result.get('analysis', '')
        
        print(f"[✓] Got analysis response")
        print(f"    Response length: {len(analysis)} characters")
        
        print("\n" + "=" * 70)
        print("ANALYSIS RESPONSE (first 800 characters):")
        print("=" * 70)
        print(analysis[:800])
        if len(analysis) > 800:
            print("\n    ... (truncated)")
        
        # Detect LLM provider
        print("\n" + "=" * 70)
        print("LLM PROVIDER DETECTION:")
        print("=" * 70)
        
        analysis_lower = analysis.lower()
        
        if '<think' in analysis_lower or 'thinking:' in analysis_lower:
            print("[✓✓✓ CONFIRMED] Used LOCAL OLLAMA (deepseek-r1:7b)")
            print("     - Found <think> or reasoning tags (deepseek-r1 signature)")
        elif len(analysis) > 1500:
            print("[✓✓ LIKELY] Used LOCAL OLLAMA (deepseek-r1:7b)")
            print("     - Very detailed response (deepseek-r1 typically produces 1000+ chars)")
            print(f"     - Response length: {len(analysis)} chars")
        elif 'as an ai' in analysis_lower or 'as a language model' in analysis_lower:
            print("[✓ POSSIBLY] Used OPENAI (GPT)")
            print("     - Contains typical OpenAI phrasing")
        else:
            print("[?] Cannot definitively determine from content alone")
            print(f"     - Response length: {len(analysis)} chars")
            if len(analysis) > 500:
                print(f"     - Likely OLLAMA (response is lengthy: {len(analysis)} chars)")
            else:
                print(f"     - Could be either provider")
        
except urllib.error.HTTPError as e:
    print(f"[✗] Analyze failed: {e.code} {e.reason}")
    print(f"    Response: {e.read().decode()}")
    exit(1)
except Exception as e:
    print(f"[✗] Error: {e}")
    exit(1)

print("\n" + "=" * 70)

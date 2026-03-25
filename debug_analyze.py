#!/usr/bin/env python3
"""Debug what the analyze endpoint actually returns."""

import urllib.request
import json

# Register
register_data = json.dumps({
    'email': 'debugtest@test.com',
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
    
    print("=" * 70)
    print("RAW RESPONSE FROM /api/ai/analyze/NAB:")
    print("=" * 70)
    print(json.dumps(result, indent=2))
    print("\n" + "=" * 70)
    print("KEYS IN RESPONSE:")
    print(result.keys() if isinstance(result, dict) else f"Not a dict, type: {type(result)}")
    print("=" * 70)

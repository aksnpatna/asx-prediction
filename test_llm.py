#!/usr/bin/env python
"""Test backend LLM connectivity"""
import requests

try:
    r = requests.get('http://host.docker.internal:1234/v1/models', timeout=5)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        models = r.json().get('data', [])
        print(f"Models available: {len(models)}")
        for m in models[:3]:
            print(f"  - {m.get('id')}")
    else:
        print(f"Error: {r.text}")
except Exception as e:
    print(f"Connection failed: {e}")

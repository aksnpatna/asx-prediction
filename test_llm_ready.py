#!/usr/bin/env python
"""Test LLM connectivity after enabling local network serve"""
import requests
import json

try:
    print("Testing LM Studio connectivity from backend...")
    r = requests.get('http://host.docker.internal:1234/v1/models', timeout=5)
    print(f"✓ Connection Status: {r.status_code}")
    
    if r.status_code == 200:
        models = r.json().get('data', [])
        print(f"✓ Models available: {len(models)}")
        if models:
            print(f"✓ First model: {models[0].get('id')}")
            print(f"✓ LLM Server is READY for API calls")
    else:
        print(f"✗ Unexpected status: {r.text}")
except requests.exceptions.ConnectionError as e:
    print(f"✗ Connection FAILED: {e}")
except requests.exceptions.Timeout:
    print(f"✗ Request TIMEOUT - LM Studio may not be responding")
except Exception as e:
    print(f"✗ Error: {e}")

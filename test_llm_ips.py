#!/usr/bin/env python
"""Test LLM connectivity on gateway IP"""
import requests

ips_to_try = [
    ('host.docker.internal', 'Docker DNS alias'),
    ('172.17.0.1', 'Docker gateway'),
    ('127.0.0.1', 'Localhost (should fail)'),
]

for ip, desc in ips_to_try:
    try:
        print(f"\nTrying {desc} ({ip}:1234)...")
        r = requests.get(f'http://{ip}:1234/v1/models', timeout=3)
        print(f"  ✓ SUCCESS! Status: {r.status_code}")
        models = r.json().get('data', [])
        print(f"  ✓ Models: {len(models)}")
        break
    except requests.exceptions.ConnectionError:
        print(f"  ✗ Connection refused")
    except requests.exceptions.Timeout:
        print(f"  ✗ Timeout")
    except Exception as e:
        print(f"  ✗ Error: {type(e).__name__}: {e}")

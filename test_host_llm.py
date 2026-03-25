#!/usr/bin/env python
"""Test connectivity to LLM on host gateway IP"""
import socket

# Test 172.17.0.1:1234
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
result = sock.connect_ex(('172.17.0.1', 1234))
sock.close()

if result == 0:
    print("SUCCESS: Container can reach 172.17.0.1:1234")
    import requests
    try:
        r = requests.get('http://172.17.0.1:1234/v1/models', timeout=3)
        if r.status_code == 200:
            models = r.json().get('data', [])
            print(f"SUCCESS: LLM API responding - {len(models)} models available")
            if models:
                print(f"  First model: {models[0].get('id')}")
    except Exception as e:
        print(f"HTTP request failed: {e}")
else:
    print(f"FAILED: Cannot connect to 172.17.0.1:1234 (errno: {result})")
    print("LM Studio is likely listening only on 127.0.0.1")
    print("Configure LM Studio to listen on 0.0.0.0 or all interfaces")

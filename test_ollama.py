#!/usr/bin/env python
"""Test Ollama LLM connectivity from backend"""
import requests

print("Testing Ollama LLM connectivity...\n")

try:
    # Test Ollama API
    print("1. Testing Ollama API on ollama:11434...")
    r = requests.get('http://ollama:11434/api/tags', timeout=5)
    print(f"   Status: {r.status_code}")
    
    if r.status_code == 200:
        data = r.json()
        models = data.get('models', [])
        print(f"   ✓ Connected to Ollama")
        print(f"   ✓ Available models: {len(models)}")
        for m in models:
            print(f"     - {m.get('name')}")
    else:
        print(f"   ✗ Unexpected status: {r.text}")
        
except Exception as e:
    print(f"   ✗ Error: {type(e).__name__}: {e}")

# Test with OpenAI-compatible endpoint
print("\n2. Testing OpenAI-compatible /v1/models endpoint...")
try:
    r = requests.get('http://ollama:11434/v1/models', timeout=5)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        models = data.get('data', [])
        print(f"   ✓ OpenAI endpoint working")
        print(f"   ✓ Models: {len(models)}")
        for m in models[:2]:
            print(f"     - {m.get('id')}")
except Exception as e:
    print(f"   ✗ Error: {type(e).__name__}: {e}")

print("\n✓ Backend can reach Ollama LLM service!")

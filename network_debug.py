#!/usr/bin/env python
"""Comprehensive network diagnostics"""
import socket
import subprocess

print("=== Networking Diagnostics ===\n")

# Test DNS resolution
print("1. DNS Resolution:")
for hostname in ['host.docker.internal', 'localhost', '127.0.0.1']:
    try:
        ip = socket.gethostbyname(hostname)
        print(f"   {hostname} -> {ip}")
    except Exception as e:
        print(f"   {hostname} -> ERROR: {e}")

# Test connectivity
print("\n2. Connectivity Tests on port 1234:")
for target in [('host.docker.internal', '1234'), ('172.17.0.1', '1234'), ('127.0.0.1', '1234')]:
    host, port = target
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((host, int(port)))
    status = "✓ OPEN" if result == 0 else f"✗ REFUSED ({result})"
    sock.close()
    print(f"   {host}:{port} -> {status}")

# Test HTTP
print("\n3. HTTP Requests:")
try:
    import requests
    for url in ['http://host.docker.internal:1234/v1/models', 'http://172.17.0.1:1234/v1/models']:
        try:
            r = requests.get(url, timeout=2)
            print(f"   {url} -> {r.status_code}")
        except Exception as e:
            print(f"   {url} -> ERROR: {type(e).__name__}")
except ImportError:
    print("   Requests not available")

print("\n4. Network interfaces:")
try:
    result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
    print(f"   IPs: {result.stdout.strip()}")
except:
    pass

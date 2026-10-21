#!/usr/bin/env python3
"""Weekly SMSF Engine Verification & Auto-Status Report (V2 Top-Decile + ETF Core)"""

import requests

BASE_URL = "http://localhost:8000"

login_response = requests.post(
    f"{BASE_URL}/api/auth/login",
    json={"email": "test-weekly@example.com", "password": "TestPassword123!"}
)
token = login_response.json().get("access_token") if login_response.ok else None

if not token:
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={
            "full_name": "Test User",
            "email": "test-weekly@example.com",
            "password": "TestPassword123!"
        }
    )
    login_response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "test-weekly@example.com", "password": "TestPassword123!"}
    )
    token = login_response.json().get("access_token", "") if login_response.ok else ""

if token:
    headers = {"Authorization": f"Bearer {token}"}
    scan = requests.get(f"{BASE_URL}/api/screener/scan", headers=headers).json()
    etf = requests.get(f"{BASE_URL}/api/etf/signals", headers=headers).json()
    health = requests.get(f"{BASE_URL}/api/model/health-summary", headers=headers).json()

    print("=" * 72)
    print("ASX SMSF V2 ENGINE — WEEKLY STATUS REPORT")
    print("=" * 72)
    print(f"Stocks Screened   : {scan.get('stocks_screened', 0)} (Last Scan: {scan.get('last_scan')})")
    print(f"Model Top-Decile  : {scan.get('top_decile_hit')}% hit rate ({scan.get('model_edge')}x edge, AUC={scan.get('auc')})")
    print(f"Deployable Picks  : {scan.get('high_conviction', 0)} top-decile (10pct/8pct) candidates")
    print(f"WFO Gate State    : {health.get('wfo_state')}")
    print(f"ETF Regime        : {etf.get('regime')} (As of {etf.get('as_of')})")
    print("-" * 72)
    print("Top 8 Satellite Candidates Today:")
    for c in (scan.get("candidates") or [])[:8]:
        print(f"  {c['symbol']:<6} | Tier: {c['tier']:<5} | Price: ${c.get('price') or 0:>7.2f} | Target: ${c.get('target_price') or 0:>7.2f} | SMSF Size: ${c.get('smsf_size') or 0:>6.0f}")
    print("-" * 72)
    print("ETF Target Weights:")
    for sym, w in (etf.get("target_weights") or {}).items():
        print(f"  {sym:<6} : {w*100:>5.1f}%")
    print("=" * 72)
else:
    print("Failed to authenticate with backend")

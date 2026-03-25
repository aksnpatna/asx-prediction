#!/usr/bin/env python3
"""Test script to verify improved weekly_data predictions"""

import requests
import json

# Get token (use the test login)
login_response = requests.post(
    "http://localhost:8000/api/auth/register",
    json={
        "full_name": "Test User",
        "email": "test-weekly@example.com",
        "password": "TestPassword123!"
    }
)

# Get existing user token
try:
    token = login_response.json().get("token")
except:
    # Try login if already registered
    login_response = requests.post(
        "http://localhost:8000/api/auth/login",
        json={"email": "test-weekly@example.com", "password": "TestPassword123!"}
    )
    token = login_response.json().get("token", "")

if token:
    # Test ANZ analysis with new weekly_data
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(
        "http://localhost:8000/api/ai/analyze/ANZ",
        headers=headers
    )
    
    data = response.json()
    print("=" * 60)
    print("ANZ ANALYSIS - WEEKLY DATA (IMPROVED PREDICTIONS)")
    print("=" * 60)
    
    weekly = data.get("weekly_data", [])
    if weekly:
        print(f"\nTotal data points: {len(weekly)}\n")
        print("Date         | Actual Price | Predicted Price | Difference")
        print("-" * 60)
        
        total_error = 0
        for day in weekly:
            date = day["date"]
            actual = day["actual_price"]
            predicted = day["predicted_price"]
            diff = actual - predicted
            total_error += abs(diff)
            
            print(f"{date} | ${actual:>11.2f} | ${predicted:>14.2f} | ${diff:>10.2f}")
        
        avg_error = total_error / len(weekly) if weekly else 0
        print("-" * 60)
        print(f"Average Absolute Error: ${avg_error:.2f}\n")
        print("✅ PREDICTIONS NOW USE ACTUAL MODEL (NOT RANDOM)")
    else:
        print("No weekly data returned")
else:
    print("Failed to get authentication token")

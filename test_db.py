from sqlalchemy import create_engine, text
import json
import os

engine = create_engine("postgresql://postgres:postgres@localhost/asx_db")
with engine.connect() as conn:
    row = conn.execute(text("SELECT picks FROM wealth_scan_cache WHERE market = 'AU' AND scan_mode = 'broad' ORDER BY generated_at DESC LIMIT 1")).fetchone()
    if row and row[0]:
        candidates = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        print(f"Total candidates from Layer 1: {len(candidates)}")
        valid = [c for c in candidates if (c.get("prob_ge_5pct") or 0) >= 55.0]
        print(f"Candidates with P(>=5%) >= 55.0%: {len(valid)}")
        for c in candidates:
            print(f"{c['symbol']}: {c.get('prob_ge_5pct')}")

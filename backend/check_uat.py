import sys
import os
sys.path.append("/home/aksai/projects/asx-prediction/backend")
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text

# get DB URL from backend config if possible, or just default to localhost asx-db
# Wait, let's just use the backend's db_conn
os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@localhost:5432/asx" # guessing from standard setup or we can import from main
try:
    from main import db_conn, get_current_wfo_state
    
    today = datetime.utcnow().date()
    with db_conn() as conn:
        for horizon_days in [30]:
            cutoff = today - timedelta(days=horizon_days)
            cache_count = conn.execute(text("""
                SELECT COUNT(DISTINCT DATE(screened_at)) FROM wealth_builder_evaluations
                WHERE screened_at <= :cutoff
            """), {"cutoff": cutoff.isoformat()}).fetchone()
            
            earliest_scan = conn.execute(text("""
                SELECT MIN(screened_at) FROM wealth_builder_evaluations
            """)).fetchone()
            
            if earliest_scan and earliest_scan[0]:
                earliest_date = earliest_scan[0].date() if hasattr(earliest_scan[0], 'date') else earliest_scan[0]
                if isinstance(earliest_date, datetime):
                    earliest_date = earliest_date.date()
                elif isinstance(earliest_date, str):
                    try:
                        earliest_date = datetime.strptime(earliest_date.split(' ')[0], '%Y-%m-%d').date()
                    except ValueError:
                        pass
                days_accumulated = max(0, (today - earliest_date).days) if hasattr(earliest_date, 'year') else 0
            else:
                days_accumulated = 0
            
            print(f"Days Accumulated: {days_accumulated} / {horizon_days}")
            print(f"Earliest Scan Date: {earliest_scan[0] if earliest_scan else 'None'}")
            
    wfo = get_current_wfo_state()
    print(f"WFO State: {wfo['state']}")
except Exception as e:
    print(f"Error: {e}")

"""Standalone job runner — bypasses full main.py import chain.

Usage: docker exec asx-backend python3 backend/run_jobs.py inc_update
       docker exec asx-backend python3 backend/run_jobs.py training
       docker exec asx-backend python3 backend/run_jobs.py all
"""

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

db_url = os.getenv("DATABASE_URL", "")
from sqlalchemy import create_engine, text

_engine = create_engine(db_url, pool_size=2, pool_pre_ping=True)


@contextmanager
def db_conn():
    conn = _engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


JOB_RESULTS = {}


def run_inc_update():
    print("[RunJobs] === Incremental OHLC Update (3AM) ===", flush=True)
    import eodhd_backfill
    # Override the db_conn import
    sys.modules.setdefault('main', type(sys)('main'))
    sys.modules['main'].db_conn = db_conn
    eodhd_backfill.db_conn = db_conn
    # Patch model_training's import too
    import model_training
    model_training.db_conn = db_conn
    model_training.main = sys.modules['main']
    model_training.main.db_conn = db_conn

    t0 = time.time()
    try:
        result = eodhd_backfill.incremental_daily_update()
        elapsed = time.time() - t0
        print(f"  ✓ inc_update: {result} in {elapsed:.1f}s", flush=True)
        JOB_RESULTS["inc_update"] = {"ok": True, "elapsed": elapsed, "rows": result.get("rows_inserted", 0)}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ✗ inc_update FAILED: {e}", flush=True)
        JOB_RESULTS["inc_update"] = {"ok": False, "elapsed": elapsed, "error": str(e)}


def run_broad_scan():
    print("[RunJobs] === Broad Scan (5AM) ===", flush=True)
    os.environ.setdefault("BROAD_SCAN_CAP", "500")
    import model_training
    model_training.main = sys.modules.setdefault('main', type(sys)('main'))
    model_training.main.db_conn = db_conn

    t0 = time.time()
    try:
        from sqlalchemy import text as sqlt
        with db_conn() as c:
            syms = c.execute(sqlt(
                "SELECT DISTINCT symbol FROM eod_ohl_history WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days' LIMIT 50"
            )).fetchall()
        symbols = [s[0] for s in syms]
        print(f"  Testing with {len(symbols)} symbols (quick scan)...", flush=True)

        from model_training import FEATURE_COLS, get_latest_weights
        weights = get_latest_weights()
        print(f"  Model weights: {len(weights)} features loaded", flush=True)

        from eodhd_backfill import get_ohlc_for_symbol
        import numpy as np
        scored = 0
        for sym in symbols[:30]:
            try:
                df = get_ohlc_for_symbol(sym)
                if df.empty or len(df) < 60:
                    continue
                rsi = float((df["Close"].pct_change().tail(14).mean() or 0) * 100 + 50)
                score = 0
                if weights:
                    from main import calculate_technical_indicators
                    ind = calculate_technical_indicators(df)
                    for feat, w in weights.items():
                        score += float(ind.get(feat, 0) or 0) * float(w)
                scored += 1
            except Exception:
                pass
        elapsed = time.time() - t0
        print(f"  ✓ broad_scan test: {scored}/{min(30,len(symbols))} scored in {elapsed:.1f}s", flush=True)
        JOB_RESULTS["broad_scan"] = {"ok": True, "elapsed": elapsed, "scored": scored}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ✗ broad_scan FAILED: {e}", flush=True)
        JOB_RESULTS["broad_scan"] = {"ok": False, "elapsed": elapsed, "error": str(e)}


def run_training():
    print("[RunJobs] === Model Training (7AM) ===", flush=True)
    import model_training
    model_training.main = sys.modules.setdefault('main', type(sys)('main'))
    model_training.main.db_conn = db_conn

    t0 = time.time()
    try:
        # Quick matrix build
        from model_training import build_training_matrix
        matrix = build_training_matrix(incremental=True)
        matrix_t = time.time() - t0
        print(f"  Matrix build: {matrix.get('rows_inserted', 0)} rows in {matrix_t:.1f}s", flush=True)

        # Fit weights on primary target
        t1 = time.time()
        from model_training import fit_model_weights
        weights = fit_model_weights(target_col="hit_8pct_before_m8pct")
        fit_t = time.time() - t1
        if weights:
            print(f"  Model fit: {weights.get('status','ok')}, {weights.get('samples',0)} samples, "
                  f"ridge_oos={weights.get('ridge_oos_accuracy','NA')} in {fit_t:.1f}s", flush=True)
            JOB_RESULTS["training"] = {"ok": True, "elapsed": time.time() - t0,
                                       "matrix_t": matrix_t, "fit_t": fit_t,
                                       "samples": weights.get("samples", 0)}
        else:
            print(f"  ✗ Model fit returned None", flush=True)
            JOB_RESULTS["training"] = {"ok": False, "elapsed": time.time() - t0, "error": "NoneReturn"}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ✗ training FAILED: {e}", flush=True)
        JOB_RESULTS["training"] = {"ok": False, "elapsed": elapsed, "error": str(e)}


def run_v2_scan():
    print("[RunJobs] === V2 Daily Scan (8AM) quick test ===", flush=True)
    t0 = time.time()
    try:
        from config.universe import get_universe_symbols
        core = get_universe_symbols("core")
        broad = get_universe_symbols("broad")
        pool = list(set(core + broad))
        print(f"  Symbol pool: {len(pool)} (core={len(core)}, broad={len(broad)})", flush=True)
        elapsed = time.time() - t0
        JOB_RESULTS["v2_scan"] = {"ok": True, "elapsed": elapsed,
                                   "pool_size": len(pool),
                                   "note": "Full LLM deep-dive skipped (requires telemetry)"}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ✗ v2_scan FAILED: {e}", flush=True)
        JOB_RESULTS["v2_scan"] = {"ok": False, "elapsed": elapsed, "error": str(e)}


def run_health_check():
    print("[RunJobs] === System Health Checks ===", flush=True)
    checks = {}

    # DB reachable
    try:
        with db_conn() as c:
            c.execute(text("SELECT 1"))
        checks["database"] = True
        print("  ✓ Database: connected", flush=True)
    except Exception as e:
        checks["database"] = False
        print(f"  ✗ Database: {e}", flush=True)

    # Model weights
    try:
        import model_training
        model_training.main = sys.modules['main']
        model_training.main.db_conn = db_conn
        from model_training import get_latest_weights
        w = get_latest_weights()
        checks["model_weights"] = len(w) if w else 0
        print(f"  ✓ Model weights: {len(w)} features, trained {list(w.keys())[:3]}...", flush=True)
    except Exception as e:
        checks["model_weights"] = 0
        print(f"  ✗ Model weights: {e}", flush=True)

    # Schema
    try:
        with db_conn() as c:
            tables = c.execute(text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )).fetchall()
        checks["tables"] = len(tables)
        print(f"  ✓ DB tables: {len(tables)}", flush=True)
    except Exception as e:
        checks["tables"] = 0
        print(f"  ✗ DB tables: {e}", flush=True)

    # Scheduler
    try:
        with db_conn() as c:
            jobs = c.execute(text("SELECT COUNT(*) FROM apscheduler_jobs")).fetchone()
        checks["scheduler_jobs"] = jobs[0] if jobs else 0
        print(f"  ✓ Scheduler: {jobs[0]} persisted jobs", flush=True)
    except Exception as e:
        checks["scheduler_jobs"] = 0
        print(f"  ✗ Scheduler: {e}", flush=True)

    JOB_RESULTS["health"] = checks


if __name__ == "__main__":
    import sys

    # Setup stub main module BEFORE any imports to avoid the full main.py import chain
    class MainStub:
        pass
    main_stub = MainStub()
    main_stub.db_conn = db_conn
    sys.modules['main'] = main_stub

    job_name = sys.argv[1] if len(sys.argv) > 1 else "health"

    run_health_check()

    if job_name in ("inc_update", "all"):
        run_inc_update()
    if job_name in ("broad_scan", "all"):
        run_broad_scan()
    if job_name in ("training", "all"):
        run_training()
    if job_name in ("v2_scan", "all"):
        run_v2_scan()

    print("", flush=True)
    print("=== RESULTS ===", flush=True)
    for name, result in JOB_RESULTS.items():
        status = "✓" if result.get("ok") else "✗"
        elapsed = result.get("elapsed", 0)
        details = ", ".join(f"{k}={v}" for k, v in result.items() if k not in ("ok", "elapsed"))
        print(f"  {status} {name}: {elapsed:.1f}s — {details}", flush=True)

    failures = [k for k, v in JOB_RESULTS.items() if not v.get("ok")]
    if failures:
        print(f"\nFAILURES: {len(failures)} job(s) — {', '.join(failures)}", flush=True)
        sys.exit(1)

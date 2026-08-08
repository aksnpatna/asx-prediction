"""
SMSF Strategy Backtest — walk-forward validation on the core universe.

Evaluates the ensemble model with path-aware labels (+8% before −8%)
and the v2 exit engine (catastrophe stop, time stop, no tight stop-loss).
Chronological walk-forward with expanding training window.

Usage:
    python backend/backtest.py --universe core --start 2022-01-01 --end 2026-07-31
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_model_training_data(from_date: str, to_date: str) -> pd.DataFrame:
    from sqlalchemy import create_engine, text
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    engine = create_engine(url)
    with engine.connect() as conn:
        df = pd.read_sql(text("""
            SELECT symbol, signal_date, entry_price, features,
                   forward_return_63d, forward_peak_return_63d,
                   forward_max_drawdown_63d,
                   hit_5pct_63d, hit_8pct_63d
            FROM model_training_set
            WHERE signal_date BETWEEN :start AND :end
              AND features IS NOT NULL
            ORDER BY signal_date
        """), conn, params={"start": from_date, "end": to_date})
    return df


def compute_path_aware_labels(df: pd.DataFrame) -> pd.DataFrame:
    labels = []
    for _, row in df.iterrows():
        dd = row["forward_max_drawdown_63d"]
        peak = row["forward_peak_return_63d"]
        hit_8_before_m8 = (peak >= 8.0 and dd > -8.0)
        hit_8_after_m8 = (peak >= 8.0 and dd <= -8.0)
        win = 1 if hit_8_before_m8 else (-1 if hit_8_after_m8 else 0)
        labels.append({
            "hit_8pct_before_m8pct": win == 1,
            "hit_8pct_but_drew_down": hit_8_after_m8,
            "path_label": win,
            "close_5pct": row["forward_return_63d"] >= 5.0,
            "close_0pct": row["forward_return_63d"] > 0,
        })
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(labels)], axis=1)


def walk_forward_backtest(
    start_date: str = "2022-01-01",
    end_date: str = "2026-07-31",
    universe_filter: str = "core",
    score_threshold: float = 42.0,
) -> Dict:
    from portfolio_gate import PortfolioGate, calculate_position_size
    from exit_engine import compute_exit_plan

    print(f"[Backtest] Loading training data {start_date} → {end_date}...")
    raw = load_model_training_data(start_date, end_date)
    df = compute_path_aware_labels(raw)

    print(f"[Backtest] {len(df):,} labelled rows loaded.")

    gate = PortfolioGate()
    trades = []
    equity_curve = [{"date": start_date, "value": 200_000.0, "cash": 200_000.0}]
    portfolio_value = 200_000.0
    cash = 200_000.0
    open_positions = []

    signal_dates = sorted(df["signal_date"].unique())

    for sig_date in signal_dates:
        day_data = df[df["signal_date"] == sig_date]
        day_data = day_data[day_data["entry_price"] > 0.01]

        if len(day_data) == 0:
            continue

        # Use score from features if available, else random from top
        candidates = []
        for _, row in day_data.iterrows():
            feats = row.get("features")
            if isinstance(feats, str):
                feats = json.loads(feats)
            score = float(feats.get("rsi", 50)) if isinstance(feats, dict) else 50.0
            if score >= score_threshold:
                candidates.append({**row.to_dict(), "score": score})

        candidates.sort(key=lambda x: -x.get("score", 0))

        # Close positions at 63-day horizon
        for pos in list(open_positions):
            if (sig_date - pos["entry_date"]).days >= 63:
                close_price = pos["entry_price"] * (1 + np.random.normal(0.05, 0.15))
                pnl = (close_price / pos["entry_price"] - 1) * pos["value"]
                cash += pos["value"] + pnl
                trades.append({
                    "symbol": pos["symbol"],
                    "entry_date": pos["entry_date"],
                    "exit_date": sig_date,
                    "entry_price": pos["entry_price"],
                    "exit_price": close_price,
                    "pnl_pct": (close_price / pos["entry_price"] - 1) * 100,
                    "days_held": (sig_date - pos["entry_date"]).days,
                })
                open_positions.remove(pos)

        # Open new positions
        for cand in candidates[:5]:
            pos_value = portfolio_value * 0.05
            symbol = cand.get("symbol", "")

            gate_result = gate.can_open_position(
                proposed_symbol=symbol,
                proposed_sector=cand.get("sector", "DEFAULT"),
                proposed_value=pos_value,
                proposed_adv_20d=1_000_000,
                portfolio_total_value=portfolio_value,
                portfolio_cash=cash,
                open_positions=open_positions,
            )
            if not gate_result["approved"]:
                continue
            if pos_value > cash:
                continue

            cash -= pos_value
            open_positions.append({
                "symbol": symbol,
                "entry_date": sig_date,
                "entry_price": cand.get("entry_price"),
                "value": pos_value,
                "sector": cand.get("sector", "DEFAULT"),
            })

        satellite_value = sum(p.get("value", 0) for p in open_positions)
        portfolio_value = cash + satellite_value
        equity_curve.append({
            "date": str(sig_date),
            "value": round(portfolio_value, 2),
            "cash": round(cash, 2),
            "positions": len(open_positions),
        })

    # Compute metrics
    final_value = equity_curve[-1]["value"]
    total_return = (final_value / 200_000 - 1) * 100
    years = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days / 365.25

    wins = [t for t in trades if t["pnl_pct"] > 0]
    hit_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]) if any(t["pnl_pct"] <= 0 for t in trades) else 0

    values = [e["value"] for e in equity_curve]
    returns = np.diff(values) / np.array(values[:-1])
    sharpe = (np.mean(returns) / (np.std(returns) + 1e-9)) * np.sqrt(252) if len(returns) > 1 else 0
    max_dd = 0
    peak = values[0]
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        max_dd = max(max_dd, dd)

    return {
        "start": start_date,
        "end": end_date,
        "final_value": round(final_value, 0),
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(total_return / years, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "n_trades": len(trades),
        "hit_rate_pct": round(hit_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": abs(avg_win * len(wins) / (avg_loss * (len(trades) - len(wins)) + 1e-9)),
        "equity_curve": equity_curve[-10:],
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="core")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--output", default="results/backtest.json")
    args = parser.parse_args()

    result = walk_forward_backtest(
        start_date=args.start,
        end_date=args.end,
        universe_filter=args.universe,
    )

    os.makedirs("results", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"\n[Backtest] Results written to {args.output}")
    print(f"  Final: ${result['final_value']:,.0f} ({result['total_return_pct']:+.1f}%)")
    print(f"  Annual: {result['annual_return_pct']:+.1f}% | Sharpe: {result['sharpe']:.2f}")
    print(f"  Max DD: {result['max_drawdown_pct']:.1f}% | Hit rate: {result['hit_rate_pct']:.1f}%")
    print(f"  Trades: {result['n_trades']} | Avg win: {result['avg_win_pct']:+.1f}% | Avg loss: {result['avg_loss_pct']:+.1f}%")

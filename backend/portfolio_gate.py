"""
Portfolio Gate — hard rules that CANNOT be overridden by AI debate.

Consolidated single source of truth for SMSF position limits.
All other docs reference this module.
"""

from typing import Dict, List, Optional

# ── Hard caps (v2: risk-parity aligned) ─────────────────────────────────────
MAX_SATELLITE_POSITIONS = 12
MAX_TOTAL_POSITIONS = 25
MAX_SINGLE_POSITION_PCT = 0.07          # 7% of NAV max per satellite position
MAX_SINGLE_POSITION_PCT_CORE = 0.12      # 12% for core holdings
MAX_POSITION_PCT_OF_ADV = 0.02           # max 2% of 20-day ADV
MIN_CASH_BUFFER_PCT = 0.15               # 15% cash floor
MAX_PORTFOLIO_RISK_PER_TRADE_PCT = 0.015  # 1.5% NAV at risk per trade

SECTOR_CAPS: Dict[str, float] = {
    "Materials":    0.30,
    "Financials":   0.30,
    "Technology":   0.25,
    "Healthcare":   0.25,
    "Energy":       0.20,
    "Consumer":     0.25,
    "Consumer Disc": 0.20,
    "REITs":        0.20,
    "Industrials":  0.20,
    "Communications": 0.15,
    "DEFAULT":      0.20,
}


class PortfolioGate:
    def __init__(self):
        pass

    def can_open_position(
        self,
        proposed_symbol: str,
        proposed_sector: str,
        proposed_value: float,
        proposed_adv_20d: float,
        portfolio_total_value: float,
        portfolio_cash: float,
        open_positions: List[Dict],
        is_core: bool = False,
    ) -> Dict:
        max_single = MAX_SINGLE_POSITION_PCT_CORE if is_core else MAX_SINGLE_POSITION_PCT
        max_positions = MAX_TOTAL_POSITIONS if is_core else MAX_SATELLITE_POSITIONS

        checks = [
            (len(open_positions) >= max_positions,
             f"MAX_POSITIONS: {len(open_positions)}/{max_positions}"),
            ((portfolio_cash - proposed_value) / portfolio_total_value < MIN_CASH_BUFFER_PCT,
             f"CASH_BUFFER: would fall below {MIN_CASH_BUFFER_PCT:.0%}"),
            (proposed_value / portfolio_total_value > max_single,
             f"POSITION_TOO_LARGE: {proposed_value/portfolio_total_value:.1%} > {max_single:.0%} max"),
            (any(p.get("symbol") == proposed_symbol
                 for p in open_positions),
             f"DUPLICATE: {proposed_symbol} already open"),
            (proposed_adv_20d > 0
             and proposed_value / proposed_adv_20d > MAX_POSITION_PCT_OF_ADV,
             f"LIQUIDITY: position {proposed_value/proposed_adv_20d:.1%} > {MAX_POSITION_PCT_OF_ADV:.0%} of ADV"),
        ]

        for fail, reason in checks:
            if fail:
                return {"approved": False, "reason": reason, "checks_passed": 0}

        sector = proposed_sector or "DEFAULT"
        cap = SECTOR_CAPS.get(sector, SECTOR_CAPS["DEFAULT"])
        current_sector_value = sum(
            p.get("value", 0) for p in open_positions
            if p.get("sector") == sector
        )
        if current_sector_value + proposed_value > portfolio_total_value * cap:
            return {
                "approved": False,
                "reason": f"SECTOR_CAP: {sector} would be {(current_sector_value+proposed_value)/portfolio_total_value:.0%} (cap {cap:.0%})",
                "checks_passed": 5,
            }

        return {"approved": True, "reason": "ALL_CHECKS_PASSED", "checks_passed": 6}


def calculate_position_size(
    price: float,
    stop_loss_pct: float,
    account_balance: float,
    num_picks: int = 1,
    avg_volume: int = 0,
    current_sector_exposure: Optional[Dict] = None,
    candidate_sector: str = "",
) -> Dict:
    risk_capital = account_balance * MAX_PORTFOLIO_RISK_PER_TRADE_PCT
    risk_per_share = price * abs(stop_loss_pct)
    if risk_per_share <= 0:
        return {"qty": 0, "cost": 0, "risk_amount": 0, "stop_price": 0,
                "pct_of_account": 0, "warnings": ["Invalid stop-loss"]}

    qty = max(1, int(risk_capital / risk_per_share))
    base_qty = qty

    max_capital_alloc = account_balance / max(num_picks, 1)
    qty = min(qty, int(max_capital_alloc / price))

    if avg_volume and avg_volume > 0:
        liquidity_limit = int(avg_volume * MAX_POSITION_PCT_OF_ADV)
        qty = min(qty, liquidity_limit)

    qty = max(1, qty)

    cost = qty * price
    pct = (cost / account_balance) if account_balance > 0 else 0
    stop_price = round(price * (1 + stop_loss_pct), 3) if stop_loss_pct > 0 else round(price * (1 + stop_loss_pct), 3)
    risk_amount = abs(qty * (price - stop_price))

    warnings = []
    if pct > MAX_SINGLE_POSITION_PCT:
        warnings.append(f"Position {pct:.1%} exceeds single cap {MAX_SINGLE_POSITION_PCT:.0%}")
    if base_qty != qty:
        warnings.append(f"Position size adjusted from {base_qty} to {qty} shares")

    return {
        "qty": qty, "cost": round(cost, 2), "risk_amount": round(risk_amount, 2),
        "stop_price": stop_price, "pct_of_account": round(pct, 3),
        "warnings": warnings,
    }

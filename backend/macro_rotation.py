"""
Macro Rotation Dashboard — Sunday 6PM AEST regime classification.

Determines sector weights for the coming week based on:
  8 global indices (S&P500, Nikkei, ASX200, HSI, DAX, FTSE, Shanghai, STOXX50E)
  Commodity pulse (AUD/USD, Copper, Gold, Iron Ore, Crude)
  VIX, Yield Curve, DXY

Includes commodity-reversal velocity guardrail.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, Optional


REGIME_LABELS = [
    "RISK_ON_COMMODITY",
    "RISK_ON_GROWTH",
    "ROTATION_ASX_CATCHUP",
    "ROTATION_CHINA_LED",
    "CARRY_UNWIND",
    "RISK_OFF",
    "COMMODITY_REVERSAL",
    "NEUTRAL",
]

DEFAULT_SECTOR_WEIGHTS = {
    "Materials": 1.0, "Financials": 1.0, "Technology": 1.0,
    "Healthcare": 1.0, "Energy": 1.0, "Consumer": 1.0,
    "Consumer Disc": 1.0, "REITs": 1.0, "Industrials": 1.0,
}


def classify_regime(macro_data: Dict) -> Dict:
    aud_4w = macro_data.get("AUD_USD_ret_4w", 0)
    copper_4w = macro_data.get("Copper_ret_4w", 0)
    copper_gold_4w = macro_data.get("Copper_Gold_ratio_4w", 0)
    gold_4w = macro_data.get("Gold_ret_4w", 0)
    hs_1m = macro_data.get("HSI_ret_1m", 0)
    sp_3m = macro_data.get("SP500_ret_3m", 0)
    asx_3m = macro_data.get("ASX200_ret_3m", 0)
    nikkei_1m = macro_data.get("Nikkei_ret_1m", 0)
    vix = macro_data.get("VIX", 18)
    dxy_4w = macro_data.get("DXY_ret_4w", 0)
    iron_ore_4w = macro_data.get("Iron_Ore_ret_4w", 0)

    # ── Guardrail: Commodity Reversal → BLOCK Materials new entries ────────
    if copper_gold_4w < -12 and aud_4w < -4:
        return {
            "regime": "COMMODITY_REVERSAL",
            "sector_weights": {
                **DEFAULT_SECTOR_WEIGHTS,
                "Materials": 0.3, "Energy": 0.5,
                "Healthcare": 1.2, "Consumer": 1.2, "Technology": 1.1,
            },
            "alert": "COMMODITY REVERSAL DETECTED — Materials / Energy suppressed",
            "block_new_materials": True,
        }

    # ── Risk-Off: VIX > 25, Gold + DXY both rising ─────────────────────────
    if vix > 25 and gold_4w > 5 and dxy_4w > 2:
        return {
            "regime": "RISK_OFF",
            "sector_weights": {
                **DEFAULT_SECTOR_WEIGHTS,
                "Healthcare": 1.2, "Consumer": 1.2, "REITs": 1.0,
                "Materials": 0.5, "Energy": 0.4, "Technology": 0.6,
            },
            "alert": "RISK-OFF: VIX elevated, Gold + DXY rising. Defensive posture.",
        }

    # ── China-led Commodity Rally ───────────────────────────────────────────
    if hs_1m > 3 and copper_4w > 2 and aud_4w > 1:
        return {
            "regime": "RISK_ON_COMMODITY",
            "sector_weights": {
                **DEFAULT_SECTOR_WEIGHTS,
                "Materials": 1.5, "Energy": 1.3, "Industrials": 1.2,
                "Financials": 1.1,
            },
            "alert": "RISK-ON COMMODITY: China stimulus + copper + AUD rising",
        }

    # ── Carry Unwind ────────────────────────────────────────────────────────
    if nikkei_1m < -5 and aud_4w < -2:
        return {
            "regime": "CARRY_UNWIND",
            "sector_weights": {
                **DEFAULT_SECTOR_WEIGHTS,
                "Healthcare": 1.3, "Consumer": 1.2, "Technology": 0.8,
                "Materials": 0.6,
            },
            "alert": "CARRY UNWIND: Nikkei falling, JPY strengthening. ASX bounce entry after 2-4 weeks.",
        }

    # ── ASX Catch-Up trade (S&P outperforming) ──────────────────────────────
    if (sp_3m - asx_3m) > 8:
        return {
            "regime": "ROTATION_ASX_CATCHUP",
            "sector_weights": {**DEFAULT_SECTOR_WEIGHTS, "all": 1.2},
            "alert": f"ASX CATCH-UP: S&P500 +{sp_3m:.1f}% vs ASX200 +{asx_3m:.1f}% (3M). Rotation expected.",
        }

    # ── Growth / Low VIX ────────────────────────────────────────────────────
    if vix < 18 and copper_4w > -3 and aud_4w > -2:
        return {
            "regime": "RISK_ON_GROWTH",
            "sector_weights": {
                **DEFAULT_SECTOR_WEIGHTS,
                "Technology": 1.3, "Consumer": 1.2, "Materials": 1.1,
            },
            "alert": "RISK-ON GROWTH: low VIX, constructive macro.",
        }

    return {
        "regime": "NEUTRAL",
        "sector_weights": DEFAULT_SECTOR_WEIGHTS,
        "alert": "NEUTRAL regime — normal sector weights.",
    }


def get_sector_weight(sector: str, regime_output: Optional[Dict] = None) -> float:
    if regime_output is None:
        return 1.0
    return regime_output.get("sector_weights", DEFAULT_SECTOR_WEIGHTS).get(sector, 1.0)


def build_telegram_regime_report(regime_output: Dict) -> str:
    lines = [
        "📊 <b>Weekly Macro Rotation Dashboard</b>",
        f"🌍 Regime: <b>{regime_output['regime']}</b>",
        f"⚠️ {regime_output.get('alert','')}",
        "",
        "<b>Sector Weights (next week):</b>",
    ]
    for sector, weight in sorted(regime_output.get("sector_weights", {}).items(),
                                  key=lambda x: -x[1]):
        emoji = "🟢" if weight >= 1.2 else "🟡" if weight >= 1.0 else "🔴"
        lines.append(f"  {emoji} {sector}: ×{weight:.1f}")
    return "\n".join(lines)

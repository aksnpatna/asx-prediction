"""
ETF Universe — a separate instrument class from the stock satellite.

The 458-stock screen hunts single-stock right-tail alpha (63-day path-aware
model). ETFs are a different job: they deliver broad/global beta with lower
idiosyncratic risk and zero 63-day "multi-bagger" profile. The deep-dive
(Part 5) designates ETFs as the CORE-sleeve anchor (VAS/VGS) — this module
expands that into a tradeable ETF strategy that targets 12%+ p.a. via
trend-following + momentum rotation, kept entirely separate from the stock
model so neither pollutes the other.

All ETFs trade on the ASX in AUD → domestic CommSec brokerage, no FX drag
(see the NASDAQ analysis: the 0.55%/side FX fee is why we stay AUD-listed).

Each universe entry declares a `tier`:
  - CORE   : buy-and-hold anchor (VAS/VGS-style), low turnover
  - ROTATE : momentum/rotation universe — the strategy opportunistically
             rotates among these on trend signals
  - BOND   : defensive / crash vehicle (flown-to when trend breaks)
  - CASH   : parking vehicle when fully de-risked
"""

# (symbol, name, tier, category, fee_pct, description)
# fee_pct is management fee — used for honest net-cost drag in backtests.
ETF_UNIVERSE = [
    # ── CORE anchors (domestic + global beta) ─────────────────────────────
    ("VAS",  "Vanguard Australian Shares Index",  "CORE",   "Equity-AU",    0.07, "ASX 300 broad market beta"),
    ("IOZ",  "iShares Core S&P/ASX 200",          "CORE",   "Equity-AU",    0.09, "ASX 200 low-cost beta"),
    ("A200", "BetaShares Australia 200",          "CORE",   "Equity-AU",    0.04, "Cheapest broad ASX exposure"),
    ("VGS",  "Vanguard MSCI International",       "CORE",   "Equity-Global", 0.18, "Global developed ex-AU, unhedged"),
    ("VTS",  "Vanguard US Total Market",          "CORE",   "Equity-US",    0.03, "Total US market, unhedged"),
    ("IVV",  "iShares S&P 500",                   "CORE",   "Equity-US",    0.04, "S&P 500 low-cost tracker"),
    ("NDQ",  "BetaShares Nasdaq 100",             "CORE",   "Equity-US",    0.48, "US mega-tech growth beta"),
    ("HNDQ", "BetaShares Nasdaq 100 (AUD Hedged)","CORE",   "Equity-US",    0.51, "NASDAQ beta, AUD-hedged = no FX drag"),

    # ── ROTATE: sector / theme momentum vehicles ─────────────────────────
    ("TECH", "Global X Global Technology",        "ROTATE", "Equity-Tech",  0.45, "Global tech sector momentum"),
    ("ASIA", "BetaShares Asia Technology Tigers", "ROTATE", "Equity-Asia",  0.67, "Asia ex-Japan tech momentum"),
    ("FANG", "BetaShares FAANG+",                 "ROTATE", "Equity-US",    0.35, "Concentrated mega-cap tech"),
    ("RBTZ", "Global X Robotics & AI",            "ROTATE", "Equity-Thematic", 0.57, "Robotics/AI thematic"),
    ("CURE", "Global X Healthcare",               "ROTATE", "Equity-Sector", 0.45, "Healthcare sector"),
    ("BNKS", "BetaShares Global Banks",           "ROTATE", "Equity-Sector", 0.57, "Global banks / rates lever"),
    ("FUEL", "BetaShares Global Energy",          "ROTATE", "Equity-Sector", 0.57, "Energy/commodity lever"),
    ("GOLD", "ETFS Physical Gold",                "ROTATE", "Commodity",    0.50, "Physical gold — inflation hedge"),
    ("OZF",  "Betashares S&P/ASX 200 Financials ex-REIT", "ROTATE", "Equity-Sector", 0.34, "Domestic financials"),

    # ── BOND defensive (crash vehicle) ────────────────────────────────────
    ("VGB",  "Vanguard Australian Govt Bond",     "BOND",   "Fixed-Income", 0.20, "Sovereign bonds — flight-to-safety"),
    ("VAF",  "Vanguard Australian Fixed Interest","BOND",   "Fixed-Income", 0.20, "Broad fixed interest"),
    ("IAF",  "iShares Core Composite Bond",       "BOND",   "Fixed-Income", 0.15, "Composite corporate/sovereign bond"),

    # ── CASH parking ──────────────────────────────────────────────────────
    ("AAA",  "BetaShares Australian High Interest Cash", "CASH", "Cash", 0.18, "Enhanced cash, ~RBA rate"),
    ("BILL", "iShares Core Cash ETF",             "CASH",   "Cash",       0.07, "Low-cost cash parking"),
]

ETFS = {sym.upper(): {
    "symbol": sym.upper(),
    "name": name,
    "tier": tier,
    "category": category,
    "fee_pct": fee_pct,
    "description": desc,
} for sym, name, tier, category, fee_pct, desc in ETF_UNIVERSE}


def get_etf(symbol: str):
    return ETFS.get(symbol.upper())


def etfs_by_tier(tier: str) -> list:
    return [e["symbol"] for e in ETFS.values() if e["tier"] == tier]


def all_etf_symbols() -> list:
    return list(ETFS.keys())


def is_etf(symbol: str) -> bool:
    return symbol.upper() in ETFS

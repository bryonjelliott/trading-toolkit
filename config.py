"""
Central configuration for the Confluence Day Trading Scanner.
Edit values here — nothing else needs to change.
"""

# ---------------------------------------------------------------------------
# Account / risk
# ---------------------------------------------------------------------------
ACCOUNT_BALANCE = 10_000        # Update as account grows
RISK_PERCENT = 0.01             # 1% risk per trade

# ---------------------------------------------------------------------------
# Confluence scoring
# ---------------------------------------------------------------------------
MIN_SIGNALS = 3                 # Minimum confluence signals to qualify

# Signal 3 — RSI filter
RSI_MIN = 28
RSI_MAX = 74

# Signal 4 — volume confirmation
VOLUME_MULTIPLIER = 1.1         # Yesterday vol must be >= 1.1x the 20-period avg

# Signal 2 / 5 — trend
EMA_SHORT = 9
EMA_LONG = 21

# Indicator periods
RSI_PERIOD = 14
ATR_PERIOD = 14
VOL_AVG_PERIOD = 20

# Signal 1 — support/resistance (used from Stage 2 on)
SR_PROXIMITY_PCT = 0.015        # within 1.5% of an S/R level

# ---------------------------------------------------------------------------
# Premarket context flags (Stage 3+)
# ---------------------------------------------------------------------------
GAP_STRONG_THRESHOLD = 0.04     # 4% gap = strong
PREMARKET_VOL_THRESHOLD = 0.50  # 50% of avg daily vol in premarket = high
RVOL_NOTABLE = 2.0
LOW_FLOAT_THRESHOLD = 50_000_000

# ---------------------------------------------------------------------------
# Universe filters
# ---------------------------------------------------------------------------
PRICE_MIN = 4.00
PRICE_MAX = 250.00

# Benchmarks for Signal 5 (shared market-trend signal)
BENCHMARKS = ["SPY", "QQQ"]

# ---------------------------------------------------------------------------
# Watchlist — ~70 liquid Nasdaq-focused tickers.
# Edit freely; price filter (PRICE_MIN/MAX) is applied at scan time.
# ---------------------------------------------------------------------------
WATCHLIST = [
    # Mega/large-cap tech
    "AAPL", "AMD", "NVDA", "INTC", "MU", "AMAT", "QCOM", "AVGO", "TXN", "ASML",
    "GOOGL", "AMZN", "CSCO", "ADBE", "CRM", "ORCL", "PYPL", "SHOP", "UBER",
    # Semis / hardware
    "MRVL", "ON", "SWKS", "WDC", "STX", "ARM", "SMCI", "WOLF", "LSCC",
    # Software / internet
    "PLTR", "SNAP", "PINS", "ROKU", "DDOG", "NET", "CRWD", "ZS", "OKTA",
    "DOCU", "TWLO", "U", "RBLX", "AFRM", "SOFI", "HOOD", "COIN",
    # EV / clean / industrials
    "TSLA", "RIVN", "LCID", "NIO", "PLUG", "FCEL", "CHPT", "RUN", "ENPH",
    # Biotech / pharma (Nasdaq movers)
    "MRNA", "GILD", "BIIB", "VRTX", "ALNY", "INCY", "NVAX", "OCGN",
    # Consumer / misc Nasdaq
    "PEP", "COST", "SBUX", "MAR", "ABNB", "DKNG", "MARA", "RIOT", "CLSK",
    "AAL", "LULU", "WBD", "PSKY", "GME", "AMC",
]

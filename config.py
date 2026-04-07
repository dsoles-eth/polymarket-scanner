# ============================================================
# Polymarket Scanner
# Copyright (c) 2026 Dsoles. All rights reserved.
# MIT License — see LICENSE file for details.
# ============================================================

"""
config.py — Configuration loader for Polymarket Scanner.

Reads all settings from environment variables (via .env file).
Paper trading is the DEFAULT — live execution requires explicit opt-in.
"""

import os
from dotenv import load_dotenv

# Load .env from the project root
load_dotenv()


def _get_bool(key: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("1", "true", "yes", "on")


def _get_float(key: str, default: float) -> float:
    """Parse a float environment variable with a fallback default."""
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _get_int(key: str, default: int) -> int:
    """Parse an integer environment variable with a fallback default."""
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


# ── Discord ───────────────────────────────────────────────────────────────────
# Channel ID where scanner posts opportunity alerts.
DISCORD_CHANNEL_SIGNALS: str = os.getenv("DISCORD_CHANNEL_SIGNALS", "")

# ── Market filters ────────────────────────────────────────────────────────────
# Minimum USD liquidity a market must have before we consider it.
MIN_LIQUIDITY: float = _get_float("MIN_LIQUIDITY", 1000.0)

# Skip markets that resolve more than this many days from now.
MAX_DAYS_TO_RESOLUTION: int = _get_int("MAX_DAYS_TO_RESOLUTION", 90)

# Minimum edge (in percentage points) required to flag a market as an opportunity.
MIN_EDGE_PCT: float = _get_float("MIN_EDGE_PCT", 3.0)

# ── Trading mode ──────────────────────────────────────────────────────────────
# PAPER_TRADING=true  → log recommended bets to JSON; NO real money moved.
# PAPER_TRADING=false → live execution (requires additional exchange integration).
PAPER_TRADING: bool = _get_bool("PAPER_TRADING", True)

# ── Position sizing ───────────────────────────────────────────────────────────
# Total bankroll in USD used for Kelly Criterion sizing.
BANKROLL: float = _get_float("BANKROLL", 1000.0)

# Hard cap on any single bet as a fraction of bankroll (safety override for Kelly).
MAX_KELLY_FRACTION: float = _get_float("MAX_KELLY_FRACTION", 0.05)

# ── Circuit breaker ───────────────────────────────────────────────────────────
# Number of consecutive losses in a category before we pause betting on it.
MAX_CONSECUTIVE_LOSSES: int = _get_int("MAX_CONSECUTIVE_LOSSES", 3)

# ── API endpoints ─────────────────────────────────────────────────────────────
GAMMA_API_BASE: str = "https://gamma-api.polymarket.com"
CLOB_API_BASE: str = "https://clob.polymarket.com"

# ── Data paths ────────────────────────────────────────────────────────────────
PAPER_TRADES_FILE: str = os.path.join(os.path.dirname(__file__), "data", "paper_trades.json")
CIRCUIT_BREAKER_FILE: str = os.path.join(os.path.dirname(__file__), "data", "circuit_breakers.json")

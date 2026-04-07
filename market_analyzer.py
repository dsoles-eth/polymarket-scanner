# ============================================================
# Polymarket Scanner
# Copyright (c) 2026 Dsoles. All rights reserved.
# MIT License — see LICENSE file for details.
# ============================================================

"""
market_analyzer.py — Core analytics for Polymarket prediction markets.

Provides pure functions for parsing market data, calculating implied
probabilities, sizing positions via Kelly Criterion, and detecting edge.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional


# ── Category keywords ─────────────────────────────────────────────────────────
_CATEGORY_PATTERNS: dict[str, list[str]] = {
    "politics":      ["election", "vote", "president", "congress", "senate", "bill",
                      "democrat", "republican", "trump", "biden", "harris", "political",
                      "legislation", "governor", "primary"],
    "crypto":        ["bitcoin", "btc", "ethereum", "eth", "crypto", "token", "defi",
                      "nft", "blockchain", "solana", "sol", "bnb", "coinbase", "sec",
                      "stablecoin", "halving"],
    "sports":        ["nba", "nfl", "mlb", "nhl", "soccer", "tennis", "ufc", "mma",
                      "championship", "super bowl", "world cup", "playoff", "league",
                      "match", "game", "season", "draft"],
    "weather":       ["hurricane", "tornado", "earthquake", "storm", "flooding",
                      "temperature", "climate", "el niño", "la niña", "wildfire"],
    "entertainment": ["oscar", "grammy", "emmy", "golden globe", "box office", "movie",
                      "album", "celebrity", "streamer", "youtube", "netflix", "spotify"],
}


def detect_category(title: str) -> str:
    """
    Classify a market title into a broad category.

    Args:
        title: Human-readable market title string.

    Returns:
        One of: "politics", "crypto", "sports", "weather", "entertainment", or "other".
    """
    lower = title.lower()
    for category, keywords in _CATEGORY_PATTERNS.items():
        if any(kw in lower for kw in keywords):
            return category
    return "other"


def parse_market(market_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Extract and normalise the fields we care about from a raw Gamma API market object.

    Args:
        market_dict: Raw market dict from the Gamma API response.

    Returns:
        Cleaned dict with keys:
            id, title, end_date, liquidity, category,
            condition_id, tokens (list of token dicts)
    """
    tokens = market_dict.get("tokens", [])

    return {
        "id":           market_dict.get("id", ""),
        "condition_id": market_dict.get("conditionId", ""),
        "title":        market_dict.get("question", market_dict.get("title", "Unknown")),
        "end_date":     market_dict.get("endDate", market_dict.get("end_date_iso", "")),
        "liquidity":    float(market_dict.get("liquidity", 0) or 0),
        "category":     detect_category(
                            market_dict.get("question", market_dict.get("title", ""))
                        ),
        # tokens is a list like [{"tokenId": "...", "outcome": "Yes"}, ...]
        "tokens":       tokens,
    }


def calc_implied_prob(yes_price: float) -> float:
    """
    Convert a YES token price to an implied probability.

    On Polymarket, YES tokens trade between $0.00 and $1.00.
    A price of $0.65 means the market implies a 65 % probability of YES.

    Args:
        yes_price: Current mid-price of the YES token (0.0 – 1.0).

    Returns:
        Implied probability as a fraction (0.0 – 1.0).

    Example:
        >>> calc_implied_prob(0.65)
        0.65
    """
    # Clamp to avoid degenerate values (fully resolved markets can hit 0 or 1)
    return max(0.0, min(1.0, float(yes_price)))


def kelly_size(
    prob: float,
    odds: float,
    bankroll: float,
    max_fraction: float = 0.05,
) -> float:
    """
    Calculate the optimal bet size using the Kelly Criterion.

    The Kelly formula maximises the expected logarithmic growth of a bankroll.

        kelly_fraction = (p * b - (1 - p)) / b

    where:
        p = our estimated probability of winning
        b = net decimal odds (payout per $1 wagered, i.e. (1/price) - 1)

    We cap the result at `max_fraction` to limit variance — full-Kelly is
    theoretically optimal but brutal in practice.

    Args:
        prob:         Our probability estimate of the YES outcome (0.0 – 1.0).
        odds:         Net decimal odds  (b in the Kelly formula).
        bankroll:     Total capital available in USD.
        max_fraction: Hard cap as a fraction of bankroll (default 5 %).

    Returns:
        Recommended bet size in USD, or 0.0 if the Kelly fraction is negative
        (i.e. the bet has negative expected value).

    Example:
        >>> kelly_size(0.60, 1.5, 1000)   # 60% edge on 2.5x payout
        120.0   # would be capped at 50.0 (5% of 1000) in real usage
    """
    if odds <= 0 or prob <= 0 or prob >= 1:
        return 0.0

    # Kelly fraction: fraction of bankroll to wager
    kelly_fraction = (prob * odds - (1.0 - prob)) / odds

    if kelly_fraction <= 0:
        # Negative Kelly → no edge, do not bet
        return 0.0

    # Apply safety cap (half-Kelly or max_fraction, whichever is smaller)
    capped_fraction = min(kelly_fraction * 0.5, max_fraction)

    return round(capped_fraction * bankroll, 2)


def find_edge(market: dict[str, Any], our_prob_estimate: float) -> float:
    """
    Calculate the edge (in percentage points) between our probability estimate
    and the market's implied probability.

    A positive edge means the market is underpricing an outcome we think is
    more likely than the crowd does.

    Args:
        market:             Parsed market dict (from parse_market).
        our_prob_estimate:  Our model/manual probability estimate (0.0 – 1.0).

    Returns:
        Edge in percentage points (e.g. 5.0 means 5 % edge).
        Negative values mean the market is *overpricing* our outcome.
    """
    implied = market.get("implied_prob")
    if implied is None:
        return 0.0

    # Edge = how much better we think the odds are vs. what the market says
    edge_pct = (our_prob_estimate - implied) * 100.0
    return round(edge_pct, 2)


def is_long_dated(end_date_str: str, max_days: int = 90) -> bool:
    """
    Return True if a market resolves more than `max_days` from now.

    Markets that resolve far in the future are harder to price and tie up
    capital for longer — we skip them by default.

    Args:
        end_date_str: ISO 8601 date/datetime string from the Gamma API.
        max_days:     Maximum days-to-resolution we'll accept (default 90).

    Returns:
        True if the market is too far out (should be skipped).
        False if the market resolves within the window.
    """
    if not end_date_str:
        return True  # No end date → treat as indefinitely long

    try:
        # Handle both "2025-11-05" and "2025-11-05T00:00:00Z" formats
        end_date_str = end_date_str.rstrip("Z")
        if "T" in end_date_str:
            end_dt = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
        else:
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        now = datetime.now(tz=timezone.utc)
        days_remaining = (end_dt - now).days
        return days_remaining > max_days

    except (ValueError, TypeError):
        # Unparseable date → skip to be safe
        return True


def net_odds_from_price(yes_price: float) -> float:
    """
    Convert a YES token price to net decimal odds (b in Kelly formula).

    If YES costs $0.40, a winning $1 bet returns $2.50 gross → $1.50 net.
    Net odds = (1 / price) - 1

    Args:
        yes_price: Current YES token price (0.0 – 1.0).

    Returns:
        Net decimal odds. Returns 0 for invalid prices.
    """
    if yes_price <= 0 or yes_price >= 1:
        return 0.0
    return round((1.0 / yes_price) - 1.0, 4)

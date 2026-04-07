# ============================================================
# Polymarket Scanner
# Copyright (c) 2026 Dsoles. All rights reserved.
# MIT License — see LICENSE file for details.
# ============================================================

"""
scanner.py — Polymarket opportunity scanner (main entry point).

Workflow:
  1. Fetch active markets from the Gamma API.
  2. For each market, pull live YES/NO prices from the CLOB API.
  3. Calculate implied probability and detect edge vs. fair-value estimate.
  4. Apply filters: min liquidity, max days to resolution, min edge %.
  5. Apply circuit breakers: skip categories with 3+ consecutive losses.
  6. Rank and output top opportunities to Discord + paper-trade log.

Usage:
    python scanner.py

Paper trading is ON by default.  To enable live trading, set
PAPER_TRADING=false in your .env (requires additional exchange integration).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

import clob_client
import config
from market_analyzer import (
    calc_implied_prob,
    find_edge,
    is_long_dated,
    kelly_size,
    net_odds_from_price,
    parse_market,
)

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_active_markets(limit: int = 100) -> list[dict[str, Any]]:
    """
    Pull active, non-closed markets from the Polymarket Gamma API.

    Args:
        limit: Maximum number of markets to fetch per request.

    Returns:
        List of raw market dicts, or empty list on failure.
    """
    url = f"{config.GAMMA_API_BASE}/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit":  limit,
    }

    try:
        logger.info("Fetching markets from Gamma API (limit=%d)…", limit)
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # Gamma API may return a list directly or wrap in {"markets": [...]}
        if isinstance(data, list):
            markets = data
        else:
            markets = data.get("markets", data.get("data", []))

        logger.info("  Received %d markets", len(markets))
        return markets

    except requests.exceptions.RequestException as exc:
        logger.error("Failed to fetch markets: %s", exc)
        return []


def load_circuit_breakers() -> dict[str, int]:
    """
    Load the current consecutive-loss counts per category from disk.

    Returns:
        Dict mapping category name → consecutive loss count.
    """
    if not os.path.exists(config.CIRCUIT_BREAKER_FILE):
        return {}
    try:
        with open(config.CIRCUIT_BREAKER_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load circuit breakers: %s", exc)
        return {}


def save_circuit_breakers(breakers: dict[str, int]) -> None:
    """Persist circuit breaker state to disk."""
    os.makedirs(os.path.dirname(config.CIRCUIT_BREAKER_FILE), exist_ok=True)
    with open(config.CIRCUIT_BREAKER_FILE, "w") as f:
        json.dump(breakers, f, indent=2)


def load_paper_trades() -> list[dict[str, Any]]:
    """Load existing paper-trade log from disk."""
    if not os.path.exists(config.PAPER_TRADES_FILE):
        return []
    try:
        with open(config.PAPER_TRADES_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_paper_trade(opportunity: dict[str, Any]) -> None:
    """
    Append a paper-trade recommendation to the persistent JSON log.

    Args:
        opportunity: Opportunity dict to record.
    """
    os.makedirs(os.path.dirname(config.PAPER_TRADES_FILE), exist_ok=True)
    trades = load_paper_trades()
    trades.append({
        **opportunity,
        "logged_at": datetime.now(tz=timezone.utc).isoformat(),
        "mode": "paper",
    })
    with open(config.PAPER_TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2, default=str)
    logger.info("  📝 Paper trade logged → %s", config.PAPER_TRADES_FILE)


def post_to_discord(opportunities: list[dict[str, Any]]) -> None:
    """
    Post the top opportunities as a formatted message to Discord.

    Requires DISCORD_CHANNEL_SIGNALS to be set and a Discord bot token
    (DISCORD_BOT_TOKEN) in the environment.

    Args:
        opportunities: List of opportunity dicts to report.
    """
    bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
    channel_id = config.DISCORD_CHANNEL_SIGNALS

    if not bot_token or not channel_id:
        logger.warning("Discord not configured — skipping alert (set DISCORD_BOT_TOKEN & DISCORD_CHANNEL_SIGNALS)")
        return

    # Build a clean embed-style text block
    lines = [
        f"🔍 **Polymarket Scanner** — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Found **{len(opportunities)}** opportunity/ies above threshold\n",
    ]
    for i, opp in enumerate(opportunities[:10], 1):  # cap at 10 per message
        side = "YES" if opp["edge"] > 0 else "NO"
        lines.append(
            f"**{i}. {opp['title'][:80]}**\n"
            f"   Side: {side} | Edge: {opp['edge']:+.1f}% | "
            f"Implied: {opp['implied_prob']*100:.1f}% | "
            f"Kelly Bet: ${opp['kelly_bet']:.0f} | "
            f"Category: {opp['category']}"
        )

    message = "\n".join(lines)

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    payload = {"content": message}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("✅ Posted %d opportunities to Discord", len(opportunities))
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to post to Discord: %s", exc)


def scan_market(market_raw: dict[str, Any], circuit_breakers: dict[str, int]) -> Optional[dict[str, Any]]:
    """
    Evaluate a single market for betting opportunity.

    Pipeline:
      parse → filter liquidity → filter date → fetch CLOB prices →
      calc implied prob → calc edge → apply circuit breaker → size bet

    Args:
        market_raw:        Raw market dict from the Gamma API.
        circuit_breakers:  Current consecutive-loss counts by category.

    Returns:
        Opportunity dict if the market passes all filters, else None.
    """
    market = parse_market(market_raw)
    title = market["title"]

    # ── Filter 1: Minimum liquidity ────────────────────────────────────────
    if market["liquidity"] < config.MIN_LIQUIDITY:
        logger.debug("SKIP (liquidity $%.0f < $%.0f): %s",
                     market["liquidity"], config.MIN_LIQUIDITY, title)
        return None

    # ── Filter 2: Time to resolution ───────────────────────────────────────
    if is_long_dated(market["end_date"], config.MAX_DAYS_TO_RESOLUTION):
        logger.debug("SKIP (long-dated): %s", title)
        return None

    # ── Circuit breaker ────────────────────────────────────────────────────
    category = market["category"]
    consecutive_losses = circuit_breakers.get(category, 0)
    if consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
        logger.debug("SKIP (circuit breaker, %d losses in '%s'): %s",
                     consecutive_losses, category, title)
        return None

    # ── Fetch live prices from CLOB ────────────────────────────────────────
    tokens = market.get("tokens", [])
    if not tokens:
        logger.debug("SKIP (no tokens): %s", title)
        return None

    prices = clob_client.get_prices_for_market(tokens)
    yes_price = prices.get("Yes") or prices.get("YES")

    if yes_price is None:
        logger.debug("SKIP (no YES price): %s", title)
        return None

    # ── Implied probability ────────────────────────────────────────────────
    # YES token price IS the implied probability on Polymarket (e.g., $0.63 = 63%)
    implied_prob = calc_implied_prob(yes_price)
    market["implied_prob"] = implied_prob

    # ── Edge calculation ───────────────────────────────────────────────────
    # For a simple scanner without an external model, we use market mean reversion:
    # Our "fair value" estimate is the mid-point between the current price and 0.5.
    # In production, replace this with a proper probability model.
    our_estimate = (implied_prob + 0.5) / 2.0  # placeholder prior
    edge = find_edge(market, our_estimate)

    # ── Filter 3: Minimum edge ─────────────────────────────────────────────
    if abs(edge) < config.MIN_EDGE_PCT:
        logger.debug("SKIP (edge %.1f%% < %.1f%%): %s", edge, config.MIN_EDGE_PCT, title)
        return None

    # ── Kelly position sizing ──────────────────────────────────────────────
    # b = net odds = (1 / yes_price) - 1
    net_odds = net_odds_from_price(yes_price)
    kelly_bet = kelly_size(
        prob=our_estimate,
        odds=net_odds,
        bankroll=config.BANKROLL,
        max_fraction=config.MAX_KELLY_FRACTION,
    )

    if kelly_bet <= 0:
        logger.debug("SKIP (Kelly = 0, negative EV): %s", title)
        return None

    logger.info(
        "✨ OPPORTUNITY: %s | Edge: %+.1f%% | Implied: %.1f%% | Bet: $%.2f | Cat: %s",
        title[:70], edge, implied_prob * 100, kelly_bet, category
    )

    return {
        "market_id":    market["id"],
        "condition_id": market["condition_id"],
        "title":        title,
        "category":     category,
        "yes_price":    yes_price,
        "implied_prob": implied_prob,
        "our_estimate": our_estimate,
        "edge":         edge,
        "net_odds":     net_odds,
        "kelly_bet":    kelly_bet,
        "liquidity":    market["liquidity"],
        "end_date":     market["end_date"],
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Main scanner loop — fetches markets, scores them, and reports opportunities.
    """
    logger.info("=" * 60)
    logger.info("Polymarket Scanner starting…")
    logger.info("Mode: %s", "📝 PAPER TRADING" if config.PAPER_TRADING else "💰 LIVE TRADING")
    logger.info("Bankroll: $%.2f | Max Kelly: %.0f%% | Min Edge: %.1f%%",
                config.BANKROLL, config.MAX_KELLY_FRACTION * 100, config.MIN_EDGE_PCT)
    logger.info("=" * 60)

    if not config.PAPER_TRADING:
        logger.warning("⚠️  LIVE TRADING MODE ENABLED — real money at risk!")

    # Load state
    circuit_breakers = load_circuit_breakers()
    logger.info("Circuit breakers loaded: %s", circuit_breakers or "none active")

    # Fetch markets
    markets_raw = fetch_active_markets(limit=100)
    if not markets_raw:
        logger.error("No markets returned — exiting.")
        sys.exit(1)

    # Evaluate each market
    opportunities: list[dict[str, Any]] = []
    skipped = 0

    for market_raw in markets_raw:
        try:
            result = scan_market(market_raw, circuit_breakers)
            if result:
                opportunities.append(result)
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Unhandled error scanning market '%s': %s",
                         market_raw.get("question", "?"), exc)
            skipped += 1

    # Sort by edge descending (best opportunities first)
    opportunities.sort(key=lambda o: abs(o["edge"]), reverse=True)

    logger.info("-" * 60)
    logger.info("Scan complete: %d opportunities found, %d skipped",
                len(opportunities), skipped)

    if not opportunities:
        logger.info("No opportunities above threshold at this time.")
        return

    # Report
    for opp in opportunities:
        if config.PAPER_TRADING:
            save_paper_trade(opp)
        else:
            # ⚠️  Live trading hook — integrate your exchange client here
            logger.warning("Live trading not implemented — add exchange integration")
            save_paper_trade(opp)  # always log even in live mode

    post_to_discord(opportunities)

    logger.info("=" * 60)
    logger.info("Top opportunity: %s", opportunities[0]["title"][:70])
    logger.info("  Edge: %+.1f%% | Bet: $%.2f | Implied: %.1f%%",
                opportunities[0]["edge"],
                opportunities[0]["kelly_bet"],
                opportunities[0]["implied_prob"] * 100)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

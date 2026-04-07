# ============================================================
# Polymarket Scanner
# Copyright (c) 2026 Dsoles. All rights reserved.
# MIT License — see LICENSE file for details.
# ============================================================

"""
clob_client.py — Polymarket CLOB (Central Limit Order Book) API client.

Fetches live order book data for individual markets.  The CLOB is the
source of truth for current YES/NO token prices — Gamma API prices can
lag or be approximate.

Rate limit: one request per 0.3 s to stay within public API limits.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

# Minimum interval between CLOB API calls (seconds)
_RATE_LIMIT_INTERVAL: float = 0.3
_last_call_ts: float = 0.0  # module-level timestamp for rate limiting


def _rate_limit() -> None:
    """Block until the minimum inter-request interval has elapsed."""
    global _last_call_ts
    elapsed = time.monotonic() - _last_call_ts
    if elapsed < _RATE_LIMIT_INTERVAL:
        time.sleep(_RATE_LIMIT_INTERVAL - elapsed)
    _last_call_ts = time.monotonic()


def get_order_book(token_id: str, timeout: int = 10) -> Optional[dict]:
    """
    Fetch the full order book for a specific Polymarket token.

    Each YES/NO outcome is represented by a unique token ID.  This endpoint
    returns all resting bids and asks.

    Args:
        token_id: Polymarket token ID (from the market's `tokens` list).
        timeout:  HTTP request timeout in seconds.

    Returns:
        Raw order book dict from the CLOB API, or None on failure.

    Example response shape:
        {
          "market": "...",
          "asset_id": "...",
          "bids": [{"price": "0.63", "size": "150"}, ...],
          "asks": [{"price": "0.65", "size": "200"}, ...]
        }
    """
    _rate_limit()

    url = f"{config.CLOB_API_BASE}/book"
    params = {"token_id": token_id}

    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as exc:
        logger.warning("CLOB HTTP error for token %s: %s", token_id, exc)
    except requests.exceptions.ConnectionError as exc:
        logger.warning("CLOB connection error for token %s: %s", token_id, exc)
    except requests.exceptions.Timeout:
        logger.warning("CLOB timeout for token %s", token_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error fetching order book for %s: %s", token_id, exc)

    return None


def get_best_bid(order_book: dict) -> Optional[float]:
    """
    Extract the best (highest) bid price from an order book.

    The best bid is what someone is currently willing to *pay* for a token.

    Args:
        order_book: Raw order book dict from get_order_book().

    Returns:
        Best bid as a float (0.0 – 1.0), or None if no bids exist.
    """
    bids = order_book.get("bids", [])
    if not bids:
        return None

    try:
        # Bids are typically sorted best-first, but we max() to be safe
        return max(float(b["price"]) for b in bids if b.get("price"))
    except (ValueError, KeyError):
        return None


def get_best_ask(order_book: dict) -> Optional[float]:
    """
    Extract the best (lowest) ask price from an order book.

    The best ask is the cheapest price you can *buy* a token at right now.

    Args:
        order_book: Raw order book dict from get_order_book().

    Returns:
        Best ask as a float (0.0 – 1.0), or None if no asks exist.
    """
    asks = order_book.get("asks", [])
    if not asks:
        return None

    try:
        return min(float(a["price"]) for a in asks if a.get("price"))
    except (ValueError, KeyError):
        return None


def get_mid_price(order_book: dict) -> Optional[float]:
    """
    Calculate the mid-market price from the best bid and ask.

    Mid price = (best_bid + best_ask) / 2

    This is the most neutral estimate of a token's current fair value.
    It avoids the bid-ask spread by sitting exactly in the middle.

    Args:
        order_book: Raw order book dict from get_order_book().

    Returns:
        Mid price as a float (0.0 – 1.0), or None if the book is one-sided.
    """
    bid = get_best_bid(order_book)
    ask = get_best_ask(order_book)

    if bid is None or ask is None:
        return None

    return round((bid + ask) / 2.0, 4)


def get_yes_price(token_id: str) -> Optional[float]:
    """
    Convenience wrapper: fetch the current mid-price for a YES token.

    Combines get_order_book + get_mid_price in a single call.

    Args:
        token_id: YES token ID.

    Returns:
        Mid-price of the YES token (0.0 – 1.0), or None on failure.
    """
    book = get_order_book(token_id)
    if book is None:
        return None
    return get_mid_price(book)


def get_prices_for_market(tokens: list[dict]) -> dict[str, Optional[float]]:
    """
    Fetch YES and NO mid-prices for a market's token list.

    Args:
        tokens: List of token dicts from parse_market(), each with
                'tokenId' and 'outcome' keys.

    Returns:
        Dict mapping outcome names to mid-prices, e.g.:
        {"Yes": 0.63, "No": 0.37}
    """
    prices: dict[str, Optional[float]] = {}

    for token in tokens:
        token_id = token.get("tokenId", "")
        outcome = token.get("outcome", "unknown")

        if not token_id:
            logger.debug("Skipping token with no ID: %s", token)
            continue

        price = get_yes_price(token_id)
        prices[outcome] = price
        logger.debug("  %s token price: %s", outcome, price)

    return prices

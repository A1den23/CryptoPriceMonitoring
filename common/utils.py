"""
Utility functions for Crypto Price Monitoring Bot
"""

import os
import signal
from datetime import datetime, timezone, timedelta
from typing import Optional, Union


def _get_timezone():
    """Get configured timezone, defaults to Asia/Shanghai (UTC+8)"""
    tz_name = os.getenv("TIMEZONE", "Asia/Shanghai")
    try:
        # Try to use zoneinfo for timezone (Python 3.9+)
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except ImportError:
        # Fallback for older Python versions or if zoneinfo not available
        # Map common timezone names to offsets
        tz_offsets = {
            "Asia/Shanghai": 8,
            "Asia/Tokyo": 9,
            "Asia/Seoul": 9,
            "Asia/Singapore": 8,
            "Asia/Hong_Kong": 8,
            "Asia/Taipei": 8,
            "America/New_York": -5,
            "America/Los_Angeles": -8,
            "America/Chicago": -6,
            "Europe/London": 0,
            "Europe/Paris": 1,
            "Europe/Berlin": 1,
            "UTC": 0,
        }
        offset_hours = tz_offsets.get(tz_name, 8)  # Default to +8 if not found
        return timezone(timedelta(hours=offset_hours))


# Timezone configuration
# For backwards compatibility, UTC8 is always UTC+8
UTC8 = timezone(timedelta(hours=8))
# TZ is the configurable timezone (defaults to Asia/Shanghai)
TZ = _get_timezone()


def _restore_signal_handler(signum: int, original_handler):
    """Restore original signal handler, handling cross-platform differences"""
    try:
        signal.signal(signum, original_handler)
    except (ValueError, OSError):
        # Signal might not be available on this platform (e.g., Windows)
        pass


def format_price(price: float) -> str:
    """Format price for display"""
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 10:
        return f"${price:.2f}"
    else:
        return f"${price:.4f}"


def get_coin_display_name(symbol: str) -> str:
    """Get display name for coin symbol"""
    return symbol.replace("USDT", "")


def get_coin_emoji(coin_name: str) -> str:
    """Get emoji for coin name"""
    emoji_map = {
        "BTC": "₿",       # Bitcoin
        "ETH": "Ξ",       # Ethereum
        "SOL": "◎",       # Solana
        "USD1": "$1",     # USD1 stablecoin
        "USDT": "₮",      # Tether
        "USDC": "₮",      # USD Coin
        "XRP": "✕",       # Ripple
        "DOGE": "Ð",      # Dogecoin
        "ADA": "₳",       # Cardano
        "DOT": "•",       # Polkadot
        "AVAX": "▲",      # Avalanche
        "MATIC": "⬡",     # Polygon
        "LINK": "⬡",      # Chainlink
        "LTC": "Ł",       # Litecoin
        "BCH": "₿",       # Bitcoin Cash
        "BNB": "🅱️",       # Binance Coin
        "UNI": "🦄",      # Uniswap
        "AAVE": "👻",     # Aave
        "ATOM": "⚛️",      # Cosmos
        "XTZ": "ꜩ",       # Tezos
    }
    return emoji_map.get(coin_name, "🪙")

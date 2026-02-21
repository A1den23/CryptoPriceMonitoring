"""
Utility functions for Crypto Price Monitoring Bot
"""

import os
from datetime import datetime, timezone, timedelta


def get_configured_timezone() -> timezone:
    """Get configured timezone, defaults to Asia/Shanghai (UTC+8)"""
    tz_name = os.getenv("TIMEZONE", "Asia/Shanghai")
    try:
        # Try to use zoneinfo for timezone (Python 3.9+)
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            pass
    except ImportError:
        pass

    # Fallback for older Python versions or invalid timezone names.
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


def now_in_configured_timezone() -> datetime:
    """Get current datetime in configured timezone."""
    return datetime.now(get_configured_timezone())


# Timezone configuration (backwards compatibility)
# Use get_configured_timezone() or now_in_configured_timezone() for runtime values.
TZ = get_configured_timezone()

# UTC+8 timezone constant (backwards compatibility)
# Deprecated: Use TZ or get_configured_timezone() instead
UTC8 = TZ


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

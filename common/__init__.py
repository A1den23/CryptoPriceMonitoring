"""
Common shared module for Crypto Price Monitoring Bot
Contains shared utilities, configuration, and base classes

This module is organized into submodules for better maintainability:
- config: Configuration management (ConfigManager, CoinConfig)
- logging: Logging setup and utilities
- clients: API clients (HTTP, WebSocket)
- notifications: Telegram notification handler
- utils: Utility functions
"""

# Config
from .config import ConfigManager, CoinConfig

# Logging
from .logging import setup_logging, get_logger, logger

# HTTP and WebSocket clients
from .clients.http import BinancePriceFetcher, AsyncBinancePriceFetcher, BinanceAPIError
from .clients.websocket import BinanceWebSocketClient, ConnectionState

# Notifications
from .notifications import TelegramNotifier

# Utils
from .utils import (
    format_price,
    get_coin_emoji,
    get_coin_display_name,
    get_configured_timezone,
    now_in_configured_timezone,
    UTC8,
    TZ,
    _restore_signal_handler,
)

__all__ = [
    # Config
    'ConfigManager',
    'CoinConfig',
    # Logging
    'setup_logging',
    'get_logger',
    'logger',
    # Clients
    'BinancePriceFetcher',
    'AsyncBinancePriceFetcher',
    'BinanceAPIError',
    'BinanceWebSocketClient',
    'ConnectionState',
    # Notifications
    'TelegramNotifier',
    # Utils
    'format_price',
    'get_coin_emoji',
    'get_coin_display_name',
    'get_configured_timezone',
    'now_in_configured_timezone',
    'UTC8',
    'TZ',
    '_restore_signal_handler',
]

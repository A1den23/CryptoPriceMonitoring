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

from importlib import import_module

from .config import CoinConfig, ConfigManager, load_environment
from .logging import get_logger, logger, setup_logging
from .utils import (
    TZ,
    UTC8,
    format_price,
    format_threshold,
    get_coin_display_name,
    get_coin_emoji,
    get_configured_timezone,
    now_in_configured_timezone,
)

__all__ = [
    # Config
    "ConfigManager",
    "CoinConfig",
    "load_environment",
    # Logging
    "setup_logging",
    "logger",
    "get_logger",
    # Clients
    "BinancePriceFetcher",
    "AsyncBinancePriceFetcher",
    "BinanceAPIError",
    "BinanceWebSocketClient",
    "ConnectionState",
    "DefiLlamaClient",
    "StablecoinSnapshot",
    # Notifications
    "TelegramNotifier",
    # Utils
    "format_price",
    "format_threshold",
    "get_coin_emoji",
    "get_coin_display_name",
    "get_configured_timezone",
    "now_in_configured_timezone",
    "TZ",
    "UTC8",
]

_LAZY_EXPORTS = {
    "BinancePriceFetcher": (".clients.http", "BinancePriceFetcher"),
    "AsyncBinancePriceFetcher": (".clients.http", "AsyncBinancePriceFetcher"),
    "BinanceAPIError": (".clients.http", "BinanceAPIError"),
    "BinanceWebSocketClient": (".clients.websocket", "BinanceWebSocketClient"),
    "ConnectionState": (".clients.websocket", "ConnectionState"),
    "DefiLlamaClient": (".clients.defillama", "DefiLlamaClient"),
    "StablecoinSnapshot": (".clients.defillama", "StablecoinSnapshot"),
    "TelegramNotifier": (".notifications", "TelegramNotifier"),
}


def __getattr__(name: str):
    """Lazily import heavyweight public exports so package import stays lightweight."""
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

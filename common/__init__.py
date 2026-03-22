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

_EXPORTS = {
    # Config
    "ConfigManager": (".config", "ConfigManager"),
    "CoinConfig": (".config", "CoinConfig"),
    "load_environment": (".config", "load_environment"),
    # Logging
    "setup_logging": (".logging", "setup_logging"),
    "logger": (".logging", "logger"),
    "get_logger": (".logging", "get_logger"),
    # Clients
    "BinancePriceFetcher": (".clients.http", "BinancePriceFetcher"),
    "AsyncBinancePriceFetcher": (".clients.http", "AsyncBinancePriceFetcher"),
    "BinanceAPIError": (".clients.http", "BinanceAPIError"),
    "BinanceWebSocketClient": (".clients.websocket", "BinanceWebSocketClient"),
    "ConnectionState": (".clients.websocket", "ConnectionState"),
    "DefiLlamaClient": (".clients.defillama", "DefiLlamaClient"),
    "StablecoinSnapshot": (".clients.defillama", "StablecoinSnapshot"),
    # Notifications
    "TelegramNotifier": (".notifications", "TelegramNotifier"),
    # Utils
    "format_price": (".utils", "format_price"),
    "format_threshold": (".utils", "format_threshold"),
    "get_coin_emoji": (".utils", "get_coin_emoji"),
    "get_coin_display_name": (".utils", "get_coin_display_name"),
    "get_configured_timezone": (".utils", "get_configured_timezone"),
    "now_in_configured_timezone": (".utils", "now_in_configured_timezone"),
    "TZ": (".utils", "TZ"),
    "UTC8": (".utils", "UTC8"),
}


def __getattr__(name: str):
    """Lazily import public exports so package import stays lightweight."""
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

"""
API Clients for Crypto Price Monitoring Bot
"""

from importlib import import_module

__all__ = [
    "BinancePriceFetcher",
    "AsyncBinancePriceFetcher",
    "BinanceAPIError",
    "BinanceWebSocketClient",
    "ConnectionState",
]

_EXPORTS = {
    "BinancePriceFetcher": (".http", "BinancePriceFetcher"),
    "AsyncBinancePriceFetcher": (".http", "AsyncBinancePriceFetcher"),
    "BinanceAPIError": (".http", "BinanceAPIError"),
    "BinanceWebSocketClient": (".websocket", "BinanceWebSocketClient"),
    "ConnectionState": (".websocket", "ConnectionState"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

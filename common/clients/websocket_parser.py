"""Pure payload parsers for Binance WebSocket messages."""

import re


VALID_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]+$")


def parse_ticker_message(data: dict) -> tuple[str, float]:
    """Parse Binance ticker message payload."""
    if "stream" in data and "data" in data:
        symbol = data["data"]["s"]
        price = float(data["data"]["c"])
    else:
        symbol = data["s"]
        price = float(data["c"])

    return symbol, price


def parse_kline_message(data: dict) -> tuple[str, float, float, bool] | None:
    """Parse Binance kline message payload."""
    event = data["data"] if "stream" in data and "data" in data else data
    if event.get("e") != "kline":
        return None

    kline = event["k"]
    symbol = kline.get("s") or event.get("s")
    if not isinstance(symbol, str) or not VALID_SYMBOL_PATTERN.match(symbol):
        raise ValueError("Kline message missing valid symbol")

    return (
        symbol,
        float(kline["c"]),
        float(kline["v"]),
        bool(kline["x"]),
    )

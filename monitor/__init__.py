#!/usr/bin/env python3
"""
Crypto Price Monitoring Bot with WebSocket Support
Monitors multiple cryptocurrency prices in real-time via WebSocket and sends alerts via Telegram

Usage:
    python monitor.py              # Run monitoring with WebSocket (default)
    python monitor.py --test       # Test volatility alerts
    python monitor.py --status     # Show current status
"""

import asyncio
from importlib import import_module
import sys

from common import (
    ConfigManager,
    format_price,
    format_threshold,
    get_coin_display_name,
    get_coin_emoji,
    load_environment,
    logger,
    now_in_configured_timezone,
    setup_logging,
)

__all__ = [
    "BinanceWebSocketClient",
    "ConfigManager",
    "PriceMonitor",
    "StablecoinDepegMonitor",
    "TelegramNotifier",
    "WebSocketMultiCoinMonitor",
    "asyncio",
    "format_price",
    "format_threshold",
    "get_coin_display_name",
    "get_coin_emoji",
    "load_environment",
    "logger",
    "main",
    "now_in_configured_timezone",
    "price_monitor",
    "setup_logging",
    "show_status",
    "test_volatility_alert",
]

_LAZY_EXPORTS = {
    "BinancePriceFetcher": ("common", "BinancePriceFetcher"),
    "BinanceWebSocketClient": ("common", "BinanceWebSocketClient"),
    "PriceMonitor": (".price_monitor", "PriceMonitor"),
    "StablecoinDepegMonitor": (".stablecoin_depeg_monitor", "StablecoinDepegMonitor"),
    "TelegramNotifier": ("common", "TelegramNotifier"),
    "WebSocketMultiCoinMonitor": (".ws_monitor", "WebSocketMultiCoinMonitor"),
    "price_monitor": (".price_monitor", None),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    if module_name.startswith("."):
        module = import_module(module_name, __name__)
    else:
        module = import_module(module_name)
    value = module if attr_name is None else getattr(module, attr_name)
    globals()[name] = value
    return value


def _resolve_export(name: str):
    try:
        return globals()[name]
    except KeyError:
        return __getattr__(name)


def test_volatility_alert():
    """Test volatility monitoring by sending a test alert."""
    BinancePriceFetcher = _resolve_export("BinancePriceFetcher")
    ConfigManager = _resolve_export("ConfigManager")
    TelegramNotifier = _resolve_export("TelegramNotifier")
    format_price = _resolve_export("format_price")
    get_coin_display_name = _resolve_export("get_coin_display_name")
    now_in_configured_timezone = _resolve_export("now_in_configured_timezone")
    logger = _resolve_export("logger")

    print("\n=== 测试波动监控 ===\n")

    config = ConfigManager()
    notifier = TelegramNotifier()
    enabled_coins = config.get_enabled_coins()

    with BinancePriceFetcher() as fetcher:
        for coin_config in enabled_coins:
            try:
                price = fetcher.get_current_price(coin_config.symbol)
                if price:
                    fake_high_price = price * 1.05
                    fake_low_price = price * 0.98
                    fake_volatility = ((fake_high_price - fake_low_price) / fake_low_price) * 100

                    coin = get_coin_display_name(coin_config.symbol)

                    print(f"测试 {coin}...")
                    print(f"  当前价格: {format_price(price)}")
                    print(f"  波动阈值: {coin_config.volatility_percent}%")
                    print(f"  模拟波动: {fake_volatility:.2f}%")

                    message = (
                        f"🧪 <b>测试告警 - 波动监控</b>\n"
                        f"🪙 {coin_config.symbol}\n"
                        f"💰 当前价格: {format_price(price)}\n"
                        f"📊 告警阈值: {coin_config.volatility_percent}% / {coin_config.volatility_window}秒\n"
                        f"✅ 波动监控已激活\n"
                        f"📈 模拟告警: {fake_volatility:.2f}% 将触发告警!\n"
                        f"⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    notifier.send_message(message)
                    print("  ✓ 测试告警已发送!\n")
            except Exception as e:
                logger.error(f"Error while testing {coin_config.coin_name}: {e}")

    print("测试完成! 请检查 Telegram 中的测试告警。\n")


def show_status():
    """Show current monitoring status."""
    BinancePriceFetcher = _resolve_export("BinancePriceFetcher")
    ConfigManager = _resolve_export("ConfigManager")
    format_price = _resolve_export("format_price")
    format_threshold = _resolve_export("format_threshold")
    get_coin_emoji = _resolve_export("get_coin_emoji")
    logger = _resolve_export("logger")

    print("\n" + "=" * 60)
    print("加密货币价格监控状态")
    print("=" * 60 + "\n")

    config = ConfigManager()
    enabled_coins = config.get_enabled_coins()

    with BinancePriceFetcher() as fetcher:
        for coin_config in enabled_coins:
            try:
                price = fetcher.get_current_price(coin_config.symbol)
                if price:
                    threshold_str = format_threshold(coin_config.integer_threshold)
                    emoji = get_coin_emoji(coin_config.coin_name)

                    print(f"{emoji} 🪙 {coin_config.coin_name}")
                    print(f"   交易对: {coin_config.symbol}")
                    print(f"   当前价格: {format_price(price)}")
                    print(f"   里程碑阈值: 每 {threshold_str}")
                    print(f"   波动告警: {coin_config.volatility_percent}% / {coin_config.volatility_window}秒")
                    print()
            except Exception as e:
                logger.error(f"Error getting status for {coin_config.coin_name}: {e}")

    print("=" * 60 + "\n")


def main():
    """Main entry point."""
    load_environment = _resolve_export("load_environment")
    setup_logging = _resolve_export("setup_logging")
    ConfigManager = _resolve_export("ConfigManager")
    WebSocketMultiCoinMonitor = _resolve_export("WebSocketMultiCoinMonitor")
    logger = _resolve_export("logger")

    load_environment()
    setup_logging()

    config = ConfigManager()

    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "--test":
            test_volatility_alert()
            return
        if arg == "--status":
            show_status()
            return
        if arg in ["--help", "-h"]:
            print(__doc__)
            return

    try:
        ws_monitor = WebSocketMultiCoinMonitor(config)
        asyncio.run(ws_monitor.run())
    except KeyboardInterrupt:
        logger.info("\nGraceful shutdown in progress...")

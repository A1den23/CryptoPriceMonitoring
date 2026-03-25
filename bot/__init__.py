#!/usr/bin/env python3
"""
Telegram Interactive Bot
Provides interactive commands and buttons to query cryptocurrency prices
"""

import asyncio
from importlib import import_module
import signal

from common import (
    ConfigManager,
    load_environment,
    logger,
    now_in_configured_timezone,
    setup_logging,
)

__all__ = [
    "ConfigManager",
    "TelegramBot",
    "TelegramNotifier",
    "asyncio",
    "load_environment",
    "logger",
    "main",
    "now_in_configured_timezone",
    "setup_logging",
    "signal",
]

_LAZY_EXPORTS = {
    "TelegramBot": (".app", "TelegramBot"),
    "TelegramNotifier": ("common", "TelegramNotifier"),
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
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def _resolve_export(name: str):
    try:
        return globals()[name]
    except KeyError:
        return __getattr__(name)


def main():
    """Main entry point."""
    load_environment = _resolve_export("load_environment")
    setup_logging = _resolve_export("setup_logging")
    ConfigManager = _resolve_export("ConfigManager")
    TelegramNotifier = _resolve_export("TelegramNotifier")
    TelegramBot = _resolve_export("TelegramBot")
    now_in_configured_timezone = _resolve_export("now_in_configured_timezone")
    logger = _resolve_export("logger")

    load_environment()
    setup_logging(log_file="logs/bot.log")

    config = ConfigManager()
    notifier = TelegramNotifier()

    try:
        bot = TelegramBot(config)

        startup_message = (
            "🤖 <b>Telegram 交互机器人已启动</b>\n\n"
            "✅ 机器人已上线并可正常使用！\n\n"
            "💬 <b>可用命令：</b>\n"
            "/start - 显示欢迎菜单\n"
            "/price [coin] - 查询指定币种价格\n"
            "/stablecoins - 查看前25稳定币价格\n"
            "/status - 查看详细状态\n"
            "/all - 查看全部价格\n"
            "/help - 查看帮助信息\n\n"
            f"⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        try:
            notifier.send_message(startup_message)
            logger.info("Startup notification sent")
        except Exception:
            logger.exception("Startup notification failed")

        bot.run()

        if bot._shutdown_event.is_set():
            logger.info("Bot stopped via signal")
            shutdown_message = (
                "👋 <b>Telegram 交互机器人已停止</b>\n\n"
                "机器人已完成优雅关闭。\n\n"
                f"⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            try:
                notifier.send_message(shutdown_message)
                logger.info("Shutdown notification sent")
            except Exception:
                logger.exception("Shutdown notification failed")

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        print("\nPlease make sure TELEGRAM_BOT_TOKEN is set in your .env file")
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt)")

#!/usr/bin/env python3
"""
Telegram Interactive Bot
Provides interactive commands and buttons to query cryptocurrency prices
"""

import asyncio
import signal

from common import (
    setup_logging,
    ConfigManager,
    load_environment,
    TelegramNotifier,
    now_in_configured_timezone,
    logger,
)

from .app import TelegramBot

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


def main():
    """Main entry point."""
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

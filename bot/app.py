"""
Telegram bot application lifecycle and shared helpers.
"""

import asyncio
import math
import os
from datetime import datetime
from pathlib import Path

from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from common.clients.defillama import DefiLlamaClient
from common.clients.http import AsyncBinancePriceFetcher
from common.config import CoinConfig, ConfigManager
from common.logging import logger
from common.notifications import TelegramNotifier
from common.runtime import HeartbeatWriter, SignalHandlerRegistry, SignalHandlingMixin
from common.utils import format_threshold, now_in_configured_timezone

from . import handlers, messages


class TelegramBot(SignalHandlingMixin):
    """Telegram Interactive Bot with shared common module."""

    BOT_CONNECTION_POOL_SIZE = 8
    BOT_POOL_TIMEOUT_SECONDS = 30.0
    GET_UPDATES_CONNECTION_POOL_SIZE = 2
    GET_UPDATES_POOL_TIMEOUT_SECONDS = 30.0
    BOT_COMMAND_MENU = (
        ("start", "显示欢迎菜单和快捷按钮"),
        ("help", "查看帮助说明"),
        ("price", "查询指定币种价格"),
        ("stablecoins", "查看前25稳定币价格"),
        ("status", "查看所有监控币种状态"),
        ("all", "查看所有已启用币种价格"),
    )

    def __init__(self, config: ConfigManager):
        self.config = config

        if not self.config.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")

        self.fetcher: AsyncBinancePriceFetcher | None = None
        self._defillama_client: DefiLlamaClient | None = None
        self.notifier = TelegramNotifier(
            bot_token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
        )

        self.application = (
            Application.builder()
            .token(self.config.telegram_bot_token)
            .connection_pool_size(self.BOT_CONNECTION_POOL_SIZE)
            .pool_timeout(self.BOT_POOL_TIMEOUT_SECONDS)
            .get_updates_connection_pool_size(self.GET_UPDATES_CONNECTION_POOL_SIZE)
            .get_updates_pool_timeout(self.GET_UPDATES_POOL_TIMEOUT_SECONDS)
            .build()
        )

        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("price", self.price_command))
        self.application.add_handler(CommandHandler("stablecoins", self.stablecoins_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("all", self.all_prices_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.command_router = handlers.BotCommandRouter(self)

        self._shutdown_event = asyncio.Event()
        self._signal_registry = SignalHandlerRegistry()
        self._signal_handlers_registered = False

        self.start_time: datetime | None = None
        self._heartbeat_file = Path(self.config.bot_heartbeat_file)
        self._heartbeat_interval = self.config.bot_heartbeat_interval_seconds
        self._heartbeat_writer = HeartbeatWriter(self._heartbeat_file, label="Bot")

        logger.info("Telegram Bot 初始化成功")

    async def _register_bot_command_menu(self) -> None:
        """Register commands shown by Telegram clients when users type '/'."""
        await self.application.bot.set_my_commands(
            [BotCommand(command, description) for command, description in self.BOT_COMMAND_MENU]
        )

    def _touch_heartbeat(self) -> None:
        """Touch heartbeat file to indicate bot event loop is alive."""
        self._heartbeat_writer.touch()

    async def _heartbeat_loop(self):
        """Periodically refresh heartbeat file while event loop is healthy."""
        while not self._shutdown_event.is_set():
            self._touch_heartbeat()
            await asyncio.sleep(self._heartbeat_interval)

    async def _get_price(self, symbol: str) -> float | None:
        """Fetch price using async HTTP client."""
        if not self.fetcher:
            logger.error("异步价格获取器未初始化")
            return None
        try:
            return await self.fetcher.get_current_price(symbol)
        except Exception:
            logger.exception(f"获取 {symbol} 价格失败")
            return None

    async def _get_prices(self, symbols: list[str]) -> dict[str, float | None]:
        """Fetch multiple prices concurrently using async HTTP client."""
        if not self.fetcher:
            logger.error("异步价格获取器未初始化")
            return {symbol: None for symbol in symbols}
        try:
            return await self.fetcher.get_multiple_prices(symbols)
        except Exception:
            logger.exception("获取多个价格失败")
            return {symbol: None for symbol in symbols}

    def _format_uptime(self) -> str:
        """Format uptime duration."""
        if not self.start_time:
            return "Unknown"

        now = now_in_configured_timezone()
        uptime_seconds = (now - self.start_time).total_seconds()

        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        seconds = int(uptime_seconds % 60)

        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    @staticmethod
    def _chunk_buttons(
        buttons: list[InlineKeyboardButton],
        row_size: int = 2,
    ) -> list[list[InlineKeyboardButton]]:
        """Group buttons into rows of a fixed size."""
        return [buttons[i:i + row_size] for i in range(0, len(buttons), row_size)]

    @staticmethod
    def _format_timestamp() -> str:
        """Format the current configured timestamp consistently."""
        return now_in_configured_timezone().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _format_threshold(coin_config: CoinConfig) -> str:
        """Format milestone threshold for display."""
        return format_threshold(coin_config.integer_threshold)

    async def _send_or_edit_message(
        self,
        chat_id,
        text: str,
        message=None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Edit an existing message or send a new one."""
        if message:
            await message.edit_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
            return

        await self.application.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_notification=False,
        )

    def _build_coin_button_rows(
        self,
        exclude_coin: str | None = None,
    ) -> list[list[InlineKeyboardButton]]:
        """Build rows of enabled coin buttons."""
        return messages.build_coin_button_rows(
            self.config.get_enabled_coins(),
            exclude_coin=exclude_coin,
        )

    def _build_start_keyboard(self) -> InlineKeyboardMarkup:
        """Build the keyboard shown on /start."""
        return messages.build_start_keyboard(self.config.get_enabled_coins())

    def _build_price_keyboard(self, coin_name: str) -> InlineKeyboardMarkup:
        """Build the keyboard shown for a specific coin price update."""
        return messages.build_price_keyboard(coin_name, self.config.get_enabled_coins())

    def _render_all_prices_message(
        self,
        enabled_coins: list[CoinConfig],
        prices: dict[str, float | None],
    ) -> str:
        """Render the shared all-prices message body."""
        return messages.render_all_prices_message(
            enabled_coins=enabled_coins,
            prices=prices,
            timestamp=self._format_timestamp(),
        )

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        await self.command_router.start_command(update, context)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        await self.command_router.help_command(update, context)

    async def price_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /price command with input validation."""
        await self.command_router.price_command(update, context)

    async def stablecoins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stablecoins command."""
        await self.command_router.stablecoins_command(update, context)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        await self.command_router.status_command(update, context)

    async def all_prices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /all command."""
        await self.command_router.all_prices_command(update, context)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks."""
        await self.command_router.button_callback(update, context)

    async def send_price_update(self, chat_id, coin_name, message=None):
        """Send a single-coin price update."""
        await self.command_router.send_price_update(chat_id, coin_name, message=message)

    async def run_async(self):
        """Start the bot asynchronously."""
        logger.info("正在启动 Telegram Bot 轮询...")
        heartbeat_task = None
        initialized = False
        started = False
        polling_started = False

        try:
            self._setup_signal_handlers()
            self._touch_heartbeat()
            self.start_time = now_in_configured_timezone()
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            self._defillama_client = DefiLlamaClient()

            async with AsyncBinancePriceFetcher() as fetcher:
                self.fetcher = fetcher
                await self.application.initialize()
                initialized = True
                await self._register_bot_command_menu()
                await self.application.start()
                started = True
                await self.application.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES,
                )
                polling_started = True
                await self._shutdown_event.wait()
        finally:
            logger.info("正在停止 Telegram Bot...")
            try:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass

                updater = getattr(self.application, "updater", None)
                if polling_started or getattr(updater, "running", False):
                    await updater.stop()
                if started or getattr(self.application, "running", False):
                    await self.application.stop()
                if initialized or getattr(self.application, "initialized", False):
                    await self.application.shutdown()
            finally:
                self._restore_signal_handlers()
                if self._defillama_client is not None:
                    await self._defillama_client.close()
                    self._defillama_client = None
                self.notifier.close()

    def run(self):
        """Start the bot (synchronous wrapper)."""
        logger.info("正在启动 Telegram Bot 轮询...")
        asyncio.run(self.run_async())

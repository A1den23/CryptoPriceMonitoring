"""
Telegram bot application lifecycle and shared helpers.
"""

import asyncio
import math
import os
import signal
from datetime import datetime
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from common import (
    AsyncBinancePriceFetcher,
    CoinConfig,
    ConfigManager,
    TelegramNotifier,
    format_threshold,
    now_in_configured_timezone,
    logger,
)

from . import handlers, messages


class TelegramBot:
    """Telegram Interactive Bot with shared common module."""

    BOT_CONNECTION_POOL_SIZE = 8
    BOT_POOL_TIMEOUT_SECONDS = 30.0
    GET_UPDATES_CONNECTION_POOL_SIZE = 2
    GET_UPDATES_POOL_TIMEOUT_SECONDS = 30.0

    def __init__(self, config: ConfigManager):
        self.config = config

        if not self.config.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")

        self.fetcher: AsyncBinancePriceFetcher | None = None
        self.notifier = TelegramNotifier()

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
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("all", self.all_prices_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

        self._shutdown_event = asyncio.Event()
        self._setup_signal_handlers()

        self.start_time: datetime | None = None
        self._heartbeat_file = Path(os.getenv("BOT_HEARTBEAT_FILE", "/tmp/bot_heartbeat"))
        self._heartbeat_interval = self._parse_heartbeat_interval()

        logger.info("Telegram Bot initialized successfully")

    @staticmethod
    def _parse_heartbeat_interval() -> float:
        """Parse heartbeat interval from environment."""
        try:
            heartbeat_interval = float(os.getenv("BOT_HEARTBEAT_INTERVAL_SECONDS", "30"))
            if math.isfinite(heartbeat_interval) and heartbeat_interval > 0:
                return heartbeat_interval
        except (TypeError, ValueError):
            pass
        return 30.0

    def _touch_heartbeat(self) -> None:
        """Touch heartbeat file to indicate bot event loop is alive."""
        try:
            self._heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
            self._heartbeat_file.touch()
        except OSError as e:
            logger.warning(f"Failed to update bot heartbeat file '{self._heartbeat_file}': {e}")

    async def _heartbeat_loop(self):
        """Periodically refresh heartbeat file while event loop is healthy."""
        while not self._shutdown_event.is_set():
            self._touch_heartbeat()
            await asyncio.sleep(self._heartbeat_interval)

    async def _get_price(self, symbol: str) -> float | None:
        """Fetch price using async HTTP client."""
        if not self.fetcher:
            logger.error("Async fetcher not initialized")
            return None
        try:
            return await self.fetcher.get_current_price(symbol)
        except Exception:
            logger.exception(f"Error fetching price for {symbol}")
            return None

    async def _get_prices(self, symbols: list[str]) -> dict[str, float | None]:
        """Fetch multiple prices concurrently using async HTTP client."""
        if not self.fetcher:
            logger.error("Async fetcher not initialized")
            return {symbol: None for symbol in symbols}
        try:
            return await self.fetcher.get_multiple_prices(symbols)
        except Exception:
            logger.exception("Error fetching multiple prices")
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

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)
        logger.debug("Signal handlers registered (bot)")

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals (SIGINT, SIGTERM)."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received signal {sig_name} ({signum}), initiating graceful shutdown...")

        original_handler = self._original_sigint if signum == signal.SIGINT else self._original_sigterm
        self._restore_signal_handler(signum, original_handler)
        self._shutdown_event.set()

    @staticmethod
    def _restore_signal_handler(signum: int, original_handler) -> None:
        """Restore original signal handler, handling cross-platform differences."""
        try:
            signal.signal(signum, original_handler)
        except (ValueError, OSError):
            pass

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

    async def run_async(self):
        """Start the bot asynchronously."""
        logger.info("Starting Telegram Bot polling...")
        self._touch_heartbeat()

        self.start_time = now_in_configured_timezone()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        async with AsyncBinancePriceFetcher() as fetcher:
            self.fetcher = fetcher
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
            )

            try:
                await self._shutdown_event.wait()
            finally:
                logger.info("Stopping Telegram Bot...")
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()

    def run(self):
        """Start the bot (synchronous wrapper)."""
        logger.info("Starting Telegram Bot polling...")
        asyncio.run(self.run_async())


TelegramBot._build_coin_button_rows = messages._build_coin_button_rows
TelegramBot._build_start_keyboard = messages._build_start_keyboard
TelegramBot._build_price_keyboard = messages._build_price_keyboard
TelegramBot._render_all_prices_message = messages._render_all_prices_message

TelegramBot.start_command = handlers.start_command
TelegramBot.help_command = handlers.help_command
TelegramBot.price_command = handlers.price_command
TelegramBot.status_command = handlers.status_command
TelegramBot.all_prices_command = handlers.all_prices_command
TelegramBot.button_callback = handlers.button_callback
TelegramBot.send_price_update = handlers.send_price_update

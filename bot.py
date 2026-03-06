#!/usr/bin/env python3
"""
Telegram Interactive Bot
Provides interactive commands and buttons to query cryptocurrency prices
"""

import asyncio
import math
import os
import re
import signal
from datetime import datetime
from pathlib import Path
from difflib import get_close_matches

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from common import (
    setup_logging,
    ConfigManager,
    CoinConfig,
    load_environment,
    AsyncBinancePriceFetcher,
    TelegramNotifier,
    format_price,
    format_threshold,
    get_coin_emoji,
    now_in_configured_timezone,
    logger
)


class TelegramBot:
    """Telegram Interactive Bot with shared common module"""

    def __init__(self, config: ConfigManager):
        self.config = config

        if not self.config.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")

        self.fetcher: AsyncBinancePriceFetcher | None = None
        self.notifier = TelegramNotifier()

        # Create application
        self.application = Application.builder().token(self.config.telegram_bot_token).build()

        # Register handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("price", self.price_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("all", self.all_prices_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

        # Shutdown handling
        self._shutdown_event = asyncio.Event()
        self._setup_signal_handlers()

        # Track start time
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
        """Format uptime duration"""
        if not self.start_time:
            return "Unknown"

        now = now_in_configured_timezone()
        uptime_seconds = (now - self.start_time).total_seconds()

        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        seconds = int(uptime_seconds % 60)

        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)
        logger.debug("Signal handlers registered (bot)")

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals (SIGINT, SIGTERM)."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received signal {sig_name} ({signum}), initiating graceful shutdown...")

        # Restore original signal handler FIRST to prevent race condition
        # This ensures a second signal triggers immediate termination
        original_handler = self._original_sigint if signum == signal.SIGINT else self._original_sigterm
        self._restore_signal_handler(signum, original_handler)

        # Set the shutdown event to stop the bot (thread-safe)
        self._shutdown_event.set()

    @staticmethod
    def _restore_signal_handler(signum: int, original_handler) -> None:
        """Restore original signal handler, handling cross-platform differences."""
        try:
            signal.signal(signum, original_handler)
        except (ValueError, OSError):
            # Signal might not be available on this platform (e.g., Windows)
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

    def _build_coin_button_rows(
        self,
        exclude_coin: str | None = None,
    ) -> list[list[InlineKeyboardButton]]:
        """Build rows of enabled coin buttons."""
        buttons: list[InlineKeyboardButton] = []
        for coin_config in self.config.get_enabled_coins():
            if coin_config.coin_name == exclude_coin:
                continue
            emoji = get_coin_emoji(coin_config.coin_name)
            buttons.append(
                InlineKeyboardButton(
                    f"{emoji} {coin_config.coin_name}",
                    callback_data=f"price_{coin_config.coin_name}",
                )
            )
        return self._chunk_buttons(buttons)

    def _build_start_keyboard(self) -> InlineKeyboardMarkup:
        """Build the keyboard shown on /start."""
        keyboard = [[InlineKeyboardButton("📊 All Prices", callback_data="all_prices")]]
        keyboard.extend(self._build_coin_button_rows())
        return InlineKeyboardMarkup(keyboard)

    def _build_price_keyboard(self, coin_name: str) -> InlineKeyboardMarkup:
        """Build the keyboard shown for a specific coin price update."""
        keyboard = [
            [InlineKeyboardButton(f"🔄 Refresh {coin_name}", callback_data=f"price_{coin_name}")],
            [InlineKeyboardButton("📊 All Prices", callback_data="all_prices")],
        ]
        keyboard.extend(self._build_coin_button_rows(exclude_coin=coin_name))
        return InlineKeyboardMarkup(keyboard)

    def _render_all_prices_message(
        self,
        enabled_coins: list[CoinConfig],
        prices: dict[str, float | None],
    ) -> str:
        """Render the shared all-prices message body."""
        message = "💰 <b>Current Prices</b>\n\n"
        if not enabled_coins:
            return f"{message}❌ No coins are currently enabled!\n\n⏱️ {self._format_timestamp()}"

        for coin_config in enabled_coins:
            price = prices.get(coin_config.symbol)
            if price is not None:
                emoji = get_coin_emoji(coin_config.coin_name)
                message += f"{emoji} <b>{coin_config.coin_name}</b>: {format_price(price)}\n"
            else:
                message += f"❌ <b>{coin_config.coin_name}</b>: Failed to fetch\n"

        return f"{message}\n⏱️ {self._format_timestamp()}"

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = (
            "🤖 <b>Crypto Price Monitor Bot</b>\n\n"
            "Welcome! I can help you monitor cryptocurrency prices.\n\n"
            "📋 <b>Available Commands:</b>\n"
            "/price - Get price for a specific coin\n"
            "/status - Show status of all monitored coins\n"
            "/all - Get prices of all enabled coins\n"
            "/help - Show this help message\n\n"
            "Or click the buttons below for quick access! 👇"
        )

        await update.message.reply_text(
            welcome_message,
            parse_mode="HTML",
            reply_markup=self._build_start_keyboard(),
            disable_notification=False
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = (
            "📖 <b>Help & Commands</b>\n\n"
            "<b>Commands:</b>\n"
            "/price [coin] - Get price for specific coin\n"
            "  Example: /price BTC\n"
            "/status - Show detailed status of all coins\n"
            "/all - Quick view of all enabled coin prices\n"
            "/start - Show welcome menu with buttons\n\n"
            "<b>Buttons:</b>\n"
            "Click any button to instantly get the latest price!\n\n"
            "<b>Monitored Coins:</b>\n"
        )

        # Add enabled coins
        enabled_coins = self.config.get_enabled_coins()
        for coin_config in enabled_coins:
            help_message += f"  • {coin_config.coin_name}: {coin_config.symbol}\n"

        await update.message.reply_text(help_message, parse_mode="HTML", disable_notification=False)

    async def price_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /price command with input validation"""
        MAX_ARG_LENGTH = 20
        ALLOWED_PATTERN = re.compile(r'^[A-Z0-9]+$')

        # Get coin from argument
        if context.args and len(context.args) > 0:
            coin = context.args[0]

            # Validate input length
            if len(coin) > MAX_ARG_LENGTH:
                await update.message.reply_text(
                    "❌ Coin name too long.\n"
                    f"Maximum length is {MAX_ARG_LENGTH} characters.",
                    parse_mode="HTML",
                    disable_notification=False
                )
                return

            # Sanitize - only allow alphanumeric characters
            coin = coin.upper().strip()
            if not ALLOWED_PATTERN.match(coin):
                await update.message.reply_text(
                    "❌ Invalid coin name format.\n"
                    "Only letters and numbers are allowed.\n\n"
                    f"Available coins: {', '.join(self.config.coin_names)}",
                    parse_mode="HTML",
                    disable_notification=False
                )
                return
        else:
            await update.message.reply_text(
                "❌ Please specify a coin.\n"
                "Example: /price BTC\n\n"
                f"Available coins: {', '.join(self.config.coin_names)}",
                parse_mode="HTML",
                disable_notification=False
            )
            return

        # Check if coin is valid
        if coin not in self.config.coin_names:
            # Get fuzzy match suggestions
            suggestions = get_close_matches(coin, self.config.coin_names, n=1, cutoff=0.5)
            suggestion_text = f"\n\nDid you mean: <b>{suggestions[0]}</b>?" if suggestions else ""

            await update.message.reply_text(
                f"❌ Unknown coin: {coin}{suggestion_text}\n\n"
                f"Available coins: {', '.join(self.config.coin_names)}",
                parse_mode="HTML",
                disable_notification=False
            )
            return

        # Get price
        await self.send_price_update(update.effective_chat.id, coin)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - show detailed status"""
        status_message = "📊 <b>Crypto Monitor Status</b>\n\n"

        enabled_coins = self.config.get_enabled_coins()
        if not enabled_coins:
            status_message += "❌ No coins are currently enabled!"
            status_message += f"\n\n⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"
            await update.message.reply_text(status_message, parse_mode="HTML", disable_notification=False)
            return

        prices = await self._get_prices([c.symbol for c in enabled_coins])

        for coin_config in enabled_coins:
            price = prices.get(coin_config.symbol)
            if price is None:
                status_message += f"❌ <b>{coin_config.coin_name}</b>: Error fetching data\n\n"
                continue

            emoji = get_coin_emoji(coin_config.coin_name)

            status_message += (
                f"{emoji} <b>{coin_config.coin_name}</b> ({coin_config.symbol})\n"
                f"   💰 Price: {format_price(price)}\n"
                f"   📍 Milestone: every {self._format_threshold(coin_config)}\n"
                f"   📊 Volatility Alert: {coin_config.volatility_percent}%/{coin_config.volatility_window}s\n\n"
            )

        # Add uptime and timestamp
        uptime = self._format_uptime()
        status_message += f"\n⌛ Uptime: {uptime}"
        status_message += f"\n⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"

        await update.message.reply_text(status_message, parse_mode="HTML", disable_notification=False)

    async def all_prices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /all command - show all prices"""
        enabled_coins = self.config.get_enabled_coins()
        prices = await self._get_prices([c.symbol for c in enabled_coins])
        message = self._render_all_prices_message(enabled_coins, prices)
        await update.message.reply_text(message, parse_mode="HTML", disable_notification=False)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks"""
        query = update.callback_query
        await query.answer()

        callback_data = query.data

        if callback_data == "all_prices":
            # Send all prices as new message
            enabled_coins = self.config.get_enabled_coins()
            prices = await self._get_prices([c.symbol for c in enabled_coins])
            message = self._render_all_prices_message(enabled_coins, prices)

            # Send new message instead of editing
            await query.message.reply_text(text=message, parse_mode="HTML", disable_notification=False)

        elif callback_data.startswith("price_"):
            # Get specific coin price - send as new message
            coin = callback_data.split("_")[1]
            await self.send_price_update(query.message.chat_id, coin, message=None)

    async def send_price_update(self, chat_id, coin_name, message=None):
        """Send price update for a specific coin"""
        coin_config = self.config.get_coin_config(coin_name)

        if not coin_config:
            await self._send_or_edit_message(
                chat_id,
                f"❌ {coin_name} is not configured.",
                message=message,
            )
            return

        if not coin_config.enabled:
            await self._send_or_edit_message(
                chat_id,
                f"❌ {coin_name} is not enabled in configuration.",
                message=message,
            )
            return

        try:
            price = await self._get_price(coin_config.symbol)

            if price is not None:
                emoji = get_coin_emoji(coin_name)

                response = (
                    f"{emoji} <b>{coin_name}</b> Price Update\n"
                    f"💰 Current: {format_price(price)}\n"
                    f"📈 Symbol: {coin_config.symbol}\n"
                    f"⏱️ {self._format_timestamp()}"
                )
                await self._send_or_edit_message(
                    chat_id,
                    response,
                    message=message,
                    reply_markup=self._build_price_keyboard(coin_name),
                )
            else:
                await self._send_or_edit_message(
                    chat_id,
                    f"❌ Failed to fetch price for {coin_name}",
                    message=message,
                )
        except Exception as e:
            logger.error(f"Error sending price update for {coin_name}: {e}")
            await self._send_or_edit_message(
                chat_id,
                f"❌ Error fetching price for {coin_name}",
                message=message,
            )

    async def run_async(self):
        """Start the bot asynchronously"""
        logger.info("Starting Telegram Bot polling...")
        self._touch_heartbeat()

        # Record start time
        self.start_time = now_in_configured_timezone()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        async with AsyncBinancePriceFetcher() as fetcher:
            self.fetcher = fetcher
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES
            )

            try:
                # Keep running until shutdown is requested (using event for efficiency)
                await self._shutdown_event.wait()
            finally:
                # Graceful shutdown
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
        """Start the bot (synchronous wrapper)"""
        logger.info("Starting Telegram Bot polling...")
        asyncio.run(self.run_async())


def main():
    """Main entry point"""
    load_environment()
    setup_logging(log_file="logs/bot.log")

    # Load configuration
    config = ConfigManager()
    notifier = TelegramNotifier()

    try:
        bot = TelegramBot(config)

        # Send startup notification
        startup_message = (
            "🤖 <b>Telegram Interactive Bot Started</b>\n\n"
            "✅ Bot is now active and ready to serve!\n\n"
            "💬 <b>Available Commands:</b>\n"
            "/start - Show welcome menu\n"
            "/price [coin] - Get specific coin price\n"
            "/status - Show detailed status\n"
            "/all - Get all prices\n"
            "/help - Show help message\n\n"
            f"⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        try:
            notifier.send_message(startup_message)
            logger.info("Startup notification sent")
        except Exception:
            logger.exception("Startup notification failed")

        # Run the bot
        bot.run()

        # Send shutdown notification if graceful shutdown was requested
        if bot._shutdown_event.is_set():
            logger.info("Bot stopped via signal")
            shutdown_message = (
                "👋 <b>Telegram Interactive Bot Stopped</b>\n\n"
                "Bot has been shut down gracefully.\n\n"
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


if __name__ == "__main__":
    main()

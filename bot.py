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
    AsyncBinancePriceFetcher,
    TelegramNotifier,
    format_price,
    get_coin_emoji,
    now_in_configured_timezone,
    logger
)

# Setup logging
setup_logging(log_file="logs/bot.log")


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

        # Create inline keyboard with enabled coins
        keyboard = [
            [InlineKeyboardButton("📊 All Prices", callback_data="all_prices")]
        ]

        # Add coin buttons in rows of 2
        enabled_coins = self.config.get_enabled_coins()
        coin_buttons = []
        for coin_config in enabled_coins:
            emoji = get_coin_emoji(coin_config.coin_name)
            coin_buttons.append(
                InlineKeyboardButton(f"{emoji} {coin_config.coin_name}", callback_data=f"price_{coin_config.coin_name}")
            )

        # Group buttons into rows of 2
        for i in range(0, len(coin_buttons), 2):
            keyboard.append(coin_buttons[i:i+2])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            welcome_message,
            parse_mode="HTML",
            reply_markup=reply_markup,
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

            # Format threshold
            if coin_config.integer_threshold >= 1:
                threshold_str = f"${int(coin_config.integer_threshold):,}"
            else:
                threshold_str = f"${coin_config.integer_threshold}"

            emoji = get_coin_emoji(coin_config.coin_name)

            status_message += (
                f"{emoji} <b>{coin_config.coin_name}</b> ({coin_config.symbol})\n"
                f"   💰 Price: {format_price(price)}\n"
                f"   📍 Milestone: every {threshold_str}\n"
                f"   📊 Volatility Alert: {coin_config.volatility_percent}%/{coin_config.volatility_window}s\n\n"
            )

        # Add uptime and timestamp
        uptime = self._format_uptime()
        status_message += f"\n⌛ Uptime: {uptime}"
        status_message += f"\n⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"

        await update.message.reply_text(status_message, parse_mode="HTML", disable_notification=False)

    async def all_prices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /all command - show all prices"""
        message = "💰 <b>Current Prices</b>\n\n"

        enabled_coins = self.config.get_enabled_coins()
        if not enabled_coins:
            message += "❌ No coins are currently enabled!"
            message += f"\n\n⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"
            await update.message.reply_text(message, parse_mode="HTML", disable_notification=False)
            return

        prices = await self._get_prices([c.symbol for c in enabled_coins])

        for coin_config in enabled_coins:
            price = prices.get(coin_config.symbol)
            if price is not None:
                emoji = get_coin_emoji(coin_config.coin_name)
                message += f"{emoji} <b>{coin_config.coin_name}</b>: {format_price(price)}\n"
            else:
                message += f"❌ <b>{coin_config.coin_name}</b>: Failed to fetch\n"

        message += f"\n⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"

        await update.message.reply_text(message, parse_mode="HTML", disable_notification=False)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks"""
        query = update.callback_query
        await query.answer()

        callback_data = query.data

        if callback_data == "all_prices":
            # Send all prices as new message
            message = "💰 <b>Current Prices</b>\n\n"

            enabled_coins = self.config.get_enabled_coins()
            prices = await self._get_prices([c.symbol for c in enabled_coins])
            for coin_config in enabled_coins:
                price = prices.get(coin_config.symbol)
                if price is not None:
                    emoji = get_coin_emoji(coin_config.coin_name)
                    message += f"{emoji} <b>{coin_config.coin_name}</b>: {format_price(price)}\n"
                else:
                    message += f"❌ <b>{coin_config.coin_name}</b>: Failed to fetch\n"

            message += f"\n⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"

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
            msg = f"❌ {coin_name} is not configured."
            if message:
                await message.edit_text(text=msg, parse_mode="HTML")
            else:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode="HTML",
                    disable_notification=False
                )
            return

        if not coin_config.enabled:
            msg = f"❌ {coin_name} is not enabled in configuration."
            if message:
                await message.edit_text(text=msg, parse_mode="HTML")
            else:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode="HTML",
                    disable_notification=False
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
                    f"⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"
                )

                # Create keyboard with buttons
                keyboard = [
                    [InlineKeyboardButton(f"🔄 Refresh {coin_name}", callback_data=f"price_{coin_name}")],
                    [InlineKeyboardButton("📊 All Prices", callback_data="all_prices")],
                ]

                # Add buttons for other coins (exclude current coin)
                enabled_coins = self.config.get_enabled_coins()
                other_coins = [c for c in enabled_coins if c.coin_name != coin_name]

                # Add other coin buttons in rows of 2
                if other_coins:
                    coin_buttons = []
                    for coin in other_coins:
                        coin_emoji = get_coin_emoji(coin.coin_name)
                        coin_buttons.append(
                            InlineKeyboardButton(f"{coin_emoji} {coin.coin_name}", callback_data=f"price_{coin.coin_name}")
                        )

                    # Group into rows of 2
                    for i in range(0, len(coin_buttons), 2):
                        keyboard.append(coin_buttons[i:i+2])

                reply_markup = InlineKeyboardMarkup(keyboard)

                if message:
                    await message.edit_text(text=response, parse_mode="HTML", reply_markup=reply_markup)
                else:
                    await self.application.bot.send_message(
                        chat_id=chat_id,
                        text=response,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                        disable_notification=False
                    )
            else:
                error_msg = f"❌ Failed to fetch price for {coin_name}"
                if message:
                    await message.edit_text(text=error_msg, parse_mode="HTML")
                else:
                    await self.application.bot.send_message(
                        chat_id=chat_id,
                        text=error_msg,
                        parse_mode="HTML",
                        disable_notification=False
                    )
        except Exception as e:
            logger.error(f"Error sending price update for {coin_name}: {e}")
            error_msg = f"❌ Error fetching price for {coin_name}"
            if message:
                await message.edit_text(text=error_msg, parse_mode="HTML")
            else:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=error_msg,
                    parse_mode="HTML",
                    disable_notification=False
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
        notifier.send_message(startup_message)
        logger.info("Startup notification sent")

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
            notifier.send_message(shutdown_message)
            logger.info("Shutdown notification sent")

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        print("\nPlease make sure TELEGRAM_BOT_TOKEN is set in your .env file")
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt)")


if __name__ == "__main__":
    main()

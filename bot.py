#!/usr/bin/env python3
"""
Telegram Interactive Bot
Provides interactive commands and buttons to query cryptocurrency prices
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

# Import from existing monitor module
from monitor import BinancePriceFetcher, CoinConfig, format_price_for_display

# Load environment variables
load_dotenv()

# UTC+8 Timezone
UTC8 = timezone(timedelta(hours=8))

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram Interactive Bot"""

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")

        self.fetcher = BinancePriceFetcher()
        self.coin_names = ["BTC", "ETH", "SOL", "USD1"]

        # Create application
        self.application = Application.builder().token(self.token).build()

        # Register handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("price", self.price_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("all", self.all_prices_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

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

        # Create inline keyboard
        keyboard = [
            [InlineKeyboardButton("📊 All Prices", callback_data="all_prices")],
            [
                InlineKeyboardButton("₿ BTC", callback_data="price_BTC"),
                InlineKeyboardButton("Ξ ETH", callback_data="price_ETH"),
            ],
            [
                InlineKeyboardButton("◎ SOL", callback_data="price_SOL"),
                InlineKeyboardButton("$1 USD1", callback_data="price_USD1"),
            ],
        ]
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
        for coin_name in self.coin_names:
            config = CoinConfig(coin_name)
            if config.enabled:
                help_message += f"  • {coin_name}: {config.symbol}\n"

        await update.message.reply_text(help_message, parse_mode="HTML", disable_notification=False)

    async def price_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /price command"""
        # Get coin from argument
        if context.args and len(context.args) > 0:
            coin = context.args[0].upper()
        else:
            await update.message.reply_text(
                "❌ Please specify a coin.\n"
                "Example: /price BTC\n\n"
                "Available coins: BTC, ETH, SOL, USD1",
                parse_mode="HTML",
                disable_notification=False
            )
            return

        # Check if coin is valid
        if coin not in self.coin_names:
            await update.message.reply_text(
                f"❌ Unknown coin: {coin}\n\n"
                f"Available coins: {', '.join(self.coin_names)}",
                parse_mode="HTML",
                disable_notification=False
            )
            return

        # Get price
        await self.send_price_update(update.effective_chat.id, coin)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - show detailed status"""
        status_message = "📊 <b>Crypto Monitor Status</b>\n\n"

        has_enabled = False
        for coin_name in self.coin_names:
            config = CoinConfig(coin_name)
            if config.enabled:
                has_enabled = True
                price = self.fetcher.get_current_price(config.symbol)

                # Format threshold
                if config.integer_threshold >= 1:
                    threshold_str = f"${int(config.integer_threshold):,}"
                else:
                    threshold_str = f"${config.integer_threshold}"

                status_message += (
                    f"🪙 <b>{coin_name}</b> ({config.symbol})\n"
                    f"   💰 Price: {format_price_for_display(price) if price else '❌ N/A'}\n"
                    f"   📍 Milestone: every {threshold_str}\n"
                    f"   📊 Volatility Alert: {config.volatility_percent}%/{config.volatility_window}s\n\n"
                )

        if not has_enabled:
            status_message += "❌ No coins are currently enabled!"

        status_message += f"⏱️ {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"

        await update.message.reply_text(status_message, parse_mode="HTML", disable_notification=False)

    async def all_prices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /all command - show all prices"""
        message = "💰 <b>Current Prices</b>\n\n"

        has_enabled = False
        for coin_name in self.coin_names:
            config = CoinConfig(coin_name)
            if config.enabled:
                has_enabled = True
                price = self.fetcher.get_current_price(config.symbol)
                if price:
                    emoji = {"BTC": "₿", "ETH": "Ξ", "SOL": "◎", "USD1": "$1"}.get(coin_name, "🪙")
                    message += f"{emoji} <b>{coin_name}</b>: {format_price_for_display(price)}\n"
                else:
                    message += f"❌ <b>{coin_name}</b>: Failed to fetch\n"

        if not has_enabled:
            message += "❌ No coins are currently enabled!"

        message += f"\n⏱️ {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"

        await update.message.reply_text(message, parse_mode="HTML", disable_notification=False)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks"""
        query = update.callback_query
        await query.answer()

        callback_data = query.data

        if callback_data == "all_prices":
            # Send all prices as new message
            message = "💰 <b>Current Prices</b>\n\n"

            for coin_name in self.coin_names:
                config = CoinConfig(coin_name)
                if config.enabled:
                    price = self.fetcher.get_current_price(config.symbol)
                    if price:
                        emoji = {"BTC": "₿", "ETH": "Ξ", "SOL": "◎", "USD1": "$1"}.get(coin_name, "🪙")
                        message += f"{emoji} <b>{coin_name}</b>: {format_price_for_display(price)}\n"
                    else:
                        message += f"❌ <b>{coin_name}</b>: Failed to fetch\n"

            message += f"\n⏱️ {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"

            # Send new message instead of editing
            await query.message.reply_text(text=message, parse_mode="HTML", disable_notification=False)

        elif callback_data.startswith("price_"):
            # Get specific coin price - send as new message
            coin = callback_data.split("_")[1]
            await self.send_price_update(query.message.chat_id, coin, message=None)

    async def send_price_update(self, chat_id, coin_name, message=None):
        """Send price update for a specific coin"""
        config = CoinConfig(coin_name)

        if not config.enabled:
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

        price = self.fetcher.get_current_price(config.symbol)

        if price:
            emoji = {"BTC": "₿", "ETH": "Ξ", "SOL": "◎", "USD1": "$1"}.get(coin_name, "🪙")

            response = (
                f"{emoji} <b>{coin_name}</b> Price Update\n"
                f"💰 Current: {format_price_for_display(price)}\n"
                f"📈 Symbol: {config.symbol}\n"
                f"⏱️ {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # Add keyboard with refresh button
            keyboard = [
                [InlineKeyboardButton(f"🔄 Refresh {coin_name}", callback_data=f"price_{coin_name}")],
                [InlineKeyboardButton("📊 All Prices", callback_data="all_prices")],
            ]
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

    def run(self):
        """Start the bot"""
        logger.info("Starting Telegram Bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


def main():
    """Main entry point"""
    try:
        bot = TelegramBot()
        bot.run()
    except ValueError as e:
        print(f"Error: {e}")
        print("\nPlease make sure TELEGRAM_BOT_TOKEN is set in your .env file")
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")


if __name__ == "__main__":
    main()

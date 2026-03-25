"""
Telegram command and callback handlers.
"""

import re
from difflib import get_close_matches

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from common.clients.defillama import DefiLlamaClient
from common.logging import logger

from .messages import (
    render_help_message,
    render_price_detail_message,
    render_price_picker_message,
    render_stablecoin_prices_message,
    render_status_message,
    render_welcome_message,
)


async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        render_welcome_message(),
        parse_mode="HTML",
        reply_markup=self._build_start_keyboard(),
        disable_notification=False,
    )


async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_message = render_help_message(self.config.get_enabled_coins())
    await update.message.reply_text(help_message, parse_mode="HTML", disable_notification=False)


async def price_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /price command with input validation."""
    max_arg_length = 20
    allowed_pattern = re.compile(r"^[A-Z0-9]+$")

    if context.args and len(context.args) > 0:
        coin = context.args[0]
        if len(coin) > max_arg_length:
            await update.message.reply_text(
                "❌ 币种名称过长。\n"
                f"最大长度为 {max_arg_length} 个字符。",
                parse_mode="HTML",
                disable_notification=False,
            )
            return

        coin = coin.upper().strip()
        if not allowed_pattern.match(coin):
            await update.message.reply_text(
                "❌ 币种名称格式无效。\n"
                "仅允许字母和数字。\n\n"
                f"可用币种：{', '.join(self.config.coin_names)}",
                parse_mode="HTML",
                disable_notification=False,
            )
            return
    else:
        enabled_coin_rows = self._build_coin_button_rows()
        await update.message.reply_text(
            render_price_picker_message(),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(enabled_coin_rows),
            disable_notification=False,
        )
        return

    if coin not in self.config.coin_names:
        suggestions = get_close_matches(coin, self.config.coin_names, n=1, cutoff=0.5)
        suggestion_text = f"\n\n你是不是想查：<b>{suggestions[0]}</b>？" if suggestions else ""

        await update.message.reply_text(
            f"❌ 未知币种：{coin}{suggestion_text}\n\n"
            f"可用币种：{', '.join(self.config.coin_names)}",
            parse_mode="HTML",
            disable_notification=False,
        )
        return

    await self.send_price_update(update.effective_chat.id, coin)


async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    enabled_coins = self.config.get_enabled_coins()
    prices = await self._get_prices([c.symbol for c in enabled_coins])
    status_message = render_status_message(self, enabled_coins, prices)
    await update.message.reply_text(status_message, parse_mode="HTML", disable_notification=False)


async def all_prices_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /all command."""
    enabled_coins = self.config.get_enabled_coins()
    prices = await self._get_prices([c.symbol for c in enabled_coins])
    message = self._render_all_prices_message(enabled_coins, prices)
    await update.message.reply_text(message, parse_mode="HTML", disable_notification=False)


async def stablecoins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stablecoins command."""
    try:
        async with DefiLlamaClient() as client:
            stablecoins = await client.fetch_stablecoins(top_n=25)
        message = render_stablecoin_prices_message(stablecoins, self._format_timestamp())
        await self._send_or_edit_message(update.effective_chat.id, message)
    except Exception as exc:
        logger.error(f"Error fetching stablecoin prices: {exc}")
        await self._send_or_edit_message(update.effective_chat.id, "❌ 获取前25稳定币价格失败")


async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks."""
    query = update.callback_query
    await query.answer()

    callback_data = query.data
    if callback_data == "all_prices":
        enabled_coins = self.config.get_enabled_coins()
        prices = await self._get_prices([c.symbol for c in enabled_coins])
        message = self._render_all_prices_message(enabled_coins, prices)
        await query.message.reply_text(text=message, parse_mode="HTML", disable_notification=False)
    elif callback_data.startswith("price_"):
        coin = callback_data.removeprefix("price_")
        if not coin:
            logger.warning(f"Ignoring invalid callback data: {callback_data}")
            return
        await self.send_price_update(query.message.chat_id, coin, message=None)


async def send_price_update(self, chat_id, coin_name, message=None):
    """Send a single-coin price update."""
    coin_config = self.config.get_coin_config(coin_name)

    if not coin_config:
        await self._send_or_edit_message(
            chat_id,
            f"❌ {coin_name} 未在配置中定义。",
            message=message,
        )
        return

    if not coin_config.enabled:
        await self._send_or_edit_message(
            chat_id,
            f"❌ {coin_name} 在配置中未启用。",
            message=message,
        )
        return

    try:
        price = await self._get_price(coin_config.symbol)
        if price is not None:
            response = render_price_detail_message(
                coin_config,
                price,
                self._format_timestamp(),
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
                f"❌ 获取 {coin_name} 价格失败",
                message=message,
            )
    except Exception as e:
        logger.error(f"Error sending price update for {coin_name}: {e}")
        await self._send_or_edit_message(
            chat_id,
            f"❌ 获取 {coin_name} 价格时出错",
            message=message,
        )

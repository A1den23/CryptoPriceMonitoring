"""
Telegram bot message rendering helpers.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from common import CoinConfig, format_price, get_coin_emoji


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
    keyboard = [[InlineKeyboardButton("📊 查看全部价格", callback_data="all_prices")]]
    keyboard.extend(self._build_coin_button_rows())
    return InlineKeyboardMarkup(keyboard)


def _build_price_keyboard(self, coin_name: str) -> InlineKeyboardMarkup:
    """Build the keyboard shown for a specific coin price update."""
    keyboard = [
        [InlineKeyboardButton(f"🔄 刷新 {coin_name}", callback_data=f"price_{coin_name}")],
        [InlineKeyboardButton("📊 查看全部价格", callback_data="all_prices")],
    ]
    keyboard.extend(self._build_coin_button_rows(exclude_coin=coin_name))
    return InlineKeyboardMarkup(keyboard)


def _render_all_prices_message(
    self,
    enabled_coins: list[CoinConfig],
    prices: dict[str, float | None],
) -> str:
    """Render the shared all-prices message body."""
    message = "💰 <b>当前价格</b>\n\n"
    if not enabled_coins:
        return f"{message}❌ 当前没有启用任何币种！\n\n⏱️ {self._format_timestamp()}"

    for coin_config in enabled_coins:
        price = prices.get(coin_config.symbol)
        if price is not None:
            emoji = get_coin_emoji(coin_config.coin_name)
            message += f"{emoji} <b>{coin_config.coin_name}</b>: {format_price(price)}\n"
        else:
            message += f"❌ <b>{coin_config.coin_name}</b>: 获取失败\n"

    return f"{message}\n⏱️ {self._format_timestamp()}"


def render_welcome_message() -> str:
    """Build the /start welcome message."""
    return (
        "🤖 <b>加密货币价格监控机器人</b>\n\n"
        "欢迎使用！我可以帮助你查看和监控加密货币价格。\n\n"
        "📋 <b>可用命令：</b>\n"
        "/price - 查询指定币种价格\n"
        "/status - 查看所有监控币种状态\n"
        "/all - 查看所有已启用币种价格\n"
        "/help - 查看帮助说明\n\n"
        "也可以直接点击下方按钮快速查询 👇"
    )


def render_help_message(enabled_coins: list[CoinConfig]) -> str:
    """Build the /help message."""
    help_message = (
        "📖 <b>帮助与命令</b>\n\n"
        "<b>命令：</b>\n"
        "/price [coin] - 查询指定币种价格\n"
        "  示例：/price BTC\n"
        "/status - 查看所有币种详细状态\n"
        "/all - 快速查看所有已启用币种价格\n"
        "/start - 显示欢迎菜单和快捷按钮\n\n"
        "<b>按钮：</b>\n"
        "点击任意按钮即可立即查看最新价格！\n\n"
        "<b>监控币种：</b>\n"
    )

    for coin_config in enabled_coins:
        help_message += f"  • {coin_config.coin_name}: {coin_config.symbol}\n"

    return help_message


def render_status_message(
    self,
    enabled_coins: list[CoinConfig],
    prices: dict[str, float | None],
) -> str:
    """Build the /status message."""
    status_message = "📊 <b>监控状态</b>\n\n"

    if not enabled_coins:
        return f"{status_message}❌ 当前没有启用任何币种！\n\n⏱️ {self._format_timestamp()}"

    for coin_config in enabled_coins:
        price = prices.get(coin_config.symbol)
        if price is None:
            status_message += f"❌ <b>{coin_config.coin_name}</b>：获取数据失败\n\n"
            continue

        emoji = get_coin_emoji(coin_config.coin_name)
        status_message += (
            f"{emoji} <b>{coin_config.coin_name}</b> ({coin_config.symbol})\n"
            f"   💰 当前价格：{format_price(price)}\n"
            f"   📍 里程碑：每 {self._format_threshold(coin_config)}\n"
            f"   📊 波动告警：{coin_config.volatility_percent}%/{coin_config.volatility_window}s\n\n"
        )

    uptime = self._format_uptime()
    status_message += f"\n⌛ 运行时间：{uptime}"
    status_message += f"\n⏱️ {self._format_timestamp()}"
    return status_message


def render_price_update(
    coin_name: str,
    symbol: str,
    price: float,
    timestamp: str,
) -> str:
    """Build the single-coin price update message."""
    emoji = get_coin_emoji(coin_name)
    return (
        f"{emoji} <b>{coin_name}</b> 价格更新\n"
        f"💰 当前价格：{format_price(price)}\n"
        f"📈 交易对：{symbol}\n"
        f"⏱️ {timestamp}"
    )

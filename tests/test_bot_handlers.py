import sys
import types
import unittest
import asyncio
from unittest.mock import AsyncMock


def _install_dependency_stubs() -> None:
    """Install lightweight stubs so bot.handlers imports work without optional packages."""
    if "aiohttp" not in sys.modules:
        aiohttp = types.ModuleType("aiohttp")

        class ClientError(Exception):
            pass

        class ClientSession:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def close(self) -> None:
                return None

        class ClientTimeout:
            def __init__(self, total=None) -> None:
                self.total = total

        aiohttp.ClientError = ClientError
        aiohttp.ClientSession = ClientSession
        aiohttp.ClientTimeout = ClientTimeout
        sys.modules["aiohttp"] = aiohttp

    if "telegram" not in sys.modules:
        telegram = types.ModuleType("telegram")

        class Update:
            pass

        class InlineKeyboardButton:
            def __init__(self, text: str, callback_data: str) -> None:
                self.text = text
                self.callback_data = callback_data

        class InlineKeyboardMarkup:
            def __init__(self, keyboard) -> None:
                self.keyboard = keyboard

        telegram.Update = Update
        telegram.InlineKeyboardButton = InlineKeyboardButton
        telegram.InlineKeyboardMarkup = InlineKeyboardMarkup
        sys.modules["telegram"] = telegram

    if "telegram.ext" not in sys.modules:
        telegram_ext = types.ModuleType("telegram.ext")

        class Application:
            @staticmethod
            def builder():
                return None

        class CommandHandler:
            def __init__(self, *args, **kwargs) -> None:
                pass

        class CallbackQueryHandler:
            def __init__(self, *args, **kwargs) -> None:
                pass

        class ContextTypes:
            DEFAULT_TYPE = object

        telegram_ext.Application = Application
        telegram_ext.CommandHandler = CommandHandler
        telegram_ext.CallbackQueryHandler = CallbackQueryHandler
        telegram_ext.ContextTypes = ContextTypes
        sys.modules["telegram.ext"] = telegram_ext


_install_dependency_stubs()

from bot.handlers import price_command, send_price_update
from common.config import CoinConfig


class PriceCommandHandlerTests(unittest.TestCase):
    @staticmethod
    def _build_config():
        enabled_btc = CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1000.0,
            volatility_percent=3.0,
            volatility_window=60,
            volume_alert_multiplier=10.0,
        )
        enabled_eth = CoinConfig(
            coin_name="ETH",
            enabled=True,
            symbol="ETHUSDT",
            integer_threshold=100.0,
            volatility_percent=3.0,
            volatility_window=60,
            volume_alert_multiplier=10.0,
        )
        disabled_sol = CoinConfig(
            coin_name="SOL",
            enabled=False,
            symbol="SOLUSDT",
            integer_threshold=10.0,
            volatility_percent=3.0,
            volatility_window=60,
            volume_alert_multiplier=10.0,
        )
        coin_configs = {
            "BTC": enabled_btc,
            "ETH": enabled_eth,
            "SOL": disabled_sol,
        }
        return types.SimpleNamespace(
            coin_names=["BTC", "ETH", "SOL"],
            get_enabled_coins=lambda: [enabled_btc, enabled_eth],
            get_coin_config=lambda coin_name: coin_configs.get(coin_name),
        )

    @staticmethod
    def _build_handler_self():
        config = PriceCommandHandlerTests._build_config()

        class FakeHandler:
            def __init__(self) -> None:
                self.config = config
                self.send_price_update = AsyncMock()
                self._get_price = AsyncMock(return_value=95123.456)
                self._send_or_edit_message = AsyncMock()

            @staticmethod
            def _chunk_buttons(buttons, row_size: int = 2):
                return [buttons[i:i + row_size] for i in range(0, len(buttons), row_size)]

            def _build_coin_button_rows(self, exclude_coin=None):
                from bot.messages import _build_coin_button_rows

                return _build_coin_button_rows(self, exclude_coin=exclude_coin)

            def _build_price_keyboard(self, coin_name):
                from bot.messages import _build_price_keyboard

                return _build_price_keyboard(self, coin_name)

            @staticmethod
            def _format_timestamp():
                return "2026-03-25 10:30:45"

        return FakeHandler()

    @staticmethod
    def _build_update(chat_id: int = 123):
        return types.SimpleNamespace(
            effective_chat=types.SimpleNamespace(id=chat_id),
            message=types.SimpleNamespace(reply_text=AsyncMock()),
        )

    @staticmethod
    def _build_context(args):
        return types.SimpleNamespace(args=args)

    def test_price_without_args_shows_picker_prompt_and_buttons(self) -> None:
        handler_self = self._build_handler_self()
        update = self._build_update()
        context = self._build_context([])

        asyncio.run(price_command(handler_self, update, context))

        update.message.reply_text.assert_awaited_once()
        args, kwargs = update.message.reply_text.await_args
        sent_text = kwargs.get("text", args[0] if args else "")
        reply_markup = kwargs.get("reply_markup")
        self.assertIn("请选择", sent_text)
        self.assertIsNotNone(reply_markup)

        button_rows = reply_markup.keyboard
        callback_data = [button.callback_data for row in button_rows for button in row]
        self.assertEqual(callback_data, ["price_BTC", "price_ETH"])
        handler_self.send_price_update.assert_not_awaited()

    def test_price_with_coin_arg_still_sends_price_update(self) -> None:
        handler_self = self._build_handler_self()
        update = self._build_update(chat_id=456)
        context = self._build_context(["BTC"])

        asyncio.run(price_command(handler_self, update, context))

        handler_self.send_price_update.assert_awaited_once_with(456, "BTC")
        update.message.reply_text.assert_not_awaited()

    def test_send_price_update_renders_richer_detail_view_for_enabled_coin(self) -> None:
        handler_self = self._build_handler_self()

        asyncio.run(send_price_update(handler_self, 789, "BTC"))

        handler_self._get_price.assert_awaited_once_with("BTCUSDT")
        handler_self._send_or_edit_message.assert_awaited_once()
        args, kwargs = handler_self._send_or_edit_message.await_args
        self.assertEqual(args[0], 789)
        self.assertEqual(
            args[1],
            "₿ <b>BTC</b> 价格详情\n"
            "💰 当前价格：$95,123.46\n"
            "📈 交易对：BTCUSDT\n"
            "📍 里程碑：每 $1,000\n"
            "📊 波动告警：3.0%/60s\n"
            "⚙️ 状态：已启用\n"
            "⏱️ 2026-03-25 10:30:45",
        )
        self.assertIsNone(kwargs["message"])
        self.assertEqual(kwargs["reply_markup"].keyboard[0][0].callback_data, "price_BTC")

    def test_dependency_stubs_expose_bot_app_import_surface(self) -> None:
        import telegram.ext as telegram_ext

        self.assertTrue(hasattr(telegram_ext, "Application"))
        self.assertTrue(hasattr(telegram_ext, "CommandHandler"))
        self.assertTrue(hasattr(telegram_ext, "CallbackQueryHandler"))

    def test_dependency_stubs_expose_async_http_import_surface(self) -> None:
        import aiohttp

        self.assertTrue(hasattr(aiohttp, "ClientError"))
        self.assertTrue(hasattr(aiohttp, "ClientSession"))
        self.assertTrue(hasattr(aiohttp, "ClientTimeout"))


if __name__ == "__main__":
    unittest.main()

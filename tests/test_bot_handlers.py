import asyncio
import types
import unittest
from unittest.mock import AsyncMock

from tests.stubs import install_dependency_stubs


install_dependency_stubs()

from bot.handlers import button_callback, price_command, send_price_update
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

            def _build_coin_button_rows(self, exclude_coin=None):
                from bot.messages import build_coin_button_rows

                return build_coin_button_rows(self.config.get_enabled_coins(), exclude_coin=exclude_coin)

            def _build_price_keyboard(self, coin_name):
                from bot.messages import build_price_keyboard

                return build_price_keyboard(coin_name, self.config.get_enabled_coins())

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

    def test_send_price_update_uses_detail_view_copy(self) -> None:
        handler_self = self._build_handler_self()

        asyncio.run(send_price_update(handler_self, 789, "BTC"))

        args, _kwargs = handler_self._send_or_edit_message.await_args
        self.assertIn("价格详情", args[1])
        self.assertIn("里程碑", args[1])
        self.assertIn("波动告警", args[1])

    def test_send_price_update_escapes_html_sensitive_fields(self) -> None:
        coin_config = CoinConfig(
            coin_name="BTC<1>",
            enabled=True,
            symbol="BTC&USDT",
            integer_threshold=1000.0,
            volatility_percent=3.0,
            volatility_window=60,
            volume_alert_multiplier=10.0,
        )

        handler_self = types.SimpleNamespace(
            config=types.SimpleNamespace(get_coin_config=lambda coin_name: coin_config if coin_name == "BTC<1>" else None),
            _get_price=AsyncMock(return_value=95123.456),
            _send_or_edit_message=AsyncMock(),
            _format_timestamp=lambda: "2026-03-25 10:30:45",
            _build_price_keyboard=lambda coin_name: None,
        )

        asyncio.run(send_price_update(handler_self, 789, "BTC<1>"))

        args, _kwargs = handler_self._send_or_edit_message.await_args
        self.assertEqual(args[0], 789)
        self.assertIn("BTC&lt;1&gt;", args[1])
        self.assertIn("BTC&amp;USDT", args[1])
        self.assertNotIn("<1>", args[1])

    def test_button_callback_all_prices_edits_existing_message(self) -> None:
        handler_self = self._build_handler_self()
        handler_self._get_prices = AsyncMock(return_value={"BTCUSDT": 95123.456, "ETHUSDT": 3200.0})
        handler_self._render_all_prices_message = lambda coins, prices: "ALL PRICES"

        query_message = types.SimpleNamespace(chat_id=123, reply_text=AsyncMock())
        query = types.SimpleNamespace(
            data="all_prices",
            answer=AsyncMock(),
            message=query_message,
        )
        update = types.SimpleNamespace(callback_query=query)

        asyncio.run(button_callback(handler_self, update, None))

        handler_self._send_or_edit_message.assert_awaited_once_with(123, "ALL PRICES", message=query_message)
        query_message.reply_text.assert_not_awaited()

    def test_button_callback_price_refresh_reuses_original_message(self) -> None:
        handler_self = self._build_handler_self()
        query_message = types.SimpleNamespace(chat_id=123)
        query = types.SimpleNamespace(data="price_BTC", answer=AsyncMock(), message=query_message)
        update = types.SimpleNamespace(callback_query=query)

        asyncio.run(button_callback(handler_self, update, None))

        handler_self.send_price_update.assert_awaited_once_with(123, "BTC", message=query_message)

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

import sys
import types
import unittest


def _install_dependency_stubs() -> None:
    """Install lightweight stubs so bot.messages imports work without optional packages."""
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
            ALL_TYPES = object()

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


_install_dependency_stubs()

from bot.messages import (
    render_help_message,
    render_price_detail_message,
    render_price_picker_message,
)
from common.config import CoinConfig


class PriceMessageRenderingTests(unittest.TestCase):
    def test_dependency_stubs_expose_bot_import_surface(self) -> None:
        import telegram

        self.assertTrue(hasattr(telegram, "Update"))
        self.assertTrue(hasattr(telegram, "InlineKeyboardButton"))
        self.assertTrue(hasattr(telegram, "InlineKeyboardMarkup"))

    def test_render_price_picker_message_matches_expected_output(self) -> None:
        self.assertEqual(
            render_price_picker_message(),
            "💰 <b>请选择要查询的币种</b>",
        )

    def test_render_help_message_describes_picker_and_direct_lookup(self) -> None:
        enabled_coins = [
            CoinConfig(
                coin_name="BTC",
                enabled=True,
                symbol="BTCUSDT",
                integer_threshold=1000.0,
                volatility_percent=3.0,
                volatility_window=60,
                volume_alert_multiplier=10.0,
            )
        ]

        message = render_help_message(enabled_coins)

        self.assertIn("/price", message)
        self.assertIn("/price BTC", message)
        self.assertIn("选择", message)

    def test_render_price_detail_message_matches_expected_output(self) -> None:
        coin_config = CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1000.0,
            volatility_percent=3.0,
            volatility_window=60,
            volume_alert_multiplier=10.0,
        )

        message = render_price_detail_message(
            coin_config=coin_config,
            price=95123.456,
            timestamp="2026-03-25 10:30:45",
        )

        self.assertEqual(
            message,
            "₿ <b>BTC</b> 价格详情\n"
            "💰 当前价格：$95,123.46\n"
            "📈 交易对：BTCUSDT\n"
            "📍 里程碑：每 $1,000\n"
            "📊 波动告警：3.0%/60s\n"
            "⚙️ 状态：已启用\n"
            "⏱️ 2026-03-25 10:30:45",
        )

    def test_render_price_detail_message_escapes_html_sensitive_fields(self) -> None:
        coin_config = CoinConfig(
            coin_name="BTC<1>",
            enabled=True,
            symbol="BTC&USDT",
            integer_threshold=1000.0,
            volatility_percent=3.0,
            volatility_window=60,
            volume_alert_multiplier=10.0,
        )

        message = render_price_detail_message(
            coin_config=coin_config,
            price=95123.456,
            timestamp="2026-03-25 10:30:45",
        )

        self.assertIn("BTC&lt;1&gt;", message)
        self.assertIn("BTC&amp;USDT", message)
        self.assertNotIn("<1>", message)


if __name__ == "__main__":
    unittest.main()

import types
import unittest

from tests.stubs import install_dependency_stubs


install_dependency_stubs()

from bot.messages import (
    build_coin_button_rows,
    build_price_keyboard,
    build_start_keyboard,
    render_all_prices_message,
    render_help_message,
    render_price_detail_message,
    render_price_picker_message,
    render_status_message,
)
from common.config import CoinConfig


class PriceMessageRenderingTests(unittest.TestCase):
    @staticmethod
    def _build_enabled_coins() -> list[CoinConfig]:
        return [
            CoinConfig(
                coin_name="BTC",
                enabled=True,
                symbol="BTCUSDT",
                integer_threshold=1000.0,
                volatility_percent=3.0,
                volatility_window=60,
                volume_alert_multiplier=10.0,
            ),
            CoinConfig(
                coin_name="ETH",
                enabled=True,
                symbol="ETHUSDT",
                integer_threshold=100.0,
                volatility_percent=4.0,
                volatility_window=120,
                volume_alert_multiplier=8.0,
            ),
        ]

    def test_presentation_helpers_expose_pure_function_api(self) -> None:
        self.assertTrue(callable(build_coin_button_rows))
        self.assertTrue(callable(build_start_keyboard))
        self.assertTrue(callable(build_price_keyboard))
        self.assertTrue(callable(render_all_prices_message))
        self.assertTrue(callable(render_status_message))

    def test_build_coin_button_rows_creates_expected_callbacks(self) -> None:
        rows = build_coin_button_rows(self._build_enabled_coins())

        callback_data = [button.callback_data for row in rows for button in row]
        self.assertEqual(callback_data, ["price_BTC", "price_ETH"])

    def test_build_start_keyboard_includes_all_prices_and_coin_buttons(self) -> None:
        keyboard = build_start_keyboard(self._build_enabled_coins())

        callback_data = [button.callback_data for row in keyboard.keyboard for button in row]
        self.assertEqual(callback_data, ["all_prices", "price_BTC", "price_ETH"])

    def test_build_price_keyboard_excludes_active_coin_from_refresh_options(self) -> None:
        keyboard = build_price_keyboard("BTC", self._build_enabled_coins())

        callback_data = [button.callback_data for row in keyboard.keyboard for button in row]
        self.assertEqual(callback_data, ["price_BTC", "all_prices", "price_ETH"])

    def test_render_all_prices_message_renders_timestamp_without_self_dependency(self) -> None:
        message = render_all_prices_message(
            enabled_coins=self._build_enabled_coins(),
            prices={"BTCUSDT": 95_123.456, "ETHUSDT": None},
            timestamp="2026-03-25 10:30:45",
        )

        self.assertIn("<b>BTC</b>: $95,123.46", message)
        self.assertIn("<b>ETH</b>: 获取失败", message)
        self.assertTrue(message.endswith("⏱️ 2026-03-25 10:30:45"))

    def test_render_status_message_renders_without_self_dependency(self) -> None:
        message = render_status_message(
            enabled_coins=self._build_enabled_coins(),
            prices={"BTCUSDT": 95_123.456, "ETHUSDT": 3_200.0},
            uptime="1h 2m 3s",
            timestamp="2026-03-25 10:30:45",
        )

        self.assertIn("<b>BTC</b> (BTCUSDT)", message)
        self.assertIn("里程碑：每 $1,000", message)
        self.assertIn("⌛ 运行时间：1h 2m 3s", message)
        self.assertTrue(message.endswith("⏱️ 2026-03-25 10:30:45"))

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

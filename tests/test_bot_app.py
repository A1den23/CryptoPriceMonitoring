import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from tests.stubs import install_dependency_stubs


install_dependency_stubs()

import bot.app as bot_app


class FakeCommandHandler:
    def __init__(self, command: str, callback) -> None:
        self.command = command
        self.callback = callback


class FakeCallbackQueryHandler:
    def __init__(self, callback) -> None:
        self.callback = callback


class FakeApplication:
    def __init__(self) -> None:
        self.handlers = []
        self.bot = types.SimpleNamespace(send_message=None)
        self.initialized = False
        self.running = False
        self.updater = types.SimpleNamespace(
            running=False,
            start_polling=AsyncMock(),
            stop=AsyncMock(),
        )
        self.initialize = AsyncMock(side_effect=self._initialize)
        self.start = AsyncMock(side_effect=self._start)
        self.stop = AsyncMock(side_effect=self._stop)
        self.shutdown = AsyncMock(side_effect=self._shutdown)

    async def _initialize(self) -> None:
        self.initialized = True

    async def _start(self) -> None:
        self.running = True

    async def _stop(self) -> None:
        self.running = False

    async def _shutdown(self) -> None:
        self.initialized = False

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)


class FakeApplicationBuilder:
    def __init__(self, application: FakeApplication) -> None:
        self.application = application

    def token(self, token: str):
        return self

    def connection_pool_size(self, size: int):
        return self

    def pool_timeout(self, timeout: float | None):
        return self

    def get_updates_connection_pool_size(self, size: int):
        return self

    def get_updates_pool_timeout(self, timeout: float | None):
        return self

    def build(self) -> FakeApplication:
        return self.application


class TelegramBotAppTests(unittest.TestCase):
    def _build_bot(self):
        application = FakeApplication()

        class FakeApplicationModule:
            @staticmethod
            def builder():
                return FakeApplicationBuilder(application)

        config = types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat",
            bot_heartbeat_file="/tmp/bot-heartbeat-test",
            bot_heartbeat_interval_seconds=30.0,
            get_enabled_coins=lambda: [],
        )

        with patch.object(bot_app, "Application", FakeApplicationModule), \
             patch.object(bot_app, "CommandHandler", FakeCommandHandler), \
             patch.object(bot_app, "CallbackQueryHandler", FakeCallbackQueryHandler), \
             patch.object(bot_app.signal, "signal", side_effect=lambda signum, handler: handler):
            telegram_bot = bot_app.TelegramBot(config)

        return telegram_bot, application

    def test_telegram_bot_registers_signal_handlers_only_during_run_async(self) -> None:
        application = FakeApplication()

        class FakeApplicationModule:
            @staticmethod
            def builder():
                return FakeApplicationBuilder(application)

        config = types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat",
            bot_heartbeat_file="/tmp/bot-heartbeat-test",
            bot_heartbeat_interval_seconds=30.0,
            get_enabled_coins=lambda: [],
        )
        original_sigint = object()
        original_sigterm = object()

        async def fake_wait() -> None:
            telegram_bot._shutdown_event.set()
            return None

        async def fake_heartbeat_loop() -> None:
            await fake_wait()

        class FakeFetcherContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
                return None

        with patch.object(bot_app, "Application", FakeApplicationModule), \
             patch.object(bot_app, "CommandHandler", FakeCommandHandler), \
             patch.object(bot_app, "CallbackQueryHandler", FakeCallbackQueryHandler), \
             patch.object(bot_app, "AsyncBinancePriceFetcher", return_value=FakeFetcherContext()), \
             patch.object(bot_app, "now_in_configured_timezone", return_value=types.SimpleNamespace(strftime=lambda _fmt: "2026-03-25 10:30:45")), \
             patch.object(bot_app.TelegramBot, "_touch_heartbeat", return_value=None), \
             patch.object(bot_app.TelegramBot, "_heartbeat_loop", side_effect=fake_heartbeat_loop), \
             patch.object(bot_app.signal, "signal") as mock_signal:
            mock_signal.side_effect = [original_sigint, original_sigterm, None, None]
            telegram_bot = bot_app.TelegramBot(config)
            self.assertEqual(mock_signal.call_args_list, [])
            self.assertFalse(hasattr(telegram_bot, "_signal_handlers_registered") and telegram_bot._signal_handlers_registered)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(telegram_bot.run_async())
            finally:
                loop.close()

        self.assertEqual(
            mock_signal.call_args_list,
            [
                unittest.mock.call(bot_app.signal.SIGINT, telegram_bot._signal_handler),
                unittest.mock.call(bot_app.signal.SIGTERM, telegram_bot._signal_handler),
                unittest.mock.call(bot_app.signal.SIGINT, original_sigint),
                unittest.mock.call(bot_app.signal.SIGTERM, original_sigterm),
            ],
        )

    def test_telegram_bot_exposes_command_and_message_helper_methods(self) -> None:
        telegram_bot, _ = self._build_bot()

        command_method_names = [
            "start_command",
            "help_command",
            "price_command",
            "stablecoins_command",
            "status_command",
            "all_prices_command",
            "button_callback",
            "send_price_update",
        ]
        helper_method_names = [
            "_build_coin_button_rows",
            "_build_start_keyboard",
            "_build_price_keyboard",
            "_render_all_prices_message",
        ]

        for method_name in command_method_names + helper_method_names:
            self.assertTrue(hasattr(bot_app.TelegramBot, method_name), method_name)
            self.assertTrue(hasattr(telegram_bot, method_name), method_name)
            self.assertTrue(callable(getattr(telegram_bot, method_name)), method_name)

        for method_name in command_method_names:
            bound_method = getattr(telegram_bot, method_name)
            self.assertIs(bound_method.__self__, telegram_bot)
            self.assertIs(bound_method.__func__, getattr(bot_app.TelegramBot, method_name))

    def test_telegram_bot_run_async_closes_owned_notifier_on_shutdown(self) -> None:
        application = FakeApplication()

        class FakeApplicationModule:
            @staticmethod
            def builder():
                return FakeApplicationBuilder(application)

        config = types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat",
            bot_heartbeat_file="/tmp/bot-heartbeat-test",
            bot_heartbeat_interval_seconds=30.0,
            get_enabled_coins=lambda: [],
        )

        async def fake_wait() -> None:
            telegram_bot._shutdown_event.set()
            return None

        async def fake_heartbeat_loop() -> None:
            await fake_wait()

        class FakeFetcherContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
                return None

        with patch.object(bot_app, "Application", FakeApplicationModule), \
             patch.object(bot_app, "CommandHandler", FakeCommandHandler), \
             patch.object(bot_app, "CallbackQueryHandler", FakeCallbackQueryHandler), \
             patch.object(bot_app, "TelegramNotifier") as mock_notifier_cls, \
             patch.object(bot_app, "AsyncBinancePriceFetcher", return_value=FakeFetcherContext()), \
             patch.object(bot_app, "now_in_configured_timezone", return_value=types.SimpleNamespace(strftime=lambda _fmt: "2026-03-25 10:30:45")), \
             patch.object(bot_app.TelegramBot, "_touch_heartbeat", return_value=None), \
             patch.object(bot_app.TelegramBot, "_heartbeat_loop", side_effect=fake_heartbeat_loop), \
             patch.object(bot_app.signal, "signal", side_effect=lambda signum, handler: handler):
            telegram_bot = bot_app.TelegramBot(config)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(telegram_bot.run_async())
            finally:
                loop.close()

        mock_notifier_cls.return_value.close.assert_called_once_with()

    def test_telegram_bot_registers_handlers_with_explicit_bot_methods(self) -> None:
        telegram_bot, application = self._build_bot()

        self.assertEqual(len(application.handlers), 7)

        expected_commands = {
            "start": "start_command",
            "help": "help_command",
            "price": "price_command",
            "stablecoins": "stablecoins_command",
            "status": "status_command",
            "all": "all_prices_command",
        }

        command_handlers = [
            handler for handler in application.handlers if isinstance(handler, FakeCommandHandler)
        ]
        callback_handlers = [
            handler for handler in application.handlers if isinstance(handler, FakeCallbackQueryHandler)
        ]

        self.assertEqual(len(command_handlers), len(expected_commands))
        self.assertEqual(len(callback_handlers), 1)

        for handler in command_handlers:
            expected_method_name = expected_commands[handler.command]
            self.assertIs(handler.callback.__self__, telegram_bot)
            self.assertIs(handler.callback.__func__, getattr(bot_app.TelegramBot, expected_method_name))

        self.assertIs(callback_handlers[0].callback.__self__, telegram_bot)
        self.assertIs(callback_handlers[0].callback.__func__, bot_app.TelegramBot.button_callback)

    def test_telegram_bot_uses_config_for_notifier_and_heartbeat_settings(self) -> None:
        application = FakeApplication()

        class FakeApplicationModule:
            @staticmethod
            def builder():
                return FakeApplicationBuilder(application)

        config = types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat-from-config",
            bot_heartbeat_file="/tmp/custom-bot-heartbeat",
            bot_heartbeat_interval_seconds=45.0,
            get_enabled_coins=lambda: [],
        )

        with patch.object(bot_app, "Application", FakeApplicationModule), \
             patch.object(bot_app, "CommandHandler", FakeCommandHandler), \
             patch.object(bot_app, "CallbackQueryHandler", FakeCallbackQueryHandler), \
             patch.object(bot_app, "TelegramNotifier") as mock_notifier_cls, \
             patch.object(bot_app.signal, "signal", side_effect=lambda signum, handler: handler):
            telegram_bot = bot_app.TelegramBot(config)

        mock_notifier_cls.assert_called_once_with(bot_token="token", chat_id="chat-from-config")
        self.assertEqual(telegram_bot._heartbeat_file, bot_app.Path("/tmp/custom-bot-heartbeat"))
        self.assertEqual(telegram_bot._heartbeat_interval, 45.0)


if __name__ == "__main__":
    unittest.main()

import sys
import types
import unittest
from unittest.mock import patch


def _install_dependency_stubs() -> None:
    """Install lightweight stubs so bot imports work without optional packages."""
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: False
        sys.modules["dotenv"] = dotenv

    if "tenacity" not in sys.modules:
        tenacity = types.ModuleType("tenacity")

        def retry(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        tenacity.retry = retry
        tenacity.retry_if_exception_type = lambda *args, **kwargs: None
        tenacity.stop_after_attempt = lambda *args, **kwargs: None
        tenacity.wait_exponential = lambda *args, **kwargs: None
        sys.modules["tenacity"] = tenacity

    if "requests" not in sys.modules:
        requests = types.ModuleType("requests")

        class RequestException(Exception):
            pass

        class DummyResponse:
            def __init__(self, payload: dict | None = None) -> None:
                self._payload = payload or {"price": "1.0"}

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return self._payload

        class Session:
            def mount(self, *args, **kwargs) -> None:
                return None

            def get(self, *args, **kwargs) -> DummyResponse:
                return DummyResponse()

            def post(self, *args, **kwargs) -> DummyResponse:
                return DummyResponse({})

            def close(self) -> None:
                return None

        class HTTPAdapter:
            def __init__(self, *args, **kwargs) -> None:
                pass

        requests.Session = Session
        requests.post = lambda *args, **kwargs: DummyResponse({})
        requests.exceptions = types.SimpleNamespace(RequestException=RequestException)
        requests.adapters = types.SimpleNamespace(HTTPAdapter=HTTPAdapter)
        sys.modules["requests"] = requests

    if "aiohttp" not in sys.modules:
        aiohttp = types.ModuleType("aiohttp")

        class ClientError(Exception):
            pass

        class ClientTimeout:
            def __init__(self, total=None) -> None:
                self.total = total

        class DummyResponse:
            def raise_for_status(self) -> None:
                return None

            async def json(self) -> dict:
                return {"price": "1.0"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
                return None

        class ClientSession:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def get(self, *args, **kwargs) -> DummyResponse:
                return DummyResponse()

            async def close(self) -> None:
                return None

        aiohttp.ClientError = ClientError
        aiohttp.ClientSession = ClientSession
        aiohttp.ClientTimeout = ClientTimeout
        sys.modules["aiohttp"] = aiohttp

    if "websockets" not in sys.modules:
        websockets = types.ModuleType("websockets")

        class DummyProtocol:
            def __init__(self) -> None:
                self.closed = False

            async def ping(self):
                return None

            async def close(self) -> None:
                self.closed = True

        async def connect(*args, **kwargs):
            return DummyProtocol()

        class ConnectionClosed(Exception):
            pass

        websockets.connect = connect
        websockets.client = types.SimpleNamespace(WebSocketClientProtocol=DummyProtocol)
        websockets.exceptions = types.SimpleNamespace(ConnectionClosed=ConnectionClosed)
        sys.modules["websockets"] = websockets

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

    if "telegram.ext" not in sys.modules:
        telegram_ext = types.ModuleType("telegram.ext")

        async def _async_noop(*args, **kwargs) -> None:
            return None

        class ApplicationBuilder:
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

            def build(self):
                return types.SimpleNamespace(
                    add_handler=lambda *args, **kwargs: None,
                    bot=types.SimpleNamespace(send_message=_async_noop),
                    updater=types.SimpleNamespace(
                        start_polling=_async_noop,
                        stop=_async_noop,
                    ),
                    initialize=_async_noop,
                    start=_async_noop,
                    stop=_async_noop,
                    shutdown=_async_noop,
                )

        class Application:
            @staticmethod
            def builder():
                return ApplicationBuilder()

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
            get_enabled_coins=lambda: [],
        )

        with patch.object(bot_app, "Application", FakeApplicationModule), \
             patch.object(bot_app, "CommandHandler", FakeCommandHandler), \
             patch.object(bot_app, "CallbackQueryHandler", FakeCallbackQueryHandler), \
             patch.object(bot_app.signal, "signal", side_effect=lambda signum, handler: handler):
            telegram_bot = bot_app.TelegramBot(config)

        return telegram_bot, application

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


if __name__ == "__main__":
    unittest.main()

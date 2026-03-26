import sys
import types
from collections.abc import Callable


def install_dependency_stubs() -> None:
    """Install lightweight third-party stubs shared across tests."""
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
                    updater=types.SimpleNamespace(start_polling=_async_noop, stop=_async_noop),
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

import importlib
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest import mock


PACKAGE_PREFIXES = ("common", "bot", "monitor")
REPO_ROOT = Path(__file__).resolve().parents[1]
PRIMARY_MONITOR_ENTRYPOINT = "python -m monitor"
PRIMARY_BOT_ENTRYPOINT = "python -m bot"


def _package_module_names() -> tuple[str, ...]:
    return PACKAGE_PREFIXES


def _install_dependency_stubs() -> None:
    """Install lightweight stubs so package imports work without optional packages."""
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
                return {"price": "1.0", "peggedAssets": []}

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

        class ConnectionClosed(Exception):
            pass

        async def connect(*args, **kwargs):
            return DummyProtocol()

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

        class ContextTypes:
            DEFAULT_TYPE = object

        class DummyUpdater:
            def __init__(self) -> None:
                self.running = False

            async def start_polling(self, *args, **kwargs) -> None:
                self.running = True

            async def stop(self) -> None:
                self.running = False

        class DummyBot:
            async def send_message(self, *args, **kwargs) -> None:
                return None

        class Application:
            def __init__(self) -> None:
                self.bot = DummyBot()
                self.updater = DummyUpdater()
                self.running = False
                self.initialized = False

            @classmethod
            def builder(cls):
                class Builder:
                    def token(self, *args, **kwargs):
                        return self

                    def connection_pool_size(self, *args, **kwargs):
                        return self

                    def pool_timeout(self, *args, **kwargs):
                        return self

                    def get_updates_connection_pool_size(self, *args, **kwargs):
                        return self

                    def get_updates_pool_timeout(self, *args, **kwargs):
                        return self

                    def build(self):
                        return Application()

                return Builder()

            def add_handler(self, *args, **kwargs) -> None:
                return None

            async def initialize(self) -> None:
                self.initialized = True

            async def start(self) -> None:
                self.running = True

            async def stop(self) -> None:
                self.running = False

            async def shutdown(self) -> None:
                self.initialized = False

        class CommandHandler:
            def __init__(self, *args, **kwargs) -> None:
                pass

        class CallbackQueryHandler:
            def __init__(self, *args, **kwargs) -> None:
                pass

        telegram_ext.Application = Application
        telegram_ext.CommandHandler = CommandHandler
        telegram_ext.CallbackQueryHandler = CallbackQueryHandler
        telegram_ext.ContextTypes = ContextTypes
        sys.modules["telegram.ext"] = telegram_ext


def _clear_package_modules() -> None:
    for name in list(sys.modules):
        if name in PACKAGE_PREFIXES or name.startswith(tuple(f"{prefix}." for prefix in PACKAGE_PREFIXES)):
            sys.modules.pop(name)


class EntrypointImportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        _install_dependency_stubs()
        self._saved_modules = {
            name: module
            for name, module in sys.modules.items()
            if name in PACKAGE_PREFIXES or name.startswith(tuple(f"{prefix}." for prefix in PACKAGE_PREFIXES))
        }
        _clear_package_modules()

    def tearDown(self) -> None:
        _clear_package_modules()
        sys.modules.update(self._saved_modules)

    def test_common_import_contract_stays_stable(self) -> None:
        common = importlib.import_module("common")

        self.assertEqual(
            set(common.__all__),
            {
                "ConfigManager",
                "CoinConfig",
                "load_environment",
                "setup_logging",
                "logger",
                "get_logger",
                "BinancePriceFetcher",
                "AsyncBinancePriceFetcher",
                "BinanceAPIError",
                "BinanceWebSocketClient",
                "ConnectionState",
                "DefiLlamaClient",
                "StablecoinSnapshot",
                "TelegramNotifier",
                "format_price",
                "format_threshold",
                "get_coin_emoji",
                "get_coin_display_name",
                "get_configured_timezone",
                "now_in_configured_timezone",
                "TZ",
                "UTC8",
            },
        )
        self.assertNotIn("common.clients.http", sys.modules)
        self.assertNotIn("common.notifications", sys.modules)

        config = importlib.import_module("common.config")
        logging_utils = importlib.import_module("common.logging")
        utils = importlib.import_module("common.utils")
        clients_http = importlib.import_module("common.clients.http")
        clients_ws = importlib.import_module("common.clients.websocket")
        clients_defillama = importlib.import_module("common.clients.defillama")
        notifications = importlib.import_module("common.notifications")

        self.assertIs(common.ConfigManager, config.ConfigManager)
        self.assertIs(common.CoinConfig, config.CoinConfig)
        self.assertIs(common.load_environment, config.load_environment)
        self.assertIs(common.setup_logging, logging_utils.setup_logging)
        self.assertIs(common.logger, logging_utils.logger)
        self.assertIs(common.get_logger, logging_utils.get_logger)
        self.assertIs(common.BinancePriceFetcher, clients_http.BinancePriceFetcher)
        self.assertIs(common.AsyncBinancePriceFetcher, clients_http.AsyncBinancePriceFetcher)
        self.assertIs(common.BinanceAPIError, clients_http.BinanceAPIError)
        self.assertIs(common.BinanceWebSocketClient, clients_ws.BinanceWebSocketClient)
        self.assertIs(common.ConnectionState, clients_ws.ConnectionState)
        self.assertIs(common.DefiLlamaClient, clients_defillama.DefiLlamaClient)
        self.assertIs(common.StablecoinSnapshot, clients_defillama.StablecoinSnapshot)
        self.assertIs(common.TelegramNotifier, notifications.TelegramNotifier)
        self.assertIs(common.format_price, utils.format_price)
        self.assertIs(common.format_threshold, utils.format_threshold)
        self.assertIs(common.get_coin_emoji, utils.get_coin_emoji)
        self.assertIs(common.get_coin_display_name, utils.get_coin_display_name)
        self.assertIs(common.get_configured_timezone, utils.get_configured_timezone)
        self.assertIs(common.now_in_configured_timezone, utils.now_in_configured_timezone)
        self.assertIs(common.TZ, utils.TZ)
        self.assertIs(common.UTC8, utils.UTC8)

    def test_bot_import_contract_stays_stable(self) -> None:
        bot = importlib.import_module("bot")

        self.assertEqual(
            set(bot.__all__),
            {
                "ConfigManager",
                "TelegramBot",
                "TelegramNotifier",
                "asyncio",
                "load_environment",
                "logger",
                "main",
                "now_in_configured_timezone",
                "setup_logging",
                "signal",
            },
        )
        self.assertNotIn("bot.app", sys.modules)

        app = importlib.import_module("bot.app")
        config = importlib.import_module("common.config")
        logging_utils = importlib.import_module("common.logging")
        notifications = importlib.import_module("common.notifications")
        utils = importlib.import_module("common.utils")

        self.assertIs(bot.TelegramBot, app.TelegramBot)
        self.assertIs(bot.ConfigManager, config.ConfigManager)
        self.assertIs(bot.TelegramNotifier, notifications.TelegramNotifier)
        self.assertIs(bot.load_environment, config.load_environment)
        self.assertIs(bot.logger, logging_utils.logger)
        self.assertIs(bot.setup_logging, logging_utils.setup_logging)
        self.assertIs(bot.now_in_configured_timezone, utils.now_in_configured_timezone)
        self.assertTrue(callable(bot.main))

    def test_monitor_import_contract_stays_stable(self) -> None:
        monitor = importlib.import_module("monitor")

        self.assertEqual(
            set(monitor.__all__),
            {
                "BinanceWebSocketClient",
                "ConfigManager",
                "PriceMonitor",
                "StablecoinDepegMonitor",
                "TelegramNotifier",
                "WebSocketMultiCoinMonitor",
                "asyncio",
                "format_price",
                "format_threshold",
                "get_coin_display_name",
                "get_coin_emoji",
                "load_environment",
                "logger",
                "main",
                "now_in_configured_timezone",
                "price_monitor",
                "setup_logging",
                "show_status",
                "test_volatility_alert",
            },
        )
        self.assertNotIn("monitor.price_monitor", sys.modules)
        self.assertNotIn("monitor.ws_monitor", sys.modules)

        price_monitor = importlib.import_module("monitor.price_monitor")
        stablecoin_monitor = importlib.import_module("monitor.stablecoin_depeg_monitor")
        ws_monitor = importlib.import_module("monitor.ws_monitor")
        config = importlib.import_module("common.config")
        http = importlib.import_module("common.clients.http")
        websocket = importlib.import_module("common.clients.websocket")
        logging_utils = importlib.import_module("common.logging")
        notifications = importlib.import_module("common.notifications")
        utils = importlib.import_module("common.utils")

        self.assertIs(monitor.BinancePriceFetcher, http.BinancePriceFetcher)
        self.assertIs(monitor.BinanceWebSocketClient, websocket.BinanceWebSocketClient)
        self.assertIs(monitor.ConfigManager, config.ConfigManager)
        self.assertIs(monitor.PriceMonitor, price_monitor.PriceMonitor)
        self.assertIs(monitor.StablecoinDepegMonitor, stablecoin_monitor.StablecoinDepegMonitor)
        self.assertIs(monitor.TelegramNotifier, notifications.TelegramNotifier)
        self.assertIs(monitor.WebSocketMultiCoinMonitor, ws_monitor.WebSocketMultiCoinMonitor)
        self.assertIs(monitor.format_price, utils.format_price)
        self.assertIs(monitor.format_threshold, utils.format_threshold)
        self.assertIs(monitor.get_coin_display_name, utils.get_coin_display_name)
        self.assertIs(monitor.get_coin_emoji, utils.get_coin_emoji)
        self.assertIs(monitor.load_environment, config.load_environment)
        self.assertIs(monitor.logger, logging_utils.logger)
        self.assertIs(monitor.now_in_configured_timezone, utils.now_in_configured_timezone)
        self.assertIs(monitor.price_monitor, price_monitor)
        self.assertIs(monitor.setup_logging, logging_utils.setup_logging)
        self.assertTrue(callable(monitor.main))
        self.assertTrue(callable(monitor.show_status))
        self.assertTrue(callable(monitor.test_volatility_alert))

    def test_public_entrypoints_resolve_exports_when_called(self) -> None:
        bot = importlib.import_module("bot")
        monitor = importlib.import_module("monitor")
        _ = bot.TelegramBot
        _ = monitor.WebSocketMultiCoinMonitor

        bot_logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None)
        monitor_logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)

        with (
            mock.patch.object(bot, "load_environment") as bot_load_environment,
            mock.patch.object(bot, "setup_logging") as bot_setup_logging,
            mock.patch.object(bot, "ConfigManager", return_value=mock.sentinel.bot_config) as bot_config_manager,
            mock.patch.object(bot, "TelegramNotifier", return_value=mock.sentinel.bot_notifier) as bot_notifier_cls,
            mock.patch.object(bot, "TelegramBot") as bot_cls,
            mock.patch.object(bot, "now_in_configured_timezone") as bot_now,
            mock.patch.object(bot, "logger", bot_logger),
            mock.patch.object(monitor, "load_environment") as monitor_load_environment,
            mock.patch.object(monitor, "setup_logging") as monitor_setup_logging,
            mock.patch.object(monitor, "ConfigManager", return_value=mock.sentinel.monitor_config) as monitor_config_manager,
            mock.patch.object(monitor, "WebSocketMultiCoinMonitor") as ws_monitor_cls,
            mock.patch.object(monitor, "logger", monitor_logger),
            mock.patch.object(monitor.asyncio, "run") as asyncio_run,
            mock.patch.object(monitor, "show_status") as show_status,
            mock.patch.object(monitor, "test_volatility_alert") as test_volatility_alert,
            mock.patch.object(monitor.sys, "argv", ["monitor", "--status"]),
        ):
            bot_instance = bot_cls.return_value
            bot_instance.run.side_effect = KeyboardInterrupt()
            bot_instance._shutdown_event = types.SimpleNamespace(is_set=lambda: False)
            bot_now.return_value = mock.Mock(strftime=mock.Mock(return_value="2026-03-24 00:00:00"))

            bot.main()
            monitor.main()

        bot_load_environment.assert_called_once_with()
        bot_setup_logging.assert_called_once_with(log_file="logs/bot.log")
        bot_config_manager.assert_called_once_with()
        bot_notifier_cls.assert_called_once_with()
        bot_cls.assert_called_once_with(mock.sentinel.bot_config)
        bot_instance.run.assert_called_once_with()

        monitor_load_environment.assert_called_once_with()
        monitor_setup_logging.assert_called_once_with()
        monitor_config_manager.assert_called_once_with()
        show_status.assert_called_once_with()
        test_volatility_alert.assert_not_called()
        ws_monitor_cls.assert_not_called()
        asyncio_run.assert_not_called()

    def test_module_main_entrypoints_delegate_to_package_main(self) -> None:
        monitor = importlib.import_module("monitor")
        bot = importlib.import_module("bot")

        with (
            mock.patch.object(monitor, "main") as monitor_main,
            mock.patch.object(bot, "main") as bot_main,
        ):
            runpy.run_module("monitor", run_name="__main__", alter_sys=True)
            runpy.run_module("bot", run_name="__main__", alter_sys=True)

        monitor_main.assert_called_once_with()
        bot_main.assert_called_once_with()

    def test_compatibility_wrappers_delegate_to_package_main(self) -> None:
        monitor = importlib.import_module("monitor")
        bot = importlib.import_module("bot")

        with (
            mock.patch.object(monitor, "main") as monitor_main,
            mock.patch.object(bot, "main") as bot_main,
        ):
            runpy.run_path(str(REPO_ROOT / "monitor.py"), run_name="__main__")
            runpy.run_path(str(REPO_ROOT / "bot.py"), run_name="__main__")

        monitor_main.assert_called_once_with()
        bot_main.assert_called_once_with()

    def test_docs_and_runtime_share_module_entrypoint_contract(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text()
        deployment = (REPO_ROOT / "DEPLOYMENT.md").read_text()
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        compose = (REPO_ROOT / "docker-compose.yml").read_text()

        self.assertIn(PRIMARY_MONITOR_ENTRYPOINT, readme)
        self.assertIn(PRIMARY_BOT_ENTRYPOINT, readme)
        self.assertIn(PRIMARY_MONITOR_ENTRYPOINT, deployment)
        self.assertIn(PRIMARY_BOT_ENTRYPOINT, deployment)
        self.assertIn('CMD ["python", "-m", "monitor"]', dockerfile)
        self.assertIn('["python", "-m", "monitor"]', compose)
        self.assertIn('["python", "-m", "bot"]', compose)
        self.assertIn("b'-m monitor'", dockerfile)
        self.assertIn("b'-m bot'", dockerfile)
        self.assertNotIn("CMD [\"python\", \"monitor.py\"]", dockerfile)
        self.assertNotIn('["python", "monitor.py"]', compose)
        self.assertNotIn('["python", "bot.py"]', compose)


if __name__ == "__main__":
    unittest.main()

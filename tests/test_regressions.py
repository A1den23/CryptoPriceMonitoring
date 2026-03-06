import os
import sys
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _install_dependency_stubs() -> None:
    """Install lightweight stubs so tests run without optional third-party packages."""
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

import bot
import monitor
from common.config import CoinConfig
from common.notifications import TelegramNotifier
from common.utils import format_threshold
from monitor import PriceMonitor


class StubNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, message: str) -> bool:
        self.messages.append(message)
        return True


class FakeClock:
    def __init__(self, start_time: datetime) -> None:
        self.current = start_time

    def now(self) -> datetime:
        return self.current


class DummyResponse:
    def raise_for_status(self) -> None:
        return None


class PriceMonitorRegressionTests(unittest.TestCase):
    @staticmethod
    def _build_price_monitor(
        notifier: StubNotifier,
        *,
        volatility_percent: float = 3.0,
        volatility_window: int = 120,
        volatility_alert_cooldown_seconds: int = 30,
    ) -> PriceMonitor:
        config = CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1_000_000.0,
            volatility_percent=volatility_percent,
            volatility_window=volatility_window,
            volume_alert_multiplier=100.0,
        )
        return PriceMonitor(
            config,
            notifier,
            volatility_alert_cooldown_seconds=volatility_alert_cooldown_seconds,
        )

    def _replay_price_points(
        self,
        price_monitor: PriceMonitor,
        points: list[tuple[int, float]],
        *,
        start_time: datetime | None = None,
    ) -> list[str | None]:
        base_time = start_time or datetime(2026, 3, 6, tzinfo=timezone.utc)
        clock = FakeClock(base_time)
        outputs: list[str | None] = []

        with patch.object(monitor, "now_in_configured_timezone", side_effect=clock.now):
            for offset_seconds, price in points:
                clock.current = base_time + timedelta(seconds=offset_seconds)
                outputs.append(price_monitor.check(price))

        return outputs

    @staticmethod
    def _count_messages(notifier: StubNotifier, keyword: str) -> int:
        return sum(keyword in message for message in notifier.messages)

    def test_threshold_formatting_keeps_fractional_steps(self) -> None:
        self.assertEqual(format_threshold(2.5), "$2.5")
        self.assertEqual(format_threshold(1000.0), "$1,000")

    def test_small_threshold_updates_still_trigger_milestones(self) -> None:
        notifier = StubNotifier()
        config = CoinConfig(
            coin_name="USD1",
            enabled=True,
            symbol="USD1USDT",
            integer_threshold=0.0005,
            volatility_percent=1.0,
            volatility_window=180,
            volume_alert_multiplier=100.0,
        )
        price_monitor = PriceMonitor(config, notifier)

        first_update = price_monitor.check(1.0000)
        second_update = price_monitor.check(1.0006)

        self.assertIsNotNone(first_update)
        self.assertIsNotNone(second_update)
        self.assertIn("🎯", second_update)
        self.assertTrue(any("价格里程碑" in message for message in notifier.messages))

    def test_non_integer_threshold_above_one_uses_floor_logic(self) -> None:
        notifier = StubNotifier()
        config = CoinConfig(
            coin_name="TEST",
            enabled=True,
            symbol="TESTUSDT",
            integer_threshold=2.5,
            volatility_percent=10.0,
            volatility_window=180,
            volume_alert_multiplier=100.0,
        )
        price_monitor = PriceMonitor(config, notifier)

        price_monitor.check(2.49)
        second_update = price_monitor.check(2.51)

        self.assertIsNotNone(second_update)
        self.assertIn("🎯", second_update)
        self.assertTrue(any("价格里程碑" in message for message in notifier.messages))

    def test_cumulative_volatility_tracking_updates_during_cooldown(self) -> None:
        notifier = StubNotifier()
        config = CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1000.0,
            volatility_percent=5.0,
            volatility_window=180,
            volume_alert_multiplier=100.0,
        )
        price_monitor = PriceMonitor(config, notifier, volatility_alert_cooldown_seconds=60)
        current_time = datetime(2026, 3, 6, tzinfo=timezone.utc)
        price_monitor.last_volatility_notification_time = current_time
        price_monitor.last_cumulative_volatility = 10.0

        with patch.object(
            price_monitor,
            "_update_price_history",
            return_value=[100.0, 110.0, 100.0, 110.0],
        ), patch.object(
            monitor,
            "now_in_configured_timezone",
            return_value=current_time + timedelta(seconds=1),
        ):
            volatility_info = price_monitor.check_volatility(110.0)

        self.assertIsNotNone(volatility_info)
        self.assertGreater(price_monitor.last_cumulative_volatility, 10.0)
        self.assertEqual(notifier.messages, [])

    def test_volatility_replay_stable_market_does_not_alert(self) -> None:
        notifier = StubNotifier()
        price_monitor = self._build_price_monitor(notifier, volatility_percent=3.0)

        self._replay_price_points(
            price_monitor,
            [
                (0, 100.00),
                (10, 100.10),
                (20, 100.05),
                (30, 100.12),
                (40, 100.08),
                (50, 100.11),
            ],
        )

        self.assertEqual(self._count_messages(notifier, "波动警报"), 0)

    def test_volatility_replay_large_swings_trigger_alert(self) -> None:
        notifier = StubNotifier()
        price_monitor = self._build_price_monitor(notifier, volatility_percent=3.0)

        outputs = self._replay_price_points(
            price_monitor,
            [
                (0, 100.0),
                (10, 103.0),
                (20, 100.0),
                (30, 103.0),
            ],
        )

        self.assertTrue(any(output and "📊" in output for output in outputs))
        self.assertEqual(self._count_messages(notifier, "波动警报"), 1)

    def test_volatility_replay_alerts_again_after_cooldown_with_new_wave(self) -> None:
        notifier = StubNotifier()
        price_monitor = self._build_price_monitor(
            notifier,
            volatility_percent=3.0,
            volatility_window=60,
            volatility_alert_cooldown_seconds=30,
        )

        self._replay_price_points(
            price_monitor,
            [
                (0, 100.0),
                (10, 103.0),
                (20, 100.0),
                (30, 103.0),
                (35, 100.0),
                (40, 103.0),
                (100, 100.0),
                (110, 104.0),
                (120, 100.0),
                (130, 104.0),
            ],
        )

        self.assertEqual(self._count_messages(notifier, "波动警报"), 2)


class TelegramNotifierRegressionTests(unittest.TestCase):
    def test_rate_limit_slots_are_reserved_atomically(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")
        notifier._rate_limit = 1
        results: list[bool] = []
        post_call_count = 0
        counter_lock = threading.Lock()

        def fake_post(*args, **kwargs):
            nonlocal post_call_count
            with counter_lock:
                post_call_count += 1
            time.sleep(0.05)
            return DummyResponse()

        def worker() -> None:
            results.append(notifier.send_message("hello"))

        with patch("common.notifications.requests.post", side_effect=fake_post), \
             patch("common.notifications.logger.warning"):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(post_call_count, 1)


class MainEntrypointRegressionTests(unittest.TestCase):
    def test_bot_main_continues_when_startup_notification_fails(self) -> None:
        state = {"ran": False}

        class DummyNotifier:
            def send_message(self, message: str) -> bool:
                raise RuntimeError("telegram unavailable")

        class DummyBot:
            def __init__(self, config) -> None:
                self._shutdown_event = type("ShutdownEvent", (), {"is_set": lambda self: False})()

            def run(self) -> None:
                state["ran"] = True

        with patch.object(bot, "load_environment"), \
             patch.object(bot, "setup_logging"), \
             patch.object(bot, "ConfigManager", return_value=object()), \
             patch.object(bot, "TelegramNotifier", return_value=DummyNotifier()), \
             patch.object(bot.logger, "exception"), \
             patch.object(bot, "TelegramBot", DummyBot):
            bot.main()

        self.assertTrue(state["ran"])

    def test_bot_main_loads_environment_before_logging(self) -> None:
        original_debug = os.environ.get("DEBUG")
        state = {"debug_at_setup": None}

        class DummyNotifier:
            def send_message(self, message: str) -> bool:
                return True

        class DummyBot:
            def __init__(self, config) -> None:
                self._shutdown_event = type("ShutdownEvent", (), {"is_set": lambda self: False})()

            def run(self) -> None:
                return None

        def fake_load_environment() -> None:
            os.environ["DEBUG"] = "true"

        def fake_setup_logging(*args, **kwargs) -> None:
            state["debug_at_setup"] = os.environ.get("DEBUG")

        try:
            with patch.object(bot, "load_environment", side_effect=fake_load_environment), \
                 patch.object(bot, "setup_logging", side_effect=fake_setup_logging), \
                 patch.object(bot, "ConfigManager", return_value=object()), \
                 patch.object(bot, "TelegramNotifier", return_value=DummyNotifier()), \
                 patch.object(bot, "TelegramBot", DummyBot):
                bot.main()
        finally:
            if original_debug is None:
                os.environ.pop("DEBUG", None)
            else:
                os.environ["DEBUG"] = original_debug

        self.assertEqual(state["debug_at_setup"], "true")

    def test_monitor_main_loads_environment_before_logging(self) -> None:
        original_debug = os.environ.get("DEBUG")
        state = {"debug_at_setup": None}

        def fake_load_environment() -> None:
            os.environ["DEBUG"] = "true"

        def fake_setup_logging(*args, **kwargs) -> None:
            state["debug_at_setup"] = os.environ.get("DEBUG")

        try:
            with patch.object(monitor, "load_environment", side_effect=fake_load_environment), \
                 patch.object(monitor, "setup_logging", side_effect=fake_setup_logging), \
                 patch.object(monitor, "ConfigManager", return_value=object()), \
                 patch.object(monitor, "WebSocketMultiCoinMonitor"), \
                 patch.object(monitor.asyncio, "run"):
                monitor.main()
        finally:
            if original_debug is None:
                os.environ.pop("DEBUG", None)
            else:
                os.environ["DEBUG"] = original_debug

        self.assertEqual(state["debug_at_setup"], "true")


if __name__ == "__main__":
    unittest.main()

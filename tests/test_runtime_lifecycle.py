import asyncio
import importlib
import importlib.util
import signal
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.stubs import install_dependency_stubs


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))


install_dependency_stubs()

from common.config import CoinConfig
from monitor.price_monitor import PriceMonitor
from monitor.ws_monitor import WebSocketMultiCoinMonitor


class BlockingNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def test_connection(self) -> bool:
        return True

    def send_message(self, message: str) -> bool:
        self.messages.append(message)
        self.started.set()
        self.release.wait(timeout=5)
        return True


class FakeTask:
    def __init__(self) -> None:
        self.cancel_called = False
        self.awaited = False

    def cancel(self) -> None:
        self.cancel_called = True

    def add_done_callback(self, callback) -> None:
        return None

    def done(self) -> bool:
        return False

    def cancelled(self) -> bool:
        return self.cancel_called

    def exception(self):
        return None

    def result(self):
        return None

    def __await__(self):
        async def _wait() -> None:
            self.awaited = True
            if self.cancel_called:
                raise asyncio.CancelledError

        return _wait().__await__()


class CleanupTask:
    def __init__(self, owner: set, *, done: bool, exception: Exception | None = None) -> None:
        self._owner = owner
        self._done = done
        self._exception = exception
        self.cancel_called = False
        self.awaited = False

    def cancel(self) -> None:
        self.cancel_called = True
        self._done = True

    def add_done_callback(self, callback) -> None:
        return None

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return self.cancel_called and self._exception is None

    def exception(self):
        if self.cancel_called:
            raise asyncio.CancelledError
        return self._exception

    def result(self):
        if self._exception is not None:
            raise self._exception
        return None

    def __await__(self):
        async def _wait() -> None:
            self.awaited = True
            self._owner.discard(self)
            if self.cancel_called:
                raise asyncio.CancelledError
            if self._exception is not None:
                raise self._exception

        return _wait().__await__()


class RuntimeHelperModuleTests(unittest.TestCase):
    def test_shared_runtime_helper_module_exposes_signal_handler_registry_api(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("common.runtime"))

        runtime = importlib.import_module("common.runtime")
        registry_cls = getattr(runtime, "SignalHandlerRegistry", None)
        self.assertIsNotNone(registry_cls)

        handler = lambda signum, frame: None
        original_sigint = object()
        original_sigterm = object()
        registry = registry_cls()

        with patch("common.runtime.signal.signal") as mock_signal:
            mock_signal.side_effect = [original_sigint, original_sigterm, None, None]
            registry.setup(handler)
            registry.restore()

        self.assertEqual(
            mock_signal.call_args_list,
            [
                unittest.mock.call(signal.SIGINT, handler),
                unittest.mock.call(signal.SIGTERM, handler),
                unittest.mock.call(signal.SIGINT, original_sigint),
                unittest.mock.call(signal.SIGTERM, original_sigterm),
            ],
        )


class RuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def _build_coin_config(self) -> CoinConfig:
        return CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1_000_000.0,
            volatility_percent=5.0,
            volatility_window=180,
            volume_alert_multiplier=10.0,
        )

    def _build_ws_config(self):
        coin = self._build_coin_config()
        return types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat",
            monitor_heartbeat_file="/tmp/monitor-heartbeat-test",
            get_enabled_coins=lambda: [coin],
            volume_alert_cooldown_seconds=60,
            volatility_alert_cooldown_seconds=60,
            milestone_alert_cooldown_seconds=600,
            ws_ping_interval_seconds=30,
            ws_pong_timeout_seconds=10,
            ws_message_timeout_seconds=120,
            stablecoin_depeg_monitor_enabled=False,
            stablecoin_depeg_threshold_percent=5.0,
            stablecoin_depeg_alert_cooldown_seconds=3600,
            stablecoin_depeg_top_n=25,
            stablecoin_depeg_poll_interval_seconds=300,
        )

    async def test_price_monitor_flush_notification_tasks_waits_for_pending_notification(self) -> None:
        notifier = BlockingNotifier()
        monitor = PriceMonitor(self._build_coin_config(), notifier)

        monitor._send_notification("hello")
        started = await asyncio.to_thread(notifier.started.wait, 1)
        self.assertTrue(started)
        self.assertEqual(len(monitor._notification_tasks), 1)

        flush_task = asyncio.create_task(monitor.flush_notification_tasks())
        await asyncio.sleep(0)
        self.assertFalse(flush_task.done())

        notifier.release.set()
        await flush_task

        self.assertEqual(notifier.messages, ["hello"])
        self.assertEqual(monitor._notification_tasks, set())

    async def test_ws_monitor_run_drains_monitor_notification_tasks_on_shutdown(self) -> None:
        ws_task = FakeTask()
        shutdown_task = FakeTask()
        fake_monitor = types.SimpleNamespace(flush_notification_tasks=AsyncMock())
        ws_client = types.SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            get_statistics=lambda: {},
        )

        def fake_create_task(coro):
            name = coro.cr_code.co_name
            coro.close()
            if name == "start":
                return ws_task
            return shutdown_task

        with patch.object(WebSocketMultiCoinMonitor, "_setup_signal_handlers", return_value=None), \
             patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=ws_client), \
             patch("monitor.ws_monitor.asyncio.create_task", side_effect=fake_create_task), \
             patch("monitor.ws_monitor.asyncio.wait", return_value=({shutdown_task}, {ws_task})):
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True
            notifier.send_message.return_value = True

            ws_monitor = WebSocketMultiCoinMonitor(self._build_ws_config())
            ws_monitor.monitors = {"BTCUSDT": fake_monitor}
            ws_monitor._shutdown_event.set()

            await ws_monitor.run()

        ws_client.stop.assert_awaited_once_with()
        fake_monitor.flush_notification_tasks.assert_awaited_once_with()

    async def test_ws_monitor_run_uses_logger_instead_of_runtime_prints(self) -> None:
        ws_task = FakeTask()
        shutdown_task = FakeTask()
        fake_monitor = types.SimpleNamespace(flush_notification_tasks=AsyncMock())
        ws_client = types.SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            get_statistics=lambda: {},
        )

        def fake_create_task(coro):
            name = coro.cr_code.co_name
            coro.close()
            if name == "start":
                return ws_task
            return shutdown_task

        with patch.object(WebSocketMultiCoinMonitor, "_setup_signal_handlers", return_value=None), \
             patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=ws_client), \
             patch("monitor.ws_monitor.asyncio.create_task", side_effect=fake_create_task), \
             patch("monitor.ws_monitor.asyncio.wait", return_value=({shutdown_task}, {ws_task})), \
             patch("monitor.ws_monitor.print") as mock_print:
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True
            notifier.send_message.return_value = True

            ws_monitor = WebSocketMultiCoinMonitor(self._build_ws_config())
            ws_monitor.monitors = {"BTCUSDT": fake_monitor}
            ws_monitor._shutdown_event.set()

            await ws_monitor.run()

        mock_print.assert_not_called()

    async def test_ws_monitor_run_closes_owned_notifier_on_shutdown(self) -> None:
        ws_task = FakeTask()
        shutdown_task = FakeTask()
        fake_monitor = types.SimpleNamespace(flush_notification_tasks=AsyncMock())
        ws_client = types.SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            get_statistics=lambda: {},
        )

        def fake_create_task(coro):
            name = coro.cr_code.co_name
            coro.close()
            if name == "start":
                return ws_task
            return shutdown_task

        with patch.object(WebSocketMultiCoinMonitor, "_setup_signal_handlers", return_value=None), \
             patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=ws_client), \
             patch("monitor.ws_monitor.asyncio.create_task", side_effect=fake_create_task), \
             patch("monitor.ws_monitor.asyncio.wait", return_value=({shutdown_task}, {ws_task})):
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True
            notifier.send_message.return_value = True

            ws_monitor = WebSocketMultiCoinMonitor(self._build_ws_config())
            ws_monitor.monitors = {"BTCUSDT": fake_monitor}
            ws_monitor._shutdown_event.set()

            await ws_monitor.run()

        notifier.close.assert_called_once_with()

    async def test_ws_monitor_run_cleans_up_on_parent_task_cancellation(self) -> None:
        fake_monitor = types.SimpleNamespace(flush_notification_tasks=AsyncMock())
        ws_client = types.SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            get_statistics=lambda: {},
        )

        async def cancelled_wait(*args, **kwargs):
            raise asyncio.CancelledError

        with patch.object(WebSocketMultiCoinMonitor, "_setup_signal_handlers", return_value=None), \
             patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=ws_client), \
             patch("monitor.ws_monitor.asyncio.wait", side_effect=cancelled_wait):
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True
            notifier.send_message.return_value = True

            ws_monitor = WebSocketMultiCoinMonitor(self._build_ws_config())
            ws_monitor.monitors = {"BTCUSDT": fake_monitor}

            with self.assertRaises(asyncio.CancelledError):
                await ws_monitor.run()

        ws_client.stop.assert_awaited_once_with()
        fake_monitor.flush_notification_tasks.assert_awaited_once_with()

    async def test_ws_monitor_run_cleans_up_pending_disconnect_notification_on_shutdown(self) -> None:
        ws_task = FakeTask()
        shutdown_task = FakeTask()
        ws_client = types.SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            get_statistics=lambda: {},
        )
        notification_started = asyncio.Event()
        notification_cancelled = asyncio.Event()

        async def pending_notification() -> bool:
            notification_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                notification_cancelled.set()
                raise

        def fake_create_task(coro):
            name = coro.cr_code.co_name
            coro.close()
            if name == "start":
                return ws_task
            return shutdown_task

        with patch.object(WebSocketMultiCoinMonitor, "_setup_signal_handlers", return_value=None), \
             patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=ws_client), \
             patch("monitor.ws_monitor.asyncio.create_task", side_effect=fake_create_task), \
             patch("monitor.ws_monitor.asyncio.wait", return_value=({shutdown_task}, {ws_task})):
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True
            notifier.send_message.return_value = True

            ws_monitor = WebSocketMultiCoinMonitor(self._build_ws_config())
            ws_monitor._shutdown_event.set()

            with patch("monitor.ws_monitor.asyncio.to_thread", new=lambda *args, **kwargs: pending_notification()):
                await ws_monitor._on_disconnect("test disconnect")
                await asyncio.wait_for(notification_started.wait(), timeout=1)
                self.assertEqual(len(ws_monitor._notification_tasks), 1)

                await ws_monitor.run()
                await asyncio.wait_for(notification_cancelled.wait(), timeout=1)

        ws_client.stop.assert_awaited_once_with()
        self.assertEqual(ws_monitor._notification_tasks, set())

    async def test_ws_monitor_run_cleans_up_pending_reconnect_notification_on_cancellation(self) -> None:
        ws_client = types.SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            get_statistics=lambda: {},
        )
        notification_started = asyncio.Event()
        notification_cancelled = asyncio.Event()

        async def pending_notification() -> bool:
            notification_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                notification_cancelled.set()
                raise

        async def cancelled_wait(*args, **kwargs):
            raise asyncio.CancelledError

        with patch.object(WebSocketMultiCoinMonitor, "_setup_signal_handlers", return_value=None), \
             patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=ws_client), \
             patch("monitor.ws_monitor.asyncio.wait", side_effect=cancelled_wait):
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True
            notifier.send_message.return_value = True

            ws_monitor = WebSocketMultiCoinMonitor(self._build_ws_config())

            with patch("monitor.ws_monitor.asyncio.to_thread", new=lambda *args, **kwargs: pending_notification()):
                await ws_monitor._on_reconnect(attempt_count=1)
                await asyncio.wait_for(notification_started.wait(), timeout=1)
                self.assertEqual(len(ws_monitor._notification_tasks), 1)

                with self.assertRaises(asyncio.CancelledError):
                    await ws_monitor.run()
                await asyncio.wait_for(notification_cancelled.wait(), timeout=1)

        ws_client.stop.assert_awaited_once_with()
        self.assertEqual(ws_monitor._notification_tasks, set())

    async def test_flush_notification_tasks_ignores_failed_completed_task_and_keeps_draining(self) -> None:
        with patch.object(WebSocketMultiCoinMonitor, "_setup_signal_handlers", return_value=None), \
             patch("monitor.ws_monitor.TelegramNotifier"):
            ws_monitor = WebSocketMultiCoinMonitor(self._build_ws_config())

        failed_task = CleanupTask(ws_monitor._notification_tasks, done=True, exception=RuntimeError("boom"))
        pending_task = CleanupTask(ws_monitor._notification_tasks, done=False)
        ws_monitor._notification_tasks.update({failed_task, pending_task})

        await ws_monitor._flush_notification_tasks()

        self.assertTrue(failed_task.awaited)
        self.assertFalse(failed_task.cancel_called)
        self.assertTrue(pending_task.awaited)
        self.assertTrue(pending_task.cancel_called)
        self.assertEqual(ws_monitor._notification_tasks, set())

    async def test_ws_monitor_registers_and_restores_signal_handlers_with_runtime_lifecycle(self) -> None:
        ws_task = FakeTask()
        shutdown_task = FakeTask()
        ws_client = types.SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            get_statistics=lambda: {},
        )
        original_sigint = object()
        original_sigterm = object()

        def fake_create_task(coro):
            name = coro.cr_code.co_name
            coro.close()
            if name == "start":
                return ws_task
            return shutdown_task

        with patch("monitor.ws_monitor.signal.signal") as mock_signal, \
             patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=ws_client), \
             patch("monitor.ws_monitor.asyncio.create_task", side_effect=fake_create_task), \
             patch("monitor.ws_monitor.asyncio.wait", return_value=({shutdown_task}, {ws_task})):
            mock_signal.side_effect = [original_sigint, original_sigterm, None, None]
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True
            notifier.send_message.return_value = True

            ws_monitor = WebSocketMultiCoinMonitor(self._build_ws_config())
            self.assertEqual(mock_signal.call_args_list, [])
            self.assertIsNone(ws_monitor._original_sigint)
            self.assertIsNone(ws_monitor._original_sigterm)
            self.assertFalse(ws_monitor._signal_handlers_registered)
            ws_monitor._shutdown_event.set()

            await ws_monitor.run()

        self.assertEqual(
            mock_signal.call_args_list,
            [
                unittest.mock.call(signal.SIGINT, ws_monitor._signal_handler),
                unittest.mock.call(signal.SIGTERM, ws_monitor._signal_handler),
                unittest.mock.call(signal.SIGINT, original_sigint),
                unittest.mock.call(signal.SIGTERM, original_sigterm),
            ],
        )


if __name__ == "__main__":
    unittest.main()

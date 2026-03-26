import asyncio
import importlib
import importlib.util
import inspect
import os
import re
import subprocess
import sys
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tests.stubs import install_dependency_stubs


install_dependency_stubs()

import bot
import monitor
from common.clients.websocket import ConnectionState
from common.config import CoinConfig, ConfigManager
from common.notifications import TelegramNotifier
from common.utils import format_threshold, get_coin_display_name
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

    def json(self) -> dict:
        return {"ok": True}


class FakeAsyncIterableWebSocket:
    def __init__(self, messages: list[str]) -> None:
        self._messages = iter(messages)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._messages)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeHeartbeatTask:
    def __init__(self) -> None:
        self.cancel_called = False
        self.awaited = False

    def cancel(self) -> None:
        self.cancel_called = True

    def result(self):
        return None

    def __await__(self):
        async def _wait() -> None:
            self.awaited = True
            if self.cancel_called:
                raise asyncio.CancelledError

        return _wait().__await__()


class FakeAsyncContextManager:
    def __init__(self, value) -> None:
        self.value = value
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.value

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.exited = True
        return None


class FakeStablecoinClient:
    def __init__(self, *, snapshots=None, error: Exception | None = None) -> None:
        self.snapshots = snapshots or []
        self.error = error
        self.calls: list[int] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    async def close(self) -> None:
        return None

    async def fetch_stablecoins(self, top_n: int):
        self.calls.append(top_n)
        if self.error is not None:
            raise self.error
        return self.snapshots[:top_n]


class SharedTestStubRegressionTests(unittest.TestCase):
    def test_shared_stub_module_exists_and_installs_core_import_surface(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("tests.stubs"))

        stubs = importlib.import_module("tests.stubs")
        install_dependency_stubs = getattr(stubs, "install_dependency_stubs")
        install_dependency_stubs()

        import aiohttp
        import telegram
        import telegram.ext as telegram_ext

        self.assertTrue(hasattr(aiohttp, "ClientSession"))
        self.assertTrue(hasattr(telegram, "Update"))
        self.assertTrue(hasattr(telegram.Update, "ALL_TYPES"))
        self.assertTrue(hasattr(telegram_ext, "Application"))


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

        with patch.object(monitor.price_monitor, "now_in_configured_timezone", side_effect=clock.now):
            for offset_seconds, price in points:
                clock.current = base_time + timedelta(seconds=offset_seconds)
                outputs.append(price_monitor.check(price))

        return outputs

    @staticmethod
    def _count_messages(notifier: StubNotifier, keyword: str) -> int:
        return sum(keyword in message for message in notifier.messages)

    def test_latest_volume_info_is_consumed_once(self) -> None:
        notifier = StubNotifier()
        price_monitor = self._build_price_monitor(notifier)
        price_monitor.latest_volume_info = "V:12.0x🚨"

        first_output = price_monitor.check(100.0)
        second_output = price_monitor.check(100.01)

        self.assertIsNotNone(first_output)
        self.assertIsNotNone(second_output)
        self.assertIn("V:12.0x🚨", first_output)
        self.assertNotIn("V:12.0x🚨", second_output)

    def test_threshold_formatting_keeps_fractional_steps(self) -> None:
        self.assertEqual(format_threshold(2.5), "$2.5")
        self.assertEqual(format_threshold(1000.0), "$1,000")

    def test_get_coin_display_name_only_removes_usdt_suffix(self) -> None:
        self.assertEqual(get_coin_display_name("BTCUSDT"), "BTC")
        self.assertEqual(get_coin_display_name("USDTBUSD"), "USDTBUSD")

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

    def test_milestone_notification_handles_missing_last_price(self) -> None:
        notifier = StubNotifier()
        config = CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1000.0,
            volatility_percent=3.0,
            volatility_window=180,
            volume_alert_multiplier=100.0,
        )
        price_monitor = PriceMonitor(config, notifier)
        current_time = datetime(2026, 3, 6, tzinfo=timezone.utc)

        with patch.object(monitor.price_monitor, "now_in_configured_timezone", return_value=current_time):
            price_monitor._send_milestone_notification(101_000.0, 101_000.0)

        self.assertEqual(price_monitor.last_price, 101_000.0)
        self.assertEqual(price_monitor.last_milestone_notification_time, current_time)
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("价格里程碑", notifier.messages[0])

    def test_monitor_notifications_escape_symbol_for_html_parse_mode(self) -> None:
        notifier = StubNotifier()
        config = CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTC<USDT>",
            integer_threshold=1000.0,
            volatility_percent=3.0,
            volatility_window=180,
            volume_alert_multiplier=10.0,
        )
        price_monitor = PriceMonitor(config, notifier)

        with patch.object(
            monitor.price_monitor,
            "now_in_configured_timezone",
            return_value=datetime(2026, 3, 6, tzinfo=timezone.utc),
        ):
            price_monitor._send_milestone_notification(101000.0, 101000.0)
            price_monitor.price_history.extend(
                [
                    monitor.price_monitor.PriceData(100000.0, datetime(2026, 3, 6, tzinfo=timezone.utc)),
                    monitor.price_monitor.PriceData(101000.0, datetime(2026, 3, 6, tzinfo=timezone.utc)),
                ]
            )
            price_monitor._send_volatility_alert(101000.0, ["区间波动: 1.00%"])
            price_monitor.volume_history.extend(
                [
                    monitor.price_monitor.VolumeData(100000.0, 100.0, datetime(2026, 3, 6, tzinfo=timezone.utc)),
                    monitor.price_monitor.VolumeData(100500.0, 100.0, datetime(2026, 3, 6, tzinfo=timezone.utc)),
                ]
            )
            price_monitor.check_volume_anomaly(101000.0, 2000.0)

        self.assertIn("BTC&lt;USDT&gt;", notifier.messages[0])
        self.assertIn("BTC&lt;USDT&gt;", notifier.messages[1])
        self.assertIn("BTC&lt;USDT&gt;", notifier.messages[2])

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
            monitor.price_monitor,
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

    def test_small_price_changes_still_update_volatility_window(self) -> None:
        notifier = StubNotifier()
        price_monitor = self._build_price_monitor(notifier, volatility_percent=0.2, volatility_window=120)

        outputs = self._replay_price_points(
            price_monitor,
            [
                (0, 1.0000),
                (10, 1.00005),
                (20, 1.00010),
                (30, 1.00015),
            ],
        )

        self.assertGreaterEqual(len(price_monitor.price_history), 3)
        self.assertTrue(any(output is None for output in outputs))

    def test_small_price_changes_preserve_full_volatility_window_for_later_alerts(self) -> None:
        notifier = StubNotifier()
        price_monitor = self._build_price_monitor(notifier, volatility_percent=0.2, volatility_window=120)

        outputs = self._replay_price_points(
            price_monitor,
            [
                (0, 1.0000),
                (10, 1.00005),
                (20, 1.00010),
                (30, 1.00015),
                (40, 1.00450),
            ],
        )

        self.assertTrue(any(output is None for output in outputs[:-1]))
        self.assertIsNotNone(outputs[-1])
        self.assertIn("📊", outputs[-1])
        self.assertEqual(self._count_messages(notifier, "波动警报"), 1)

    def test_milestone_send_failure_does_not_advance_notification_state(self) -> None:
        class FailingNotifier(StubNotifier):
            def send_message(self, message: str) -> bool:
                raise RuntimeError("telegram unavailable")

        notifier = FailingNotifier()
        price_monitor = self._build_price_monitor(notifier)
        current_time = datetime(2026, 3, 6, tzinfo=timezone.utc)
        price_monitor.last_price = 100_000.0

        async def exercise() -> None:
            with patch.object(
                monitor.price_monitor,
                "now_in_configured_timezone",
                return_value=current_time,
            ), patch.object(monitor.price_monitor.logger, "exception"):
                price_monitor._send_milestone_notification(101_000.0, 101_000.0)
                await price_monitor.flush_notification_tasks()

        asyncio.run(exercise())

        self.assertEqual(price_monitor.last_price, 100_000.0)
        self.assertIsNone(price_monitor.last_milestone_notification_time)

    def test_volatility_send_failure_does_not_advance_cooldown(self) -> None:
        class FailingNotifier(StubNotifier):
            def send_message(self, message: str) -> bool:
                raise RuntimeError("telegram unavailable")

        notifier = FailingNotifier()
        price_monitor = self._build_price_monitor(notifier)
        current_time = datetime(2026, 3, 6, tzinfo=timezone.utc)
        price_monitor.price_history.extend(
            [
                monitor.price_monitor.PriceData(100_000.0, current_time),
                monitor.price_monitor.PriceData(101_000.0, current_time),
            ]
        )

        async def exercise() -> None:
            with patch.object(
                monitor.price_monitor,
                "now_in_configured_timezone",
                return_value=current_time,
            ), patch.object(monitor.price_monitor.logger, "exception"):
                price_monitor._send_volatility_alert(101_000.0, ["区间波动: 1.00%"])
                await price_monitor.flush_notification_tasks()

        asyncio.run(exercise())

        self.assertIsNone(price_monitor.last_volatility_notification_time)

    def test_volume_send_failure_does_not_advance_cooldown(self) -> None:
        class FailingNotifier(StubNotifier):
            def send_message(self, message: str) -> bool:
                raise RuntimeError("telegram unavailable")

        notifier = FailingNotifier()
        price_monitor = self._build_price_monitor(notifier)
        current_time = datetime(2026, 3, 6, tzinfo=timezone.utc)
        price_monitor.volume_history.extend(
            [
                monitor.price_monitor.VolumeData(100_000.0, 100.0, current_time),
                monitor.price_monitor.VolumeData(100_500.0, 100.0, current_time),
            ]
        )

        async def exercise() -> None:
            with patch.object(
                monitor.price_monitor,
                "now_in_configured_timezone",
                return_value=current_time,
            ), patch.object(monitor.price_monitor.logger, "exception"):
                price_monitor.check_volume_anomaly(101_000.0, 20_000.0)
                await price_monitor.flush_notification_tasks()

        asyncio.run(exercise())

        self.assertIsNone(price_monitor.last_volume_alert_time)

    def test_milestone_false_send_result_does_not_advance_notification_state(self) -> None:
        class FalseNotifier(StubNotifier):
            def send_message(self, message: str) -> bool:
                return False

        notifier = FalseNotifier()
        price_monitor = self._build_price_monitor(notifier)
        current_time = datetime(2026, 3, 6, tzinfo=timezone.utc)
        price_monitor.last_price = 100_000.0

        async def exercise() -> None:
            with patch.object(
                monitor.price_monitor,
                "now_in_configured_timezone",
                return_value=current_time,
            ), patch.object(monitor.price_monitor.logger, "error"):
                price_monitor._send_milestone_notification(101_000.0, 101_000.0)
                await price_monitor.flush_notification_tasks()

        asyncio.run(exercise())

        self.assertEqual(price_monitor.last_price, 100_000.0)
        self.assertIsNone(price_monitor.last_milestone_notification_time)

    def test_volatility_false_send_result_does_not_advance_cooldown(self) -> None:
        class FalseNotifier(StubNotifier):
            def send_message(self, message: str) -> bool:
                return False

        notifier = FalseNotifier()
        price_monitor = self._build_price_monitor(notifier)
        current_time = datetime(2026, 3, 6, tzinfo=timezone.utc)
        price_monitor.price_history.extend(
            [
                monitor.price_monitor.PriceData(100_000.0, current_time),
                monitor.price_monitor.PriceData(101_000.0, current_time),
            ]
        )

        async def exercise() -> None:
            with patch.object(
                monitor.price_monitor,
                "now_in_configured_timezone",
                return_value=current_time,
            ), patch.object(monitor.price_monitor.logger, "error"):
                price_monitor._send_volatility_alert(101_000.0, ["区间波动: 1.00%"])
                await price_monitor.flush_notification_tasks()

        asyncio.run(exercise())

        self.assertIsNone(price_monitor.last_volatility_notification_time)

    def test_volume_false_send_result_does_not_advance_cooldown(self) -> None:
        class FalseNotifier(StubNotifier):
            def send_message(self, message: str) -> bool:
                return False

        notifier = FalseNotifier()
        price_monitor = self._build_price_monitor(notifier)
        current_time = datetime(2026, 3, 6, tzinfo=timezone.utc)
        price_monitor.volume_history.extend(
            [
                monitor.price_monitor.VolumeData(100_000.0, 100.0, current_time),
                monitor.price_monitor.VolumeData(100_500.0, 100.0, current_time),
            ]
        )

        async def exercise() -> None:
            with patch.object(
                monitor.price_monitor,
                "now_in_configured_timezone",
                return_value=current_time,
            ), patch.object(monitor.price_monitor.logger, "error"):
                price_monitor.check_volume_anomaly(101_000.0, 20_000.0)
                await price_monitor.flush_notification_tasks()

        asyncio.run(exercise())

        self.assertIsNone(price_monitor.last_volume_alert_time)


class AlertMessagePresenterRegressionTests(unittest.TestCase):
    def test_alert_message_module_exposes_renderers_for_price_monitor_notifications(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("monitor.alerts"))

        alerts = importlib.import_module("monitor.alerts")
        render_milestone_alert = getattr(alerts, "render_milestone_alert", None)
        render_volatility_alert = getattr(alerts, "render_volatility_alert", None)
        render_volume_alert = getattr(alerts, "render_volume_alert", None)

        self.assertTrue(callable(render_milestone_alert))
        self.assertTrue(callable(render_volatility_alert))
        self.assertTrue(callable(render_volume_alert))

    def test_render_milestone_alert_preserves_existing_message_text(self) -> None:
        alerts = importlib.import_module("monitor.alerts")
        message = alerts.render_milestone_alert(
            symbol="BTC<USDT>",
            current_price=101_000.0,
            is_up=True,
            current_time=datetime(2026, 3, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(
            message,
            "🎉🎉【价格里程碑】🎉🎉\n"
            "🪙 BTC&lt;USDT&gt;\n"
            "💰 价格: $101,000.00\n"
            "📈 突破方向: 向上 ↑\n"
            "⏱️ 2026-03-06 00:00:00",
        )

    def test_render_volatility_alert_preserves_existing_message_text(self) -> None:
        alerts = importlib.import_module("monitor.alerts")
        message = alerts.render_volatility_alert(
            symbol="BTC<USDT>",
            current_price=101_000.0,
            volatility_window=180,
            sample_count=2,
            reasons=["区间波动: 1.00%"],
            change_percent=1.0,
            current_time=datetime(2026, 3, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(
            message,
            "⚠️⚠️【波动警报】⚠️⚠️\n"
            "━━━━━━━━━━━━━━━━━\n"
            "🪙 BTC&lt;USDT&gt;\n"
            "💰 当前: $101,000.00\n"
            "📊 时间窗口: 180s (2 pts)\n"
            "⚡️ 触发指标: 区间波动: 1.00%\n"
            "📈 净变化: +1.00%\n"
            "⏱️ 2026-03-06 00:00:00\n"
            "━━━━━━━━━━━━━━━━━",
        )

    def test_render_volume_alert_preserves_existing_message_text(self) -> None:
        alerts = importlib.import_module("monitor.alerts")
        message = alerts.render_volume_alert(
            symbol="BTC<USDT>",
            current_price=101_000.0,
            price_change_pct=1.0,
            volume_multiplier=10.0,
            current_volume=20_000.0,
            avg_volume=2_000.0,
            current_time=datetime(2026, 3, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(
            message,
            "🚨🚨【成交量异常警报】🚨🚨\n"
            "━━━━━━━━━━━━━━━━━\n"
            "🪙 BTC&lt;USDT&gt;\n"
            "💰 当前价格: $101,000.00\n"
            "📈 价格变化: +1.00%\n"
            "📊 成交量暴增: 10.0x\n"
            "📈 当前成交量: 20,000\n"
            "📊 基准成交量: 2,000\n"
            "⏱️ 2026-03-06 00:00:00\n"
            "━━━━━━━━━━━━━━━━━",
        )


class WebSocketRuntimeMessageRegressionTests(unittest.TestCase):
    def test_runtime_message_module_exposes_ws_monitor_renderers(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("monitor.runtime_messages"))

        runtime_messages = importlib.import_module("monitor.runtime_messages")
        self.assertTrue(callable(getattr(runtime_messages, "render_shutdown_notification", None)))
        self.assertTrue(callable(getattr(runtime_messages, "render_disconnect_alert", None)))
        self.assertTrue(callable(getattr(runtime_messages, "render_reconnect_alert", None)))
        self.assertTrue(callable(getattr(runtime_messages, "render_realtime_updates_block", None)))

    def test_render_shutdown_notification_preserves_existing_message_text(self) -> None:
        runtime_messages = importlib.import_module("monitor.runtime_messages")
        message = runtime_messages.render_shutdown_notification(
            current_time=datetime(2026, 3, 6, tzinfo=timezone.utc),
            uptime="1h 2m",
            monitor_count=3,
        )

        self.assertEqual(
            message,
            "👋 <b>加密货币价格监控已停止</b>\n"
            "⏱️ 2026-03-06 00:00:00\n"
            "⌛ 运行时间: 1h 2m\n"
            "🪙 监控币种: 3 个\n"
            "📊 状态: 优雅关闭",
        )

    def test_render_disconnect_alert_preserves_existing_message_text(self) -> None:
        runtime_messages = importlib.import_module("monitor.runtime_messages")
        message = runtime_messages.render_disconnect_alert(
            reason="test disconnect",
            current_time=datetime(2026, 3, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(
            message,
            "🚨🚨【连接断开警报】🚨🚨\n"
            "━━━━━━━━━━━━━━━━━\n"
            "⚠️ 价格监控连接已中断！\n"
            "📡 连接状态: 已断开\n"
            "🔍 断开原因: test disconnect\n"
            "⏱️ 2026-03-06 00:00:00\n"
            "━━━━━━━━━━━━━━━━━\n"
            "💡 系统正在尝试自动重连...",
        )

    def test_render_reconnect_alert_preserves_existing_message_text(self) -> None:
        runtime_messages = importlib.import_module("monitor.runtime_messages")
        message = runtime_messages.render_reconnect_alert(
            attempt_count=2,
            downtime="15秒",
            current_time=datetime(2026, 3, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(
            message,
            "✅✅【连接恢复通知】✅✅\n"
            "━━━━━━━━━━━━━━━━━\n"
            "📡 价格监控已恢复正常\n"
            "🔄 重连次数: 2 次\n"
            "⏱️ 中断时长: 15秒\n"
            "⏱️ 2026-03-06 00:00:00\n"
            "━━━━━━━━━━━━━━━━━",
        )

    def test_render_realtime_updates_block_preserves_existing_console_text(self) -> None:
        runtime_messages = importlib.import_module("monitor.runtime_messages")
        block = runtime_messages.render_realtime_updates_block(
            timestamp="12:34:56",
            updates=["BTC $100,000", "ETH $3,000"],
        )

        self.assertEqual(
            block,
            "[12:34:56] 实时更新:\n"
            "  BTC $100,000\n"
            "  ETH $3,000\n",
        )


class ConfigManagerRegressionTests(unittest.TestCase):
    def test_config_manager_reads_stablecoin_depeg_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "STABLECOIN_DEPEG_MONITOR_ENABLED": "true",
                "STABLECOIN_DEPEG_TOP_N": "12",
                "STABLECOIN_DEPEG_THRESHOLD_PERCENT": "7.5",
                "STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS": "90",
                "STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS": "1800",
            },
            clear=True,
        ), patch("common.config.load_environment"):
            config = ConfigManager()

        self.assertTrue(config.stablecoin_depeg_monitor_enabled)
        self.assertEqual(config.stablecoin_depeg_top_n, 12)
        self.assertEqual(config.stablecoin_depeg_threshold_percent, 7.5)
        self.assertEqual(config.stablecoin_depeg_poll_interval_seconds, 90)
        self.assertEqual(config.stablecoin_depeg_alert_cooldown_seconds, 1800)

    def test_config_manager_falls_back_to_stablecoin_depeg_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("common.config.load_environment"):
            config = ConfigManager()

        self.assertTrue(config.stablecoin_depeg_monitor_enabled)
        self.assertEqual(config.stablecoin_depeg_top_n, 25)
        self.assertEqual(config.stablecoin_depeg_threshold_percent, 5.0)
        self.assertEqual(config.stablecoin_depeg_poll_interval_seconds, 60)
        self.assertEqual(config.stablecoin_depeg_alert_cooldown_seconds, 300)

    def test_config_manager_reads_runtime_notification_and_heartbeat_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "token-from-config",
                "TELEGRAM_CHAT_ID": "chat-from-config",
                "BOT_HEARTBEAT_FILE": "/tmp/custom-bot-heartbeat",
                "BOT_HEARTBEAT_INTERVAL_SECONDS": "45",
                "MONITOR_HEARTBEAT_FILE": "/tmp/custom-monitor-heartbeat",
            },
            clear=True,
        ), patch("common.config.load_environment"):
            config = ConfigManager()

        self.assertEqual(config.telegram_bot_token, "token-from-config")
        self.assertEqual(config.telegram_chat_id, "chat-from-config")
        self.assertEqual(config.bot_heartbeat_file, "/tmp/custom-bot-heartbeat")
        self.assertEqual(config.bot_heartbeat_interval_seconds, 45.0)
        self.assertEqual(config.monitor_heartbeat_file, "/tmp/custom-monitor-heartbeat")


class DefiLlamaClientRegressionTests(unittest.TestCase):
    def test_defillama_client_parses_top_stablecoins(self) -> None:
        from common.clients.defillama import DefiLlamaClient, StablecoinSnapshot

        payload = {
            "peggedAssets": [
                {"name": "USDC", "symbol": "USDC", "price": 0.943, "circulating": 1_000},
                {"name": "USDT", "symbol": "USDT", "price": 1.0, "circulating": 2_000},
                {"name": "DAI", "symbol": "DAI", "price": 0.998, "circulating": 500},
            ]
        }

        client = DefiLlamaClient()
        snapshots = client.parse_stablecoins(payload, top_n=2)

        self.assertEqual(
            snapshots,
            [
                StablecoinSnapshot(name="USDT", symbol="USDT", price=1.0, circulating=2000.0, rank=1),
                StablecoinSnapshot(name="USDC", symbol="USDC", price=0.943, circulating=1000.0, rank=2),
            ],
        )

    def test_defillama_client_excludes_usyc_and_usdy_before_top_n_ranking(self) -> None:
        from common.clients.defillama import DefiLlamaClient

        payload = {
            "peggedAssets": [
                {"name": "Circle USYC", "symbol": "USYC", "price": 1.02, "circulating": 5000},
                {"name": "Ondo US Dollar Yield", "symbol": "USDY", "price": 1.01, "circulating": 4000},
                {"name": "Tether", "symbol": "USDT", "price": 1.0, "circulating": 3000},
                {"name": "USDC", "symbol": "USDC", "price": 1.0, "circulating": 2000},
                {"name": "DAI", "symbol": "DAI", "price": 1.0, "circulating": 1000},
            ]
        }

        client = DefiLlamaClient()
        snapshots = client.parse_stablecoins(payload, top_n=3)

        self.assertEqual([snapshot.symbol for snapshot in snapshots], ["USDT", "USDC", "DAI"])
        self.assertEqual([snapshot.rank for snapshot in snapshots], [1, 2, 3])

    def test_defillama_client_skips_invalid_entries(self) -> None:
        from common.clients.defillama import DefiLlamaClient

        payload = {
            "peggedAssets": [
                {"name": "USDC", "symbol": "USDC", "price": 0.943, "circulating": 1_000},
                {"name": None, "symbol": "BAD", "price": "oops", "circulating": 100},
                {"name": "MISS", "price": 1.0, "circulating": 10},
            ]
        }

        client = DefiLlamaClient()
        snapshots = client.parse_stablecoins(payload, top_n=5)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].symbol, "USDC")
        self.assertEqual(snapshots[0].rank, 1)

    def test_defillama_client_parses_circulating_dict_from_live_payload_shape(self) -> None:
        from common.clients.defillama import DefiLlamaClient, StablecoinSnapshot

        payload = {
            "peggedAssets": [
                {
                    "name": "Tether",
                    "symbol": "USDT",
                    "price": 0.9999,
                    "circulating": {"peggedUSD": 184189819394.69513},
                },
                {
                    "name": "USDC",
                    "symbol": "USDC",
                    "price": 1.0001,
                    "circulating": {"peggedUSD": 60123456789.0},
                },
            ]
        }

        client = DefiLlamaClient()
        snapshots = client.parse_stablecoins(payload, top_n=2)

        self.assertEqual(
            snapshots,
            [
                StablecoinSnapshot(
                    name="Tether",
                    symbol="USDT",
                    price=0.9999,
                    circulating=184189819394.69513,
                    rank=1,
                ),
                StablecoinSnapshot(
                    name="USDC",
                    symbol="USDC",
                    price=1.0001,
                    circulating=60123456789.0,
                    rank=2,
                ),
            ],
        )


class DefiLlamaClientAsyncRegressionTests(unittest.TestCase):
    def test_defillama_client_fetch_stablecoins_uses_async_session(self) -> None:
        from common.clients.defillama import DefiLlamaClient, StablecoinSnapshot

        payload = {
            "peggedAssets": [
                {"name": "USDC", "symbol": "USDC", "price": 0.999, "circulating": 1_500},
                {"name": "USDT", "symbol": "USDT", "price": 1.0, "circulating": 2_000},
                {"name": "DAI", "symbol": "DAI", "price": 1.001, "circulating": 500},
            ]
        }
        response = AsyncMock()
        response.__aenter__.return_value = response
        response.raise_for_status = Mock()
        response.json = AsyncMock(return_value=payload)
        session = Mock()
        session.get.return_value = response

        client = DefiLlamaClient(timeout=7.5)
        client.session = session

        coroutine = client.fetch_stablecoins(top_n=2)
        self.assertTrue(inspect.isawaitable(coroutine))

        snapshots = asyncio.run(coroutine)

        self.assertEqual(
            snapshots,
            [
                StablecoinSnapshot(name="USDT", symbol="USDT", price=1.0, circulating=2000.0, rank=1),
                StablecoinSnapshot(name="USDC", symbol="USDC", price=0.999, circulating=1500.0, rank=2),
            ],
        )
        session.get.assert_called_once_with("https://stablecoins.llama.fi/stablecoins")
        response.raise_for_status.assert_called_once_with()
        response.json.assert_awaited_once_with()

    def test_defillama_client_fetch_stablecoins_raises_on_invalid_payload(self) -> None:
        from common.clients.defillama import DefiLlamaClient

        response = AsyncMock()
        response.__aenter__.return_value = response
        response.raise_for_status = Mock()
        response.json = AsyncMock(return_value={"oops": []})
        session = Mock()
        session.get.return_value = response

        client = DefiLlamaClient()
        client.session = session

        with self.assertRaises(ValueError):
            asyncio.run(client.fetch_stablecoins(top_n=3))

        response.raise_for_status.assert_called_once_with()
        response.json.assert_awaited_once_with()

    def test_defillama_client_close_awaits_session_close(self) -> None:
        from common.clients.defillama import DefiLlamaClient

        session = AsyncMock()
        client = DefiLlamaClient()
        client.session = session

        asyncio.run(client.close())

        session.close.assert_awaited_once_with()
        self.assertIsNone(client.session)


class StablecoinDepegMonitorRegressionTests(unittest.TestCase):
    def _build_stablecoin_monitor(self, notifier: StubNotifier, *, threshold_percent: float = 5.0, cooldown_seconds: int = 3600):
        from monitor.stablecoin_depeg_monitor import StablecoinDepegMonitor

        config = types.SimpleNamespace(
            stablecoin_depeg_threshold_percent=threshold_percent,
            stablecoin_depeg_alert_cooldown_seconds=cooldown_seconds,
            stablecoin_depeg_top_n=25,
            stablecoin_depeg_poll_interval_seconds=300,
        )
        return StablecoinDepegMonitor(config=config, notifier=notifier, client=object())

    def test_stablecoin_monitor_does_not_alert_within_threshold(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        notifier = StubNotifier()
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)

        within_upper = StablecoinSnapshot("USDX", "USDX", 1.049, 1000.0, 1)
        within_lower = StablecoinSnapshot("USDY", "USDY", 0.951, 900.0, 2)

        self.assertFalse(stablecoin_monitor.evaluate_snapshot(within_upper))
        self.assertFalse(stablecoin_monitor.evaluate_snapshot(within_lower))
        self.assertEqual(notifier.messages, [])

    def test_stablecoin_monitor_alerts_when_price_exceeds_upper_threshold(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        notifier = StubNotifier()
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)

        snapshot = StablecoinSnapshot("USDX", "USDX", 1.051, 1000.0, 1)

        self.assertTrue(stablecoin_monitor.evaluate_snapshot(snapshot))
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("USDX", notifier.messages[0])
        self.assertIn("+5.10%", notifier.messages[0])

    def test_stablecoin_monitor_alerts_when_price_exceeds_lower_threshold(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        notifier = StubNotifier()
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)

        snapshot = StablecoinSnapshot("USDX", "USDX", 0.949, 1000.0, 1)

        self.assertTrue(stablecoin_monitor.evaluate_snapshot(snapshot))
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("-5.10%", notifier.messages[0])

    def test_stablecoin_monitor_escapes_html_special_characters_in_alert_message(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        notifier = StubNotifier()
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)

        snapshot = StablecoinSnapshot("USD <One> & Co", "USD<T>", 1.051, 1000.0, 1)

        self.assertTrue(stablecoin_monitor.evaluate_snapshot(snapshot))
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("USD &lt;One&gt; &amp; Co", notifier.messages[0])
        self.assertIn("USD&lt;T&gt;", notifier.messages[0])
        self.assertNotIn("USD <One> & Co", notifier.messages[0])
        self.assertNotIn("USD<T>", notifier.messages[0])

    def test_stablecoin_monitor_respects_per_coin_cooldown(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        notifier = StubNotifier()
        stablecoin_monitor = self._build_stablecoin_monitor(notifier, cooldown_seconds=3600)
        start_time = datetime(2026, 3, 22, tzinfo=timezone.utc)
        clock = FakeClock(start_time)
        snapshot = StablecoinSnapshot("USDX", "USDX", 0.949, 1000.0, 1)

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now):
            self.assertTrue(stablecoin_monitor.evaluate_snapshot(snapshot))
            clock.current = start_time + timedelta(seconds=60)
            self.assertFalse(stablecoin_monitor.evaluate_snapshot(snapshot))

        self.assertEqual(len(notifier.messages), 1)

    def test_stablecoin_monitor_resets_after_returning_to_normal(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        notifier = StubNotifier()
        stablecoin_monitor = self._build_stablecoin_monitor(notifier, cooldown_seconds=3600)
        start_time = datetime(2026, 3, 22, tzinfo=timezone.utc)
        clock = FakeClock(start_time)
        depegged = StablecoinSnapshot("USDX", "USDX", 0.949, 1000.0, 1)
        recovered = StablecoinSnapshot("USDX", "USDX", 1.0, 1000.0, 1)

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now):
            self.assertTrue(stablecoin_monitor.evaluate_snapshot(depegged))
            clock.current = start_time + timedelta(seconds=60)
            self.assertFalse(stablecoin_monitor.evaluate_snapshot(recovered))
            clock.current = start_time + timedelta(seconds=120)
            self.assertTrue(stablecoin_monitor.evaluate_snapshot(depegged))

        self.assertEqual(len(notifier.messages), 2)


class StablecoinDepegMonitorPollingTests(unittest.TestCase):
    def _build_stablecoin_monitor(self, notifier, client, *, top_n: int = 2):
        from monitor.stablecoin_depeg_monitor import StablecoinDepegMonitor

        config = types.SimpleNamespace(
            stablecoin_depeg_threshold_percent=5.0,
            stablecoin_depeg_alert_cooldown_seconds=3600,
            stablecoin_depeg_top_n=top_n,
            stablecoin_depeg_poll_interval_seconds=300,
        )
        return StablecoinDepegMonitor(config=config, notifier=notifier, client=client)

    def test_stablecoin_monitor_processes_top_n_snapshots_from_client(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        notifier = StubNotifier()
        calls = []

        async def fetch_stablecoins(top_n: int):
            calls.append(top_n)
            snapshots = [
                StablecoinSnapshot("USDT", "USDT", 1.0, 2000.0, 1),
                StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 2),
                StablecoinSnapshot("DAI", "DAI", 0.93, 500.0, 3),
            ]
            return snapshots[:top_n]

        client = types.SimpleNamespace(fetch_stablecoins=fetch_stablecoins)
        stablecoin_monitor = self._build_stablecoin_monitor(notifier, client, top_n=2)

        alerts = asyncio.run(stablecoin_monitor.run_once())

        self.assertEqual(calls, [2])
        self.assertEqual(alerts, 1)
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("USDC", notifier.messages[0])

    def test_stablecoin_monitor_skips_failed_poll_and_continues(self) -> None:
        notifier = StubNotifier()
        calls = []

        async def fetch_stablecoins(top_n: int):
            calls.append(top_n)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return []

        async def fake_sleep(seconds: int) -> None:
            if len(calls) >= 2:
                raise asyncio.CancelledError

        client = types.SimpleNamespace(fetch_stablecoins=fetch_stablecoins)
        stablecoin_monitor = self._build_stablecoin_monitor(notifier, client, top_n=2)

        with patch("monitor.stablecoin_depeg_monitor.asyncio.sleep", side_effect=fake_sleep), \
             patch("monitor.stablecoin_depeg_monitor.logger.error") as mock_error:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(stablecoin_monitor.run())

        self.assertEqual(calls, [2, 2])
        self.assertEqual(notifier.messages, [])
        mock_error.assert_called_once()


class StablecoinDepegMonitorAsyncPollingTests(unittest.TestCase):
    def _build_stablecoin_monitor(self, notifier, client, *, top_n: int = 2):
        from monitor.stablecoin_depeg_monitor import StablecoinDepegMonitor

        config = types.SimpleNamespace(
            stablecoin_depeg_threshold_percent=5.0,
            stablecoin_depeg_alert_cooldown_seconds=3600,
            stablecoin_depeg_top_n=top_n,
            stablecoin_depeg_poll_interval_seconds=300,
        )
        return StablecoinDepegMonitor(config=config, notifier=notifier, client=client)

    def test_stablecoin_monitor_run_once_awaits_async_client_and_sends_alerts(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))
        client_calls = []

        async def fetch_stablecoins(top_n: int):
            client_calls.append(top_n)
            return [StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 1)]

        stablecoin_monitor = self._build_stablecoin_monitor(
            notifier,
            types.SimpleNamespace(fetch_stablecoins=fetch_stablecoins),
        )

        with patch("monitor.stablecoin_depeg_monitor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            async def invoke(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = invoke
            alerts = asyncio.run(stablecoin_monitor.run_once())

        self.assertEqual(client_calls, [2])
        self.assertEqual(alerts, 1)
        mock_to_thread.assert_awaited_once()
        notifier.send_message.assert_called_once()

    def test_stablecoin_monitor_run_continues_after_failed_async_poll(self) -> None:
        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))
        calls = []

        async def fetch_stablecoins(top_n: int):
            calls.append(top_n)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return []

        async def fake_sleep(seconds: int) -> None:
            if len(calls) >= 2:
                raise asyncio.CancelledError

        stablecoin_monitor = self._build_stablecoin_monitor(
            notifier,
            types.SimpleNamespace(fetch_stablecoins=fetch_stablecoins),
        )

        with patch("monitor.stablecoin_depeg_monitor.asyncio.sleep", side_effect=fake_sleep), \
             patch("monitor.stablecoin_depeg_monitor.logger.error") as mock_error:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(stablecoin_monitor.run())

        self.assertEqual(calls, [2, 2])
        mock_error.assert_called_once()

    def test_stablecoin_monitor_run_once_logs_successful_poll_summary(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))

        async def fetch_stablecoins(top_n: int):
            return [
                StablecoinSnapshot("USDT", "USDT", 1.0, 2000.0, 1),
                StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 2),
            ]

        stablecoin_monitor = self._build_stablecoin_monitor(
            notifier,
            types.SimpleNamespace(fetch_stablecoins=fetch_stablecoins),
        )

        with patch("monitor.stablecoin_depeg_monitor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread, \
             patch("monitor.stablecoin_depeg_monitor.logger.info") as mock_info:
            async def invoke(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = invoke
            alerts = asyncio.run(stablecoin_monitor.run_once())

        self.assertEqual(alerts, 1)
        mock_info.assert_called_once_with("Stablecoin poll completed: snapshots=2, alerts=1")

    def test_stablecoin_monitor_run_propagates_cancelled_error(self) -> None:
        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))

        async def fetch_stablecoins(top_n: int):
            raise asyncio.CancelledError

        stablecoin_monitor = self._build_stablecoin_monitor(
            notifier,
            types.SimpleNamespace(fetch_stablecoins=fetch_stablecoins),
        )

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(stablecoin_monitor.run_once())


class FakeCompletedTask:
    def __init__(self) -> None:
        self.cancel_called = False
        self.awaited = False

    def cancel(self) -> None:
        self.cancel_called = True

    def result(self):
        return None

    def __await__(self):
        async def _wait() -> None:
            self.awaited = True
            if self.cancel_called:
                raise asyncio.CancelledError

        return _wait().__await__()


class WebSocketMultiCoinMonitorStablecoinIntegrationTests(unittest.TestCase):
    def test_ws_monitor_awaits_stablecoin_client_close_in_finally(self) -> None:
        stablecoin_client = AsyncMock()

        class DummyWsClient:
            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

        def fake_create_task(coro):
            name = coro.cr_code.co_name
            coro.close()
            if name == "start":
                return FakeCompletedTask()
            if name == "wait":
                return FakeCompletedTask()
            return FakeCompletedTask()

        with patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=DummyWsClient()), \
             patch("monitor.ws_monitor.DefiLlamaClient", return_value=stablecoin_client), \
             patch("monitor.ws_monitor.asyncio.create_task", side_effect=fake_create_task), \
             patch("monitor.ws_monitor.asyncio.wait", return_value=(set(), set())), \
             patch("monitor.ws_monitor.StablecoinDepegMonitor") as mock_stablecoin_monitor_cls:
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True

            async def run() -> None:
                return None

            mock_stablecoin_monitor_cls.return_value.run = run

            from monitor.ws_monitor import WebSocketMultiCoinMonitor

            ws_monitor = WebSocketMultiCoinMonitor(self._build_config(stablecoin_enabled=True))
            asyncio.run(ws_monitor.run())

        stablecoin_client.close.assert_awaited_once_with()

    def _build_config(self, *, stablecoin_enabled: bool):
        coin = CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1000.0,
            volatility_percent=5.0,
            volatility_window=180,
            volume_alert_multiplier=10.0,
        )
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
            stablecoin_depeg_monitor_enabled=stablecoin_enabled,
            stablecoin_depeg_threshold_percent=5.0,
            stablecoin_depeg_alert_cooldown_seconds=3600,
            stablecoin_depeg_top_n=25,
            stablecoin_depeg_poll_interval_seconds=300,
        )

    def test_ws_monitor_starts_stablecoin_task_when_enabled(self) -> None:
        created_task_names = []
        stablecoin_client = AsyncMock()

        def fake_create_task(coro):
            created_task_names.append(coro.cr_code.co_name)
            coro.close()
            return FakeCompletedTask()

        class DummyWsClient:
            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

        with patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=DummyWsClient()), \
             patch("monitor.ws_monitor.DefiLlamaClient", return_value=stablecoin_client), \
             patch("monitor.ws_monitor.asyncio.create_task", side_effect=fake_create_task), \
             patch("monitor.ws_monitor.asyncio.wait", return_value=(set(), set())), \
             patch("monitor.ws_monitor.StablecoinDepegMonitor") as mock_stablecoin_monitor_cls:
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True

            async def run() -> None:
                return None

            mock_stablecoin_monitor_cls.return_value.run = run

            from monitor.ws_monitor import WebSocketMultiCoinMonitor

            ws_monitor = WebSocketMultiCoinMonitor(self._build_config(stablecoin_enabled=True))
            asyncio.run(ws_monitor.run())

        self.assertGreaterEqual(created_task_names.count("run"), 1)
        mock_stablecoin_monitor_cls.assert_called_once()
        stablecoin_client.close.assert_awaited_once_with()

    def test_ws_monitor_does_not_start_stablecoin_task_when_disabled(self) -> None:
        created_task_names = []

        def fake_create_task(coro):
            created_task_names.append(coro.cr_code.co_name)
            coro.close()
            return FakeCompletedTask()

        class DummyWsClient:
            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

        with patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=DummyWsClient()), \
             patch("monitor.ws_monitor.DefiLlamaClient") as mock_defillama_client_cls, \
             patch("monitor.ws_monitor.asyncio.create_task", side_effect=fake_create_task), \
             patch("monitor.ws_monitor.asyncio.wait", return_value=(set(), set())), \
             patch("monitor.ws_monitor.StablecoinDepegMonitor") as mock_stablecoin_monitor_cls:
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True

            from monitor.ws_monitor import WebSocketMultiCoinMonitor

            ws_monitor = WebSocketMultiCoinMonitor(self._build_config(stablecoin_enabled=False))
            asyncio.run(ws_monitor.run())

        self.assertEqual(created_task_names.count("run"), 0)
        mock_stablecoin_monitor_cls.assert_not_called()
        mock_defillama_client_cls.assert_not_called()

    def test_ws_monitor_cancels_stablecoin_task_on_shutdown(self) -> None:
        class FakeTask:
            def __init__(self) -> None:
                self.cancel_called = False
                self.awaited = False

            def cancel(self) -> None:
                self.cancel_called = True

            def result(self):
                return None

            def __await__(self):
                async def _wait() -> None:
                    self.awaited = True
                    if self.cancel_called:
                        raise asyncio.CancelledError

                return _wait().__await__()

        stablecoin_task = FakeTask()
        shutdown_task = FakeHeartbeatTask()
        ws_task = FakeHeartbeatTask()
        stablecoin_client = AsyncMock()

        def fake_create_task(coro):
            name = coro.cr_code.co_name
            coro.close()
            if name == "run":
                return stablecoin_task
            if name == "start":
                return ws_task
            return shutdown_task

        class DummyWsClient:
            async def start(self) -> None:
                return None

            async def stop(self) -> None:
                return None

            def get_statistics(self) -> dict:
                return {}

        with patch("monitor.ws_monitor.TelegramNotifier") as mock_notifier_cls, \
             patch("monitor.ws_monitor.BinanceWebSocketClient", return_value=DummyWsClient()), \
             patch("monitor.ws_monitor.DefiLlamaClient", return_value=stablecoin_client), \
             patch("monitor.ws_monitor.asyncio.create_task", side_effect=fake_create_task), \
             patch("monitor.ws_monitor.asyncio.wait", return_value=({shutdown_task}, {ws_task, stablecoin_task})), \
             patch("monitor.ws_monitor.StablecoinDepegMonitor") as mock_stablecoin_monitor_cls:
            notifier = mock_notifier_cls.return_value
            notifier.test_connection.return_value = True
            notifier.send_message.return_value = True

            async def run() -> None:
                return None

            mock_stablecoin_monitor_cls.return_value.run = run

            from monitor.ws_monitor import WebSocketMultiCoinMonitor

            ws_monitor = WebSocketMultiCoinMonitor(self._build_config(stablecoin_enabled=True))
            ws_monitor._shutdown_event.set()
            asyncio.run(ws_monitor.run())

        self.assertTrue(stablecoin_task.cancel_called)
        self.assertTrue(stablecoin_task.awaited)
        stablecoin_client.close.assert_awaited_once_with()


class TelegramNotifierRegressionTests(unittest.TestCase):
    def test_send_message_uses_session_post_without_storing_base_url(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")

        with patch.object(notifier.session, "post", return_value=DummyResponse()) as mock_post:
            self.assertTrue(notifier.send_message("hello"))

        self.assertFalse(hasattr(notifier, "base_url"))
        mock_post.assert_called_once()
        self.assertIn("/bottoken/sendMessage", mock_post.call_args.args[0])

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

        with patch.object(notifier.session, "post", side_effect=fake_post), \
             patch("common.notifications.logger.warning"):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(post_call_count, 1)

    def test_repr_redacts_token(self) -> None:
        notifier = TelegramNotifier(bot_token="secret123", chat_id="chat")
        rendered = repr(notifier)

        self.assertIn("TelegramNotifier", rendered)
        self.assertIn("***", rendered)
        self.assertNotIn("secret123", rendered)


class WebSocketParserRegressionTests(unittest.TestCase):
    def test_websocket_parser_module_exposes_message_parsers(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("common.clients.websocket_parser"))

        parser = importlib.import_module("common.clients.websocket_parser")
        self.assertTrue(callable(getattr(parser, "parse_ticker_message", None)))
        self.assertTrue(callable(getattr(parser, "parse_kline_message", None)))

    def test_parse_ticker_message_preserves_combined_stream_shape(self) -> None:
        parser = importlib.import_module("common.clients.websocket_parser")
        symbol, price = parser.parse_ticker_message(
            {
                "stream": "btcusdt@ticker",
                "data": {"e": "24hrTicker", "s": "BTCUSDT", "c": "95123.45"},
            }
        )

        self.assertEqual(symbol, "BTCUSDT")
        self.assertEqual(price, 95123.45)

    def test_parse_kline_message_preserves_closed_kline_shape(self) -> None:
        parser = importlib.import_module("common.clients.websocket_parser")
        kline_data = parser.parse_kline_message(
            {
                "stream": "btcusdt@kline_1m",
                "data": {
                    "e": "kline",
                    "s": "BTCUSDT",
                    "k": {"s": "BTCUSDT", "c": "1.23", "v": "4.56", "x": True},
                },
            }
        )

        self.assertEqual(kline_data, ("BTCUSDT", 1.23, 4.56, True))

    def test_parse_kline_message_returns_none_for_non_kline_events(self) -> None:
        parser = importlib.import_module("common.clients.websocket_parser")
        kline_data = parser.parse_kline_message(
            {"stream": "btcusdt@ticker", "data": {"e": "24hrTicker", "s": "BTCUSDT", "c": "95123.45"}}
        )

        self.assertIsNone(kline_data)


class BinanceWebSocketClientRegressionTests(unittest.TestCase):
    def test_message_handler_transitions_to_reconnecting_when_stream_ends_cleanly(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        client = monitor.BinanceWebSocketClient(["BTCUSDT"], on_price)
        client.state = ConnectionState.CONNECTED
        client.websocket = FakeAsyncIterableWebSocket([])

        asyncio.run(client._message_handler())

        self.assertEqual(client.state, ConnectionState.RECONNECTING)

    def test_message_handler_keeps_stopped_state_when_stop_event_is_set_before_clean_end(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        client = monitor.BinanceWebSocketClient(["BTCUSDT"], on_price)
        client.state = ConnectionState.STOPPED
        client._stop_event.set()
        client.websocket = FakeAsyncIterableWebSocket([])

        asyncio.run(client._message_handler())

        self.assertEqual(client.state, ConnectionState.STOPPED)

    def test_message_handler_drops_closed_kline_with_missing_symbol(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        on_kline = AsyncMock()

        client = monitor.BinanceWebSocketClient(
            ["BTCUSDT"],
            on_price,
            on_kline_callback=on_kline,
        )
        client.websocket = FakeAsyncIterableWebSocket(
            ['{"e": "kline", "k": {"c": "1.23", "v": "4.56", "x": true}}']
        )

        with patch("common.clients.websocket.logger.error") as mock_error:
            asyncio.run(client._message_handler())

        on_kline.assert_not_awaited()
        mock_error.assert_any_call("Failed to parse kline message: Kline message missing valid symbol")

    def test_message_handler_logs_subscription_confirmation_with_kline_callback(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        async def on_kline(symbol: str, price: float, volume: float, is_closed: bool) -> None:
            return None

        client = monitor.BinanceWebSocketClient(
            ["BTCUSDT"],
            on_price,
            on_kline_callback=on_kline,
        )
        client.websocket = FakeAsyncIterableWebSocket(['{"result": null, "id": 1}'])

        with patch("common.clients.websocket.logger.info") as mock_info:
            asyncio.run(client._message_handler())

        mock_info.assert_any_call("Subscription confirmed: {'result': None, 'id': 1}")

    def test_message_handler_logs_error_message_with_kline_callback(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        async def on_kline(symbol: str, price: float, volume: float, is_closed: bool) -> None:
            return None

        client = monitor.BinanceWebSocketClient(
            ["BTCUSDT"],
            on_price,
            on_kline_callback=on_kline,
        )
        client.websocket = FakeAsyncIterableWebSocket(['{"code": 400, "msg": "bad request"}'])

        with patch("common.clients.websocket.logger.error") as mock_error:
            asyncio.run(client._message_handler())

        mock_error.assert_any_call("Binance error: {'code': 400, 'msg': 'bad request'}")

    def test_bad_ticker_payload_is_logged_and_skipped_without_reconnect(self) -> None:
        received = []

        async def on_price(symbol: str, price: float) -> None:
            received.append((symbol, price))

        client = monitor.BinanceWebSocketClient(["BTCUSDT"], on_price)
        client.state = ConnectionState.CONNECTED
        client.websocket = FakeAsyncIterableWebSocket([
            '{"stream":"btcusdt@ticker","data":{"e":"24hrTicker","s":"BTCUSDT"}}',
            '{"stream":"btcusdt@ticker","data":{"e":"24hrTicker","s":"BTCUSDT","c":"95123.45"}}',
        ])
        disconnects = []

        async def on_disconnect(reason: str) -> None:
            disconnects.append(reason)

        client.on_disconnect_callback = on_disconnect

        with patch("common.clients.websocket.logger.error") as mock_error:
            asyncio.run(client._message_handler())

        self.assertEqual(received, [("BTCUSDT", 95123.45)])
        self.assertEqual(disconnects, ["Connection closed cleanly"])
        self.assertEqual(client.state, ConnectionState.RECONNECTING)
        mock_error.assert_any_call("Failed to parse ticker message: 'c'")


class TelegramBotRegressionTests(unittest.TestCase):
    def test_run_async_cleans_up_when_initialize_fails_after_partial_setup(self) -> None:
        config = types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat",
            bot_heartbeat_file="/tmp/bot-heartbeat-test",
            bot_heartbeat_interval_seconds=30.0,
            get_enabled_coins=lambda: [],
        )
        fake_fetcher = object()
        fetcher_context = FakeAsyncContextManager(fake_fetcher)
        heartbeat_task = FakeHeartbeatTask()
        original_sigint = object()
        original_sigterm = object()

        async def fail_initialize() -> None:
            application.initialized = True
            raise RuntimeError("initialize failed")

        application = types.SimpleNamespace(
            initialized=False,
            running=False,
            initialize=AsyncMock(side_effect=fail_initialize),
            start=AsyncMock(),
            stop=AsyncMock(),
            shutdown=AsyncMock(),
            updater=types.SimpleNamespace(
                running=False,
                start_polling=AsyncMock(),
                stop=AsyncMock(),
            ),
        )

        def fake_create_task(coro):
            coro.close()
            return heartbeat_task

        with patch.object(bot.signal, "signal") as mock_signal, \
             patch("bot.app.AsyncBinancePriceFetcher", return_value=fetcher_context), \
             patch("bot.app.asyncio.create_task", side_effect=fake_create_task):
            mock_signal.side_effect = [original_sigint, original_sigterm, None, None]
            telegram_bot = bot.TelegramBot(config)
            telegram_bot.application = application

            with self.assertRaisesRegex(RuntimeError, "initialize failed"):
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(telegram_bot.run_async())
                finally:
                    loop.close()

        self.assertIs(telegram_bot.fetcher, fake_fetcher)
        self.assertTrue(fetcher_context.entered)
        self.assertTrue(fetcher_context.exited)
        self.assertTrue(heartbeat_task.cancel_called)
        self.assertTrue(heartbeat_task.awaited)
        application.initialize.assert_awaited_once_with()
        application.start.assert_not_awaited()
        application.updater.start_polling.assert_not_awaited()
        application.updater.stop.assert_not_awaited()
        application.stop.assert_not_awaited()
        application.shutdown.assert_awaited_once_with()
        self.assertEqual(
            mock_signal.call_args_list,
            [
                unittest.mock.call(bot.signal.SIGINT, telegram_bot._signal_handler),
                unittest.mock.call(bot.signal.SIGTERM, telegram_bot._signal_handler),
                unittest.mock.call(bot.signal.SIGINT, original_sigint),
                unittest.mock.call(bot.signal.SIGTERM, original_sigterm),
            ],
        )

    def test_run_async_cleans_up_when_startup_fails(self) -> None:
        config = types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat",
            bot_heartbeat_file="/tmp/bot-heartbeat-test",
            bot_heartbeat_interval_seconds=30.0,
            get_enabled_coins=lambda: [],
        )
        fake_fetcher = object()
        fetcher_context = FakeAsyncContextManager(fake_fetcher)
        heartbeat_task = FakeHeartbeatTask()
        allowed_updates = object()
        application = types.SimpleNamespace(
            initialize=AsyncMock(),
            start=AsyncMock(),
            stop=AsyncMock(),
            shutdown=AsyncMock(),
            updater=types.SimpleNamespace(
                start_polling=AsyncMock(side_effect=RuntimeError("polling failed")),
                stop=AsyncMock(),
            ),
        )

        def fake_create_task(coro):
            coro.close()
            return heartbeat_task

        with patch.object(bot.signal, "signal", side_effect=lambda signum, handler: handler), \
             patch("bot.app.AsyncBinancePriceFetcher", return_value=fetcher_context), \
             patch("bot.app.Update.ALL_TYPES", new=allowed_updates), \
             patch("bot.app.asyncio.create_task", side_effect=fake_create_task):
            telegram_bot = bot.TelegramBot(config)
            telegram_bot.application = application

            with self.assertRaisesRegex(RuntimeError, "polling failed"):
                asyncio.run(telegram_bot.run_async())

        self.assertIs(telegram_bot.fetcher, fake_fetcher)
        self.assertTrue(fetcher_context.entered)
        self.assertTrue(fetcher_context.exited)
        self.assertTrue(heartbeat_task.cancel_called)
        self.assertTrue(heartbeat_task.awaited)
        application.initialize.assert_awaited_once_with()
        application.start.assert_awaited_once_with()
        application.updater.start_polling.assert_awaited_once_with(
            drop_pending_updates=True,
            allowed_updates=allowed_updates,
        )
        application.updater.stop.assert_not_awaited()
        application.stop.assert_awaited_once_with()
        application.shutdown.assert_awaited_once_with()

    def test_render_all_prices_message_is_localized_in_chinese(self) -> None:
        config = types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat",
            bot_heartbeat_file="/tmp/bot-heartbeat-test",
            bot_heartbeat_interval_seconds=30.0,
            get_enabled_coins=lambda: [],
        )

        with patch.object(bot.signal, "signal", side_effect=lambda signum, handler: handler):
            telegram_bot = bot.TelegramBot(config)

        enabled_coins = [
            CoinConfig(
                coin_name="BTC",
                enabled=True,
                symbol="BTCUSDT",
                integer_threshold=1000.0,
                volatility_percent=3.0,
                volatility_window=180,
                volume_alert_multiplier=10.0,
            ),
        ]
        message = telegram_bot._render_all_prices_message(enabled_coins, {"BTCUSDT": None})

        self.assertIn("当前价格", message)
        self.assertIn("获取失败", message)
        self.assertNotIn("Current Prices", message)

    def test_button_callback_preserves_full_coin_name_after_prefix(self) -> None:
        config = types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat",
            bot_heartbeat_file="/tmp/bot-heartbeat-test",
            bot_heartbeat_interval_seconds=30.0,
            get_enabled_coins=lambda: [],
        )

        with patch.object(bot.signal, "signal", side_effect=lambda signum, handler: handler):
            telegram_bot = bot.TelegramBot(config)

        telegram_bot.send_price_update = AsyncMock()

        query = types.SimpleNamespace(
            data="price_MY_COIN",
            answer=AsyncMock(),
            message=types.SimpleNamespace(chat_id=123),
        )
        update = types.SimpleNamespace(callback_query=query)

        asyncio.run(telegram_bot.button_callback(update, None))

        telegram_bot.send_price_update.assert_awaited_once_with(123, "MY_COIN", message=query.message)


class TelegramBotStablecoinCommandRegressionTests(unittest.TestCase):
    @staticmethod
    def _build_bot() -> bot.TelegramBot:
        config = types.SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id="chat",
            bot_heartbeat_file="/tmp/bot-heartbeat-test",
            bot_heartbeat_interval_seconds=30.0,
            get_enabled_coins=lambda: [],
        )

        with patch.object(bot.signal, "signal", side_effect=lambda signum, handler: handler):
            return bot.TelegramBot(config)

    @staticmethod
    def _build_update(chat_id: int = 123):
        return types.SimpleNamespace(
            effective_chat=types.SimpleNamespace(id=chat_id),
            message=types.SimpleNamespace(chat_id=chat_id, reply_text=AsyncMock()),
        )

    @staticmethod
    def _build_context():
        return types.SimpleNamespace(args=[])

    @staticmethod
    def _build_snapshots():
        from common.clients.defillama import StablecoinSnapshot

        return [
            StablecoinSnapshot("Tether", "USDT", 0.9987, 150_000_000_000.0, 1),
            *[
                StablecoinSnapshot(
                    f"Stablecoin {rank}",
                    f"USD{rank}",
                    1.0 + rank / 10_000,
                    1_000_000.0 - rank,
                    rank,
                )
                for rank in range(2, 27)
            ],
        ]

    def test_stablecoins_command_returns_formatted_top_25_list(self) -> None:
        telegram_bot = self._build_bot()
        telegram_bot._send_or_edit_message = AsyncMock()
        update = self._build_update()
        context = self._build_context()
        stablecoin_client = FakeStablecoinClient(snapshots=self._build_snapshots())

        self.assertTrue(
            hasattr(telegram_bot, "stablecoins_command"),
            "TelegramBot.stablecoins_command is not implemented yet",
        )

        with patch("bot.handlers.DefiLlamaClient", return_value=stablecoin_client, create=True):
            asyncio.run(telegram_bot.stablecoins_command(update, context))

        self.assertEqual(stablecoin_client.calls, [25])
        telegram_bot._send_or_edit_message.assert_awaited_once()
        send_args = telegram_bot._send_or_edit_message.await_args.args
        self.assertEqual(send_args[0], update.effective_chat.id)
        sent_text = send_args[1]
        self.assertIn("前25稳定币价格", sent_text)
        self.assertIn("USDT", sent_text)
        self.assertIn("Tether", sent_text)
        self.assertRegex(sent_text, r"[+-]\d+\.\d+%")

        ranks = [int(rank) for rank in re.findall(r"#(\d+)", sent_text)]
        self.assertEqual(ranks, list(range(1, 26)))
        self.assertNotIn("#26", sent_text)
        self.assertNotIn("Stablecoin 26", sent_text)

    def test_stablecoins_command_returns_error_message_when_fetch_fails(self) -> None:
        telegram_bot = self._build_bot()
        telegram_bot._send_or_edit_message = AsyncMock()
        update = self._build_update()
        context = self._build_context()
        stablecoin_client = FakeStablecoinClient(error=RuntimeError("boom"))

        self.assertTrue(
            hasattr(telegram_bot, "stablecoins_command"),
            "TelegramBot.stablecoins_command is not implemented yet",
        )

        with patch("bot.handlers.DefiLlamaClient", return_value=stablecoin_client, create=True):
            asyncio.run(telegram_bot.stablecoins_command(update, context))

        self.assertEqual(stablecoin_client.calls, [25])
        telegram_bot._send_or_edit_message.assert_awaited_once()
        sent_text = telegram_bot._send_or_edit_message.await_args.args[1]
        self.assertIn("稳定币价格", sent_text)
        self.assertIn("前25稳定币价格失败", sent_text)

    def test_stablecoins_command_escapes_html_special_characters(self) -> None:
        from common.clients.defillama import StablecoinSnapshot

        telegram_bot = self._build_bot()
        telegram_bot._send_or_edit_message = AsyncMock()
        update = self._build_update()
        context = self._build_context()
        stablecoin_client = FakeStablecoinClient(
            snapshots=[
                StablecoinSnapshot("USD <One> & Co", "USD<T>", 0.9987, 150_000_000_000.0, 1),
            ]
        )

        with patch("bot.handlers.DefiLlamaClient", return_value=stablecoin_client, create=True):
            asyncio.run(telegram_bot.stablecoins_command(update, context))

        sent_text = telegram_bot._send_or_edit_message.await_args.args[1]
        self.assertIn("USD &lt;One&gt; &amp; Co", sent_text)
        self.assertIn("USD&lt;T&gt;", sent_text)
        self.assertNotIn("USD <One> & Co", sent_text)
        self.assertNotIn("USD<T>", sent_text)

    def test_help_message_mentions_stablecoins_command(self) -> None:
        from bot.messages import render_help_message

        help_text = render_help_message([])
        self.assertIn("/stablecoins", help_text)
        self.assertIn("前25稳定币价格", help_text)

    def test_welcome_message_mentions_stablecoins_command(self) -> None:
        from bot.messages import render_welcome_message

        welcome_text = render_welcome_message()
        self.assertIn("/stablecoins", welcome_text)
        self.assertIn("前25稳定币价格", welcome_text)


class TelegramNotifierLocalizationTests(unittest.TestCase):
    def test_test_connection_message_is_localized_in_chinese(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")

        with patch.object(notifier, "send_message", return_value=True) as mock_send:
            self.assertTrue(notifier.test_connection())

        message = mock_send.call_args.args[0]
        self.assertIn("加密货币价格监控机器人", message)
        self.assertIn("正在监控多个加密货币价格", message)
        self.assertNotIn("Monitoring multiple cryptocurrencies", message)


class MainEntrypointRegressionTests(unittest.TestCase):
    def test_package_imports_do_not_require_optional_dependencies(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "import bot, common, monitor"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)

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
             patch.object(
                 bot,
                 "ConfigManager",
                 return_value=types.SimpleNamespace(
                     telegram_bot_token="token",
                     telegram_chat_id="chat",
                 ),
             ), \
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
                 patch.object(
                 bot,
                 "ConfigManager",
                 return_value=types.SimpleNamespace(
                     telegram_bot_token="token",
                     telegram_chat_id="chat",
                 ),
             ), \
                 patch.object(bot, "TelegramNotifier", return_value=DummyNotifier()), \
                 patch.object(bot, "TelegramBot", DummyBot):
                bot.main()
        finally:
            if original_debug is None:
                os.environ.pop("DEBUG", None)
            else:
                os.environ["DEBUG"] = original_debug

        self.assertEqual(state["debug_at_setup"], "true")


class EnvExampleRegressionTests(unittest.TestCase):
    def test_env_example_includes_stablecoin_depeg_settings(self) -> None:
        content = (Path(__file__).resolve().parents[1] / ".env.example").read_text()

        self.assertIn("STABLECOIN_DEPEG_MONITOR_ENABLED=", content)
        self.assertIn("STABLECOIN_DEPEG_TOP_N=25", content)
        self.assertIn("STABLECOIN_DEPEG_THRESHOLD_PERCENT=5", content)
        self.assertIn("STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS=60", content)
        self.assertIn("STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS=300", content)


class StablecoinDocumentationRegressionTests(unittest.TestCase):
    def test_deployment_doc_includes_stablecoin_depeg_settings(self) -> None:
        content = (Path(__file__).resolve().parents[1] / "DEPLOYMENT.md").read_text()

        self.assertIn("STABLECOIN_DEPEG_MONITOR_ENABLED=", content)
        self.assertIn("STABLECOIN_DEPEG_TOP_N=25", content)
        self.assertIn("STABLECOIN_DEPEG_THRESHOLD_PERCENT=5", content)
        self.assertIn("STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS=60", content)
        self.assertIn("STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS=300", content)

    def test_readme_describes_stablecoin_threshold_as_configurable(self) -> None:
        content = (Path(__file__).resolve().parents[1] / "README.md").read_text()

        self.assertIn("| `STABLECOIN_DEPEG_MONITOR_ENABLED` | 是否启用稳定币脱锚监控 | `true` |", content)
        self.assertIn("| `STABLECOIN_DEPEG_TOP_N` | 监控市值前 N 个稳定币 | `25` |", content)
        self.assertIn("| `STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS` | DefiLlama 轮询间隔（秒） | `60` |", content)
        self.assertIn("| `STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS` | 同一稳定币重复告警冷却（秒） | `300` |", content)
        self.assertIn("默认 ±5%", content)
        self.assertIn("STABLECOIN_DEPEG_THRESHOLD_PERCENT", content)

    def test_readme_documents_timezone_fallback_as_fixed_offset_approximation(self) -> None:
        content = (Path(__file__).resolve().parents[1] / "README.md").read_text()

        self.assertIn("TIMEZONE", content)
        self.assertIn("fixed-offset fallback", content)
        self.assertIn("DST", content)


class DockerRegressionTests(unittest.TestCase):
    def test_dockerfile_uses_heartbeat_healthcheck(self) -> None:
        dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()

        self.assertIn("/proc/1/cmdline", dockerfile)
        self.assertIn("/tmp/monitor_heartbeat", dockerfile)
        self.assertIn("/tmp/bot_heartbeat", dockerfile)
        self.assertNotIn("api.binance.com/api/v3/ping", dockerfile)

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
                 patch.object(
                 monitor,
                 "ConfigManager",
                 return_value=types.SimpleNamespace(
                     telegram_bot_token="token",
                     telegram_chat_id="chat",
                     monitor_heartbeat_file="/tmp/monitor-heartbeat-test",
                 ),
             ), \
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

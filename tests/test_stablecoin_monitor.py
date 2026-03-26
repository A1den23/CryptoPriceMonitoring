import asyncio
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tests.stubs import install_dependency_stubs


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))


install_dependency_stubs()

from common.clients.defillama import StablecoinSnapshot
from monitor.stablecoin_depeg_monitor import StablecoinDepegMonitor


class FakeClock:
    def __init__(self, start_time: datetime) -> None:
        self.current = start_time

    def now(self) -> datetime:
        return self.current


class StablecoinMonitorSendStateTests(unittest.TestCase):
    def _build_stablecoin_monitor(self, notifier, client=None):
        config = types.SimpleNamespace(
            stablecoin_depeg_threshold_percent=5.0,
            stablecoin_depeg_alert_cooldown_seconds=3600,
            stablecoin_depeg_top_n=2,
            stablecoin_depeg_poll_interval_seconds=300,
        )
        if client is None:
            client = types.SimpleNamespace(fetch_stablecoins=AsyncMock(return_value=[]))
        return StablecoinDepegMonitor(config=config, notifier=notifier, client=client)

    def test_evaluate_snapshot_updates_last_alert_time_only_after_successful_send(self) -> None:
        notifier = types.SimpleNamespace(send_message=Mock(return_value=False))
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)
        start_time = datetime(2026, 3, 24, tzinfo=timezone.utc)
        clock = FakeClock(start_time)
        snapshot = StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 1)

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now):
            self.assertFalse(stablecoin_monitor.evaluate_snapshot(snapshot))

        state = stablecoin_monitor._states["USDC"]
        self.assertTrue(state.is_depegged)
        self.assertIsNone(state.last_alert_time)
        notifier.send_message.assert_called_once()

    def test_run_once_counts_alert_only_after_successful_send(self) -> None:
        snapshot = StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 1)

        async def fetch_stablecoins(top_n: int):
            return [snapshot]

        notifier = types.SimpleNamespace(send_message=Mock(return_value=False))
        stablecoin_monitor = self._build_stablecoin_monitor(
            notifier,
            types.SimpleNamespace(fetch_stablecoins=fetch_stablecoins),
        )
        start_time = datetime(2026, 3, 24, tzinfo=timezone.utc)
        clock = FakeClock(start_time)

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now), \
             patch("monitor.stablecoin_depeg_monitor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            async def invoke(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = invoke
            alerts = asyncio.run(stablecoin_monitor.run_once())

        state = stablecoin_monitor._states["USDC"]
        self.assertEqual(alerts, 0)
        self.assertTrue(state.is_depegged)
        self.assertIsNone(state.last_alert_time)
        mock_to_thread.assert_awaited_once()
        notifier.send_message.assert_called_once()

    def test_run_once_records_last_alert_time_after_successful_send(self) -> None:
        snapshot = StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 1)

        async def fetch_stablecoins(top_n: int):
            return [snapshot]

        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))
        stablecoin_monitor = self._build_stablecoin_monitor(
            notifier,
            types.SimpleNamespace(fetch_stablecoins=fetch_stablecoins),
        )
        start_time = datetime(2026, 3, 24, tzinfo=timezone.utc)
        clock = FakeClock(start_time)

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now), \
             patch("monitor.stablecoin_depeg_monitor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            async def invoke(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = invoke
            alerts = asyncio.run(stablecoin_monitor.run_once())

        state = stablecoin_monitor._states["USDC"]
        self.assertEqual(alerts, 1)
        self.assertEqual(state.last_alert_time, start_time)
        mock_to_thread.assert_awaited_once()
        notifier.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()

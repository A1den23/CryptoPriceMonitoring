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
            stablecoin_universe_cache_path="/tmp/stablecoin_top25.json",
        )
        if client is None:
            client = types.SimpleNamespace(fetch_all_stablecoins=AsyncMock(return_value=[]))
        return StablecoinDepegMonitor(config=config, notifier=notifier, client=client)

    def test_build_alert_message_updates_last_alert_time_only_after_successful_send(self) -> None:
        notifier = types.SimpleNamespace(send_message=Mock(return_value=False))
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)
        start_time = datetime(2026, 3, 24, tzinfo=timezone.utc)
        clock = FakeClock(start_time)
        snapshot = StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 1)

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now):
            alert = stablecoin_monitor._build_alert_message(snapshot)
            self.assertIsNotNone(alert)
            message, alert_time = alert
            sent = notifier.send_message(message)
            if not sent:
                # Do NOT call _mark_alert_sent when send fails
                pass

        state = stablecoin_monitor._states[("USDC", "USDC")]
        self.assertTrue(state.is_depegged)
        self.assertIsNone(state.last_alert_time)
        notifier.send_message.assert_called_once()

    def test_run_once_counts_alert_only_after_successful_send(self) -> None:
        snapshot = StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 1)

        notifier = types.SimpleNamespace(send_message=Mock(return_value=False))
        stablecoin_monitor = self._build_stablecoin_monitor(
            notifier,
            types.SimpleNamespace(fetch_all_stablecoins=AsyncMock(return_value=[snapshot])),
        )
        start_time = datetime(2026, 3, 24, tzinfo=timezone.utc)
        clock = FakeClock(start_time)

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now), \
             patch(
                 "monitor.stablecoin_depeg_monitor.resolve_live_snapshots_for_cached_universe",
                 new=AsyncMock(return_value=[snapshot]),
             ), \
             patch("monitor.stablecoin_depeg_monitor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            async def invoke(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = invoke
            alerts = asyncio.run(stablecoin_monitor.run_once())

        state = stablecoin_monitor._states[("USDC", "USDC")]
        self.assertEqual(alerts, 0)
        self.assertTrue(state.is_depegged)
        self.assertIsNone(state.last_alert_time)
        mock_to_thread.assert_awaited_once()
        notifier.send_message.assert_called_once()

    def test_run_once_continues_after_send_exception_and_retries_next_poll(self) -> None:
        first = StablecoinSnapshot("USD Coin", "USDC", 0.94, 1000.0, 1)
        second = StablecoinSnapshot("Tether", "USDT", 0.94, 900.0, 2)

        notifier = types.SimpleNamespace(
            send_message=Mock(side_effect=[RuntimeError("secret-token"), True, True])
        )
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)
        clock = FakeClock(datetime(2026, 3, 24, tzinfo=timezone.utc))

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now), \
             patch(
                 "monitor.stablecoin_depeg_monitor.resolve_live_snapshots_for_cached_universe",
                 new=AsyncMock(return_value=[first, second]),
             ), \
             patch("monitor.stablecoin_depeg_monitor.logger.error") as mock_error:
            first_alerts = asyncio.run(stablecoin_monitor.run_once())
            first_state = stablecoin_monitor._states[("USDC", "USD Coin")]
            second_state = stablecoin_monitor._states[("USDT", "Tether")]

            self.assertEqual(first_alerts, 1)
            self.assertIsNone(first_state.last_alert_time)
            self.assertEqual(second_state.last_alert_time, clock.current)
            mock_error.assert_called_once()
            self.assertNotIn("secret-token", mock_error.call_args.args[0])

            second_alerts = asyncio.run(stablecoin_monitor.run_once())

        self.assertEqual(second_alerts, 1)
        self.assertEqual(first_state.last_alert_time, clock.current)
        self.assertEqual(notifier.send_message.call_count, 3)

    def test_run_once_records_last_alert_time_after_successful_send(self) -> None:
        snapshot = StablecoinSnapshot("USDC", "USDC", 0.94, 1000.0, 1)

        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))
        stablecoin_monitor = self._build_stablecoin_monitor(
            notifier,
            types.SimpleNamespace(fetch_all_stablecoins=AsyncMock(return_value=[snapshot])),
        )
        start_time = datetime(2026, 3, 24, tzinfo=timezone.utc)
        clock = FakeClock(start_time)

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now), \
             patch(
                 "monitor.stablecoin_depeg_monitor.resolve_live_snapshots_for_cached_universe",
                 new=AsyncMock(return_value=[snapshot]),
             ), \
             patch("monitor.stablecoin_depeg_monitor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            async def invoke(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = invoke
            alerts = asyncio.run(stablecoin_monitor.run_once())

        state = stablecoin_monitor._states[("USDC", "USDC")]
        self.assertEqual(alerts, 1)
        self.assertEqual(state.last_alert_time, start_time)
        mock_to_thread.assert_awaited_once()
        notifier.send_message.assert_called_once()

    def test_stablecoins_with_same_symbol_keep_separate_alert_state(self) -> None:
        first = StablecoinSnapshot("First Dollar", "USDX", 0.94, 1000.0, 1)
        second = StablecoinSnapshot("Second Dollar", "USDX", 0.94, 900.0, 2)
        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)
        start_time = datetime(2026, 3, 24, tzinfo=timezone.utc)
        clock = FakeClock(start_time)

        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now):
            self.assertIsNotNone(stablecoin_monitor._build_alert_message(first))
            stablecoin_monitor._mark_alert_sent(first, start_time)
            self.assertIsNotNone(stablecoin_monitor._build_alert_message(second))

        self.assertIn(("USDX", "First Dollar"), stablecoin_monitor._states)
        self.assertIn(("USDX", "Second Dollar"), stablecoin_monitor._states)

    def _poll(self, stablecoin_monitor, clock, snapshots) -> int:
        with patch("monitor.stablecoin_depeg_monitor.now_in_configured_timezone", side_effect=clock.now), \
             patch(
                 "monitor.stablecoin_depeg_monitor.resolve_live_snapshots_for_cached_universe",
                 new=AsyncMock(return_value=snapshots),
             ):
            return asyncio.run(stablecoin_monitor.run_once())

    def test_recovery_notification_sent_once_and_state_cleared(self) -> None:
        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)
        clock = FakeClock(datetime(2026, 3, 24, tzinfo=timezone.utc))
        recovered = StablecoinSnapshot("USD <Coin>", "USDC", 0.998, 1000.0, 1)

        self._poll(stablecoin_monitor, clock, [StablecoinSnapshot("USD <Coin>", "USDC", 0.94, 1000.0, 1)])
        clock.current += timedelta(seconds=60)
        alerts = self._poll(stablecoin_monitor, clock, [recovered])
        self._poll(stablecoin_monitor, clock, [recovered])

        self.assertEqual(alerts, 0)
        self.assertEqual(notifier.send_message.call_count, 2)
        message = notifier.send_message.call_args_list[1].args[0]
        self.assertIn("稳定币恢复通知", message)
        self.assertIn("USDC (USD &lt;Coin&gt;)", message)
        self.assertIn("$0.998", message)
        self.assertIn("-0.20%", message)
        state = stablecoin_monitor._states[("USDC", "USD <Coin>")]
        self.assertFalse(state.is_depegged)
        self.assertIsNone(state.last_alert_time)

    def test_failed_recovery_keeps_depegged_and_isolates_other_coins(self) -> None:
        for failure in (RuntimeError("secret-token"), False):
            with self.subTest(failure=failure):
                first_depegged = StablecoinSnapshot("First", "USDA", 0.94, 1000.0, 1)
                second_depegged = StablecoinSnapshot("Second", "USDB", 0.94, 900.0, 2)
                first_recovered = StablecoinSnapshot("First", "USDA", 1.0, 1000.0, 1)
                second_recovered = StablecoinSnapshot("Second", "USDB", 1.0, 900.0, 2)
                notifier = types.SimpleNamespace(
                    send_message=Mock(side_effect=[True, True, failure, True, True])
                )
                stablecoin_monitor = self._build_stablecoin_monitor(notifier)
                clock = FakeClock(datetime(2026, 3, 24, tzinfo=timezone.utc))
                first_state_key = ("USDA", "First")
                second_state_key = ("USDB", "Second")

                self._poll(stablecoin_monitor, clock, [first_depegged, second_depegged])
                first_alert_time = stablecoin_monitor._states[first_state_key].last_alert_time
                clock.current += timedelta(seconds=60)
                with patch("monitor.stablecoin_depeg_monitor.logger.error") as mock_error:
                    self._poll(stablecoin_monitor, clock, [first_recovered, second_recovered])

                first_state = stablecoin_monitor._states[first_state_key]
                self.assertTrue(first_state.is_depegged)
                self.assertEqual(first_state.last_alert_time, first_alert_time)
                self.assertFalse(stablecoin_monitor._states[second_state_key].is_depegged)
                self.assertEqual(notifier.send_message.call_count, 4)
                for call in mock_error.call_args_list:
                    self.assertNotIn("secret-token", call.args[0])

                clock.current += timedelta(seconds=60)
                self._poll(stablecoin_monitor, clock, [first_recovered, second_recovered])

                self.assertFalse(first_state.is_depegged)
                self.assertEqual(notifier.send_message.call_count, 5)
                self.assertIn("First", notifier.send_message.call_args.args[0])
                self.assertIn("稳定币恢复通知", notifier.send_message.call_args.args[0])

    def test_recovery_retry_is_dropped_when_coin_depegs_again_within_cooldown(self) -> None:
        depegged = StablecoinSnapshot("USD Coin", "USDC", 0.94, 1000.0, 1)
        recovered = StablecoinSnapshot("USD Coin", "USDC", 1.0, 1000.0, 1)
        notifier = types.SimpleNamespace(send_message=Mock(side_effect=[True, False]))
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)
        clock = FakeClock(datetime(2026, 3, 24, tzinfo=timezone.utc))

        self._poll(stablecoin_monitor, clock, [depegged])
        clock.current += timedelta(seconds=60)
        self._poll(stablecoin_monitor, clock, [recovered])
        clock.current += timedelta(seconds=60)
        alerts = self._poll(stablecoin_monitor, clock, [depegged])

        self.assertEqual(alerts, 0)
        self.assertEqual(notifier.send_message.call_count, 2)
        self.assertTrue(stablecoin_monitor._states[("USDC", "USD Coin")].is_depegged)

    def test_no_recovery_notification_when_depeg_alert_was_never_delivered(self) -> None:
        depegged = StablecoinSnapshot("USD Coin", "USDC", 0.94, 1000.0, 1)
        recovered = StablecoinSnapshot("USD Coin", "USDC", 1.0, 1000.0, 1)
        notifier = types.SimpleNamespace(send_message=Mock(return_value=False))
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)
        clock = FakeClock(datetime(2026, 3, 24, tzinfo=timezone.utc))

        self._poll(stablecoin_monitor, clock, [depegged])
        clock.current += timedelta(seconds=60)
        self._poll(stablecoin_monitor, clock, [recovered])

        self.assertEqual(notifier.send_message.call_count, 1)
        self.assertFalse(stablecoin_monitor._states[("USDC", "USD Coin")].is_depegged)

    def test_no_recovery_notification_for_coin_never_depegged(self) -> None:
        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)
        clock = FakeClock(datetime(2026, 3, 24, tzinfo=timezone.utc))

        self._poll(stablecoin_monitor, clock, [StablecoinSnapshot("USD Coin", "USDC", 1.0, 1000.0, 1)])

        notifier.send_message.assert_not_called()

    def test_recovery_state_is_tracked_per_symbol_and_name(self) -> None:
        notifier = types.SimpleNamespace(send_message=Mock(return_value=True))
        stablecoin_monitor = self._build_stablecoin_monitor(notifier)
        clock = FakeClock(datetime(2026, 3, 24, tzinfo=timezone.utc))

        self._poll(stablecoin_monitor, clock, [StablecoinSnapshot("First Dollar", "USDX", 0.94, 1000.0, 1)])
        clock.current += timedelta(seconds=60)
        self._poll(
            stablecoin_monitor,
            clock,
            [
                StablecoinSnapshot("Second Dollar", "USDX", 1.0, 900.0, 2),
                StablecoinSnapshot("First Dollar", "USDX", 0.94, 1000.0, 1),
            ],
        )

        self.assertEqual(notifier.send_message.call_count, 1)
        self.assertTrue(stablecoin_monitor._states[("USDX", "First Dollar")].is_depegged)


if __name__ == "__main__":
    unittest.main()

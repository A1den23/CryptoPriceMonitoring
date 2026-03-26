"""
WebSocket monitor orchestration and runtime lifecycle management.
"""

import asyncio
import signal
from datetime import datetime
from pathlib import Path

from common.clients.defillama import DefiLlamaClient
from common.clients.websocket import BinanceWebSocketClient
from common.config import ConfigManager
from common.logging import logger
from common.notifications import TelegramNotifier
from common.runtime import SignalHandlerRegistry
from common.utils import now_in_configured_timezone

from .price_monitor import PriceMonitor
from .runtime_messages import (
    render_disconnect_alert,
    render_realtime_updates_block,
    render_reconnect_alert,
    render_shutdown_notification,
)
from .stablecoin_depeg_monitor import StablecoinDepegMonitor


class WebSocketMultiCoinMonitor:
    """
    Real-time multi-cryptocurrency monitor using WebSocket.

    This class provides real-time price monitoring using Binance WebSocket streams,
    with automatic reconnection and connection health monitoring.
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self.notifier = TelegramNotifier(
            bot_token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
        )

        self.monitors: dict[str, PriceMonitor] = {}
        self._load_monitors()

        self.ws_client: BinanceWebSocketClient | None = None
        self.stablecoin_client: DefiLlamaClient | None = None
        self.stablecoin_monitor: StablecoinDepegMonitor | None = None

        self.last_print_time: datetime | None = None
        self.print_interval = 5
        self._pending_updates: list[str] = []
        self._update_lock = asyncio.Lock()

        self._shutdown_event = asyncio.Event()
        self._signal_registry = SignalHandlerRegistry()
        self._original_sigint = None
        self._original_sigterm = None
        self._signal_handlers_registered = False
        self._disconnect_alert_time: datetime | None = None
        self._last_disconnect_reason: str | None = None
        self._notification_tasks: set[asyncio.Task] = set()

        self._heartbeat_file = Path(self.config.monitor_heartbeat_file)
        self._last_heartbeat_touch: datetime | None = None

    def _load_monitors(self):
        """Load monitors from configuration."""
        enabled_coins = self.config.get_enabled_coins()
        for coin_config in enabled_coins:
            monitor = PriceMonitor(
                coin_config,
                self.notifier,
                volume_alert_cooldown_seconds=self.config.volume_alert_cooldown_seconds,
                volatility_alert_cooldown_seconds=self.config.volatility_alert_cooldown_seconds,
                milestone_alert_cooldown_seconds=self.config.milestone_alert_cooldown_seconds,
            )
            self.monitors[coin_config.symbol] = monitor
            logger.info(f"Loaded monitor for {coin_config}")

        if not self.monitors:
            logger.warning("No coins are enabled in configuration")

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        if self._signal_handlers_registered:
            return
        self._signal_registry.setup(self._signal_handler)
        self._original_sigint = self._signal_registry._original_sigint
        self._original_sigterm = self._signal_registry._original_sigterm
        self._signal_handlers_registered = True
        logger.debug("Signal handlers registered for SIGINT and SIGTERM")

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals (SIGINT, SIGTERM)."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received signal {sig_name} ({signum}), initiating graceful shutdown...")

        original_handler = self._original_sigint if signum == signal.SIGINT else self._original_sigterm
        if original_handler is not None:
            self._restore_signal_handler(signum, original_handler)
        self._shutdown_event.set()

    @staticmethod
    def _restore_signal_handler(signum: int, original_handler) -> None:
        """Restore original signal handler, handling cross-platform differences."""
        SignalHandlerRegistry._restore_signal(signum, original_handler)

    def _restore_signal_handlers(self) -> None:
        """Restore original signal handlers on monitor exit."""
        if not self._signal_handlers_registered:
            return
        self._signal_registry.restore()
        self._original_sigint = None
        self._original_sigterm = None
        self._signal_handlers_registered = False

    async def _send_shutdown_notification(self) -> None:
        """Send shutdown notification via Telegram."""
        now = now_in_configured_timezone()
        uptime = "未知"

        if self.ws_client:
            stats = self.ws_client.get_statistics()
            uptime_seconds = stats.get("uptime_seconds", 0)
            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            uptime = f"{hours}h {minutes}m"

        message = render_shutdown_notification(
            current_time=now,
            uptime=uptime,
            monitor_count=len(self.monitors),
        )
        try:
            self.notifier.send_message(message)
        except Exception as e:
            logger.error(f"Failed to send shutdown notification: {e}")

    async def _flush_monitor_notifications(self) -> None:
        """Wait for all monitor notification tasks during graceful shutdown."""
        for monitor in self.monitors.values():
            await monitor.flush_notification_tasks()

    async def _on_price_update(self, symbol: str, price: float):
        """Callback function for WebSocket price updates."""
        monitor = self.monitors.get(symbol)
        if not monitor:
            return

        output = monitor.check(price)
        self._touch_heartbeat()

        if output is not None:
            async with self._update_lock:
                self._pending_updates.append(output)

        current_time = now_in_configured_timezone()
        if (
            self.last_print_time is None
            or (current_time - self.last_print_time).total_seconds() >= self.print_interval
        ):
            await self._print_updates()
            self.last_print_time = current_time

    async def _on_kline_update(self, symbol: str, price: float, volume: float, is_closed: bool):
        """Callback function for WebSocket kline updates (volume monitoring)."""
        monitor = self.monitors.get(symbol)
        if not monitor:
            logger.warning(f"No monitor registered for symbol: {symbol}")
            return

        if is_closed:
            volume_info = monitor.check_volume_anomaly(price, volume)
            if volume_info:
                monitor.latest_volume_info = volume_info

    async def _print_updates(self):
        """Print accumulated price updates."""
        async with self._update_lock:
            if not self._pending_updates:
                return

            updates_to_print = list(self._pending_updates)
            self._pending_updates.clear()

        timestamp = now_in_configured_timezone().strftime("%H:%M:%S")
        logger.info(f"Real-time price updates [{timestamp}]:")
        for update in updates_to_print:
            logger.info(f"  {update}")

        try:
            print(render_realtime_updates_block(timestamp=timestamp, updates=updates_to_print))
        except (OSError, IOError):
            pass
        finally:
            self._touch_heartbeat()

    def _touch_heartbeat(self):
        """Touch heartbeat file to indicate monitor is actively receiving market updates."""
        now = now_in_configured_timezone()
        if self._last_heartbeat_touch and (now - self._last_heartbeat_touch).total_seconds() < 1:
            return
        try:
            self._heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
            self._heartbeat_file.touch()
            self._last_heartbeat_touch = now
        except OSError as e:
            logger.warning(f"Failed to update heartbeat file '{self._heartbeat_file}': {e}")

    def _track_notification_task(self, task: asyncio.Task) -> asyncio.Task:
        """Track a ws_monitor-owned notification task until completion."""
        self._notification_tasks.add(task)
        task.add_done_callback(self._discard_notification_task)
        return task

    def _discard_notification_task(self, task: asyncio.Task) -> None:
        """Remove a completed notification task from tracking."""
        self._notification_tasks.discard(task)

    async def _flush_notification_tasks(self) -> None:
        """Cancel and await ws_monitor-owned notification tasks during cleanup."""
        while self._notification_tasks:
            pending_tasks = list(self._notification_tasks)
            for task in pending_tasks:
                if not task.done():
                    task.cancel()
            for task in pending_tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.error(f"Notification task failed during cleanup: {exc}")

    async def _on_disconnect(self, reason: str) -> None:
        """Handle WebSocket disconnect event."""
        self._last_disconnect_reason = reason
        self._disconnect_alert_time = now_in_configured_timezone()

        message = render_disconnect_alert(
            reason=reason,
            current_time=self._disconnect_alert_time,
        )

        try:
            loop = asyncio.get_running_loop()
            task = self._track_notification_task(
                loop.create_task(asyncio.to_thread(self.notifier.send_message, message))
            )
            task.add_done_callback(self._on_disconnect_done)
        except Exception as e:
            logger.error(f"Failed to enqueue disconnect alert: {e}")

    def _on_disconnect_done(self, task: asyncio.Task) -> None:
        """Handle disconnect notification completion."""
        if task.cancelled():
            return
        err = task.exception()
        if err:
            logger.error(f"Disconnect alert delivery failed: {err}")
        else:
            logger.info(f"Disconnect alert sent: {task.result()}")

    def _format_downtime(self, seconds: float) -> str:
        """Format downtime duration for display."""
        if seconds < 60:
            return f"{int(seconds)}秒"
        if seconds < 3600:
            return f"{int(seconds // 60)}分{int(seconds % 60)}秒"
        return f"{int(seconds // 3600)}小时{int((seconds % 3600) // 60)}分"

    def _on_reconnect_done(self, task: asyncio.Task) -> None:
        """Handle reconnect notification completion."""
        if task.cancelled():
            return
        err = task.exception()
        if err:
            logger.error(f"Reconnect alert delivery failed: {err}")
        else:
            logger.info(f"Reconnect alert sent: {task.result()}")

    async def _on_reconnect(self, attempt_count: int) -> None:
        """Handle WebSocket reconnect success."""
        now = now_in_configured_timezone()
        downtime = ""
        if self._disconnect_alert_time:
            downtime_seconds = (now - self._disconnect_alert_time).total_seconds()
            downtime = self._format_downtime(downtime_seconds)

        message = render_reconnect_alert(
            attempt_count=attempt_count,
            downtime=downtime if downtime else "未知",
            current_time=now,
        )

        try:
            loop = asyncio.get_running_loop()
            task = self._track_notification_task(
                loop.create_task(asyncio.to_thread(self.notifier.send_message, message))
            )
            task.add_done_callback(self._on_reconnect_done)
            self._disconnect_alert_time = None
            self._last_disconnect_reason = None
        except Exception as e:
            logger.error(f"Failed to enqueue reconnect alert: {e}")

    async def run(self):
        """Start WebSocket monitoring."""
        logger.info("=" * 60)
        logger.info("启动多币种价格监控 (WebSocket 模式)")
        logger.info("=" * 60)
        logger.info(f"监控币种: {len(self.monitors)} 个")
        logger.info("连接方式: WebSocket 实时推送")
        logger.info("=" * 60)

        ws_task = None
        shutdown_task = None
        stablecoin_task = None
        tasks: list[asyncio.Task] = []
        should_cleanup = False
        should_notify_shutdown = False
        cancellation_error: asyncio.CancelledError | None = None

        try:
            self._setup_signal_handlers()

            if not self.monitors:
                logger.error("No enabled coins configured. Set at least one *_ENABLED=true")
                return

            if not self.notifier.test_connection():
                logger.warning("Test message failed to send. Check Telegram configuration")

            symbols = list(self.monitors.keys())
            self._touch_heartbeat()

            self.ws_client = BinanceWebSocketClient(
                symbols=symbols,
                on_price_callback=self._on_price_update,
                on_kline_callback=self._on_kline_update,
                on_disconnect_callback=self._on_disconnect,
                on_reconnect_callback=self._on_reconnect,
                reconnect_delay=5.0,
                ping_interval=self.config.ws_ping_interval_seconds,
                pong_timeout=self.config.ws_pong_timeout_seconds,
                message_timeout=self.config.ws_message_timeout_seconds,
                max_reconnect_attempts=None,
            )

            if self.config.stablecoin_depeg_monitor_enabled:
                self.stablecoin_client = DefiLlamaClient()
                self.stablecoin_monitor = StablecoinDepegMonitor(
                    config=self.config,
                    notifier=self.notifier,
                    client=self.stablecoin_client,
                )

            ws_task = asyncio.create_task(self.ws_client.start())
            shutdown_task = asyncio.create_task(self._shutdown_event.wait())
            tasks = [ws_task, shutdown_task]
            should_cleanup = True

            if self.stablecoin_monitor is not None:
                stablecoin_task = asyncio.create_task(self.stablecoin_monitor.run())
                tasks.append(stablecoin_task)

            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if stablecoin_task is not None and stablecoin_task in done:
                try:
                    stablecoin_task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Stablecoin depeg monitor task exited unexpectedly")
                    raise
                else:
                    if not self._shutdown_event.is_set():
                        raise RuntimeError("Stablecoin depeg monitor exited unexpectedly without a shutdown signal")

            if ws_task in done:
                try:
                    ws_task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("WebSocket client task exited unexpectedly")
                    raise
                else:
                    if not self._shutdown_event.is_set():
                        raise RuntimeError("WebSocket client exited unexpectedly without a shutdown signal")

            if self._shutdown_event.is_set():
                logger.info("Graceful shutdown in progress...")
                should_notify_shutdown = True

        except KeyboardInterrupt:
            logger.info("\nStopping WebSocket monitor (KeyboardInterrupt)...")
            should_notify_shutdown = False
            if self.ws_client:
                await self.ws_client.stop()
            self.notifier.send_message("👋 加密货币价格监控已停止")
        except asyncio.CancelledError as exc:
            cancellation_error = exc
            logger.info("WebSocket monitor cancelled by supervisor, performing graceful cleanup...")
        except Exception:
            logger.exception("WebSocket monitor encountered an unexpected error")
            raise
        finally:
            try:
                if should_cleanup:
                    for task in tasks:
                        if task is not None:
                            task.cancel()
                    for task in tasks:
                        if task is not None:
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass

                    if self.ws_client:
                        await self.ws_client.stop()

                    await self._flush_monitor_notifications()
                    await self._flush_notification_tasks()

                    if should_notify_shutdown:
                        await self._send_shutdown_notification()
            finally:
                self._restore_signal_handlers()
                if self.stablecoin_client is not None:
                    await self.stablecoin_client.close()
                self.notifier.close()

            if cancellation_error is not None:
                raise cancellation_error

    async def print_statistics(self):
        """Print WebSocket connection statistics."""
        if self.ws_client:
            stats = self.ws_client.get_statistics()
            print("\n📊 WebSocket 统计:")
            print(f"  状态: {stats['state']}")
            print(f"  接收消息: {stats['messages_received']}")
            print(f"  重连次数: {stats['reconnect_count']}")
            print(f"  运行时间: {stats['uptime_seconds']:.1f}秒")
            if stats["last_message_time"]:
                print(f"  最后更新: {stats['last_message_time']}")

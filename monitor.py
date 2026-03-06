#!/usr/bin/env python3
"""
Crypto Price Monitoring Bot with WebSocket Support
Monitors multiple cryptocurrency prices in real-time via WebSocket and sends alerts via Telegram

Usage:
    python monitor.py              # Run monitoring with WebSocket (default)
    python monitor.py --test       # Test volatility alerts
    python monitor.py --status     # Show current status
"""

import asyncio
import math
import os
import signal
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from common import (
    setup_logging,
    ConfigManager,
    CoinConfig,
    load_environment,
    BinancePriceFetcher,
    BinanceWebSocketClient,
    TelegramNotifier,
    format_price,
    format_threshold,
    get_coin_display_name,
    get_coin_emoji,
    now_in_configured_timezone,
    logger
)


@dataclass(slots=True)
class PriceData:
    """Compact price sample used for rolling volatility calculations."""
    price: float
    timestamp: datetime


@dataclass(slots=True)
class VolumeData:
    """Compact volume sample used for anomaly detection."""
    price: float
    volume: float
    timestamp: datetime


@dataclass(slots=True)
class VolatilityMetrics:
    """Volatility metrics derived from a rolling price window."""
    std_dev_pct: float
    cumulative_volatility_pct: float
    range_volatility_pct: float
    acceleration: float


class PriceMonitor:
    """Monitor price changes for a single coin"""
    def __init__(self, config: CoinConfig, notifier: TelegramNotifier,
                 volume_alert_cooldown_seconds: int = 5,
                 volatility_alert_cooldown_seconds: int = 60,
                 milestone_alert_cooldown_seconds: int = 600):
        self.config = config
        self.notifier = notifier

        # State tracking
        # Keep a bounded number of representative samples per window instead of every tick.
        self.price_sample_interval_seconds = max(
            self.MIN_PRICE_SAMPLE_INTERVAL_SECONDS,
            config.volatility_window / self.MAX_PRICE_HISTORY_SAMPLES,
        )
        max_price_history = max(
            min(
                math.ceil(config.volatility_window / self.price_sample_interval_seconds) + 2,
                self.MAX_PRICE_HISTORY_SAMPLES + 2,
            ),
            self.MIN_PRICE_HISTORY_SAMPLES,
        )
        self.price_history: deque[PriceData] = deque(maxlen=max_price_history)
        self.last_price: float | None = None
        self.last_processed_price: float | None = None  # Last price that triggered alerts

        # Milestone notification cooldown tracking (global cooldown for any milestone crossing)
        self.last_milestone_notification_time: datetime | None = None
        self.milestone_cooldown_seconds = milestone_alert_cooldown_seconds

        # Volatility notification cooldown tracking (independent from milestone cooldown)
        self.last_volatility_notification_time: datetime | None = None
        self.volatility_cooldown_seconds = volatility_alert_cooldown_seconds

        # Volatility tracking - only alert when cumulative volatility is increasing
        self.last_cumulative_volatility: float = 0.0

        # Volume anomaly monitoring
        # Keep at least a 3-minute window so minimum sample count is reachable for 1m klines.
        self.volume_window_seconds = max(
            config.volatility_window,
            self.MIN_VOLUME_DATA_POINTS * self.KLINE_INTERVAL_SECONDS,
        )
        max_volume_history = max(
            self.volume_window_seconds // self.KLINE_INTERVAL_SECONDS + 5,
            self.MIN_VOLUME_DATA_POINTS + 2,
        )
        self.volume_history: deque[VolumeData] = deque(maxlen=max_volume_history)
        self.last_volume_alert_time: datetime | None = None
        self.volume_alert_cooldown_seconds = volume_alert_cooldown_seconds
        self.latest_volume_info: str | None = None  # Store latest volume info for display
        self._notification_tasks: set[asyncio.Task] = set()

    # Constants for price/volume monitoring
    MIN_PRICE_SAMPLE_INTERVAL_SECONDS = 0.25
    MIN_PRICE_HISTORY_SAMPLES = 100
    MAX_PRICE_HISTORY_SAMPLES = 600
    MIN_VOLUME_DATA_POINTS = 3  # Minimum data points needed for volume comparison
    MIN_VOLUME_VALUE = 0.0001  # Minimum valid volume value to prevent zero/negative issues
    KLINE_INTERVAL_SECONDS = 60  # Binance 1m kline close interval

    def _calculate_milestone(self, price: float, threshold: float) -> float:
        """Calculate the milestone for a given price and threshold."""
        if threshold <= 0:
            raise ValueError(f"Invalid threshold for {self.config.symbol}: {threshold}")

        # Use floor-based stepping for all thresholds so values like 2.5 work correctly.
        epsilon = max(threshold * 1e-9, 1e-12)
        return math.floor((price + epsilon) / threshold) * threshold

    def _is_in_milestone_cooldown(self, coin: str) -> bool:
        """Check if milestone notification is in cooldown period.

        Returns True if in cooldown (should skip), False if not in cooldown.
        """
        if not self.last_milestone_notification_time:
            return False

        now = now_in_configured_timezone()
        time_since_last = (now - self.last_milestone_notification_time).total_seconds()
        if time_since_last >= self.milestone_cooldown_seconds:
            return False

        logger.debug(f"[{coin}] Global cooldown active ({time_since_last:.0f}s ago)")
        return True

    def _on_notification_done(self, task: asyncio.Task) -> None:
        """Cleanup completed async notification task and log errors."""
        self._notification_tasks.discard(task)
        try:
            task.result()
        except Exception:
            logger.exception(f"[{self.config.symbol}] Failed to send Telegram notification")

    def _send_notification(self, message: str) -> None:
        """Send notification without blocking the event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.notifier.send_message(message)
            return

        task = loop.create_task(asyncio.to_thread(self.notifier.send_message, message))
        self._notification_tasks.add(task)
        task.add_done_callback(self._on_notification_done)

    def _send_milestone_notification(self, current_price: float, current_milestone: float):
        """Send milestone notification and update tracking"""
        coin = get_coin_display_name(self.config.symbol)
        is_up = current_price > self.last_price
        direction = "📈" if is_up else "📉"

        now = now_in_configured_timezone()
        self.last_price = current_price
        self.last_milestone_notification_time = now

        direction_text = "向上 ↑" if is_up else "向下 ↓"

        message = (
            f"🎉🎉【价格里程碑】🎉🎉\n"
            f"🪙 {self.config.symbol}\n"
            f"💰 价格: {format_price(current_price)}\n"
            f"{direction} 突破方向: {direction_text}\n"
            f"⏱️ {now.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self._send_notification(message)

        # Format milestone for logging
        milestone_str = format_threshold(current_milestone)
        logger.info(f"[{coin}] 突破里程碑: {milestone_str}")

    def check_integer_milestone(self, current_price: float) -> bool:
        """Check if price reached an integer milestone using crossing detection"""
        threshold = self.config.integer_threshold
        coin = get_coin_display_name(self.config.symbol)

        # Initialize last_price on first run
        if self.last_price is None:
            self.last_price = current_price
            return False

        # Calculate current and last milestones
        try:
            current_milestone = self._calculate_milestone(current_price, threshold)
            last_milestone = self._calculate_milestone(self.last_price, threshold)
        except ValueError as e:
            logger.error(str(e))
            self.last_price = current_price
            return False

        # Check if milestone was crossed
        if last_milestone != current_milestone:
            # Check cooldown - skip if in cooldown period
            if self._is_in_milestone_cooldown(coin):
                self.last_price = current_price
                return False

            # Send notification
            self._send_milestone_notification(current_price, current_milestone)
            return True

        # Update last price for next iteration
        self.last_price = current_price
        return False

    def _append_price_sample(self, current_price: float, current_time: datetime) -> None:
        """Add or refresh the most recent representative price sample."""
        if self.price_history:
            elapsed_seconds = (current_time - self.price_history[-1].timestamp).total_seconds()
            if elapsed_seconds < self.price_sample_interval_seconds:
                self.price_history[-1] = PriceData(current_price, current_time)
                return

        self.price_history.append(PriceData(current_price, current_time))

    def _update_price_history(self, current_price: float) -> list[float]:
        """Update price history and return rolling window prices."""
        current_time = now_in_configured_timezone()
        self._append_price_sample(current_price, current_time)

        # Remove old data outside the time window (sliding window)
        cutoff_time = current_time - timedelta(seconds=self.config.volatility_window)
        while self.price_history and self.price_history[0].timestamp < cutoff_time:
            self.price_history.popleft()

        return [p.price for p in self.price_history]

    def _calculate_std_dev_metric(self, prices: list[float]) -> float:
        """Calculate standard deviation percentage"""
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        std_dev = variance ** 0.5
        return (std_dev / mean_price) * 100 if mean_price > 0 else 0

    def _calculate_cumulative_metric(self, prices: list[float]) -> float:
        """Calculate cumulative volatility percentage"""
        if len(prices) < 2:
            return 0.0
        cumulative_change = sum(abs(prices[i] - prices[i-1]) for i in range(1, len(prices)))
        return (cumulative_change / prices[0]) * 100 if prices[0] > 0 else 0

    def _calculate_range_metric(self, prices: list[float]) -> float:
        """Calculate min/max range volatility percentage"""
        min_price = min(prices)
        max_price = max(prices)
        return ((max_price - min_price) / min_price) * 100 if min_price > 0 else 0

    def _calculate_acceleration_metric(self, prices: list[float]) -> float:
        """Calculate volatility acceleration"""
        if len(prices) < 4:
            return 1
        recent_prices = prices[-4:]
        recent_changes = [
            abs(recent_prices[i] - recent_prices[i - 1])
            for i in range(1, len(recent_prices))
        ]
        avg_change = (sum(recent_changes) / len(recent_changes)) if recent_changes else 0
        return (max(recent_changes) / avg_change) if avg_change > 0 else 1

    def _build_volatility_metrics(self, prices: list[float]) -> VolatilityMetrics:
        """Build volatility metrics from the current rolling price window."""
        return VolatilityMetrics(
            std_dev_pct=self._calculate_std_dev_metric(prices),
            cumulative_volatility_pct=self._calculate_cumulative_metric(prices),
            range_volatility_pct=self._calculate_range_metric(prices),
            acceleration=self._calculate_acceleration_metric(prices),
        )

    def _evaluate_volatility_thresholds(
        self,
        metrics: VolatilityMetrics,
        threshold: float,
    ) -> tuple[bool, list[str]]:
        """
        Evaluate if volatility exceeds thresholds
        Returns: (is_volatile, reasons)
        """
        std_dev_pct = metrics.std_dev_pct
        cumulative_volatility_pct = metrics.cumulative_volatility_pct
        range_volatility_pct = metrics.range_volatility_pct
        acceleration = metrics.acceleration

        # Cumulative volatility alert logic: dynamic tracking
        cumulative_alert = False
        if cumulative_volatility_pct >= threshold:
            if cumulative_volatility_pct > self.last_cumulative_volatility:
                cumulative_alert = True

        # Always update the tracking value
        self.last_cumulative_volatility = cumulative_volatility_pct

        is_volatile = (
            std_dev_pct >= threshold * 0.7 or
            cumulative_alert or
            range_volatility_pct >= threshold or
            (acceleration >= 2.0 and std_dev_pct >= threshold * 0.3)
        )

        # Collect reasons for alert
        reasons = []
        if is_volatile:
            if std_dev_pct >= threshold * 0.7:
                reasons.append(f"标准差: {std_dev_pct:.2f}%")
            if cumulative_alert:
                reasons.append(f"累计波动: {cumulative_volatility_pct:.2f}%")
            if range_volatility_pct >= threshold:
                reasons.append(f"区间波动: {range_volatility_pct:.2f}%")
            if acceleration >= 2.0 and std_dev_pct >= threshold * 0.3:
                reasons.append(f"加速度: {acceleration:.1f}x")

        return is_volatile, reasons

    def _is_in_volatility_cooldown(self, current_time: datetime) -> bool:
        """Check if volatility notification is in cooldown period"""
        if not self.last_volatility_notification_time:
            return False
        time_since_last = (current_time - self.last_volatility_notification_time).total_seconds()
        return time_since_last < self.volatility_cooldown_seconds

    def _send_volatility_alert(self, current_price: float, reasons: list[str]) -> None:
        """Send volatility alert notification"""
        current_time = now_in_configured_timezone()
        change = current_price - self.price_history[0].price
        change_percent = (change / self.price_history[0].price) * 100
        direction = "📈" if change > 0 else "📉"
        coin = get_coin_display_name(self.config.symbol)

        message = (
            f"⚠️⚠️【波动警报】⚠️⚠️\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🪙 {self.config.symbol}\n"
            f"💰 当前: {format_price(current_price)}\n"
            f"📊 时间窗口: {self.config.volatility_window}s ({len(self.price_history)} pts)\n"
            f"⚡️ 触发指标: {', '.join(reasons)}\n"
            f"{direction} 净变化: {change_percent:+.2f}%\n"
            f"⏱️ {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━"
        )
        self._send_notification(message)
        logger.info(f"[{coin}] 高波动 - {', '.join(reasons)}")

        # Update last notification time
        self.last_volatility_notification_time = current_time

    def check_volatility(self, current_price: float) -> str | None:
        """
        Enhanced volatility check using multiple metrics:
        1. Standard deviation (measures price dispersion)
        2. Cumulative volatility (sum of all price movements)
        3. Min/max range (original method)
        4. Volatility acceleration (rate of change)
        """
        # Update price history
        prices = self._update_price_history(current_price)

        # Need at least 3 data points for meaningful statistics
        if len(prices) < 3:
            return None

        # Calculate all metrics
        metrics = self._build_volatility_metrics(prices)

        # Format volatility info for display
        volatility_info = (
            f"σ:{metrics.std_dev_pct:.2f}% "
            f"Σ:{metrics.cumulative_volatility_pct:.2f}% "
            f"R:{metrics.range_volatility_pct:.2f}%"
        )

        # Evaluate thresholds before cooldown so cumulative tracking stays fresh.
        threshold = self.config.volatility_percent
        is_volatile, reasons = self._evaluate_volatility_thresholds(metrics, threshold)

        # Check cooldown
        if self._is_in_volatility_cooldown(now_in_configured_timezone()):
            return volatility_info

        # Send alert if volatile
        if is_volatile:
            self._send_volatility_alert(current_price, reasons)

        return volatility_info

    def check_volume_anomaly(self, current_price: float, volume: float) -> str | None:
        """
        Check for volume anomalies (sudden spikes in trading volume)

        Detects when volume increases significantly compared to recent baseline,
        which may indicate market maker issues, heavy trading, or manipulation.

        Args:
            current_price: Current price from kline close
            volume: Trading volume from current 1-minute kline

        Returns:
            Formatted volume info string, or None if no anomaly or insufficient data
        """
        # Validate input data
        if volume <= 0 or current_price <= 0:
            logger.warning(f"[{self.config.symbol}] Invalid volume data: price={current_price}, volume={volume}")
            return None

        current_time = now_in_configured_timezone()

        # Add compact rolling data for current window calculations
        self.volume_history.append(
            VolumeData(
                price=current_price,
                volume=volume,
                timestamp=current_time,
            )
        )

        # Remove old data outside the effective volume window.
        cutoff_time = current_time - timedelta(seconds=self.volume_window_seconds)
        while self.volume_history and self.volume_history[0].timestamp < cutoff_time:
            self.volume_history.popleft()

        # Need minimum data points for meaningful comparison
        if len(self.volume_history) < self.MIN_VOLUME_DATA_POINTS:
            return None

        # Extract volumes
        volumes = [entry.volume for entry in self.volume_history]

        # Calculate baseline (average of previous data points, excluding most recent)
        # This avoids including the potential anomaly in the baseline
        baseline_volumes = volumes[:-1]
        avg_volume = sum(baseline_volumes) / len(baseline_volumes)
        current_volume = volumes[-1]

        # Avoid division by zero (shouldn't happen after validation above)
        if avg_volume <= 0:
            return None

        # Calculate volume multiplier
        volume_multiplier = current_volume / avg_volume

        # Volume anomaly threshold: 10x by default (can be configured per coin)
        volume_alert_multiplier = getattr(self.config, 'volume_alert_multiplier', 10.0)

        # Check cooldown - prevent alert spam while still showing volume info
        if self.last_volume_alert_time:
            time_since_last = (current_time - self.last_volume_alert_time).total_seconds()
            if time_since_last < self.volume_alert_cooldown_seconds:
                # In cooldown, just return info without alerting
                return f"V:{volume_multiplier:.1f}x"

        # Trigger alert if volume exceeds threshold
        if volume_multiplier >= volume_alert_multiplier:
            coin = get_coin_display_name(self.config.symbol)

            # Determine price direction (use first price in current window)
            first_price = self.volume_history[0].price
            price_change = current_price - first_price
            price_change_pct = (price_change / first_price) * 100 if first_price > 0 else 0
            direction = "📈" if price_change > 0 else "📉"

            message = (
                f"🚨🚨【成交量异常警报】🚨🚨\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"🪙 {self.config.symbol}\n"
                f"💰 当前价格: {format_price(current_price)}\n"
                f"{direction} 价格变化: {price_change_pct:+.2f}%\n"
                f"📊 成交量暴增: {volume_multiplier:.1f}x\n"
                f"📈 当前成交量: {current_volume:,.0f}\n"
                f"📊 基准成交量: {avg_volume:,.0f}\n"
                f"⏱️ {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━"
            )
            self._send_notification(message)
            logger.info(f"[{coin}] 成交量异常: {volume_multiplier:.1f}x (当前:{current_volume:,.0f}, 平均:{avg_volume:,.0f})")

            # Update last notification time
            self.last_volume_alert_time = current_time

            return f"V:{volume_multiplier:.1f}x🚨"

        return f"V:{volume_multiplier:.1f}x"

    def check(self, current_price: float) -> str | None:
        """
        Check price and return formatted output

        Only processes if price has changed significantly to avoid duplicate alerts.
        Returns None if price hasn't changed, otherwise returns formatted output.
        """
        # Skip if price hasn't changed (avoid duplicate processing from WebSocket high-frequency updates)
        if self.last_processed_price is not None:
            price_diff = abs(current_price - self.last_processed_price)
            # For prices >= $1, minimum change is $0.001
            # For prices < $1, minimum change is $0.0001
            base_min_change = 0.001 if current_price >= 1 else 0.0001
            min_change = min(base_min_change, self.config.integer_threshold)

            if price_diff < min_change:
                # Price hasn't changed enough to matter
                return None

        # Update last processed price
        self.last_processed_price = current_price

        coin = get_coin_display_name(self.config.symbol)

        # Check for integer milestone
        milestone_alert = self.check_integer_milestone(current_price)

        # Check for volatility
        volatility_info = self.check_volatility(current_price)

        # Format output
        emoji = get_coin_emoji(coin)
        output = f"{emoji} [{coin}] {format_price(current_price)}"
        if milestone_alert:
            output += " 🎯"
        if volatility_info:
            output += f" 📊{volatility_info}"
        if self.latest_volume_info:
            output += f" {self.latest_volume_info}"

        return output


class WebSocketMultiCoinMonitor:
    """
    Real-time multi-cryptocurrency monitor using WebSocket

    This class provides real-time price monitoring using Binance WebSocket streams,
    with automatic reconnection and connection health monitoring.
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self.notifier = TelegramNotifier()

        # Load configurations for all enabled coins
        self.monitors: dict[str, PriceMonitor] = {}
        self._load_monitors()

        # WebSocket client
        self.ws_client: BinanceWebSocketClient | None = None

        # Display statistics
        self.last_print_time: datetime | None = None
        self.print_interval = 5  # Print status every 5 seconds
        self._pending_updates: list[str] = []
        self._update_lock = asyncio.Lock()  # Lock for thread-safe updates

        # Shutdown handling
        self._shutdown_event = asyncio.Event()
        self._setup_signal_handlers()
        self._disconnect_alert_time: datetime | None = None
        self._last_disconnect_reason: str | None = None

        # Heartbeat file: touched when fresh market updates are processed.
        self._heartbeat_file = Path(os.getenv("MONITOR_HEARTBEAT_FILE", "/tmp/monitor_heartbeat"))
        self._last_heartbeat_touch: datetime | None = None

    def _load_monitors(self):
        """Load monitors from configuration"""
        enabled_coins = self.config.get_enabled_coins()
        for coin_config in enabled_coins:
            monitor = PriceMonitor(
                coin_config,
                self.notifier,
                volume_alert_cooldown_seconds=self.config.volume_alert_cooldown_seconds,
                volatility_alert_cooldown_seconds=self.config.volatility_alert_cooldown_seconds,
                milestone_alert_cooldown_seconds=self.config.milestone_alert_cooldown_seconds
            )
            self.monitors[coin_config.symbol] = monitor
            logger.info(f"✓ 已加载 {coin_config}")

        if not self.monitors:
            logger.warning("配置中没有启用的币种！")

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        # Store original signal handlers
        self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)

        logger.debug("Signal handlers registered for SIGINT and SIGTERM")

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals (SIGINT, SIGTERM)."""
        sig_name = signal.Signals(signum).name
        logger.info(f"收到信号 {sig_name} ({signum})，开始优雅关闭...")

        # Restore original signal handler FIRST to prevent race condition
        # This ensures a second signal triggers immediate termination
        original_handler = self._original_sigint if signum == signal.SIGINT else self._original_sigterm
        self._restore_signal_handler(signum, original_handler)

        # Set the shutdown event to stop the WebSocket
        self._shutdown_event.set()

    @staticmethod
    def _restore_signal_handler(signum: int, original_handler) -> None:
        """Restore original signal handler, handling cross-platform differences."""
        try:
            signal.signal(signum, original_handler)
        except (ValueError, OSError):
            # Signal might not be available on this platform (e.g., Windows)
            pass

    async def _send_shutdown_notification(self) -> None:
        """Send shutdown notification via Telegram."""
        now = now_in_configured_timezone()
        uptime = "未知"

        if self.ws_client:
            stats = self.ws_client.get_statistics()
            uptime_seconds = stats.get('uptime_seconds', 0)
            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            uptime = f"{hours}h {minutes}m"

        message = (
            f"👋 <b>加密货币价格监控已停止</b>\n"
            f"⏱️ {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"⌛ 运行时间: {uptime}\n"
            f"🪙 监控币种: {len(self.monitors)} 个\n"
            f"📊 状态: 优雅关闭"
        )
        try:
            self.notifier.send_message(message)
        except Exception as e:
            logger.error(f"发送关闭通知失败: {e}")

    async def _on_price_update(self, symbol: str, price: float):
        """Callback function for WebSocket price updates"""
        monitor = self.monitors.get(symbol)
        if not monitor:
            return

        # Check price for alerts (returns None if price hasn't changed)
        output = monitor.check(price)
        self._touch_heartbeat()

        # Only add to pending updates if price has changed (thread-safe)
        if output is not None:
            async with self._update_lock:
                self._pending_updates.append(output)

        # Print updates periodically
        current_time = now_in_configured_timezone()
        if (
            self.last_print_time is None
            or (current_time - self.last_print_time).total_seconds() >= self.print_interval
        ):
            await self._print_updates()
            self.last_print_time = current_time

    async def _on_kline_update(self, symbol: str, price: float, volume: float, is_closed: bool):
        """Callback function for WebSocket kline updates (volume monitoring)"""
        monitor = self.monitors.get(symbol)
        if not monitor:
            logger.warning(f"未找到交易对的监控器: {symbol}")
            return

        # Only check volume when kline is closed
        if is_closed:
            volume_info = monitor.check_volume_anomaly(price, volume)

            # Store volume info for display
            if volume_info:
                monitor.latest_volume_info = volume_info

    async def _print_updates(self):
        """Print accumulated price updates"""
        async with self._update_lock:
            if not self._pending_updates:
                return

            updates_to_print = list(self._pending_updates)
            self._pending_updates.clear()

        timestamp = now_in_configured_timezone().strftime('%H:%M:%S')
        logger.info(f"实时价格更新 [{timestamp}]:")
        for update in updates_to_print:
            logger.info(f"  {update}")

        try:
            print(f"[{timestamp}] 实时更新:")
            for update in updates_to_print:
                print(f"  {update}")
            print()
        except (OSError, IOError):
            # Handle broken pipe when running in background
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
            logger.warning(f"更新心跳文件失败 '{self._heartbeat_file}': {e}")

    async def _on_disconnect(self, reason: str) -> None:
        """Handle WebSocket disconnect event."""
        self._last_disconnect_reason = reason
        self._disconnect_alert_time = now_in_configured_timezone()

        message = (
            f"🚨🚨【连接断开警报】🚨🚨\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⚠️ 价格监控连接已中断！\n"
            f"📡 连接状态: 已断开\n"
            f"🔍 断开原因: {reason}\n"
            f"⏱️ {self._disconnect_alert_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💡 系统正在尝试自动重连..."
        )

        # Use async notification to avoid blocking
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(asyncio.to_thread(self.notifier.send_message, message))
            task.add_done_callback(self._on_disconnect_done)
        except Exception as e:
            logger.error(f"发送断开告警失败: {e}")

    def _on_disconnect_done(self, task: asyncio.Task) -> None:
        """Handle disconnect notification completion."""
        if task.cancelled():
            return
        err = task.exception()
        if err:
            logger.error(f"断开告警发送失败: {err}")
        else:
            logger.info(f"断开告警已发送: {task.result()}")

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
            logger.error(f"重连告警发送失败: {err}")
        else:
            logger.info(f"重连告警已发送: {task.result()}")

    async def _on_reconnect(self, attempt_count: int) -> None:
        """Handle WebSocket reconnect success."""
        now = now_in_configured_timezone()
        downtime = ""
        if self._disconnect_alert_time:
            downtime_seconds = (now - self._disconnect_alert_time).total_seconds()
            downtime = self._format_downtime(downtime_seconds)

        message = (
            f"✅✅【连接恢复通知】✅✅\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📡 价格监控已恢复正常\n"
            f"🔄 重连次数: {attempt_count} 次\n"
            f"⏱️ 中断时长: {downtime if downtime else '未知'}\n"
            f"⏱️ {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━"
        )

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(asyncio.to_thread(self.notifier.send_message, message))
            task.add_done_callback(self._on_reconnect_done)
            self._disconnect_alert_time = None
            self._last_disconnect_reason = None
        except Exception as e:
            logger.error(f"发送重连告警失败: {e}")

    async def run(self):
        """Start WebSocket monitoring"""
        print(f"\n{'='*60}")
        print(f"启动多币种价格监控 (WebSocket 模式)")
        print(f"{'='*60}")
        print(f"监控币种: {len(self.monitors)} 个")
        print(f"连接方式: WebSocket 实时推送")
        print(f"{'='*60}\n")

        if not self.monitors:
            logger.error("没有配置启用的币种。请至少设置一个 *_ENABLED=true")
            return

        # Test Telegram connection
        if not self.notifier.test_connection():
            logger.warning("测试消息发送失败。请检查 Telegram 配置。")

        # Get list of symbols to monitor
        symbols = list(self.monitors.keys())
        self._touch_heartbeat()

        # Create WebSocket client
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
            max_reconnect_attempts=None,  # Infinite reconnect
        )

        try:
            # Start WebSocket and wait for shutdown signal
            ws_task = asyncio.create_task(self.ws_client.start())
            shutdown_task = asyncio.create_task(self._shutdown_event.wait())

            # Wait for either WebSocket to complete or shutdown signal
            done, pending = await asyncio.wait(
                [ws_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Surface WebSocket task failures instead of exiting silently
            if ws_task in done:
                try:
                    ws_task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("WebSocket 客户端任务异常退出")
                    raise
                else:
                    if not self._shutdown_event.is_set():
                        raise RuntimeError("WebSocket 客户端意外退出（未收到关闭信号）")

            # If shutdown was triggered, handle graceful shutdown
            if self._shutdown_event.is_set():
                logger.info("正在优雅关闭...")
                await self.ws_client.stop()
                await self._send_shutdown_notification()

        except KeyboardInterrupt:
            logger.info("\n正在停止 WebSocket 监控 (键盘中断)...")
            if self.ws_client:
                await self.ws_client.stop()
            self.notifier.send_message("👋 加密货币价格监控已停止")
        except Exception:
            logger.exception("WebSocket 监控出现意外错误")
            if self.ws_client:
                await self.ws_client.stop()
            raise

    async def print_statistics(self):
        """Print WebSocket connection statistics"""
        if self.ws_client:
            stats = self.ws_client.get_statistics()
            print(f"\n📊 WebSocket 统计:")
            print(f"  状态: {stats['state']}")
            print(f"  接收消息: {stats['messages_received']}")
            print(f"  重连次数: {stats['reconnect_count']}")
            print(f"  运行时间: {stats['uptime_seconds']:.1f}秒")
            if stats['last_message_time']:
                print(f"  最后更新: {stats['last_message_time']}")


def test_volatility_alert():
    """Test volatility monitoring by sending a test alert"""
    print("\n=== 测试波动监控 ===\n")

    config = ConfigManager()
    notifier = TelegramNotifier()
    enabled_coins = config.get_enabled_coins()

    # Use context manager to ensure proper cleanup
    with BinancePriceFetcher() as fetcher:
        for coin_config in enabled_coins:
            try:
                price = fetcher.get_current_price(coin_config.symbol)
                if price:
                    # Calculate a fake high volatility scenario
                    fake_high_price = price * 1.05
                    fake_low_price = price * 0.98
                    fake_volatility = ((fake_high_price - fake_low_price) / fake_low_price) * 100

                    coin = get_coin_display_name(coin_config.symbol)

                    print(f"测试 {coin}...")
                    print(f"  当前价格: {format_price(price)}")
                    print(f"  波动阈值: {coin_config.volatility_percent}%")
                    print(f"  模拟波动: {fake_volatility:.2f}%")

                    # Send test alert
                    message = (
                        f"🧪 <b>测试告警 - 波动监控</b>\n"
                        f"🪙 {coin_config.symbol}\n"
                        f"💰 当前价格: {format_price(price)}\n"
                        f"📊 告警阈值: {coin_config.volatility_percent}% / {coin_config.volatility_window}秒\n"
                        f"✅ 波动监控已激活\n"
                        f"📈 模拟告警: {fake_volatility:.2f}% 将触发告警!\n"
                        f"⏱️ {now_in_configured_timezone().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    notifier.send_message(message)
                    print(f"  ✓ 测试告警已发送!\n")
            except Exception as e:
                logger.error(f"测试 {coin_config.coin_name} 时出错: {e}")

    print("测试完成! 请检查 Telegram 中的测试告警。\n")


def show_status():
    """Show current monitoring status"""
    print("\n" + "="*60)
    print("加密货币价格监控状态")
    print("="*60 + "\n")

    config = ConfigManager()
    enabled_coins = config.get_enabled_coins()

    # Use context manager to ensure proper cleanup
    with BinancePriceFetcher() as fetcher:
        for coin_config in enabled_coins:
            try:
                price = fetcher.get_current_price(coin_config.symbol)
                if price:
                    threshold_str = format_threshold(coin_config.integer_threshold)
                    emoji = get_coin_emoji(coin_config.coin_name)

                    print(f"{emoji} 🪙 {coin_config.coin_name}")
                    print(f"   交易对: {coin_config.symbol}")
                    print(f"   当前价格: {format_price(price)}")
                    print(f"   里程碑阈值: 每 {threshold_str}")
                    print(f"   波动告警: {coin_config.volatility_percent}% / {coin_config.volatility_window}秒")
                    print()
            except Exception as e:
                logger.error(f"获取 {coin_config.coin_name} 状态时出错: {e}")

    print("="*60 + "\n")


def main():
    """Main entry point"""
    load_environment()

    # Setup logging
    setup_logging()

    # Load configuration
    config = ConfigManager()

    # Check command line arguments
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "--test":
            test_volatility_alert()
            return
        elif arg == "--status":
            show_status()
            return
        elif arg in ["--help", "-h"]:
            print(__doc__)
            return

    # Default: Use WebSocket mode
    try:
        monitor = WebSocketMultiCoinMonitor(config)
        asyncio.run(monitor.run())
    except KeyboardInterrupt:
        logger.info("\n正在优雅关闭...")


if __name__ == "__main__":
    main()

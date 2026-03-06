"""
Price monitoring primitives and alert evaluation logic.
"""

import asyncio
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from common import (
    CoinConfig,
    TelegramNotifier,
    format_price,
    format_threshold,
    get_coin_display_name,
    get_coin_emoji,
    now_in_configured_timezone,
    logger,
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
    """Monitor price changes for a single coin."""

    MIN_PRICE_SAMPLE_INTERVAL_SECONDS = 0.25
    MIN_PRICE_HISTORY_SAMPLES = 100
    MAX_PRICE_HISTORY_SAMPLES = 600
    MIN_VOLUME_DATA_POINTS = 3
    MIN_VOLUME_VALUE = 0.0001
    KLINE_INTERVAL_SECONDS = 60

    def __init__(
        self,
        config: CoinConfig,
        notifier: TelegramNotifier,
        volume_alert_cooldown_seconds: int = 5,
        volatility_alert_cooldown_seconds: int = 60,
        milestone_alert_cooldown_seconds: int = 600,
    ):
        self.config = config
        self.notifier = notifier

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
        self.last_processed_price: float | None = None

        self.last_milestone_notification_time: datetime | None = None
        self.milestone_cooldown_seconds = milestone_alert_cooldown_seconds

        self.last_volatility_notification_time: datetime | None = None
        self.volatility_cooldown_seconds = volatility_alert_cooldown_seconds
        self.last_cumulative_volatility: float = 0.0

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
        self.latest_volume_info: str | None = None
        self._notification_tasks: set[asyncio.Task] = set()

    def _calculate_milestone(self, price: float, threshold: float) -> float:
        """Calculate the milestone for a given price and threshold."""
        if threshold <= 0:
            raise ValueError(f"Invalid threshold for {self.config.symbol}: {threshold}")

        epsilon = max(threshold * 1e-9, 1e-12)
        return math.floor((price + epsilon) / threshold) * threshold

    def _is_in_milestone_cooldown(self, coin: str) -> bool:
        """Check if milestone notification is in cooldown period."""
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
        """Send milestone notification and update tracking."""
        coin = get_coin_display_name(self.config.symbol)
        previous_price = self.last_price
        is_up = previous_price is None or current_price > previous_price
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

        milestone_str = format_threshold(current_milestone)
        logger.info(f"[{coin}] Milestone crossed: {milestone_str}")

    def check_integer_milestone(self, current_price: float) -> bool:
        """Check if price reached an integer milestone using crossing detection."""
        threshold = self.config.integer_threshold
        coin = get_coin_display_name(self.config.symbol)

        if self.last_price is None:
            self.last_price = current_price
            return False

        try:
            current_milestone = self._calculate_milestone(current_price, threshold)
            last_milestone = self._calculate_milestone(self.last_price, threshold)
        except ValueError as e:
            logger.error(str(e))
            self.last_price = current_price
            return False

        if last_milestone != current_milestone:
            if self._is_in_milestone_cooldown(coin):
                self.last_price = current_price
                return False

            self._send_milestone_notification(current_price, current_milestone)
            return True

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

        cutoff_time = current_time - timedelta(seconds=self.config.volatility_window)
        while self.price_history and self.price_history[0].timestamp < cutoff_time:
            self.price_history.popleft()

        return [p.price for p in self.price_history]

    def _calculate_std_dev_metric(self, prices: list[float]) -> float:
        """Calculate standard deviation percentage."""
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        std_dev = variance ** 0.5
        return (std_dev / mean_price) * 100 if mean_price > 0 else 0

    def _calculate_cumulative_metric(self, prices: list[float]) -> float:
        """Calculate cumulative volatility percentage."""
        if len(prices) < 2:
            return 0.0
        cumulative_change = sum(abs(prices[i] - prices[i - 1]) for i in range(1, len(prices)))
        return (cumulative_change / prices[0]) * 100 if prices[0] > 0 else 0

    def _calculate_range_metric(self, prices: list[float]) -> float:
        """Calculate min/max range volatility percentage."""
        min_price = min(prices)
        max_price = max(prices)
        return ((max_price - min_price) / min_price) * 100 if min_price > 0 else 0

    def _calculate_acceleration_metric(self, prices: list[float]) -> float:
        """Calculate volatility acceleration."""
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
        """Evaluate whether the current window exceeds volatility thresholds."""
        std_dev_pct = metrics.std_dev_pct
        cumulative_volatility_pct = metrics.cumulative_volatility_pct
        range_volatility_pct = metrics.range_volatility_pct
        acceleration = metrics.acceleration

        cumulative_alert = False
        if cumulative_volatility_pct >= threshold:
            if cumulative_volatility_pct > self.last_cumulative_volatility:
                cumulative_alert = True

        self.last_cumulative_volatility = cumulative_volatility_pct

        is_volatile = (
            std_dev_pct >= threshold * 0.7
            or cumulative_alert
            or range_volatility_pct >= threshold
            or (acceleration >= 2.0 and std_dev_pct >= threshold * 0.3)
        )

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
        """Check if volatility notification is in cooldown period."""
        if not self.last_volatility_notification_time:
            return False
        time_since_last = (current_time - self.last_volatility_notification_time).total_seconds()
        return time_since_last < self.volatility_cooldown_seconds

    def _send_volatility_alert(self, current_price: float, reasons: list[str]) -> None:
        """Send volatility alert notification."""
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
        log_reasons = (
            ", ".join(reasons)
            .replace("标准差", "std dev")
            .replace("累计波动", "cumulative volatility")
            .replace("区间波动", "range volatility")
            .replace("加速度", "acceleration")
        )
        logger.info(f"[{coin}] High volatility detected - {log_reasons}")

        self.last_volatility_notification_time = current_time

    def check_volatility(self, current_price: float) -> str | None:
        """Check price history for volatility thresholds."""
        prices = self._update_price_history(current_price)

        if len(prices) < 3:
            return None

        metrics = self._build_volatility_metrics(prices)
        volatility_info = (
            f"σ:{metrics.std_dev_pct:.2f}% "
            f"Σ:{metrics.cumulative_volatility_pct:.2f}% "
            f"R:{metrics.range_volatility_pct:.2f}%"
        )

        threshold = self.config.volatility_percent
        is_volatile, reasons = self._evaluate_volatility_thresholds(metrics, threshold)

        if self._is_in_volatility_cooldown(now_in_configured_timezone()):
            return volatility_info

        if is_volatile:
            self._send_volatility_alert(current_price, reasons)

        return volatility_info

    def check_volume_anomaly(self, current_price: float, volume: float) -> str | None:
        """Check for sudden spikes in trading volume."""
        if volume <= 0 or current_price <= 0:
            logger.warning(
                f"[{self.config.symbol}] Invalid volume data: price={current_price}, volume={volume}"
            )
            return None

        current_time = now_in_configured_timezone()
        self.volume_history.append(
            VolumeData(
                price=current_price,
                volume=volume,
                timestamp=current_time,
            )
        )

        cutoff_time = current_time - timedelta(seconds=self.volume_window_seconds)
        while self.volume_history and self.volume_history[0].timestamp < cutoff_time:
            self.volume_history.popleft()

        if len(self.volume_history) < self.MIN_VOLUME_DATA_POINTS:
            return None

        volumes = [entry.volume for entry in self.volume_history]
        baseline_volumes = volumes[:-1]
        avg_volume = sum(baseline_volumes) / len(baseline_volumes)
        current_volume = volumes[-1]

        if avg_volume <= 0:
            return None

        volume_multiplier = current_volume / avg_volume
        volume_alert_multiplier = getattr(self.config, "volume_alert_multiplier", 10.0)

        if self.last_volume_alert_time:
            time_since_last = (current_time - self.last_volume_alert_time).total_seconds()
            if time_since_last < self.volume_alert_cooldown_seconds:
                return f"V:{volume_multiplier:.1f}x"

        if volume_multiplier >= volume_alert_multiplier:
            coin = get_coin_display_name(self.config.symbol)
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
            logger.info(
                f"[{coin}] Volume anomaly detected: {volume_multiplier:.1f}x "
                f"(current:{current_volume:,.0f}, avg:{avg_volume:,.0f})"
            )

            self.last_volume_alert_time = current_time
            return f"V:{volume_multiplier:.1f}x🚨"

        return f"V:{volume_multiplier:.1f}x"

    def check(self, current_price: float) -> str | None:
        """Check price and return the formatted terminal output line."""
        if self.last_processed_price is not None:
            price_diff = abs(current_price - self.last_processed_price)
            base_min_change = 0.001 if current_price >= 1 else 0.0001
            min_change = min(base_min_change, self.config.integer_threshold)

            if price_diff < min_change:
                return None

        self.last_processed_price = current_price

        coin = get_coin_display_name(self.config.symbol)
        milestone_alert = self.check_integer_milestone(current_price)
        volatility_info = self.check_volatility(current_price)

        emoji = get_coin_emoji(coin)
        output = f"{emoji} [{coin}] {format_price(current_price)}"
        if milestone_alert:
            output += " 🎯"
        if volatility_info:
            output += f" 📊{volatility_info}"
        if self.latest_volume_info:
            output += f" {self.latest_volume_info}"

        return output

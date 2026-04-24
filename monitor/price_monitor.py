"""
Price monitoring primitives and alert evaluation logic.
"""

import asyncio
import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from common.config import CoinConfig
from common.logging import logger
from common.notifications import TelegramNotifier
from common.runtime import BackgroundTaskSet
from common.utils import (
    format_price,
    format_threshold,
    get_coin_display_name,
    get_coin_emoji,
    now_in_configured_timezone,
)

from .alert_evaluators import (
    MilestoneEvaluator,
    VolatilityEvaluator,
    VolatilityMetrics,
    VolumeAnomalyEvaluator,
)
from .alerts import (
    render_milestone_alert,
    render_volatility_alert,
    render_volume_alert,
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
        self.milestone_evaluator = MilestoneEvaluator(
            symbol=config.symbol,
            threshold=config.integer_threshold,
        )
        self.volatility_evaluator = VolatilityEvaluator()
        self.volume_evaluator = VolumeAnomalyEvaluator()

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
        self._notification_task_set = BackgroundTaskSet(cleanup_error_message=None)
        self._notification_tasks = self._notification_task_set.tasks

    def _calculate_milestone(self, price: float, threshold: float) -> float:
        """Calculate the milestone for a given price and threshold."""
        if threshold == self.milestone_evaluator.threshold:
            return self.milestone_evaluator.calculate_milestone(price)
        return MilestoneEvaluator(self.config.symbol, threshold).calculate_milestone(price)

    def _is_in_milestone_cooldown(self, coin: str, _now: datetime | None = None) -> bool:
        """Check if milestone notification is in cooldown period."""
        if not self.last_milestone_notification_time:
            return False

        now = _now or now_in_configured_timezone()
        time_since_last = (now - self.last_milestone_notification_time).total_seconds()
        if time_since_last >= self.milestone_cooldown_seconds:
            return False

        logger.debug(f"[{coin}] 全局冷却中 (已过 {time_since_last:.0f}s)")
        return True

    def _on_notification_done(self, task: asyncio.Task[bool]) -> None:
        """Cleanup completed async notification task and log failures."""
        self._notification_tasks.discard(task)
        if task.cancelled():
            return
        try:
            sent = task.result()
        except Exception:
            logger.exception(f"[{self.config.symbol}] Telegram 通知发送失败")
            return

        if not sent:
            logger.error(f"[{self.config.symbol}] Telegram 通知发送返回 false")

    async def flush_notification_tasks(self) -> None:
        """Wait for any queued notification tasks to finish."""
        await self._notification_task_set.flush()

    def _send_notification(self, message: str, on_success: Callable[[], None] | None = None) -> bool:
        """Send notification without blocking the event loop."""
        def _handle_success(sent: bool) -> bool:
            if sent and on_success is not None:
                on_success()
            return sent

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            sent = self.notifier.send_message(message)
            return _handle_success(sent)

        async def _send_async() -> bool:
            sent = await asyncio.to_thread(self.notifier.send_message, message)
            return _handle_success(sent)

        task = self._notification_task_set.track(loop.create_task(_send_async()))
        task.add_done_callback(self._on_notification_done)
        return True

    def _send_milestone_notification(self, current_price: float, current_milestone: float):
        """Send milestone notification and update tracking."""
        coin = get_coin_display_name(self.config.symbol)
        previous_price = self.last_price
        is_up = previous_price is None or current_price > previous_price
        _direction = "📈" if is_up else "📉"

        now = now_in_configured_timezone()

        # Set cooldown immediately to prevent duplicate notifications from
        # rapid WebSocket updates arriving before the async send completes.
        self.last_price = current_price
        self.last_milestone_notification_time = now

        message = render_milestone_alert(
            symbol=self.config.symbol,
            current_price=current_price,
            is_up=is_up,
            current_time=now,
        )

        def _log_sent() -> None:
            milestone_str = format_threshold(current_milestone)
            logger.info(f"[{coin}] 里程碑突破: {milestone_str}")

        self._send_notification(message, on_success=_log_sent)

    def check_integer_milestone(self, current_price: float, _now: datetime | None = None) -> bool:
        """Check if price reached an integer milestone using crossing detection."""
        threshold = self.config.integer_threshold
        coin = get_coin_display_name(self.config.symbol)

        if self.last_price is None:
            self.last_price = current_price
            return False

        try:
            evaluator = self.milestone_evaluator
            if threshold != evaluator.threshold:
                evaluator = MilestoneEvaluator(self.config.symbol, threshold)
            crossed, current_milestone = evaluator.has_crossed(
                self.last_price,
                current_price,
            )
        except ValueError as e:
            logger.error(str(e))
            self.last_price = current_price
            return False

        if crossed:
            if self._is_in_milestone_cooldown(coin, _now=_now):
                self.last_price = current_price
                return False

            self._send_milestone_notification(current_price, current_milestone)
            return True

        self.last_price = current_price
        return False

    def _append_price_sample(self, current_price: float, current_time: datetime) -> None:  # noqa: E501 — keep param name explicit
        """Add or refresh the most recent representative price sample."""
        if self.price_history:
            elapsed_seconds = (current_time - self.price_history[-1].timestamp).total_seconds()
            if elapsed_seconds < self.price_sample_interval_seconds:
                self.price_history[-1] = PriceData(current_price, current_time)
                return

        self.price_history.append(PriceData(current_price, current_time))

    def _update_price_history(self, current_price: float, _now: datetime | None = None) -> list[float]:
        """Update price history and return rolling window prices."""
        current_time = _now or now_in_configured_timezone()
        self._append_price_sample(current_price, current_time)

        cutoff_time = current_time - timedelta(seconds=self.config.volatility_window)
        while self.price_history and self.price_history[0].timestamp < cutoff_time:
            self.price_history.popleft()

        return [p.price for p in self.price_history]

    def _calculate_std_dev_metric(self, prices: list[float]) -> float:
        """Calculate standard deviation percentage."""
        return self.volatility_evaluator.calculate_std_dev_pct(prices)

    def _calculate_cumulative_metric(self, prices: list[float]) -> float:
        """Calculate cumulative volatility percentage."""
        return self.volatility_evaluator.calculate_cumulative_pct(prices)

    def _calculate_range_metric(self, prices: list[float]) -> float:
        """Calculate min/max range volatility percentage."""
        return self.volatility_evaluator.calculate_range_pct(prices)

    def _calculate_acceleration_metric(self, prices: list[float]) -> float:
        """Calculate volatility acceleration."""
        return self.volatility_evaluator.calculate_acceleration(prices)

    def _build_volatility_metrics(self, prices: list[float]) -> VolatilityMetrics:
        """Build volatility metrics from the current rolling price window."""
        return self.volatility_evaluator.build_metrics(prices)

    def _evaluate_volatility_thresholds(
        self,
        metrics: VolatilityMetrics,
        threshold: float,
    ) -> tuple[bool, list[str]]:
        """Evaluate whether the current window exceeds volatility thresholds."""
        evaluation = self.volatility_evaluator.evaluate(
            metrics,
            threshold,
            self.last_cumulative_volatility,
        )
        self.last_cumulative_volatility = evaluation.next_cumulative_volatility

        return evaluation.is_volatile, evaluation.reasons

    def _is_in_volatility_cooldown(self, current_time: datetime | None = None) -> bool:
        """Check if volatility notification is in cooldown period."""
        _time = current_time or now_in_configured_timezone()
        if not self.last_volatility_notification_time:
            return False
        time_since_last = (_time - self.last_volatility_notification_time).total_seconds()
        return time_since_last < self.volatility_cooldown_seconds

    def _send_volatility_alert(self, current_price: float, reasons: list[str]) -> None:
        """Send volatility alert notification."""
        current_time = now_in_configured_timezone()
        change = current_price - self.price_history[0].price
        change_percent = (change / self.price_history[0].price) * 100
        coin = get_coin_display_name(self.config.symbol)

        message = render_volatility_alert(
            symbol=self.config.symbol,
            current_price=current_price,
            volatility_window=self.config.volatility_window,
            sample_count=len(self.price_history),
            reasons=reasons,
            change_percent=change_percent,
            current_time=current_time,
        )
        log_reasons = (
            ", ".join(reasons)
            .replace("标准差", "std dev")
            .replace("累计波动", "cumulative volatility")
            .replace("区间波动", "range volatility")
            .replace("加速度", "acceleration")
        )

        # Set cooldown immediately to prevent duplicate notifications.
        self.last_volatility_notification_time = current_time

        def _log_sent() -> None:
            logger.info(f"[{coin}] 检测到高波动 - {log_reasons}")

        self._send_notification(message, on_success=_log_sent)

    def check_volatility(self, current_price: float, _now: datetime | None = None) -> str | None:
        """Check price history for volatility thresholds."""
        now = _now or now_in_configured_timezone()
        prices = self._update_price_history(current_price, _now=now)

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

        if self._is_in_volatility_cooldown(now):
            return volatility_info

        if is_volatile:
            self._send_volatility_alert(current_price, reasons)

        return volatility_info

    def check_volume_anomaly(self, current_price: float, volume: float) -> str | None:
        """Check for sudden spikes in trading volume."""
        if volume <= 0 or current_price <= 0:
            logger.warning(
                f"[{self.config.symbol}] 成交量数据无效: price={current_price}, volume={volume}"
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

        evaluation = self.volume_evaluator.evaluate(
            [entry.volume for entry in self.volume_history],
            self.config.volume_alert_multiplier,
        )
        if evaluation is None:
            return None

        volume_multiplier = evaluation.volume_multiplier

        if self.last_volume_alert_time:
            time_since_last = (current_time - self.last_volume_alert_time).total_seconds()
            if time_since_last < self.volume_alert_cooldown_seconds:
                return f"V:{volume_multiplier:.1f}x"

        if evaluation.should_alert:
            coin = get_coin_display_name(self.config.symbol)
            first_price = self.volume_history[0].price
            price_change = current_price - first_price
            price_change_pct = (price_change / first_price) * 100 if first_price > 0 else 0
            message = render_volume_alert(
                symbol=self.config.symbol,
                current_price=current_price,
                price_change_pct=price_change_pct,
                volume_multiplier=volume_multiplier,
                current_volume=evaluation.current_volume,
                avg_volume=evaluation.avg_volume,
                current_time=current_time,
            )

            # Set cooldown immediately to prevent duplicate notifications.
            self.last_volume_alert_time = current_time

            def _log_sent() -> None:
                logger.info(
                    f"[{coin}] 成交量异常: {volume_multiplier:.1f}x "
                    f"(当前:{evaluation.current_volume:,.0f}, 均值:{evaluation.avg_volume:,.0f})"
                )

            self._send_notification(message, on_success=_log_sent)
            return f"V:{volume_multiplier:.1f}x🚨"

        return f"V:{volume_multiplier:.1f}x"

    def check(self, current_price: float) -> str | None:
        """Check price and return the formatted terminal output line."""
        should_emit_output = True
        if self.last_processed_price is not None:
            price_diff = abs(current_price - self.last_processed_price)
            base_min_change = 0.001 if current_price >= 1 else 0.0001
            min_change = min(base_min_change, self.config.integer_threshold)
            should_emit_output = price_diff >= min_change

        now = now_in_configured_timezone()
        coin = get_coin_display_name(self.config.symbol)
        milestone_alert = self.check_integer_milestone(current_price, _now=now)
        volatility_info = self.check_volatility(current_price, _now=now)

        if not should_emit_output:
            return None

        self.last_processed_price = current_price

        emoji = get_coin_emoji(coin)
        output = f"{emoji} [{coin}] {format_price(current_price)}"
        if milestone_alert:
            output += " 🎯"
        if volatility_info:
            output += f" 📊{volatility_info}"

        volume_info = self.latest_volume_info
        self.latest_volume_info = None
        if volume_info:
            output += f" {volume_info}"

        return output

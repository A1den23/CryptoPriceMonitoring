"""Pure alert evaluation helpers for price monitoring."""

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VolatilityMetrics:
    """Volatility metrics derived from a rolling price window."""

    std_dev_pct: float
    cumulative_volatility_pct: float
    range_volatility_pct: float
    acceleration: float


@dataclass(frozen=True, slots=True)
class VolatilityEvaluation:
    """Threshold evaluation result for a volatility window."""

    is_volatile: bool
    reasons: list[str]
    next_cumulative_volatility: float


@dataclass(frozen=True, slots=True)
class VolumeAnomalyEvaluation:
    """Volume anomaly evaluation result."""

    current_volume: float
    avg_volume: float
    volume_multiplier: float
    should_alert: bool


class MilestoneEvaluator:
    """Evaluate milestone crossings for one trading symbol."""

    def __init__(self, symbol: str, threshold: float) -> None:
        self.symbol = symbol
        self.threshold = threshold

    def calculate_milestone(self, price: float) -> float:
        """Calculate the milestone bucket for *price*."""
        if self.threshold <= 0:
            raise ValueError(f"Invalid threshold for {self.symbol}: {self.threshold}")

        epsilon = max(self.threshold * 1e-9, 1e-12)
        return math.floor((price + epsilon) / self.threshold) * self.threshold

    def has_crossed(self, previous_price: float, current_price: float) -> tuple[bool, float]:
        """Return whether a milestone changed and the current milestone."""
        current_milestone = self.calculate_milestone(current_price)
        last_milestone = self.calculate_milestone(previous_price)
        return last_milestone != current_milestone, current_milestone


class VolatilityEvaluator:
    """Build and evaluate rolling volatility metrics."""

    @staticmethod
    def calculate_std_dev_pct(prices: Sequence[float]) -> float:
        mean_price = sum(prices) / len(prices)
        variance = sum((price - mean_price) ** 2 for price in prices) / len(prices)
        std_dev = variance ** 0.5
        return (std_dev / mean_price) * 100 if mean_price > 0 else 0

    @staticmethod
    def calculate_cumulative_pct(prices: Sequence[float]) -> float:
        if len(prices) < 2:
            return 0.0
        cumulative_change = sum(
            abs(prices[index] - prices[index - 1])
            for index in range(1, len(prices))
        )
        return (cumulative_change / prices[0]) * 100 if prices[0] > 0 else 0

    @staticmethod
    def calculate_range_pct(prices: Sequence[float]) -> float:
        min_price = min(prices)
        max_price = max(prices)
        return ((max_price - min_price) / min_price) * 100 if min_price > 0 else 0

    @staticmethod
    def calculate_acceleration(prices: Sequence[float]) -> float:
        if len(prices) < 4:
            return 1
        recent_prices = prices[-4:]
        recent_changes = [
            abs(recent_prices[index] - recent_prices[index - 1])
            for index in range(1, len(recent_prices))
        ]
        avg_change = (sum(recent_changes) / len(recent_changes)) if recent_changes else 0
        return (max(recent_changes) / avg_change) if avg_change > 0 else 1

    def build_metrics(self, prices: Sequence[float]) -> VolatilityMetrics:
        """Build volatility metrics from the current rolling price window."""
        return VolatilityMetrics(
            std_dev_pct=self.calculate_std_dev_pct(prices),
            cumulative_volatility_pct=self.calculate_cumulative_pct(prices),
            range_volatility_pct=self.calculate_range_pct(prices),
            acceleration=self.calculate_acceleration(prices),
        )

    def evaluate(
        self,
        metrics: VolatilityMetrics,
        threshold: float,
        last_cumulative_volatility: float,
    ) -> VolatilityEvaluation:
        """Evaluate whether the current metrics exceed volatility thresholds."""
        cumulative_alert = (
            metrics.cumulative_volatility_pct >= threshold
            and metrics.cumulative_volatility_pct > last_cumulative_volatility
        )

        is_volatile = (
            metrics.std_dev_pct >= threshold * 0.7
            or cumulative_alert
            or metrics.range_volatility_pct >= threshold
            or (metrics.acceleration >= 2.0 and metrics.std_dev_pct >= threshold * 0.3)
        )

        reasons = []
        if is_volatile:
            if metrics.std_dev_pct >= threshold * 0.7:
                reasons.append(f"标准差: {metrics.std_dev_pct:.2f}%")
            if cumulative_alert:
                reasons.append(f"累计波动: {metrics.cumulative_volatility_pct:.2f}%")
            if metrics.range_volatility_pct >= threshold:
                reasons.append(f"区间波动: {metrics.range_volatility_pct:.2f}%")
            if metrics.acceleration >= 2.0 and metrics.std_dev_pct >= threshold * 0.3:
                reasons.append(f"加速度: {metrics.acceleration:.1f}x")

        return VolatilityEvaluation(
            is_volatile=is_volatile,
            reasons=reasons,
            next_cumulative_volatility=metrics.cumulative_volatility_pct,
        )


class VolumeAnomalyEvaluator:
    """Evaluate whether the latest volume sample is anomalous."""

    @staticmethod
    def evaluate(volumes: Sequence[float], alert_multiplier: float) -> VolumeAnomalyEvaluation | None:
        baseline_volumes = volumes[:-1]
        avg_volume = sum(baseline_volumes) / len(baseline_volumes)
        current_volume = volumes[-1]

        if avg_volume <= 0:
            return None

        volume_multiplier = current_volume / avg_volume
        return VolumeAnomalyEvaluation(
            current_volume=current_volume,
            avg_volume=avg_volume,
            volume_multiplier=volume_multiplier,
            should_alert=volume_multiplier >= alert_multiplier,
        )

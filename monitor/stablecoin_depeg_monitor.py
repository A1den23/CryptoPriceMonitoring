"""
Stablecoin depeg monitoring logic.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime

from common import DefiLlamaClient, StablecoinSnapshot, now_in_configured_timezone, logger


@dataclass(slots=True)
class StablecoinAlertState:
    is_depegged: bool = False
    last_alert_time: datetime | None = None


class StablecoinDepegMonitor:
    """Monitor stablecoin snapshots and alert on depegs."""

    def __init__(self, config, notifier, client: DefiLlamaClient):
        self.config = config
        self.notifier = notifier
        self.client = client
        self.threshold_percent = config.stablecoin_depeg_threshold_percent
        self.cooldown_seconds = config.stablecoin_depeg_alert_cooldown_seconds
        self.top_n = config.stablecoin_depeg_top_n
        self.poll_interval_seconds = config.stablecoin_depeg_poll_interval_seconds
        self._states: dict[str, StablecoinAlertState] = {}

    def _deviation_percent(self, price: float) -> float:
        return (price - 1.0) * 100

    def _is_depegged(self, price: float) -> bool:
        return price > (1 + self.threshold_percent / 100) or price < (1 - self.threshold_percent / 100)

    def _format_alert_message(self, snapshot: StablecoinSnapshot, deviation_percent: float, timestamp: datetime) -> str:
        return (
            f"🚨【稳定币脱锚警报】🚨\n"
            f"🪙 {snapshot.symbol} ({snapshot.name})\n"
            f"🏅 排名: #{snapshot.rank}\n"
            f"💰 当前价格: ${snapshot.price:.3f}\n"
            f"📉 偏离 $1: {deviation_percent:+.2f}%\n"
            f"⚠️ 阈值: ±{self.threshold_percent:.2f}%\n"
            f"⏱️ {timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _should_alert(self, state: StablecoinAlertState, now: datetime) -> bool:
        if not state.is_depegged:
            return True
        if state.last_alert_time is None:
            return True
        return (now - state.last_alert_time).total_seconds() >= self.cooldown_seconds

    def evaluate_snapshot(self, snapshot: StablecoinSnapshot) -> bool:
        state = self._states.setdefault(snapshot.symbol, StablecoinAlertState())
        if not self._is_depegged(snapshot.price):
            state.is_depegged = False
            return False

        now = now_in_configured_timezone()
        if not self._should_alert(state, now):
            state.is_depegged = True
            return False

        deviation_percent = self._deviation_percent(snapshot.price)
        self.notifier.send_message(self._format_alert_message(snapshot, deviation_percent, now))
        state.is_depegged = True
        state.last_alert_time = now
        return True

    def run_once(self) -> int:
        snapshots = self.client.fetch_stablecoins(top_n=self.top_n)
        alerts = 0
        for snapshot in snapshots:
            if self.evaluate_snapshot(snapshot):
                alerts += 1
        return alerts

    async def run(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception as exc:
                logger.error(f"Stablecoin depeg poll failed: {exc}")
            await asyncio.sleep(self.poll_interval_seconds)

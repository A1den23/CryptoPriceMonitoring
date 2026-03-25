"""
Stablecoin depeg monitoring logic.
"""

import asyncio
from html import escape
from dataclasses import dataclass
from datetime import datetime

from common.clients.defillama import DefiLlamaClient, StablecoinSnapshot
from common.logging import logger
from common.utils import now_in_configured_timezone


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
        safe_symbol = escape(snapshot.symbol)
        safe_name = escape(snapshot.name)
        return (
            f"🚨【稳定币脱锚警报】🚨\n"
            f"🪙 {safe_symbol} ({safe_name})\n"
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

    def _build_alert_message(self, snapshot: StablecoinSnapshot) -> tuple[str, datetime] | None:
        state = self._states.setdefault(snapshot.symbol, StablecoinAlertState())
        if not self._is_depegged(snapshot.price):
            state.is_depegged = False
            return None

        now = now_in_configured_timezone()
        if not self._should_alert(state, now):
            state.is_depegged = True
            return None

        deviation_percent = self._deviation_percent(snapshot.price)
        message = self._format_alert_message(snapshot, deviation_percent, now)
        state.is_depegged = True
        return message, now

    def _mark_alert_sent(self, symbol: str, alert_time: datetime) -> None:
        state = self._states.setdefault(symbol, StablecoinAlertState())
        state.is_depegged = True
        state.last_alert_time = alert_time

    def evaluate_snapshot(self, snapshot: StablecoinSnapshot) -> bool:
        alert = self._build_alert_message(snapshot)
        if alert is None:
            return False
        message, alert_time = alert
        sent = self.notifier.send_message(message)
        if sent:
            self._mark_alert_sent(snapshot.symbol, alert_time)
        return sent

    async def _send_alert(self, message: str) -> bool:
        return await asyncio.to_thread(self.notifier.send_message, message)

    async def run_once(self) -> int:
        snapshots = await self.client.fetch_stablecoins(top_n=self.top_n)
        alerts = 0
        for snapshot in snapshots:
            alert = self._build_alert_message(snapshot)
            if alert is None:
                continue
            message, alert_time = alert
            sent = await self._send_alert(message)
            if not sent:
                continue
            self._mark_alert_sent(snapshot.symbol, alert_time)
            alerts += 1
        logger.info(f"Stablecoin poll completed: snapshots={len(snapshots)}, alerts={alerts}")
        return alerts

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error(f"Stablecoin depeg poll failed: {exc}")
            await asyncio.sleep(self.poll_interval_seconds)

"""
Telegram notification utilities
"""

import threading
import time
from collections import deque

import requests

from .logging import logger


class TelegramNotifier:
    """Handle Telegram notifications with retry mechanism and rate limiting."""

    API_ORIGIN = "https://api.telegram.org"
    MAX_SEND_ATTEMPTS = 3
    RETRY_BACKOFF_MIN_SECONDS = 2
    RETRY_BACKOFF_MAX_SECONDS = 10
    MAX_RETRY_AFTER_SECONDS = 60

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5,
            pool_maxsize=10,
            max_retries=0,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        # Rate limiting: max 20 messages per minute (conservative)
        self._message_times: deque[float] = deque()
        self._rate_limit = 20  # messages
        self._rate_window = 60  # seconds
        self._rate_limit_condition = threading.Condition()

        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot_token 或 chat_id 未配置")

    def __repr__(self) -> str:
        """Avoid exposing credentials in debug output."""
        token_preview = "***" if self.bot_token else None
        return f"TelegramNotifier(bot_token={token_preview}, chat_id={self.chat_id!r})"

    def _build_api_url(self, method: str) -> str:
        """Build a Telegram Bot API URL without storing tokenized URLs on the instance."""
        return f"{self.API_ORIGIN}/bot{self.bot_token}/{method}"

    def _prune_rate_limit_window(self, now: float) -> None:
        """Remove expired rate limit entries."""
        while self._message_times and self._message_times[0] < now - self._rate_window:
            self._message_times.popleft()

    def _release_rate_limit_slot(self, reserved_at: float) -> None:
        """Release a reserved slot when the request fails."""
        with self._rate_limit_condition:
            try:
                self._message_times.remove(reserved_at)
            except ValueError:
                pass
            self._rate_limit_condition.notify()

    def _wait_for_rate_limit_slot(self) -> float:
        """Wait for a send slot instead of silently dropping an alert."""
        with self._rate_limit_condition:
            while True:
                now = time.time()
                self._prune_rate_limit_window(now)
                if len(self._message_times) < self._rate_limit:
                    self._message_times.append(now)
                    return now
                wait_seconds = max(
                    self._message_times[0] + self._rate_window - now,
                    0.1,
                )
                self._rate_limit_condition.wait(wait_seconds)

    @staticmethod
    def _error_payload(response) -> dict:
        """Read a Telegram error body, tolerating non-JSON responses."""
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _retry_after_seconds(self, response) -> float | None:
        """Return Telegram's capped retry_after, or None when absent or invalid."""
        parameters = self._error_payload(response).get("parameters")
        if not isinstance(parameters, dict):
            return None
        retry_after = parameters.get("retry_after")
        if isinstance(retry_after, bool) or not isinstance(retry_after, (int, float)):
            return None
        if not retry_after > 0:
            return None
        return min(retry_after, self.MAX_RETRY_AFTER_SECONDS)

    def _backoff_seconds(self, attempt: int) -> float:
        """Return the exponential backoff delay after a failed attempt."""
        return min(
            max(2 ** (attempt - 1), self.RETRY_BACKOFF_MIN_SECONDS),
            self.RETRY_BACKOFF_MAX_SECONDS,
        )

    def send_message(self, message: str) -> bool:
        """Send message via Telegram bot with retry mechanism and rate limiting."""
        if not self.bot_token or not self.chat_id:
            logger.error("Telegram bot_token 或 chat_id 未配置")
            return False

        attempt = 0
        while True:
            attempt += 1
            try:
                return self._send_once(message)
            except requests.exceptions.RequestException as exc:
                response = getattr(exc, "response", None)
                status_code = getattr(response, "status_code", None)
                failure = f"HTTP {status_code}" if status_code is not None else type(exc).__name__

                if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                    logger.error(
                        "Telegram API 拒绝消息，不再重试: %s %s",
                        failure,
                        self._error_payload(response).get("description", "未知错误"),
                    )
                    raise

                if attempt >= self.MAX_SEND_ATTEMPTS:
                    logger.error("Telegram 消息发送失败，已尝试 %d 次: %s", attempt, failure)
                    raise

                delay = None
                if status_code == 429:
                    delay = self._retry_after_seconds(response)
                if delay is None:
                    delay = self._backoff_seconds(attempt)
                logger.warning(
                    "Telegram 消息发送失败 (%s)，%.1f 秒后重试 (%d/%d)",
                    failure,
                    delay,
                    attempt,
                    self.MAX_SEND_ATTEMPTS,
                )
                time.sleep(delay)

    def _send_once(self, message: str) -> bool:
        """Perform a single rate-limited sendMessage request."""
        reserved_at = self._wait_for_rate_limit_slot()

        url = self._build_api_url("sendMessage")
        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        try:
            response = self.session.post(url, json=data, timeout=10)
            response.raise_for_status()
        except Exception:
            self._release_rate_limit_slot(reserved_at)
            raise

        try:
            payload = response.json()
        except ValueError:
            logger.error("Telegram API 返回了格式错误的 JSON")
            return False

        if not isinstance(payload, dict):
            logger.error("Telegram API 返回了意外的负载类型: %s", type(payload).__name__)
            return False

        if payload.get("ok") is False:
            logger.error(
                "Telegram API 拒绝消息: %s",
                payload.get("description", "未知错误"),
            )
            return False

        if payload.get("ok") is True:
            logger.info("Telegram 消息发送成功")
            return True

        logger.error("Telegram API 响应缺少明确的 ok=true")
        return False

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    def test_connection(self) -> bool:
        """Test Telegram bot connection."""
        try:
            return self.send_message(
                "🤖 <b>加密货币价格监控机器人</b> 已启动！\n\n"
                "正在监控多个加密货币价格..."
            )
        except Exception:
            logger.error("Telegram 连接测试失败")
            return False

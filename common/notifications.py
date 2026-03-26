"""
Telegram notification utilities
"""

import threading
import time
from collections import deque

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .logging import logger


class TelegramNotifier:
    """Handle Telegram notifications with retry mechanism and rate limiting."""

    API_ORIGIN = "https://api.telegram.org"

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
        self._rate_limit_lock = threading.Lock()

        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot_token or chat_id not configured")

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

    def _reserve_rate_limit_slot(self) -> float | None:
        """Reserve a send slot atomically so concurrent sends honor the limit."""
        now = time.time()
        with self._rate_limit_lock:
            self._prune_rate_limit_window(now)
            if len(self._message_times) >= self._rate_limit:
                return None
            self._message_times.append(now)
            return now

    def _release_rate_limit_slot(self, reserved_at: float) -> None:
        """Release a reserved slot when the request fails."""
        with self._rate_limit_lock:
            try:
                self._message_times.remove(reserved_at)
            except ValueError:
                pass

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True,
    )
    def send_message(self, message: str) -> bool:
        """Send message via Telegram bot with retry mechanism and rate limiting."""
        if not self.bot_token or not self.chat_id:
            logger.error("Telegram bot_token or chat_id not configured")
            return False

        # Check rate limit
        reserved_at = self._reserve_rate_limit_slot()
        if reserved_at is None:
            logger.warning("Telegram rate limit exceeded, dropping message")
            return False

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
            logger.error("Telegram API returned malformed JSON")
            return False

        if not isinstance(payload, dict):
            logger.error("Telegram API returned unexpected payload type: %s", type(payload).__name__)
            return False

        if payload.get("ok") is False:
            logger.error(
                "Telegram API rejected message: %s",
                payload.get("description", "unknown error"),
            )
            return False

        if payload.get("ok") is True:
            logger.info("Telegram message sent successfully")
            return True

        logger.error("Telegram API response missing explicit ok=true")
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
            logger.error("Telegram connection test failed")
            return False

"""
Telegram notification utilities
"""

import os
import threading
import time
from collections import deque

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .logging import logger


class TelegramNotifier:
    """Handle Telegram notifications with retry mechanism and rate limiting."""

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

        # Rate limiting: max 20 messages per minute (conservative)
        self._message_times: deque[float] = deque()
        self._rate_limit = 20  # messages
        self._rate_window = 60  # seconds
        self._rate_limit_lock = threading.Lock()

        # Validate token and construct URL only if configured
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot_token or chat_id not configured")
            self.base_url: str | None = None
        else:
            self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

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

        url = f"{self.base_url}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        try:
            response = requests.post(url, json=data, timeout=10)
            response.raise_for_status()
        except Exception:
            self._release_rate_limit_slot(reserved_at)
            raise
        logger.info("Telegram message sent successfully")
        return True

    def test_connection(self) -> bool:
        """Test Telegram bot connection."""
        try:
            return self.send_message(
                "🤖 <b>Crypto Price Monitoring Bot</b> is now active!\n\n"
                "Monitoring multiple cryptocurrencies..."
            )
        except Exception as e:
            logger.error(f"Telegram connection test failed: {e}")
            return False

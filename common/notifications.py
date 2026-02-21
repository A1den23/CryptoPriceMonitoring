"""
Telegram notification utilities
"""

import os
import time
from collections import deque
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .logging import logger


class TelegramNotifier:
    """Handle Telegram notifications with retry mechanism and rate limiting"""

    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

        # Rate limiting: max 20 messages per minute (conservative)
        self._message_times: deque[float] = deque()
        self._rate_limit = 20  # messages
        self._rate_window = 60  # seconds

        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot_token or chat_id not configured")

    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits"""
        now = time.time()

        # Remove old entries outside window
        while self._message_times and self._message_times[0] < now - self._rate_window:
            self._message_times.popleft()

        return len(self._message_times) < self._rate_limit

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True
    )
    def send_message(self, message: str) -> bool:
        """Send message via Telegram bot with retry mechanism and rate limiting"""
        if not self.bot_token or not self.chat_id:
            logger.error("Telegram bot_token or chat_id not configured")
            return False

        # Check rate limit
        if not self._check_rate_limit():
            logger.warning("Telegram rate limit exceeded, dropping message")
            return False

        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, json=data, timeout=10)
            response.raise_for_status()
            logger.info("Telegram message sent successfully")

            # Track message time for rate limiting
            self._message_times.append(time.time())
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending Telegram message: {e}")
            raise

    def test_connection(self) -> bool:
        """Test Telegram bot connection"""
        return self.send_message("🤖 <b>Crypto Price Monitoring Bot</b> is now active!\n\nMonitoring multiple cryptocurrencies...")

#!/usr/bin/env python3
"""
Common shared module for Crypto Price Monitoring Bot
Contains shared utilities, configuration, and base classes
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass

import requests
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# UTC+8 Timezone
UTC8 = timezone(timedelta(hours=8))

# Configure structured logging
def setup_logging(log_file: str = "logs/monitor.log", level: int = logging.INFO):
    """Setup structured logging with file and console handlers"""
    # Create logs directory if it doesn't exist
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # Configure logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        level=level,
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)


logger = logging.getLogger(__name__)


@dataclass
class CoinConfig:
    """Configuration for a single coin"""
    coin_name: str
    enabled: bool
    symbol: str
    integer_threshold: float
    volatility_percent: float
    volatility_window: int

    @classmethod
    def from_env(cls, coin_name: str) -> 'CoinConfig':
        """Create CoinConfig from environment variables"""
        return cls(
            coin_name=coin_name,
            enabled=os.getenv(f"{coin_name}_ENABLED", "false").lower() == "true",
            symbol=os.getenv(f"{coin_name}_SYMBOL", f"{coin_name}USDT"),
            integer_threshold=float(os.getenv(f"{coin_name}_INTEGER_THRESHOLD", "1000")),
            volatility_percent=float(os.getenv(f"{coin_name}_VOLATILITY_PERCENT", "3.0")),
            volatility_window=int(os.getenv(f"{coin_name}_VOLATILITY_WINDOW_SECONDS", "60"))
        )

    def __str__(self):
        threshold_str = f"{int(self.integer_threshold):,}" if self.integer_threshold >= 1 else f"{self.integer_threshold}"
        return (
            f"{self.coin_name}: enabled={self.enabled}, symbol={self.symbol}, "
            f"integer_threshold={threshold_str}, "
            f"volatility={self.volatility_percent}%/{self.volatility_window}s"
        )


class ConfigManager:
    """Centralized configuration management"""
    def __init__(self):
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.check_interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "5"))
        self.debug_mode = os.getenv("DEBUG", "false").lower() == "true"

        # Get coin list from env or use default
        coin_list = os.getenv("COIN_LIST", "BTC,ETH,SOL,USD1")
        self.coin_names = [coin.strip() for coin in coin_list.split(",")]

        # Load all coin configurations
        self.coins: Dict[str, CoinConfig] = {}
        self._load_coins()

    def _load_coins(self):
        """Load configurations for all coins"""
        for coin_name in self.coin_names:
            config = CoinConfig.from_env(coin_name)
            self.coins[coin_name] = config

    def get_enabled_coins(self) -> List[CoinConfig]:
        """Get list of enabled coin configurations"""
        return [config for config in self.coins.values() if config.enabled]

    def get_coin_config(self, coin_name: str) -> Optional[CoinConfig]:
        """Get configuration for specific coin"""
        return self.coins.get(coin_name)


def format_price(price: float) -> str:
    """Format price for display"""
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 10:
        return f"${price:.2f}"
    else:
        return f"${price:.4f}"


class BinanceAPIError(Exception):
    """Custom exception for Binance API errors"""
    pass


class BinancePriceFetcher:
    """Fetch prices from Binance API with retry mechanism"""

    def __init__(self, base_url: str = "https://api.binance.com/api/v3"):
        self.base_url = base_url
        self.session = requests.Session()
        # Set connection pool and timeouts
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0  # We handle retries with tenacity
        )
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.exceptions.RequestException, BinanceAPIError)),
        reraise=True
    )
    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price from Binance with retry mechanism"""
        try:
            url = f"{self.base_url}/ticker/price"
            params = {"symbol": symbol}
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data["price"])
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            raise BinanceAPIError(f"Failed to fetch price for {symbol}") from e
        except (KeyError, ValueError) as e:
            logger.error(f"Invalid response for {symbol}: {e}")
            raise BinanceAPIError(f"Invalid response format for {symbol}") from e

    def close(self):
        """Close the session"""
        self.session.close()


class AsyncBinancePriceFetcher:
    """Async fetcher for concurrent price requests"""

    def __init__(self, base_url: str = "https://api.binance.com/api/v3"):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        """Create async session when entering context"""
        timeout = aiohttp.ClientTimeout(total=10)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close async session when exiting context"""
        if self.session:
            await self.session.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, BinanceAPIError)),
        reraise=True
    )
    async def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price asynchronously with retry mechanism"""
        if not self.session:
            raise RuntimeError("Session not initialized. Use async context manager.")

        try:
            url = f"{self.base_url}/ticker/price"
            params = {"symbol": symbol}
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                return float(data["price"])
        except aiohttp.ClientError as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            raise BinanceAPIError(f"Failed to fetch price for {symbol}") from e
        except (KeyError, ValueError) as e:
            logger.error(f"Invalid response for {symbol}: {e}")
            raise BinanceAPIError(f"Invalid response format for {symbol}") from e

    async def get_multiple_prices(self, symbols: List[str]) -> Dict[str, Optional[float]]:
        """Fetch multiple prices concurrently"""
        tasks = {symbol: self.get_current_price(symbol) for symbol in symbols}
        results = {}

        for symbol, task in tasks.items():
            try:
                results[symbol] = await task
            except BinanceAPIError as e:
                logger.error(f"Failed to fetch {symbol} after retries: {e}")
                results[symbol] = None

        return results


class TelegramNotifier:
    """Handle Telegram notifications with retry mechanism"""

    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot_token or chat_id not configured")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True
    )
    def send_message(self, message: str) -> bool:
        """Send message via Telegram bot with retry mechanism"""
        if not self.bot_token or not self.chat_id:
            logger.error("Telegram bot_token or chat_id not configured")
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
            logger.info(f"Telegram message sent successfully")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending Telegram message: {e}")
            raise

    def test_connection(self) -> bool:
        """Test Telegram bot connection"""
        return self.send_message("🤖 <b>Crypto Price Monitoring Bot</b> is now active!\n\nMonitoring multiple cryptocurrencies...")


def get_coin_display_name(symbol: str) -> str:
    """Get display name for coin symbol"""
    return symbol.replace("USDT", "")


def get_coin_emoji(coin_name: str) -> str:
    """Get emoji for coin name"""
    return {"BTC": "₿", "ETH": "Ξ", "SOL": "◎", "USD1": "$1"}.get(coin_name, "🪙")

#!/usr/bin/env python3
"""
Common shared module for Crypto Price Monitoring Bot
Contains shared utilities, configuration, and base classes
"""

import os
import logging
import asyncio
import json
import signal
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Callable, Awaitable, Tuple, Union
from dataclasses import dataclass
from enum import Enum

import requests
import aiohttp
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv


def _restore_signal_handler(signum: int, original_handler):
    """Restore original signal handler, handling cross-platform differences"""
    try:
        signal.signal(signum, original_handler)
    except (ValueError, OSError):
        # Signal might not be available on this platform (e.g., Windows)
        pass

# Load environment variables
load_dotenv()

# UTC+8 Timezone
UTC8 = timezone(timedelta(hours=8))

# Configure structured logging
def _resolve_log_level(level: Optional[Union[int, str]]) -> int:
    """Resolve log level from explicit value or LOG_LEVEL/DEBUG env"""
    if level is not None:
        if isinstance(level, str):
            name = level.strip().upper()
            if name in logging._nameToLevel:
                return logging._nameToLevel[name]
            return logging.INFO
        return level

    env_level = os.getenv("LOG_LEVEL")
    if env_level:
        name = env_level.strip().upper()
        if name in logging._nameToLevel:
            return logging._nameToLevel[name]

    if os.getenv("DEBUG", "false").lower() == "true":
        return logging.DEBUG

    return logging.INFO


def setup_logging(log_file: str = "logs/monitor.log", level: Optional[Union[int, str]] = None):
    """Setup structured logging with file and console handlers"""
    handlers = [logging.StreamHandler()]

    # Try to add file handler, fall back to console only if permission denied
    try:
        # Create logs directory if it doesn't exist
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        # Add file handler
        handlers.append(logging.FileHandler(log_file))
    except (PermissionError, OSError) as e:
        # Fall back to console only if file logging fails
        print(f"Warning: Could not create log file '{log_file}': {e}")
        print("Logging to console only.")

    # Configure logging
    resolved_level = _resolve_log_level(level)
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        level=resolved_level,
        handlers=handlers
    )

    # Reduce sensitive/noisy logs (prevents Telegram bot token from appearing)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return logging.getLogger(__name__)


# Note: logger is initialized lazily to ensure proper configuration
# Use get_logger() to get the configured logger
def get_logger():
    """Get the configured logger"""
    return logging.getLogger(__name__)


# Backwards compatible module-level logger (will be configured after setup_logging is called)
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
    volume_alert_multiplier: float = 10.0  # Volume anomaly threshold (10x = 1000% increase)

    @classmethod
    def from_env(cls, coin_name: str) -> 'CoinConfig':
        """Create CoinConfig from environment variables"""
        return cls(
            coin_name=coin_name,
            enabled=os.getenv(f"{coin_name}_ENABLED", "false").lower() == "true",
            symbol=os.getenv(f"{coin_name}_SYMBOL", f"{coin_name}USDT"),
            integer_threshold=float(os.getenv(f"{coin_name}_INTEGER_THRESHOLD", "1000")),
            volatility_percent=float(os.getenv(f"{coin_name}_VOLATILITY_PERCENT", "3.0")),
            volatility_window=int(os.getenv(f"{coin_name}_VOLATILITY_WINDOW_SECONDS", "60")),
            volume_alert_multiplier=float(os.getenv(f"{coin_name}_VOLUME_ALERT_MULTIPLIER", "10.0"))
        )

    def __str__(self):
        threshold_str = f"{int(self.integer_threshold):,}" if self.integer_threshold >= 1 else f"{self.integer_threshold}"
        return (
            f"{self.coin_name}: enabled={self.enabled}, symbol={self.symbol}, "
            f"integer_threshold={threshold_str}, "
            f"volatility={self.volatility_percent}%/{self.volatility_window}s, "
            f"volume_alert={self.volume_alert_multiplier}x"
        )


class ConfigManager:
    """Centralized configuration management"""
    def __init__(self):
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        # Note: CHECK_INTERVAL_SECONDS is kept for backwards compatibility but not used
        # (WebSocket mode provides real-time updates without polling)
        self.check_interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "5"))
        self.debug_mode = os.getenv("DEBUG", "false").lower() == "true"
        # Volume alert cooldown (global default, can be overridden per coin in the future)
        self.volume_alert_cooldown_seconds = int(os.getenv("VOLUME_ALERT_COOLDOWN_SECONDS", "5"))
        # Volatility alert cooldown (time between volatility notifications)
        self.volatility_alert_cooldown_seconds = int(os.getenv("VOLATILITY_ALERT_COOLDOWN_SECONDS", "60"))
        # Milestone alert cooldown (global cooldown for any milestone crossing)
        self.milestone_alert_cooldown_seconds = int(os.getenv("MILESTONE_ALERT_COOLDOWN_SECONDS", "600"))
        # WebSocket keepalive and stale-connection detection
        self.ws_ping_interval_seconds = float(os.getenv("WS_PING_INTERVAL_SECONDS", "30"))
        self.ws_pong_timeout_seconds = float(os.getenv("WS_PONG_TIMEOUT_SECONDS", "10"))
        self.ws_message_timeout_seconds = float(os.getenv("WS_MESSAGE_TIMEOUT_SECONDS", "120"))

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

    def __enter__(self):
        """Support for 'with' statement"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close session when exiting 'with' block"""
        self.close()
        return False


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
        tasks = {symbol: asyncio.create_task(self.get_current_price(symbol)) for symbol in symbols}
        results: Dict[str, Optional[float]] = {}

        completed = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for symbol, result in zip(tasks.keys(), completed):
            if isinstance(result, Exception):
                logger.error(f"Failed to fetch {symbol} after retries: {result}")
                results[symbol] = None
            else:
                results[symbol] = result

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
    # Extended emoji mapping for common cryptocurrencies
    emoji_map = {
        "BTC": "₿",       # Bitcoin
        "ETH": "Ξ",       # Ethereum
        "SOL": "◎",       # Solana
        "USD1": "$1",     # USD1 stablecoin
        "USDT": "₮",      # Tether
        "USDC": "₮",      # USD Coin
        "XRP": "✕",       # Ripple
        "DOGE": "Ð",      # Dogecoin
        "ADA": "₳",       # Cardano
        "DOT": "•",       # Polkadot
        "AVAX": "▲",      # Avalanche
        "MATIC": "⬡",     # Polygon
        "LINK": "⬡",      # Chainlink
        "LTC": "Ł",       # Litecoin
        "BCH": "₿",       # Bitcoin Cash
        "BNB": "🅱️",       # Binance Coin
        "UNI": "🦄",      # Uniswap
        "AAVE": "👻",     # Aave
        "ATOM": "⚛️",      # Cosmos
        "XTZ": "ꜩ",       # Tezos
    }
    return emoji_map.get(coin_name, "🪙")


class ConnectionState(Enum):
    """WebSocket connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


class BinanceWebSocketClient:
    """
    Real-time Binance WebSocket client with auto-reconnect and heartbeat

    This class provides a robust WebSocket connection to Binance's real-time
    price stream, with automatic reconnection on failure and connection health monitoring.

    Usage:
        async def on_price_update(symbol, price):
            print(f"{symbol}: {price}")

        client = BinanceWebSocketClient(["BTCUSDT", "ETHUSDT"], on_price_update)
        await client.start()  # Runs forever until stop() is called
    """

    # Binance WebSocket endpoints
    BASE_WS_URL = "wss://stream.binance.com:9443/ws"
    BASE_COMBINED_WS_URL = "wss://stream.binance.com:9443/stream"

    def __init__(
        self,
        symbols: List[str],
        on_price_callback: Callable[[str, float], Awaitable[None]],
        on_kline_callback: Optional[Callable[[str, float, float, bool], Awaitable[None]]] = None,
        on_disconnect_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        on_reconnect_callback: Optional[Callable[[int], Awaitable[None]]] = None,
        reconnect_delay: float = 5.0,
        ping_interval: float = 30.0,
        pong_timeout: float = 10.0,
        message_timeout: float = 120.0,
        max_reconnect_attempts: int = None,
    ):
        """
        Initialize WebSocket client

        Args:
            symbols: List of trading symbols (e.g., ["BTCUSDT", "ETHUSDT"])
            on_price_callback: Async callback function called on each price update
            on_kline_callback: Optional async callback for kline updates (symbol, price, volume, is_closed)
            on_disconnect_callback: Optional async callback when connection is lost (reason)
            on_reconnect_callback: Optional async callback when reconnection successful (attempt_count)
            reconnect_delay: Delay between reconnection attempts (seconds)
            ping_interval: Interval for sending ping frames (seconds)
            pong_timeout: Timeout waiting for ping response (seconds)
            message_timeout: Max allowed seconds without any market message before reconnect
            max_reconnect_attempts: Maximum reconnection attempts (None = infinite)
        """
        if not symbols:
            raise ValueError("At least one symbol is required for BinanceWebSocketClient")

        self.symbols = symbols
        self.on_price_callback = on_price_callback
        self.on_kline_callback = on_kline_callback
        self.on_disconnect_callback = on_disconnect_callback
        self.on_reconnect_callback = on_reconnect_callback
        self.reconnect_delay = reconnect_delay
        self.ping_interval = ping_interval
        self.pong_timeout = pong_timeout
        self.message_timeout = message_timeout
        self.max_reconnect_attempts = max_reconnect_attempts

        # Connection state
        self.state = ConnectionState.DISCONNECTED
        self.websocket: Optional[websockets.client.WebSocketClientProtocol] = None
        self.reconnect_count = 0
        self._stop_event = asyncio.Event()
        self._message_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

        # Alert tracking (prevent duplicate disconnect alerts)
        self._disconnect_alert_sent = False
        self._disconnect_alert_lock = asyncio.Lock()

        # Statistics
        self.messages_received = 0
        self.last_message_time: Optional[datetime] = None
        self.connection_time: Optional[datetime] = None

        logger.info(f"BinanceWebSocketClient initialized for {len(symbols)} symbols")

    def _build_stream_url(self) -> str:
        """Build WebSocket URL with subscribed streams (ticker + kline)"""
        streams = []
        for symbol in self.symbols:
            symbol_lower = symbol.lower()
            streams.append(f"{symbol_lower}@ticker")      # 价格更新
            if self.on_kline_callback:
                streams.append(f"{symbol_lower}@kline_1m")  # 1分钟K线(含成交量)

        if not streams:
            raise ValueError("No streams configured for WebSocket connection")

        return f"{self.BASE_COMBINED_WS_URL}?streams={'/'.join(streams)}"

    def _parse_ticker_message(self, data: dict) -> Tuple[str, float]:
        """
        Parse Binance ticker message

        Format:
        {
            "e": "24hrTicker",
            "s": "BTCUSDT",
            "c": "98000.50000000",  # Current price
            ...
        }
        """
        try:
            # Combined stream format
            if "stream" in data and "data" in data:
                symbol = data["data"]["s"]
                price = float(data["data"]["c"])
            # Single stream format
            else:
                symbol = data["s"]
                price = float(data["c"])

            return symbol, price
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse ticker message: {e}, data: {data}")
            raise

    def _parse_kline_message(self, data: dict) -> Optional[Tuple[str, float, float, bool]]:
        """
        Parse Binance kline message

        Returns:
            Tuple of (symbol, price, volume, is_closed) or None
            - symbol: Trading pair (e.g., "ETHUSDT")
            - price: Kline close price
            - volume: Kline volume
            - is_closed: Whether the kline is closed (complete)

        Format:
        {
            "e": "kline",
            "s": "ETHUSDT",
            "k": {
                "t": 1739980800000,    # Kline start time
                "T": 1739980859999,    # Kline end time
                "s": "ETHUSDT",        # Symbol
                "i": "1m",             # Interval
                "o": "2700.00",        # Open price
                "c": "2700.50",        # Close price
                "h": "2705.00",        # High price
                "l": "2698.00",        # Low price
                "v": "1234.56",        # Volume
                "n": 100,              # Number of trades
                "x": true,             # Is kline closed
                "q": "3333333",        # Quote volume
                ...
            }
        }
        """
        try:
            # Combined stream format
            if "stream" in data and "data" in data:
                inner_data = data["data"]
                if inner_data.get("e") == "kline":
                    kline = inner_data.get("k", {})
                    return (
                        kline.get("s"),           # Symbol
                        float(kline.get("c")),    # Close price
                        float(kline.get("v")),    # Volume
                        kline.get("x", False)     # Is closed
                    )
            # Single stream format
            elif data.get("e") == "kline":
                kline = data.get("k", {})
                return (
                    kline.get("s"),
                    float(kline.get("c")),
                    float(kline.get("v")),
                    kline.get("x", False)
                )

            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse kline message: {e}, data: {data}")
            return None

    async def _message_handler(self):
        """Handle incoming WebSocket messages"""
        logger.info("Message handler started")

        try:
            async for message in self.websocket:
                if self._stop_event.is_set():
                    break

                try:
                    data = json.loads(message)

                    # Handle different message types
                    if isinstance(data, dict):
                        # Ticker update
                        if data.get("e") == "24hrTicker" or ("stream" in data and "data" in data and data["data"].get("e") == "24hrTicker"):
                            symbol, price = self._parse_ticker_message(data)

                            # Update statistics
                            self.messages_received += 1
                            self.last_message_time = datetime.now(UTC8)

                            # Call user callback
                            try:
                                await self.on_price_callback(symbol, price)
                            except BrokenPipeError:
                                # Ignore broken pipe errors when running in background
                                pass
                            except Exception as e:
                                logger.exception("Error in price callback")

                        # Kline update
                        elif data.get("e") == "kline" or ("stream" in data and "data" in data and data["data"].get("e") == "kline"):
                            if self.on_kline_callback:
                                kline_data = self._parse_kline_message(data)
                                if kline_data:
                                    symbol, price, volume, is_closed = kline_data

                                    # Only process when kline is closed
                                    if is_closed:
                                        # Update statistics
                                        self.messages_received += 1
                                        self.last_message_time = datetime.now(UTC8)

                                        # Call user callback
                                        try:
                                            await self.on_kline_callback(symbol, price, volume, is_closed)
                                        except BrokenPipeError:
                                            pass
                                        except Exception as e:
                                            logger.exception("Error in kline callback")

                        # Subscription confirmation
                        elif "result" in data:
                            logger.info(f"Subscription confirmed: {data}")

                        # Error message
                        elif "code" in data:
                            logger.error(f"Binance error: {data}")

                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON received: {e}")

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}")
            if not self._stop_event.is_set():
                self.state = ConnectionState.RECONNECTING
                await self._trigger_disconnect_alert(f"Connection closed: {e}")
        except Exception as e:
            logger.error(f"Error in message handler: {e}")
            if not self._stop_event.is_set():
                self.state = ConnectionState.RECONNECTING
                await self._trigger_disconnect_alert(f"Error: {e}")

    async def _trigger_disconnect_alert(self, reason: str):
        """Trigger disconnect alert with deduplication"""
        if not self.on_disconnect_callback:
            return

        async with self._disconnect_alert_lock:
            if self._disconnect_alert_sent:
                return
            self._disconnect_alert_sent = True

        try:
            await self.on_disconnect_callback(reason)
        except Exception as cb_err:
            logger.error(f"Error in disconnect callback: {cb_err}")

    async def _reset_disconnect_alert_flag(self):
        """Reset disconnect alert flag after successful reconnection"""
        async with self._disconnect_alert_lock:
            self._disconnect_alert_sent = False

    async def _ping_handler(self):
        """Send periodic ping frames to keep connection alive"""
        logger.info(
            f"Ping handler started (interval: {self.ping_interval}s, pong timeout: {self.pong_timeout}s)"
        )

        while not self._stop_event.is_set() and self.state == ConnectionState.CONNECTED:
            try:
                await asyncio.sleep(self.ping_interval)

                if self.websocket and not self.websocket.closed:
                    # Send ping and require timely pong, otherwise force reconnect.
                    pong_waiter = await self.websocket.ping()
                    await asyncio.wait_for(pong_waiter, timeout=self.pong_timeout)
                    logger.debug("Ping sent")
                else:
                    break

            except asyncio.TimeoutError:
                reason = f"Ping timed out (>{self.pong_timeout}s without pong)"
                logger.error(reason)
                await self._trigger_disconnect_alert(reason)
                if self.websocket and not self.websocket.closed:
                    await self.websocket.close()
                self.state = ConnectionState.RECONNECTING
                break
            except Exception as e:
                logger.error(f"Error sending ping: {e}")
                await self._trigger_disconnect_alert(f"Ping failed: {e}")
                if self.websocket and not self.websocket.closed:
                    await self.websocket.close()
                self.state = ConnectionState.RECONNECTING
                break

    async def _connection_watchdog(self):
        """Reconnect when the socket is connected but no market data arrives for too long."""
        check_interval = max(2.0, min(self.ping_interval, 10.0))
        logger.info(
            f"Connection watchdog started (message timeout: {self.message_timeout}s, check every: {check_interval}s)"
        )

        while not self._stop_event.is_set() and self.state == ConnectionState.CONNECTED:
            await asyncio.sleep(check_interval)

            if self.state != ConnectionState.CONNECTED:
                break

            if not self.last_message_time:
                continue

            silence_seconds = (datetime.now(UTC8) - self.last_message_time).total_seconds()
            if silence_seconds <= self.message_timeout:
                continue

            reason = f"No market messages for {int(silence_seconds)}s (timeout={self.message_timeout}s)"
            logger.error(reason)
            await self._trigger_disconnect_alert(reason)
            self.state = ConnectionState.RECONNECTING
            if self.websocket and not self.websocket.closed:
                await self.websocket.close()
            break

    async def _connect(self) -> bool:
        """Establish WebSocket connection"""
        self.state = ConnectionState.CONNECTING
        url = self._build_stream_url()

        try:
            logger.info(f"Connecting to Binance WebSocket: {url}")

            # Set connection timeout
            self.websocket = await asyncio.wait_for(
                websockets.connect(
                    url,
                    ping_interval=None,  # We handle ping manually
                    close_timeout=10,
                ),
                timeout=10.0
            )

            self.state = ConnectionState.CONNECTED
            self.connection_time = datetime.now(UTC8)
            self.reconnect_count = 0
            self.last_message_time = datetime.now(UTC8)

            logger.info("✅ WebSocket connected successfully")

            # Start message handler
            self._message_task = asyncio.create_task(self._message_handler())

            # Start ping handler
            self._ping_task = asyncio.create_task(self._ping_handler())
            # Start message freshness watchdog
            self._watchdog_task = asyncio.create_task(self._connection_watchdog())

            return True

        except asyncio.TimeoutError:
            logger.error("Connection timeout")
            return False
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def _reconnect_loop(self):
        """Handle reconnection logic"""
        while not self._stop_event.is_set():
            # Check max reconnection attempts
            if (
                self.max_reconnect_attempts is not None
                and self.reconnect_count >= self.max_reconnect_attempts
            ):
                logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached")
                self.state = ConnectionState.DISCONNECTED
                break

            # Wait before reconnecting
            attempt_no = self.reconnect_count + 1
            logger.info(f"Reconnecting in {self.reconnect_delay}s... (attempt {attempt_no})")
            await asyncio.sleep(self.reconnect_delay)

            # Attempt reconnection
            if self._stop_event.is_set():
                break

            success = await self._connect()
            if success:
                # Connection successful, reset alert flag and call callback
                await self._reset_disconnect_alert_flag()
                if self.on_reconnect_callback:
                    try:
                        await self.on_reconnect_callback(attempt_no)
                    except Exception as cb_err:
                        logger.error(f"Error in reconnect callback: {cb_err}")
                return

            self.reconnect_count = attempt_no

    async def start(self):
        """
        Start WebSocket connection with auto-reconnect

        This method runs until stop() is called or max reconnection attempts is reached.
        """
        logger.info("Starting Binance WebSocket client...")

        while not self._stop_event.is_set():
            # Initial connection
            success = await self._connect()

            if not success:
                logger.error("Failed to establish initial connection")
                self.state = ConnectionState.RECONNECTING
                await self._reconnect_loop()
                continue

            # Connection established, wait for disconnection
            while self.state == ConnectionState.CONNECTED and not self._stop_event.is_set():
                await asyncio.sleep(0.5)

            # If we're here, connection was lost
            if not self._stop_event.is_set():
                logger.warning("Connection lost, attempting to reconnect...")
                self.state = ConnectionState.RECONNECTING

                # Cancel old tasks
                if self._message_task:
                    self._message_task.cancel()
                    try:
                        await self._message_task
                    except asyncio.CancelledError:
                        pass

                if self._ping_task:
                    self._ping_task.cancel()
                    try:
                        await self._ping_task
                    except asyncio.CancelledError:
                        pass

                if self._watchdog_task:
                    self._watchdog_task.cancel()
                    try:
                        await self._watchdog_task
                    except asyncio.CancelledError:
                        pass

                # Enter reconnection loop
                await self._reconnect_loop()

        # Cleanup
        await self._cleanup()

    async def stop(self):
        """Stop WebSocket connection gracefully"""
        logger.info("Stopping WebSocket client...")
        self._stop_event.set()
        self.state = ConnectionState.STOPPED

        await self._cleanup()

    async def _cleanup(self):
        """Cleanup resources"""
        # Cancel tasks
        if self._message_task:
            self._message_task.cancel()
        if self._ping_task:
            self._ping_task.cancel()
        if self._watchdog_task:
            self._watchdog_task.cancel()

        # Close WebSocket
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")

        logger.info("WebSocket client stopped")

    def get_statistics(self) -> dict:
        """Get connection statistics"""
        return {
            "state": self.state.value,
            "messages_received": self.messages_received,
            "reconnect_count": self.reconnect_count,
            "connection_time": self.connection_time.isoformat() if self.connection_time else None,
            "last_message_time": self.last_message_time.isoformat() if self.last_message_time else None,
            "uptime_seconds": (
                (datetime.now(UTC8) - self.connection_time).total_seconds()
                if self.connection_time
                else 0
            ),
        }

    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return (
            self.state == ConnectionState.CONNECTED
            and self.websocket is not None
            and not self.websocket.closed
        )

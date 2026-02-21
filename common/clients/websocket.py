"""
WebSocket client for Binance real-time price streams
"""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, List, Callable, Awaitable, Tuple
from urllib.parse import quote

import websockets

from ..logging import logger
from ..utils import UTC8


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
    """

    # Binance WebSocket endpoints
    BASE_WS_URL = "wss://stream.binance.com:9443/ws"
    BASE_COMBINED_WS_URL = "wss://stream.binance.com:9443/stream"

    VALID_SYMBOL_PATTERN = __import__('re').compile(r'^[A-Z0-9]+$')

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
            # Validate symbol format
            if not symbol or not self.VALID_SYMBOL_PATTERN.match(symbol):
                logger.warning(f"Invalid symbol format: {symbol}")
                continue

            symbol_lower = symbol.lower()
            safe_symbol = quote(symbol_lower, safe='')
            streams.append(f"{safe_symbol}@ticker")
            if self.on_kline_callback:
                streams.append(f"{safe_symbol}@kline_1m")

        if not streams:
            raise ValueError("No valid streams configured for WebSocket connection")

        return f"{self.BASE_COMBINED_WS_URL}?streams={'/'.join(streams)}"

    def _parse_ticker_message(self, data: dict) -> Tuple[str, float]:
        """Parse Binance ticker message"""
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
            logger.error(f"Failed to parse ticker message: {e}")
            raise

    def _parse_kline_message(self, data: dict) -> Optional[Tuple[str, float, float, bool]]:
        """Parse Binance kline message"""
        try:
            # Combined stream format
            if "stream" in data and "data" in data:
                inner_data = data["data"]
                if inner_data.get("e") == "kline":
                    kline = inner_data.get("k", {})
                    return (
                        kline.get("s"),
                        float(kline.get("c", 0)),
                        float(kline.get("v", 0)),
                        kline.get("x", False)
                    )
            # Single stream format
            elif data.get("e") == "kline":
                kline = data.get("k", {})
                return (
                    kline.get("s"),
                    float(kline.get("c", 0)),
                    float(kline.get("v", 0)),
                    kline.get("x", False)
                )

            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse kline message: {e}")
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
                                pass
                            except Exception:
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
                                        except Exception:
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
        except asyncio.CancelledError:
            raise  # Re-raise to allow proper cancellation
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
                    # Send ping and require timely pong
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

            logger.info("WebSocket connected successfully")

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
        """Start WebSocket connection with auto-reconnect"""
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
        """Cleanup resources with proper task cancellation"""
        # Cancel tasks and wait for them to complete
        tasks = [t for t in (self._message_task, self._ping_task, self._watchdog_task) if t]
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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

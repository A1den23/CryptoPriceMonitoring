"""
WebSocket client for Binance real-time price streams
"""

import asyncio
import json
import re
from datetime import datetime
from enum import Enum, auto
from typing import Awaitable, Callable

import websockets

from ..logging import logger
from ..utils import get_configured_timezone
from .websocket_parser import parse_kline_message, parse_ticker_message


class ConnectionState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    STOPPED = auto()


class BinanceWebSocketClient:
    """
    Real-time Binance WebSocket client with auto-reconnect and heartbeat
    """

    # Binance WebSocket endpoints
    BASE_WS_URL = "wss://stream.binance.com:9443/ws"
    BASE_COMBINED_WS_URL = "wss://stream.binance.com:9443/stream"

    _VALID_SYMBOL_PATTERN = re.compile(r'^[A-Z0-9]+$')

    def __init__(
        self,
        symbols: list[str],
        on_price_callback: Callable[[str, float], Awaitable[None]],
        on_kline_callback: Callable[[str, float, float, bool], Awaitable[None]] | None = None,
        on_disconnect_callback: Callable[[str], Awaitable[None]] | None = None,
        on_reconnect_callback: Callable[[int], Awaitable[None]] | None = None,
        reconnect_delay: float = 5.0,
        ping_interval: float = 30.0,
        pong_timeout: float = 10.0,
        message_timeout: float = 120.0,
        max_reconnect_attempts: int | None = None,
    ) -> None:
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
        self.websocket: websockets.client.WebSocketClientProtocol | None = None
        self.reconnect_count = 0
        self._stop_event = asyncio.Event()
        self._message_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None

        # Alert tracking (prevent duplicate disconnect alerts)
        self._disconnect_alert_sent = False
        self._disconnect_alert_lock = asyncio.Lock()

        # Statistics
        self.messages_received = 0
        self.last_message_time: datetime | None = None
        self.connection_time: datetime | None = None

        logger.info(f"BinanceWebSocketClient initialized for {len(symbols)} symbols")

    def _build_stream_url(self) -> str:
        """Build WebSocket URL with subscribed streams (ticker + kline)."""
        streams: list[str] = []
        for symbol in self.symbols:
            # Validate symbol format
            if not symbol or not self._VALID_SYMBOL_PATTERN.match(symbol):
                logger.warning(f"Invalid symbol format: {symbol}")
                continue

            symbol_lower = symbol.lower()
            streams.append(f"{symbol_lower}@ticker")
            if self.on_kline_callback:
                streams.append(f"{symbol_lower}@kline_1m")

        if not streams:
            raise ValueError("No valid streams configured for WebSocket connection")

        return f"{self.BASE_COMBINED_WS_URL}?streams={'/'.join(streams)}"

    @staticmethod
    def _now() -> datetime:
        """Get current time in configured timezone."""
        return datetime.now(get_configured_timezone())

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
                    if not isinstance(data, dict):
                        continue

                    event_type = data.get("e")
                    inner_event = data.get("data", {}).get("e") if "stream" in data and "data" in data else None

                    # Subscription confirmation
                    if "result" in data:
                        logger.info(f"Subscription confirmed: {data}")

                    # Error message
                    elif "code" in data:
                        logger.error(f"Binance error: {data}")

                    # Ticker update
                    elif event_type == "24hrTicker" or inner_event == "24hrTicker":
                        try:
                            symbol, price = parse_ticker_message(data)
                        except (KeyError, ValueError, TypeError) as e:
                            logger.error(f"Failed to parse ticker message: {e}")
                            continue

                        # Update statistics
                        self.messages_received += 1
                        self.last_message_time = self._now()

                        # Call user callback
                        try:
                            await self.on_price_callback(symbol, price)
                        except BrokenPipeError:
                            pass
                        except Exception:
                            logger.exception("Error in price callback")

                    # Kline update
                    elif event_type == "kline" or inner_event == "kline":
                        if self.on_kline_callback:
                            try:
                                kline_data = parse_kline_message(data)
                            except (KeyError, ValueError, TypeError) as e:
                                logger.error(f"Failed to parse kline message: {e}")
                                continue
                            if kline_data and kline_data[3]:  # is_closed
                                symbol, price, volume, _ = kline_data

                                # Update statistics
                                self.messages_received += 1
                                self.last_message_time = self._now()

                                # Call user callback
                                try:
                                    await self.on_kline_callback(symbol, price, volume, True)
                                except BrokenPipeError:
                                    pass
                                except Exception:
                                    logger.exception("Error in kline callback")

                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON received: {e}")

            if not self._stop_event.is_set() and self.state == ConnectionState.CONNECTED:
                logger.warning("WebSocket message stream ended cleanly")
                self.state = ConnectionState.RECONNECTING
                await self._trigger_disconnect_alert("Connection closed cleanly")
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

            silence_seconds = (self._now() - self.last_message_time).total_seconds()
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
            self.connection_time = self._now()
            self.last_message_time = self._now()

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

    async def _reconnect_loop(self) -> bool:
        """Handle reconnection logic.

        Returns:
            True: reconnected successfully
            False: stopped or exhausted retries
        """
        failed_attempts = 0
        while not self._stop_event.is_set():
            # Check max reconnection attempts
            if (
                self.max_reconnect_attempts is not None
                and failed_attempts >= self.max_reconnect_attempts
            ):
                logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached")
                self.state = ConnectionState.DISCONNECTED
                return False

            # Wait before reconnecting
            attempt_no = failed_attempts + 1
            logger.info(f"Reconnecting in {self.reconnect_delay}s... (attempt {attempt_no})")
            await asyncio.sleep(self.reconnect_delay)

            # Attempt reconnection
            if self._stop_event.is_set():
                break

            success = await self._connect()
            if success:
                # Connection successful, reset alert flag and call callback
                await self._reset_disconnect_alert_flag()
                self.reconnect_count += 1
                if self.on_reconnect_callback:
                    try:
                        await self.on_reconnect_callback(self.reconnect_count)
                    except Exception as cb_err:
                        logger.error(f"Error in reconnect callback: {cb_err}")
                return True

            failed_attempts = attempt_no

        return False

    async def _cancel_runtime_tasks(self) -> None:
        """Cancel runtime tasks created for a live connection."""
        tasks = [t for t in (self._message_task, self._ping_task, self._watchdog_task) if t]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._message_task = None
        self._ping_task = None
        self._watchdog_task = None

    async def start(self):
        """Start WebSocket connection with auto-reconnect"""
        logger.info("Starting Binance WebSocket client...")

        while not self._stop_event.is_set():
            # Connect only when not already connected.
            # This avoids creating duplicate sockets after reconnect succeeds.
            if self.state != ConnectionState.CONNECTED:
                success = await self._connect()
                if not success:
                    logger.error("Failed to establish connection")
                    self.state = ConnectionState.RECONNECTING
                    reconnect_success = await self._reconnect_loop()
                    if not reconnect_success:
                        if self.max_reconnect_attempts is not None:
                            break
                        continue

            # Connection established, wait for disconnection
            while self.state == ConnectionState.CONNECTED and not self._stop_event.is_set():
                await asyncio.sleep(0.5)

            # If we're here, connection was lost
            if not self._stop_event.is_set():
                logger.warning("Connection lost, attempting to reconnect...")
                self.state = ConnectionState.RECONNECTING

                # Cancel old tasks
                await self._cancel_runtime_tasks()

                # Enter reconnection loop
                reconnect_success = await self._reconnect_loop()
                if not reconnect_success and self.max_reconnect_attempts is not None:
                    break

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
        await self._cancel_runtime_tasks()

        # Close WebSocket
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
            finally:
                self.websocket = None

        logger.info("WebSocket client stopped")

    def get_statistics(self) -> dict:
        """Get connection statistics."""
        return {
            "state": self.state.name.lower(),
            "messages_received": self.messages_received,
            "reconnect_count": self.reconnect_count,
            "connection_time": self.connection_time.isoformat() if self.connection_time else None,
            "last_message_time": self.last_message_time.isoformat() if self.last_message_time else None,
            "uptime_seconds": (
                (self._now() - self.connection_time).total_seconds()
                if self.connection_time
                else 0
            ),
        }

    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return (
            self.state == ConnectionState.CONNECTED
            and self.websocket is not None
            and not self.websocket.closed
        )

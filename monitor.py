#!/usr/bin/env python3
"""
Crypto Price Monitoring Bot with WebSocket Support
Monitors multiple cryptocurrency prices in real-time via WebSocket and sends alerts via Telegram

Usage:
    python monitor.py              # Run monitoring with WebSocket (default)
    python monitor.py --test       # Test volatility alerts
    python monitor.py --status     # Show current status
"""

import sys
import asyncio
import signal
from datetime import datetime, timedelta
from collections import deque
from typing import Optional, List, Dict

from common import (
    setup_logging,
    ConfigManager,
    CoinConfig,
    BinancePriceFetcher,
    BinanceWebSocketClient,
    TelegramNotifier,
    format_price,
    get_coin_display_name,
    get_coin_emoji,
    UTC8,
    logger
)


class PriceData:
    """Store price data with timestamp"""
    def __init__(self, price: float, timestamp: datetime):
        self.price = price
        self.timestamp = timestamp


class PriceMonitor:
    """Monitor price changes for a single coin"""
    def __init__(self, config: CoinConfig, notifier: TelegramNotifier):
        self.config = config
        self.notifier = notifier

        # State tracking
        self.price_history: deque[PriceData] = deque()
        self.last_price = None
        self.last_processed_price = None  # Last price that triggered alerts

        # Milestone notification cooldown tracking (global cooldown for any milestone crossing)
        self.last_milestone_notification_time: Optional[datetime] = None
        self.milestone_cooldown_seconds = 600  # 10 minutes global cooldown

    def _calculate_milestone(self, price: float, threshold: float) -> float:
        """Calculate the milestone for a given price and threshold"""
        if threshold >= 1:
            # For larger thresholds (>= 1), use integer-based checking (BTC, ETH, SOL)
            price_int = int(price)
            return int(price_int / threshold) * threshold
        else:
            # For small thresholds (< 1), use precise checking for stablecoins (USD1)
            offset = price - 1.0
            return 1.0 + round(offset / threshold) * threshold

    def _check_milestone_cooldown(self, coin: str) -> bool:
        """Check if milestone notification is in cooldown period.
        Returns True if in cooldown (should skip), False if not in cooldown.
        """
        if self.last_milestone_notification_time:
            now = datetime.now(UTC8)
            time_since_last = (now - self.last_milestone_notification_time).total_seconds()
            if time_since_last < self.milestone_cooldown_seconds:
                logger.debug(f"[{coin}] Global cooldown active ({time_since_last:.0f}s ago)")
                return True
        return False

    def _send_milestone_notification(self, current_price: float, current_milestone: float):
        """Send milestone notification and update tracking"""
        coin = get_coin_display_name(self.config.symbol)
        is_up = current_price > self.last_price
        direction = "📈" if is_up else "📉"

        now = datetime.now(UTC8)
        self.last_price = current_price
        self.last_milestone_notification_time = now

        direction_text = "向上 ↑" if is_up else "向下 ↓"

        message = (
            f"🎉🎉【价格里程碑】🎉🎉\n"
            f"🪙 {self.config.symbol}\n"
            f"💰 价格: {format_price(current_price)}\n"
            f"{direction} 突破方向: {direction_text}\n"
            f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.notifier.send_message(message)

        # Format milestone for logging
        if self.config.integer_threshold >= 1:
            milestone_str = f"${current_milestone:,}"
        else:
            milestone_str = format_price(current_milestone)
        logger.info(f"[{coin}] Crossed milestone: {milestone_str}")

    def check_integer_milestone(self, current_price: float) -> bool:
        """Check if price reached an integer milestone using crossing detection"""
        threshold = self.config.integer_threshold
        coin = get_coin_display_name(self.config.symbol)

        # Initialize last_price on first run
        if self.last_price is None:
            self.last_price = current_price
            return False

        # Calculate current and last milestones
        current_milestone = self._calculate_milestone(current_price, threshold)
        last_milestone = self._calculate_milestone(self.last_price, threshold)

        # Check if milestone was crossed
        if last_milestone != current_milestone:
            # Check cooldown - skip if in cooldown period
            if self._check_milestone_cooldown(coin):
                self.last_price = current_price
                return False

            # Send notification
            self._send_milestone_notification(current_price, current_milestone)
            return True

        # Update last price for next iteration
        self.last_price = current_price
        return False

    def check_volatility(self, current_price: float) -> Optional[str]:
        """
        Enhanced volatility check using multiple metrics:
        1. Standard deviation (measures price dispersion)
        2. Cumulative volatility (sum of all price movements)
        3. Min/max range (original method)
        4. Volatility acceleration (rate of change)
        """
        current_time = datetime.now(UTC8)

        # Add current price to history
        self.price_history.append(PriceData(current_price, current_time))

        # Remove old data outside the time window (sliding window, no clearing)
        cutoff_time = current_time - timedelta(seconds=self.config.volatility_window)
        while self.price_history and self.price_history[0].timestamp < cutoff_time:
            self.price_history.popleft()

        # Need at least 3 data points for meaningful statistics
        if len(self.price_history) < 3:
            return None

        prices = [p.price for p in self.price_history]

        # Metric 1: Standard deviation (relative to mean price)
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        std_dev = variance ** 0.5
        std_dev_pct = (std_dev / mean_price) * 100 if mean_price > 0 else 0

        # Metric 2: Cumulative volatility (sum of absolute price changes)
        cumulative_change = sum(abs(prices[i] - prices[i-1]) for i in range(1, len(prices)))
        cumulative_volatility_pct = (cumulative_change / prices[0]) * 100 if prices[0] > 0 else 0

        # Metric 3: Min/max range (original method)
        min_price = min(prices)
        max_price = max(prices)
        range_volatility_pct = ((max_price - min_price) / min_price) * 100 if min_price > 0 else 0

        # Metric 4: Volatility acceleration (rate of change in recent movements)
        if len(prices) >= 4:
            recent_changes = [abs(prices[i] - prices[i-1]) for i in range(-4, 0)]
            acceleration = max(recent_changes) / (sum(recent_changes) / len(recent_changes)) if sum(recent_changes) > 0 else 1
        else:
            acceleration = 1

        # Determine if volatility is high (any metric exceeds threshold)
        # Use lower threshold for std_dev (more sensitive) and cumulative (catches rapid movements)
        threshold = self.config.volatility_percent

        is_volatile = (
            std_dev_pct >= threshold * 0.7 or  # 70% of threshold for std dev (balanced sensitivity)
            cumulative_volatility_pct >= threshold * 1.5 or  # 150% for cumulative
            range_volatility_pct >= threshold or  # 100% for range (original)
            (acceleration >= 2.0 and std_dev_pct >= threshold * 0.3)  # High acceleration with some volatility
        )

        # Create detailed volatility info for display
        volatility_info = f"σ:{std_dev_pct:.2f}% Σ:{cumulative_volatility_pct:.2f}% R:{range_volatility_pct:.2f}%"

        # Cooldown tracking: only alert if enough time passed since last alert
        if self.last_milestone_notification_time:
            time_since_last = (current_time - self.last_milestone_notification_time).total_seconds()
            # Use balanced cooldown for volatility (30 seconds to prevent alert fatigue)
            volatility_cooldown = min(self.milestone_cooldown_seconds, 30)
            if time_since_last < volatility_cooldown:
                return volatility_info

        if is_volatile:
            change = current_price - self.price_history[0].price
            change_percent = (change / self.price_history[0].price) * 100
            direction = "📈" if change > 0 else "📉"
            coin = get_coin_display_name(self.config.symbol)

            # Determine primary reason for alert
            reasons = []
            if std_dev_pct >= threshold * 0.7:
                reasons.append(f"Std Dev: {std_dev_pct:.2f}%")
            if cumulative_volatility_pct >= threshold * 1.2:
                reasons.append(f"Cumulative: {cumulative_volatility_pct:.2f}%")
            if range_volatility_pct >= threshold:
                reasons.append(f"Range: {range_volatility_pct:.2f}%")
            if acceleration >= 2.0 and std_dev_pct >= threshold * 0.3:
                reasons.append(f"Acceleration: {acceleration:.1f}x")

            message = (
                f"⚠️⚠️【波动警报】⚠️⚠️\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"🪙 {self.config.symbol}\n"
                f"💰 当前: {format_price(current_price)}\n"
                f"📊 时间窗口: {self.config.volatility_window}s ({len(self.price_history)} pts)\n"
                f"⚡️ 触发指标: {', '.join(reasons)}\n"
                f"{direction} 净变化: {change_percent:+.2f}%\n"
                f"⏱️ {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━"
            )
            self.notifier.send_message(message)
            logger.info(f"[{coin}] High volatility - {', '.join(reasons)}")

            # Update last notification time (but don't clear history - use sliding window)
            self.last_milestone_notification_time = current_time
            return volatility_info

        return volatility_info

    def check(self, current_price: float) -> Optional[str]:
        """
        Check price and return formatted output

        Only processes if price has changed significantly to avoid duplicate alerts.
        Returns None if price hasn't changed, otherwise returns formatted output.
        """
        # Skip if price hasn't changed (avoid duplicate processing from WebSocket high-frequency updates)
        if self.last_processed_price is not None:
            price_diff = abs(current_price - self.last_processed_price)
            # For prices >= $1, minimum change is $0.001
            # For prices < $1, minimum change is $0.0001
            min_change = 0.001 if current_price >= 1 else 0.0001

            if price_diff < min_change:
                # Price hasn't changed enough to matter
                return None

        # Update last processed price
        self.last_processed_price = current_price

        coin = get_coin_display_name(self.config.symbol)

        # Check for integer milestone
        milestone_alert = self.check_integer_milestone(current_price)

        # Check for volatility
        volatility_info = self.check_volatility(current_price)

        # Format output
        emoji = get_coin_emoji(coin)
        output = f"{emoji} [{coin}] {format_price(current_price)}"
        if milestone_alert:
            output += " 🎯"
        if volatility_info:
            output += f" 📊{volatility_info}"

        return output


class WebSocketMultiCoinMonitor:
    """
    Real-time multi-cryptocurrency monitor using WebSocket

    This class provides real-time price monitoring using Binance WebSocket streams,
    with automatic reconnection and connection health monitoring.
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self.notifier = TelegramNotifier()

        # Load configurations for all enabled coins
        self.monitors: Dict[str, PriceMonitor] = {}
        self._load_monitors()

        # WebSocket client
        self.ws_client: Optional[BinanceWebSocketClient] = None

        # Display statistics
        self.last_print_time = None
        self.print_interval = 5  # Print status every 5 seconds
        self._pending_updates: List[str] = []

        # Shutdown handling
        self._shutdown_event = asyncio.Event()
        self._setup_signal_handlers()

    def _load_monitors(self):
        """Load monitors from configuration"""
        enabled_coins = self.config.get_enabled_coins()
        for coin_config in enabled_coins:
            monitor = PriceMonitor(coin_config, self.notifier)
            self.monitors[coin_config.symbol] = monitor
            logger.info(f"✓ Loaded {coin_config}")

        if not self.monitors:
            logger.warning("No coins enabled in configuration!")

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        # Store original signal handlers
        self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)

        logger.debug("Signal handlers registered for SIGINT and SIGTERM")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals (SIGINT, SIGTERM)"""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received signal {sig_name} ({signum}), initiating graceful shutdown...")

        # Set the shutdown event to stop the WebSocket
        self._shutdown_event.set()

        # Restore original signal handler to allow immediate force-quit if needed
        signal.signal(signum, self._original_sigint if signum == signal.SIGINT else self._original_sigterm)

    async def _send_shutdown_notification(self):
        """Send shutdown notification via Telegram"""
        try:
            now = datetime.now(UTC8)
            uptime = "Unknown"

            if self.ws_client:
                stats = self.ws_client.get_statistics()
                uptime_seconds = stats.get('uptime_seconds', 0)
                hours = int(uptime_seconds // 3600)
                minutes = int((uptime_seconds % 3600) // 60)
                uptime = f"{hours}h {minutes}m"

            message = (
                f"👋 <b>Crypto Price Monitoring Bot Stopped</b>\n"
                f"⏱️ {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"⌛ Uptime: {uptime}\n"
                f"🪙 Monitored: {len(self.monitors)} coin(s)\n"
                f"📊 Status: Graceful shutdown"
            )
            self.notifier.send_message(message)
        except Exception as e:
            logger.error(f"Failed to send shutdown notification: {e}")

    async def _on_price_update(self, symbol: str, price: float):
        """Callback function for WebSocket price updates"""
        monitor = self.monitors.get(symbol)
        if not monitor:
            return

        # Check price for alerts (returns None if price hasn't changed)
        output = monitor.check(price)

        # Only add to pending updates if price has changed
        if output is not None:
            self._pending_updates.append(output)

        # Print updates periodically
        current_time = datetime.now(UTC8)
        if (
            self.last_print_time is None
            or (current_time - self.last_print_time).total_seconds() >= self.print_interval
        ):
            self._print_updates()
            self.last_print_time = current_time

    def _print_updates(self):
        """Print accumulated price updates"""
        if not self._pending_updates:
            return

        timestamp = datetime.now(UTC8).strftime('%H:%M:%S')
        logger.info(f"Real-time price updates [{timestamp}]:")
        for update in self._pending_updates:
            logger.info(f"  {update}")

        try:
            print(f"[{timestamp}] Real-time updates:")
            for update in self._pending_updates:
                print(f"  {update}")
            print()
        except (OSError, IOError):
            # Handle broken pipe when running in background
            pass

        self._pending_updates.clear()

    async def run(self):
        """Start WebSocket monitoring"""
        print(f"\n{'='*60}")
        print(f"Starting Multi-Coin Price Monitor (WebSocket Mode)")
        print(f"{'='*60}")
        print(f"Monitored coins: {len(self.monitors)}")
        print(f"Connection: Real-time WebSocket (10-50ms latency)")
        print(f"{'='*60}\n")

        # Test Telegram connection
        if not self.notifier.test_connection():
            logger.warning("Failed to send test message. Check your Telegram configuration.")

        # Get list of symbols to monitor
        symbols = list(self.monitors.keys())

        # Create WebSocket client
        self.ws_client = BinanceWebSocketClient(
            symbols=symbols,
            on_price_callback=self._on_price_update,
            reconnect_delay=5.0,
            ping_interval=30.0,
            max_reconnect_attempts=None,  # Infinite reconnect
        )

        try:
            # Start WebSocket and wait for shutdown signal
            ws_task = asyncio.create_task(self.ws_client.start())
            shutdown_task = asyncio.create_task(self._shutdown_event.wait())

            # Wait for either WebSocket to complete or shutdown signal
            done, pending = await asyncio.wait(
                [ws_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # If shutdown was triggered, handle graceful shutdown
            if self._shutdown_event.is_set():
                logger.info("Shutting down gracefully...")
                await self.ws_client.stop()
                await self._send_shutdown_notification()

        except KeyboardInterrupt:
            logger.info("\nStopping WebSocket monitor (KeyboardInterrupt)...")
            await self.ws_client.stop()
            self.notifier.send_message("👋 Crypto Price Monitoring Bot stopped.")
        except Exception as e:
            logger.error(f"Unexpected error in WebSocket monitor: {e}")
            if self.ws_client:
                await self.ws_client.stop()
            raise

    async def print_statistics(self):
        """Print WebSocket connection statistics"""
        if self.ws_client:
            stats = self.ws_client.get_statistics()
            print(f"\n📊 WebSocket Statistics:")
            print(f"  State: {stats['state']}")
            print(f"  Messages received: {stats['messages_received']}")
            print(f"  Reconnections: {stats['reconnect_count']}")
            print(f"  Uptime: {stats['uptime_seconds']:.1f}s")
            if stats['last_message_time']:
                print(f"  Last update: {stats['last_message_time']}")


def test_volatility_alert():
    """Test volatility monitoring by sending a test alert"""
    print("\n=== Testing Volatility Monitoring ===\n")

    config = ConfigManager()
    fetcher = BinancePriceFetcher()
    notifier = TelegramNotifier()

    enabled_coins = config.get_enabled_coins()

    for coin_config in enabled_coins:
        try:
            price = fetcher.get_current_price(coin_config.symbol)
            if price:
                # Calculate a fake high volatility scenario
                fake_high_price = price * 1.05
                fake_low_price = price * 0.98
                fake_volatility = ((fake_high_price - fake_low_price) / fake_low_price) * 100

                coin = get_coin_display_name(coin_config.symbol)

                print(f"Testing {coin}...")
                print(f"  Current Price: {format_price(price)}")
                print(f"  Volatility Threshold: {coin_config.volatility_percent}%")
                print(f"  Simulated Volatility: {fake_volatility:.2f}%")

                # Send test alert
                message = (
                    f"🧪 <b>Test Alert - Volatility Monitoring</b>\n"
                    f"🪙 {coin_config.symbol}\n"
                    f"💰 Current Price: {format_price(price)}\n"
                    f"📊 Your Alert Threshold: {coin_config.volatility_percent}% in {coin_config.volatility_window}s\n"
                    f"✅ Volatility monitoring is ACTIVE\n"
                    f"📈 Simulated Alert: {fake_volatility:.2f}% would trigger alert!\n"
                    f"⏱️ {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                )
                notifier.send_message(message)
                print(f"  ✓ Test alert sent!\n")
        except Exception as e:
            logger.error(f"Error testing {coin_config.coin_name}: {e}")

    print("Test complete! Check your Telegram for the test alerts.\n")


def show_status():
    """Show current monitoring status"""
    print("\n" + "="*60)
    print("Crypto Price Monitoring Status")
    print("="*60 + "\n")

    config = ConfigManager()
    fetcher = BinancePriceFetcher()

    enabled_coins = config.get_enabled_coins()

    for coin_config in enabled_coins:
        try:
            price = fetcher.get_current_price(coin_config.symbol)
            if price:
                threshold_str = f"${int(coin_config.integer_threshold):,}" if coin_config.integer_threshold >= 1 else f"${coin_config.integer_threshold}"
                emoji = get_coin_emoji(coin_config.coin_name)

                print(f"{emoji} 🪙 {coin_config.coin_name}")
                print(f"   Symbol: {coin_config.symbol}")
                print(f"   Current Price: {format_price(price)}")
                print(f"   Integer Milestone: every {threshold_str}")
                print(f"   Volatility Alert: {coin_config.volatility_percent}% in {coin_config.volatility_window}s")
                print()
        except Exception as e:
            logger.error(f"Error fetching status for {coin_config.coin_name}: {e}")

    print("="*60 + "\n")


def main():
    """Main entry point"""
    # Setup logging
    setup_logging()

    # Load configuration
    config = ConfigManager()

    # Check command line arguments
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "--test":
            test_volatility_alert()
            return
        elif arg == "--status":
            show_status()
            return
        elif arg in ["--help", "-h"]:
            print(__doc__)
            return

    # Default: Use WebSocket mode
    try:
        monitor = WebSocketMultiCoinMonitor(config)
        asyncio.run(monitor.run())
    except KeyboardInterrupt:
        logger.info("\nShutting down gracefully...")


if __name__ == "__main__":
    main()

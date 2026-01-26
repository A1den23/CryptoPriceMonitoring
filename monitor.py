#!/usr/bin/env python3
"""
Crypto Price Monitoring Bot with WebSocket Support
Monitors multiple cryptocurrency prices in real-time via WebSocket and sends alerts via Telegram

Usage:
    python monitor.py              # Run monitoring with WebSocket (default)
    python monitor.py --test       # Test volatility alerts
    python monitor.py --status     # Show current status
    python monitor.py --polling    # Use old polling mode (fallback)
"""

import os
import sys
import time
import asyncio
from datetime import datetime, timedelta
from collections import deque
from typing import Optional, List, Dict

from common import (
    setup_logging,
    ConfigManager,
    CoinConfig,
    BinancePriceFetcher,
    AsyncBinancePriceFetcher,
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
        self.last_integer_milestone = None
        self.last_price = None
        self.last_processed_price = None  # Last price that triggered alerts

        # Milestone notification cooldown tracking (milestone -> last notification time)
        self.last_milestone_notification_time: Dict[float, datetime] = {}
        self.milestone_cooldown_seconds = 60  # 1 minute cooldown for same milestone

    def check_integer_milestone(self, current_price: float) -> bool:
        """Check if price reached an integer milestone using crossing detection"""
        threshold = self.config.integer_threshold
        coin = get_coin_display_name(self.config.symbol)

        # Initialize last_price on first run
        if self.last_price is None:
            self.last_price = current_price
            return False

        # For larger thresholds (>= 1), use integer-based checking (BTC, ETH, SOL)
        if threshold >= 1:
            price_int = int(current_price)
            current_milestone = int(price_int / threshold) * threshold

            last_price_int = int(self.last_price)
            last_milestone = int(last_price_int / threshold) * threshold

            # Method 1: Crossing detection (优先) - 检测是否跨越了关口线
            if last_milestone != current_milestone:
                # Check cooldown: skip if we recently notified for this milestone
                now = datetime.now(UTC8)
                if current_milestone in self.last_milestone_notification_time:
                    time_since_last = (now - self.last_milestone_notification_time[current_milestone]).total_seconds()
                    if time_since_last < self.milestone_cooldown_seconds:
                        # In cooldown period, skip notification but still update tracking
                        logger.debug(f"[{coin}] Milestone ${current_milestone:,} in cooldown ({time_since_last:.0f}s ago)")
                        self.last_price = current_price
                        return False

                direction = "📈" if current_price > self.last_price else "📉"

                self.last_integer_milestone = current_milestone
                self.last_price = current_price
                self.last_milestone_notification_time[current_milestone] = now

                message = (
                    f"🎯 <b>Integer Milestone Alert!</b>\n"
                    f"🪙 {self.config.symbol}\n"
                    f"💰 Price: {format_price(current_price)}\n"
                    f"📍 Milestone: ${current_milestone:,}\n"
                    f"{direction} Direction: {'Up' if direction == '📈' else 'Down'}\n"
                    f"🕐 {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                )
                self.notifier.send_message(message)
                logger.info(f"[{coin}] Crossed milestone: ${current_milestone:,}")
                return True

        else:
            # For small thresholds (< 1), use precise checking for stablecoins (USD1)
            offset = current_price - 1.0
            current_milestone = 1.0 + round(offset / threshold) * threshold

            last_offset = self.last_price - 1.0
            last_milestone = 1.0 + round(last_offset / threshold) * threshold

            # Crossing detection for stablecoins
            if last_milestone != current_milestone:
                # Check cooldown: skip if we recently notified for this milestone
                now = datetime.now(UTC8)
                if current_milestone in self.last_milestone_notification_time:
                    time_since_last = (now - self.last_milestone_notification_time[current_milestone]).total_seconds()
                    if time_since_last < self.milestone_cooldown_seconds:
                        # In cooldown period, skip notification but still update tracking
                        logger.debug(f"[{coin}] Milestone {format_price(current_milestone)} in cooldown ({time_since_last:.0f}s ago)")
                        self.last_price = current_price
                        return False

                direction = "📈" if current_price > self.last_price else "📉"

                self.last_integer_milestone = current_milestone
                self.last_price = current_price
                self.last_milestone_notification_time[current_milestone] = now

                message = (
                    f"🎯 <b>Integer Milestone Alert!</b>\n"
                    f"🪙 {self.config.symbol}\n"
                    f"💰 Price: {format_price(current_price)}\n"
                    f"📍 Milestone: {format_price(current_milestone)}\n"
                    f"{direction} Direction: {'Up' if direction == '📈' else 'Down'}\n"
                    f"🕐 {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                )
                self.notifier.send_message(message)
                logger.info(f"[{coin}] Crossed milestone: {format_price(current_milestone)}")
                return True

        # Update last price for next iteration
        self.last_price = current_price
        return False

    def check_volatility(self, current_price: float) -> Optional[str]:
        """Check if price volatility exceeds threshold within time window"""
        current_time = datetime.now(UTC8)

        # Add current price to history
        self.price_history.append(PriceData(current_price, current_time))

        # Remove old data outside the time window
        cutoff_time = current_time - timedelta(seconds=self.config.volatility_window)
        while self.price_history and self.price_history[0].timestamp < cutoff_time:
            self.price_history.popleft()

        # Need at least 2 data points to calculate volatility
        if len(self.price_history) < 2:
            return None

        # Calculate min and max prices in the window
        prices = [p.price for p in self.price_history]
        min_price = min(prices)
        max_price = max(prices)

        # Calculate volatility percentage
        if min_price > 0:
            volatility = ((max_price - min_price) / min_price) * 100
        else:
            return None

        # Return volatility info for display
        volatility_info = f"{volatility:.2f}%/{len(self.price_history)}pts"

        if volatility >= self.config.volatility_percent:
            change = current_price - self.price_history[0].price
            change_percent = (change / self.price_history[0].price) * 100
            direction = "📈" if change > 0 else "📉"
            coin = get_coin_display_name(self.config.symbol)

            message = (
                f"🚨 <b>High Volatility Alert!</b>\n"
                f"🪙 {self.config.symbol}\n"
                f"💰 Current: {format_price(current_price)}\n"
                f"📊 Volatility: {volatility:.2f}% in {self.config.volatility_window}s\n"
                f"{direction} Change: {change_percent:+.2f}%\n"
                f"⏱️ {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.notifier.send_message(message)
            logger.info(f"[{coin}] High volatility: {volatility:.2f}%")

            # Clear history to avoid duplicate alerts
            self.price_history.clear()
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

    def _load_monitors(self):
        """Load monitors from configuration"""
        enabled_coins = self.config.get_enabled_coins()
        for coin_config in enabled_coins:
            monitor = PriceMonitor(coin_config, self.notifier)
            self.monitors[coin_config.symbol] = monitor
            logger.info(f"✓ Loaded {coin_config}")

        if not self.monitors:
            logger.warning("No coins enabled in configuration!")

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
            # Start WebSocket (runs forever until interrupted)
            await self.ws_client.start()

        except KeyboardInterrupt:
            logger.info("\nStopping WebSocket monitor...")
            await self.ws_client.stop()
            self.notifier.send_message("👋 Crypto Price Monitoring Bot stopped.")

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


class PollingMultiCoinMonitor:
    """
    Legacy polling-based monitor (fallback mode)

    Uses HTTP polling instead of WebSocket. Less efficient but more compatible.
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self.notifier = TelegramNotifier()

        # Load configurations for all enabled coins
        self.monitors: List[PriceMonitor] = []
        self._load_monitors()

    def _load_monitors(self):
        """Load monitors from configuration"""
        enabled_coins = self.config.get_enabled_coins()
        for coin_config in enabled_coins:
            monitor = PriceMonitor(coin_config, self.notifier)
            self.monitors.append(monitor)
            logger.info(f"✓ Loaded {coin_config}")

        if not self.monitors:
            logger.warning("No coins enabled in configuration!")

    async def fetch_all_prices_async(self) -> dict:
        """Fetch all prices concurrently using async"""
        symbols = [monitor.config.symbol for monitor in self.monitors]

        async with AsyncBinancePriceFetcher() as fetcher:
            prices = await fetcher.get_multiple_prices(symbols)

        return prices

    def run(self):
        """Main monitoring loop with concurrent price fetching"""
        print(f"\n{'='*60}")
        print(f"Starting Multi-Coin Price Monitor (Polling Mode)")
        print(f"{'='*60}")
        print(f"Monitored coins: {len(self.monitors)}")
        print(f"Check interval: {self.config.check_interval}s")
        print(f"⚠️  Using polling mode. Consider using WebSocket for better performance.")
        print(f"{'='*60}\n")

        # Test Telegram connection
        if not self.notifier.test_connection():
            logger.warning("Failed to send test message. Check your Telegram configuration.")

        try:
            # Create event loop for async operations
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            while True:
                print(f"[{datetime.now(UTC8).strftime('%H:%M:%S')}] Checking prices...")

                # Fetch prices concurrently
                prices = loop.run_until_complete(self.fetch_all_prices_async())

                # Check all monitors
                for monitor in self.monitors:
                    price = prices.get(monitor.config.symbol)
                    if price:
                        output = monitor.check(price)
                        print(output)
                    else:
                        logger.error(f"Failed to get price for {monitor.config.symbol}")

                print()
                time.sleep(self.config.check_interval)

        except KeyboardInterrupt:
            logger.info("\nStopping monitor...")
            self.notifier.send_message("👋 Crypto Price Monitoring Bot stopped.")
        finally:
            loop.close()


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
        elif arg == "--polling":
            # Use old polling mode
            monitor = PollingMultiCoinMonitor(config)
            monitor.run()
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

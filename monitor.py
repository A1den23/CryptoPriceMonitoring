#!/usr/bin/env python3
"""
Crypto Price Monitoring Bot
Monitors multiple cryptocurrency prices and sends alerts via Telegram

Usage:
    python monitor.py              # Run monitoring
    python monitor.py --test       # Test volatility alerts
    python monitor.py --status     # Show current status
"""

import os
import sys
import time
import asyncio
from datetime import datetime, timedelta
from collections import deque
from typing import Optional, List

from common import (
    setup_logging,
    ConfigManager,
    CoinConfig,
    BinancePriceFetcher,
    AsyncBinancePriceFetcher,
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
                direction = "📈" if current_price > self.last_price else "📉"

                self.last_integer_milestone = current_milestone
                self.last_price = current_price

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

            # Method 2: Proximity detection (兜底) - 价格非常接近关口时也触发
            if threshold >= 1000:
                proximity = 5
            elif threshold >= 100:
                proximity = 2
            else:
                proximity = 0.1

            if abs(current_price - current_milestone) < proximity:
                if current_milestone != self.last_integer_milestone:
                    self.last_integer_milestone = current_milestone

                    message = (
                        f"🎯 <b>Near Integer Milestone!</b>\n"
                        f"🪙 {self.config.symbol}\n"
                        f"💰 Price: {format_price(current_price)}\n"
                        f"📍 Milestone: ${current_milestone:,}\n"
                        f"📏 Distance: {format_price(abs(current_price - current_milestone))} away\n"
                        f"🕐 {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    self.notifier.send_message(message)
                    logger.info(f"[{coin}] Near milestone: ${current_milestone:,}")
                    return True

        else:
            # For small thresholds (< 1), use precise checking for stablecoins (USD1)
            offset = current_price - 1.0
            current_milestone = 1.0 + round(offset / threshold) * threshold

            last_offset = self.last_price - 1.0
            last_milestone = 1.0 + round(last_offset / threshold) * threshold

            # Crossing detection for stablecoins
            if last_milestone != current_milestone:
                direction = "📈" if current_price > self.last_price else "📉"

                self.last_integer_milestone = current_milestone
                self.last_price = current_price

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

            # Proximity detection for stablecoins (within 10% of threshold)
            if abs(current_price - current_milestone) < threshold * 0.1:
                if current_milestone != self.last_integer_milestone:
                    self.last_integer_milestone = current_milestone

                    message = (
                        f"🎯 <b>Near Integer Milestone!</b>\n"
                        f"🪙 {self.config.symbol}\n"
                        f"💰 Price: {format_price(current_price)}\n"
                        f"📍 Milestone: {format_price(current_milestone)}\n"
                        f"📏 Distance: {format_price(abs(current_price - current_milestone))} away\n"
                        f"🕐 {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    self.notifier.send_message(message)
                    logger.info(f"[{coin}] Near milestone: {format_price(current_milestone)}")
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

    def check(self, current_price: float) -> str:
        """Check price and return formatted output"""
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


class MultiCoinMonitor:
    """Monitor multiple cryptocurrencies with async price fetching"""

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

    def check_all_sync(self) -> dict:
        """Fetch all prices synchronously (fallback)"""
        fetcher = BinancePriceFetcher()
        prices = {}
        for monitor in self.monitors:
            try:
                price = fetcher.get_current_price(monitor.config.symbol)
                prices[monitor.config.symbol] = price
            except Exception as e:
                logger.error(f"Failed to fetch {monitor.config.symbol}: {e}")
                prices[monitor.config.symbol] = None
        return prices

    def run(self):
        """Main monitoring loop with concurrent price fetching"""
        print(f"\n{'='*60}")
        print(f"Starting Multi-Coin Price Monitor")
        print(f"{'='*60}")
        print(f"Monitored coins: {len(self.monitors)}")
        print(f"Check interval: {self.config.check_interval}s")
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

    # Normal monitoring mode
    monitor = MultiCoinMonitor(config)
    monitor.run()


if __name__ == "__main__":
    main()

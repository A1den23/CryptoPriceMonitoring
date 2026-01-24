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
import json
from datetime import datetime, timezone, timedelta
from collections import deque
from typing import Optional, Dict, List
import requests
from dotenv import load_dotenv
import threading

# Load environment variables
load_dotenv()

# Debug mode - set DEBUG=true in .env to enable
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"

# UTC+8 Timezone
UTC8 = timezone(timedelta(hours=8))


class PriceData:
    """Store price data with timestamp"""
    def __init__(self, price: float, timestamp: datetime):
        self.price = price
        self.timestamp = timestamp


class TelegramNotifier:
    """Handle Telegram notifications"""
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.lock = threading.Lock()

    def send_message(self, message: str) -> bool:
        """Send message via Telegram bot"""
        if not self.bot_token or not self.chat_id:
            print("Warning: Telegram bot_token or chat_id not configured")
            return False

        with self.lock:
            try:
                url = f"{self.base_url}/sendMessage"
                data = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML"
                }
                response = requests.post(url, json=data, timeout=10)
                response.raise_for_status()
                return True
            except Exception as e:
                print(f"Error sending Telegram message: {e}")
                return False

    def test_connection(self) -> bool:
        """Test Telegram bot connection"""
        return self.send_message("🤖 <b>Crypto Price Monitoring Bot</b> is now active!\n\nMonitoring multiple cryptocurrencies...")


class BinancePriceFetcher:
    """Fetch prices from Binance API"""
    def __init__(self):
        self.base_url = "https://api.binance.com/api/v3"
        self.lock = threading.Lock()

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price from Binance"""
        try:
            url = f"{self.base_url}/ticker/price"
            params = {"symbol": symbol}
            with self.lock:
                response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data["price"])
        except Exception as e:
            print(f"Error fetching price for {symbol}: {e}")
            return None


class CoinConfig:
    """Configuration for a single coin"""
    def __init__(self, coin_name: str):
        self.coin_name = coin_name
        self.enabled = os.getenv(f"{coin_name}_ENABLED", "false").lower() == "true"
        self.symbol = os.getenv(f"{coin_name}_SYMBOL", f"{coin_name}USDT")
        self.integer_threshold = float(os.getenv(f"{coin_name}_INTEGER_THRESHOLD", "1000"))
        self.volatility_percent = float(os.getenv(f"{coin_name}_VOLATILITY_PERCENT", "3.0"))
        self.volatility_window = int(os.getenv(f"{coin_name}_VOLATILITY_WINDOW_SECONDS", "60"))

    def __str__(self):
        # Format threshold based on its value
        if self.integer_threshold >= 1:
            threshold_str = f"{int(self.integer_threshold):,}"
        else:
            threshold_str = f"{self.integer_threshold}"
        return (
            f"{self.coin_name}: enabled={self.enabled}, symbol={self.symbol}, "
            f"integer_threshold={threshold_str}, "
            f"volatility={self.volatility_percent}%/{self.volatility_window}s"
        )


class PriceMonitor:
    """Monitor price changes for a single coin"""
    def __init__(self, config: CoinConfig, fetcher: BinancePriceFetcher, notifier: TelegramNotifier):
        self.config = config
        self.fetcher = fetcher
        self.notifier = notifier

        # State tracking
        self.price_history: deque[PriceData] = deque()
        self.last_integer_milestone = None
        self.last_price = None  # Track last price for milestone crossing detection

    def format_price(self, price: float) -> str:
        """Format price for display"""
        if price >= 1000:
            return f"${price:,.2f}"
        elif price >= 10:
            return f"${price:.2f}"
        else:
            # For prices < 10 (including stablecoins like USD1), show 4 decimals
            return f"${price:.4f}"

    def get_coin_name(self) -> str:
        """Get display name for the coin"""
        return self.config.symbol.replace("USDT", "")

    def check_integer_milestone(self, current_price: float) -> bool:
        """Check if price reached an integer milestone using crossing detection"""
        threshold = self.config.integer_threshold
        coin = self.get_coin_name()

        # Initialize last_price on first run
        if self.last_price is None:
            self.last_price = current_price
            return False

        # For larger thresholds (>= 1), use integer-based checking (BTC, ETH, SOL)
        if threshold >= 1:
            # Calculate current milestone
            price_int = int(current_price)
            current_milestone = int(price_int / threshold) * threshold

            # Calculate last milestone
            last_price_int = int(self.last_price)
            last_milestone = int(last_price_int / threshold) * threshold

            # Method 1: Crossing detection (优先) - 检测是否跨越了关口线
            if last_milestone != current_milestone:
                # Crossed the milestone line!
                # Determine direction BEFORE updating last_price
                direction = "📈" if current_price > self.last_price else "📉"

                self.last_integer_milestone = current_milestone
                self.last_price = current_price

                message = (
                    f"🎯 <b>Integer Milestone Alert!</b>\n"
                    f"🪙 {self.config.symbol}\n"
                    f"💰 Price: {self.format_price(current_price)}\n"
                    f"📍 Milestone: ${current_milestone:,}\n"
                    f"{direction} Direction: {'Up' if direction == '📈' else 'Down'}\n"
                    f"🕐 {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                )
                self.notifier.send_message(message)
                print(f"[{coin}] Crossed milestone: ${current_milestone:,}")
                return True

            # Method 2: Proximity detection (兜底) - 价格非常接近关口时也触发
            # Calculate proximity threshold based on coin price
            if threshold >= 1000:
                # For BTC: within $5 of milestone
                proximity = 5
            elif threshold >= 100:
                # For ETH: within $2 of milestone
                proximity = 2
            else:
                # For SOL: within $0.1 of milestone (1% of threshold)
                proximity = 0.1

            if abs(current_price - current_milestone) < proximity:
                if current_milestone != self.last_integer_milestone:
                    self.last_integer_milestone = current_milestone

                    message = (
                        f"🎯 <b>Near Integer Milestone!</b>\n"
                        f"🪙 {self.config.symbol}\n"
                        f"💰 Price: {self.format_price(current_price)}\n"
                        f"📍 Milestone: ${current_milestone:,}\n"
                        f"📏 Distance: {self.format_price(abs(current_price - current_milestone))} away\n"
                        f"🕐 {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    self.notifier.send_message(message)
                    print(f"[{coin}] Near milestone: ${current_milestone:,}")
                    return True

        else:
            # For small thresholds (< 1), use precise checking for stablecoins (USD1)
            # Milestones work both above and below 1.0
            # Above 1.0: 1.0005, 1.0010, 1.0015, ...
            # Below 1.0: 0.9995, 0.9990, 0.9985, ...

            # Calculate the current milestone
            offset = current_price - 1.0  # Can be positive or negative
            current_milestone = 1.0 + round(offset / threshold) * threshold

            # Calculate last milestone
            last_offset = self.last_price - 1.0
            last_milestone = 1.0 + round(last_offset / threshold) * threshold

            # Crossing detection for stablecoins
            if last_milestone != current_milestone:
                # Determine direction BEFORE updating last_price
                direction = "📈" if current_price > self.last_price else "📉"

                self.last_integer_milestone = current_milestone
                self.last_price = current_price

                message = (
                    f"🎯 <b>Integer Milestone Alert!</b>\n"
                    f"🪙 {self.config.symbol}\n"
                    f"💰 Price: {self.format_price(current_price)}\n"
                    f"📍 Milestone: {self.format_price(current_milestone)}\n"
                    f"{direction} Direction: {'Up' if direction == '📈' else 'Down'}\n"
                    f"🕐 {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                )
                self.notifier.send_message(message)
                print(f"[{coin}] Crossed milestone: {self.format_price(current_milestone)}")
                return True

            # Proximity detection for stablecoins (within 10% of threshold)
            if abs(current_price - current_milestone) < threshold * 0.1:
                if current_milestone != self.last_integer_milestone:
                    self.last_integer_milestone = current_milestone

                    message = (
                        f"🎯 <b>Near Integer Milestone!</b>\n"
                        f"🪙 {self.config.symbol}\n"
                        f"💰 Price: {self.format_price(current_price)}\n"
                        f"📍 Milestone: {self.format_price(current_milestone)}\n"
                        f"📏 Distance: {self.format_price(abs(current_price - current_milestone))} away\n"
                        f"🕐 {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    self.notifier.send_message(message)
                    print(f"[{coin}] Near milestone: {self.format_price(current_milestone)}")
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
            coin = self.get_coin_name()

            message = (
                f"🚨 <b>High Volatility Alert!</b>\n"
                f"🪙 {self.config.symbol}\n"
                f"💰 Current: {self.format_price(current_price)}\n"
                f"📊 Volatility: {volatility:.2f}% in {self.config.volatility_window}s\n"
                f"{direction} Change: {change_percent:+.2f}%\n"
                f"⏱️ {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.notifier.send_message(message)
            print(f"[{coin}] High volatility: {volatility:.2f}%")

            # Clear history to avoid duplicate alerts
            self.price_history.clear()
            return volatility_info

        return volatility_info

    def check(self) -> Optional[float]:
        """Check price and return it"""
        current_price = self.fetcher.get_current_price(self.config.symbol)
        if current_price:
            coin = self.get_coin_name()

            # Check for integer milestone
            milestone_alert = self.check_integer_milestone(current_price)

            # Check for volatility
            volatility_info = self.check_volatility(current_price)

            # Format output
            output = f"[{coin}] {self.format_price(current_price)}"
            if milestone_alert:
                output += " 🎯"
            if volatility_info:
                # Show volatility info (e.g., "📊1.23%/15pts")
                output += f" 📊{volatility_info}"

            print(output)

        return current_price


class MultiCoinMonitor:
    """Monitor multiple cryptocurrencies"""
    def __init__(self):
        self.fetcher = BinancePriceFetcher()
        self.notifier = TelegramNotifier()
        self.check_interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "5"))

        # Load configurations for all coins
        self.monitors: List[PriceMonitor] = []
        self._load_monitors()

    def _load_monitors(self):
        """Load monitors from environment variables"""
        # Define available coins
        coin_names = ["BTC", "ETH", "SOL", "USD1"]

        for coin_name in coin_names:
            config = CoinConfig(coin_name)
            if config.enabled:
                monitor = PriceMonitor(config, self.fetcher, self.notifier)
                self.monitors.append(monitor)
                print(f"✓ Loaded {config}")

        if not self.monitors:
            print("Warning: No coins enabled in configuration!")

    def run(self):
        """Main monitoring loop"""
        print(f"\n{'='*60}")
        print(f"Starting Multi-Coin Price Monitor")
        print(f"{'='*60}")
        print(f"Monitored coins: {len(self.monitors)}")
        print(f"Check interval: {self.check_interval}s")
        print(f"{'='*60}\n")

        # Test Telegram connection
        if not self.notifier.test_connection():
            print("Warning: Failed to send test message. Check your Telegram configuration.")

        try:
            while True:
                print(f"[{datetime.now(UTC8).strftime('%H:%M:%S')}] Checking prices...")

                # Check all enabled monitors
                for monitor in self.monitors:
                    monitor.check()

                print()
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            print("\n\nStopping monitor...")
            self.notifier.send_message("👋 Crypto Price Monitoring Bot stopped.")


def format_price_for_display(price: float) -> str:
    """Format price for display (helper function)"""
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 10:
        return f"${price:.2f}"
    else:
        return f"${price:.4f}"


def test_volatility_alert():
    """Test volatility monitoring by sending a test alert"""
    print("\n=== Testing Volatility Monitoring ===\n")

    fetcher = BinancePriceFetcher()
    notifier = TelegramNotifier()

    # Test each enabled coin
    coin_names = ["BTC", "ETH", "SOL", "USD1"]

    for coin_name in coin_names:
        config = CoinConfig(coin_name)
        if config.enabled:
            price = fetcher.get_current_price(config.symbol)
            if price:
                # Calculate a fake high volatility scenario
                fake_high_price = price * 1.05
                fake_low_price = price * 0.98
                fake_volatility = ((fake_high_price - fake_low_price) / fake_low_price) * 100

                coin = config.symbol.replace("USDT", "")

                print(f"Testing {coin}...")
                print(f"  Current Price: {format_price_for_display(price)}")
                print(f"  Volatility Threshold: {config.volatility_percent}%")
                print(f"  Simulated Volatility: {fake_volatility:.2f}%")

                # Send test alert
                message = (
                    f"🧪 <b>Test Alert - Volatility Monitoring</b>\n"
                    f"🪙 {config.symbol}\n"
                    f"💰 Current Price: {format_price_for_display(price)}\n"
                    f"📊 Your Alert Threshold: {config.volatility_percent}% in {config.volatility_window}s\n"
                    f"✅ Volatility monitoring is ACTIVE\n"
                    f"📈 Simulated Alert: {fake_volatility:.2f}% would trigger alert!\n"
                    f"⏱️ {datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')}"
                )
                notifier.send_message(message)
                print(f"  ✓ Test alert sent!\n")

    print("Test complete! Check your Telegram for the test alerts.\n")


def show_status():
    """Show current monitoring status"""
    print("\n" + "="*60)
    print("Crypto Price Monitoring Status")
    print("="*60 + "\n")

    fetcher = BinancePriceFetcher()

    # Test each enabled coin
    coin_names = ["BTC", "ETH", "SOL", "USD1"]

    for coin_name in coin_names:
        config = CoinConfig(coin_name)
        if config.enabled:
            price = fetcher.get_current_price(config.symbol)
            if price:
                # Format threshold based on its value
                if config.integer_threshold >= 1:
                    threshold_str = f"${int(config.integer_threshold):,}"
                else:
                    threshold_str = f"${config.integer_threshold}"

                # Format price based on its value
                if price >= 1000:
                    price_str = f"${price:,.2f}"
                elif price >= 10:
                    price_str = f"${price:.2f}"
                else:
                    price_str = f"${price:.4f}"

                print(f"🪙 {coin_name}")
                print(f"   Symbol: {config.symbol}")
                print(f"   Current Price: {price_str}")
                print(f"   Integer Milestone: every {threshold_str}")
                print(f"   Volatility Alert: {config.volatility_percent}% in {config.volatility_window}s")
                print()

    print("="*60 + "\n")


def main():
    """Main entry point"""
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
    monitor = MultiCoinMonitor()
    monitor.run()


if __name__ == "__main__":
    main()

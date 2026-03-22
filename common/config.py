"""
Configuration management for Crypto Price Monitoring Bot
"""

import math
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs) -> bool:
        """Fallback when python-dotenv is unavailable."""
        return False


_ENV_LOADED = False
_ENV_PATH = Path(__file__).parent.parent / ".env"


def load_environment() -> None:
    """Load environment variables from the project .env file once."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    if _ENV_PATH.exists():
        load_dotenv(dotenv_path=_ENV_PATH, override=False)
    _ENV_LOADED = True


def _safe_int_env(
    name: str,
    default: int,
    min_val: int = 0,
    max_val: int = 1_000_000_000,
) -> int:
    """Safely read integer environment variable with bounds."""
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < min_val or value > max_val:
        return default
    return value


def _safe_float_env(
    name: str,
    default: float,
    min_val: float = 0.0,
    max_val: float = 1_000_000_000.0,
) -> float:
    """Safely read float environment variable with bounds."""
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    if value < min_val or value > max_val:
        return default
    return value


def _safe_int(
    value: str,
    default: int,
    min_val: int = 0,
    max_val: int = 1_000_000_000,
) -> int:
    """Safely parse integer string with bounds."""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < min_val or result > max_val:
        return default
    return result


def _safe_float(
    value: str,
    default: float,
    min_val: float = 0.0,
    max_val: float = 1_000_000_000.0,
) -> float:
    """Safely parse float string with bounds."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    if result < min_val or result > max_val:
        return default
    return result


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
        """Create CoinConfig from environment variables with validation."""
        threshold_str = os.getenv(f"{coin_name}_INTEGER_THRESHOLD", "1000")
        threshold = _safe_float(threshold_str, 1000.0, 0.0001, 1e9)

        volatility_str = os.getenv(f"{coin_name}_VOLATILITY_PERCENT", "3.0")
        volatility = _safe_float(volatility_str, 3.0, 0.0, 1000.0)

        window_str = os.getenv(f"{coin_name}_VOLATILITY_WINDOW_SECONDS", "60")
        window = _safe_int(window_str, 60, 1, 86400)

        return cls(
            coin_name=coin_name,
            enabled=os.getenv(f"{coin_name}_ENABLED", "false").lower() == "true",
            symbol=os.getenv(f"{coin_name}_SYMBOL", f"{coin_name}USDT"),
            integer_threshold=threshold,
            volatility_percent=volatility,
            volatility_window=window,
            volume_alert_multiplier=_safe_float(
                os.getenv(f"{coin_name}_VOLUME_ALERT_MULTIPLIER", "10.0"),
                10.0, 1.0, 10000.0
            )
        )

    def __str__(self) -> str:
        if self.integer_threshold >= 1 and self.integer_threshold.is_integer():
            threshold_str = f"{int(self.integer_threshold):,}"
        else:
            threshold_str = f"{self.integer_threshold}"
        return (
            f"{self.coin_name}: enabled={self.enabled}, symbol={self.symbol}, "
            f"integer_threshold={threshold_str}, "
            f"volatility={self.volatility_percent}%/{self.volatility_window}s, "
            f"volume_alert={self.volume_alert_multiplier}x"
        )


class ConfigManager:
    """Centralized configuration management."""

    def __init__(self) -> None:
        load_environment()

        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        # Note: CHECK_INTERVAL_SECONDS is kept for backwards compatibility but not used
        # (WebSocket mode provides real-time updates without polling)
        self.check_interval = _safe_int_env("CHECK_INTERVAL_SECONDS", 5, 1, 86400)
        self.debug_mode = os.getenv("DEBUG", "false").lower() == "true"
        # Volume alert cooldown (global default, can be overridden per coin in the future)
        self.volume_alert_cooldown_seconds = _safe_int_env("VOLUME_ALERT_COOLDOWN_SECONDS", 5, 0, 86400)
        # Volatility alert cooldown (time between volatility notifications)
        self.volatility_alert_cooldown_seconds = _safe_int_env("VOLATILITY_ALERT_COOLDOWN_SECONDS", 60, 0, 86400)
        # Milestone alert cooldown (global cooldown for any milestone crossing)
        self.milestone_alert_cooldown_seconds = _safe_int_env("MILESTONE_ALERT_COOLDOWN_SECONDS", 600, 0, 604800)
        # WebSocket keepalive and stale-connection detection
        self.ws_ping_interval_seconds = _safe_float_env("WS_PING_INTERVAL_SECONDS", 30.0, 1.0, 3600.0)
        self.ws_pong_timeout_seconds = _safe_float_env("WS_PONG_TIMEOUT_SECONDS", 10.0, 1.0, 300.0)
        self.ws_message_timeout_seconds = _safe_float_env("WS_MESSAGE_TIMEOUT_SECONDS", 120.0, 1.0, 86400.0)
        self.stablecoin_depeg_monitor_enabled = os.getenv(
            "STABLECOIN_DEPEG_MONITOR_ENABLED",
            "false",
        ).lower() == "true"
        self.stablecoin_depeg_top_n = _safe_int_env("STABLECOIN_DEPEG_TOP_N", 20, 1, 1000)
        self.stablecoin_depeg_threshold_percent = _safe_float_env(
            "STABLECOIN_DEPEG_THRESHOLD_PERCENT",
            5.0,
            0.0,
            1000.0,
        )
        self.stablecoin_depeg_poll_interval_seconds = _safe_int_env(
            "STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS",
            300,
            1,
            86400,
        )
        self.stablecoin_depeg_alert_cooldown_seconds = _safe_int_env(
            "STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS",
            3600,
            0,
            604800,
        )

        # Get coin list from env or use default
        coin_list = os.getenv("COIN_LIST", "BTC,ETH,SOL,USD1")
        self.coin_names = [coin.strip() for coin in coin_list.split(",") if coin.strip()]

        # Load all coin configurations
        self.coins: dict[str, CoinConfig] = {}
        self._load_coins()

    def _load_coins(self) -> None:
        """Load configurations for all coins."""
        for coin_name in self.coin_names:
            self.coins[coin_name] = CoinConfig.from_env(coin_name)

    def get_enabled_coins(self) -> list[CoinConfig]:
        """Get list of enabled coin configurations."""
        return [config for config in self.coins.values() if config.enabled]

    def get_coin_config(self, coin_name: str) -> CoinConfig | None:
        """Get configuration for specific coin."""
        return self.coins.get(coin_name)

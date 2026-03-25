"""
Logging utilities for Crypto Price Monitoring Bot
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Union


DEFAULT_LOG_MAX_BYTES = 1_048_576
DEFAULT_LOG_BACKUP_COUNT = 3


def _resolve_log_level(level: Union[int, str, None]) -> int:
    """Resolve log level from explicit value or LOG_LEVEL/DEBUG env."""
    if level is not None:
        if isinstance(level, str):
            name = level.strip().upper()
            return logging._nameToLevel.get(name, logging.INFO)
        return level

    env_level = os.getenv("LOG_LEVEL")
    if env_level:
        name = env_level.strip().upper()
        return logging._nameToLevel.get(name, logging.INFO)

    if os.getenv("DEBUG", "false").lower() == "true":
        return logging.DEBUG

    return logging.INFO


def setup_logging(
    log_file: str = "logs/monitor.log",
    level: Union[int, str, None] = None,
) -> logging.Logger:
    """Setup structured logging with file and console handlers."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    # Try to add file handler, fall back to console only if permission denied
    try:
        # Validate log file path to prevent directory traversal
        log_path = Path(log_file).resolve()

        # Get allowed base directories (current working directory or /app in Docker)
        # Both paths are resolved immediately to prevent race conditions with symlinks
        allowed_bases = [Path.cwd().resolve()]
        app_path = Path("/app")
        if app_path.exists() and app_path.is_dir():
            allowed_bases.append(app_path.resolve())

        # Check if log path is within any allowed directory
        is_allowed = any(
            log_path.is_relative_to(base) for base in allowed_bases
        )
        if not is_allowed:
            allowed_paths = ", ".join(str(b) for b in allowed_bases)
            raise ValueError(
                f"Log file path '{log_file}' is outside allowed directories: {allowed_paths}"
            )

        # Create logs directory if it doesn't exist
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Add rotating file handler with conservative defaults
        handlers.append(
            RotatingFileHandler(
                str(log_path),
                maxBytes=DEFAULT_LOG_MAX_BYTES,
                backupCount=DEFAULT_LOG_BACKUP_COUNT,
            )
        )
    except (PermissionError, OSError, ValueError) as e:
        # Fall back to console only if file logging fails
        print(f"Warning: Could not create log file '{log_file}': {e}")
        print("Logging to console only.")

    # Configure logging
    resolved_level = _resolve_log_level(level)
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
        level=resolved_level,
        handlers=handlers,
        force=True,
    )

    # Reduce sensitive/noisy logs (prevents Telegram bot token from appearing)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return logging.getLogger(__name__)


# Module-level logger (configured after setup_logging is called)
logger = logging.getLogger(__name__)


def get_logger(name: str | None = None) -> logging.Logger:
    """[DEPRECATED] Get a logger instance.

    This function is deprecated. Use logging.getLogger() directly or
    import 'logger' from this module instead.

    Args:
        name: Logger name. If None, uses __name__.

    Returns:
        logging.Logger: Configured logger instance.
    """
    import warnings
    warnings.warn(
        "get_logger() is deprecated, use logging.getLogger() or import 'logger' from this module",
        DeprecationWarning,
        stacklevel=2
    )
    return logging.getLogger(name or __name__)

"""
Logging utilities for Crypto Price Monitoring Bot
"""

import os
import logging
from pathlib import Path
from typing import Optional, Union


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
        # Validate log file path to prevent directory traversal
        log_path = Path(log_file).resolve()

        # Get allowed base directory (current working directory or /app in Docker)
        allowed_base = Path.cwd().resolve()
        if Path("/app").exists():
            allowed_base = Path("/app").resolve()

        # Check if log path is within allowed directory
        try:
            log_path.relative_to(allowed_base)
        except ValueError:
            raise ValueError(f"Log file path '{log_file}' is outside allowed directory '{allowed_base}'")

        # Create logs directory if it doesn't exist
        log_dir = log_path.parent
        log_dir.mkdir(parents=True, exist_ok=True)

        # Add file handler
        handlers.append(logging.FileHandler(str(log_path)))
    except (PermissionError, OSError, ValueError) as e:
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


def get_logger():
    """Get the configured logger"""
    return logging.getLogger(__name__)


# Module-level logger (configured after setup_logging is called)
logger = logging.getLogger(__name__)

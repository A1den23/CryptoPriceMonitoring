"""
Logging utilities for Crypto Price Monitoring Bot
"""

import logging
import os
import re
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Union

from .config import get_configured_log_level_name, is_debug_mode_enabled


DEFAULT_LOG_MAX_BYTES = 1_048_576
DEFAULT_LOG_BACKUP_COUNT = 3
TELEGRAM_TOKEN_PATTERN = re.compile(r"bot\d+(?::|%3[Aa])[A-Za-z0-9_-]+")


def redact_secrets(value: object) -> str:
    """Remove Telegram bot tokens from text destined for logs."""
    redacted = TELEGRAM_TOKEN_PATTERN.sub("bot<redacted>", str(value))
    configured_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if configured_token:
        redacted = redacted.replace(configured_token, "<redacted>")
    return redacted


def safe_exception_text(exc: BaseException) -> str:
    """Describe an exception by type and redacted message, without a traceback."""
    detail = redact_secrets(exc)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _render_log_message(record: logging.LogRecord) -> str:
    """Render a record's message, describing it instead of raising when it is malformed."""
    try:
        return record.getMessage()
    except Exception:
        try:
            return f"Unformattable log record: msg={record.msg!r} args={record.args!r}"
        except Exception:
            return f"Unformattable log record: msg type {type(record.msg).__name__}"


class TokenRedactingFilter(logging.Filter):
    """Rewrite a record in place so every handler renders it without Telegram tokens."""

    _exception_formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(_render_log_message(record))
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = self._exception_formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_secrets(record.stack_info)
        return True


_TOKEN_REDACTING_FILTER = TokenRedactingFilter()


def _redacting_excepthook(exc_type, exc, exc_traceback) -> None:
    """Print an uncaught exception's traceback to stderr without Telegram tokens."""
    rendered = "".join(traceback.format_exception(exc_type, exc, exc_traceback))
    sys.stderr.write(redact_secrets(rendered))


def _install_log_redaction(handlers: list[logging.Handler]) -> None:
    """Redact every output path the logging setup owns, including fallbacks."""
    for handler in handlers:
        handler.addFilter(_TOKEN_REDACTING_FILTER)
    if logging.lastResort is not None:
        logging.lastResort.addFilter(_TOKEN_REDACTING_FILTER)
    sys.excepthook = _redacting_excepthook


def _resolve_log_level(level: Union[int, str, None]) -> int:
    """Resolve log level from explicit value or LOG_LEVEL/DEBUG env."""
    def _resolve_name(name: str) -> int:
        resolved = getattr(logging, name, None)
        return resolved if isinstance(resolved, int) else logging.INFO

    if level is not None:
        if isinstance(level, str):
            return _resolve_name(level.strip().upper())
        return level

    env_level = get_configured_log_level_name()
    if env_level:
        return _resolve_name(env_level.strip().upper())

    if is_debug_mode_enabled():
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
    _install_log_redaction(handlers)
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

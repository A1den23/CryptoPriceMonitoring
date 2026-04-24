"""Small shared runtime helpers for bot and monitor lifecycle."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import signal

from .logging import logger
from .utils import now_in_configured_timezone


class HeartbeatWriter:
    """Write a heartbeat file with optional minimum interval throttling."""

    def __init__(
        self,
        path: str | Path,
        *,
        min_interval_seconds: float = 0.0,
        label: str = "heartbeat",
        clock: Callable[[], datetime] = now_in_configured_timezone,
    ) -> None:
        self.path = Path(path)
        self.min_interval_seconds = min_interval_seconds
        self.label = label
        self.clock = clock
        self.last_touch = None

    def touch(self) -> None:
        """Touch the heartbeat file if the throttle allows it."""
        now = self.clock()
        if self.last_touch is not None and self.min_interval_seconds > 0:
            elapsed_seconds = (now - self.last_touch).total_seconds()
            if elapsed_seconds < self.min_interval_seconds:
                return

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch()
            self.last_touch = now
        except OSError as exc:
            logger.warning(f"更新 {self.label} 心跳文件失败 '{self.path}': {exc}")


class BackgroundTaskSet:
    """Track and flush a set of background asyncio tasks."""

    def __init__(self, *, cleanup_error_message: str | None = "清理后台任务失败: {exc}") -> None:
        self.tasks: set[asyncio.Task] = set()
        self.cleanup_error_message = cleanup_error_message

    def track(self, task: asyncio.Task) -> asyncio.Task:
        """Track *task* until completion and return it for callback chaining."""
        self.tasks.add(task)
        task.add_done_callback(self.discard)
        return task

    def discard(self, task: asyncio.Task) -> None:
        """Remove a completed task from the tracked set."""
        self.tasks.discard(task)

    async def flush(self, *, cancel: bool = False) -> None:
        """Await tracked tasks, optionally cancelling pending tasks first."""
        while self.tasks:
            pending_tasks = list(self.tasks)
            if cancel:
                for task in pending_tasks:
                    if not task.done():
                        task.cancel()

            for task in pending_tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    if self.cleanup_error_message is not None:
                        logger.error(self.cleanup_error_message.format(exc=exc))


class SignalHandlerRegistry:
    """Register and restore SIGINT/SIGTERM handlers."""

    def __init__(self) -> None:
        self._original_sigint = None
        self._original_sigterm = None
        self._registered = False

    def setup(self, handler) -> None:
        """Register *handler* for SIGINT and SIGTERM, saving originals."""
        if self._registered:
            return
        self._original_sigint = signal.signal(signal.SIGINT, handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, handler)
        self._registered = True

    def get_original(self, signum: int):
        """Return the original handler saved for *signum*."""
        if signum == signal.SIGINT:
            return self._original_sigint
        return self._original_sigterm

    @staticmethod
    def _restore_signal(signum: int, original_handler) -> None:
        try:
            signal.signal(signum, original_handler)
        except (ValueError, OSError):
            pass

    def restore(self) -> None:
        """Restore the original signal handlers that were saved by setup()."""
        if not self._registered:
            return
        if self._original_sigint is not None:
            self._restore_signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm is not None:
            self._restore_signal(signal.SIGTERM, self._original_sigterm)
        self._original_sigint = None
        self._original_sigterm = None
        self._registered = False


class SignalHandlingMixin:
    """Reusable signal-handling mixin for long-running async services.

    Subclasses need a ``_signal_registry: SignalHandlerRegistry`` attribute
    and a ``_shutdown_event: asyncio.Event`` attribute.
    """

    _signal_registry: SignalHandlerRegistry
    _signal_handlers_registered: bool = False

    def _setup_signal_handlers(self) -> None:
        """Register signal handlers that set the shutdown event."""
        if self._signal_handlers_registered:
            return
        self._signal_registry.setup(self._on_signal)
        self._signal_handlers_registered = True

    def _on_signal(self, signum: int, frame) -> None:
        """Handle SIGINT/SIGTERM by setting the shutdown event."""
        import signal as _signal

        from .logging import logger

        sig_name = _signal.Signals(signum).name
        logger.info(f"收到信号 {sig_name} ({signum})，开始优雅停机...")
        original = self._signal_registry.get_original(signum)
        if original is not None:
            SignalHandlerRegistry._restore_signal(signum, original)
        self._shutdown_event.set()

    def _restore_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        if not self._signal_handlers_registered:
            return
        self._signal_registry.restore()
        self._signal_handlers_registered = False

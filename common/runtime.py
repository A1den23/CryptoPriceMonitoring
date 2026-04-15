"""Small shared runtime helpers for bot and monitor lifecycle."""

import signal


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

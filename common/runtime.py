"""Small shared runtime helpers for bot and monitor lifecycle."""

import signal


class SignalHandlerRegistry:
    """Register and restore SIGINT/SIGTERM handlers."""

    def __init__(self) -> None:
        self._original_sigint = None
        self._original_sigterm = None
        self._registered = False

    def setup(self, handler) -> None:
        if self._registered:
            return
        self._original_sigint = signal.signal(signal.SIGINT, handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, handler)
        self._registered = True

    @staticmethod
    def _restore_signal(signum: int, original_handler) -> None:
        try:
            signal.signal(signum, original_handler)
        except (ValueError, OSError):
            pass

    def restore(self) -> None:
        if not self._registered:
            return
        if self._original_sigint is not None:
            self._restore_signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm is not None:
            self._restore_signal(signal.SIGTERM, self._original_sigterm)
        self._original_sigint = None
        self._original_sigterm = None
        self._registered = False

import importlib
import logging
import sys
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) in sys.path:
    sys.path.remove(str(WORKTREE_ROOT))
sys.path.insert(0, str(WORKTREE_ROOT))
sys.modules.pop("common.logging", None)
sys.modules.pop("common", None)

logging_utils = importlib.import_module("common.logging")


class SetupLoggingTests(unittest.TestCase):
    def _close_handlers(self, handlers: list[logging.Handler]) -> None:
        for handler in handlers:
            handler.close()

    def _snapshot_root_logger(self) -> tuple[list[logging.Handler], int]:
        root_logger = logging.getLogger()
        return list(root_logger.handlers), root_logger.level

    def _restore_root_logger(
        self, handlers: list[logging.Handler], level: int
    ) -> None:
        root_logger = logging.getLogger()
        current_handlers = list(root_logger.handlers)
        for handler in current_handlers:
            root_logger.removeHandler(handler)
            if handler not in handlers:
                handler.close()
        for handler in handlers:
            if handler not in root_logger.handlers:
                root_logger.addHandler(handler)
        root_logger.setLevel(level)

    def test_setup_logging_uses_rotating_file_handler(self) -> None:
        with TemporaryDirectory(dir=WORKTREE_ROOT) as temp_dir:
            log_file = Path(temp_dir) / "logs" / "monitor.log"

            with patch.object(logging_utils.Path, "cwd", return_value=WORKTREE_ROOT), patch.object(
                logging_utils.logging, "basicConfig"
            ) as basic_config:
                logging_utils.setup_logging(log_file=str(log_file))

            handlers = basic_config.call_args.kwargs["handlers"]
            self.addCleanup(self._close_handlers, handlers)

            file_handlers = [
                handler for handler in handlers if isinstance(handler, RotatingFileHandler)
            ]
            self.assertEqual(len(file_handlers), 1)
            self.assertEqual(file_handlers[0].maxBytes, 1_048_576)
            self.assertEqual(file_handlers[0].backupCount, 3)

            stream_handlers = [
                handler for handler in handlers if type(handler) is logging.StreamHandler
            ]
            self.assertEqual(len(stream_handlers), 1)

    def test_setup_logging_falls_back_to_console_when_rotating_handler_fails(self) -> None:
        with TemporaryDirectory(dir=WORKTREE_ROOT) as temp_dir:
            log_file = Path(temp_dir) / "logs" / "monitor.log"

            with patch.object(logging_utils.Path, "cwd", return_value=WORKTREE_ROOT), patch.object(
                logging_utils,
                "RotatingFileHandler",
                side_effect=PermissionError("denied"),
                create=True,
            ), patch.object(logging_utils.logging, "basicConfig") as basic_config, patch(
                "builtins.print"
            ) as mock_print:
                logging_utils.setup_logging(log_file=str(log_file))

            handlers = basic_config.call_args.kwargs["handlers"]
            self.addCleanup(self._close_handlers, handlers)

            self.assertEqual(len(handlers), 1)
            self.assertIsInstance(handlers[0], logging.StreamHandler)
            mock_print.assert_any_call(
                f"Warning: Could not create log file '{log_file}': denied"
            )
            mock_print.assert_any_call("Logging to console only.")

    def test_setup_logging_replaces_handlers_when_logging_was_preconfigured(self) -> None:
        original_handlers, original_level = self._snapshot_root_logger()
        self.addCleanup(self._restore_root_logger, original_handlers, original_level)

        root_logger = logging.getLogger()
        preconfigured_handler = logging.NullHandler()
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        root_logger.addHandler(preconfigured_handler)
        root_logger.setLevel(logging.WARNING)

        with TemporaryDirectory(dir=WORKTREE_ROOT) as temp_dir:
            log_file = Path(temp_dir) / "logs" / "monitor.log"

            with patch.object(logging_utils.Path, "cwd", return_value=WORKTREE_ROOT):
                logging_utils.setup_logging(log_file=str(log_file), level=logging.DEBUG)

            root_handlers = list(root_logger.handlers)
            self.addCleanup(self._close_handlers, [
                handler for handler in root_handlers if handler is not preconfigured_handler
            ])

            file_handlers = [
                handler
                for handler in root_handlers
                if isinstance(handler, RotatingFileHandler)
            ]
            stream_handlers = [
                handler
                for handler in root_handlers
                if type(handler) is logging.StreamHandler
            ]

            self.assertNotIn(preconfigured_handler, root_handlers)
            self.assertEqual(root_logger.level, logging.DEBUG)
            self.assertEqual(len(file_handlers), 1)
            self.assertEqual(len(stream_handlers), 1)


if __name__ == "__main__":
    unittest.main()

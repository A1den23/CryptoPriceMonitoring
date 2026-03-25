import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))


def _install_dependency_stubs() -> None:
    if "tenacity" not in sys.modules:
        tenacity = types.ModuleType("tenacity")

        def retry(*args, **kwargs):
            def decorator(func):
                return func
            return decorator

        tenacity.retry = retry
        tenacity.retry_if_exception_type = lambda *args, **kwargs: None
        tenacity.stop_after_attempt = lambda *args, **kwargs: None
        tenacity.wait_exponential = lambda *args, **kwargs: None
        sys.modules["tenacity"] = tenacity

    if "requests" not in sys.modules:
        requests = types.ModuleType("requests")

        class RequestException(Exception):
            pass

        class Session:
            def mount(self, *args, **kwargs) -> None:
                return None

            def post(self, *args, **kwargs):
                raise NotImplementedError

            def close(self) -> None:
                return None

        class HTTPAdapter:
            def __init__(self, *args, **kwargs) -> None:
                pass

        requests.Session = Session
        requests.exceptions = types.SimpleNamespace(RequestException=RequestException)
        requests.adapters = types.SimpleNamespace(HTTPAdapter=HTTPAdapter)
        sys.modules["requests"] = requests


_install_dependency_stubs()

from common.notifications import TelegramNotifier


class TelegramNotifierTests(unittest.TestCase):
    def test_send_message_returns_false_when_telegram_json_ok_is_false(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": False, "description": "Bad Request: chat not found"}

        with patch.object(notifier.session, "post", return_value=response):
            self.assertFalse(notifier.send_message("hello"))

    def test_send_message_returns_true_when_telegram_json_ok_is_true(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "result": {"message_id": 1}}

        with patch.object(notifier.session, "post", return_value=response):
            self.assertTrue(notifier.send_message("hello"))

    def test_send_message_returns_false_when_telegram_json_is_malformed(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("malformed json")

        with patch.object(notifier.session, "post", return_value=response):
            self.assertFalse(notifier.send_message("hello"))

    def test_send_message_returns_false_when_telegram_json_missing_ok(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": {"message_id": 1}}

        with patch.object(notifier.session, "post", return_value=response):
            self.assertFalse(notifier.send_message("hello"))

    def test_test_connection_logs_generic_failure_without_exception_text(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")
        error = RuntimeError("https://api.telegram.org/bottoken/sendMessage failed")

        with patch.object(notifier, "send_message", side_effect=error):
            with self.assertLogs("common.logging", level=logging.ERROR) as log_context:
                self.assertFalse(notifier.test_connection())

        self.assertEqual(log_context.output, ["ERROR:common.logging:Telegram connection test failed"])


if __name__ == "__main__":
    unittest.main()

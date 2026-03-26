import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.stubs import install_dependency_stubs


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))


install_dependency_stubs()

from common.notifications import TelegramNotifier


class TelegramNotifierTests(unittest.TestCase):
    def test_close_closes_underlying_http_session(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")

        with patch.object(notifier.session, "close") as mock_close:
            notifier.close()

        mock_close.assert_called_once_with()

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

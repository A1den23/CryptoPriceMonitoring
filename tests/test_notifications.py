import logging
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from tests.stubs import install_dependency_stubs


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))


install_dependency_stubs()

import requests

from common.notifications import TelegramNotifier


def build_http_error(status_code: int, payload=None):
    response = MagicMock()
    response.__bool__.return_value = False
    response.status_code = status_code
    if payload is None:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = payload
    error = requests.exceptions.RequestException(
        f"{status_code} Error for url: https://api.telegram.org/botsecret-token/sendMessage"
    )
    error.response = response
    response.raise_for_status.side_effect = error
    return response, error


def build_ok_response():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True}
    return response


class TelegramNotifierRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.notifier = TelegramNotifier(bot_token="secret-token", chat_id="chat")
        sleep_patcher = patch("common.notifications.time.sleep")
        self.mock_sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def _post(self, responses):
        return patch.object(self.notifier.session, "post", side_effect=responses)

    def test_permanent_4xx_raises_without_retry_or_token_in_logs(self) -> None:
        for status_code in (400, 401, 403, 404):
            with self.subTest(status_code=status_code):
                self.mock_sleep.reset_mock()
                response, error = build_http_error(
                    status_code,
                    {"ok": False, "description": "Bad Request: chat not found"},
                )
                with self._post([response, build_ok_response()]) as mock_post, \
                     self.assertLogs("common.logging", level=logging.ERROR) as log_context:
                    with self.assertRaises(type(error)):
                        self.notifier.send_message("hello")

                self.assertEqual(mock_post.call_count, 1)
                self.mock_sleep.assert_not_called()
                self.assertIn(f"HTTP {status_code}", log_context.output[0])
                self.assertIn("chat not found", log_context.output[0])
                self.assertNotIn("secret-token", "\n".join(log_context.output))
                self.assertEqual(len(self.notifier._message_times), 0)

    def test_429_waits_for_retry_after_then_succeeds(self) -> None:
        response, _error = build_http_error(
            429,
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 7}},
        )
        with self._post([response, build_ok_response()]) as mock_post:
            self.assertTrue(self.notifier.send_message("hello"))

        self.assertEqual(mock_post.call_count, 2)
        self.mock_sleep.assert_called_once_with(7)

    def test_429_retry_after_is_capped(self) -> None:
        response, _error = build_http_error(429, {"parameters": {"retry_after": 3600}})
        with self._post([response, build_ok_response()]):
            self.assertTrue(self.notifier.send_message("hello"))

        self.mock_sleep.assert_called_once_with(60)

    def test_429_without_usable_retry_after_falls_back_to_backoff(self) -> None:
        payloads = (
            None,
            {"ok": False},
            {"parameters": "soon"},
            {"parameters": {"retry_after": "7"}},
            {"parameters": {"retry_after": True}},
            {"parameters": {"retry_after": 0}},
            {"parameters": {"retry_after": -1}},
            {"parameters": {"retry_after": float("nan")}},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self.mock_sleep.reset_mock()
                response, _error = build_http_error(429, payload)
                with self._post([response, build_ok_response()]):
                    self.assertTrue(self.notifier.send_message("hello"))

                self.mock_sleep.assert_called_once_with(2)

    def test_429_raises_after_attempts_are_exhausted(self) -> None:
        response, error = build_http_error(429, {"parameters": {"retry_after": 5}})
        with self._post([response, response, response]) as mock_post:
            with self.assertRaises(type(error)):
                self.notifier.send_message("hello")

        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual([call.args[0] for call in self.mock_sleep.call_args_list], [5, 5])
        self.assertEqual(len(self.notifier._message_times), 0)

    def test_5xx_retries_with_backoff_then_raises_original_error(self) -> None:
        response, error = build_http_error(502)
        with self._post([response, response, response]) as mock_post, \
             self.assertLogs("common.logging", level=logging.WARNING) as log_context:
            with self.assertRaises(type(error)) as raised:
                self.notifier.send_message("hello")

        self.assertIs(raised.exception, error)
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual([call.args[0] for call in self.mock_sleep.call_args_list], [2, 2])
        self.assertNotIn("secret-token", "\n".join(log_context.output))

    def test_network_error_retries_then_succeeds(self) -> None:
        network_error = requests.exceptions.RequestException(
            "https://api.telegram.org/botsecret-token/sendMessage unreachable"
        )
        with self._post([network_error, network_error, build_ok_response()]) as mock_post, \
             self.assertLogs("common.logging", level=logging.WARNING) as log_context:
            self.assertTrue(self.notifier.send_message("hello"))

        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual([call.args[0] for call in self.mock_sleep.call_args_list], [2, 2])
        self.assertNotIn("secret-token", "\n".join(log_context.output))

    def test_backoff_grows_exponentially_up_to_the_maximum(self) -> None:
        self.notifier.MAX_SEND_ATTEMPTS = 6
        response, _error = build_http_error(503)
        with self._post([response] * 6):
            with self.assertRaises(requests.exceptions.RequestException):
                self.notifier.send_message("hello")

        self.assertEqual(
            [call.args[0] for call in self.mock_sleep.call_args_list],
            [2, 2, 4, 8, 10],
        )

    def test_unexpected_exception_is_not_retried(self) -> None:
        with self._post([RuntimeError("boom")]) as mock_post:
            with self.assertRaises(RuntimeError):
                self.notifier.send_message("hello")

        self.assertEqual(mock_post.call_count, 1)
        self.mock_sleep.assert_not_called()

    def test_ok_false_on_success_status_returns_false_without_retry(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": False, "description": "rejected"}
        with self._post([response]) as mock_post:
            self.assertFalse(self.notifier.send_message("hello"))

        self.assertEqual(mock_post.call_count, 1)
        self.mock_sleep.assert_not_called()


class TelegramNotifierTests(unittest.TestCase):
    def test_rate_limited_send_waits_instead_of_dropping_alert(self) -> None:
        notifier = TelegramNotifier(bot_token="token", chat_id="chat")
        notifier._rate_limit = 1
        notifier._message_times.append(100.0)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True}
        completed = threading.Event()

        def fake_time() -> float:
            return 100.0 if not completed.is_set() else 161.0

        def release_wait(*_args, **_kwargs) -> None:
            completed.set()

        with patch("common.notifications.time.time", side_effect=fake_time), \
             patch.object(notifier._rate_limit_condition, "wait", side_effect=release_wait), \
             patch.object(notifier.session, "post", return_value=response):
            self.assertTrue(notifier.send_message("hello"))

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

        self.assertEqual(log_context.output, ["ERROR:common.logging:Telegram 连接测试失败"])


if __name__ == "__main__":
    unittest.main()

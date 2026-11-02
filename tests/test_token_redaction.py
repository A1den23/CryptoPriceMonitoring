import asyncio
import io
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from tests.stubs import install_dependency_stubs


WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))


install_dependency_stubs()

import requests

import bot
import monitor
from bot.handlers import send_price_update, stablecoins_command
from common.config import CoinConfig
from common.logging import redact_secrets, safe_exception_text, setup_logging
from common.notifications import TelegramNotifier
from common.runtime import BackgroundTaskSet
from monitor.price_monitor import PriceMonitor
from monitor.ws_monitor import WebSocketMultiCoinMonitor


FAKE_TOKEN = "123456:FAKE_test-token"
TOKEN_FRAGMENTS = (FAKE_TOKEN, "FAKE_test-token")
TOKEN_URL = f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"


def build_token_error():
    error_class = getattr(
        requests.exceptions,
        "ConnectionError",
        requests.exceptions.RequestException,
    )
    return error_class(f"Max retries exceeded with url: {TOKEN_URL}")


def failed_task_stub(error):
    return types.SimpleNamespace(cancelled=lambda: False, exception=lambda: error)


class RedactSecretsTests(unittest.TestCase):
    def test_token_pattern_is_redacted_from_url(self) -> None:
        redacted = redact_secrets(f"404 Client Error for url: {TOKEN_URL}")

        self.assertEqual(
            redacted,
            "404 Client Error for url: https://api.telegram.org/bot<redacted>/sendMessage",
        )

    def test_percent_encoded_token_is_redacted(self) -> None:
        redacted = redact_secrets("/bot123456%3AFAKE_test-token/sendMessage")

        self.assertEqual(redacted, "/bot<redacted>/sendMessage")

    def test_configured_token_is_redacted_without_bot_prefix(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "custom.fake~value"}):
            redacted = redact_secrets("token=custom.fake~value url=/botcustom.fake~value/getMe")

        self.assertEqual(redacted, "token=<redacted> url=/bot<redacted>/getMe")

    def test_text_without_token_is_unchanged(self) -> None:
        self.assertEqual(redact_secrets("503 Server Error"), "503 Server Error")

    def test_safe_exception_text_keeps_type_and_status_detail(self) -> None:
        error = requests.exceptions.RequestException(f"404 Client Error for url: {TOKEN_URL}")

        text = safe_exception_text(error)

        self.assertEqual(
            text,
            f"{type(error).__name__}: 404 Client Error for url: "
            "https://api.telegram.org/bot<redacted>/sendMessage",
        )

    def test_safe_exception_text_falls_back_to_type_for_empty_message(self) -> None:
        self.assertEqual(safe_exception_text(ValueError()), "ValueError")


class RedactionAssertions:
    def assert_redacted(self, log_context, expected_message: str) -> None:
        records = log_context.records
        self.assertTrue(records)
        formatter = logging.Formatter("%(message)s")
        combined = "\n".join(formatter.format(record) for record in records)
        for fragment in TOKEN_FRAGMENTS:
            self.assertNotIn(fragment, combined)
        self.assertIn(expected_message, combined)
        self.assertIn("bot<redacted>", combined)
        self.assertIn(type(build_token_error()).__name__, combined)
        for record in records:
            self.assertIsNone(record.exc_info)

    @staticmethod
    def _coin_config() -> CoinConfig:
        return CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1_000_000.0,
            volatility_percent=5.0,
            volatility_window=180,
            volume_alert_multiplier=10.0,
        )


class RedactedLogSiteTests(RedactionAssertions, unittest.IsolatedAsyncioTestCase):
    def _ws_monitor(self) -> WebSocketMultiCoinMonitor:
        coin = self._coin_config()
        config = types.SimpleNamespace(
            telegram_bot_token="unused",
            telegram_chat_id="chat",
            monitor_heartbeat_file="/tmp/monitor-heartbeat-test",
            get_enabled_coins=lambda: [coin],
            volume_alert_cooldown_seconds=60,
            volatility_alert_cooldown_seconds=60,
            milestone_alert_cooldown_seconds=600,
            stablecoin_depeg_monitor_enabled=False,
            stablecoin_universe_auto_refresh_enabled=False,
        )
        with patch("monitor.ws_monitor.TelegramNotifier"):
            return WebSocketMultiCoinMonitor(config)

    async def test_shutdown_notification_through_real_notifier_does_not_log_token(self) -> None:
        ws_monitor = self._ws_monitor()
        ws_monitor.notifier = TelegramNotifier(bot_token=FAKE_TOKEN, chat_id="chat")

        with patch.object(ws_monitor.notifier.session, "post", side_effect=build_token_error()), \
             patch("common.notifications.time.sleep"), \
             self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            await ws_monitor._send_shutdown_notification()

        self.assert_redacted(log_context, "发送停机通知失败")

    async def test_keyboard_interrupt_notification_failure_is_redacted_and_contained(self) -> None:
        ws_monitor = self._ws_monitor()
        ws_monitor.notifier = Mock()
        ws_monitor.notifier.send_message.side_effect = build_token_error()

        with patch.object(ws_monitor, "_setup_signal_handlers", side_effect=KeyboardInterrupt), \
             self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            await ws_monitor.run()

        self.assert_redacted(log_context, "发送停机通知失败")

    async def test_disconnect_and_reconnect_callbacks_do_not_log_token(self) -> None:
        ws_monitor = self._ws_monitor()
        task = failed_task_stub(build_token_error())

        with self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            ws_monitor._on_disconnect_done(task)
        self.assert_redacted(log_context, "断开告警投递失败")

        with self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            ws_monitor._on_reconnect_done(task)
        self.assert_redacted(log_context, "重连告警投递失败")

    async def test_background_task_flush_does_not_log_token(self) -> None:
        task_set = BackgroundTaskSet(cleanup_error_message="清理时通知任务失败: {exc}")

        async def fail() -> None:
            raise build_token_error()

        task_set.track(asyncio.create_task(fail()))
        with self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            await task_set.flush()

        self.assert_redacted(log_context, "清理时通知任务失败")

    async def test_price_monitor_notification_failure_has_no_token_or_traceback(self) -> None:
        notifier = Mock()
        notifier.send_message.side_effect = build_token_error()
        price_monitor = PriceMonitor(self._coin_config(), notifier)

        with self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            price_monitor._send_notification("hello")
            await price_monitor.flush_notification_tasks()
            await asyncio.sleep(0)

        self.assert_redacted(log_context, "Telegram 通知发送失败")

    async def test_stablecoin_command_failure_does_not_log_token(self) -> None:
        handler = types.SimpleNamespace(
            _defillama_client=object(),
            config=types.SimpleNamespace(stablecoin_universe_cache_path="unused"),
            _send_or_edit_message=AsyncMock(),
        )
        update = types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=1))

        with patch(
            "bot.handlers.resolve_live_snapshots_for_cached_universe",
            new=AsyncMock(side_effect=build_token_error()),
        ), self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            await stablecoins_command(handler, update, None)

        self.assert_redacted(log_context, "获取稳定币价格失败")

    async def test_price_update_failure_does_not_log_token(self) -> None:
        coin = self._coin_config()
        handler = types.SimpleNamespace(
            config=types.SimpleNamespace(get_coin_config=lambda coin_name: coin),
            _get_price=AsyncMock(side_effect=build_token_error()),
            _send_or_edit_message=AsyncMock(),
        )

        with self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            await send_price_update(handler, 1, "BTC")

        self.assert_redacted(log_context, "发送 BTC 价格更新失败")


class RedactedEntrypointTests(RedactionAssertions, unittest.TestCase):
    def test_bot_main_notification_failures_have_no_token_or_traceback(self) -> None:
        notifier = Mock()
        notifier.send_message.side_effect = build_token_error()

        class DummyBot:
            def __init__(self, config) -> None:
                self._shutdown_event = types.SimpleNamespace(is_set=lambda: True)

            def run(self) -> None:
                return None

        config = types.SimpleNamespace(telegram_bot_token=FAKE_TOKEN, telegram_chat_id="chat")
        with patch.object(bot, "load_environment"), \
             patch.object(bot, "setup_logging"), \
             patch.object(bot, "ConfigManager", return_value=config), \
             patch.object(bot, "TelegramNotifier", return_value=notifier), \
             patch.object(bot, "TelegramBot", DummyBot), \
             self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            bot.main()

        self.assert_redacted(log_context, "启动通知发送失败")
        self.assert_redacted(log_context, "停机通知发送失败")

    def test_monitor_test_alert_failure_does_not_log_token(self) -> None:
        notifier = Mock()
        notifier.send_message.side_effect = build_token_error()
        coin = self._coin_config()
        config = types.SimpleNamespace(
            telegram_bot_token=FAKE_TOKEN,
            telegram_chat_id="chat",
            get_enabled_coins=lambda: [coin],
        )
        fetcher = MagicMock()
        fetcher.__enter__.return_value.get_current_price.return_value = 100.0

        with patch.object(monitor, "ConfigManager", return_value=config), \
             patch.object(monitor, "TelegramNotifier", return_value=notifier), \
             patch.object(monitor, "BinancePriceFetcher", return_value=fetcher, create=True), \
             patch("builtins.print"), \
             self.assertLogs("common.logging", level=logging.ERROR) as log_context:
            monitor.test_volatility_alert()

        self.assert_redacted(log_context, "测试 BTC 时出错")


class BrokenStream:
    def write(self, _text: str) -> int:
        raise OSError("disk unavailable")

    def flush(self) -> None:
        pass


class SetupLoggingRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name).resolve()
        self.log_path = temp_path / "logs" / "test.log"
        self.stderr = io.StringIO()

        root = logging.getLogger()
        self.addCleanup(self._restore_root, list(root.handlers), root.level)
        httpx_logger = logging.getLogger("httpx")
        self.addCleanup(httpx_logger.setLevel, httpx_logger.level)

        for patcher in (
            patch.object(sys, "stderr", self.stderr),
            patch.object(sys, "excepthook", sys.excepthook),
            patch.object(logging.lastResort, "filters", []),
            patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": FAKE_TOKEN}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        with patch.object(Path, "cwd", return_value=temp_path):
            setup_logging(log_file=str(self.log_path), level=logging.INFO)

    @staticmethod
    def _restore_root(handlers: list[logging.Handler], level: int) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in handlers:
            root.addHandler(handler)
        root.setLevel(level)

    def outputs(self) -> dict[str, str]:
        for handler in logging.getLogger().handlers:
            handler.flush()
        return {
            "console": self.stderr.getvalue(),
            "file": self.log_path.read_text(encoding="utf-8"),
        }

    def assert_redacted_everywhere(self, *expected: str) -> None:
        for name, text in self.outputs().items():
            with self.subTest(output=name):
                for fragment in TOKEN_FRAGMENTS:
                    self.assertNotIn(fragment, text)
                for detail in expected:
                    self.assertIn(detail, text)

    def test_every_configured_handler_is_covered(self) -> None:
        handlers = logging.getLogger().handlers

        self.assertEqual(len(handlers), 2)
        for handler in handlers:
            self.assertTrue(handler.filters)

    def test_httpx_info_url_is_redacted_and_detail_preserved(self) -> None:
        httpx_logger = logging.getLogger("httpx")
        httpx_logger.setLevel(logging.INFO)

        httpx_logger.info(f'HTTP Request: POST {TOKEN_URL} "HTTP/1.1 404 Not Found"')

        self.assert_redacted_everywhere(
            "httpx - INFO",
            'HTTP Request: POST https://api.telegram.org/bot<redacted>/sendMessage "HTTP/1.1 404 Not Found"',
        )

    def test_other_library_loggers_are_redacted(self) -> None:
        names = ("telegram.ext.Updater", "httpcore", "urllib3.connectionpool", "requests", "aiohttp.client")

        for name in names:
            logging.getLogger(name).warning("POST %s 502 from %s", TOKEN_URL, name)

        self.assert_redacted_everywhere(
            *(f"POST https://api.telegram.org/bot<redacted>/sendMessage 502 from {name}" for name in names)
        )

    def test_args_based_formatting_is_redacted(self) -> None:
        logging.getLogger("telegram").info("%s returned %d", TOKEN_URL, 404)

        self.assert_redacted_everywhere("bot<redacted>/sendMessage returned 404")

    def test_percent_signs_left_after_rendering_do_not_break_later_handlers(self) -> None:
        logging.getLogger("telegram").info("%s at 100%%", f"{TOKEN_URL}?q=%20")

        self.assert_redacted_everywhere("bot<redacted>/sendMessage?q=%20 at 100%")

    def test_logger_exception_redacts_traceback_without_changing_exception(self) -> None:
        error = build_token_error()
        original_args = error.args

        with self.assertRaises(type(error)) as raised:
            try:
                raise error
            except type(error):
                logging.getLogger("requests").exception("sendMessage failed with status %d", 503)
                raise

        self.assertIs(raised.exception, error)
        self.assertEqual(error.args, original_args)
        self.assertIn(TOKEN_URL, str(error))
        self.assert_redacted_everywhere(
            "sendMessage failed with status 503",
            "Traceback (most recent call last)",
            type(error).__name__,
            "Max retries exceeded with url: https://api.telegram.org/bot<redacted>/sendMessage",
        )

    def test_chained_exception_cause_is_redacted(self) -> None:
        error = RuntimeError("delivery failed")
        error.__cause__ = build_token_error()

        logging.getLogger("telegram").error("send failed", exc_info=error)

        self.assert_redacted_everywhere("delivery failed", "bot<redacted>/sendMessage")

    def test_precomputed_exception_and_stack_text_are_redacted(self) -> None:
        record = logging.makeLogRecord(
            {
                "name": "telegram",
                "levelno": logging.ERROR,
                "levelname": "ERROR",
                "msg": "prepared record",
                "exc_text": f"Cached traceback {TOKEN_URL}",
                "stack_info": f"Stack (most recent call last) {TOKEN_URL}",
            }
        )

        logging.getLogger("telegram").handle(record)

        self.assert_redacted_everywhere(
            "prepared record",
            "Cached traceback https://api.telegram.org/bot<redacted>/sendMessage",
            "Stack (most recent call last) https://api.telegram.org/bot<redacted>/sendMessage",
        )

    def test_stack_info_is_kept(self) -> None:
        logging.getLogger("telegram").warning("%s", TOKEN_URL, stack_info=True)

        self.assert_redacted_everywhere("bot<redacted>/sendMessage", "Stack (most recent call last)")

    def test_configured_token_without_standard_shape_is_redacted(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "custom.fake~value"}):
            logging.getLogger("httpcore").warning("token=%s status=401", "custom.fake~value")

        self.assertNotIn("custom.fake~value", "".join(self.outputs().values()))
        self.assert_redacted_everywhere("token=<redacted> status=401")

    def test_malformed_format_args_are_kept_and_redacted(self) -> None:
        logging.getLogger("telegram").info("status %d", TOKEN_URL)

        self.assert_redacted_everywhere(
            "Unformattable log record",
            "status %d",
            "bot<redacted>/sendMessage",
        )
        self.assertNotIn("--- Logging error ---", self.stderr.getvalue())

    def test_unprintable_message_does_not_raise_into_caller(self) -> None:
        class Unprintable:
            def __str__(self) -> str:
                raise ValueError("no text")

            __repr__ = __str__

        logging.getLogger("telegram").info(Unprintable())

        self.assert_redacted_everywhere("msg type Unprintable")

    def test_handler_error_report_does_not_print_raw_record(self) -> None:
        console = next(
            handler for handler in logging.getLogger().handlers
            if type(handler) is logging.StreamHandler
        )
        console.setStream(BrokenStream())

        logging.getLogger("telegram").info("POST %s returned %d", TOKEN_URL, 502)

        report = self.stderr.getvalue()
        self.assertIn("--- Logging error ---", report)
        self.assertIn("bot<redacted>/sendMessage returned 502", report)
        for fragment in TOKEN_FRAGMENTS:
            self.assertNotIn(fragment, report)

    def test_last_resort_handler_is_redacted(self) -> None:
        isolated = logging.getLogger("tests.redaction.isolated")
        isolated.propagate = False
        self.addCleanup(setattr, isolated, "propagate", True)

        isolated.warning("POST %s returned %d", TOKEN_URL, 502)

        self.assertIn("bot<redacted>/sendMessage returned 502", self.stderr.getvalue())
        for fragment in TOKEN_FRAGMENTS:
            self.assertNotIn(fragment, self.stderr.getvalue())

    def test_uncaught_exception_hook_redacts_traceback(self) -> None:
        error = build_token_error()

        try:
            raise error
        except type(error):
            sys.excepthook(*sys.exc_info())

        report = self.stderr.getvalue()
        self.assertIn("Traceback (most recent call last)", report)
        self.assertIn(type(error).__name__, report)
        self.assertIn("bot<redacted>/sendMessage", report)
        for fragment in TOKEN_FRAGMENTS:
            self.assertNotIn(fragment, report)
        self.assertIn(TOKEN_URL, str(error))


if __name__ == "__main__":
    unittest.main()

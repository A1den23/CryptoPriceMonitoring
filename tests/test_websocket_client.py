import asyncio
import importlib
import importlib.util
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from tests.stubs import install_dependency_stubs


install_dependency_stubs()

import monitor
from common.clients.websocket import ConnectionState


class FakeAsyncIterableWebSocket:
    def __init__(self, messages: list[str]) -> None:
        self._messages = iter(messages)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._messages)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class WebSocketParserRegressionTests(unittest.TestCase):
    def test_websocket_parser_module_exposes_message_parsers(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("common.clients.websocket_parser"))

        parser = importlib.import_module("common.clients.websocket_parser")
        self.assertTrue(callable(getattr(parser, "parse_ticker_message", None)))
        self.assertTrue(callable(getattr(parser, "parse_kline_message", None)))

    def test_parse_ticker_message_preserves_combined_stream_shape(self) -> None:
        parser = importlib.import_module("common.clients.websocket_parser")
        symbol, price = parser.parse_ticker_message(
            {
                "stream": "btcusdt@ticker",
                "data": {"e": "24hrTicker", "s": "BTCUSDT", "c": "95123.45"},
            }
        )

        self.assertEqual(symbol, "BTCUSDT")
        self.assertEqual(price, 95123.45)

    def test_parse_kline_message_preserves_closed_kline_shape(self) -> None:
        parser = importlib.import_module("common.clients.websocket_parser")
        kline_data = parser.parse_kline_message(
            {
                "stream": "btcusdt@kline_1m",
                "data": {
                    "e": "kline",
                    "s": "BTCUSDT",
                    "k": {"s": "BTCUSDT", "c": "1.23", "v": "4.56", "x": True},
                },
            }
        )

        self.assertEqual(kline_data, ("BTCUSDT", 1.23, 4.56, True))

    def test_parse_kline_message_returns_none_for_non_kline_events(self) -> None:
        parser = importlib.import_module("common.clients.websocket_parser")
        kline_data = parser.parse_kline_message(
            {"stream": "btcusdt@ticker", "data": {"e": "24hrTicker", "s": "BTCUSDT", "c": "95123.45"}}
        )

        self.assertIsNone(kline_data)


class BinanceWebSocketClientRegressionTests(unittest.TestCase):
    def test_message_handler_transitions_to_reconnecting_when_stream_ends_cleanly(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        client = monitor.BinanceWebSocketClient(["BTCUSDT"], on_price)
        client.state = ConnectionState.CONNECTED
        client.websocket = FakeAsyncIterableWebSocket([])

        asyncio.run(client._message_handler())

        self.assertEqual(client.state, ConnectionState.RECONNECTING)

    def test_message_handler_keeps_stopped_state_when_stop_event_is_set_before_clean_end(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        client = monitor.BinanceWebSocketClient(["BTCUSDT"], on_price)
        client.state = ConnectionState.STOPPED
        client._stop_event.set()
        client.websocket = FakeAsyncIterableWebSocket([])

        asyncio.run(client._message_handler())

        self.assertEqual(client.state, ConnectionState.STOPPED)

    def test_message_handler_drops_closed_kline_with_missing_symbol(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        on_kline = AsyncMock()

        client = monitor.BinanceWebSocketClient(
            ["BTCUSDT"],
            on_price,
            on_kline_callback=on_kline,
        )
        client.websocket = FakeAsyncIterableWebSocket(
            ['{"e": "kline", "k": {"c": "1.23", "v": "4.56", "x": true}}']
        )

        with patch("common.clients.websocket.logger.error") as mock_error:
            asyncio.run(client._message_handler())

        on_kline.assert_not_awaited()
        mock_error.assert_any_call("解析 kline 消息失败: Kline message missing valid symbol")

    def test_message_handler_logs_subscription_confirmation_with_kline_callback(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        async def on_kline(symbol: str, price: float, volume: float, is_closed: bool) -> None:
            return None

        client = monitor.BinanceWebSocketClient(
            ["BTCUSDT"],
            on_price,
            on_kline_callback=on_kline,
        )
        client.websocket = FakeAsyncIterableWebSocket(['{"result": null, "id": 1}'])

        with patch("common.clients.websocket.logger.info") as mock_info:
            asyncio.run(client._message_handler())

        mock_info.assert_any_call("订阅确认: {'result': None, 'id': 1}")

    def test_message_handler_logs_error_message_with_kline_callback(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        async def on_kline(symbol: str, price: float, volume: float, is_closed: bool) -> None:
            return None

        client = monitor.BinanceWebSocketClient(
            ["BTCUSDT"],
            on_price,
            on_kline_callback=on_kline,
        )
        client.websocket = FakeAsyncIterableWebSocket(['{"code": 400, "msg": "bad request"}'])

        with patch("common.clients.websocket.logger.error") as mock_error:
            asyncio.run(client._message_handler())

        mock_error.assert_any_call("Binance 错误: {'code': 400, 'msg': 'bad request'}")

    def test_bad_ticker_payload_is_logged_and_skipped_without_reconnect(self) -> None:
        received = []

        async def on_price(symbol: str, price: float) -> None:
            received.append((symbol, price))

        client = monitor.BinanceWebSocketClient(["BTCUSDT"], on_price)
        client.state = ConnectionState.CONNECTED
        client.websocket = FakeAsyncIterableWebSocket([
            '{"stream":"btcusdt@ticker","data":{"e":"24hrTicker","s":"BTCUSDT"}}',
            '{"stream":"btcusdt@ticker","data":{"e":"24hrTicker","s":"BTCUSDT","c":"95123.45"}}',
        ])
        disconnects = []

        async def on_disconnect(reason: str) -> None:
            disconnects.append(reason)

        client.on_disconnect_callback = on_disconnect

        with patch("common.clients.websocket.logger.error") as mock_error:
            asyncio.run(client._message_handler())

        self.assertEqual(received, [("BTCUSDT", 95123.45)])
        self.assertEqual(disconnects, ["Connection closed cleanly"])
        self.assertEqual(client.state, ConnectionState.RECONNECTING)
        mock_error.assert_any_call("解析 ticker 消息失败: 'c'")

    def test_connection_watchdog_wakes_start_loop_after_stale_messages(self) -> None:
        async def on_price(symbol: str, price: float) -> None:
            return None

        class ClosableWebSocket:
            closed = False

            async def close(self) -> None:
                self.closed = True

        async def exercise() -> tuple[ConnectionState, bool, bool]:
            client = monitor.BinanceWebSocketClient(["BTCUSDT"], on_price)
            client._set_state(ConnectionState.CONNECTED)
            client.last_message_time = client._now() - timedelta(seconds=10)
            client.message_timeout = 1.0
            client.websocket = ClosableWebSocket()

            with patch("common.clients.websocket.asyncio.sleep", new=AsyncMock()):
                await client._connection_watchdog()

            return client.state, client._disconnected_event.is_set(), client.websocket.closed

        state, disconnected_event_set, websocket_closed = asyncio.run(exercise())

        self.assertEqual(state, ConnectionState.RECONNECTING)
        self.assertTrue(disconnected_event_set)
        self.assertTrue(websocket_closed)


if __name__ == "__main__":
    unittest.main()

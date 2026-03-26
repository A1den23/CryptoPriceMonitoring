# Code Quality Remediation Round 2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 收敛 Bot / Monitor / Notification 的关键质量问题，优先修复 HTML 渲染边界、Bot 生命周期副作用、按钮交互语义、WebSocket 容错与采样耦合问题，并补齐相应回归测试。

**Architecture:** 保持现有 `common/`、`monitor/`、`bot/` 分层不变，只做边界收敛，不做大规模重构。先用 `unittest` 在现有测试文件中补最小失败用例，再做最小实现；第一阶段修 Bot 与通知边界，第二阶段修 WebSocket 与 PriceMonitor 语义，第三阶段再清理死代码、依赖和工程治理项。所有实现按 `@superpowers:test-driven-development` 执行，完成前用 `@superpowers:verification-before-completion` 做局部与全量验证。

**Tech Stack:** Python 3.11、unittest、asyncio、requests、aiohttp、websockets、python-telegram-bot、Docker Compose

---

### Task 1: 统一 Bot 消息渲染的 HTML 安全边界

**Files:**
- Modify: `bot/messages.py:50-198`
- Test: `tests/test_bot_messages.py`
- Test: `tests/test_bot_handlers.py`

**Step 1: Write the failing rendering test**

在 `tests/test_bot_messages.py` 的 `PriceMessageRenderingTests` 中新增一条测试，验证动态字段中的 HTML 特殊字符会被安全转义：

```python
def test_render_price_detail_message_escapes_html_sensitive_fields(self) -> None:
    coin_config = CoinConfig(
        coin_name="BTC<1>",
        enabled=True,
        symbol="BTC&USDT",
        integer_threshold=1000.0,
        volatility_percent=3.0,
        volatility_window=60,
        volume_alert_multiplier=10.0,
    )

    message = render_price_detail_message(
        coin_config=coin_config,
        price=95123.456,
        timestamp="2026-03-25 10:30:45",
    )

    self.assertIn("BTC&lt;1&gt;", message)
    self.assertIn("BTC&amp;USDT", message)
    self.assertNotIn("<1>", message)
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_bot_messages.PriceMessageRenderingTests.test_render_price_detail_message_escapes_html_sensitive_fields
```

Expected: FAIL，因为当前渲染直接插入 `coin_name` / `symbol`。

**Step 3: Write minimal implementation**

在 `bot/messages.py` 中：
- 复用已有 `from html import escape`
- 对以下渲染路径里的动态文本统一做 `escape()`：
  - `_render_all_prices_message(...)`
  - `render_help_message(...)`
  - `render_status_message(...)`
  - `render_price_detail_message(...)`
- 仅对文本字段做转义，不要影响 `format_price(...)`、`format_threshold(...)` 这类已格式化值

实现约束：
- 不要改现有文案结构
- 不要引入新的抽象层
- 先用小步重复应用相同规则

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_bot_messages
```

Expected: PASS

**Step 5: Add one handler-level regression**

在 `tests/test_bot_handlers.py` 中新增测试，验证当 `coin_name` 包含特殊字符时，`send_price_update(...)` 最终走 `_send_or_edit_message(...)` 的文本仍是安全的 HTML。

**Step 6: Run handler + message suites**

Run:
```bash
python3 -m unittest tests.test_bot_messages tests.test_bot_handlers
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_bot_messages.py tests/test_bot_handlers.py bot/messages.py
git commit -m "fix: escape bot html message fields"
```

---

### Task 2: 修复 Bot 按钮回调的刷新语义

**Files:**
- Modify: `bot/handlers.py:119-185`
- Test: `tests/test_bot_handlers.py`
- Test: `tests/test_regressions.py:1516-1536`

**Step 1: Write the failing callback tests**

在 `tests/test_bot_handlers.py` 新增两条测试：

```python
def test_button_callback_all_prices_edits_existing_message(self) -> None:
    handler_self = self._build_handler_self()
    handler_self._get_prices = AsyncMock(return_value={"BTCUSDT": 95123.456, "ETHUSDT": 3200.0})
    handler_self._render_all_prices_message = lambda coins, prices: "ALL PRICES"

    query_message = types.SimpleNamespace(chat_id=123, reply_text=AsyncMock())
    query = types.SimpleNamespace(
        data="all_prices",
        answer=AsyncMock(),
        message=query_message,
    )
    update = types.SimpleNamespace(callback_query=query)

    asyncio.run(button_callback(handler_self, update, None))

    handler_self._send_or_edit_message.assert_awaited_once_with(123, "ALL PRICES", message=query_message)
    query_message.reply_text.assert_not_awaited()


def test_button_callback_price_refresh_reuses_original_message(self) -> None:
    handler_self = self._build_handler_self()
    query_message = types.SimpleNamespace(chat_id=123)
    query = types.SimpleNamespace(data="price_BTC", answer=AsyncMock(), message=query_message)
    update = types.SimpleNamespace(callback_query=query)

    asyncio.run(button_callback(handler_self, update, None))

    handler_self.send_price_update.assert_awaited_once_with(123, "BTC", message=query_message)
```

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_bot_handlers.PriceCommandHandlerTests.test_button_callback_all_prices_edits_existing_message tests.test_bot_handlers.PriceCommandHandlerTests.test_button_callback_price_refresh_reuses_original_message
```

Expected: FAIL，因为当前实现对 `all_prices` 直接 `reply_text(...)`，且 `price_` 分支传的是 `message=None`。

**Step 3: Write minimal implementation**

在 `bot/handlers.py` 中：
- `all_prices` 分支改为调用 `self._send_or_edit_message(query.message.chat_id, message, message=query.message)`
- `price_` 分支改为 `await self.send_price_update(query.message.chat_id, coin, message=query.message)`
- 不改变 callback 数据契约：`all_prices` / `price_<coin>`

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_bot_handlers
```

Expected: PASS

**Step 5: Update existing regression expectation**

修改 `tests/test_regressions.py` 里的 `test_button_callback_preserves_full_coin_name_after_prefix`，把预期从 `message=None` 更新为 `message=query.message`。

**Step 6: Run targeted regression suite**

Run:
```bash
python3 -m unittest tests.test_bot_handlers tests.test_regressions.TelegramBotRegressionTests.test_button_callback_preserves_full_coin_name_after_prefix
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_bot_handlers.py tests/test_regressions.py bot/handlers.py
git commit -m "fix: reuse original message for bot refresh callbacks"
```

---

### Task 3: 把 Bot signal handler 注册收敛到运行时生命周期

**Files:**
- Modify: `bot/app.py:32-60`
- Modify: `bot/app.py:133-155`
- Modify: `bot/app.py:249-289`
- Test: `tests/test_bot_app.py`
- Test: `tests/test_regressions.py:1388-1488`

**Step 1: Write the failing lifecycle test**

在 `tests/test_bot_app.py` 中新增测试，模式对齐 `tests/test_runtime_lifecycle.py:488-535`：

```python
def test_telegram_bot_registers_signal_handlers_only_during_run_async(self) -> None:
    application = FakeApplication()

    class FakeApplicationModule:
        @staticmethod
        def builder():
            return FakeApplicationBuilder(application)

    config = types.SimpleNamespace(telegram_bot_token="token", get_enabled_coins=lambda: [])
    original_sigint = object()
    original_sigterm = object()

    with patch.object(bot_app, "Application", FakeApplicationModule), \
         patch.object(bot_app, "CommandHandler", FakeCommandHandler), \
         patch.object(bot_app, "CallbackQueryHandler", FakeCallbackQueryHandler), \
         patch.object(bot_app.signal, "signal") as mock_signal:
        mock_signal.side_effect = [original_sigint, original_sigterm, None, None]
        telegram_bot = bot_app.TelegramBot(config)
        self.assertEqual(mock_signal.call_args_list, [])
```

再补一条运行期测试，验证 `run_async()` 才会注册并在退出时恢复。

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_bot_app
```

Expected: FAIL，因为当前 `__init__` 就会注册 signal handler。

**Step 3: Write minimal implementation**

在 `bot/app.py` 中：
- 从 `__init__` 移除 `_setup_signal_handlers()`
- 增加类似 `ws_monitor` 的注册状态标记（例如 `_signal_handlers_registered`）
- 在 `run_async()` 启动阶段调用 `_setup_signal_handlers()`
- 在 `finally` 中恢复原 handler
- `_restore_signal_handler(...)` 可保留，但要补成对恢复路径

实现要求：
- 不改变现有 handler 回调函数签名
- 不改变 `_shutdown_event` 语义
- 尽量复用 `monitor/ws_monitor.py` 现有生命周期模式

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_bot_app tests.test_regressions.TelegramBotRegressionTests
```

Expected: PASS

**Step 5: Add partial-startup regression**

补一条回归，验证当 `initialize()` 或 `start_polling()` 失败时，signal handler 仍能在 `finally` 中恢复。

**Step 6: Run bot lifecycle suites**

Run:
```bash
python3 -m unittest tests.test_bot_app tests.test_regressions.TelegramBotRegressionTests tests.test_runtime_lifecycle
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_bot_app.py tests/test_regressions.py bot/app.py
git commit -m "fix: register bot signal handlers during runtime only"
```

---

### Task 4: 统一 monitor 告警消息中的 HTML 安全边界

**Files:**
- Modify: `monitor/price_monitor.py:169-175`
- Modify: `monitor/price_monitor.py:324-333`
- Modify: `monitor/price_monitor.py:419-429`
- Test: `tests/test_regressions.py`

**Step 1: Write the failing regression test**

在 `tests/test_regressions.py` 的 `PriceMonitorRegressionTests` 中新增：

```python
def test_monitor_notifications_escape_symbol_for_html_parse_mode(self) -> None:
    notifier = StubNotifier()
    config = CoinConfig(
        coin_name="BTC",
        enabled=True,
        symbol="BTC<USDT>",
        integer_threshold=1000.0,
        volatility_percent=3.0,
        volatility_window=180,
        volume_alert_multiplier=10.0,
    )
    price_monitor = PriceMonitor(config, notifier)

    with patch.object(monitor.price_monitor, "now_in_configured_timezone", return_value=datetime(2026, 3, 6, tzinfo=timezone.utc)):
        price_monitor._send_milestone_notification(101000.0, 101000.0)

    self.assertIn("BTC&lt;USDT&gt;", notifier.messages[0])
```

然后再各补一条波动消息和成交量消息的断言，至少保证三类消息模板都统一 escape。

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_regressions.PriceMonitorRegressionTests.test_monitor_notifications_escape_symbol_for_html_parse_mode
```

Expected: FAIL，因为当前消息模板直接插入 `self.config.symbol`。

**Step 3: Write minimal implementation**

在 `monitor/price_monitor.py` 中：
- 引入 `from html import escape`
- 对三类消息中的 `self.config.symbol` 做 `escape(...)`
- 仅处理动态文本，不改变数值与格式化函数输出

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.PriceMonitorRegressionTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_regressions.py monitor/price_monitor.py
git commit -m "fix: escape monitor html notification fields"
```

---

### Task 5: 让单条 ticker 脏消息不触发整连接重连

**Files:**
- Modify: `common/clients/websocket.py:109-124`
- Modify: `common/clients/websocket.py:176-190`
- Test: `tests/test_regressions.py:1302-1387`

**Step 1: Write the failing regression test**

在 `tests/test_regressions.py` 的 `BinanceWebSocketClientRegressionTests` 中新增：

```python
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

    asyncio.run(client._message_handler())

    self.assertEqual(received, [("BTCUSDT", 95123.45)])
    self.assertEqual(disconnects, [])
    self.assertEqual(client.state, ConnectionState.CONNECTED)
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_regressions.BinanceWebSocketClientRegressionTests.test_bad_ticker_payload_is_logged_and_skipped_without_reconnect
```

Expected: FAIL，因为当前 ticker 解析异常会冒泡到外层，进入 reconnect 路径。

**Step 3: Write minimal implementation**

在 `common/clients/websocket.py` 中：
- 保留 `_parse_ticker_message(...)` 的解析职责
- 在 `_message_handler()` 的 ticker 分支内局部捕获解析异常，记录日志后 `continue`
- 不要因为单条坏消息修改 `self.state`

实现要求：
- 不改变 `json.JSONDecodeError` 的既有处理
- 不改变 callback 抛错的既有日志策略
- 单条 ticker 坏消息应与 kline 坏消息一样，属于消息级容错

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.BinanceWebSocketClientRegressionTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_regressions.py common/clients/websocket.py
git commit -m "fix: ignore malformed ticker payloads without reconnecting"
```

---

### Task 6: 拆开 PriceMonitor 的输出节流与波动采样

**Files:**
- Modify: `monitor/price_monitor.py:442-470`
- Test: `tests/test_regressions.py:324-499`

**Step 1: Write the failing regression test**

在 `tests/test_regressions.py` 的 `PriceMonitorRegressionTests` 中新增测试，验证小幅连续变动即使不产生日志输出，也会持续更新波动窗口：

```python
def test_small_price_changes_still_update_volatility_window(self) -> None:
    notifier = StubNotifier()
    price_monitor = self._build_price_monitor(notifier, volatility_percent=0.2, volatility_window=120)

    outputs = self._replay_price_points(
        price_monitor,
        [
            (0, 1.0000),
            (10, 1.00005),
            (20, 1.00010),
            (30, 1.00015),
        ],
    )

    self.assertGreaterEqual(len(price_monitor.price_history), 3)
    self.assertTrue(any(output is None for output in outputs))
```

再补一条更强的断言：即使中间多次 `output is None`，后续 `check_volatility(...)` 仍基于完整窗口计算，而不是只看被节流后的点。

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_regressions.PriceMonitorRegressionTests.test_small_price_changes_still_update_volatility_window
```

Expected: FAIL，因为当前 `check()` 在小变动时直接返回，后续 `check_volatility()` 不执行。

**Step 3: Write minimal implementation**

在 `monitor/price_monitor.py` 中：
- 保留 `last_processed_price` 作为终端输出节流判断
- 但不要在小变动时提前返回到完全跳过历史更新
- 调整 `check()` 顺序，使：
  - 价格历史更新 / 波动计算继续发生
  - 是否返回 terminal output 再单独决定
- 不要动 `check_volume_anomaly(...)` 的闭盘驱动语义

实现要求：
- 不改变 milestone crossing 的现有判断模型
- 尽量只重排 `check()` 流程，不新增复杂抽象

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_regressions.PriceMonitorRegressionTests
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_regressions.py monitor/price_monitor.py
git commit -m "fix: decouple price sampling from terminal output throttling"
```

---

### Task 7: 清理 bot 渲染死代码并收敛入口

**Files:**
- Modify: `bot/messages.py:161-174`
- Test: `tests/test_bot_messages.py`
- Test: `tests/test_bot_handlers.py`

**Step 1: Confirm dead code with test coverage audit**

先确认 `render_price_update(...)` 没有任何生产调用，只剩定义。当前仓库搜索结果应仅命中：
- `bot/messages.py:161`

**Step 2: Write a small regression to lock the real rendering path**

在 `tests/test_bot_handlers.py` 中补一条断言，明确 `send_price_update(...)` 使用的是 `render_price_detail_message(...)` 所代表的“详情页文案”，例如断言文本中包含：
- `价格详情`
- `里程碑`
- `波动告警`

**Step 3: Remove dead code**

从 `bot/messages.py` 删除 `render_price_update(...)` 定义，不做兼容保留。

**Step 4: Run tests to verify it passes**

Run:
```bash
python3 -m unittest tests.test_bot_messages tests.test_bot_handlers
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_bot_messages.py tests/test_bot_handlers.py bot/messages.py
git commit -m "refactor: remove unused bot price update renderer"
```

---

### Task 8: 梳理依赖与工程治理规则

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Test: `tests/test_deployment_contracts.py`
- Test: `tests/test_entrypoints.py`

**Step 1: Decide the smallest acceptable dependency policy**

采用最小策略之一：
- 继续保留范围依赖，但新增锁定依赖流程说明
- 或直接把线上运行依赖钉到具体 patch 版本

推荐优先选第一种，避免在本轮把依赖升级/降级变成额外风险。

**Step 2: Write doc/contract regression if needed**

如果你决定引入锁定依赖文件或调整 ignore 规则，先在相关测试里加一条契约测试，明确：
- Docker 构建不会把 `.venv/`、`logs/`、`.env` 带进上下文
- `.dockerignore` 不再包含会让维护者困惑但无实际收益的条目时，测试同步更新

**Step 3: Apply minimal config cleanup**

建议最小变更：
- `requirements.txt` 保留现状，但在后续文档/计划里明确锁定依赖策略
- `.gitignore` 复核是否继续忽略 `.dockerignore`
- `.dockerignore` 复核是否继续忽略 `README.md`、`*.md`、`Dockerfile`、`docker-compose.yml`

**Step 4: Run contract tests**

Run:
```bash
python3 -m unittest tests.test_deployment_contracts tests.test_entrypoints
```

Expected: PASS

**Step 5: Commit**

```bash
git add requirements.txt .gitignore .dockerignore tests/test_deployment_contracts.py tests/test_entrypoints.py
git commit -m "chore: tighten dependency and ignore-file contracts"
```

---

### Task 9: Run focused verification, then full regression suite

**Files:**
- Verify only

**Step 1: Run all changed local suites**

Run:
```bash
python3 -m unittest \
  tests.test_bot_messages \
  tests.test_bot_handlers \
  tests.test_bot_app \
  tests.test_notifications \
  tests.test_runtime_lifecycle \
  tests.test_regressions
```

Expected: PASS

**Step 2: Run full repository suite**

Run:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS

**Step 3: Manual spot checks**

If `.env` is configured, run:
```bash
python3 -m bot
python3 -m monitor --status
```

Expected:
- Bot can start and stop cleanly
- `monitor --status` still works
- No obvious HTML rendering issues in Telegram messages

**Step 4: Request code review**

Use `@superpowers:requesting-code-review` after implementation is complete and tests pass.

**Step 5: Final commit if needed**

If verification changes were needed, create a new commit rather than amending.

---

## Notes for execution

- 优先执行 Task 1-3，再执行 Task 4-6；Task 7-8 放到收尾。
- 除非测试明确要求，不要引入新测试文件；优先把回归测试补进现有 `tests/test_*.py`。
- 不要做架构性大重构；本计划目标是边界收敛，不是重写模块。
- 任何提交前都要先跑对应局部测试；不要等到最后一次性发现问题。

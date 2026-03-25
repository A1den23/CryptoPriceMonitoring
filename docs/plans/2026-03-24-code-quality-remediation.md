# Code Quality and Reliability Remediation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复通知链路可靠性问题、降低代码结构复杂度，并补齐部署与日志方面的工程化短板。

**Architecture:** 保持现有 `common/`、`monitor/`、`bot/` 三层边界不变，先做 P1 可靠性修复，再做 P2 结构收敛，最后做 P3 运维与测试整理。每个任务都按 `@superpowers:test-driven-development` 执行：先写最小失败测试，再做最小实现，再跑局部测试，最后小步提交；收尾时用 `@superpowers:verification-before-completion` 做整体验证。

**Tech Stack:** Python 3.11、unittest、asyncio、requests、aiohttp、websockets、python-telegram-bot、Docker Compose

---

### Task 1: 修复 Telegram 发送成功判定语义

**Files:**
- Modify: `common/notifications.py`
- Create: `tests/test_notifications.py`

**Step 1: Write the failing test**

在 `tests/test_notifications.py` 新增：

```python
import unittest
from unittest.mock import Mock

from common.notifications import TelegramNotifier


class DummyResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("http error")

    def json(self):
        return self._payload


class TelegramNotifierRegressionTests(unittest.TestCase):
    def test_send_message_returns_false_when_telegram_ok_false(self):
        notifier = TelegramNotifier("token", "chat")
        notifier.session.post = Mock(
            return_value=DummyResponse(
                {"ok": False, "description": "Bad Request: can't parse entities"}
            )
        )

        self.assertFalse(notifier.send_message("<b>broken"))
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_notifications.TelegramNotifierRegressionTests.test_send_message_returns_false_when_telegram_ok_false
```

Expected: FAIL，因为当前实现只看 HTTP 状态码，不检查 JSON 里的 `ok`。

**Step 3: Write minimal implementation**

在 `common/notifications.py` 中将发送逻辑调整为：

```python
response = self.session.post(url, json=data, timeout=10)
response.raise_for_status()
payload = response.json()
if not payload.get("ok", False):
    logger.error("Telegram API rejected message: %s", payload.get("description", "unknown error"))
    return False
return True
```

实现要求：
- 保留现有 `requests.exceptions.RequestException` 的 retry 行为
- 对 `ok: false` 返回 `False`，不要记录“sent successfully”
- 不要把 Telegram token 写进日志

**Step 4: Run test to verify it passes**

Run:
```bash
python3 -m unittest tests.test_notifications.TelegramNotifierRegressionTests.test_send_message_returns_false_when_telegram_ok_false
```

Expected: PASS

**Step 5: Add one success-path regression test**

新增测试，验证 `{"ok": True}` 时返回 `True`。

**Step 6: Run local notifier suite**

Run:
```bash
python3 -m unittest tests.test_notifications
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_notifications.py common/notifications.py
git commit -m "fix: honor Telegram API success flag"
```

---

### Task 2: 修复 PriceMonitor 告警状态只在发送成功后推进

**Files:**
- Modify: `monitor/price_monitor.py`
- Create: `tests/test_price_monitor.py`

**Step 1: Write the failing tests**

在 `tests/test_price_monitor.py` 新增最小测试，至少覆盖两个场景：

```python
import unittest
from unittest.mock import Mock

from common.config import CoinConfig
from monitor.price_monitor import PriceMonitor


class PriceMonitorAlertStateTests(unittest.TestCase):
    def make_monitor(self):
        config = CoinConfig(
            coin_name="BTC",
            enabled=True,
            symbol="BTCUSDT",
            integer_threshold=1000.0,
            volatility_percent=3.0,
            volatility_window=60,
            volume_alert_multiplier=2.0,
        )
        notifier = Mock()
        notifier.send_message.return_value = False
        return PriceMonitor(config, notifier), notifier

    def test_milestone_cooldown_updates_only_after_successful_send(self):
        monitor, notifier = self.make_monitor()
        monitor.last_price = 99999.0

        monitor.check_integer_milestone(100001.0)

        self.assertIsNone(monitor.last_milestone_notification_time)
        notifier.send_message.assert_called_once()

    def test_volume_cooldown_updates_only_after_successful_send(self):
        monitor, notifier = self.make_monitor()
        monitor.check_volume_anomaly(100.0, 100.0)
        monitor.check_volume_anomaly(101.0, 100.0)
        monitor.check_volume_anomaly(102.0, 10000.0)

        self.assertIsNone(monitor.last_volume_alert_time)
```

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_price_monitor.PriceMonitorAlertStateTests
```

Expected: FAIL，因为当前实现会先写 cooldown/state，再发送消息。

**Step 3: Write minimal implementation**

将 `PriceMonitor` 中“构造消息”和“提交状态更新”拆开，新增统一通知入口，例如：

```python
def _send_notification(self, message: str, on_success=None) -> None:
    ...
    success = self.notifier.send_message(message)
    if success and on_success:
        on_success()
```

然后把以下状态更新延后到 `on_success` 中执行：
- `last_milestone_notification_time`
- `last_volatility_notification_time`
- `last_volume_alert_time`

实现要求：
- 不改变现有告警文案
- 不引入新的全局状态对象
- 保留异步 `to_thread` 路径，但成功回调必须只在结果为 `True` 时触发

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_price_monitor.PriceMonitorAlertStateTests
```

Expected: PASS

**Step 5: Add one success-path regression**

增加一条测试，验证 `notifier.send_message.return_value = True` 时，对应 cooldown 字段确实会更新。

**Step 6: Run local price-monitor suite**

Run:
```bash
python3 -m unittest tests.test_price_monitor
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_price_monitor.py monitor/price_monitor.py
git commit -m "fix: update price alert cooldowns only after successful delivery"
```

---

### Task 3: 修复 Stablecoin 脱锚告警状态只在发送成功后推进

**Files:**
- Modify: `monitor/stablecoin_depeg_monitor.py`
- Create: `tests/test_stablecoin_monitor.py`

**Step 1: Write the failing tests**

在 `tests/test_stablecoin_monitor.py` 新增：

```python
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from common.clients.defillama import StablecoinSnapshot
from monitor.stablecoin_depeg_monitor import StablecoinDepegMonitor


class StablecoinDepegMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_alert_time_updates_only_after_successful_send(self):
        config = Mock(
            stablecoin_depeg_threshold_percent=5.0,
            stablecoin_depeg_alert_cooldown_seconds=300,
            stablecoin_depeg_top_n=25,
            stablecoin_depeg_poll_interval_seconds=60,
        )
        notifier = Mock()
        notifier.send_message.return_value = False
        client = AsyncMock()
        client.fetch_stablecoins.return_value = [
            StablecoinSnapshot(symbol="USDX", name="USD X", price=0.92, rank=10)
        ]
        monitor = StablecoinDepegMonitor(config, notifier, client)

        alerts = await monitor.run_once()

        self.assertEqual(alerts, 0)
        self.assertIsNone(monitor._states["USDX"].last_alert_time)
        self.assertTrue(monitor._states["USDX"].is_depegged)
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_stablecoin_monitor.StablecoinDepegMonitorTests.test_last_alert_time_updates_only_after_successful_send
```

Expected: FAIL，因为当前实现先写 `last_alert_time`，且 `alerts += 1` 不看真实发送结果。

**Step 3: Write minimal implementation**

调整 `monitor/stablecoin_depeg_monitor.py`：
- `_build_alert_message()` 只负责判定和构造文案，不提前写 `last_alert_time`
- `state.is_depegged = True` 可以保留，因为它反映当前行情状态
- 只有 `_send_alert()` 返回 `True` 时，才设置 `state.last_alert_time = now`
- `run_once()` 的 `alerts += 1` 只在真实发送成功后执行

可按如下结构落地：

```python
message, now = self._build_alert_message(snapshot)
if message is None:
    continue
sent = await self._send_alert(message)
if sent:
    state.last_alert_time = now
    alerts += 1
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3 -m unittest tests.test_stablecoin_monitor.StablecoinDepegMonitorTests.test_last_alert_time_updates_only_after_successful_send
```

Expected: PASS

**Step 5: Add one success-path regression**

新增一条测试，验证发送成功时：
- `alerts == 1`
- `last_alert_time is not None`

**Step 6: Run local stablecoin suite**

Run:
```bash
python3 -m unittest tests.test_stablecoin_monitor
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_stablecoin_monitor.py monitor/stablecoin_depeg_monitor.py
git commit -m "fix: advance stablecoin alert state only after successful send"
```

---

### Task 4: 在优雅停机时等待未完成通知任务

**Files:**
- Modify: `monitor/price_monitor.py`
- Modify: `monitor/ws_monitor.py`
- Create: `tests/test_runtime_lifecycle.py`

**Step 1: Write the failing test**

在 `tests/test_runtime_lifecycle.py` 新增：

```python
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from common.config import CoinConfig
from monitor.price_monitor import PriceMonitor
from monitor.ws_monitor import WebSocketMultiCoinMonitor


class RuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_price_monitor_flush_waits_for_pending_notification_tasks(self):
        config = CoinConfig("BTC", True, "BTCUSDT", 1000.0, 3.0, 60, 2.0)
        notifier = Mock()
        gate = asyncio.Event()

        def slow_send(_message):
            asyncio.run(asyncio.sleep(0))
            return True

        notifier.send_message.side_effect = slow_send
        monitor = PriceMonitor(config, notifier)
        monitor._send_notification("hello")

        self.assertGreaterEqual(len(monitor._notification_tasks), 1)
        await monitor.flush_notifications()
        self.assertEqual(len(monitor._notification_tasks), 0)
```

然后再加一条集成测试，验证 `WebSocketMultiCoinMonitor` 在 shutdown 路径会调用各个 `PriceMonitor.flush_notifications()`。

**Step 2: Run tests to verify they fail**

Run:
```bash
python3 -m unittest tests.test_runtime_lifecycle.RuntimeLifecycleTests
```

Expected: FAIL，因为当前没有公开的 flush/drain 接口，shutdown 也不会等这些任务。

**Step 3: Write minimal implementation**

在 `PriceMonitor` 中新增：

```python
async def flush_notifications(self) -> None:
    if not self._notification_tasks:
        return
    pending = list(self._notification_tasks)
    await asyncio.gather(*pending, return_exceptions=True)
```

在 `WebSocketMultiCoinMonitor.run()` 的 shutdown 路径中新增：

```python
await asyncio.gather(
    *(monitor.flush_notifications() for monitor in self.monitors.values()),
    return_exceptions=True,
)
```

实现要求：
- 只 drain 已创建的任务，不新增后台 worker
- 不要吞掉 monitor stop 的主异常
- 若需要超时，使用一个小而明确的 `asyncio.wait_for(...)`

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest tests.test_runtime_lifecycle.RuntimeLifecycleTests
```

Expected: PASS

**Step 5: Run adjacent suites**

Run:
```bash
python3 -m unittest tests.test_price_monitor tests.test_runtime_lifecycle
```

Expected: PASS

**Step 6: Commit**

```bash
git add tests/test_runtime_lifecycle.py monitor/price_monitor.py monitor/ws_monitor.py
git commit -m "fix: flush pending notifications during graceful shutdown"
```

---

### Task 5: 给应用文件日志增加轮转，避免无限增长

**Files:**
- Modify: `common/logging.py`
- Create: `tests/test_logging.py`

**Step 1: Write the failing test**

在 `tests/test_logging.py` 新增：

```python
import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from common.logging import setup_logging


class LoggingRegressionTests(unittest.TestCase):
    def test_setup_logging_uses_rotating_file_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "app.log"
            logger = setup_logging(log_file=str(log_file))
            file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
            self.assertTrue(any(isinstance(h, RotatingFileHandler) for h in file_handlers))
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_logging.LoggingRegressionTests.test_setup_logging_uses_rotating_file_handler
```

Expected: FAIL，因为当前使用的是 `logging.FileHandler`。

**Step 3: Write minimal implementation**

把 `common/logging.py` 中的 file handler 改为 `RotatingFileHandler`，使用固定保守默认值，例如：

```python
RotatingFileHandler(str(log_path), maxBytes=10 * 1024 * 1024, backupCount=5)
```

实现要求：
- 保持现有路径校验逻辑不变
- 不新增环境变量配置，避免本轮过度设计
- 保留 console handler

**Step 4: Run test to verify it passes**

Run:
```bash
python3 -m unittest tests.test_logging.LoggingRegressionTests.test_setup_logging_uses_rotating_file_handler
```

Expected: PASS

**Step 5: Add one fallback-path regression**

增加一条测试，验证非法日志路径时仍然能退回 console-only。

**Step 6: Run local logging suite**

Run:
```bash
python3 -m unittest tests.test_logging
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_logging.py common/logging.py
git commit -m "fix: rotate application log files"
```

---

### Task 6: 修复 Docker 构建上下文泄漏并校正文档说明

**Files:**
- Modify: `.dockerignore`
- Modify: `README.md`
- Modify: `DEPLOYMENT.md`
- Create: `tests/test_deployment_contracts.py`

**Step 1: Write the failing test**

在 `tests/test_deployment_contracts.py` 新增：

```python
import unittest
from pathlib import Path


class DeploymentContractTests(unittest.TestCase):
    def test_dockerignore_excludes_dotvenv(self):
        text = Path('.dockerignore').read_text(encoding='utf-8')
        self.assertIn('.venv/', text)
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_deployment_contracts.DeploymentContractTests.test_dockerignore_excludes_dotvenv
```

Expected: FAIL，因为当前 `.dockerignore` 没有 `.venv/`。

**Step 3: Write minimal implementation**

修改 `.dockerignore`：

```text
.venv/
```

同时最小更新 `README.md` 和 `DEPLOYMENT.md`：
- 明确当前主要测试入口是 `python3 -m unittest discover -s tests -p 'test_*.py'`
- 说明 `docker-compose.yml` 中的 `deploy.resources` 更偏向声明性配置，在普通 `docker compose` 模式下不应被当作强保证
- 不扩展为完整 CI 文档

**Step 4: Run test to verify it passes**

Run:
```bash
python3 -m unittest tests.test_deployment_contracts.DeploymentContractTests.test_dockerignore_excludes_dotvenv
```

Expected: PASS

**Step 5: Manually verify docs stay aligned**

检查：
- `README.md`
- `DEPLOYMENT.md`

确认没有引入与实际运行方式冲突的新描述。

**Step 6: Commit**

```bash
git add tests/test_deployment_contracts.py .dockerignore README.md DEPLOYMENT.md
git commit -m "chore: tighten docker context and deployment docs"
```

---

### Task 7: 去掉 `TelegramBot` 的 monkey-patch 组装方式

**Files:**
- Modify: `bot/app.py`
- Modify: `bot/handlers.py`
- Modify: `bot/messages.py`
- Create: `tests/test_bot_app.py`

**Step 1: Write characterization tests**

在 `tests/test_bot_app.py` 中先固定当前公开行为，例如：

```python
import unittest

from bot.app import TelegramBot


class TelegramBotStructureTests(unittest.TestCase):
    def test_telegram_bot_exposes_command_methods(self):
        for name in [
            'start_command',
            'help_command',
            'price_command',
            'stablecoins_command',
            'status_command',
            'all_prices_command',
            'button_callback',
        ]:
            self.assertTrue(callable(getattr(TelegramBot, name)))
```

再补一条测试，实例化 `TelegramBot` 后确认 handler 注册仍然发生。

**Step 2: Run tests to establish the baseline**

Run:
```bash
python3 -m unittest tests.test_bot_app
```

Expected: PASS（这是表征测试，不要求先失败）

**Step 3: Refactor with minimal behavior change**

目标：
- 移除 `bot/app.py` 文件底部的 monkey-patch 代码
- 将命令处理与消息构建改为显式方法或显式 helper 调用
- 如果保留 `bot/handlers.py`、`bot/messages.py`，也要让 `TelegramBot` 的方法来源在类定义中清晰可见

建议最小落地：

```python
class TelegramBot:
    async def start_command(self, update, context):
        return await handlers.start_command(self, update, context)
```

消息构建辅助方法同理，改为显式包装，而不是模块末尾赋值。

**Step 4: Run tests after refactor**

Run:
```bash
python3 -m unittest tests.test_bot_app tests.test_runtime_lifecycle
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_bot_app.py bot/app.py bot/handlers.py bot/messages.py
git commit -m "refactor: replace TelegramBot monkey patching with explicit methods"
```

---

### Task 8: 收缩包级动态导出，改为更显式的内部导入

**Files:**
- Modify: `common/__init__.py`
- Modify: `monitor/__init__.py`
- Modify: `bot/__init__.py`
- Modify: internal imports in `monitor/*.py`, `bot/*.py`, `common/clients/*.py` as needed
- Create: `tests/test_entrypoints.py`

**Step 1: Write the failing import-contract tests**

在 `tests/test_entrypoints.py` 新增：

```python
import unittest


class ImportContractTests(unittest.TestCase):
    def test_common_package_still_exports_public_contract(self):
        import common
        self.assertTrue(hasattr(common, 'ConfigManager'))
        self.assertTrue(hasattr(common, 'TelegramNotifier'))

    def test_bot_and_monitor_module_entrypoints_remain_importable(self):
        import bot
        import monitor
        self.assertTrue(hasattr(bot, 'main'))
        self.assertTrue(hasattr(monitor, 'main'))
```

**Step 2: Run tests to verify current baseline**

Run:
```bash
python3 -m unittest tests.test_entrypoints
```

Expected: PASS（这是收敛性重构的契约测试）

**Step 3: Refactor imports minimally**

目标：
- 内部代码优先从具体模块导入，例如 `from common.notifications import TelegramNotifier`
- `__init__.py` 只保留少量稳定导出
- 不再依赖“几乎所有对象都经由包根懒加载”

落地要求：
- 不破坏 `import common` / `import bot` / `import monitor` 的现有公共接口
- 不在这一任务中重命名公共 API

**Step 4: Run tests after refactor**

Run:
```bash
python3 -m unittest tests.test_entrypoints tests.test_bot_app tests.test_notifications
```

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_entrypoints.py common/__init__.py monitor/__init__.py bot/__init__.py common/ bot/ monitor/
git commit -m "refactor: reduce reliance on dynamic package exports"
```

---

### Task 9: 统一入口与健康检查约定

**Files:**
- Modify: `monitor.py`
- Modify: `bot.py`
- Modify: `monitor/__main__.py`
- Modify: `bot/__main__.py`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Modify: `DEPLOYMENT.md`
- Update: `tests/test_entrypoints.py`

**Step 1: Write the failing contract tests**

在 `tests/test_entrypoints.py` 增加：

```python
from pathlib import Path

class EntrypointContractTests(unittest.TestCase):
    def test_readme_and_deployment_docs_reference_one_primary_start_mode(self):
        readme = Path('README.md').read_text(encoding='utf-8')
        deployment = Path('DEPLOYMENT.md').read_text(encoding='utf-8')
        self.assertIn('python3 -m monitor', readme)
        self.assertIn('python3 -m bot', readme)
        self.assertIn('python -m monitor', deployment)
        self.assertIn('python -m bot', deployment)
```

如果你决定反过来统一到顶层脚本入口，就把断言改成只检查 `monitor.py` / `bot.py`。本任务必须先选定一种唯一主入口，再写断言。

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest tests.test_entrypoints.EntrypointContractTests.test_readme_and_deployment_docs_reference_one_primary_start_mode
```

Expected: FAIL，因为当前文档与 Docker/healthcheck 同时保留多种约定。

**Step 3: Write minimal implementation**

本任务建议统一到包入口：
- 主要启动方式：`python3 -m monitor` / `python3 -m bot`
- 顶层 `monitor.py` / `bot.py` 仅保留轻量兼容包装，或者在确认不再需要后删除
- `docker-compose.yml` 改用模块入口
- `Dockerfile` 健康检查不再依赖只匹配 `monitor.py` / `bot.py` 的 `cmdline` 片段

最小实现原则：
- 选定一种主入口后，让文档、Compose、健康检查全部对齐
- 不同时维护两套同等地位的运行说明

**Step 4: Run local entrypoint suite**

Run:
```bash
python3 -m unittest tests.test_entrypoints
```

Expected: PASS

**Step 5: Manually verify runtime commands**

Run:
```bash
python3 -m monitor --status
python3 -m bot
```

Expected:
- `monitor` 能正常进入状态逻辑
- `bot` 能正常初始化或至少在缺少真实 Telegram token 时给出预期配置错误

**Step 6: Commit**

```bash
git add tests/test_entrypoints.py monitor.py bot.py monitor/__main__.py bot/__main__.py Dockerfile docker-compose.yml README.md DEPLOYMENT.md
git commit -m "refactor: align entrypoints, docs, and healthchecks"
```

---

### Task 10: 运行完整验证并整理变更边界

**Files:**
- No new product code expected
- Update tests/docs only if verification exposes a real mismatch

**Step 1: Run the full unit test suite**

Run:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: PASS

**Step 2: Run a Docker build smoke test**

Run:
```bash
docker compose build
```

Expected: PASS

**Step 3: Run a startup smoke test for the monitor entrypoint**

Run:
```bash
python3 -m monitor --status
```

Expected: PASS

**Step 4: Run a startup smoke test for the bot entrypoint**

Run:
```bash
python3 -m bot
```

Expected: exits cleanly with normal startup path or a clear configuration error if Telegram token is absent

**Step 5: Review the diff for scope control**

Checklist:
- 只改了通知可靠性、结构收敛、日志轮转、Docker hygiene、文档一致性
- 没有顺手重构无关业务逻辑
- 没有新增不必要的配置项

**Step 6: Final commit**

```bash
git add tests/ common/ monitor/ bot/ .dockerignore Dockerfile docker-compose.yml README.md DEPLOYMENT.md
git commit -m "chore: improve reliability, structure, and deployment hygiene"
```

---

## Execution Status

- Status: 已完成并已合并到 `main`
- Final verification completed on main:
  - `python3 -m unittest discover -s tests -p 'test_*.py'`
  - `python3 -m monitor --status`
  - `python3 -m bot`
  - `docker compose build`
- Result summary:
  - 单元测试通过
  - monitor / bot 真实入口验证通过
  - Docker 构建验证通过

## Notes for Execution

- 严格按顺序执行，先完成 Task 1-4，再决定是否继续 Task 5-9。
- 如果时间有限，**最小高价值范围** 是 Task 1-4 + Task 10。
- 新测试优先放进新的领域测试文件，**不要继续把新回归点堆进** `tests/test_regressions.py`。
- 如果 Task 8 或 Task 9 的结构改动引发过大连锁反应，停止扩散，回到“保留公共接口、只减少内部动态依赖”的最小方案。

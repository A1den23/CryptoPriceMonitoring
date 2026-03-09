# P1 Reliability Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复代码审查报告中的 4 个 P1 问题，提升运行时可靠性、状态语义正确性和配置一致性，并补齐最小回归测试。

**Architecture:** 继续保持现有 `common/`、`monitor/`、`bot/` 边界不变，按子系统分阶段做最小改动。每个问题先补失败测试，再做最小实现，再运行局部测试，最后运行完整回归测试。除 P1 以外的问题不纳入本轮。

**Tech Stack:** Python 3.11、unittest、asyncio、aiohttp、websockets、python-telegram-bot、Docker Compose

---

### Task 1: 修复 WebSocket 干净关闭后的重连语义

**Files:**
- Modify: `common/clients/websocket.py`
- Test: `tests/test_regressions.py`

**Step 1: 写失败测试，覆盖 clean close 后应进入 reconnect 语义**

在 `tests/test_regressions.py` 的 `BinanceWebSocketClientRegressionTests` 中新增一个测试，模拟：
- `client.state = CONNECTED`
- `client.websocket` 是一个正常结束迭代的 fake websocket
- `_stop_event` 未设置
- 执行 `_message_handler()` 后，断言客户端不再保持 `CONNECTED`
- 如实现采用显式状态切换，则断言进入 `RECONNECTING`

测试重点：不要依赖 watchdog，直接锁定消息循环正常退出后的状态变化。

**Step 2: 运行该测试并确认失败**

Run:
```bash
python3 -m unittest tests.test_regressions.BinanceWebSocketClientRegressionTests.test_message_handler_clean_close_transitions_to_reconnecting
```

Expected: FAIL，因为当前实现对正常结束的消息循环没有及时切状态。

**Step 3: 以最小改动修复 `common/clients/websocket.py`**

实现要求：
- 当 `_message_handler()` 的 `async for` 正常结束，且 `_stop_event` 未设置时：
  - 记录连接已结束
  - 将状态切换为 `RECONNECTING`
  - 如需要，走统一断连告警路径，但不要在主动 stop 时误触发
- 不引入新的状态枚举
- 不重构整个 `start()` 循环

**Step 4: 运行测试确认通过**

Run:
```bash
python3 -m unittest tests.test_regressions.BinanceWebSocketClientRegressionTests.test_message_handler_clean_close_transitions_to_reconnecting
```

Expected: PASS

**Step 5: 补一个 stop 场景保护测试**

在同一测试类中新增测试，验证：
- `_stop_event` 已设置
- `_message_handler()` 正常结束时不会误切到 reconnect 语义

**Step 6: 运行 WebSocket 回归测试组**

Run:
```bash
python3 -m unittest tests.test_regressions.BinanceWebSocketClientRegressionTests
```

Expected: PASS

**Step 7: Commit**

```bash
git add tests/test_regressions.py common/clients/websocket.py
git commit -m "fix: reconnect immediately after clean websocket close"
```

---

### Task 2: 修复 Bot 启动失败时的 cleanup

**Files:**
- Modify: `bot/app.py`
- Test: `tests/test_regressions.py`

**Step 1: 写失败测试，覆盖 startup 中途失败的 cleanup 行为**

在 `tests/test_regressions.py` 中新增测试，目标覆盖：
- `TelegramBot.run_async()` 中 `initialize()` 抛异常时：
  - heartbeat task 被取消
  - 不留下悬挂运行状态
- 或者 `start_polling()` 抛异常时：
  - `updater.stop()` / `application.stop()` / `application.shutdown()` 按已初始化程度执行

优先写一个最小、可稳定复现的测试，不要求一次覆盖所有 startup 阶段。

**Step 2: 运行该测试并确认失败**

Run:
```bash
python3 -m unittest tests.test_regressions.TelegramBotRegressionTests.test_run_async_cleans_up_when_startup_fails
```

Expected: FAIL，因为当前 startup 关键步骤在 `try/finally` 之前。

**Step 3: 以最小改动修复 `bot/app.py`**

实现要求：
- 将 startup 过程纳入统一 cleanup 范围
- heartbeat task 只要被创建，就必须在异常路径被取消
- 对 application / updater 的 stop/shutdown 调用应适配“部分启动成功”的状态
- 不引入复杂生命周期管理器

**Step 4: 运行测试确认通过**

Run:
```bash
python3 -m unittest tests.test_regressions.TelegramBotRegressionTests.test_run_async_cleans_up_when_startup_fails
```

Expected: PASS

**Step 5: 运行 Telegram Bot 回归测试组**

Run:
```bash
python3 -m unittest tests.test_regressions.TelegramBotRegressionTests tests.test_regressions.MainEntrypointRegressionTests
```

Expected: PASS

**Step 6: Commit**

```bash
git add tests/test_regressions.py bot/app.py
git commit -m "fix: clean up bot resources on startup failure"
```

---

### Task 3: 修复 `latest_volume_info` 陈旧残留

**Files:**
- Modify: `monitor/price_monitor.py`
- Possibly Modify: `monitor/ws_monitor.py`
- Test: `tests/test_regressions.py`

**Step 1: 写失败测试，覆盖 volume 信息只应展示一次**

在 `tests/test_regressions.py` 中新增测试，模拟：
- 先通过 `check_volume_anomaly()` 或直接设置受控状态，制造一个 `latest_volume_info`
- 连续调用两次 `check()`
- 断言：
  - 第一次输出包含该 volume 信息
  - 第二次输出不再包含同一条陈旧 volume 信息

测试应明确锁定“一次性消费”语义。

**Step 2: 运行该测试并确认失败**

Run:
```bash
python3 -m unittest tests.test_regressions.PriceMonitorRegressionTests.test_latest_volume_info_is_consumed_once
```

Expected: FAIL，因为当前实现会持续附加旧值。

**Step 3: 以最小改动修复 `monitor/price_monitor.py`**

实现要求：
- 将 `latest_volume_info` 视为一次性展示字段
- 在 `check()` 中消费后立即清空
- 不引入额外时间窗口逻辑
- 如 `monitor/ws_monitor.py` 需要配合调整，仅做最小改动

**Step 4: 运行测试确认通过**

Run:
```bash
python3 -m unittest tests.test_regressions.PriceMonitorRegressionTests.test_latest_volume_info_is_consumed_once
```

Expected: PASS

**Step 5: 运行 PriceMonitor 回归测试组**

Run:
```bash
python3 -m unittest tests.test_regressions.PriceMonitorRegressionTests
```

Expected: PASS

**Step 6: Commit**

```bash
git add tests/test_regressions.py monitor/price_monitor.py monitor/ws_monitor.py
git commit -m "fix: avoid stale volume info in price output"
```

---

### Task 4: 修复 `.env.example` 配置漂移

**Files:**
- Modify: `.env.example`
- Possibly Modify: `README.md`
- Possibly Modify: `DEPLOYMENT.md`
- Test: `tests/test_regressions.py`

**Step 1: 写失败测试，检查示例配置与默认值一致**

在 `tests/test_regressions.py` 中新增轻量测试：
- 读取 `.env.example`
- 断言 `VOLUME_ALERT_COOLDOWN_SECONDS=5`

如果你更倾向于避免硬编码，也可以读取 [common/config.py](common/config.py) 中默认值来源并进行一致性检查，但本轮建议保持测试简单直接。

**Step 2: 运行该测试并确认失败**

Run:
```bash
python3 -m unittest tests.test_regressions.DockerRegressionTests.test_env_example_matches_volume_cooldown_default
```

Expected: FAIL，因为当前 `.env.example` 里是 `60`。

**Step 3: 修复 `.env.example`**

将：
```env
VOLUME_ALERT_COOLDOWN_SECONDS=60
```
改为：
```env
VOLUME_ALERT_COOLDOWN_SECONDS=5
```

**Step 4: 复核 README / DEPLOYMENT 文档**

检查：
- `README.md`
- `DEPLOYMENT.md`

如果它们已经和代码默认值一致，则不要做无意义修改。
如果发现仍有不一致，再做最小同步。

**Step 5: 运行测试确认通过**

Run:
```bash
python3 -m unittest tests.test_regressions.DockerRegressionTests.test_env_example_matches_volume_cooldown_default
```

Expected: PASS

**Step 6: Commit**

```bash
git add tests/test_regressions.py .env.example README.md DEPLOYMENT.md
git commit -m "docs: align example env defaults with runtime config"
```

> 如果 README.md / DEPLOYMENT.md 最终没有改动，提交时不要包含它们。

---

### Task 5: 运行完整回归验证

**Files:**
- Verify only, no code changes required

**Step 1: 运行完整测试套件**

Run:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: 全部 PASS

**Step 2: Spot check 关键行为**

人工复核以下逻辑：
- `common/clients/websocket.py` 中 clean close 不会卡在 CONNECTED
- `bot/app.py` 中 startup 失败 cleanup 路径清晰
- `monitor/price_monitor.py` 中 volume info 为一次性消费
- `.env.example` 与 README / DEPLOYMENT / `common/config.py` 一致

**Step 3: Commit**

```bash
git add tests/test_regressions.py common/clients/websocket.py bot/app.py monitor/price_monitor.py monitor/ws_monitor.py .env.example README.md DEPLOYMENT.md
git commit -m "fix: improve runtime reliability for monitor and bot"
```

> 如果前面已经按任务分次提交，这一步不要再重复提交；改为只做最终验证。

---

## Execution Notes

- 每个任务都保持最小改动，不做 P2/P3 扩展。
- 如果某个测试难以稳定模拟异步边界，先缩小测试目标，不要写脆弱时序测试。
- 优先让测试表达“语义结果”，而不是绑定内部实现细节。
- 如果实现中发现两个 P1 问题天然耦合，先暂停并记录，再决定是否合并任务，但默认应保持独立。

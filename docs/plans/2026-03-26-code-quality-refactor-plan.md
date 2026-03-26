# Code Quality Refactor Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 按低风险、高收益的顺序，收敛测试基础设施重复、运行时生命周期重复、配置入口分散，以及核心运行时类职责过重的问题，在不改变现有产品行为的前提下，提升可维护性与后续重构安全性。

**Architecture:** 保持现有 `common/`、`monitor/`、`bot/` 分层不变，不做大规模重写。优先做边界清理和职责下沉，而不是引入新框架。每个任务按 `@superpowers:test-driven-development` 执行：先补最小失败测试，再做最小实现，再跑局部验证；每个批次结束后做聚合验证。

**Tech Stack:** Python 3.11、unittest、asyncio、requests、aiohttp、websockets、python-telegram-bot、Docker Compose

---

## Batch 1: 低风险高收益基础清理

### Task 1: 收敛重复的测试依赖 stub 与 helper

**Goal:** 把测试里重复定义的第三方依赖 stub 收口到共享 helper，减少 `sys.modules` 污染和多文件漂移。

**Files:**
- Add: `tests/helpers/` 或 `tests/stubs.py`
- Modify: `tests/test_bot_app.py`
- Modify: `tests/test_bot_handlers.py`
- Modify: `tests/test_bot_messages.py`
- Modify: `tests/test_regressions.py`
- Modify: `tests/test_runtime_lifecycle.py`
- Modify: other test files that redefine the same stubs

**Steps:**
1. 先搜索并列出所有重复定义的模块 stub（如 `telegram`、`telegram.ext`、`aiohttp`、`requests`、`websockets`、`dotenv`、`tenacity`）
2. 写一个最小回归，确保共享 helper 被多个测试文件复用后，全量测试仍可稳定运行
3. 提取共享 stub/helper，只保留一份权威定义
4. 批量替换各测试文件中的本地重复实现
5. 跑以下验证：
   ```bash
   python3 -m unittest \
     tests.test_bot_messages \
     tests.test_bot_handlers \
     tests.test_bot_app \
     tests.test_runtime_lifecycle \
     tests.test_regressions
   ```
6. 再跑：
   ```bash
   python3 -m unittest discover -s tests -p 'test_*.py'
   ```

**Acceptance criteria:**
- 不再在多个测试文件中重复维护相同 stub 定义
- 相关测试仍通过
- 不引入新的 import-order 敏感失败

---

### Task 2: 收口配置来源到 `ConfigManager`

**Goal:** 把零散的环境变量读取进一步集中到 `ConfigManager`，降低配置入口分散问题。

**Files:**
- Modify: `common/config.py`
- Modify: `common/notifications.py`
- Modify: `bot/app.py`
- Modify: `monitor/ws_monitor.py`
- Modify: tests covering config and entrypoints

**Steps:**
1. 先搜索所有 `os.getenv(...)` 和直接环境读取点，确认哪些已经被 `ConfigManager` 覆盖，哪些还没有
2. 写最小失败测试，锁定以下配置项从 `ConfigManager` 获取：
   - notifier token/chat id
   - heartbeat path / interval
   - 其他仍散落在 runtime 中的 env 值
3. 在 `ConfigManager` 中新增对应字段和解析逻辑
4. 让调用方改为依赖 `ConfigManager`
5. 跑局部测试：
   ```bash
   python3 -m unittest \
     tests.test_entrypoints \
     tests.test_regressions \
     tests.test_bot_app
   ```
6. 再跑全量测试

**Acceptance criteria:**
- 主要 runtime 配置来源统一
- 运行行为不变
- 没有把敏感信息写入日志

---

### Task 3: 提取 bot/monitor 共享的运行时生命周期 helper

**Goal:** 去掉 bot 和 monitor 之间明显重复的 signal/heartbeat/cleanup 模式，但不改变当前生命周期语义。

**Files:**
- Add: small runtime helper module under `common/` or `common/runtime.py`
- Modify: `bot/app.py`
- Modify: `monitor/ws_monitor.py`
- Modify: related lifecycle tests

**Steps:**
1. 先锁定当前 bot 和 monitor 生命周期行为的测试覆盖点
2. 提取最小共享 helper，只覆盖以下内容：
   - signal setup / restore
   - heartbeat file touch / remove
   - 可复用的 cleanup helper（如果足够小）
3. 先让一侧接入 helper，再让另一侧接入，避免一次改太多
4. 跑局部测试：
   ```bash
   python3 -m unittest \
     tests.test_bot_app \
     tests.test_runtime_lifecycle \
     tests.test_regressions
   ```
5. 再跑全量测试

**Acceptance criteria:**
- bot 与 monitor 生命周期测试全部通过
- 不改变现有 signal 恢复、heartbeat 文件、shutdown 流程行为
- 共享 helper 规模保持很小，不引入框架化抽象

---

## Batch 2: 核心职责拆分

### Task 4: 从 `PriceMonitor` 中提取告警消息渲染

**Goal:** 降低 `PriceMonitor` 的职责密度，让消息渲染不再与状态判断和发送路径强耦合。

**Files:**
- Add or Modify: monitor message/presenter helper (small, explicit)
- Modify: `monitor/price_monitor.py`
- Modify: `tests/test_regressions.py`
- Modify: any focused `PriceMonitor` tests

**Steps:**
1. 先通过测试锁定现有 milestone / volatility / volume 文案不变
2. 把消息构造逻辑提取到独立纯函数或小型 presenter 中
3. `PriceMonitor` 只保留：
   - 状态维护
   - 阈值判断
   - 调用渲染函数
   - 调用通知发送
4. 跑局部测试：
   ```bash
   python3 -m unittest \
     tests.test_regressions.PriceMonitorRegressionTests \
     tests.test_runtime_lifecycle
   ```
5. 再跑全量测试

**Acceptance criteria:**
- 文案和行为不变
- `PriceMonitor` 文件复杂度下降
- 消息渲染更容易独立测试

---

### Task 5: 简化 `ws_monitor.py` 的编排边界

**Goal:** 让 `ws_monitor.py` 更专注于运行时编排，减少与输出/通知细节的耦合。

**Files:**
- Modify: `monitor/ws_monitor.py`
- Possibly Add: small helper module for runtime output/notifications
- Modify: related tests

**Steps:**
1. 先标出 `ws_monitor.py` 中最适合下沉的内容：
   - 生命周期文案
   - heartbeat handling
   - monitor flush/cleanup orchestration if separable
2. 选择一个最小边界先拆，例如先下沉 runtime output/notification message helpers
3. 保持 websocket monitor 主流程清晰：
   - init dependencies
   - start tasks
   - wait/shutdown
   - cleanup
4. 跑局部测试：
   ```bash
   python3 -m unittest \
     tests.test_runtime_lifecycle \
     tests.test_regressions
   ```
5. 再跑全量测试

**Acceptance criteria:**
- `ws_monitor.py` 可读性提升
- 运行行为不变
- 不引入新的 shutdown/reconnect 回归

---

## Batch 3: 中期结构优化

### Task 6: 拆分 WebSocket client 的解析层与连接层

**Goal:** 让 `common/clients/websocket.py` 不再同时承载过多层次的责任。

**Files:**
- Modify: `common/clients/websocket.py`
- Possibly Add: parser/helper module under `common/clients/`
- Modify: websocket regression tests

**Steps:**
1. 先锁定当前消息级容错、clean-close 语义、callback 调用语义
2. 提取消息解析逻辑（ticker/kline payload parsing）到更纯的函数层
3. 保留连接状态、重连、ping/pong 在主 client 中
4. 如果范围允许，再把统计/健康追踪拆出；如果范围过大，留到后续批次
5. 跑局部测试：
   ```bash
   python3 -m unittest \
     tests.test_regressions.BinanceWebSocketClientRegressionTests \
     tests.test_runtime_lifecycle
   ```
6. 再跑全量测试

**Acceptance criteria:**
- 解析逻辑和连接逻辑边界更清晰
- 消息级错误不会影响连接层既有契约
- 测试继续覆盖 clean-close 和 malformed payload 语义

---

### Task 7: 清理 bot handler / messages 的边界

**Goal:** 让 bot presentation 层 API 更一致，避免 `messages.py` 继续承担伪实例方法角色。

**Files:**
- Modify: `bot/messages.py`
- Modify: `bot/handlers.py`
- Modify: `bot/app.py`
- Modify: bot-related tests

**Steps:**
1. 先选一种方向：
   - 纯函数渲染 API
   - 小型 presenter 对象
2. 写回归，锁定当前 bot 输出内容和按钮交互行为
3. 统一 `messages.py` 边界，减少对 `self` 的隐式依赖
4. 跑局部测试：
   ```bash
   python3 -m unittest \
     tests.test_bot_messages \
     tests.test_bot_handlers \
     tests.test_bot_app
   ```
5. 再跑全量测试

**Acceptance criteria:**
- bot presentation API 更清晰
- handler 和 render 的职责边界更容易理解
- 不改变既有文案和回调契约

---

### Task 8: 收尾一致性清理

**Goal:** 清理剩余但价值较高的一致性问题。

**Files:**
- Modify: `common/notifications.py`
- Modify: `monitor/__init__.py`
- Modify: `bot/__init__.py`
- Modify: `monitor/ws_monitor.py`
- Modify: `common/utils.py`
- Modify: `common/logging.py`
- Modify: tests as needed

**Focus areas:**
- 明确 notifier 生命周期 ownership 和 `close()` 路径
- 尽量统一 runtime 输出到 logger，减少 `print` 混用
- 处理 timezone fallback 文档或行为说明
- 避免使用 `logging._nameToLevel` 这类私有 API

**Acceptance criteria:**
- 行为不变
- 运行时输出风格更一致
- 资源关闭责任更明确

---

## Verification Strategy

每个任务完成后：
1. 跑对应局部测试
2. 每个 batch 结束后跑一次聚合验证
3. 全部完成后跑：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

如环境允许，再做手工 spot check：

```bash
python3 -m bot
python3 -m monitor --status
```

---

## Success Criteria

完成后应达到：
- 测试基础设施重复显著减少
- runtime 生命周期重复减少
- 配置来源更统一
- `PriceMonitor` 与 `ws_monitor.py` 可读性和职责边界明显改善
- WebSocket client 更容易理解和维护
- 全量测试持续通过

---

## Notes

- 优先完成 Batch 1，再考虑 Batch 2/3
- 不要在一个任务里同时做两类结构变更
- 不做大规模重写；目标是“更容易继续改”，不是“更漂亮”
- 每一步都应以现有回归测试为护栏

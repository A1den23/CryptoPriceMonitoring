# P1 可靠性修复设计

## 背景

根据 [REVIEW.md](../../REVIEW.md) 的审查结论，当前项目最值得优先修复的问题集中在 P1 级别，主要涉及运行时可靠性、状态语义正确性和配置一致性。这些问题不会立即导致代码无法运行，但会影响监控系统的恢复速度、Bot 启动失败时的清理完整性、终端输出的真实性，以及新部署实例的配置行为。

本设计只覆盖 P1 问题，不扩展到 P2/P3，以确保本轮改动范围明确、验证成本可控。

## 目标

本轮修复聚焦以下四项：

1. 修复 WebSocket 正常关闭后可能不会立即重连的问题
2. 修复 Telegram Bot 启动失败路径 cleanup 不完整的问题
3. 修复 `latest_volume_info` 状态陈旧残留的问题
4. 修复 `.env.example` 与代码/README 默认值漂移的问题

## 非目标

以下问题明确不在本轮范围内：

- callback 改为优先编辑原消息
- 异步 HTTP timeout 纳入 retry
- 无效时区 warning / fail-fast 策略
- Telegram token 异常日志脱敏增强
- 容器进一步加固
- `TZ` / `UTC8` 导出清理

## 设计原则

### 1. 小步修复
每个 P1 问题独立成单独任务块处理，避免一轮改动中同时重构多个子系统。

### 2. 先测后改
优先为问题建立失败测试，再做最小实现改动，最后通过回归测试确认行为稳定。

### 3. 保持现有结构
继续沿用当前 `common/`、`monitor/`、`bot/` 的模块边界，不引入额外抽象层。

### 4. 语义优先于“顺手优化”
本轮只修复已确认问题，不附带“顺便优化”其它可疑点，避免计划外扩张。

## 修复方案

## 一、WebSocket reconnect 语义修复

### 现状问题
在 [common/clients/websocket.py](../../common/clients/websocket.py) 中，如果消息循环是“正常结束”而不是异常关闭，当前实现可能不会立即把状态切到 reconnect 语义，而是依赖后续 watchdog 超时来间接触发恢复。

### 目标行为
- 任何非主动停止下的消息循环退出，都应被视为连接已丢失。
- 应尽快进入 reconnect 路径，而不是等待消息超时补救。
- 主动 stop 时，不应误触发 reconnect。

### 设计思路
- 调整 `_message_handler()` 在正常退出时的状态处理逻辑。
- 保持现有状态机结构，不额外引入新状态。
- 用最小改动让 `start()` 主循环能及时观察到状态变化并进入重连逻辑。

### 需要验证的行为
- 正常关闭的 websocket 会触发 reconnect 语义。
- `stop()` 流程中的关闭不会导致误告警或误重连。

## 二、Bot 启动失败 cleanup 修复

### 现状问题
在 [bot/app.py](../../bot/app.py) 中，heartbeat task 的创建时机早于完整 startup 结束，而 `initialize()` / `start()` / `start_polling()` 失败时，不一定能进入完整 cleanup。

### 目标行为
- 任一启动阶段失败，heartbeat task 都应被取消。
- application / updater 应根据已完成阶段执行对应清理。
- 启动失败后不留下半初始化状态。

### 设计思路
- 将 startup 过程纳入统一的异常清理范围。
- 尽量复用现有 shutdown 结构，不新增复杂生命周期管理器。
- cleanup 逻辑应能处理“部分启动成功”的中间状态。

### 需要验证的行为
- `initialize()` 抛错时不会残留 heartbeat task。
- `start_polling()` 抛错时会执行必要的 stop / shutdown。

## 三、成交量陈旧状态修复

### 现状问题
在 [monitor/price_monitor.py](../../monitor/price_monitor.py) 和 [monitor/ws_monitor.py](../../monitor/ws_monitor.py) 中，`latest_volume_info` 会被后续多个 ticker 输出长期复用，导致用户看到过期成交量信息。

### 目标行为
- 终端中显示的成交量信息必须代表“当前最近一次有效展示状态”。
- 不允许一个旧的 volume 标记在多个无关价格输出中长期残留。

### 设计思路
本轮采用最小、最清晰的策略：

- `latest_volume_info` 作为“一次性展示字段”使用
- 在被价格输出消费后清空

### 取舍说明
相比“加时间戳+有效窗口”，一次性消费更简单：
- 逻辑更直观
- 测试更稳定
- 不需要额外定义时间窗口语义

## 四、配置示例漂移修复

### 现状问题
`.env.example` 中的 `VOLUME_ALERT_COOLDOWN_SECONDS=60` 与代码默认值、README、DEPLOYMENT 文档不一致。

### 目标行为
- 示例配置、代码默认值、文档描述保持一致。
- 新用户通过 `cp .env.example .env` 后得到与预期一致的默认行为。

### 设计思路
- 以代码默认值为主基准。
- 修正 `.env.example`。
- 顺手复核 README / DEPLOYMENT 是否仍一致，但仅在发现真实不一致时做最小同步。

## 测试策略

本轮只新增与 P1 直接相关的最小回归测试：

1. WebSocket 正常关闭后的 reconnect 行为
2. Bot 启动失败的 cleanup 行为
3. `latest_volume_info` 的一次性消费行为
4. `.env.example` 与默认值一致性检查

不在本轮引入大规模集成测试或真实外部依赖测试。

## 影响范围

预计影响文件：

- [common/clients/websocket.py](../../common/clients/websocket.py)
- [bot/app.py](../../bot/app.py)
- [monitor/price_monitor.py](../../monitor/price_monitor.py)
- [monitor/ws_monitor.py](../../monitor/ws_monitor.py)
- [tests/test_regressions.py](../../tests/test_regressions.py)
- [.env.example](../../.env.example)
- 视实际复核情况可能涉及：
  - [README.md](../../README.md)
  - [DEPLOYMENT.md](../../DEPLOYMENT.md)

## 风险控制

- 每个问题都先补失败测试，再改实现。
- 每个问题修完后都运行对应局部测试。
- 最后运行完整回归测试：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 预期结果

修复完成后，项目应具备以下改进：

- WebSocket 连接关闭后的恢复路径更及时、更可预期
- Bot 启动失败不会留下残余任务或半初始化状态
- 终端输出中的成交量信息不再误导用户
- 配置示例与代码/文档行为一致

## 后续衔接

本设计确认后，下一步生成对应的 implementation plan，并按 TDD 小步执行。
# Crypto Price Monitoring Bot

实时监控多个加密货币价格，通过 Telegram 发送告警，并提供交互式 Bot 查询。

## 功能概览

- WebSocket 实时行情（Binance）
- 多币种监控（由 `COIN_LIST` 决定）
- 三类告警：价格里程碑、波动率、成交量异常
- 自动重连、心跳保活、优雅停机
- 支持 Docker 一键运行 `monitor + bot`

## 快速开始

### 1. 安装依赖（本地）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

至少填写：

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. 启动监控

主入口统一使用模块方式：

```bash
python3 -m monitor
```

常用参数：

```bash
python3 -m monitor --status
python3 -m monitor --test
python3 -m monitor --help
```

### 4. 启动交互式 Bot

主入口统一使用模块方式：

```bash
python3 -m bot
```

常用查询方式：

```text
/price - 弹出正在监控的币种选择器
/price BTC - 直接查询 BTC 详情
```

说明：

- 顶层 `monitor.py` / `bot.py` 仅保留为兼容包装层，不再作为文档中的主入口
- 主要实现已拆分到 `monitor/` 与 `bot/` 包中

### 5. 运行测试

官方测试命令：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

说明：

- 该命令是当前仓库约定的稳定测试入口
- 文档中的本地测试示例统一以 `python3 -m unittest` 为准，不再额外约定 `pytest` 入口
- `python3 -m unittest tests.test_regressions` 也可以运行相同回归测试
- 包级导入已改为惰性加载，`python3 -m unittest` 在缺少可选三方库时也不会因导入阶段提前失败
- 若 `.env` 已配置完成，建议同时补跑以下真实入口检查：

```bash
python3 -m monitor --status
python3 -m bot
```

## Docker 运行

```bash
# 启动全部服务（monitor + bot）
docker compose up -d --build

# 查看日志
docker compose logs -f

# 查看状态
docker compose ps

# 停止
docker compose down
```

详细部署见 `DEPLOYMENT.md`。

## 配置说明

### 全局配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEBUG` | 是否开启调试 | `false` |
| `LOG_LEVEL` | 日志级别（`DEBUG/INFO/WARNING/ERROR`） | `INFO` |
| `TIMEZONE` | 时区（如 `Asia/Shanghai`, `UTC`, `America/New_York`） | `Asia/Shanghai` |
| `COIN_LIST` | 逗号分隔的币种名 | `BTC,ETH,SOL,USD1` |

说明：`TIMEZONE` 优先使用 `zoneinfo` 解析；若运行环境缺少对应时区数据，则会退回到 fixed-offset fallback。该 fallback 为近似行为，DST（夏令时）切换期间可能不完全准确。
| `MILESTONE_ALERT_COOLDOWN_SECONDS` | 里程碑告警冷却（秒） | 600 |
| `VOLATILITY_ALERT_COOLDOWN_SECONDS` | 波动告警冷却（秒） | 60 |
| `VOLUME_ALERT_COOLDOWN_SECONDS` | 成交量告警冷却（秒） | 5 |
| `WS_PING_INTERVAL_SECONDS` | WebSocket 心跳间隔 | 30 |
| `WS_PONG_TIMEOUT_SECONDS` | WebSocket 心跳超时 | 10 |
| `WS_MESSAGE_TIMEOUT_SECONDS` | WebSocket 消息超时（无数据断开） | 120 |
| `BOT_HEARTBEAT_INTERVAL_SECONDS` | Bot 心跳文件更新间隔 | 30 |
| `STABLECOIN_DEPEG_MONITOR_ENABLED` | 是否启用稳定币脱锚监控 | `true` |
| `STABLECOIN_DEPEG_TOP_N` | 监控市值前 N 个稳定币 | `25` |
| `STABLECOIN_DEPEG_THRESHOLD_PERCENT` | 偏离 $1 的告警阈值百分比 | `5.0` |
| `STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS` | DefiLlama 轮询间隔（秒） | `60` |
| `STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS` | 同一稳定币重复告警冷却（秒） | `300` |

### 每个币种配置

以 `BTC` 为例：

- `BTC_ENABLED`
- `BTC_SYMBOL`（如 `BTCUSDT`）
- `BTC_INTEGER_THRESHOLD`
- `BTC_VOLATILITY_PERCENT`
- `BTC_VOLATILITY_WINDOW_SECONDS`
- `BTC_VOLUME_ALERT_MULTIPLIER`

示例：

```env
COIN_LIST=BTC,ETH,SOL,USD1

BTC_ENABLED=true
BTC_SYMBOL=BTCUSDT
BTC_INTEGER_THRESHOLD=1000
BTC_VOLATILITY_PERCENT=5.0
BTC_VOLATILITY_WINDOW_SECONDS=180
BTC_VOLUME_ALERT_MULTIPLIER=10.0
```

## 告警触发逻辑（精简版）

### 1) 价格里程碑告警

触发条件：

- 当前价格跨越了新的里程碑档位（不是“接近”）
- 通过里程碑冷却时间限制

说明：

- 大阈值（`>=1`）按整数档位判断
- 小阈值（`<1`）按 `floor` 档位判断，避免提前触发

### 2) 波动告警

在 `VOLATILITY_WINDOW_SECONDS` 滑动窗口内，任一条件满足会触发：

- 标准差占比 `>= 阈值 * 0.7`
- 累计波动 `>= 阈值` 且较上次累计更高
- 区间波动 `>= 阈值`
- 加速度 `>= 2.0` 且标准差占比 `>= 阈值 * 0.3`

同时受 `VOLATILITY_ALERT_COOLDOWN_SECONDS` 限制。

### 3) 成交量异常告警

触发流程：

- 仅在 `1m kline` 收盘（`is_closed=true`）时计算
- 使用最近 `VOLATILITY_WINDOW_SECONDS` 的滑动窗口
- 至少 3 个点才开始判定
- 基线成交量 = 前 `N-1` 点平均值
- 最新倍率 = 最新成交量 / 基线成交量
- 当倍率 `>= {coin}_VOLUME_ALERT_MULTIPLIER` 触发告警
- 受 `VOLUME_ALERT_COOLDOWN_SECONDS` 限制

### 4) 稳定币脱锚告警

触发条件：

- 仅在 `STABLECOIN_DEPEG_MONITOR_ENABLED=true` 时启用
- 从 DefiLlama 轮询市值前 `STABLECOIN_DEPEG_TOP_N` 个稳定币
- 默认在偏离 $1 达到 `±5%` 时触发告警，可通过 `STABLECOIN_DEPEG_THRESHOLD_PERCENT` 调整
- 同一稳定币在持续脱锚期间受 `STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS` 限制
- 回到正常区间后清除脱锚状态（默认阈值下为 `[0.95, 1.05]`）

说明：

- 该功能独立于 `COIN_LIST`，启用时无需把稳定币加入 `COIN_LIST`
- 默认 ±5%，实际阈值由 `STABLECOIN_DEPEG_THRESHOLD_PERCENT` 控制
- 修改上述环境变量后需要重启服务

## 常用运维命令

```bash
# 实时日志
docker compose logs -f

# 重启单服务
docker compose restart crypto-monitor
docker compose restart crypto-bot

# 进入容器
docker compose exec crypto-monitor bash
docker compose exec crypto-bot bash
```

## 故障排查

### 收不到 Telegram 消息

- 检查 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
- 确认你已和 Bot 发过消息（或 `/start`）
- 查看日志：`docker compose logs -f crypto-monitor`

### WebSocket 连接失败

- 检查服务器网络
- 测试 Binance 连通性：`curl -I https://api.binance.com`
- 查看日志中是否持续重连

### 配置不生效

- 确认改的是 `.env` 不是 `.env.example`
- 重启服务使配置生效

### 时间显示不正确

- 检查 `TIMEZONE` 配置，支持常见时区如 `Asia/Shanghai`, `UTC`, `America/New_York`, `Europe/London`
- 修改后需重启服务

## 最近更新

### 当前工作区变更（2026-03-25）

- **结构重构**：
  - `monitor.py` 已拆分为 `monitor/` 包（`price_monitor.py`、`ws_monitor.py`、`__main__.py`）
  - `bot.py` 已拆分为 `bot/` 包（`app.py`、`handlers.py`、`messages.py`、`__main__.py`）
  - 顶层 `monitor.py` / `bot.py` 保留为兼容包装层，继续支持 `python monitor.py` / `python bot.py`
- **运行入口**：
  - 主入口统一为 `python -m monitor` 与 `python -m bot`
  - 顶层 `monitor.py` / `bot.py` 仅保留为兼容包装层
- **通知可靠性**：
  - `TelegramNotifier` 现在要求 Telegram API 显式返回 `ok=true` 才视为发送成功
  - 对 malformed JSON、缺少 `ok`、`ok=false` 的响应会明确返回失败，不再误判为成功
  - `test_connection()` 不再把可能带 token 的异常文本写入日志
- **告警状态语义**：
  - `PriceMonitor` 的里程碑 / 波动 / 成交量告警状态只在发送成功后推进
  - `StablecoinDepegMonitor` 的 `last_alert_time` 与 `alerts` 只在真实发送成功后更新
- **生命周期与停机**：
  - 优雅停机时会 drain 未完成的通知任务
  - `WebSocketMultiCoinMonitor` 增强了取消安全 cleanup、信号注册/恢复和 ws 级通知任务管理
- **结构收敛**：
  - `TelegramBot` 已移除 monkey-patch 组装方式，改为显式方法包装
  - `common` / `bot` / `monitor` 的包级导出已收敛，内部导入更显式
- **日志与部署契约**：
  - 文件日志已切换为轮转策略，避免无限增长
  - `.dockerignore` 现已排除 `.venv/`
  - Dockerfile、Compose、README、DEPLOYMENT 已统一到 `python -m monitor` / `python -m bot` 主入口
- **验证结果**：
  - `python3 -m unittest discover -s tests -p 'test_*.py'` 通过（93 tests）
  - `python3 -m monitor --status` 已通过真实运行验证
  - `python3 -m bot` 已通过真实启动 / 优雅停机验证
  - `docker compose build` 已通过

```
.
├── monitor/                     # 监控包
│   ├── __init__.py             # 对外导出与 CLI 入口
│   ├── __main__.py             # 支持 `python -m monitor`
│   ├── price_monitor.py        # PriceMonitor 与价格/波动/成交量逻辑
│   └── ws_monitor.py           # WebSocketMultiCoinMonitor
├── bot/                         # Telegram Bot 包
│   ├── __init__.py             # 对外导出与 CLI 入口
│   ├── __main__.py             # 支持 `python -m bot`
│   ├── app.py                  # TelegramBot 生命周期与应用装配
│   ├── handlers.py             # 命令与按钮回调
│   └── messages.py             # 消息与按钮渲染
├── common/                      # 共享模块包
│   ├── __init__.py             # 导出公共 API
│   ├── config.py               # 配置管理 (ConfigManager, CoinConfig)
│   ├── logging.py              # 日志工具
│   ├── utils.py                # 工具函数 (时区、价格格式化、币符号)
│   ├── notifications.py        # Telegram 通知器
│   └── clients/
│       ├── http.py             # HTTP API 客户端
│       └── websocket.py        # WebSocket 客户端
├── monitor.py                   # 兼容入口（转发到 `monitor` 包）
├── bot.py                       # 兼容入口（转发到 `bot` 包）
├── docker-compose.yml           # 容器编排
├── Dockerfile                   # 镜像构建
└── requirements.txt             # Python 依赖
```

## License

MIT

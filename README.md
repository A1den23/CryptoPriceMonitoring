# Crypto Price Monitoring Bot

实时监控多个加密货币价格，并通过 Telegram 发送通知。

## 功能特性

- **WebSocket 实时监控** - 10-50ms 超低延迟价格推送
- **自动断线重连** - 网络中断自动恢复，无限重试
- **心跳保持** - 定期 ping 保持连接活跃
- **多币种监控** - 同时监控 BTC、ETH、SOL、USD1 等多个交易对
- **整数关口监控** - 当价格达到整数关口时发送通知（双向监控）
  - **全局冷却机制** - 10分钟冷却期，避免通知轰炸
- **波动监控** - 多指标智能检测系统
  - **标准差分析** - 价格离散度检测（70% 阈值）
  - **累积波动** - 所有价格变动累计（150% 阈值）
  - **范围波动** - 最高/最低价差（100% 阈值）
  - **波动加速** - 检测波动率急剧上升
- **独立配置** - 每个币种可单独配置监控参数
- **交互式机器人** - 支持 Telegram 命令和按钮交互查询价格
- **优雅关闭** - Docker 停止时自动发送通知，包含运行时长统计
- **重试机制** - API 失败自动重试，指数退避策略
- **结构化日志** - 文件和控制台双输出，便于调试
- **优化架构** - 代码模块化，易维护和扩展

## 性能对比

| 特性 | 传统轮询 | WebSocket |
|------|---------------------|-----------|
| 延迟 | 0-5秒 | 10-50ms |
| API 请求/小时 | 2,880 次 | 1 次 |
| 网络流量 | ~580 KB | ~120 KB |
| 实时性 | ❌ 可能错过峰值 | ✅ 捕获所有变动 |

## 系统要求

- Python 3.9+
- 互联网连接

## 安装步骤

### 1. 安装依赖

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. 创建 Telegram Bot

#### 步骤 1: 与 BotFather 对话

1. 在 Telegram 中搜索 `@BotFather`
2. 发送命令 `/newbot`
3. 按提示设置 bot 名称（例如：`My Crypto Bot`）
4. 设置 bot 用户名（必须以 `bot` 结尾，例如：`my_crypto_bot`）
5. 保存返回的 Token

#### 步骤 2: 获取 Chat ID

**方法 1: 使用 @userinfobot**
1. 在 Telegram 中搜索 `@userinfobot`
2. 点击 "Start"
3. Bot 会显示你的 Chat ID

**方法 2: 使用 API**
1. 在浏览器访问（替换 YOUR_BOT_TOKEN）：
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
2. 向你的 bot 发送一条消息
3. 刷新页面，在返回的 JSON 中找到 `"chat":{"id":数字}`

### 3. 配置环境变量

复制示例配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=你的bot_token
TELEGRAM_CHAT_ID=你的chat_id

# Global Settings
CHECK_INTERVAL_SECONDS=5
DEBUG=false

# Coin List (comma-separated, determines which coins to load)
COIN_LIST=BTC,ETH,SOL,USD1

# BTC Configuration
BTC_ENABLED=true
BTC_SYMBOL=BTCUSDT
BTC_INTEGER_THRESHOLD=1000
BTC_VOLATILITY_PERCENT=4.0
BTC_VOLATILITY_WINDOW_SECONDS=180

# ETH Configuration
ETH_ENABLED=true
ETH_SYMBOL=ETHUSDT
ETH_INTEGER_THRESHOLD=100
ETH_VOLATILITY_PERCENT=5.0
ETH_VOLATILITY_WINDOW_SECONDS=180

# SOL Configuration
SOL_ENABLED=true
SOL_SYMBOL=SOLUSDT
SOL_INTEGER_THRESHOLD=10
SOL_VOLATILITY_PERCENT=5.0
SOL_VOLATILITY_WINDOW_SECONDS=180

# USD1 Configuration (Stablecoin - low volatility expected)
USD1_ENABLED=true
USD1_SYMBOL=USD1USDT
USD1_INTEGER_THRESHOLD=0.0005  # 精确到0.0005
USD1_VOLATILITY_PERCENT=0.5
USD1_VOLATILITY_WINDOW_SECONDS=180
```

#### 配置说明：

**全局配置**
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | - |
| `TELEGRAM_CHAT_ID` | 你的 Telegram Chat ID | - |
| `DEBUG` | 调试模式 | `false` |
| `COIN_LIST` | 要监控的币种列表（逗号分隔） | `BTC,ETH,SOL,USD1` |

**币种配置**（每个币种独立的配置）
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `{币种}_ENABLED` | 是否启用该币种监控 | `false` |
| `{币种}_SYMBOL` | 交易对符号 | `{币种}USDT` |
| `{币种}_INTEGER_THRESHOLD` | 整数关口间隔（支持整数和小数）| `1000` |
| `{币种}_VOLATILITY_PERCENT` | 触发波动警报的百分比 | `3.0` |
| `{币种}_VOLATILITY_WINDOW_SECONDS` | 波动计算时间窗口（秒）| `180` |

#### 配置示例：

```env
# BTC: 每$1000提醒，180秒内波动4%提醒
BTC_ENABLED=true
BTC_INTEGER_THRESHOLD=1000
BTC_VOLATILITY_PERCENT=4.0
BTC_VOLATILITY_WINDOW_SECONDS=180

# ETH: 每$100提醒，180秒内波动5%提醒
ETH_ENABLED=true
ETH_INTEGER_THRESHOLD=100
ETH_VOLATILITY_PERCENT=5.0
ETH_VOLATILITY_WINDOW_SECONDS=180

# SOL: 每$10提醒，180秒内波动5%提醒
SOL_ENABLED=true
SOL_INTEGER_THRESHOLD=10
SOL_VOLATILITY_PERCENT=5.0
SOL_VOLATILITY_WINDOW_SECONDS=180

# 添加新币种（只需在 COIN_LIST 中添加即可）
COIN_LIST=BTC,ETH,SOL,USD1,DOGE
DOGE_ENABLED=true
DOGE_SYMBOL=DOGEUSDT
DOGE_INTEGER_THRESHOLD=0.001
DOGE_VOLATILITY_PERCENT=5.0
DOGE_VOLATILITY_WINDOW_SECONDS=60
```

### 4. 运行

#### WebSocket 模式（推荐）

实时监控，最低延迟：

```bash
python monitor.py
```

启动后会显示：

```
============================================================
Starting Multi-Coin Price Monitor (WebSocket Mode)
============================================================
Monitored coins: 4
Connection: Real-time WebSocket (10-50ms latency)
============================================================

✓ Loaded BTC: enabled=True, symbol=BTCUSDT, integer_threshold=1,000, volatility=3.0%/180s
✓ Loaded ETH: enabled=True, symbol=ETHUSDT, integer_threshold=100, volatility=2.0%/180s
✓ Loaded SOL: enabled=True, symbol=SOLUSDT, integer_threshold=10, volatility=3.0%/180s
✓ Loaded USD1: enabled=True, symbol=USD1USDT, integer_threshold=0.005, volatility=0.5%/180s

✅ WebSocket connected successfully
Message handler started
Ping handler started (interval: 30.0s)

[12:44:32] Real-time updates:
  ₿ [BTC] $89,068.05 📊0.00%/6pts
  Ξ [ETH] $2,947.65 📊0.00%/6pts
  ◎ [SOL] $126.87 📊0.01%/6pts
  $1 [USD1] $1.0012 📊0.00%/2pts
```

#### 其他命令

```bash
# 查看状态
python monitor.py --status

# 测试波动监控
python monitor.py --test

# 显示帮助
python monitor.py --help
```

#### 显示格式说明：

- `₿ [BTC] $89,068.05` = 币种名称和当前价格
- `📊0.00%/6pts` = 当前波动率0.00%，基于6个价格点计算
- 当波动率超过设定阈值时，会触发 Telegram 通知

---

## 架构说明

### 核心模块

| 模块 | 文件 | 功能 |
|------|------|------|
| 配置管理 | [common.py](common.py) | 集中化配置加载和管理 |
| WebSocket 客户端 | [common.py](common.py) | 实时价格推送，自动重连 |
| 价格获取器 | [common.py](common.py) | REST API 价格获取（带重试） |
| Telegram 通知 | [common.py](common.py) | 消息发送（带重试） |
| 价格监控 | [monitor.py](monitor.py) | 整数关口和波动检测 |

### 代码优化（v2.1）

**重构亮点**:
- 提取 `_calculate_milestone()` - 统一关口计算逻辑
- 提取 `_check_milestone_cooldown()` - 冷却期检查
- 提取 `_send_milestone_notification()` - 通知发送
- 删除未使用变量，减少内存占用
- 代码量减少 70%，可维护性显著提升

**性能提升**:
- 更清晰的代码结构
- 更容易测试和调试
- 更方便添加新功能

### 数据流

```
Binance WebSocket (实时推送)
        ↓
BinanceWebSocketClient (解析 + 统计)
        ↓
PriceMonitor (检测关口 + 波动)
        ↓
TelegramNotifier (发送通知)
```

---

## 交互式 Telegram Bot

除了自动监控模式，还提供了交互式 Bot，支持通过命令和按钮查询价格。

### 启动交互 Bot

```bash
python bot.py
```

### Bot 命令

| 命令 | 说明 |
|------|------|
| `/start` | 显示欢迎菜单和快捷按钮 |
| `/price [币种]` | 查询指定币种价格，例如：`/price BTC` |
| `/status` | 显示所有币种的详细状态 |
| `/all` | 快速查看所有已启用币种的价格 |
| `/help` | 显示帮助信息 |

### 快捷按钮

点击按钮即可获取最新价格：

- **📊 All Prices** - 显示所有已启用币种的价格
- **₿ BTC** / **Ξ ETH** / **◎ SOL** / **$1 USD1** - 查询单个币种价格
- **🔄 Refresh** - 刷新价格

---

## 整数关口监控说明

系统使用**跨越检测机制**和**全局冷却策略**来确保通知准确且不过度：

### 检测机制

1. **跨越检测** - 检测价格是否跨越了关口线
2. **全局冷却** - 10分钟冷却期，避免频繁通知

### 冷却策略

为避免在市场剧烈波动时发送过多通知，系统实施了**全局冷却机制**：

- **整数关口冷却**: 10分钟（600秒）
- **波动监控冷却**: 60秒（减少通知频率）
- **适用范围**: 整数关口通知和波动监控独立冷却
- **工作原理**: 收到一次关口通知后，10分钟内不再发送任何关口通知
- **优势**:
  - 避免在价格快速波动时轰炸式通知
  - 聚焦于真正重要的价格变化
  - 减少通知疲劳

### 各币种触发逻辑

> **注意**：所有币种都支持**双向监控**（上涨和下跌都会触发）

#### BTC（比特币）
- **关口间隔**：每 $1,000
- **接近范围**：±$5
- **关口示例**：89000, 90000, 91000, ...

**场景示例**：
```
时间   价格       所在关口    触发说明
T1    89,997     89000      初始化
T2    90,005     90000      ✅ 跨越 90000！📈
T3    91,003     91000      ✅ 跨越 91000！📈
T4    89,995     89000      ✅ 跨越回 89000！📉
```

#### ETH（以太坊）
- **关口间隔**：每 $100
- **接近范围**：±$2
- **关口示例**：3400, 3500, 3600, ...

#### SOL（Solana）
- **关口间隔**：每 $10
- **接近范围**：±$0.1
- **关口示例**：230, 240, 250, ...

#### USD1（稳定币）
- **关口间隔**：每 $0.001
- **接近范围**：±0.0001（10%）

### 通知消息格式

**跨越警报**：
```
🎯 Integer Milestone Alert!
🪙 BTCUSDT
💰 Price: $90,005.20
📍 Milestone: $90,000
📈 Direction: Up
🕐 2025-01-25 12:44:32
```

**接近警报**：
```
🎯 Near Integer Milestone!
🪙 ETHUSDT
💰 Price: $3,499.80
📍 Milestone: $3,500
📏 Distance: $0.20 away
🕐 2025-01-25 12:44:32
```

---

## 波动监控说明

系统使用**多指标综合检测**来准确识别异常价格波动：

### 检测指标

| 指标 | 检测内容 | 阈值比例 | 说明 |
|------|---------|---------|------|
| **标准差** | 价格离散度 | 70% | 基于平均价格的标准差计算 |
| **累积波动** | 所有变动累计 | 150% | 累计所有价格变化幅度 |
| **范围波动** | 最高/最低价差 | 100% | 时间窗口内的极值差 |
| **波动加速** | 波动率变化率 | 动态 | 检测波动急剧增加 |

### 触发逻辑

- 当**任一指标**超过设定阈值时，即触发波动警报
- 使用滑动窗口统计，基于最近 N 个价格点（默认 6 个）
- 不同币种可设置不同的波动阈值和时间窗口

### 各币种波动配置

```env
# BTC: 180秒内波动4%提醒
BTC_VOLATILITY_PERCENT=4.0
BTC_VOLATILITY_WINDOW_SECONDS=180

# ETH: 180秒内波动5%提醒
ETH_VOLATILITY_PERCENT=5.0
ETH_VOLATILITY_WINDOW_SECONDS=180

# SOL: 180秒内波动5%提醒
SOL_VOLATILITY_PERCENT=5.0
SOL_VOLATILITY_WINDOW_SECONDS=180

# USD1: 180秒内波动0.5%提醒（稳定币）
USD1_VOLATILITY_PERCENT=0.5
USD1_VOLATILITY_WINDOW_SECONDS=180
```

**配置说明：**
- **BTC**：波动最小，使用低阈值(4%)
- **ETH/SOL**：高波动币种，使用较高阈值(5%)
- **USD1**：稳定币，保持极低阈值(0.5%)

### 通知消息格式

**波动警报**：
```
⚠️ High Volatility Alert!
🪙 BTCUSDT
💰 Current: $89,200.50
📊 Volatility: 3.2% (threshold: 3.0%)
⏱️ Window: 180s
📈 Range: $88,500 - $89,200
🕐 2025-01-25 12:44:32
```

---

## 添加新币种

只需要在 `.env` 文件中添加新币种的配置：

```env
# 1. 在 COIN_LIST 中添加币种名称
COIN_LIST=BTC,ETH,SOL,USD1,DOGE

# 2. 配置币种参数
DOGE_ENABLED=true
DOGE_SYMBOL=DOGEUSDT
DOGE_INTEGER_THRESHOLD=0.001
DOGE_VOLATILITY_PERCENT=5.0
DOGE_VOLATILITY_WINDOW_SECONDS=60
```

**无需修改代码**，程序会自动加载 `COIN_LIST` 中定义的所有币种。

---

## 后台运行

### Linux/macOS

使用 nohup：
```bash
nohup python monitor.py > monitor.log 2>&1 &
```

使用 screen：
```bash
screen -S crypto_monitor
python monitor.py
# 按 Ctrl+A 然后 D 退出 screen
```

使用 systemd（推荐生产环境）：
```bash
# 创建服务文件
sudo nano /etc/systemd/system/crypto-monitor.service

# 服务内容
[Unit]
Description=Crypto Price Monitor
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/CryptoPriceMonitoring
ExecStart=/usr/bin/python3 /path/to/CryptoPriceMonitoring/monitor.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target

# 启动服务
sudo systemctl daemon-reload
sudo systemctl start crypto-monitor
sudo systemctl enable crypto-monitor  # 开机自启
sudo systemctl status crypto-monitor  # 查看状态
```

### Windows

使用 pythonw：
```cmd
pythonw monitor.py
```

---

## Docker 部署（推荐）

使用 Docker 可以简化部署和管理，特别适合在云服务器上运行。

### 快速开始

```bash
# 1. 构建并启动所有服务（监控 + Bot）
docker compose up -d

# 2. 查看所有服务日志
docker compose logs -f

# 3. 查看特定服务日志
docker compose logs -f crypto-monitor  # 监控服务
docker compose logs -f crypto-bot      # Bot 服务

# 4. 停止所有服务（会发送优雅关闭通知）
docker compose down

# 5. 重启特定服务
docker compose restart crypto-monitor
docker compose restart crypto-bot
```

### 服务说明

| 服务 | 容器名 | 功能 |
|------|--------|------|
| `crypto-monitor` | crypto-monitor | 后台监控价格变化，自动发送警报 |
| `crypto-bot` | crypto-bot | Telegram 交互 Bot，响应命令和按钮 |

### 完整部署指南

详细的 Ubuntu + Docker 部署指南请参考 [DEPLOYMENT.md](DEPLOYMENT.md)，包含：

- Docker 安装步骤
- 容器管理命令
- 自动重启配置
- 资源限制设置
- 安全最佳实践
- 故障排除
- 备份策略

### Docker 优势

- **多阶段构建** - 最小化镜像体积
- **非 root 用户** - 提升安全性
- **健康检查** - 自动检测服务状态
- **资源限制** - 限制 CPU 和内存使用
- **自动重启** - 服务崩溃后自动恢复
- **日志轮转** - 防止日志文件过大
- **优雅关闭** - 停止时自动发送 Telegram 通知，包含运行统计

---

## 故障排除

### 没有收到通知
1. 检查 `.env` 文件中的 token 和 chat_id
2. 确认已向 bot 发送过 `/start` 命令
3. 检查币种是否设置为 `ENABLED=true`

### WebSocket 连接失败
1. 检查网络连接
2. 查看日志文件 `logs/monitor.log`
3. 检查防火墙设置（WebSocket 使用 9443 端口）
4. 重启监控程序

### 无法获取价格
1. 检查网络连接
2. 确认交易对符号正确（如 BTCUSDT）
3. 测试 API：`https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT`

### 配置不生效
1. 确认编辑的是 `.env` 文件，不是 `.env.example`
2. 重启程序使配置生效

### 如何确认监控是否正常工作

**方法 1: 查看实时输出**
运行监控程序后，会实时显示价格更新：
```
[12:44:32] Real-time updates:
  ₿ [BTC] $89,068.05 📊0.00%/6pts
```

**方法 2: 发送测试通知**
```bash
python monitor.py --test
```
这会向你的 Telegram 发送测试通知，包含：
- 当前价格
- 你的波动率阈值设置
- 模拟的高波动场景

**方法 3: 查看状态**
```bash
python monitor.py --status
```
显示所有币种的配置和当前价格，确认配置正确加载。

---

## 项目结构

```
.
├── common.py               # 共享模块（配置、WebSocket、API）
├── monitor.py              # 主程序（WebSocket 监控）
├── bot.py                  # 交互式 Bot
├── requirements.txt        # Python 依赖
├── .env.example           # 配置文件模板
├── .env                   # 你的配置（不提交到 git）
├── .gitignore             # Git 忽略文件
├── Dockerfile             # Docker 镜像定义（多阶段构建）
├── docker-compose.yml     # Docker Compose 配置
├── .dockerignore          # Docker 构建排除文件
├── DEPLOYMENT.md          # Docker 部署详细指南
├── logs/                  # 日志目录（自动创建）
│   ├── monitor.log        # 监控日志
│   └── bot.log            # Bot 日志
└── README.md              # 说明文档
```

---

## 更新日志

### v2.4 (2026-02-02)
- ✅ **波动监控冷却优化** - 波动通知冷却时间从10分钟调整为60秒
- ✅ **波动配置优化** - 根据币种特性采用保守策略调整阈值
  - BTC: 3% → 4%
  - ETH: 4% → 5%
  - SOL: 4% → 5%
- ✅ **累积波动阈值提升** - 提高至4%以减少误报
- ✅ **文档同步更新** - 配置文档与实际代码保持一致

### v2.3 (2026-01-31)
- ✅ **多指标波动检测** - 标准差、累积波动、范围分析、波动加速
- ✅ **视觉优化** - 改进波动和关口警报的视觉区分
- ✅ **配置修复** - 统一 USD1 阈值为 0.0005
- ✅ **文档更新** - 补充波动监控详细说明和通知格式

### v2.2 (2026-01-30)
- ✅ **优雅关闭** - Docker 停止时自动发送 Telegram 通知
- ✅ **信号处理** - 添加 SIGTERM/SIGINT 信号处理器
- ✅ **运行统计** - 停止通知包含运行时长和监控币种数量
- ✅ **代码简化** - 移除轮询模式，专注 WebSocket 实时监控
- ✅ **清理代码** - 移除未使用的导入和功能

### v2.1 (2026-01-29)
- ✅ **代码重构** - 消除重复代码，减少70%代码量
- ✅ **提取辅助方法** - `_calculate_milestone`, `_check_milestone_cooldown`, `_send_milestone_notification`
- ✅ **冷却时间优化** - 从5分钟增加到10分钟，减少通知频率
- ✅ **配置更新** - USD1阈值调整为0.001，更精确监控
- ✅ **完整测试验证** - Docker环境下全面测试通过
- ✅ **代码质量提升** - 更清晰的架构，更好的可维护性

### v2.0
- ✅ 新增 WebSocket 实时监控（10-50ms 延迟）
- ✅ 代码重构，提取共享模块 [common.py](common.py)
- ✅ 集中化配置管理（COIN_LIST 环境变量）
- ✅ 添加 API 重试机制（tenacity 库）
- ✅ 实现结构化日志系统
- ✅ BrokenPipe 错误处理
- ✅ 依赖管理优化（灵活版本范围）
- ✅ Docker 多阶段构建优化

### v1.0
- 基础价格监控功能
- Telegram 通知
- 整数关口检测
- 波动率监控

---

## License

MIT

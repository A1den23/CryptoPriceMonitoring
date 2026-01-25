# Crypto Price Monitoring Bot

实时监控多个加密货币价格，并通过 Telegram 发送通知。

## 功能特性

- **多币种监控** - 同时监控 BTC、ETH、SOL 等多个交易对
- **整数关口监控** - 当价格达到整数关口时发送通知
- **波动监控** - 当价格在指定时间内波动超过阈值时发送通知
- **独立配置** - 每个币种可单独配置监控参数
- **交互式机器人** - 支持 Telegram 命令和按钮交互查询价格

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

# BTC Configuration
BTC_ENABLED=true
BTC_SYMBOL=BTCUSDT
BTC_INTEGER_THRESHOLD=1000
BTC_VOLATILITY_PERCENT=3.0
BTC_VOLATILITY_WINDOW_SECONDS=180

# ETH Configuration
ETH_ENABLED=true
ETH_SYMBOL=ETHUSDT
ETH_INTEGER_THRESHOLD=100
ETH_VOLATILITY_PERCENT=2.0
ETH_VOLATILITY_WINDOW_SECONDS=120

# SOL Configuration
SOL_ENABLED=true
SOL_SYMBOL=SOLUSDT
SOL_INTEGER_THRESHOLD=10
SOL_VOLATILITY_PERCENT=3.0
SOL_VOLATILITY_WINDOW_SECONDS=60

# USD1 Configuration (Stablecoin - low volatility expected)
USD1_ENABLED=true
USD1_SYMBOL=USD1USDT
USD1_INTEGER_THRESHOLD=0.001
USD1_VOLATILITY_PERCENT=0.5
USD1_VOLATILITY_WINDOW_SECONDS=180
```

#### 配置说明：

**全局配置**
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `CHECK_INTERVAL_SECONDS` | 价格检查间隔（秒）| `5` |

**币种配置**（每个币种独立的配置）
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `{币种}_ENABLED` | 是否启用该币种监控 | `false` |
| `{币种}_SYMBOL` | 交易对符号 | `{币种}USDT` |
| `{币种}_INTEGER_THRESHOLD` | 整数关口间隔（支持整数和小数）| `1000` |
| `{币种}_VOLATILITY_PERCENT` | 触发波动警报的百分比 | `3.0` |
| `{币种}_VOLATILITY_WINDOW_SECONDS` | 波动计算时间窗口（秒）| `60` |

#### 配置示例：

```env
# BTC: 每$1000提醒，180秒内波动3%提醒
BTC_ENABLED=true
BTC_INTEGER_THRESHOLD=1000
BTC_VOLATILITY_PERCENT=3.0
BTC_VOLATILITY_WINDOW_SECONDS=180

# ETH: 每$100提醒，120秒内波动2%提醒
ETH_ENABLED=true
ETH_INTEGER_THRESHOLD=100
ETH_VOLATILITY_PERCENT=2.0
ETH_VOLATILITY_WINDOW_SECONDS=120

# SOL: 每$10提醒，60秒内波动3%提醒
SOL_ENABLED=true
SOL_INTEGER_THRESHOLD=10
SOL_VOLATILITY_PERCENT=3.0
SOL_VOLATILITY_WINDOW_SECONDS=60

# 禁用某个币种
DOGE_ENABLED=false
```

### 4. 运行

**正常监控模式：**
```bash
python monitor.py
```

启动后会显示：

```
============================================================
Starting Multi-Coin Price Monitor
============================================================
Monitored coins: 3
Check interval: 5s
============================================================

✓ Loaded BTC: enabled=True, symbol=BTCUSDT, integer_threshold=1000, volatility=3.0%/180s
✓ Loaded ETH: enabled=True, symbol=ETHUSDT, integer_threshold=100, volatility=2.0%/120s
✓ Loaded SOL: enabled=True, symbol=SOLUSDT, integer_threshold=10, volatility=3.0%/60s
```

运行时会实时显示：
```
[15:30:00] Checking prices...
[BTC] $105,234.50 📊0.85%/12pts
[ETH] $3,456.78 📊1.23%/15pts
[SOL] $234.56 📊2.10%/18pts
```

**显示格式说明：**
- `📊0.85%/12pts` = 当前波动率0.85%，基于12个价格点计算
- 当波动率超过设定阈值时，会触发 Telegram 通知

**查看状态：**
```bash
python monitor.py --status
```
显示所有币种的当前价格和配置。

**测试波动监控：**
```bash
python monitor.py --test
```
发送测试通知到 Telegram，验证波动监控是否正常工作。

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

### 示例对话

```
你: /start
Bot: 🤖 Crypto Price Monitor Bot

    Welcome! I can help you monitor cryptocurrency prices.

    📋 Available Commands:
    /price - Get price for a specific coin
    /status - Show status of all monitored coins
    /all - Get prices of all enabled coins
    /help - Show this help message

    Or click the buttons below for quick access! 👇

    [📊 All Prices] [₿ BTC] [Ξ ETH]
    [◎ SOL] [$1 USD1]
```

---

## 整数关口监控说明

系统使用**双重检测机制**来确保不会漏掉任何重要的价格关口：

### 检测机制

1. **跨越检测**（优先）- 检测价格是否跨越了关口线
2. **接近检测**（兜底）- 价格非常接近关口时也触发

### 各币种触发逻辑

> **注意**：所有币种都支持**双向监控**（上涨和下跌都会触发）

#### BTC（比特币）
- **关口间隔**：每 $1,000
- **接近范围**：±$5
- **关口示例**：89000, 90000, 91000, ...
- **触发条件**：
  - 价格从 89xxx 跨越到 90xxx → 📈 触发上涨警报
  - 价格从 90xxx 跌破到 89xxx → 📉 触发下跌警报
  - 价格在 89995-90005 范围内 → 触发 "Near Milestone"

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
- **触发条件**：
  - 价格从 34xx 跨越到 35xx → 📈 触发上涨警报
  - 价格从 35xx 跌破到 34xx → 📉 触发下跌警报
  - 价格在 3498-3502 范围内 → 触发 "Near Milestone"

**场景示例**：
```
时间   价格       所在关口    触发说明
T1    3,498      3400       初始化
T2    3,502      3500       ✅ 跨越 3500！📈
T3    3,605      3600       ✅ 跨越 3600！📈
T4    3,395      3300       ✅ 跨越回 3300！📉
```

#### SOL（Solana）
- **关口间隔**：每 $10
- **接近范围**：±$0.1
- **关口示例**：230, 240, 250, ...
- **触发条件**：
  - 价格从 23x 跨越到 24x → 📈 触发上涨警报
  - 价格从 24x 跌破到 23x → 📉 触发下跌警报
  - 价格在 239.9-240.1 范围内 → 触发 "Near Milestone"

**场景示例**：
```
时间   价格       所在关口    触发说明
T1    239.5      230        初始化
T2    240.3      240        ✅ 跨越 240！📈
T3    250.5      250        ✅ 跨越 250！📈
T4    239.2      230        ✅ 跨越回 230！📉
```

#### USD1（稳定币）
- **关口间隔**：每 $0.001
- **接近范围**：±0.0001（10%）
- **关口示例**：
  - 高于 1.0：1.001, 1.002, 1.003, ...
  - 低于 1.0：0.999, 0.998, 0.997, ...
- **触发条件**：
  - 支持双向监控（高于或低于 1.0 均可触发）
  - 价格从 1.0000 跨越到 1.001 → 触发
  - 价格在 1.0009-1.0011 范围内 → 触发 "Near Milestone"

### 通知消息格式

**跨越警报**：
```
🎯 Integer Milestone Alert!
🪙 BTCUSDT
💰 Price: $90,005.20
📍 Milestone: $90,000
📈 Direction: Up
🕐 2025-01-24 15:30:45
```

**接近警报**：
```
🎯 Near Integer Milestone!
🪙 ETHUSDT
💰 Price: $3,499.80
📍 Milestone: $3,500
📏 Distance: $0.20 away
🕐 2025-01-24 15:30:45
```

---

## 添加新币种

要添加新的币种监控（例如 DOGE），在 `.env` 文件中添加：

```env
DOGE_ENABLED=true
DOGE_SYMBOL=DOGEUSDT
DOGE_INTEGER_THRESHOLD=0.001
DOGE_VOLATILITY_PERCENT=5.0
DOGE_VOLATILITY_WINDOW_SECONDS=60
```

然后在 [monitor.py](monitor.py#L249) 的 `coin_names` 列表中添加：

```python
coin_names = ["BTC", "ETH", "SOL", "USD1", "DOGE"]
```

以及更新测试和状态函数中的列表（约第280行和第324行）。

## 示例通知

### 整数关口通知
```
🎯 Integer Milestone Alert!
🪙 ETHUSDT
💰 Price: $3,000.00
📍 Milestone: $3,000
🕐 2025-01-23 15:30:45
```

### 波动警报通知
```
🚨 High Volatility Alert!
🪙 SOLUSDT
💰 Current: $150.00
📊 Volatility: 3.25% in 60s
📈 Change: +2.50%
⏱️ 2025-01-23 15:32:15
```

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

### Windows

使用 pythonw：
```cmd
pythonw monitor.py
```

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

# 4. 停止所有服务
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

- **自动重启** - 服务崩溃或服务器重启后自动恢复
- **资源隔离** - 限制 CPU 和内存使用
- **易于管理** - 一键启动、停止、更新
- **健康检查** - 自动检测服务状态
- **日志持久化** - 日志文件保存在宿主机

## 故障排除

### 没有收到通知
1. 检查 `.env` 文件中的 token 和 chat_id
2. 确认已向 bot 发送过 `/start` 命令
3. 检查币种是否设置为 `ENABLED=true`

### 无法获取价格
1. 检查网络连接
2. 确认交易对符号正确（如 BTCUSDT）
3. 测试 API：`https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT`

### 配置不生效
1. 确认编辑的是 `.env` 文件，不是 `.env.example`
2. 重启程序使配置生效

### 如何确认波动监控是否正常工作

**方法 1: 查看实时输出**
运行监控程序后，会实时显示波动率数据：
```
[BTC] $105,234.50 📊0.85%/12pts
```
- `📊0.85%` = 当前180秒内的波动率是0.85%
- `/12pts` = 基于12个价格采样点计算
- 如果显示这个信息，说明波动监控正在运行

**方法 2: 发送测试通知**
```bash
python monitor.py --test
```
这会向你的 Telegram 发送测试通知，包含：
- 当前价格
- 你的波动率阈值设置
- 模拟的高波动场景
- 确认通知功能正常

**方法 3: 查看状态**
```bash
python monitor.py --status
```
显示所有币种的配置和当前价格，确认配置正确加载。

**常见问题：**
- 刚启动时波动率显示为空或较低是正常的（需要累积足够的价格数据）
- 波动率计算基于设定的时间窗口（如180秒），启动后需要等待至少2次价格检查才有数据显示
- 如果波动率一直显示为0%，可能是市场平静，或者检查间隔太长

## 项目结构

```
.
├── monitor.py              # 主程序（监控模式）
├── bot.py                  # 交互式 Bot（命令/按钮模式）
├── requirements.txt        # Python 依赖
├── .env.example           # 配置文件模板
├── .env                   # 你的配置（不提交到 git）
├── .gitignore             # Git 忽略文件
├── Dockerfile             # Docker 镜像定义
├── docker-compose.yml     # Docker Compose 配置
├── .dockerignore          # Docker 构建排除文件
├── DEPLOYMENT.md          # Docker 部署详细指南
└── README.md              # 说明文档
```

## License

MIT

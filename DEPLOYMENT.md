# Docker 部署指南（Ubuntu）

本文档提供最短可执行的生产部署流程。

## 1. 前提

- Ubuntu 22.04+（其他 Linux 发行版也可）
- 已安装 Docker 和 Docker Compose 插件
- 已准备好 Telegram Bot Token 与 Chat ID

安装 Docker（官方脚本）：

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装必要的包
sudo apt install -y apt-transport-https ca-certificates curl software-properties-common

# 添加 Docker 官方 GPG 密钥
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# 添加 Docker 仓库
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 安装 Docker
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 启动 Docker
sudo systemctl start docker
sudo systemctl enable docker

# 验证安装
docker --version
docker compose version
```

## 2. 部署步骤

### 2.1 获取代码

```bash
git clone <your-repo-url> CryptoPriceMonitoring
cd CryptoPriceMonitoring
```

### 2.2 配置环境变量

```bash
cp .env.example .env
nano .env
```

**最少需要**：

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

**推荐配置**：

```env
# 时区设置
TIMEZONE=Asia/Shanghai

# WebSocket 连接优化
WS_PING_INTERVAL_SECONDS=30
WS_PONG_TIMEOUT_SECONDS=10
WS_MESSAGE_TIMEOUT_SECONDS=120

# Bot 健康检查
BOT_HEARTBEAT_INTERVAL_SECONDS=30

# 告警冷却时间（秒）
MILESTONE_ALERT_COOLDOWN_SECONDS=600
VOLATILITY_ALERT_COOLDOWN_SECONDS=60
VOLUME_ALERT_COOLDOWN_SECONDS=5

# 稳定币脱锚监控（默认关闭）
STABLECOIN_DEPEG_MONITOR_ENABLED=false
STABLECOIN_DEPEG_TOP_N=20
STABLECOIN_DEPEG_THRESHOLD_PERCENT=5
STABLECOIN_DEPEG_POLL_INTERVAL_SECONDS=300
STABLECOIN_DEPEG_ALERT_COOLDOWN_SECONDS=3600
```

### 2.3 启动服务

```bash
docker compose up -d --build
```

## 3. 验证部署

```bash
# 服务状态
docker compose ps

# 查看日志
docker compose logs -f

# 查看监控当前状态
docker compose exec crypto-monitor python monitor.py --status
# 等价写法
docker compose exec crypto-monitor python -m monitor --status
```

正常情况下：

- `crypto-monitor` 日志包含 `WebSocket connected`
- `crypto-bot` 日志包含 `Application started`
- 顶层 `monitor.py` / `bot.py` 仍可用，但当前主要实现已分别拆分到 `monitor/` 和 `bot/` 包

## 4. 日常运维

### 日志与状态

```bash
docker compose ps
docker compose logs -f
docker compose logs --tail=200 crypto-monitor
docker compose logs --tail=200 crypto-bot
```

### 启停与重启

```bash
docker compose up -d
docker compose restart
docker compose down
```

### 单服务重启

```bash
docker compose restart crypto-monitor
docker compose restart crypto-bot
```

### 容器调试

```bash
docker compose exec crypto-monitor bash
docker compose exec crypto-bot bash
```

## 5. 更新与回滚

### 更新

```bash
git pull
docker compose up -d --build
```

### 快速回滚

```bash
git log --oneline -n 10
git checkout <commit>
docker compose up -d --build
```

## 6. 故障排查

### 容器未启动

```bash
docker compose ps
docker compose logs --tail=200
```

### WebSocket 异常

```bash
docker compose logs --tail=200 crypto-monitor
curl -I https://api.binance.com
```

### Telegram 发送失败

```bash
docker compose logs --tail=200 crypto-monitor
docker compose logs --tail=200 crypto-bot
```

重点检查：

- `TELEGRAM_BOT_TOKEN` 是否正确
- `TELEGRAM_CHAT_ID` 是否正确
- Bot 是否能收到你发送的消息

### 配置修改后无效

```bash
docker compose up -d
# 必要时强制重建
docker compose up -d --build
```

### 日志时间不正确

检查并设置正确的时区：

```bash
# 查看当前时区配置
grep TIMEZONE .env

# 修改为正确的时区
# 亚洲/上海：Asia/Shanghai
#  UTC：UTC
# 纽约：America/New_York
```

修改后重启服务：

```bash
docker compose restart
```

## 7. 生产建议（简版）

- 使用专用 Linux 用户运行项目
- 设置 `.env` 权限：`chmod 600 .env`
- 定期清理无用镜像：`docker image prune -f`
- 使用外部监控系统观察容器重启次数与错误日志

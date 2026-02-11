# Docker 部署指南（Ubuntu）

本文档提供最短可执行的生产部署流程。

## 1. 前提

- Ubuntu 22.04+（其他 Linux 发行版也可）
- 已安装 Docker 和 Docker Compose 插件
- 已准备好 Telegram Bot Token 与 Chat ID

快速安装 Docker（官方脚本）：

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

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

最少需要：

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
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
```

正常情况下：

- `crypto-monitor` 日志包含 `WebSocket connected`
- `crypto-bot` 日志包含 `Application started`

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

## 7. 生产建议（简版）

- 使用专用 Linux 用户运行项目
- 设置 `.env` 权限：`chmod 600 .env`
- 定期清理无用镜像：`docker image prune -f`
- 使用外部监控系统观察容器重启次数与错误日志


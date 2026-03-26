"""Pure runtime message renderers for WebSocket monitor orchestration."""

from datetime import datetime


def render_shutdown_notification(*, current_time: datetime, uptime: str, monitor_count: int) -> str:
    """Render the graceful shutdown Telegram message."""
    return (
        f"👋 <b>加密货币价格监控已停止</b>\n"
        f"⏱️ {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"⌛ 运行时间: {uptime}\n"
        f"🪙 监控币种: {monitor_count} 个\n"
        f"📊 状态: 优雅关闭"
    )


def render_disconnect_alert(*, reason: str, current_time: datetime) -> str:
    """Render the disconnect alert Telegram message."""
    return (
        f"🚨🚨【连接断开警报】🚨🚨\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚠️ 价格监控连接已中断！\n"
        f"📡 连接状态: 已断开\n"
        f"🔍 断开原因: {reason}\n"
        f"⏱️ {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💡 系统正在尝试自动重连..."
    )


def render_reconnect_alert(*, attempt_count: int, downtime: str, current_time: datetime) -> str:
    """Render the reconnect success Telegram message."""
    return (
        f"✅✅【连接恢复通知】✅✅\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📡 价格监控已恢复正常\n"
        f"🔄 重连次数: {attempt_count} 次\n"
        f"⏱️ 中断时长: {downtime}\n"
        f"⏱️ {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━"
    )


def render_realtime_updates_block(*, timestamp: str, updates: list[str]) -> str:
    """Render the console block for batched realtime updates."""
    lines = [f"[{timestamp}] 实时更新:"]
    lines.extend(f"  {update}" for update in updates)
    return "\n".join(lines) + "\n"

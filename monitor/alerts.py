"""Pure alert message renderers shared by monitor components."""

from datetime import datetime
from html import escape

from common.utils import format_price


def render_milestone_alert(
    *,
    symbol: str,
    current_price: float,
    is_up: bool,
    current_time: datetime,
) -> str:
    """Render the milestone alert message body."""
    direction = "📈" if is_up else "📉"
    direction_text = "向上 ↑" if is_up else "向下 ↓"
    safe_symbol = escape(symbol)
    return (
        f"🎉🎉【价格里程碑】🎉🎉\n"
        f"🪙 {safe_symbol}\n"
        f"💰 价格: {format_price(current_price)}\n"
        f"{direction} 突破方向: {direction_text}\n"
        f"⏱️ {current_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )


def render_volatility_alert(
    *,
    symbol: str,
    current_price: float,
    volatility_window: int,
    sample_count: int,
    reasons: list[str],
    change_percent: float,
    current_time: datetime,
) -> str:
    """Render the volatility alert message body."""
    direction = "📈" if change_percent > 0 else "📉"
    safe_symbol = escape(symbol)
    return (
        f"⚠️⚠️【波动警报】⚠️⚠️\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🪙 {safe_symbol}\n"
        f"💰 当前: {format_price(current_price)}\n"
        f"📊 时间窗口: {volatility_window}s ({sample_count} pts)\n"
        f"⚡️ 触发指标: {', '.join(reasons)}\n"
        f"{direction} 净变化: {change_percent:+.2f}%\n"
        f"⏱️ {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━"
    )


def render_volume_alert(
    *,
    symbol: str,
    current_price: float,
    price_change_pct: float,
    volume_multiplier: float,
    current_volume: float,
    avg_volume: float,
    current_time: datetime,
) -> str:
    """Render the volume anomaly alert message body."""
    direction = "📈" if price_change_pct > 0 else "📉"
    safe_symbol = escape(symbol)
    return (
        f"🚨🚨【成交量异常警报】🚨🚨\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🪙 {safe_symbol}\n"
        f"💰 当前价格: {format_price(current_price)}\n"
        f"{direction} 价格变化: {price_change_pct:+.2f}%\n"
        f"📊 成交量暴增: {volume_multiplier:.1f}x\n"
        f"📈 当前成交量: {current_volume:,.0f}\n"
        f"📊 基准成交量: {avg_volume:,.0f}\n"
        f"⏱️ {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

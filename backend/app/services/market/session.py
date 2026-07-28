"""A 股交易时段判断与刷新间隔配置。"""

import os
from datetime import UTC, datetime, time as datetime_time

from .constants import CHINA_TZ


def market_refresh_interval() -> int:
    """读取刷新秒数并限制在 10 秒到 1 小时之间。"""
    try:
        value = int(os.getenv("MONIPAN_MARKET_INTERVAL", "60"))
    except ValueError:
        value = 60
    return max(10, min(value, 3600))


def market_session(now: datetime | None = None) -> tuple[str, str, bool]:
    """返回当前 A 股时段；第一版按中国时区工作日判断。"""
    local_now = now or datetime.now(CHINA_TZ)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=UTC).astimezone(CHINA_TZ)
    else:
        local_now = local_now.astimezone(CHINA_TZ)

    if local_now.weekday() >= 5:
        return "weekend", "周末休市", False

    current = local_now.timetz().replace(tzinfo=None)
    if current < datetime_time(9, 15):
        return "pre_open", "等待开市", False
    if current <= datetime_time(9, 25):
        return "call_auction", "集合竞价", True
    if current < datetime_time(9, 30):
        return "opening_break", "开盘准备", True
    if current <= datetime_time(11, 30):
        return "morning", "上午交易", True
    if current < datetime_time(13, 0):
        return "lunch_break", "午间休市", False
    if current <= datetime_time(15, 0):
        return "afternoon", "下午交易", True
    return "closed", "已收盘", False


def is_a_share_session(now: datetime | None = None) -> bool:
    """判断当前是否处于工作日 A 股交易时段。"""
    return market_session(now)[2]

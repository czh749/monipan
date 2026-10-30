"""Bounded process-local fetch observations; database timestamps remain authoritative.

After a restart an unobserved request is pending, never invented as a failure.
The existing single market worker owns retries; reading these states never fetches.
"""

from collections import OrderedDict
from datetime import UTC, datetime, timedelta
import math
from threading import Lock

from .calendar import trading_day_state
from .constants import CHINA_TZ
from .session import market_refresh_interval, market_session

_observations = OrderedDict()
_lock = Lock()


def record_fetch(kind, symbol, *, failed, retry_seconds=0):
    now = datetime.now(UTC).replace(tzinfo=None)
    with _lock:
        _observations[(kind, symbol)] = (failed, now, now + timedelta(seconds=retry_seconds))
        _observations.move_to_end((kind, symbol))
        while len(_observations) > 10000:
            _observations.popitem(last=False)


def fetch_observation(kind, symbol, now=None):
    now = now or datetime.now(UTC).replace(tzinfo=None)
    with _lock:
        result = _observations.get((kind, symbol))
    if result is None:
        return False, None, None
    failed, attempted, retry_at = result
    return failed, attempted, max(0, math.ceil((retry_at - now).total_seconds()))


def quote_preparation(stock, now=None):
    now = now or datetime.now(UTC).replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    failed, attempted, retry = fetch_observation("quote", stock.symbol, now)
    available = stock.price > 0 and stock.prev_close > 0
    state, message = "pending", "等待首次行情；后台将自动尝试"
    if failed and (not available or stock.updated_at <= attempted):
        state = "failed"
        message = "行情获取失败；后台将自动重试" if not available else "本轮行情更新失败，当前显示上次快照"
    elif available:
        source = stock.quote_source_at
        interval = market_refresh_interval()
        _, session_label, opened = market_session(now.replace(tzinfo=UTC))
        local = now.replace(tzinfo=UTC).astimezone(CHINA_TZ)
        expected = local.date()
        if (local.hour, local.minute) < (9, 15):
            expected -= timedelta(days=1)
        # Avoid calling an old Friday quote stale merely because it is Sunday.
        for _ in range(14):
            if expected.weekday() < 5 and trading_day_state(expected) is not False:
                break
            expected -= timedelta(days=1)
        age = (now - min(source, stock.updated_at)).total_seconds() if source else None
        if source is None or source > now + timedelta(seconds=60) or stock.updated_at > now + timedelta(seconds=60):
            state, message = "stale", "行情时间无效，当前价格不可视为最新行情"
        elif source.replace(tzinfo=UTC).astimezone(CHINA_TZ).date() < expected or (opened and age > interval * 5):
            state, message = "stale", "行情已陈旧，当前显示历史快照"
        elif opened and age > math.ceil(interval * 2.5):
            state, message = "delayed", "行情更新延迟，当前显示上次快照"
        elif not opened:
            state, message = "closed", f"{session_label}，显示最近交易快照"
        else:
            state, message = "ready", "行情已更新"
    return {"quote_state": state, "quote_message": message,
            "quote_retry_after_seconds": retry if state == "failed" else None}


def history_preparation(symbol, complete):
    failed, attempted, retry = fetch_observation("history", symbol)
    state = "failed" if failed else "ready" if complete else "partial" if attempted else "pending"
    messages = {"failed": "历史行情获取失败，已保留缓存；可稍后重试",
                "ready": "历史缓存已就绪", "partial": "已取得部分历史行情",
                "pending": "等待历史行情补齐"}
    return {"preparation_state": state, "preparation_message": messages[state],
            "retry_after_seconds": retry if failed else None}

"""行情服务包。

从各子模块汇总公开 API，保持向后兼容：
    from app.services.market import tick_market, stock_values, ...

子模块分工：
    constants  — 常量与日志器
    types      — 数据类型、异常与运行时状态
    session    — A 股交易时段判断与刷新间隔配置
    eastmoney  — 东方财富公开行情接口的请求与解析
    tick       — 行情刷新：将东财行情覆盖写入数据库
    status     — 行情状态计算：覆盖率与基于时段的新鲜度
    formatters — ORM 对象格式化为前端友好的字典

注意：requests / time / random 三个标准库模块在此重新暴露，
是为了兼容测试中的 monkeypatch.setattr(market.requests, ...) 写法。
由于 Python 模块是单例，patch 作用在共享的模块对象上，
eastmoney 子模块中的 import requests 会看到同样的修改。
"""

import random
import requests
import time

from .constants import (
    EASTMONEY_BATCH_SIZE,
    EASTMONEY_FAILURE_PAUSE_SECONDS,
    EASTMONEY_QUOTE_URLS,
    EASTMONEY_RETRY_COOLDOWN_SECONDS,
)
from .eastmoney import fetch_eastmoney_indices, fetch_eastmoney_quotes
from .formatters import market_index_values, stock_values
from .session import is_a_share_session, market_refresh_interval, market_session
from .status import market_status_values
from .tick import tick_market, tick_market_indices
from .types import MARKET_REFRESH_STATE, MarketDataError

__all__ = [
    "EASTMONEY_BATCH_SIZE",
    "EASTMONEY_FAILURE_PAUSE_SECONDS",
    "EASTMONEY_QUOTE_URLS",
    "EASTMONEY_RETRY_COOLDOWN_SECONDS",
    "MARKET_REFRESH_STATE",
    "MarketDataError",
    "fetch_eastmoney_indices",
    "fetch_eastmoney_quotes",
    "is_a_share_session",
    "market_index_values",
    "market_refresh_interval",
    "market_session",
    "market_status_values",
    "random",
    "requests",
    "stock_values",
    "tick_market",
    "tick_market_indices",
    "time",
]

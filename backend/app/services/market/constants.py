"""行情服务共享的常量与日志器。

所有与东方财富公开接口、精度控制、健康阈值相关的配置集中在此，
便于统一调整而无需在多个文件间搜索。
"""

import logging
from datetime import timedelta, timezone
from decimal import Decimal


# 人民币最小单位（分），所有金额量化到此精度。
CENT = Decimal("0.01")

# 中国时区（UTC+8），用于判断 A 股交易时段。
CHINA_TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# 东方财富公开行情接口配置
# ---------------------------------------------------------------------------

# 东财会主动断开缺少常规浏览器标识的批量请求。
# 使用公开行情页面同源请求头可以显著降低 RemoteDisconnected，但不绕过鉴权。
EASTMONEY_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
    # 该公开接口会在复用较多批次后主动关闭长连接；逐批短连接更稳定。
    "Connection": "close",
}

# 单次请求最大股票数；200 只股票池正好一个批次。
EASTMONEY_BATCH_SIZE = 200

# 主域名与延迟域名，第二遍重试时切换到延迟域名。
EASTMONEY_QUOTE_URLS = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get",
    "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
)

# 第一遍批次间隔（秒），叠加随机抖动。
EASTMONEY_FIRST_PASS_INTERVAL_SECONDS = 1.5

# 第二遍重试批次间隔（秒）。
EASTMONEY_RETRY_INTERVAL_SECONDS = 2.5

# 批次间隔随机抖动上限（秒）。
EASTMONEY_INTERVAL_JITTER_SECONDS = 0.8

# 第一遍全部失败后，进入第二遍前的统一冷却（秒）。
EASTMONEY_RETRY_COOLDOWN_SECONDS = 15

# 连续失败多少批后触发暂停。
EASTMONEY_CONSECUTIVE_FAILURE_LIMIT = 3

# 连续失败暂停时长（秒），每次翻倍。
EASTMONEY_FAILURE_PAUSE_SECONDS = 15

# 单遍最多暂停次数，超过则提前结束。
EASTMONEY_MAX_FAILURE_PAUSES_PER_PASS = 2

# 单轮行情获取的总时间预算（秒）。
EASTMONEY_ROUND_BUDGET_SECONDS = 240

# ---------------------------------------------------------------------------
# 健康阈值
# ---------------------------------------------------------------------------

# 新鲜覆盖率低于此阈值时标记为"行情不完整"。
HEALTHY_COVERAGE_THRESHOLD_PERCENT = Decimal("95.0")

# 复用 uvicorn 的错误日志器，行情相关日志与请求日志统一输出。
logger = logging.getLogger("uvicorn.error")

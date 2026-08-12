"""东方财富公开行情接口的请求与解析。

包含：
    - 数值/代码解析工具函数（内部使用）
    - 股票行情批量获取（含两遍请求 + 冷却重试）
    - 大盘指数行情获取
"""

import math
import random
import time
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import requests

from ...market_index_pool import MARKET_INDEX_POOL
from .constants import (
    CENT,
    CHINA_TZ,
    EASTMONEY_CONSECUTIVE_FAILURE_LIMIT,
    EASTMONEY_FAILURE_PAUSE_SECONDS,
    EASTMONEY_FIRST_PASS_INTERVAL_SECONDS,
    EASTMONEY_INTERVAL_JITTER_SECONDS,
    EASTMONEY_MAX_FAILURE_PAUSES_PER_PASS,
    EASTMONEY_QUOTE_URLS,
    EASTMONEY_REQUEST_HEADERS,
    EASTMONEY_RETRY_COOLDOWN_SECONDS,
    EASTMONEY_RETRY_INTERVAL_SECONDS,
    EASTMONEY_ROUND_BUDGET_SECONDS,
    logger,
)
from .types import BatchFetchStats, MarketDataError, QuoteFetchResult


# ---------------------------------------------------------------------------
# 解析工具函数
# ---------------------------------------------------------------------------

def _normalize_symbol(value: Any) -> str:
    """把数值或字符串股票代码规范成 6 位字符串，保留前导零。"""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


def _positive_decimal(value: Any) -> Decimal | None:
    """将有效正数价格转成分；NaN、空值和零值都视为无效。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return Decimal(str(number)).quantize(CENT, ROUND_HALF_UP)


def _decimal_value(value: Any) -> Decimal | None:
    """将允许为负数或零的有效数值转换为两位小数。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return Decimal(str(number)).quantize(CENT, ROUND_HALF_UP)


def _nonnegative_decimal(value: Any) -> Decimal | None:
    """将成交额等非负数值转换为两位小数。"""
    parsed = _decimal_value(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _volume_in_shares(value: Any) -> int | None:
    """东财成交量单位为手，转换成数据库使用的股。"""
    try:
        lots = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lots) or lots < 0:
        return None
    return int(lots * 100)


def _quote_trade_date(value: Any) -> date | None:
    """Parse the provider's last-trade timestamp into a China-market date.

    ``f124`` is deliberately kept separate from ``fetched_at``.  During a
    weekend or exchange holiday the endpoint can still return Friday's quote;
    using the HTTP request time would manufacture a bar for a non-trading day.
    """
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(timestamp) or timestamp <= 0:
        return None
    if timestamp >= 1_000_000_000_000:
        timestamp /= 1000
    try:
        parsed = datetime.fromtimestamp(timestamp, UTC).astimezone(CHINA_TZ)
    except (OSError, OverflowError, ValueError):
        return None
    if parsed.year < 2000 or parsed.year > 2100:
        return None
    return parsed.date()


def _eastmoney_secid(symbol: str) -> str:
    """东财沪市代码使用 1 前缀，深市代码使用 0 前缀。"""
    return f"{'1' if symbol.startswith('6') else '0'}.{symbol}"


# ---------------------------------------------------------------------------
# 股票行情批量获取
# ---------------------------------------------------------------------------

def _fetch_eastmoney_quote_batch(
    client: requests.Session,
    url: str,
    fields: str,
    batch: list[str],
    requested_symbols: set[str],
) -> tuple[dict[str, dict[str, Any]] | None, Exception | None]:
    """请求并解析一个股票批次；失败交由外层延迟重试。"""
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": fields,
        "secids": ",".join(_eastmoney_secid(symbol) for symbol in batch),
    }
    try:
        response = client.get(url, params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
        rows = (payload.get("data") or {}).get("diff")
        if not isinstance(rows, list):
            raise ValueError("东财行情返回格式异常")
    except (requests.RequestException, ValueError) as exc:
        return None, exc

    fetched_at = datetime.now(UTC).replace(tzinfo=None)
    quotes: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _normalize_symbol(row.get("f12"))
        price = _positive_decimal(row.get("f2"))
        prev_close = _positive_decimal(row.get("f18"))
        if symbol not in requested_symbols or price is None or prev_close is None:
            # 停牌或无有效报价时保留最后一笔有效行情。
            continue

        name = row.get("f14")
        quotes[symbol] = {
            "name": name.strip() if isinstance(name, str) else None,
            "price": price,
            "prev_close": prev_close,
            "open_price": _positive_decimal(row.get("f17")),
            "high_price": _positive_decimal(row.get("f15")),
            "low_price": _positive_decimal(row.get("f16")),
            "volume": _volume_in_shares(row.get("f5")),
            "quote_trade_date": _quote_trade_date(row.get("f124")),
            "updated_at": fetched_at,
        }
    return quotes, None


def fetch_eastmoney_quotes(symbols: list[str]) -> QuoteFetchResult:
    """
    按最陈旧优先顺序分批获取行情，并对失败批次做冷却后的第二遍重试。

    调用方传入的代码顺序会被保留。第一遍请求均匀摊开；连续失败时自动暂停，
    第一遍失败的批次在统一冷却后才进入第二遍，避免短时间立即重试放大限流。
    """
    fields = "f2,f5,f12,f14,f15,f16,f17,f18,f124"

    # 延迟导入：测试通过 monkeypatch.setattr(market, "EASTMONEY_BATCH_SIZE", 5)
    # 来模拟多批次场景，必须从包级别查找才能看到 patch 后的值。
    from . import EASTMONEY_BATCH_SIZE

    ordered_symbols = list(dict.fromkeys(symbols))
    requested_symbols = set(ordered_symbols)
    batches = [
        ordered_symbols[offset : offset + EASTMONEY_BATCH_SIZE]
        for offset in range(0, len(ordered_symbols), EASTMONEY_BATCH_SIZE)
    ]
    indexed_batches = list(enumerate(batches, start=1))
    result: dict[str, dict[str, Any]] = {}
    successful_batch_ids: set[int] = set()
    retry_attempted_ids: set[int] = set()
    recovered_batch_ids: set[int] = set()
    request_attempts = 0
    pause_count = 0
    started_at = time.monotonic()
    deadline = started_at + EASTMONEY_ROUND_BUDGET_SECONDS

    def sleep_with_budget(seconds: float) -> bool:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(seconds, remaining))
        return time.monotonic() < deadline

    def run_pass(
        client: requests.Session,
        candidates: list[tuple[int, list[str]]],
        *,
        pass_name: str,
        pass_url: str,
        interval_seconds: float,
        retry_pass: bool,
    ) -> list[tuple[int, list[str]]]:
        nonlocal request_attempts, pause_count
        failed: list[tuple[int, list[str]]] = []
        consecutive_failures = 0
        pass_pauses = 0

        for position, (batch_id, batch) in enumerate(candidates):
            if time.monotonic() >= deadline:
                failed.extend(candidates[position:])
                logger.warning(
                    "东财行情%s达到 %s 秒预算，剩余 %s 个批次留待下一轮",
                    pass_name,
                    EASTMONEY_ROUND_BUDGET_SECONDS,
                    len(candidates) - position,
                )
                break

            if retry_pass:
                retry_attempted_ids.add(batch_id)
            request_attempts += 1
            quotes, error = _fetch_eastmoney_quote_batch(
                client,
                pass_url,
                fields,
                batch,
                requested_symbols,
            )

            if error is not None:
                failed.append((batch_id, batch))
                consecutive_failures += 1
                logger.warning(
                    "东财行情%s失败，批次 %s 进入%s：%s",
                    pass_name,
                    batch_id,
                    "下一轮" if retry_pass else "延迟重试队列",
                    error,
                )
            else:
                result.update(quotes or {})
                successful_batch_ids.add(batch_id)
                if retry_pass:
                    recovered_batch_ids.add(batch_id)
                consecutive_failures = 0

            has_more = position + 1 < len(candidates)
            if (
                has_more
                and consecutive_failures >= EASTMONEY_CONSECUTIVE_FAILURE_LIMIT
            ):
                if pass_pauses >= EASTMONEY_MAX_FAILURE_PAUSES_PER_PASS:
                    failed.extend(candidates[position + 1 :])
                    logger.warning(
                        "东财行情%s连续失败且已暂停 %s 次，提前结束本遍",
                        pass_name,
                        pass_pauses,
                    )
                    break
                pause_seconds = EASTMONEY_FAILURE_PAUSE_SECONDS * (
                    pass_pauses + 1
                )
                pass_pauses += 1
                pause_count += 1
                logger.warning(
                    "东财行情%s连续失败 %s 批，暂停 %s 秒",
                    pass_name,
                    EASTMONEY_CONSECUTIVE_FAILURE_LIMIT,
                    pause_seconds,
                )
                if not sleep_with_budget(pause_seconds):
                    failed.extend(candidates[position + 1 :])
                    break
                consecutive_failures = 0
            elif has_more:
                delay = interval_seconds + random.uniform(
                    0,
                    EASTMONEY_INTERVAL_JITTER_SECONDS,
                )
                if not sleep_with_budget(delay):
                    failed.extend(candidates[position + 1 :])
                    break

        return failed

    with requests.Session() as client:
        # 东财会主动断开缺少常规浏览器标识的批量请求。使用公开行情
        # 页面同源请求头可以显著降低 RemoteDisconnected，但不绕过鉴权。
        client.headers.update(EASTMONEY_REQUEST_HEADERS)
        retry_candidates = run_pass(
            client,
            indexed_batches,
            pass_name="第一遍",
            pass_url=EASTMONEY_QUOTE_URLS[0],
            interval_seconds=EASTMONEY_FIRST_PASS_INTERVAL_SECONDS,
            retry_pass=False,
        )

        if retry_candidates and time.monotonic() < deadline:
            logger.info(
                "东财行情第一遍结束：成功=%s/%s，失败=%s，冷却 %s 秒后重试",
                len(successful_batch_ids),
                len(indexed_batches),
                len(retry_candidates),
                EASTMONEY_RETRY_COOLDOWN_SECONDS,
            )
            if sleep_with_budget(EASTMONEY_RETRY_COOLDOWN_SECONDS):
                run_pass(
                    client,
                    retry_candidates,
                    pass_name="延迟重试",
                    pass_url=EASTMONEY_QUOTE_URLS[1],
                    interval_seconds=EASTMONEY_RETRY_INTERVAL_SECONDS,
                    retry_pass=True,
                )

    elapsed_seconds = round(time.monotonic() - started_at, 1)
    stats = BatchFetchStats(
        total_batches=len(indexed_batches),
        successful_batches=len(successful_batch_ids),
        failed_batches=max(0, len(indexed_batches) - len(successful_batch_ids)),
        retried_batches=len(retry_attempted_ids),
        recovered_batches=len(recovered_batch_ids),
        request_attempts=request_attempts,
        pause_count=pause_count,
        fallback_used=bool(retry_attempted_ids),
        elapsed_seconds=elapsed_seconds,
    )
    logger.info(
        "行情批次统计：total=%s success=%s failed=%s success_rate=%s%% "
        "retried=%s recovered=%s attempts=%s pauses=%s fallback=%s elapsed=%ss",
        stats.total_batches,
        stats.successful_batches,
        stats.failed_batches,
        stats.success_percent,
        stats.retried_batches,
        stats.recovered_batches,
        stats.request_attempts,
        stats.pause_count,
        stats.fallback_used,
        stats.elapsed_seconds,
    )

    if not result:
        raise MarketDataError(
            "东财没有返回股票池中的有效行情",
            batch_stats=stats,
        )
    return QuoteFetchResult(result, stats)


# ---------------------------------------------------------------------------
# 大盘指数行情获取
# ---------------------------------------------------------------------------

def fetch_eastmoney_indices() -> dict[str, dict[str, Any]]:
    """一次获取五个主要 A 股指数的点位、涨跌幅与成交额。"""
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": "2",
        "invt": "2",
        # 精简字段，避免公开接口或网络代理因 URL 过长主动断开。
        "fields": "f2,f3,f4,f6,f12,f14",
        "secids": ",".join(item[3] for item in MARKET_INDEX_POOL),
    }
    requested_symbols = {item[0] for item in MARKET_INDEX_POOL}
    fetched_at = datetime.now(UTC).replace(tzinfo=None)
    last_error: Exception | None = None

    for attempt, url in enumerate(EASTMONEY_QUOTE_URLS):
        try:
            response = requests.get(
                url,
                params=params,
                headers=EASTMONEY_REQUEST_HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            rows = (payload.get("data") or {}).get("diff")
            if not isinstance(rows, list):
                raise ValueError("东财指数行情返回格式异常")

            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                symbol = _normalize_symbol(row.get("f12"))
                price = _positive_decimal(row.get("f2"))
                change = _decimal_value(row.get("f4"))
                change_percent = _decimal_value(row.get("f3"))
                turnover = _nonnegative_decimal(row.get("f6"))
                if (
                    symbol not in requested_symbols
                    or price is None
                    or change is None
                    or change_percent is None
                    or turnover is None
                ):
                    continue
                result[symbol] = {
                    "price": price,
                    "change": change,
                    "change_percent": change_percent,
                    "turnover": turnover,
                    "updated_at": fetched_at,
                }

            if result:
                return result
            raise ValueError("东财没有返回有效指数行情")
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < len(EASTMONEY_QUOTE_URLS):
                time.sleep(EASTMONEY_RETRY_COOLDOWN_SECONDS)

    raise MarketDataError(f"指数行情获取失败：{last_error}")

"""行情状态计算：汇总行情源、刷新任务、覆盖率与基于时段的数据新鲜度。"""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import Stock
from .constants import HEALTHY_COVERAGE_THRESHOLD_PERCENT
from .session import market_refresh_interval, market_session
from .types import BatchFetchStats, MARKET_REFRESH_STATE


def market_status_values(
    db: Session,
    now: datetime | None = None,
) -> dict[str, Any]:
    """汇总行情源、刷新任务、覆盖率与基于时段的数据新鲜度。"""
    observed_at = now or datetime.now(UTC).replace(tzinfo=None)
    if observed_at.tzinfo is not None:
        observed_at = observed_at.astimezone(UTC).replace(tzinfo=None)

    interval = market_refresh_interval()
    fresh_threshold = math.ceil(interval * 2.5)
    stale_threshold = interval * 5
    session, session_label, session_open = market_session(
        observed_at.replace(tzinfo=UTC)
    )

    valid_quote = (Stock.price > 0, Stock.prev_close > 0)
    total_count = db.scalar(select(func.count()).select_from(Stock)) or 0
    available_count = (
        db.scalar(
            select(func.count()).select_from(Stock).where(*valid_quote)
        )
        or 0
    )
    latest_quote_at = db.scalar(
        select(func.max(Stock.updated_at)).where(*valid_quote)
    )

    quote_age_seconds: int | None = None
    fresh_count = 0
    if latest_quote_at:
        quote_age_seconds = max(
            0, int((observed_at - latest_quote_at).total_seconds())
        )
        freshness_cutoff = observed_at - timedelta(seconds=fresh_threshold)
        fresh_count = (
            db.scalar(
                select(func.count())
                .select_from(Stock)
                .where(*valid_quote, Stock.updated_at >= freshness_cutoff)
            )
            or 0
        )

    runtime = MARKET_REFRESH_STATE.snapshot()
    batch_stats: BatchFetchStats = runtime["batch_stats"]
    last_success_at = runtime["last_success_at"] or latest_quote_at
    coverage_percent = (
        Decimal(available_count) / Decimal(total_count) * Decimal("100")
        if total_count
        else Decimal("0")
    ).quantize(Decimal("0.1"))
    fresh_coverage_percent = (
        Decimal(fresh_count) / Decimal(total_count) * Decimal("100")
        if total_count
        else Decimal("0")
    ).quantize(Decimal("0.1"))
    round_coverage_percent = (
        Decimal(runtime["updated_count"]) / Decimal(total_count) * Decimal("100")
        if total_count
        else Decimal("0")
    ).quantize(Decimal("0.1"))
    has_completed_refresh = runtime["last_success_at"] is not None

    if runtime["refreshing"]:
        status = "refreshing"
        status_label = "正在更新"
        status_message = "正在从东方财富获取新一轮行情快照。"
    elif runtime["last_error"]:
        status = "source_error"
        status_label = "行情源异常"
        status_message = "本轮刷新失败，当前继续展示最后一次成功快照。"
    elif not available_count:
        status = "unavailable"
        status_label = "暂无行情"
        status_message = "尚未取得可用于模拟交易的真实行情。"
    elif not session_open:
        status = "closed"
        status_label = "休市快照"
        status_message = f"{session_label}，当前展示最近一次成功行情。"
    elif quote_age_seconds is None:
        status = "unavailable"
        status_label = "暂无行情"
        status_message = "交易时段内尚未取得有效行情。"
    elif quote_age_seconds > stale_threshold:
        status = "stale"
        status_label = "行情陈旧"
        status_message = (
            f"最近行情已超过 {stale_threshold} 秒陈旧阈值。"
        )
    elif quote_age_seconds > fresh_threshold:
        status = "delayed"
        status_label = "行情延迟"
        status_message = (
            f"最近行情已超过 {fresh_threshold} 秒正常阈值。"
        )
    elif fresh_coverage_percent < HEALTHY_COVERAGE_THRESHOLD_PERCENT:
        status = "partial"
        status_label = "行情不完整"
        status_message = (
            f"仅 {fresh_count}/{total_count} 只行情处于新鲜阈值内"
            f"（{fresh_coverage_percent}%），低于 "
            f"{HEALTHY_COVERAGE_THRESHOLD_PERCENT}% 健康标准。"
        )
    elif (
        has_completed_refresh
        and round_coverage_percent < HEALTHY_COVERAGE_THRESHOLD_PERCENT
    ):
        status = "partial"
        status_label = "行情不完整"
        status_message = (
            f"最近一轮仅更新 {runtime['updated_count']}/{total_count} 只"
            f"（{round_coverage_percent}%），低于 "
            f"{HEALTHY_COVERAGE_THRESHOLD_PERCENT}% 完整标准。"
        )
    else:
        status = "fresh"
        status_label = "行情正常"
        status_message = (
            f"新鲜行情覆盖 {fresh_count}/{total_count} 只"
            f"（{fresh_coverage_percent}%）。"
        )

    return {
        "provider": "eastmoney",
        "provider_label": "东方财富",
        "source_type": "REAL_PUBLIC_QUOTE",
        "trading_mode": "SIMULATED",
        "status": status,
        "status_label": status_label,
        "status_message": status_message,
        "session": session,
        "session_label": session_label,
        "session_open": session_open,
        "refresh_interval_seconds": interval,
        "fresh_threshold_seconds": fresh_threshold,
        "stale_threshold_seconds": stale_threshold,
        "last_attempt_at": runtime["last_attempt_at"],
        "last_success_at": last_success_at,
        "latest_quote_at": latest_quote_at,
        "quote_age_seconds": quote_age_seconds,
        "updated_count": runtime["updated_count"],
        "available_count": available_count,
        "fresh_count": fresh_count,
        "total_count": total_count,
        "coverage_percent": coverage_percent,
        "fresh_coverage_percent": fresh_coverage_percent,
        "round_coverage_percent": round_coverage_percent,
        "healthy_coverage_threshold_percent": HEALTHY_COVERAGE_THRESHOLD_PERCENT,
        "batch_total_count": batch_stats.total_batches,
        "batch_success_count": batch_stats.successful_batches,
        "batch_failed_count": batch_stats.failed_batches,
        "batch_retry_count": batch_stats.retried_batches,
        "batch_recovered_count": batch_stats.recovered_batches,
        "batch_request_attempts": batch_stats.request_attempts,
        "batch_pause_count": batch_stats.pause_count,
        "batch_fallback_used": batch_stats.fallback_used,
        "batch_success_percent": batch_stats.success_percent,
        "batch_elapsed_seconds": batch_stats.elapsed_seconds,
        "last_error": runtime["last_error"],
        "observed_at": observed_at,
    }

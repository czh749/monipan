"""行情服务的数据类型、自定义异常与运行时状态。"""

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class BatchFetchStats:
    """记录最近一轮股票行情批次的最终结果。"""

    total_batches: int = 0
    successful_batches: int = 0
    failed_batches: int = 0
    retried_batches: int = 0
    recovered_batches: int = 0
    request_attempts: int = 0
    pause_count: int = 0
    fallback_used: bool = False
    elapsed_seconds: float = 0

    @property
    def success_percent(self) -> Decimal:
        if not self.total_batches:
            return Decimal("0.0")
        return (
            Decimal(self.successful_batches)
            / Decimal(self.total_batches)
            * Decimal("100")
        ).quantize(Decimal("0.1"))


class QuoteFetchResult(dict[str, dict[str, Any]]):
    """保持字典兼容性的行情结果，同时携带批次统计。"""

    def __init__(
        self,
        quotes: dict[str, dict[str, Any]],
        stats: BatchFetchStats,
    ) -> None:
        super().__init__(quotes)
        self.stats = stats


class MarketDataError(RuntimeError):
    """行情源不可用或返回了无效数据。"""

    def __init__(
        self,
        message: str,
        batch_stats: BatchFetchStats | None = None,
    ) -> None:
        super().__init__(message)
        self.batch_stats = batch_stats


@dataclass
class MarketRefreshState:
    """保存本进程最近一轮行情任务的运行结果。"""

    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    updated_count: int = 0
    batch_stats: BatchFetchStats = field(default_factory=BatchFetchStats)
    refreshing: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def begin(self) -> None:
        with self._lock:
            self.last_attempt_at = datetime.now(UTC).replace(tzinfo=None)
            self.refreshing = True

    def succeed(
        self,
        updated_count: int,
        batch_stats: BatchFetchStats | None = None,
    ) -> None:
        with self._lock:
            self.last_success_at = datetime.now(UTC).replace(tzinfo=None)
            self.last_error = None
            self.updated_count = updated_count
            if batch_stats is not None:
                self.batch_stats = batch_stats
            self.refreshing = False

    def fail(self, error: Exception) -> None:
        with self._lock:
            self.last_error = str(error) or error.__class__.__name__
            self.updated_count = 0
            batch_stats = getattr(error, "batch_stats", None)
            if batch_stats is not None:
                self.batch_stats = batch_stats
            self.refreshing = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "last_attempt_at": self.last_attempt_at,
                "last_success_at": self.last_success_at,
                "last_error": self.last_error,
                "updated_count": self.updated_count,
                "batch_stats": self.batch_stats,
                "refreshing": self.refreshing,
            }


# 进程级单例，供 tick 与 status 共享同一份刷新状态。
MARKET_REFRESH_STATE = MarketRefreshState()

"""Daily K-line retrieval and local cache management."""

from datetime import datetime
from decimal import Decimal
import threading
import time
from typing import Any

import requests
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...models import Stock, StockBar
from .constants import EASTMONEY_REQUEST_HEADERS, logger
from .eastmoney import (
    _decimal_value,
    _eastmoney_secid,
    _nonnegative_decimal,
    _positive_decimal,
)
from .types import MarketDataError


EASTMONEY_HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
HISTORY_CACHE_TARGET_BARS = 50
HISTORY_FETCH_LIMIT = 90
HISTORY_REQUEST_INTERVAL_SECONDS = 3.0
HISTORY_FAILURE_COOLDOWN_SECONDS = 15.0

_HISTORY_REQUEST_LOCK = threading.Lock()
_history_next_request_at = 0.0


def fetch_eastmoney_history(
    symbol: str,
    limit: int = HISTORY_FETCH_LIMIT,
    *,
    max_wait_seconds: float = 5.0,
) -> list[dict[str, Any]]:
    """Fetch forward-adjusted daily bars from Eastmoney's public quote endpoint."""
    params = {
        "secid": _eastmoney_secid(symbol),
        "klt": "101",
        "fqt": "1",
        "lmt": str(limit),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    global _history_next_request_at
    with _HISTORY_REQUEST_LOCK:
        wait_seconds = max(0.0, _history_next_request_at - time.monotonic())
        if wait_seconds > max_wait_seconds:
            raise MarketDataError(
                f"历史行情请求正在冷却，约 {wait_seconds:.0f} 秒后重试"
            )
        if wait_seconds:
            time.sleep(wait_seconds)
        try:
            response = requests.get(
                EASTMONEY_HISTORY_URL,
                params=params,
                headers=EASTMONEY_REQUEST_HEADERS,
                timeout=10,
            )
            response.raise_for_status()
            rows = (response.json().get("data") or {}).get("klines")
            if not isinstance(rows, list):
                raise ValueError("历史行情返回格式异常")
        except (requests.RequestException, ValueError) as exc:
            _history_next_request_at = (
                time.monotonic() + HISTORY_FAILURE_COOLDOWN_SECONDS
            )
            raise MarketDataError(f"历史行情获取失败：{exc}") from exc
        _history_next_request_at = time.monotonic() + HISTORY_REQUEST_INTERVAL_SECONDS

    result: list[dict[str, Any]] = []
    for raw in rows:
        fields = raw.split(",") if isinstance(raw, str) else []
        if len(fields) < 7:
            continue
        open_price = _positive_decimal(fields[1])
        close_price = _positive_decimal(fields[2])
        high_price = _positive_decimal(fields[3])
        low_price = _positive_decimal(fields[4])
        turnover = _nonnegative_decimal(fields[6]) or Decimal("0")
        provider_change_percent = (
            _decimal_value(fields[8]) if len(fields) > 8 else None
        )
        provider_change = _decimal_value(fields[9]) if len(fields) > 9 else None
        try:
            trade_date = datetime.strptime(fields[0], "%Y-%m-%d").date()
            volume = max(0, int(float(fields[5]) * 100))
        except (TypeError, ValueError):
            continue
        if not all((open_price, close_price, high_price, low_price)):
            continue
        if high_price < max(open_price, close_price) or low_price > min(
            open_price, close_price
        ):
            continue
        previous_close = (
            close_price - provider_change
            if provider_change is not None
            else (result[-1]["close_price"] if result else None)
        )
        if previous_close is not None and previous_close <= 0:
            previous_close = None
        change = (
            provider_change
            if provider_change is not None
            else (close_price - previous_close if previous_close is not None else None)
        )
        change_percent = provider_change_percent
        if change_percent is None and previous_close is not None and change is not None:
            change_percent = (
                change / previous_close * Decimal("100")
            ).quantize(Decimal("0.0001"))
        result.append(
            {
                "trade_date": trade_date,
                "open_price": open_price,
                "high_price": high_price,
                "low_price": low_price,
                "close_price": close_price,
                "prev_close": previous_close,
                "change_amount": change,
                "change_percent": change_percent,
                "volume": volume,
                "turnover": turnover,
            }
        )
    if not result:
        raise MarketDataError("历史行情没有返回有效 K 线")
    return result


def upsert_stock_bars(db: Session, stock: Stock, rows: list[dict[str, Any]]) -> None:
    dates = [row["trade_date"] for row in rows]
    existing = {
        bar.trade_date: bar
        for bar in db.scalars(
            select(StockBar).where(
                StockBar.stock_id == stock.id,
                StockBar.trade_date.in_(dates),
            )
        ).all()
    }
    for row in rows:
        bar = existing.get(row["trade_date"])
        if bar is None:
            bar = StockBar(stock_id=stock.id, trade_date=row["trade_date"])
            db.add(bar)
        for field in (
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
            "turnover",
        ):
            setattr(bar, field, row[field])
        for field in ("prev_close", "change_amount", "change_percent"):
            if field in row:
                setattr(bar, field, row[field])


def reconcile_authoritative_stock_bars(
    db: Session,
    stock: Stock,
    rows: list[dict[str, Any]],
) -> None:
    """Remove cached dates contradicted by a successful provider history pull."""
    if not rows:
        return
    authoritative_dates = {row["trade_date"] for row in rows}
    earliest_date = min(authoritative_dates)
    latest_date = max(authoritative_dates)
    for bar in db.scalars(
        select(StockBar).where(StockBar.stock_id == stock.id)
    ).all():
        impossible_weekend = bar.trade_date.weekday() >= 5
        missing_inside_window = (
            earliest_date <= bar.trade_date <= latest_date
            and bar.trade_date not in authoritative_dates
        )
        stale_future_snapshot = (
            bar.trade_date > latest_date
            and bar.trade_date != stock.quote_trade_date
        )
        if impossible_weekend or missing_inside_window or stale_future_snapshot:
            db.delete(bar)


def prune_impossible_weekend_bars(db: Session, stock: Stock) -> None:
    """Weekend dates can never be A-share daily bars, even without network access."""
    for bar in db.scalars(
        select(StockBar).where(StockBar.stock_id == stock.id)
    ).all():
        if bar.trade_date.weekday() >= 5:
            db.delete(bar)


def upsert_latest_stock_bar(db: Session, stock: Stock) -> None:
    """Keep a truthful daily bar even when the historical endpoint is unavailable."""
    if stock.quote_trade_date is None or min(stock.price, stock.prev_close) <= 0:
        return
    open_price = stock.open_price if stock.open_price > 0 else stock.prev_close
    high_price = max(stock.high_price, open_price, stock.price)
    low_price = min(
        stock.low_price if stock.low_price > 0 else min(open_price, stock.price),
        open_price,
        stock.price,
    )
    change = stock.price - stock.prev_close
    change_percent = (
        change / stock.prev_close * Decimal("100")
    ).quantize(Decimal("0.0001"))
    upsert_stock_bars(
        db,
        stock,
        [{
            "trade_date": stock.quote_trade_date,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "close_price": stock.price,
            "prev_close": stock.prev_close,
            "change_amount": change,
            "change_percent": change_percent,
            "volume": stock.volume,
            "turnover": Decimal("0"),
        }],
    )


def stock_history(db: Session, stock: Stock, limit: int) -> tuple[str, list[StockBar]]:
    """Return history from the provider when possible, otherwise cached snapshots."""
    source = "CACHED_HISTORY"
    prune_impossible_weekend_bars(db, stock)
    db.flush()
    cached_count = len(
        db.scalars(select(StockBar.id).where(StockBar.stock_id == stock.id)).all()
    )
    incomplete_metric_count = db.scalar(
        select(func.count(StockBar.id)).where(
            StockBar.stock_id == stock.id,
            StockBar.prev_close.is_(None),
        )
    ) or 0
    target_count = min(limit, HISTORY_CACHE_TARGET_BARS)
    # A single oldest row may legitimately lack a pre-window previous close.
    # Multiple missing values identify a legacy cache that predates bar metrics.
    if cached_count < target_count or incomplete_metric_count > 1:
        try:
            rows = fetch_eastmoney_history(stock.symbol, limit)
            reconcile_authoritative_stock_bars(db, stock, rows)
            upsert_stock_bars(db, stock, rows)
            source = "EASTMONEY_HISTORY"
        except MarketDataError as exc:
            logger.warning(
                "历史行情按需补取失败：symbol=%s cached=%s target=%s error=%s",
                stock.symbol,
                cached_count,
                target_count,
                exc,
            )
            source = "LATEST_SNAPSHOT"

    upsert_latest_stock_bar(db, stock)
    db.commit()
    bars = db.scalars(
        select(StockBar)
        .where(StockBar.stock_id == stock.id)
        .order_by(StockBar.trade_date.desc())
        .limit(limit)
    ).all()
    bars.reverse()
    if source == "LATEST_SNAPSHOT" and len(bars) > 1:
        source = "CACHED_HISTORY"
    return source, bars


def backfill_stock_history_once(
    db: Session,
    after_stock_id: int = 0,
    *,
    target_count: int = HISTORY_CACHE_TARGET_BARS,
) -> tuple[int, str | None, bool]:
    """Backfill one deficient stock and advance even when that stock fails.

    The cursor makes the background worker rotate through the whole stock pool;
    a throttled symbol therefore cannot starve every symbol behind it.
    """
    bar_count = (
        select(func.count(StockBar.id))
        .where(StockBar.stock_id == Stock.id)
        .correlate(Stock)
        .scalar_subquery()
    )
    incomplete_metric_count = (
        select(func.count(StockBar.id))
        .where(
            StockBar.stock_id == Stock.id,
            StockBar.prev_close.is_(None),
        )
        .correlate(Stock)
        .scalar_subquery()
    )

    def candidate(after_id: int) -> Stock | None:
        return db.scalar(
            select(Stock)
            .where(
                Stock.id > after_id,
                Stock.price > 0,
                Stock.prev_close > 0,
                or_(bar_count < target_count, incomplete_metric_count > 1),
            )
            .order_by(Stock.id.asc())
            .limit(1)
        )

    stock = candidate(after_stock_id)
    if stock is None and after_stock_id:
        stock = candidate(0)
    if stock is None:
        return 0, None, True

    try:
        rows = fetch_eastmoney_history(
            stock.symbol,
            max(HISTORY_FETCH_LIMIT, target_count),
            max_wait_seconds=5.0,
        )
        reconcile_authoritative_stock_bars(db, stock, rows)
        upsert_stock_bars(db, stock, rows)
        upsert_latest_stock_bar(db, stock)
        db.commit()
        cached_count = db.scalar(
            select(func.count(StockBar.id)).where(StockBar.stock_id == stock.id)
        ) or 0
        logger.info(
            "历史行情后台补全：symbol=%s cached=%s target=%s",
            stock.symbol,
            cached_count,
            target_count,
        )
        return stock.id, stock.symbol, cached_count >= target_count
    except MarketDataError as exc:
        db.rollback()
        logger.warning(
            "历史行情后台补全延后：symbol=%s error=%s",
            stock.symbol,
            exc,
        )
        return stock.id, stock.symbol, False

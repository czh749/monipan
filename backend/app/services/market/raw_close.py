"""Unadjusted closes for replayable account valuation."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ...models import RawCloseSync, Stock, StockRawClose, Trade, utcnow
from .constants import CHINA_TZ, logger
from .history import fetch_eastmoney_history
from .types import MarketDataError


RAW_HISTORY_LIMIT = 500


def _upsert_statement(dialect: str, values: list[dict], *, source: str):
    """Build an atomic write for the two supported database engines."""
    if dialect == "sqlite":
        statement = sqlite_insert(StockRawClose).values(values)
        update = {
            "close_price": statement.excluded.close_price,
            "source": statement.excluded.source,
            "updated_at": statement.excluded.updated_at,
        }
        statement = statement.on_conflict_do_update(
            index_elements=[StockRawClose.stock_id, StockRawClose.trade_date],
            set_=update,
            where=StockRawClose.source != "HISTORY" if source == "SNAPSHOT" else None,
        )
    elif dialect == "mysql":
        statement = mysql_insert(StockRawClose).values(values)
        if source == "SNAPSHOT":
            keep_history = StockRawClose.source == "HISTORY"
            statement = statement.on_duplicate_key_update(
                close_price=case((keep_history, StockRawClose.close_price), else_=statement.inserted.close_price),
                source=case((keep_history, StockRawClose.source), else_=statement.inserted.source),
                updated_at=case((keep_history, StockRawClose.updated_at), else_=statement.inserted.updated_at),
            )
        else:
            statement = statement.on_duplicate_key_update(
                close_price=statement.inserted.close_price,
                source=statement.inserted.source,
                updated_at=statement.inserted.updated_at,
            )
    else:
        raise RuntimeError(f"不支持的原始收盘价数据库：{dialect}")
    return statement


def _upsert_prices(db: Session, values: list[dict], *, source: str) -> None:
    """Use a database upsert so the quote loop cannot race history backfill."""
    db.execute(_upsert_statement(db.get_bind().dialect.name, values, source=source))


def upsert_quote_raw_close(db: Session, stock: Stock) -> None:
    """Quote prices are raw, but remain provisional until history confirms them."""
    if stock.quote_trade_date is None or stock.price <= 0:
        return
    _upsert_prices(db, [{
        "stock_id": stock.id,
        "trade_date": stock.quote_trade_date,
        "close_price": stock.price,
        "source": "SNAPSHOT",
        "updated_at": utcnow(),
    }], source="SNAPSHOT")


def upsert_raw_history(db: Session, stock: Stock, rows: list[dict]) -> int:
    """Only completed dates become confirmed historical closes."""
    completed = datetime.now(CHINA_TZ).date() - timedelta(days=1)
    valid = [row for row in rows if row["trade_date"] <= completed and Decimal(row["close_price"]) > 0]
    if not valid:
        return 0
    now = utcnow()
    _upsert_prices(db, [{
        "stock_id": stock.id,
        "trade_date": item["trade_date"],
        "close_price": Decimal(item["close_price"]),
        "source": "HISTORY",
        "updated_at": now,
    } for item in valid], source="HISTORY")
    return len(valid)


def backfill_raw_closes_once(db: Session, after_stock_id: int = 0) -> tuple[int, str | None, bool]:
    """Fetch one traded symbol with bounded provider pressure and retry cooldown."""
    now = utcnow()
    stale_success = now - timedelta(hours=24)
    stale_failure = now - timedelta(minutes=15)
    traded = select(Trade.stock_id).distinct()

    def candidate(cursor: int) -> Stock | None:
        return db.scalar(
            select(Stock)
            .outerjoin(RawCloseSync, RawCloseSync.stock_id == Stock.id)
            .where(
                Stock.id.in_(traded),
                Stock.id > cursor,
                or_(
                    RawCloseSync.stock_id.is_(None),
                    and_(RawCloseSync.last_success_at.is_(None), RawCloseSync.last_attempt_at < stale_failure),
                    RawCloseSync.last_success_at < stale_success,
                ),
            )
            .order_by(Stock.id)
            .limit(1)
        )

    stock = candidate(after_stock_id)
    if stock is None and after_stock_id:
        stock = candidate(0)
    if stock is None:
        return 0, None, True
    sync = db.get(RawCloseSync, stock.id)
    if sync is None:
        sync = RawCloseSync(stock_id=stock.id, last_attempt_at=now)
        db.add(sync)
    else:
        sync.last_attempt_at = now
    try:
        rows = fetch_eastmoney_history(stock.symbol, RAW_HISTORY_LIMIT, adjusted=False)
        upsert_raw_history(db, stock, rows)
        sync.last_success_at = utcnow()
        db.commit()
        return stock.id, stock.symbol, True
    except MarketDataError as exc:
        db.commit()
        logger.warning("未复权历史收盘价补全延后：symbol=%s error=%s", stock.symbol, exc)
        return stock.id, stock.symbol, False

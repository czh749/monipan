"""Account-serialized quota and shared Stock identity creation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import time

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from ..models import SecurityCatalogEntry, SimulationAccount, Stock, WatchlistItem
from .stock_activity import CUSTOM_STOCK_LIMIT, DEFAULT_SYMBOLS


def lock_account(db, account_id):
    # MySQL locking reads see the latest committed membership even under
    # REPEATABLE READ. SQLite has no FOR UPDATE, so reserve its writer lock.
    if db.bind.dialect.name == "sqlite":
        db.execute(update(SimulationAccount).where(SimulationAccount.id == account_id)
                   .values(available_cash=SimulationAccount.available_cash))
    return db.scalar(select(SimulationAccount).where(SimulationAccount.id == account_id).with_for_update())


def add_research_watchlist(db, account_id, symbol):
    for attempt in range(3):
        try:
            return _add_research_watchlist_once(db, account_id, symbol)
        except OperationalError as exc:
            db.rollback()
            # An InnoDB deadlock rolls back the transaction, including its
            # savepoint. Retry the whole account-locked operation, bounded.
            if not getattr(exc.orig, "args", ()) or exc.orig.args[0] not in (1205, 1213):
                raise
            if attempt == 2:
                raise HTTPException(503, "添加请求繁忙，请稍后重试") from exc
            time.sleep(0.05 * (attempt + 1))


def _add_research_watchlist_once(db, account_id, symbol):
    lock_account(db, account_id)
    # Locking reads avoid an earlier authentication SELECT's stale snapshot.
    items = db.scalars(select(WatchlistItem).where(
        WatchlistItem.account_id == account_id).with_for_update()).all()
    stocks = {s.id: s for s in db.scalars(select(Stock).where(
        Stock.id.in_([item.stock_id for item in items])).with_for_update()).all()}
    for item in items:
        if stocks[item.stock_id].symbol == symbol:
            db.commit()
            return {"symbol": symbol, "created_at": item.created_at}
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if symbol not in DEFAULT_SYMBOLS:
        entry = db.scalar(select(SecurityCatalogEntry).where(
            SecurityCatalogEntry.symbol == symbol, SecurityCatalogEntry.is_current.is_(True)))
        if entry is None:
            raise HTTPException(404, "证券不在当前沪深 A 股目录中")
        if entry.collected_at < datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7):
            raise HTTPException(503, "证券目录已超过 7 天，请先更新目录后再添加")
        if sum(s.symbol not in DEFAULT_SYMBOLS for s in stocks.values()) >= CUSTOM_STOCK_LIMIT:
            raise HTTPException(409, f"池外研究股票最多 {CUSTOM_STOCK_LIMIT} 只，请先移除一只")
        if stock is None:
            try:
                with db.begin_nested():
                    stock = Stock(symbol=symbol, name=entry.name, exchange=entry.exchange,
                                  industry=entry.industry[:40], research_only=True,
                                  price=Decimal(0), prev_close=Decimal(0), open_price=Decimal(0),
                                  high_price=Decimal(0), low_price=Decimal(0), volume=0,
                                  updated_at=datetime(1970, 1, 1))
                    db.add(stock)
                    db.flush()
            except IntegrityError:
                # A different account may have created the shared identity.
                stock = db.scalar(select(Stock).where(Stock.symbol == symbol).with_for_update())
                if stock is None:
                    raise
        stock.research_only = True
    elif stock is None:
        raise HTTPException(404, "默认股票尚未初始化")
    item = WatchlistItem(account_id=account_id, stock_id=stock.id)
    db.add(item)
    db.commit()
    return {"symbol": symbol, "created_at": item.created_at}

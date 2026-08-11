from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .market_index_pool import MARKET_INDEX_POOL
from .models import MarketIndex, Stock
from .stock_pool import STOCK_POOL


def seed_database(db: Session) -> None:
    # 按代码补齐股票池，而不是仅在空表时初始化。这样已有的 100 只股票、
    # 持仓和历史交易都会保留，升级后只新增缺少的 100 只。
    existing_symbols = set(db.scalars(select(Stock.symbol)).all())
    for symbol, name, exchange, industry in STOCK_POOL:
        if symbol in existing_symbols:
            continue
        db.add(
            Stock(
                symbol=symbol,
                name=name,
                exchange=exchange,
                industry=industry,
                # 新股票不再生成随机价格；0 表示尚未取得真实行情。
                prev_close=Decimal("0"),
                price=Decimal("0"),
                open_price=Decimal("0"),
                high_price=Decimal("0"),
                low_price=Decimal("0"),
                volume=0,
                updated_at=datetime(1970, 1, 1),
            )
        )

    existing_indices = set(db.scalars(select(MarketIndex.symbol)).all())
    for symbol, name, exchange, _secid, display_order in MARKET_INDEX_POOL:
        if symbol in existing_indices:
            continue
        db.add(
            MarketIndex(
                symbol=symbol,
                name=name,
                exchange=exchange,
                display_order=display_order,
                price=Decimal("0"),
                change=Decimal("0"),
                change_percent=Decimal("0"),
                turnover=Decimal("0"),
                updated_at=datetime(1970, 1, 1),
            )
        )
    db.commit()

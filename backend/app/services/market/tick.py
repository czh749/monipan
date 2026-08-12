"""行情刷新：将东财返回的行情覆盖写入数据库。"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...market_index_pool import MARKET_INDEX_POOL
from ...models import MarketIndex, Stock
from .constants import logger
from .history import upsert_latest_stock_bar
from .types import MARKET_REFRESH_STATE


def tick_market_indices(db: Session) -> int:
    """覆盖更新固定指数池，失败时保留最后一次有效快照。"""
    # 延迟导入：测试通过 monkeypatch.setattr(market, "fetch_eastmoney_indices", ...)
    # 替换行情源，必须从包级别查找才能看到 patch 后的值。
    from . import fetch_eastmoney_indices

    quotes = fetch_eastmoney_indices()
    indices = {
        item.symbol: item
        for item in db.scalars(select(MarketIndex)).all()
    }
    updated_count = 0
    for symbol, name, exchange, _secid, display_order in MARKET_INDEX_POOL:
        quote = quotes.get(symbol)
        if not quote:
            continue
        item = indices.get(symbol)
        if item is None:
            item = MarketIndex(
                symbol=symbol,
                name=name,
                exchange=exchange,
                display_order=display_order,
                price=Decimal("0"),
                change=Decimal("0"),
                change_percent=Decimal("0"),
                turnover=Decimal("0"),
            )
            db.add(item)
        item.name = name
        item.exchange = exchange
        item.display_order = display_order
        item.price = quote["price"]
        item.change = quote["change"]
        item.change_percent = quote["change_percent"]
        item.turnover = quote["turnover"]
        item.updated_at = quote["updated_at"]
        updated_count += 1

    db.commit()
    return updated_count


def tick_market(db: Session) -> int:
    """
    刷新一次行情并覆盖股票表中的最新值。

    真实行情获取失败时会抛出 MarketDataError，且不会提交或清空旧数据。
    生产环境只使用真实东财行情，不提供随机行情回退。
    """
    MARKET_REFRESH_STATE.begin()
    try:
        # 延迟导入：测试通过 monkeypatch.setattr(market, "fetch_eastmoney_quotes", ...)
        # 替换行情源，必须从包级别查找才能看到 patch 后的值。
        from . import fetch_eastmoney_quotes

        # 最久未成功更新的股票优先，避免每轮部分成功时总是同一批股票陈旧。
        stocks = db.scalars(
            select(Stock).order_by(Stock.updated_at.asc(), Stock.id.asc())
        ).all()
        quotes = fetch_eastmoney_quotes([stock.symbol for stock in stocks])
        batch_stats = getattr(quotes, "stats", None)
        updated_count = 0
        for stock in stocks:
            quote = quotes.get(stock.symbol)
            if not quote:
                continue
            quote_trade_date = quote.get("quote_trade_date")
            same_trade_date = (
                quote_trade_date is not None
                and stock.quote_trade_date == quote_trade_date
            )
            quoted_open = quote.get("open_price")
            quoted_high = quote.get("high_price")
            quoted_low = quote.get("low_price")
            stock.price = quote["price"]
            stock.prev_close = quote["prev_close"]
            stock.open_price = (
                quoted_open
                or (stock.open_price if same_trade_date else stock.prev_close)
            )
            stock.high_price = max(
                quoted_high
                or (stock.high_price if same_trade_date else stock.open_price),
                stock.open_price,
                stock.price,
            )
            stock.low_price = min(
                quoted_low
                or (stock.low_price if same_trade_date else stock.open_price),
                stock.open_price,
                stock.price,
            )
            if quote.get("volume") is not None:
                stock.volume = quote["volume"]
            elif not same_trade_date:
                stock.volume = 0
            if quote.get("name"):
                stock.name = quote["name"]
            if quote_trade_date is not None:
                stock.quote_trade_date = quote_trade_date
            stock.updated_at = quote["updated_at"]
            if quote_trade_date is not None:
                upsert_latest_stock_bar(db, stock)
            updated_count += 1

        db.commit()
        # 行情落库后撮合已触价的限价委托。延迟导入避免行情与交易服务循环依赖。
        from ..trading import match_pending_orders

        try:
            match_pending_orders(db)
        except Exception:
            # 撮合故障不能把已经成功落库的公开行情标记为刷新失败。
            logger.exception("限价委托撮合失败，行情快照已保留")
    except Exception as exc:
        db.rollback()
        MARKET_REFRESH_STATE.fail(exc)
        raise

    MARKET_REFRESH_STATE.succeed(updated_count, batch_stats=batch_stats)
    return updated_count

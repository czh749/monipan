"""将 ORM 对象格式化为前端友好的字典。"""

from decimal import Decimal
from typing import Any

from ...models import MarketIndex, Stock
from .constants import CENT


def market_index_values(item: MarketIndex) -> dict[str, Any]:
    """将大盘指数 ORM 对象格式化为 API 响应。"""
    return {
        "symbol": item.symbol,
        "name": item.name,
        "exchange": item.exchange,
        "price": item.price,
        "change": item.change,
        "change_percent": item.change_percent,
        "turnover": item.turnover,
        "updated_at": item.updated_at,
    }


def stock_values(stock: Stock) -> dict:
    """将 Stock ORM 对象格式化为前端使用的行情字典。"""
    change = (stock.price - stock.prev_close).quantize(CENT)
    percent = (
        change / stock.prev_close * Decimal("100")
        if stock.prev_close
        else Decimal("0")
    ).quantize(Decimal("0.01"))

    return {
        "symbol": stock.symbol,
        "name": stock.name,
        "exchange": stock.exchange,
        "industry": stock.industry,
        "prev_close": stock.prev_close,
        "price": stock.price,
        "open_price": stock.open_price,
        "high_price": stock.high_price,
        "low_price": stock.low_price,
        "volume": stock.volume,
        "change": change,
        "change_percent": percent,
        "updated_at": stock.updated_at,
    }

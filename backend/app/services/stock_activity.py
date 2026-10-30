"""One scope for shared collection and one private scope for each account."""

from sqlalchemy import exists, or_, select

from ..models import Order, Position, SimulationAccount, Stock, User, WatchlistItem
from ..stock_pool import STOCK_POOL

DEFAULT_SYMBOLS = frozenset(row[0] for row in STOCK_POOL)
CUSTOM_STOCK_LIMIT = 30


def active_stock_condition(account_id: int | None = None):
    watch = select(WatchlistItem.id).where(WatchlistItem.stock_id == Stock.id)
    if account_id is not None:
        watch = watch.where(WatchlistItem.account_id == account_id)
    else:
        watch = watch.join(SimulationAccount, SimulationAccount.id == WatchlistItem.account_id).join(
            User, User.id == SimulationAccount.user_id).where(User.status == "ACTIVE")
    held = select(Position.id).where(Position.stock_id == Stock.id, Position.quantity > 0)
    pending = select(Order.id).where(Order.stock_id == Stock.id, Order.status == "PENDING")
    if account_id is not None:
        held = held.where(Position.account_id == account_id)
        pending = pending.where(Order.account_id == account_id)
    return or_(Stock.symbol.in_(DEFAULT_SYMBOLS), exists(watch), exists(held), exists(pending))

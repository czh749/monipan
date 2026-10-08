"""Trading engine with A-share lot, T+1, price-limit and limit-order rules."""

from datetime import UTC, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..models import (
    AccountTransaction,
    Order,
    Position,
    SimulationAccount,
    Stock,
    Trade,
)
from .market.constants import CHINA_TZ
from .market.session import is_continuous_trading, market_refresh_interval, market_session


CENT = Decimal("0.01")
COMMISSION_RATE = Decimal("0.0003")
MIN_COMMISSION = Decimal("5.00")
SELL_STAMP_DUTY_RATE = Decimal("0.0005")


def trading_now() -> datetime:
    """Clock boundary kept in one place for deterministic order tests."""
    return datetime.now(UTC)


def quote_blocking_reason(stock: Stock, now: datetime) -> str | None:
    """Reject cached or non-session quotes before they can set a fill price."""
    if stock.quote_source_at is None or stock.quote_trade_date is None:
        return "缺少行情源时间，暂不可按该价格模拟成交"
    source_at = stock.quote_source_at.replace(tzinfo=UTC)
    fetched_at = stock.updated_at.replace(tzinfo=UTC)
    if stock.quote_trade_date != now.astimezone(CHINA_TZ).date():
        return "该股票行情并非当前交易日，暂不可下单"
    if not is_continuous_trading(source_at):
        return "该股票尚无连续竞价时段的有效报价，暂不可下单"
    max_age = int(market_refresh_interval() * 2.5 + 0.5)
    source_age = (now - source_at).total_seconds()
    fetch_age = (now - fetched_at).total_seconds()
    if source_age < -60 or fetch_age < -60:
        return "行情时间异常，暂不可下单"
    if source_age > max_age or fetch_age > max_age:
        return f"该股票行情超过 {max_age} 秒新鲜阈值，暂不可下单"
    return None


def trading_blocking_reason(stock: Stock, now: datetime) -> str | None:
    session, label, _ = market_session(now)
    if session not in {"morning", "afternoon"}:
        return f"{label}，仅连续竞价时段接受模拟委托"
    return quote_blocking_reason(stock, now)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def calculate_fee(side: str, amount: Decimal) -> Decimal:
    commission = max(MIN_COMMISSION, money(amount * COMMISSION_RATE))
    stamp_duty = money(amount * SELL_STAMP_DUTY_RATE) if side == "SELL" else Decimal("0")
    return money(commission + stamp_duty)


def price_limit_rate(stock: Stock) -> Decimal:
    """Current board rate; main-board ST also uses 10% since 2026-07-06."""
    if stock.exchange == "BSE" or stock.symbol.startswith(("4", "8", "92")):
        return Decimal("0.30")
    if stock.symbol.startswith(("688", "300", "301")):
        return Decimal("0.20")
    return Decimal("0.10")


def price_limits(stock: Stock) -> tuple[Decimal, Decimal, Decimal]:
    rate = price_limit_rate(stock)
    upper = money(stock.prev_close * (Decimal("1") + rate))
    lower = money(stock.prev_close * (Decimal("1") - rate))
    return rate, upper, lower


def _today_utc_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    observed = now or trading_now()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    local_date = observed.astimezone(CHINA_TZ).date()
    start_local = datetime.combine(local_date, time.min, tzinfo=CHINA_TZ)
    end_local = datetime.combine(local_date, time.max, tzinfo=CHINA_TZ)
    return (
        start_local.astimezone(UTC).replace(tzinfo=None),
        end_local.astimezone(UTC).replace(tzinfo=None),
    )


def position_sellable_values(
    db: Session,
    account_id: int,
    position: Position | None,
    *,
    exclude_order_id: int | None = None,
    now: datetime | None = None,
) -> tuple[int, int, int]:
    """Return T+1 sellable, frozen pending-sell and currently orderable shares."""
    if position is None or position.quantity <= 0:
        return 0, 0, 0
    start, end = _today_utc_bounds(now)
    bought_today = db.scalar(
        select(func.coalesce(func.sum(Trade.quantity), 0)).where(
            Trade.account_id == account_id,
            Trade.stock_id == position.stock_id,
            Trade.side == "BUY",
            Trade.created_at >= start,
            Trade.created_at <= end,
        )
    ) or 0
    pending_query = select(
        func.coalesce(func.sum(Order.quantity - Order.filled_quantity), 0)
    ).where(
        Order.account_id == account_id,
        Order.stock_id == position.stock_id,
        Order.side == "SELL",
        Order.status == "PENDING",
    )
    if exclude_order_id is not None:
        pending_query = pending_query.where(Order.id != exclude_order_id)
    frozen = int(db.scalar(pending_query) or 0)
    sellable = max(0, position.quantity - int(bought_today))
    return sellable, frozen, max(0, sellable - frozen)


def _pending_buy_reserve(db: Session, account_id: int) -> Decimal:
    orders = db.scalars(
        select(Order).where(
            Order.account_id == account_id,
            Order.side == "BUY",
            Order.status == "PENDING",
        )
    ).all()
    return sum(
        (
            money((order.limit_price or order.price) * (order.quantity - order.filled_quantity))
            + calculate_fee("BUY", money((order.limit_price or order.price) * (order.quantity - order.filled_quantity)))
            for order in orders
        ),
        Decimal("0"),
    )


def _max_buy_quantity(buying_power: Decimal, price: Decimal) -> int:
    if buying_power <= 0 or price <= 0:
        return 0
    quantity = int(buying_power / price) // 100 * 100
    while quantity > 0:
        amount = money(price * quantity)
        if amount + calculate_fee("BUY", amount) <= buying_power:
            return quantity
        quantity -= 100
    return 0


def _portfolio_market_value(db: Session, account_id: int) -> Decimal:
    positions = db.scalars(
        select(Position)
        .where(Position.account_id == account_id, Position.quantity > 0)
        .options(joinedload(Position.stock))
    ).all()
    return sum((item.stock.price * item.quantity for item in positions), Decimal("0"))


def build_order_preview(
    db: Session,
    account: SimulationAccount,
    symbol: str,
    side: str,
    quantity: int,
    order_type: str = "MARKET",
    limit_price: Decimal | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    observed_at = now or trading_now()
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    if stock.price <= 0 or stock.prev_close <= 0:
        raise HTTPException(status_code=503, detail="该股票尚未取得真实行情，暂不可下单")

    position = db.scalar(
        select(Position).where(
            Position.account_id == account.id,
            Position.stock_id == stock.id,
        )
    )
    sellable, frozen, orderable = position_sellable_values(db, account.id, position)
    rate, upper, lower = price_limits(stock)
    estimate_price = limit_price if order_type == "LIMIT" and limit_price else stock.price
    amount = money(estimate_price * quantity)
    fee = calculate_fee(side, amount)
    estimated_total = money(amount + fee if side == "BUY" else amount - fee)
    reserve = _pending_buy_reserve(db, account.id)
    buying_power = max(Decimal("0"), account.available_cash - reserve)
    max_quantity = (
        _max_buy_quantity(buying_power, estimate_price)
        if side == "BUY"
        else orderable
    )

    blocking_reason = trading_blocking_reason(stock, observed_at)
    if blocking_reason is None:
        if order_type == "LIMIT" and limit_price is not None and not lower <= limit_price <= upper:
            blocking_reason = f"委托价须在当日涨跌停区间 ¥{lower}—¥{upper} 内"
        elif side == "BUY" and estimated_total > buying_power:
            blocking_reason = "可用资金不足（已扣除待成交买单占用）"
        elif side == "SELL" and quantity > orderable:
            blocking_reason = "T+1 可卖数量不足或已有待成交卖单占用"
        elif order_type == "MARKET" and side == "BUY" and stock.price >= upper:
            blocking_reason = "当前已涨停，市价买入无法保证成交"
        elif order_type == "MARKET" and side == "SELL" and stock.price <= lower:
            blocking_reason = "当前已跌停，市价卖出无法保证成交"

    current_market_value = _portfolio_market_value(db, account.id)
    position_value_delta = stock.price * quantity * (Decimal("1") if side == "BUY" else Decimal("-1"))
    post_market_value = max(Decimal("0"), current_market_value + position_value_delta)
    post_cash = (
        account.available_cash - estimated_total
        if side == "BUY"
        else account.available_cash + estimated_total
    )
    post_assets = post_cash + post_market_value
    post_ratio = (
        post_market_value / post_assets * Decimal("100")
        if post_assets > 0
        else Decimal("0")
    )
    warnings: list[str] = []
    if side == "BUY" and post_ratio >= Decimal("80"):
        warnings.append("成交后总仓位将超过 80%，请关注现金缓冲")
    current_position_value = (position.quantity if position else 0) * stock.price
    post_symbol_value = current_position_value + (
        stock.price * quantity * (Decimal("1") if side == "BUY" else Decimal("-1"))
    )
    if side == "BUY" and post_assets > 0 and post_symbol_value / post_assets >= Decimal("0.35"):
        warnings.append("成交后单股仓位将超过账户资产的 35%")
    if order_type == "LIMIT":
        warnings.append("未触及委托价时订单将进入待成交，可在委托记录中撤单")

    return {
        "symbol": stock.symbol,
        "side": side,
        "order_type": order_type,
        "quantity": quantity,
        "reference_price": stock.price,
        "limit_price": limit_price,
        "estimated_amount": amount,
        "estimated_fee": fee,
        "estimated_total": estimated_total,
        "max_quantity": max_quantity,
        "position_quantity": position.quantity if position else 0,
        "sellable_quantity": sellable,
        "frozen_sell_quantity": frozen,
        "post_available_cash": money(post_cash),
        "post_position_ratio": post_ratio.quantize(Decimal("0.01")),
        "price_limit_rate": rate * Decimal("100"),
        "upper_limit": upper,
        "lower_limit": lower,
        "quote_updated_at": stock.updated_at,
        "quote_source_at": stock.quote_source_at,
        "allowed": blocking_reason is None,
        "blocking_reason": blocking_reason,
        "warnings": warnings,
    }


def _fill_order(
    db: Session,
    order: Order,
    stock: Stock,
    account: SimulationAccount,
    execution_price: Decimal,
) -> None:
    amount = money(execution_price * order.quantity)
    fee = calculate_fee(order.side, amount)
    position = db.scalar(
        select(Position).where(
            Position.account_id == account.id,
            Position.stock_id == stock.id,
        )
    )

    if order.side == "BUY":
        total_cost = amount + fee
        if account.available_cash < total_cost:
            raise HTTPException(status_code=400, detail="订单触价，但当前可用资金不足")
        old_quantity = position.quantity if position else 0
        old_cost = position.average_cost * old_quantity if position else Decimal("0")
        if position is None:
            position = Position(account_id=account.id, stock_id=stock.id)
            db.add(position)
        position.quantity = old_quantity + order.quantity
        position.average_cost = (old_cost + total_cost) / position.quantity
        account.available_cash = money(account.available_cash - total_cost)
        cash_change = -total_cost
    else:
        _sellable, _frozen, orderable = position_sellable_values(
            db,
            account.id,
            position,
            exclude_order_id=order.id,
        )
        if position is None or orderable < order.quantity:
            raise HTTPException(status_code=400, detail="订单触价，但 T+1 可卖数量不足")
        proceeds = amount - fee
        position.quantity -= order.quantity
        if position.quantity == 0:
            position.average_cost = Decimal("0")
        account.available_cash = money(account.available_cash + proceeds)
        cash_change = proceeds

    filled_at = trading_now().astimezone(UTC).replace(tzinfo=None)
    position.updated_at = filled_at
    order.price = execution_price
    order.filled_quote_at = stock.quote_source_at
    order.fee = fee
    order.filled_quantity = order.quantity
    order.status = "FILLED"
    db.add(
        Trade(
            trade_no=f"T{uuid4().hex[:16].upper()}",
            order_id=order.id,
            account_id=account.id,
            stock_id=stock.id,
            side=order.side,
            quantity=order.quantity,
            price=execution_price,
            amount=amount,
            fee=fee,
            filled_quote_at=stock.quote_source_at,
            created_at=filled_at,
        )
    )
    db.add(
        AccountTransaction(
            account_id=account.id,
            order_id=order.id,
            transaction_type=order.side,
            amount=cash_change,
            balance_after=account.available_cash,
            created_at=filled_at,
        )
    )


def _idempotent_order(
    db: Session,
    *,
    account_id: int,
    idempotency_key: str,
) -> Order | None:
    return db.scalar(
        select(Order)
        .where(
            Order.account_id == account_id,
            Order.idempotency_key == idempotency_key,
        )
        .options(joinedload(Order.stock))
    )


def _same_order_request(
    order: Order,
    *,
    symbol: str,
    side: str,
    quantity: int,
    order_type: str,
    limit_price: Decimal | None,
) -> bool:
    expected_limit_price = limit_price if order_type == "LIMIT" else None
    return bool(
        order.stock.symbol == symbol
        and order.side == side
        and order.quantity == quantity
        and order.order_type == order_type
        and order.limit_price == expected_limit_price
    )


def _return_or_reject_idempotent_order(
    order: Order,
    *,
    symbol: str,
    side: str,
    quantity: int,
    order_type: str,
    limit_price: Decimal | None,
) -> Order:
    if not _same_order_request(
        order,
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
    ):
        raise HTTPException(
            status_code=409,
            detail="该防重复操作键已经用于另一笔委托，请重新发起下单",
        )
    return order


def place_order(
    db: Session,
    account: SimulationAccount,
    symbol: str,
    side: str,
    quantity: int,
    order_type: str = "MARKET",
    limit_price: Decimal | None = None,
    idempotency_key: str | None = None,
) -> Order:
    observed_at = trading_now()
    operation_key = idempotency_key or f"server-{uuid4().hex}"
    existing = _idempotent_order(
        db,
        account_id=account.id,
        idempotency_key=operation_key,
    )
    if existing is not None:
        return _return_or_reject_idempotent_order(
            existing,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
        )

    preview = build_order_preview(
        db, account, symbol, side, quantity, order_type, limit_price, now=observed_at
    )
    if not preview["allowed"]:
        raise HTTPException(status_code=400, detail=preview["blocking_reason"])
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    assert stock is not None
    current_blocker = trading_blocking_reason(stock, trading_now())
    if current_blocker:
        raise HTTPException(status_code=400, detail=current_blocker)
    requested_price = limit_price if order_type == "LIMIT" else stock.price
    order = Order(
        order_no=f"O{uuid4().hex[:16].upper()}",
        account_id=account.id,
        idempotency_key=operation_key,
        stock_id=stock.id,
        side=side,
        order_type=order_type,
        quantity=quantity,
        filled_quantity=0,
        price=requested_price,
        limit_price=limit_price,
        submitted_quote_price=stock.price,
        submitted_quote_at=stock.quote_source_at,
        fee=Decimal("0"),
        status="PENDING",
        created_at=observed_at.astimezone(UTC).replace(tzinfo=None),
    )
    try:
        db.add(order)
        db.flush()
        marketable = order_type == "MARKET" or (
            side == "BUY" and limit_price is not None and limit_price >= stock.price
        ) or (
            side == "SELL" and limit_price is not None and limit_price <= stock.price
        )
        if marketable:
            _fill_order(db, order, stock, account, stock.price)
        db.commit()
        db.refresh(order)
        order.stock = stock
        return order
    except IntegrityError:
        db.rollback()
        # Two copies of the same HTTP request can pass the initial lookup
        # together. The database unique constraint elects one winner; once it
        # commits, return that order instead of creating another one.
        existing = _idempotent_order(
            db,
            account_id=account.id,
            idempotency_key=operation_key,
        )
        if existing is not None:
            return _return_or_reject_idempotent_order(
                existing,
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
            )
        raise
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def place_market_order(
    db: Session,
    account: SimulationAccount,
    symbol: str,
    side: str,
    quantity: int,
) -> Order:
    """Backward-compatible wrapper used by older callers."""
    return place_order(db, account, symbol, side, quantity)


def cancel_order(
    db: Session,
    account: SimulationAccount,
    order_no: str,
) -> Order:
    order = db.scalar(
        select(Order)
        .where(Order.account_id == account.id, Order.order_no == order_no)
        .options(joinedload(Order.stock))
    )
    if order is None:
        raise HTTPException(status_code=404, detail="委托不存在")
    if order.status != "PENDING":
        raise HTTPException(status_code=400, detail="只有待成交委托可以撤单")
    order.status = "CANCELED"
    db.commit()
    db.refresh(order)
    return order


def expire_pending_orders(db: Session, now: datetime | None = None) -> int:
    """Expire unfilled day orders after 15:00 China time or on a later date."""
    observed = now or trading_now()
    local_now = observed.astimezone(CHINA_TZ)
    pending = db.scalars(select(Order).where(Order.status == "PENDING")).all()
    expired = 0
    for order in pending:
        submitted_at = order.created_at.replace(tzinfo=UTC).astimezone(CHINA_TZ)
        if submitted_at.date() < local_now.date() or (
            submitted_at.date() == local_now.date()
            and local_now.time() >= time(15, 0)
        ):
            order.status = "EXPIRED"
            order.reject_reason = "当日有效委托已过期"
            expired += 1
    if expired:
        db.commit()
    return expired


def match_pending_orders(db: Session) -> int:
    """Fill limit orders crossed by the latest quote; reject orders invalid at fill time."""
    observed_at = trading_now()
    expire_pending_orders(db, observed_at)
    if not is_continuous_trading(observed_at):
        return 0
    orders = db.scalars(
        select(Order)
        .where(Order.status == "PENDING", Order.order_type == "LIMIT")
        .options(joinedload(Order.stock))
        .order_by(Order.created_at.asc())
    ).all()
    matched = 0
    for order in orders:
        stock = order.stock
        if quote_blocking_reason(stock, observed_at):
            continue
        if order.submitted_quote_at and stock.quote_source_at <= order.submitted_quote_at:
            continue
        limit_price = order.limit_price or order.price
        crossed = (
            order.side == "BUY" and stock.price <= limit_price
        ) or (
            order.side == "SELL" and stock.price >= limit_price
        )
        _rate, upper, lower = price_limits(stock)
        locked = (
            order.side == "BUY" and stock.price >= upper
        ) or (
            order.side == "SELL" and stock.price <= lower
        )
        if not crossed or locked:
            continue
        account = db.get(SimulationAccount, order.account_id)
        assert account is not None
        try:
            _fill_order(db, order, stock, account, stock.price)
            db.commit()
            matched += 1
        except HTTPException as exc:
            db.rollback()
            refreshed = db.scalar(select(Order).where(Order.id == order.id))
            if refreshed is not None:
                refreshed.status = "REJECTED"
                refreshed.reject_reason = str(exc.detail)
                db.commit()
    return matched

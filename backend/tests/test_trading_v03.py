"""Regression tests for session-gated snapshot-price simulation."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Order, SimulationAccount, Stock, Trade, User
from app.services import trading
from app.services.market.calendar import trading_day_state
from app.services.market.eastmoney import _quote_source_at, _quote_trade_date
from app.services.market.session import market_session
from app.services.market.status import market_status_values


OPEN_AT = datetime(2026, 9, 28, 2, 0, tzinfo=UTC)


@pytest.fixture
def trading_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(trading, "trading_now", lambda: OPEN_AT)
    with Session(engine) as db:
        user = User(username="v03", password_hash="unused")
        db.add(user)
        db.flush()
        account = SimulationAccount(user_id=user.id)
        stock = Stock(
            symbol="600519",
            name="测试股票",
            exchange="SSE",
            industry="测试",
            prev_close=Decimal("10.00"),
            price=Decimal("10.01"),
            open_price=Decimal("10.00"),
            high_price=Decimal("10.02"),
            low_price=Decimal("9.99"),
            volume=10000,
            quote_trade_date=date(2026, 9, 28),
            quote_source_at=(OPEN_AT - timedelta(seconds=30)).replace(tzinfo=None),
            updated_at=(OPEN_AT - timedelta(seconds=20)).replace(tzinfo=None),
        )
        db.add_all((account, stock))
        db.commit()
        yield db, account, stock
    engine.dispose()


@pytest.mark.parametrize(
    ("observed", "session"),
    [
        (datetime(2026, 9, 25, 2, tzinfo=UTC), "holiday"),
        (datetime(2026, 9, 28, 1, 20, tzinfo=UTC), "call_auction"),
        (datetime(2026, 9, 28, 4, tzinfo=UTC), "lunch_break"),
        (datetime(2026, 9, 28, 6, 57, tzinfo=UTC), "closing_auction"),
        (datetime(2026, 9, 28, 7, tzinfo=UTC), "closed"),
        (datetime(2027, 1, 4, 2, tzinfo=UTC), "calendar_unknown"),
    ],
)
def test_closed_sessions_reject_new_orders(trading_db, observed, session):
    db, account, _stock = trading_db
    assert market_session(observed)[0] == session
    preview = trading.build_order_preview(
        db, account, "600519", "BUY", 100, now=observed
    )
    assert preview["allowed"] is False
    assert "连续竞价时段" in preview["blocking_reason"]


def test_exchange_calendar_distinguishes_holiday_and_reopening():
    assert trading_day_state(date(2026, 9, 25)) is False
    assert trading_day_state(date(2026, 9, 28)) is True
    assert trading_day_state(date(2026, 10, 1)) is False
    assert trading_day_state(date(2027, 1, 4)) is None


@pytest.mark.parametrize(
    ("source_offset", "fetch_offset", "trade_date", "reason"),
    [
        (-900, -20, date(2026, 9, 28), "新鲜阈值"),
        (-30, -900, date(2026, 9, 28), "新鲜阈值"),
        (-30, -20, date(2026, 9, 25), "当前交易日"),
        (90, -20, date(2026, 9, 28), "时间异常"),
    ],
)
def test_stale_wrong_day_and_future_quotes_cannot_trade(
    trading_db, source_offset, fetch_offset, trade_date, reason
):
    db, account, stock = trading_db
    stock.quote_source_at = (OPEN_AT + timedelta(seconds=source_offset)).replace(
        tzinfo=None
    )
    stock.updated_at = (OPEN_AT + timedelta(seconds=fetch_offset)).replace(tzinfo=None)
    stock.quote_trade_date = trade_date
    db.commit()
    preview = trading.build_order_preview(db, account, "600519", "BUY", 100)
    assert preview["allowed"] is False
    assert reason in preview["blocking_reason"]
    with pytest.raises(HTTPException, match=reason):
        trading.place_order(db, account, "600519", "BUY", 100)
    assert db.scalars(select(Order)).all() == []


def test_missing_source_time_and_auction_quote_cannot_trade(trading_db):
    db, account, stock = trading_db
    stock.quote_source_at = None
    db.commit()
    assert "缺少行情源时间" in trading.build_order_preview(
        db, account, "600519", "BUY", 100
    )["blocking_reason"]

    stock.quote_source_at = datetime(2026, 9, 28, 1, 25)
    db.commit()
    assert "连续竞价时段的有效报价" in trading.build_order_preview(
        db, account, "600519", "BUY", 100
    )["blocking_reason"]


def test_market_order_persists_exact_quote_used_for_fill(trading_db):
    db, account, stock = trading_db
    order = trading.place_order(db, account, "600519", "BUY", 100)
    trade = db.scalar(select(Trade).where(Trade.order_id == order.id))
    assert trade is not None
    assert order.status == "FILLED"
    assert order.submitted_quote_price == stock.price
    assert order.submitted_quote_at == stock.quote_source_at
    assert order.filled_quote_at == stock.quote_source_at
    assert trade.filled_quote_at == stock.quote_source_at
    assert trade.price == stock.price


def test_limit_order_needs_new_valid_quote_and_open_session(trading_db, monkeypatch):
    db, account, stock = trading_db
    order = trading.place_order(
        db, account, "600519", "BUY", 100,
        order_type="LIMIT", limit_price=Decimal("10.00"),
    )
    assert order.status == "PENDING"
    stock.price = Decimal("9.99")
    db.commit()
    assert trading.match_pending_orders(db) == 0  # Same source snapshot.

    stock.quote_source_at = (OPEN_AT + timedelta(seconds=30)).replace(tzinfo=None)
    stock.updated_at = stock.quote_source_at
    db.commit()
    monkeypatch.setattr(trading, "trading_now", lambda: OPEN_AT + timedelta(seconds=40))
    assert trading.match_pending_orders(db) == 1
    assert order.filled_quote_at == stock.quote_source_at

    monkeypatch.setattr(trading, "trading_now", lambda: OPEN_AT + timedelta(hours=6))
    assert trading.match_pending_orders(db) == 0


def test_provider_timestamp_is_distinct_from_fetch_time():
    source_at = datetime(2026, 9, 25, 7, tzinfo=UTC).timestamp()
    assert _quote_source_at(source_at) == datetime(2026, 9, 25, 7)
    assert _quote_trade_date(source_at) == date(2026, 9, 25)
    assert _quote_source_at(None) is None


def test_market_status_does_not_treat_future_source_time_as_fresh(trading_db):
    db, _account, stock = trading_db
    stock.quote_source_at = (OPEN_AT + timedelta(minutes=2)).replace(tzinfo=None)
    db.commit()
    status = market_status_values(db, now=OPEN_AT)
    assert status["latest_quote_at"] is None
    assert status["fresh_count"] == 0


@pytest.mark.parametrize(
    ("symbol", "exchange", "rate"),
    [
        ("600001", "SSE", "0.10"),
        ("000001", "SZSE", "0.10"),
        ("300001", "SZSE", "0.20"),
        ("688001", "SSE", "0.20"),
        ("920001", "BSE", "0.30"),
    ],
)
def test_current_price_limit_rate_is_board_based_even_for_st(
    symbol, exchange, rate
):
    stock = Stock(symbol=symbol, name="*ST 测试", exchange=exchange)
    assert trading.price_limit_rate(stock) == Decimal(rate)


def test_pending_day_order_expires_at_close_and_cannot_fill_next_day(
    trading_db, monkeypatch
):
    db, account, stock = trading_db
    order = trading.place_order(
        db, account, "600519", "BUY", 100,
        order_type="LIMIT", limit_price=Decimal("10.00"),
    )
    assert trading.expire_pending_orders(db, OPEN_AT.replace(hour=6)) == 0
    assert order.status == "PENDING"

    after_close = OPEN_AT.replace(hour=7)
    assert trading.expire_pending_orders(db, after_close) == 1
    assert order.status == "EXPIRED"
    assert trading.expire_pending_orders(db, after_close) == 0

    stock.price = Decimal("9.99")
    stock.quote_source_at = datetime(2026, 9, 29, 2)
    stock.quote_trade_date = date(2026, 9, 29)
    stock.updated_at = stock.quote_source_at
    db.commit()
    monkeypatch.setattr(
        trading, "trading_now", lambda: datetime(2026, 9, 29, 2, 1, tzinfo=UTC)
    )
    assert trading.match_pending_orders(db) == 0
    assert db.scalars(select(Trade)).all() == []

"""Focused regression tests for K-line dates, metrics, and cache repair."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app import database as database_service
from app.database import Base
from app.models import Stock, StockBar
from app.services import market
from app.services.market import history as history_service
from app.services.market.eastmoney import _quote_trade_date


def make_stock() -> Stock:
    return Stock(
        symbol="600519",
        name="贵州茅台",
        exchange="SSE",
        industry="白酒",
        prev_close=Decimal("10.00"),
        price=Decimal("9.50"),
        open_price=Decimal("9.00"),
        high_price=Decimal("9.60"),
        low_price=Decimal("8.90"),
        volume=1_000_000,
        updated_at=datetime(2026, 8, 11, 2),
    )


def test_quote_trade_date_uses_provider_timestamp_not_fetch_time() -> None:
    provider_timestamp = datetime(2026, 8, 7, 7, tzinfo=UTC).timestamp()

    assert _quote_trade_date(provider_timestamp) == date(2026, 8, 7)
    assert _quote_trade_date(None) is None
    assert _quote_trade_date("invalid") is None


def test_additive_migration_upgrades_legacy_market_tables(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE stocks (id INTEGER PRIMARY KEY, volume BIGINT)")
        )
        connection.execute(
            text("CREATE TABLE stock_bars (id INTEGER PRIMARY KEY, volume BIGINT)")
        )
    monkeypatch.setattr(database_service, "engine", engine)

    database_service.migrate_database()

    inspector = inspect(engine)
    stock_columns = {column["name"] for column in inspector.get_columns("stocks")}
    bar_columns = {
        column["name"] for column in inspector.get_columns("stock_bars")
    }
    assert "quote_trade_date" in stock_columns
    assert {"prev_close", "change_amount", "change_percent"} <= bar_columns


def test_history_parser_keeps_daily_change_separate_from_candle_direction(
    monkeypatch,
) -> None:
    requested_params: dict[str, str] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": {
                    "klines": [
                        "2026-08-10,90,95,96,89,1000,950000,7.00,-5.00,-5.00,1.20"
                    ]
                }
            }

    def fake_get(*_args, **kwargs) -> FakeResponse:
        requested_params.update(kwargs["params"])
        return FakeResponse()

    monkeypatch.setattr(history_service, "_history_next_request_at", 0.0)
    monkeypatch.setattr(history_service.requests, "get", fake_get)

    row = history_service.fetch_eastmoney_history("600519", 20)[0]

    assert "f59" in requested_params["fields2"]
    assert "f60" in requested_params["fields2"]
    assert row["close_price"] > row["open_price"]
    assert row["prev_close"] == Decimal("100.00")
    assert row["change_amount"] == Decimal("-5.00")
    assert row["change_percent"] == Decimal("-5.00")


def test_latest_snapshot_requires_source_trade_date_and_keeps_daily_metrics() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        stock = make_stock()
        db.add(stock)
        db.flush()

        history_service.upsert_latest_stock_bar(db, stock)
        assert db.scalar(select(StockBar)) is None

        stock.quote_trade_date = date(2026, 8, 10)
        history_service.upsert_latest_stock_bar(db, stock)
        db.flush()
        bar = db.scalar(select(StockBar))

        assert bar is not None
        assert bar.close_price > bar.open_price
        assert bar.prev_close == Decimal("10.00")
        assert bar.change_amount == Decimal("-0.50")
        assert bar.change_percent == Decimal("-5.0000")


def test_new_trade_date_does_not_reuse_previous_day_ohlc(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    fetched_at = datetime(2026, 8, 10, 2)
    stock = make_stock()
    stock.quote_trade_date = date(2026, 8, 7)
    stock.open_price = Decimal("20.00")
    stock.high_price = Decimal("21.00")
    stock.low_price = Decimal("19.00")
    stock.volume = 999_999

    monkeypatch.setattr(
        market,
        "fetch_eastmoney_quotes",
        lambda _symbols: {
            stock.symbol: {
                "name": stock.name,
                "price": Decimal("9.50"),
                "prev_close": Decimal("10.00"),
                "open_price": None,
                "high_price": None,
                "low_price": None,
                "volume": None,
                "quote_trade_date": date(2026, 8, 10),
                "updated_at": fetched_at,
            }
        },
    )

    with Session(engine) as db:
        db.add(stock)
        db.commit()
        assert market.tick_market(db) == 1
        db.refresh(stock)

        assert stock.open_price == Decimal("10.00")
        assert stock.high_price == Decimal("10.00")
        assert stock.low_price == Decimal("9.50")
        assert stock.volume == 0


def test_authoritative_history_removes_fake_non_trading_dates() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        stock = make_stock()
        stock.quote_trade_date = date(2026, 8, 10)
        db.add(stock)
        db.flush()
        for trade_date in (
            date(2026, 8, 7),
            date(2026, 8, 8),
            date(2026, 8, 9),
            date(2026, 8, 10),
        ):
            db.add(
                StockBar(
                    stock_id=stock.id,
                    trade_date=trade_date,
                    open_price=Decimal("10.00"),
                    high_price=Decimal("10.10"),
                    low_price=Decimal("9.90"),
                    close_price=Decimal("10.00"),
                    volume=100,
                    turnover=Decimal("1000"),
                )
            )
        db.flush()

        authoritative_rows = [
            {
                "trade_date": date(2026, 8, 7),
                "open_price": Decimal("10.00"),
                "high_price": Decimal("10.10"),
                "low_price": Decimal("9.90"),
                "close_price": Decimal("10.00"),
                "volume": 100,
                "turnover": Decimal("1000"),
            },
            {
                "trade_date": date(2026, 8, 10),
                "open_price": Decimal("9.00"),
                "high_price": Decimal("9.60"),
                "low_price": Decimal("8.90"),
                "close_price": Decimal("9.50"),
                "volume": 100,
                "turnover": Decimal("950"),
            },
        ]
        history_service.reconcile_authoritative_stock_bars(
            db, stock, authoritative_rows
        )
        db.flush()

        dates = set(
            db.scalars(
                select(StockBar.trade_date).where(StockBar.stock_id == stock.id)
            ).all()
        )
        assert dates == {date(2026, 8, 7), date(2026, 8, 10)}

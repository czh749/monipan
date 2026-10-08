"""Focused regression tests for K-line dates, metrics, and cache repair."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session

from app import database as database_service
from app.database import Base
from app.models import Stock, StockBar, StockRawClose, Trade
from app.services import market
from app.services.market import history as history_service
from app.services.market.eastmoney import _quote_trade_date
from app.services.market.raw_close import upsert_quote_raw_close, upsert_raw_history
from app.services.market import raw_close as raw_close_service


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
        connection.execute(
            text("CREATE TABLE orders (id INTEGER PRIMARY KEY, account_id INTEGER)")
        )
        connection.execute(text("CREATE TABLE trades (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(database_service, "engine", engine)

    database_service.migrate_database()
    database_service.migrate_database()  # Application startup can run repeatedly.

    inspector = inspect(engine)
    stock_columns = {column["name"] for column in inspector.get_columns("stocks")}
    bar_columns = {
        column["name"] for column in inspector.get_columns("stock_bars")
    }
    assert "quote_trade_date" in stock_columns
    assert "quote_source_at" in stock_columns
    assert {"prev_close", "change_amount", "change_percent"} <= bar_columns
    order_columns = {column["name"] for column in inspector.get_columns("orders")}
    trade_columns = {column["name"] for column in inspector.get_columns("trades")}
    assert {"submitted_quote_price", "submitted_quote_at", "filled_quote_at"} <= order_columns
    assert "filled_quote_at" in trade_columns


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

    assert requested_params["fqt"] == "1"
    assert "f59" in requested_params["fields2"]
    assert "f60" in requested_params["fields2"]
    assert row["close_price"] > row["open_price"]
    assert row["prev_close"] == Decimal("100.00")
    assert row["change_amount"] == Decimal("-5.00")
    assert row["change_percent"] == Decimal("-5.00")
    monkeypatch.setattr(history_service, "_history_next_request_at", 0.0)
    history_service.fetch_eastmoney_history("600519", 20, adjusted=False)
    assert requested_params["fqt"] == "0"


def test_raw_close_history_supersedes_provisional_quote() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        stock = make_stock()
        stock.quote_trade_date = date(2026, 8, 10)
        db.add(stock)
        db.flush()
        upsert_quote_raw_close(db, stock)
        db.flush()
        row = db.scalar(select(StockRawClose))
        assert row is not None
        assert row.source == "SNAPSHOT"
        assert row.close_price == Decimal("9.50")
        upsert_raw_history(db, stock, [{"trade_date": date(2026, 8, 10), "close_price": Decimal("10.00")}])
        stock.price = Decimal("9.00")
        upsert_quote_raw_close(db, stock)
        db.flush()
        db.expire(row)
        assert db.scalar(select(StockRawClose)).source == "HISTORY"
        assert db.scalar(select(StockRawClose)).close_price == Decimal("10.00")


def test_raw_close_backfill_only_fetches_traded_symbols(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[tuple[str, int, bool]] = []

    def fake_history(symbol: str, limit: int, *, adjusted: bool) -> list[dict]:
        calls.append((symbol, limit, adjusted))
        return [{"trade_date": date(2026, 8, 10), "close_price": Decimal("10.00")}]

    monkeypatch.setattr(raw_close_service, "fetch_eastmoney_history", fake_history)
    with Session(engine) as db:
        stock = make_stock()
        db.add(stock)
        db.flush()
        db.add(Trade(
            trade_no="raw-history-test", order_id=1, account_id=1,
            stock_id=stock.id, side="BUY", quantity=100,
            price=Decimal("9.50"), amount=Decimal("950"), fee=Decimal("5"),
            created_at=datetime(2026, 8, 10, 2),
        ))
        db.commit()
        cursor, symbol, success = raw_close_service.backfill_raw_closes_once(db)
        assert (cursor, symbol, success) == (stock.id, stock.symbol, True)
        assert calls == [("600519", raw_close_service.RAW_HISTORY_LIMIT, False)]
        assert db.scalar(select(StockRawClose)).source == "HISTORY"
        assert raw_close_service.backfill_raw_closes_once(db) == (0, None, True)


def test_raw_close_mysql_upsert_compiles_without_live_server() -> None:
    statement = raw_close_service._upsert_statement(
        "mysql",
        [{
            "stock_id": 1, "trade_date": date(2026, 8, 10),
            "close_price": Decimal("10.00"), "source": "SNAPSHOT",
            "updated_at": datetime(2026, 8, 10, 8),
        }],
        source="SNAPSHOT",
    )
    sql = str(statement.compile(dialect=mysql.dialect()))
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "CASE WHEN" in sql


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

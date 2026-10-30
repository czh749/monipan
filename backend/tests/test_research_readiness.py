"""Failures are visible, cache polling stays offline, recovery retains guards."""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.models import Stock, SimulationAccount
from app.services.market import preparation
from app.services.market.history import stock_history
from app.services.market.types import MarketDataError
from app.services.research_access import require_research_enabled
from app.services.research_watchlist import add_research_watchlist
from .test_security_catalog import factory


@pytest.fixture(autouse=True)
def clear_observations():
    preparation._observations.clear()
    yield
    preparation._observations.clear()


def quote(at):
    return SimpleNamespace(symbol="688900", price=Decimal(10), prev_close=Decimal(10),
                           quote_source_at=at, updated_at=at)


def test_single_old_stock_is_stale_even_if_other_stocks_are_fresh(monkeypatch):
    monkeypatch.setenv("MONIPAN_MARKET_INTERVAL", "300")
    now = datetime(2026, 10, 30, 2, 0)
    assert preparation.quote_preparation(quote(now), now)["quote_state"] == "ready"
    assert preparation.quote_preparation(quote(now - timedelta(minutes=30)), now)["quote_state"] == "stale"
    assert preparation.quote_preparation(quote(now - timedelta(minutes=15)), now)["quote_state"] == "delayed"


def test_friday_snapshot_is_closed_on_weekend_but_older_day_is_stale():
    sunday = datetime(2026, 11, 1, 4)
    assert preparation.quote_preparation(quote(datetime(2026, 10, 30, 7)), sunday)["quote_state"] == "closed"
    assert preparation.quote_preparation(quote(datetime(2026, 10, 29, 7)), sunday)["quote_state"] == "stale"


def test_pending_failure_and_recovery_do_not_invent_quotes():
    stock = quote(datetime(1970, 1, 1))
    stock.price = stock.prev_close = Decimal(0)
    assert preparation.quote_preparation(stock)["quote_state"] == "pending"
    preparation.record_fetch("quote", stock.symbol, failed=True, retry_seconds=300)
    values = preparation.quote_preparation(stock)
    assert values["quote_state"] == "failed"
    assert 0 < values["quote_retry_after_seconds"] <= 300
    preparation.record_fetch("quote", stock.symbol, failed=False)
    assert preparation.quote_preparation(stock)["quote_state"] == "pending"


def test_cache_poll_does_not_fetch_and_failure_keeps_existing_bar(factory, monkeypatch):
    from app.services.market import history
    with factory() as db:
        account = db.scalar(select(SimulationAccount))
        add_research_watchlist(db, account.id, "688900")
        stock = db.scalar(select(Stock).where(Stock.symbol == "688900"))
        stock.price = stock.prev_close = stock.open_price = stock.high_price = stock.low_price = Decimal(10)
        stock.quote_trade_date = datetime(2026, 10, 30).date()
        stock.updated_at = stock.quote_source_at = datetime(2026, 10, 30, 2)
        db.commit()
        calls = []
        def fail(*args, **kwargs):
            calls.append(args)
            raise MarketDataError("test provider failure")
        monkeypatch.setattr(history, "fetch_eastmoney_history", fail)
        _, bars = stock_history(db, stock, 90)
        assert len(bars) == 1 and len(calls) == 1
        assert preparation.history_preparation(stock.symbol, False)["preparation_state"] == "failed"
        _, polled = stock_history(db, stock, 90, refresh=False)
        assert len(polled) == 1 and len(calls) == 1


def test_recovery_switch_blocks_new_research_and_preserves_existing_membership(factory, monkeypatch):
    with factory() as db:
        account = db.scalar(select(SimulationAccount))
        add_research_watchlist(db, account.id, "688900")
        monkeypatch.setenv("MONIPAN_RESEARCH_ENABLED", "0")
        for operation in (require_research_enabled,
                          lambda: add_research_watchlist(db, account.id, "688901")):
            with pytest.raises(HTTPException) as error:
                operation()
            assert error.value.status_code == 503
        assert db.scalar(select(Stock).where(Stock.symbol == "688900")).research_only
        monkeypatch.setenv("MONIPAN_RESEARCH_ENABLED", "1")
        assert add_research_watchlist(db, account.id, "688900")["symbol"] == "688900"

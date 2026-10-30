"""Offline directory integrity, account scope and research trading boundaries."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import io
from threading import Barrier
import zipfile

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import SecurityCatalogEntry, Stock, SimulationAccount, User, WatchlistItem, Position, Order, Trade
from app.services.security_catalog import (CatalogError, CatalogRecord, SSE_SOURCE, SZSE_SOURCE,
    parse_sse, parse_szse, replace_directory, search_directory)
from app.services.research_watchlist import add_research_watchlist
from app.services.stock_activity import active_stock_condition
from app.services import trading, market
from app.services.market.tick import tick_pending_research, _PENDING_ATTEMPTS
from app.services.market.status import market_status_values


def records(count=32):
    return [CatalogRecord(f"688{900+i:03}", "SSE", f"研究{i}", None, "科创板", "制造业", None, SSE_SOURCE)
            for i in range(count)] + [CatalogRecord("302132", "SZSE", "中航成飞", None, "创业板", "制造业", None, SZSE_SOURCE)]


@pytest.fixture
def factory(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "research.db").as_posix(), connect_args={"timeout": 15})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        for name in ("first", "second"):
            user = User(username=name, status="ACTIVE")
            db.add(user); db.flush()
            db.add(SimulationAccount(user_id=user.id))
        db.commit()
        replace_directory(db, records())
    yield factory
    engine.dispose()


def workbook(rows):
    from xml.etree.ElementTree import Element, SubElement, tostring
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = Element("worksheet", xmlns=ns)
    data = SubElement(sheet, "sheetData")
    for index, cells in enumerate(rows, 1):
        row = SubElement(data, "row", r=str(index))
        for col, value in zip("ABCDE", cells):
            cell = SubElement(row, "c", r=f"{col}{index}", t="inlineStr")
            SubElement(SubElement(cell, "is"), "t").text = value
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", tostring(sheet, encoding="utf-8"))
    return buf.getvalue()


def test_exchange_parsers_preserve_zeros_and_exclude_cdr():
    row = {"STOCK_TYPE": "8", "A_STOCK_CODE": "688901", "SEC_NAME_CN": "华兴源创", "LIST_DATE": "20190722"}
    result = parse_sse({"result": [row, dict(row, A_STOCK_CODE="689009")], "pageHelp": {"total": 2}}, "8")
    assert [r.symbol for r in result] == ["688901"]
    assert result[0].listing_date.isoformat() == "2019-07-22"
    headers = ["A股代码", "A股简称", "板块", "A股上市日期"]
    raw = workbook([headers, ["1", "平安银行", "主板", "1991-04-03"], ["302132", "中航成飞", "创业板", "20100827"]])
    assert [r.symbol for r in parse_szse(raw, expected_count=2)] == ["000001", "302132"]
    with pytest.raises(CatalogError, match="Incomplete"):
        parse_szse(raw, expected_count=3)
    with pytest.raises(CatalogError):
        parse_sse({"result": [row], "pageHelp": {"total": 2}}, "8")
    with pytest.raises(CatalogError):
        parse_sse({"result": [dict(row, SEC_NAME_CN="坏\ufffd名")], "pageHelp": {"total": 1}}, "8")
    with pytest.raises(CatalogError):
        parse_szse(workbook([headers, ["159001", "ETF", "主板", "20200101"]]))


def test_snapshot_failures_keep_previous_directory_and_user_rows(factory):
    with factory() as db:
        add_research_watchlist(db, 1, "688900")
        old = search_directory(db, "688900", 1)
        for bad in (records() + [records()[0]], records(1), [replace(r, exchange="BSE") for r in records()]):
            with pytest.raises(CatalogError):
                replace_directory(db, bad)
            assert search_directory(db, "688900", 1) == old
        assert db.scalar(select(func.count()).select_from(WatchlistItem)) == 1


def test_quota_idempotence_default_exemption_and_shared_identity(factory):
    with factory() as db:
        db.add(Stock(symbol="600519", name="贵州茅台", exchange="SSE", industry="白酒",
                     price=10, prev_close=10, open_price=10, high_price=10, low_price=10))
        db.commit()
        for i in range(30):
            add_research_watchlist(db, 1, f"688{900+i:03}")
        first = add_research_watchlist(db, 1, "688900")
        assert add_research_watchlist(db, 1, "688900") == first
        add_research_watchlist(db, 1, "600519")
        with pytest.raises(HTTPException) as error:
            add_research_watchlist(db, 1, "688930")
        assert error.value.status_code == 409
        db.rollback()
        add_research_watchlist(db, 2, "688900")
        assert db.scalar(select(func.count()).select_from(Stock).where(Stock.symbol == "688900")) == 1
        assert search_directory(db, "688900", 1)["quota"] == {"used": 30, "limit": 30}
        assert search_directory(db, "688900", 2)["quota"]["used"] == 1


def test_transient_deadlock_retries_whole_membership_transaction(factory, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.services import research_watchlist as service
    original = service._add_research_watchlist_once
    attempts = []
    def add(db, account_id, symbol):
        attempts.append(symbol)
        if len(attempts) == 1:
            raise OperationalError("fixture", None, Exception(1213, "fixture deadlock"))
        return original(db, account_id, symbol)
    monkeypatch.setattr(service, "_add_research_watchlist_once", add)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    with factory() as db:
        service.add_research_watchlist(db, 1, "688900")
        assert len(attempts) == 2
        assert search_directory(db, "", 1)["quota"]["used"] == 1


def test_repeated_deadlock_has_a_bounded_retry_and_safe_error(factory, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.services import research_watchlist as service
    attempts = []
    def add(*_args):
        attempts.append(1)
        raise OperationalError("fixture", None, Exception(1213, "private fixture detail"))
    monkeypatch.setattr(service, "_add_research_watchlist_once", add)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    with factory() as db:
        with pytest.raises(HTTPException) as error:
            service.add_research_watchlist(db, 1, "688900")
        assert len(attempts) == 3 and error.value.status_code == 503
        assert "private" not in str(error.value.detail)
        assert search_directory(db, "", 1)["quota"]["used"] == 0


def test_racing_thirtieth_and_thirty_first_add_never_exceeds_quota_sqlite(factory):
    with factory() as db:
        for i in range(29):
            add_research_watchlist(db, 1, f"688{900+i:03}")
    barrier = Barrier(2)
    def add(symbol):
        with factory() as db:
            barrier.wait(timeout=5)
            try:
                add_research_watchlist(db, 1, symbol)
                return 201
            except HTTPException as exc:
                return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(add, ["688929", "688930"]))
    assert sorted(results) == [201, 409]
    with factory() as db:
        assert search_directory(db, "", 1)["quota"]["used"] == 30


def test_two_accounts_racing_to_add_share_one_stock_sqlite(factory):
    barrier = Barrier(2)
    def add(account_id):
        with factory() as db:
            barrier.wait(timeout=5)
            return add_research_watchlist(db, account_id, "688900")
    with ThreadPoolExecutor(2) as pool:
        assert len(list(pool.map(add, (1, 2)))) == 2
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Stock)) == 1
        assert db.scalar(select(func.count()).select_from(WatchlistItem)) == 2


def test_private_lists_and_active_retention_do_not_delete_shared_stock(factory):
    with factory() as db:
        add_research_watchlist(db, 1, "688900")
        stock = db.scalar(select(Stock).where(Stock.symbol == "688900"))
        assert db.scalars(select(Stock).where(active_stock_condition(1))).all() == [stock]
        assert db.scalars(select(Stock).where(active_stock_condition(2))).all() == []
        db.execute(delete(WatchlistItem)); db.commit()
        assert db.scalars(select(Stock).where(active_stock_condition())).all() == []
        db.add(Position(account_id=1, stock_id=stock.id, quantity=100)); db.commit()
        assert db.scalars(select(Stock).where(active_stock_condition())).all() == [stock]
        assert market_status_values(db)["total_count"] == 1
        assert db.get(Stock, stock.id) is not None


def test_stale_directory_search_survives_but_new_add_is_refused(factory):
    with factory() as db:
        for row in db.scalars(select(SecurityCatalogEntry)):
            row.collected_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8)
        db.commit()
        assert search_directory(db, "302132", 1)["catalog"]["state"] == "stale"
        assert search_directory(db, "%", 1)["items"] == []
        with pytest.raises(HTTPException) as error:
            add_research_watchlist(db, 1, "302132")
        assert error.value.status_code == 503


def test_disabled_watchers_and_cancelled_orders_stop_collection(factory, monkeypatch):
    from app.services.market.tick import tick_market
    with factory() as db:
        add_research_watchlist(db, 2, "688900")
        stock = db.scalar(select(Stock))
        db.get(User, 2).status = "DISABLED"
        db.commit()
        assert db.scalars(select(Stock).where(active_stock_condition())).all() == []
        order = Order(order_no="retain-pending", stock_id=stock.id, account_id=2, side="BUY", price=10,
                      quantity=100, order_type="LIMIT", limit_price=10)
        db.add(order); db.commit()
        assert db.scalars(select(Stock).where(active_stock_condition())).all() == [stock]
        order.status = "CANCELLED"; db.commit()
        assert market_status_values(db)["total_count"] == 0
        seen = []
        monkeypatch.setattr(market, "fetch_eastmoney_quotes", lambda symbols: seen.extend(symbols) or {})
        assert tick_market(db) == 0
        assert seen == []


def test_pending_quote_batch_budget_fair_retry_and_no_foreign_inactive_rows(factory, monkeypatch):
    calls = []
    _PENDING_ATTEMPTS.clear()
    monkeypatch.setattr(market, "fetch_eastmoney_quotes", lambda symbols: calls.append(symbols) or {})
    with factory() as db:
        for i in range(25):
            add_research_watchlist(db, 1, f"688{900+i:03}")
        assert tick_pending_research(db, monotonic_now=1000) == 0
        assert tick_pending_research(db, monotonic_now=1020) == 0
        tick_pending_research(db, monotonic_now=1040)
        assert len(calls) == 2 and len(calls[0]) == 20 and len(calls[1]) == 5
        assert not set(calls[0]) & set(calls[1])
        db.execute(delete(WatchlistItem)); db.commit()
        tick_pending_research(db, monotonic_now=1400)
        assert len(calls) == 2
    _PENDING_ATTEMPTS.clear()


def test_direct_preview_submit_and_matcher_cannot_trade_research_stock(factory, monkeypatch):
    now = datetime(2026, 10, 26, 2, 0, tzinfo=UTC)
    monkeypatch.setattr(trading, "trading_now", lambda: now)
    with factory() as db:
        add_research_watchlist(db, 1, "688900")
        account = db.get(SimulationAccount, 1)
        stock = db.scalar(select(Stock))
        from app.agent.tools import build_readonly_tools
        quote_tool = next(t for t in build_readonly_tools(session_factory=factory) if t.name == "get_stock_quote")
        result = quote_tool.invoke({"symbol": stock.symbol})
        assert result["status"] == "NO_DATA"
        assert "price" not in result["data"]
        for function in (trading.build_order_preview, trading.place_order):
            with pytest.raises(HTTPException) as error:
                function(db, account, stock.symbol, "BUY", 100)
            assert error.value.status_code == 403
        stock.price = stock.prev_close = stock.open_price = stock.high_price = stock.low_price = 10
        stock.updated_at = stock.quote_source_at = now.replace(tzinfo=None)
        order = Order(order_no="boundary", account_id=1, stock_id=stock.id, side="BUY", order_type="LIMIT",
                      price=10, limit_price=10, quantity=100, created_at=now.replace(tzinfo=None))
        db.add(order); db.commit()
        assert trading.match_pending_orders(db) == 0
        assert order.status == "PENDING"
        assert db.scalar(select(func.count()).select_from(Trade)) == 0
        assert account.available_cash == 1000000


def test_http_search_add_pending_quote_and_account_isolation(factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import router
    from app.auth import get_current_account, get_current_user
    from app.database import get_db
    app = FastAPI()
    app.include_router(router)
    def database():
        with factory() as db:
            yield db
    def account(account_id):
        with factory() as db:
            return db.get(SimulationAccount, account_id)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="first", status="ACTIVE")
    app.dependency_overrides[get_current_account] = lambda: account(1)
    with TestClient(app) as client:
        search = client.get("/api/securities/search?keyword=688900")
        assert search.status_code == 200
        assert search.json()["items"][0]["source_url"] == SSE_SOURCE
        assert client.post("/api/watchlist/688900").status_code == 201
        stocks = client.get("/api/stocks").json()
        assert len(stocks) == 1 and stocks[0]["quote_status"] == "pending"
        assert stocks[0]["research_only"] is True
        for path in ("/api/orders/preview", "/api/orders"):
            result = client.post(path, headers={"Idempotency-Key": "research-boundary"}, json={"symbol": "688900", "side": "BUY", "quantity": 100})
            assert result.status_code == 403
        app.dependency_overrides[get_current_account] = lambda: account(2)
        assert client.get("/api/stocks").json() == []
        assert client.get("/api/securities/search?keyword=688900").json()["items"][0]["added"] is False
        assert client.delete("/api/watchlist/688900").status_code == 204
        app.dependency_overrides[get_current_account] = lambda: account(1)
        assert len(client.get("/api/watchlist").json()) == 1
        assert client.delete("/api/watchlist/688900").status_code == 204
        assert client.get("/api/stocks").json() == []

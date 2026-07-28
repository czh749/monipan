"""
API 集成测试。

本测试文件使用 FastAPI 的 TestClient 进行端到端集成测试，
验证 API 端点的正确性和业务逻辑的完整性。

测试覆盖：
    1. 健康检查和股票池 — 验证基础服务可用 + 200 只股票存在
    2. 完整买卖流程   — 买入 → 验证持仓/订单/成交 → 卖出
    3. 无效手数拒绝   — 验证非 100 整数倍被拒绝

测试数据管理：
    每个测试前后自动清理交易数据（AccountTransaction、Trade、Order、Position），
    并重置账户可用资金为 100 万，保证测试之间相互独立。

运行方式：
    cd backend
    python -m pytest           # 运行所有测试
    python -m pytest -v        # 详细输出
    python -m pytest -k "buy"  # 只运行包含 "buy" 的测试
"""

import os
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

# 集成测试必须离线、可重复，禁止在应用生命周期中请求真实行情。
os.environ["MONIPAN_DISABLE_MARKET_LOOP"] = "1"
TEST_DATABASE_PATH = (
    Path(tempfile.gettempdir()) / f"monipan-test-{uuid4().hex}.db"
)
os.environ["MONIPAN_DATABASE_URL"] = (
    f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"
)

from app.database import Base, SessionLocal, engine
from app import main as app_main
from app.main import app
from app.models import (
    AccountTransaction,
    MarketIndex,
    Order,
    Position,
    SimulationAccount,
    Stock,
    Trade,
)
from app.seed import seed_database
from app.services import market
from app.stock_pool import STOCK_POOL


# ---------------------------------------------------------------------------
# 测试辅助函数
# ---------------------------------------------------------------------------

def reset_demo_account() -> None:
    """
    重置 demo 账户的交易数据。

    清理以下表的数据：
        - account_transactions: 资金流水
        - trades: 成交记录
        - orders: 委托订单
        - positions: 持仓记录

    然后重置账户可用资金为 100 万。

    在每次测试前后调用，确保测试隔离性。
    """
    with SessionLocal() as db:
        # 按依赖顺序删除：先删子表，再删父表
        db.execute(delete(AccountTransaction))
        db.execute(delete(Trade))
        db.execute(delete(Order))
        db.execute(delete(Position))

        # 重置可用资金为初始值
        db.execute(
            update(SimulationAccount).values(available_cash=1_000_000)
        )

        db.commit()


# ---------------------------------------------------------------------------
# pytest Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    """为整轮测试创建临时数据库，并在结束后清理数据库及 WAL 文件。"""
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_database(db)
        # 仅在临时测试库写入固定测试报价；生产代码不包含随机行情。
        now = datetime.now(UTC).replace(tzinfo=None)
        for index, stock in enumerate(
            db.scalars(select(Stock).order_by(Stock.id)).all()
        ):
            base = Decimal("10") + Decimal(index) / Decimal("100")
            stock.prev_close = base
            stock.price = base + Decimal("0.01")
            stock.open_price = base
            stock.high_price = base + Decimal("0.02")
            stock.low_price = base - Decimal("0.02")
            stock.volume = 1_000_000 + index * 100
            stock.updated_at = now
        for index, item in enumerate(
            db.scalars(select(MarketIndex).order_by(MarketIndex.display_order)).all()
        ):
            item.price = Decimal("3000") + Decimal(index * 100)
            item.change = Decimal("10") - Decimal(index)
            item.change_percent = Decimal("0.50") - Decimal(index) / Decimal("10")
            item.turnover = Decimal("100000000000") + Decimal(index * 1_000_000)
            item.updated_at = now
        db.commit()
    yield
    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{TEST_DATABASE_PATH}{suffix}").unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def clean_trading_data():
    """
    自动执行的测试夹具：每个测试前后清理数据。

    autouse=True: 每个测试函数自动使用此夹具，无需手动声明。

    执行顺序：
        1. reset_demo_account() — 测试前清理
        2. yield — 执行测试
        3. reset_demo_account() — 测试后清理
    """
    reset_demo_account()
    yield
    reset_demo_account()


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def test_health_and_stock_pool() -> None:
    """
    测试健康检查和股票池完整性。

    验证：
        - /health 端点返回 {"status": "ok"}
        - /api/stocks 端点返回 200 状态码
        - 股票池包含恰好 200 只股票
    """
    # TestClient 作为上下文管理器，自动管理连接生命周期
    with TestClient(app) as client:
        # 健康检查
        assert client.get("/health").json() == {"status": "ok"}

        # 股票列表
        response = client.get("/api/stocks")
        assert response.status_code == 200
        # 验证恰好 200 只股票
        assert len(response.json()) == 200


def test_market_status_exposes_source_and_freshness_policy() -> None:
    """行情状态接口需要明确真实来源、刷新阈值、覆盖率和模拟交易边界。"""
    with TestClient(app) as client:
        response = client.get("/api/market/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "eastmoney"
    assert payload["provider_label"] == "东方财富"
    assert payload["source_type"] == "REAL_PUBLIC_QUOTE"
    assert payload["trading_mode"] == "SIMULATED"
    assert payload["refresh_interval_seconds"] == 60
    assert payload["fresh_threshold_seconds"] == 150
    assert payload["stale_threshold_seconds"] == 300
    assert payload["available_count"] == 200
    assert payload["total_count"] == 200
    assert payload["coverage_percent"] == "100.0"
    assert payload["fresh_coverage_percent"] == "100.0"
    assert payload["healthy_coverage_threshold_percent"] == "95.0"
    assert payload["latest_quote_at"] is not None
    assert payload["status"] in {
        "fresh",
        "partial",
        "delayed",
        "stale",
        "closed",
        "source_error",
    }


def test_market_status_is_partial_when_latest_quote_masks_stale_symbols() -> None:
    """最新一只股票刚更新时，不能掩盖股票池中大量陈旧行情。"""
    observed_at = datetime(2026, 7, 27, 2, 0)
    fresh_at = observed_at - timedelta(seconds=30)
    stale_at = observed_at - timedelta(seconds=180)

    state = market.MARKET_REFRESH_STATE
    with state._lock:
        original_state = {
            "last_attempt_at": state.last_attempt_at,
            "last_success_at": state.last_success_at,
            "last_error": state.last_error,
            "updated_count": state.updated_count,
            "refreshing": state.refreshing,
        }
        state.last_attempt_at = observed_at - timedelta(seconds=10)
        state.last_success_at = observed_at - timedelta(seconds=5)
        state.last_error = None
        state.updated_count = 200
        state.refreshing = False

    try:
        with SessionLocal() as db:
            stocks = db.scalars(select(Stock).order_by(Stock.id)).all()
            original_updated_at = stocks[0].updated_at
            try:
                for index, stock in enumerate(stocks):
                    stock.updated_at = fresh_at if index < 144 else stale_at
                db.commit()

                payload = market.market_status_values(db, now=observed_at)

                assert payload["latest_quote_at"] == fresh_at
                assert payload["fresh_count"] == 144
                assert payload["fresh_coverage_percent"] == Decimal("72.0")
                assert payload["status"] == "partial"
                assert payload["status_label"] == "行情不完整"
            finally:
                db.execute(update(Stock).values(updated_at=original_updated_at))
                db.commit()
    finally:
        with state._lock:
            for field, value in original_state.items():
                setattr(state, field, value)


def test_market_status_is_partial_when_latest_refresh_is_incomplete() -> None:
    """即使旧快照仍在新鲜窗口内，本轮大量批次失败也不能显示绿色。"""
    observed_at = datetime(2026, 7, 27, 2, 0)
    fresh_at = observed_at - timedelta(seconds=30)
    state = market.MARKET_REFRESH_STATE

    with state._lock:
        original_state = {
            "last_attempt_at": state.last_attempt_at,
            "last_success_at": state.last_success_at,
            "last_error": state.last_error,
            "updated_count": state.updated_count,
            "refreshing": state.refreshing,
        }
        state.last_attempt_at = observed_at - timedelta(seconds=10)
        state.last_success_at = observed_at - timedelta(seconds=5)
        state.last_error = None
        state.updated_count = 144
        state.refreshing = False

    try:
        with SessionLocal() as db:
            stocks = db.scalars(select(Stock)).all()
            original_updated_at = stocks[0].updated_at
            try:
                for stock in stocks:
                    stock.updated_at = fresh_at
                db.commit()

                payload = market.market_status_values(db, now=observed_at)

                assert payload["fresh_count"] == 200
                assert payload["round_coverage_percent"] == Decimal("72.0")
                assert payload["status"] == "partial"
                assert "最近一轮仅更新 144/200 只" in payload["status_message"]
            finally:
                db.execute(update(Stock).values(updated_at=original_updated_at))
                db.commit()
    finally:
        with state._lock:
            for field, value in original_state.items():
                setattr(state, field, value)


def test_market_indices_returns_five_benchmarks() -> None:
    """大盘接口按固定顺序返回五个主要 A 股指数。"""
    with TestClient(app) as client:
        response = client.get("/api/market/indices")

    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload] == [
        "上证指数",
        "深证成指",
        "创业板指",
        "沪深300",
        "科创50",
    ]
    assert all(Decimal(item["price"]) > 0 for item in payload)
    assert all(Decimal(item["turnover"]) > 0 for item in payload)


def test_refresh_prioritizes_stock_pool_before_indices(monkeypatch) -> None:
    """每轮首个数据源请求必须用于股票池，并在指数前执行冷却。"""
    events: list[str] = []

    def tick_stocks(_db) -> int:
        events.append("stocks")
        return 199

    def tick_indices(_db) -> int:
        events.append("indices")
        return 5

    def sleep(_seconds: float) -> None:
        events.append("cooldown")

    monkeypatch.setattr(app_main, "tick_market", tick_stocks)
    monkeypatch.setattr(app_main, "tick_market_indices", tick_indices)
    monkeypatch.setattr(app_main.time, "sleep", sleep)

    assert app_main.refresh_market_once() == (199, 5)
    assert events == ["stocks", "cooldown", "indices"]


def test_market_session_is_aware_of_breaks_and_weekends() -> None:
    """不能把午休、收盘后和周末的最近快照误判为交易时段行情。"""
    monday = datetime(2026, 7, 27, tzinfo=UTC)
    assert market.market_session(monday.replace(hour=1, minute=20))[0] == "call_auction"
    assert market.market_session(monday.replace(hour=1, minute=27))[0] == "opening_break"
    assert market.market_session(monday.replace(hour=2))[2] is True
    assert market.market_session(monday.replace(hour=4))[0] == "lunch_break"
    assert market.market_session(monday.replace(hour=8))[0] == "closed"

    saturday = datetime(2026, 8, 1, 2, tzinfo=UTC)
    assert market.market_session(saturday)[0] == "weekend"


def test_eastmoney_request_uses_browser_headers(monkeypatch) -> None:
    """批量行情请求需要携带公开行情页面同源标识，避免被服务端主动断开。"""
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": {
                    "diff": [
                        {
                            "f2": 1500.25,
                            "f5": 123456,
                            "f12": "600519",
                            "f14": "贵州茅台",
                            "f15": 1510.00,
                            "f16": 1488.00,
                            "f17": 1499.00,
                            "f18": 1498.00,
                        }
                    ]
                }
            }

    class FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.requested_batches: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def get(self, *_args, **kwargs) -> FakeResponse:
            self.requested_batches.append(kwargs["params"]["secids"])
            return FakeResponse()

    session = FakeSession()
    monkeypatch.setattr(market.requests, "Session", lambda: session)
    monkeypatch.setattr(market.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(market.random, "uniform", lambda _start, _end: 0)

    quotes = market.fetch_eastmoney_quotes(
        ["600519", "000001", "000002", "000063", "000100", "000157"]
    )

    assert session.headers["Referer"] == "https://quote.eastmoney.com/"
    assert "Mozilla/5.0" in session.headers["User-Agent"]
    assert session.headers["Connection"] == "close"
    assert len(session.requested_batches) == 1
    assert len(session.requested_batches[0].split(",")) == 6
    assert quotes["600519"]["open_price"] == Decimal("1499.00")
    assert quotes["600519"]["high_price"] == Decimal("1510.00")
    assert quotes["600519"]["low_price"] == Decimal("1488.00")
    assert quotes.stats.successful_batches == 1
    assert quotes.stats.success_percent == Decimal("100.0")
    assert quotes.stats.fallback_used is False


def test_failed_quote_batch_is_retried_after_cooldown(monkeypatch) -> None:
    """第一遍失败批次必须延迟到统一冷却后重试，并记录恢复统计。"""
    class FakeResponse:
        def __init__(self, symbols: list[str]) -> None:
            self.symbols = symbols

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": {
                    "diff": [
                        {
                            "f2": 10.01,
                            "f5": 100,
                            "f12": symbol,
                            "f14": symbol,
                            "f15": 10.02,
                            "f16": 9.99,
                            "f17": 10.00,
                            "f18": 10.00,
                        }
                        for symbol in self.symbols
                    ]
                }
            }

    class FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.attempts: dict[str, int] = {}
            self.urls: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def get(self, *_args, **kwargs) -> FakeResponse:
            self.urls.append(_args[0])
            secids = kwargs["params"]["secids"]
            self.attempts[secids] = self.attempts.get(secids, 0) + 1
            if len(self.attempts) == 1 and self.attempts[secids] == 1:
                raise market.requests.ConnectionError("测试断开")
            symbols = [item.split(".", 1)[1] for item in secids.split(",")]
            return FakeResponse(symbols)

    session = FakeSession()
    sleep_calls: list[float] = []
    monkeypatch.setattr(market.requests, "Session", lambda: session)
    monkeypatch.setattr(market.time, "sleep", sleep_calls.append)
    monkeypatch.setattr(market.random, "uniform", lambda _start, _end: 0)

    symbols = [
        "600519",
        "000001",
        "000002",
        "000063",
        "000100",
        "000157",
    ]
    quotes = market.fetch_eastmoney_quotes(symbols)

    assert set(quotes) == set(symbols)
    assert market.EASTMONEY_RETRY_COOLDOWN_SECONDS in sleep_calls
    assert quotes.stats.total_batches == 1
    assert quotes.stats.retried_batches == 1
    assert quotes.stats.recovered_batches == 1
    assert quotes.stats.fallback_used is True
    assert quotes.stats.success_percent == Decimal("100.0")
    assert session.urls == list(market.EASTMONEY_QUOTE_URLS)


def test_consecutive_quote_failures_trigger_pause(monkeypatch) -> None:
    """连续三个批次失败时必须暂停，而不是继续形成请求突发。"""
    class FailingSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def get(self, *_args, **_kwargs):
            raise market.requests.ConnectionError("测试连续断开")

    sleep_calls: list[float] = []
    monkeypatch.setattr(market, "EASTMONEY_BATCH_SIZE", 5)
    monkeypatch.setattr(market.requests, "Session", FailingSession)
    monkeypatch.setattr(market.time, "sleep", sleep_calls.append)
    monkeypatch.setattr(market.random, "uniform", lambda _start, _end: 0)

    symbols = [f"600{index:03d}" for index in range(20)]
    with pytest.raises(market.MarketDataError) as exc_info:
        market.fetch_eastmoney_quotes(symbols)

    stats = exc_info.value.batch_stats
    assert stats is not None
    assert stats.total_batches == 4
    assert stats.successful_batches == 0
    assert stats.pause_count >= 2
    assert market.EASTMONEY_FAILURE_PAUSE_SECONDS in sleep_calls
    assert market.EASTMONEY_RETRY_COOLDOWN_SECONDS in sleep_calls


def test_eastmoney_index_quote_parsing(monkeypatch) -> None:
    """指数行情需要保留负涨跌值并解析大额成交额。"""
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": {
                    "diff": [
                        {
                            "f2": 3835.71,
                            "f3": -0.56,
                            "f4": -21.51,
                            "f6": 438_402_813_286.9,
                            "f12": "000001",
                            "f14": "上证指数",
                        }
                    ]
                }
            }

    captured: dict = {}

    def fake_get(*_args, **kwargs) -> FakeResponse:
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(market.requests, "get", fake_get)
    quotes = market.fetch_eastmoney_indices()

    assert captured["headers"]["Connection"] == "close"
    assert quotes["000001"]["price"] == Decimal("3835.71")
    assert quotes["000001"]["change"] == Decimal("-21.51")
    assert quotes["000001"]["change_percent"] == Decimal("-0.56")
    assert quotes["000001"]["turnover"] == Decimal("438402813286.90")


def test_stock_pool_has_200_unique_symbols() -> None:
    """固定股票池必须恰好包含 200 个不重复代码。"""
    assert len(STOCK_POOL) == 200
    assert len({item[0] for item in STOCK_POOL}) == 200


def test_real_quote_overwrites_selected_stock(monkeypatch) -> None:
    """真实行情只覆盖返回有效报价的股票，成交量从手转换后的股写入。"""
    fetched_at = datetime.now(UTC).replace(tzinfo=None)

    def fake_quotes(_symbols):
        return {
            "600519": {
                "name": "贵州茅台",
                "price": Decimal("1500.25"),
                "prev_close": Decimal("1498.00"),
                "open_price": Decimal("1499.00"),
                "high_price": Decimal("1510.00"),
                "low_price": Decimal("1488.00"),
                "volume": 12_345_600,
                "updated_at": fetched_at,
            }
        }

    monkeypatch.setattr(market, "fetch_eastmoney_quotes", fake_quotes)

    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        original = {
            "name": stock.name,
            "price": stock.price,
            "prev_close": stock.prev_close,
            "open_price": stock.open_price,
            "high_price": stock.high_price,
            "low_price": stock.low_price,
            "volume": stock.volume,
            "updated_at": stock.updated_at,
        }
        try:
            assert market.tick_market(db) == 1
            db.refresh(stock)
            assert stock.price == Decimal("1500.25")
            assert stock.volume == 12_345_600
            assert stock.updated_at == fetched_at
        finally:
            for field, value in original.items():
                setattr(stock, field, value)
            db.commit()


def test_tick_market_prioritizes_stalest_stock(monkeypatch) -> None:
    """数据库中更新时间最早的股票必须排在下一轮请求最前面。"""
    captured_symbols: list[str] = []

    def capture_quotes(symbols):
        captured_symbols.extend(symbols)
        return {}

    monkeypatch.setattr(market, "fetch_eastmoney_quotes", capture_quotes)

    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        original_updated_at = stock.updated_at
        try:
            stock.updated_at = datetime(2000, 1, 1)
            db.commit()

            assert market.tick_market(db) == 0
            assert captured_symbols[0] == "600519"
        finally:
            stock.updated_at = original_updated_at
            db.commit()


def test_market_failure_keeps_last_quote(monkeypatch) -> None:
    """行情源失败时不提交任何价格变更。"""
    def fail_quotes(_symbols):
        raise market.MarketDataError("测试行情故障")

    monkeypatch.setattr(market, "fetch_eastmoney_quotes", fail_quotes)

    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        original_price = stock.price
        with pytest.raises(market.MarketDataError):
            market.tick_market(db)
        db.refresh(stock)
        assert stock.price == original_price


def test_buy_then_sell() -> None:
    """
    测试完整的买入→卖出交易流程。

    流程：
        1. 以市价买入 100 股 "600519"（贵州茅台）
        2. 验证订单状态为 FILLED
        3. 验证持仓中有贵州茅台，数量 ≥ 100
        4. 验证订单列表有 1 条记录
        5. 验证成交记录有 1 条
        6. 卖出 100 股贵州茅台
        7. 验证卖出订单成功

    此测试覆盖了交易引擎的核心路径：
        资金校验 → 持仓创建 → 均价计算 → 成交记录 → 资金流水
    """
    with TestClient(app) as client:
        # ---- 买入 ----
        buy = client.post(
            "/api/orders",
            json={"symbol": "600519", "side": "BUY", "quantity": 100},
        )
        # 验证 HTTP 状态码为 201 Created
        assert buy.status_code == 201, buy.text
        # 验证订单状态为已成交
        assert buy.json()["status"] == "FILLED"

        # ---- 验证买入后的状态 ----
        # 持仓中应有贵州茅台
        positions = client.get("/api/positions").json()
        target = next(
            item for item in positions if item["symbol"] == "600519"
        )
        assert target["quantity"] >= 100

        # 订单列表应有 1 条记录
        assert len(client.get("/api/orders").json()) == 1

        # 成交记录应有 1 条
        assert len(client.get("/api/trades").json()) == 1

        # ---- 卖出 ----
        sell = client.post(
            "/api/orders",
            json={"symbol": "600519", "side": "SELL", "quantity": 100},
        )
        # 验证卖出成功
        assert sell.status_code == 201, sell.text


def test_reject_invalid_lot() -> None:
    """
    测试非整百股委托被正确拒绝。

    A 股交易规则要求买卖数量必须是 100 股（1 手）的整数倍。
    发送 quantity=1（1 股）的买入委托，期望返回 422 Unprocessable Entity。

    422 状态码表示请求体格式正确但业务校验失败（Pydantic validator 抛出异常）。
    """
    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
            json={"symbol": "600519", "side": "BUY", "quantity": 1},
        )
        # 期望返回 422（Pydantic 校验失败）
        assert response.status_code == 422

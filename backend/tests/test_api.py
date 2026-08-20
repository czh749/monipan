"""
API 集成测试。

本测试文件使用 FastAPI 的 TestClient 进行端到端集成测试，
验证 API 端点的正确性和业务逻辑的完整性。

测试覆盖：
    1. 健康检查和股票池 — 验证基础服务可用 + 200 只股票存在
    2. 完整买卖流程   — 买入 → 验证持仓/订单/成交 → 卖出
    3. 无效手数拒绝   — 验证非 100 整数倍被拒绝

测试数据管理：
    每个测试前后自动清理测试用户及账户级交易数据，保证测试之间相互独立。

运行方式：
    cd backend
    python -m pytest           # 运行所有测试
    python -m pytest -v        # 详细输出
    python -m pytest -k "buy"  # 只运行包含 "buy" 的测试
"""

import hashlib
import os
import re
import shutil
import tempfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

# 集成测试必须离线、可重复，禁止在应用生命周期中请求真实行情。
os.environ["MONIPAN_ENVIRONMENT"] = "test"
os.environ["MONIPAN_DISABLE_MARKET_LOOP"] = "1"
os.environ["MONIPAN_COOKIE_SECURE"] = "0"
TEST_DATABASE_PATH = (
    Path(tempfile.gettempdir()) / f"monipan-test-{uuid4().hex}.db"
)
TEST_DOCUMENT_TEMP_PATH = (
    Path(tempfile.gettempdir()) / f"monipan-documents-test-{uuid4().hex}"
)
os.environ["MONIPAN_DATABASE_URL"] = (
    f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"
)

from app import auth as app_auth
from app.database import Base, SessionLocal, engine
from app.auth import hash_invite_code
from app.agent import (
    AgentEvidenceRecord,
    AgentPersistenceError,
    AgentRecommendationRecord,
    AgentTokenUsage,
    READONLY_TOOL_NAMES,
    build_readonly_tools,
    complete_agent_run,
    fail_agent_run,
    start_agent_run,
)
from app.agent import portfolio as agent_portfolio
from app.agent import tools as agent_tools
from app.agent import orchestrator as agent_orchestrator
from app.agent.evidence import EvidenceSearchResult
from app import main as app_main
from app.main import app
from app.invites import create_invitations, disable_invitation
from app.models import (
    AccountTransaction,
    AgentEvidence,
    AgentRecommendation,
    AgentRun,
    AnnouncementSyncState,
    AuthSession,
    CompanyAnnouncement,
    FinancialReport,
    InvitationCode,
    MarketIndex,
    Order,
    OfficialDocumentChunk,
    OfficialDocumentContent,
    Position,
    RateLimitCounter,
    RateLimitLease,
    PerformanceEvent,
    RegulatoryLetter,
    RegulatoryLetterReply,
    RegulatorySyncState,
    SimulationAccount,
    Stock,
    StockBar,
    Trade,
    User,
    WatchlistItem,
)
from app.seed import seed_database
from app.rate_limit import acquire_analysis_lease, release_analysis_lease
from app.services import market
from app.services import announcements as announcements_service
from app.services import documents as documents_service
from app.services import document_search as document_search_service
from app.services import fundamentals as fundamentals_service
from app.services import news as news_service
from app.services import regulatory as regulatory_service
from app.services.market import history as history_service
from app.services.news import NewsSearchResult
from app.stock_pool import STOCK_POOL


# ---------------------------------------------------------------------------
# 测试辅助函数
# ---------------------------------------------------------------------------

def reset_user_accounts() -> None:
    """
    清理测试产生的用户和账户级数据。

    清理以下表的数据：
        - account_transactions: 资金流水
        - trades: 成交记录
        - orders: 委托订单
        - positions: 持仓记录

    在每次测试前后调用，确保测试隔离性。
    """
    with SessionLocal() as db:
        # 按依赖顺序删除：先删子表，再删父表
        db.execute(delete(RateLimitLease))
        db.execute(delete(RateLimitCounter))
        db.execute(delete(AgentRecommendation))
        db.execute(delete(AgentEvidence))
        db.execute(delete(AgentRun))
        db.execute(delete(AccountTransaction))
        db.execute(delete(Trade))
        db.execute(delete(Order))
        db.execute(delete(Position))
        db.execute(delete(WatchlistItem))
        db.execute(delete(AuthSession))
        db.execute(delete(InvitationCode))
        db.execute(delete(OfficialDocumentChunk))
        db.execute(delete(OfficialDocumentContent))
        db.execute(delete(RegulatoryLetterReply))
        db.execute(delete(RegulatorySyncState))
        db.execute(delete(RegulatoryLetter))
        db.execute(delete(AnnouncementSyncState))
        db.execute(delete(CompanyAnnouncement))
        db.execute(delete(PerformanceEvent))
        db.execute(delete(FinancialReport))

        db.execute(delete(SimulationAccount))
        db.execute(delete(User))

        db.commit()


def register_test_user(
    client: TestClient,
    username: str | None = None,
) -> str:
    selected_username = username or f"trader_{uuid4().hex[:8]}"
    invite_code = create_test_invite()
    response = client.post(
        "/api/auth/register",
        json={
            "username": selected_username,
            "password": "StrongPass2026",
            "invite_code": invite_code,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["username"] == selected_username
    return selected_username


def create_test_invite(
    *,
    expires_at: datetime | None = None,
    disabled_at: datetime | None = None,
) -> str:
    raw_code = f"invite-{uuid4().hex}"
    with SessionLocal() as db:
        db.add(
            InvitationCode(
                code_hash=hash_invite_code(raw_code),
                expires_at=expires_at,
                disabled_at=disabled_at,
                label="pytest",
            )
        )
        db.commit()
    return raw_code


def order_headers(idempotency_key: str | None = None) -> dict[str, str]:
    return {
        "Idempotency-Key": idempotency_key or f"test-{uuid4().hex}",
    }


def minimal_text_pdf(text: str = "Official risk notice") -> bytes:
    """Build a one-page PDF fixture without relying on a PDF writer in tests."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 20 100 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content
        + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_number, value in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{object_number} 0 obj\n".encode("ascii"))
        payload.extend(value)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


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
    shutil.rmtree(TEST_DOCUMENT_TEMP_PATH, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_trading_data():
    """
    自动执行的测试夹具：每个测试前后清理数据。

    autouse=True: 每个测试函数自动使用此夹具，无需手动声明。

    执行顺序：
        1. reset_user_accounts() — 测试前清理
        2. yield — 执行测试
        3. reset_user_accounts() — 测试后清理
    """
    reset_user_accounts()
    yield
    reset_user_accounts()


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
        health = client.get("/health", headers={"X-Request-ID": "health-test"})
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert health.headers["X-Request-ID"] == "health-test"
        assert health.headers["Cache-Control"] == "no-store"
        liveness = client.get("/livez", headers={"X-Request-ID": "x" * 100})
        assert liveness.status_code == 200
        assert liveness.json() == {"status": "ok"}
        assert re.fullmatch(r"[0-9a-f]{32}", liveness.headers["X-Request-ID"])
        readiness = client.get("/readyz")
        assert readiness.status_code == 200
        assert readiness.json() == {
            "status": "ok",
            "database": "ok",
        }
        assert readiness.headers["Cache-Control"] == "no-store"

        # 股票列表
        register_test_user(client)
        response = client.get("/api/stocks")
        assert response.status_code == 200
        # 验证恰好 200 只股票
        assert len(response.json()) == 200


def test_readiness_hides_database_failure_details(monkeypatch) -> None:
    def unavailable_session():
        raise SQLAlchemyError("sensitive database failure")

    with TestClient(app) as client:
        monkeypatch.setattr(app_main, "SessionLocal", unavailable_session)
        response = client.get("/readyz")
        health = client.get("/health")
        liveness = client.get("/livez")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "database": "unavailable",
    }
    assert "sensitive" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])
    assert health.status_code == 200
    assert liveness.status_code == 200


def test_runtime_configuration_validation_runs_during_lifespan(monkeypatch) -> None:
    calls = 0

    def validate_once():
        nonlocal calls
        calls += 1
        return SimpleNamespace(environment="test")

    monkeypatch.setattr(app_main, "validate_runtime_configuration", validate_once)
    with TestClient(app):
        pass

    assert calls == 1


def test_session_cookie_uses_secure_production_attributes(monkeypatch) -> None:
    monkeypatch.setattr(app_auth, "COOKIE_SECURE", True)

    response = Response()
    app_auth.set_session_cookie(response, "opaque-token")
    cookie = response.headers["set-cookie"].lower()

    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "max-age=" in cookie

    cleared = Response()
    app_auth.clear_session_cookie(cleared)
    cleared_cookie = cleared.headers["set-cookie"].lower()
    assert "secure" in cleared_cookie
    assert "httponly" in cleared_cookie
    assert "samesite=lax" in cleared_cookie
    assert "path=/" in cleared_cookie


def test_market_seed_does_not_recreate_demo_user() -> None:
    with SessionLocal() as db:
        seed_database(db)
        assert db.scalar(select(User).where(User.username == "demo")) is None


def test_private_api_requires_authentication() -> None:
    with TestClient(app) as client:
        protected_paths = (
            "/api/stocks",
            "/api/stocks/600519",
            "/api/stocks/600519/history",
            "/api/stocks/600519/fundamentals",
            "/api/stocks/600519/announcements",
            "/api/stocks/600519/regulatory-letters",
            "/api/market/status",
            "/api/market/indices",
            "/api/account",
        )
        for path in protected_paths:
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.headers["www-authenticate"] == "Session"


def test_manual_market_tick_is_not_exposed() -> None:
    with TestClient(app) as client:
        assert client.post("/api/market/tick").status_code == 404


def test_registration_is_rate_limited_by_ip() -> None:
    with TestClient(app) as client:
        for index in range(3):
            invite_code = create_test_invite()
            response = client.post(
                "/api/auth/register",
                json={
                    "username": f"limited_registration_{index}",
                    "password": "StrongPass2026",
                    "invite_code": invite_code,
                },
            )
            assert response.status_code == 201, response.text

        blocked = client.post(
            "/api/auth/register",
            json={
                "username": "limited_registration_blocked",
                "password": "StrongPass2026",
                "invite_code": create_test_invite(),
            },
        )
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "RATE_LIMITED"
        assert int(blocked.headers["retry-after"]) > 0


def test_registration_requires_a_valid_one_time_invitation() -> None:
    with TestClient(app) as client:
        invalid = client.post(
            "/api/auth/register",
            json={
                "username": "invalid_invite_user",
                "password": "StrongPass2026",
                "invite_code": "invalid-invitation-code",
            },
        )
        assert invalid.status_code == 409

        invite_code = create_test_invite()
        first = client.post(
            "/api/auth/register",
            json={
                "username": "invited_user",
                "password": "StrongPass2026",
                "invite_code": invite_code,
            },
        )
        assert first.status_code == 201, first.text

        reused = client.post(
            "/api/auth/register",
            json={
                "username": "invite_reuse_user",
                "password": "StrongPass2026",
                "invite_code": invite_code,
            },
        )
        assert reused.status_code == 409
        assert "邀请码无效" in reused.json()["detail"]

        with SessionLocal() as db:
            invitation = db.scalar(
                select(InvitationCode).where(
                    InvitationCode.code_hash == hash_invite_code(invite_code)
                )
            )
            assert invitation is not None
            assert invitation.used_at is not None
            assert invitation.used_by_user_id is not None
            assert db.scalar(
                select(User.id).where(User.username == "invite_reuse_user")
            ) is None


def test_registration_rejects_expired_and_disabled_invitations() -> None:
    expired = create_test_invite(
        expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    )
    disabled = create_test_invite(
        disabled_at=datetime.now(UTC).replace(tzinfo=None)
    )
    with TestClient(app) as client:
        for username, invite_code in (
            ("expired_invite_user", expired),
            ("disabled_invite_user", disabled),
        ):
            response = client.post(
                "/api/auth/register",
                json={
                    "username": username,
                    "password": "StrongPass2026",
                    "invite_code": invite_code,
                },
            )
            assert response.status_code == 409


def test_invitation_generator_stores_only_hash_and_can_disable() -> None:
    raw_code = create_invitations(
        count=1,
        expires_days=30,
        label="pytest-generated",
    )[0]
    with SessionLocal() as db:
        invitation = db.scalar(
            select(InvitationCode).where(
                InvitationCode.code_hash == hash_invite_code(raw_code)
            )
        )
        assert invitation is not None
        assert invitation.code_hash != raw_code
        assert invitation.label == "pytest-generated"
        assert invitation.expires_at is not None

    assert disable_invitation(raw_code) is True
    with SessionLocal() as db:
        invitation = db.scalar(
            select(InvitationCode).where(
                InvitationCode.code_hash == hash_invite_code(raw_code)
            )
        )
        assert invitation is not None
        assert invitation.disabled_at is not None


def test_failed_login_limit_clears_after_correct_password() -> None:
    with TestClient(app) as client:
        register_test_user(client, "limited_login_user")
        for _attempt in range(5):
            response = client.post(
                "/api/auth/login",
                json={
                    "username": "limited_login_user",
                    "password": "WrongPass2026",
                },
            )
            assert response.status_code == 401

        blocked = client.post(
            "/api/auth/login",
            json={
                "username": "limited_login_user",
                "password": "WrongPass2026",
            },
        )
        assert blocked.status_code == 429

        recovered = client.post(
            "/api/auth/login",
            json={
                "username": "limited_login_user",
                "password": "StrongPass2026",
            },
        )
        assert recovered.status_code == 200


def test_ai_concurrency_leases_limit_user_and_global_capacity() -> None:
    first = acquire_analysis_lease(user_id=101, global_limit=1, ttl_seconds=60)
    try:
        with pytest.raises(HTTPException) as same_user:
            acquire_analysis_lease(user_id=101, global_limit=1, ttl_seconds=60)
        assert same_user.value.status_code == 429
        assert same_user.value.detail["code"] == "AI_ALREADY_RUNNING"

        with pytest.raises(HTTPException) as global_capacity:
            acquire_analysis_lease(user_id=202, global_limit=1, ttl_seconds=60)
        assert global_capacity.value.status_code == 429
        assert global_capacity.value.detail["code"] == "AI_CAPACITY_BUSY"
    finally:
        release_analysis_lease(first)

    second = acquire_analysis_lease(user_id=202, global_limit=1, ttl_seconds=60)
    release_analysis_lease(second)


def test_register_session_and_logout() -> None:
    with TestClient(app) as client:
        username = register_test_user(client, "auth_user")
        cookie = client.cookies.get("monipan_session")
        assert cookie is not None

        current = client.get("/api/auth/me")
        assert current.status_code == 200
        assert current.json()["username"] == username
        assert client.get("/api/account").json()["username"] == username

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401


def test_user_orders_and_watchlist_are_isolated() -> None:
    with TestClient(app) as first_client:
        register_test_user(first_client, "first_user")
        assert first_client.post("/api/watchlist/600519").status_code == 201
        assert first_client.post(
            "/api/orders",
            json={"symbol": "600519", "side": "BUY", "quantity": 100},
            headers=order_headers(),
        ).status_code == 201

        with TestClient(app) as second_client:
            register_test_user(second_client, "second_user")
            assert second_client.get("/api/watchlist").json() == []
            assert second_client.get("/api/orders").json() == []
            assert second_client.get("/api/positions").json() == []

        assert [
            item["symbol"] for item in first_client.get("/api/watchlist").json()
        ] == ["600519"]
        assert len(first_client.get("/api/orders").json()) == 1


def test_market_status_exposes_source_and_freshness_policy() -> None:
    """行情状态接口需要明确真实来源、刷新阈值、覆盖率和模拟交易边界。"""
    with TestClient(app) as client:
        register_test_user(client)
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


def test_stock_fundamentals_refreshes_once_then_uses_cache(monkeypatch) -> None:
    """财务接口首次按需采集，后续请求必须直接命中结构化缓存。"""
    calls = {"reports": 0, "forecasts": 0}

    def fake_reports(_symbol: str) -> list[dict]:
        calls["reports"] += 1
        return [
            {
                "report_period": date(2025, 12, 31),
                "report_type": "ANNUAL",
                "report_name": "2025年年报",
                "announcement_date": date(2026, 4, 17),
                "revenue": Decimal("172054171890.91"),
                "net_profit_parent": Decimal("82320067101.68"),
                "basic_eps": Decimal("65.66"),
                "deducted_eps": Decimal("65.64"),
                "weighted_roe": Decimal("32.53"),
                "gross_margin": Decimal("91.18"),
                "revenue_yoy": Decimal("-1.20"),
                "net_profit_yoy": Decimal("-4.53"),
                "book_value_per_share": Decimal("195.36"),
                "operating_cash_flow_per_share": Decimal("49.13"),
                "source_updated_at": datetime(2026, 4, 16, 22, 50),
                "source_url": "https://data.eastmoney.com/bbsj/202512/yjbb.html",
                "content_hash": "a" * 64,
            }
        ]

    def fake_forecasts(_symbol: str) -> list[dict]:
        calls["forecasts"] += 1
        return [
            {
                "event_type": "FORECAST",
                "report_period": date(2024, 12, 31),
                "report_name": "2024年年报",
                "announcement_date": date(2025, 1, 3),
                "forecast_type": "略增",
                "revenue_lower": Decimal("173800000000"),
                "revenue_upper": Decimal("173800000000"),
                "revenue_growth_lower": Decimal("15.44"),
                "revenue_growth_upper": Decimal("15.44"),
                "net_profit_lower": Decimal("85700000000"),
                "net_profit_upper": Decimal("85700000000"),
                "net_profit_growth_lower": Decimal("14.67"),
                "net_profit_growth_upper": Decimal("14.67"),
                "summary": "预计营业收入和归母净利润同比增长。",
                "reason": None,
                "source_url": "https://data.eastmoney.com/bbsj/202412/yjyg.html",
                "content_hash": "b" * 64,
            }
        ]

    monkeypatch.setattr(
        fundamentals_service,
        "fetch_eastmoney_financial_reports",
        fake_reports,
    )
    monkeypatch.setattr(
        fundamentals_service,
        "fetch_eastmoney_performance_forecasts",
        fake_forecasts,
    )

    with TestClient(app) as client:
        register_test_user(client)
        first = client.get("/api/stocks/600519/fundamentals")
        second = client.get("/api/stocks/600519/fundamentals")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["cache_status"] == "REFRESHED"
    assert second.json()["cache_status"] == "CACHED"
    assert second.json()["latest_report"]["report_name"] == "2025年年报"
    assert second.json()["latest_report"]["revenue"] == "172054171890.91"
    assert {item["event_type"] for item in second.json()["events"]} == {
        "FORECAST",
        "REPORT",
    }
    assert calls == {"reports": 1, "forecasts": 1}

    with SessionLocal() as db:
        assert len(db.scalars(select(FinancialReport)).all()) == 1
        assert len(db.scalars(select(PerformanceEvent)).all()) == 1


def test_stock_announcements_refreshes_then_uses_cache(monkeypatch) -> None:
    """上交所公告首次同步元数据，后续请求直接读取本地缓存。"""
    calls = {"count": 0}
    announcement_date = datetime.now(UTC).date() - timedelta(days=2)

    def fake_announcements(
        symbol: str,
        _begin_date: date,
        _end_date: date,
    ) -> list[dict]:
        calls["count"] += 1
        assert symbol == "600519"
        return [
            {
                "exchange": "SSE",
                "external_id": "600519_20260803_TEST",
                "title": "贵州茅台测试公告",
                "announcement_date": announcement_date,
                "announcement_heading": "临时公告",
                "announcement_type": "其它",
                "source_url": (
                    "https://www.sse.com.cn/disclosure/listedinfo/"
                    "announcement/c/new/2026-08-03/600519_20260803_TEST.pdf"
                ),
                "source_published_at": datetime(2026, 8, 2, 20, 30),
                "content_hash": "c" * 64,
            }
        ]

    monkeypatch.setattr(
        announcements_service,
        "fetch_sse_announcements",
        fake_announcements,
    )

    with TestClient(app) as client:
        register_test_user(client)
        first = client.get("/api/stocks/600519/announcements")
        second = client.get("/api/stocks/600519/announcements")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["cache_status"] == "REFRESHED"
    assert second.json()["cache_status"] == "CACHED"
    assert second.json()["provider"] == "SSE"
    assert second.json()["announcements"][0]["external_id"] == (
        "600519_20260803_TEST"
    )
    assert second.json()["announcements"][0]["announcement_heading"] == "临时公告"
    assert second.json()["announcements"][0]["source_url"].endswith("TEST.pdf")
    assert calls == {"count": 1}

    with SessionLocal() as db:
        assert len(db.scalars(select(CompanyAnnouncement)).all()) == 1
        assert len(db.scalars(select(AnnouncementSyncState)).all()) == 1


def test_sse_announcement_fetch_reads_every_page(monkeypatch) -> None:
    """上交所目录分页必须完整读取，不能只缓存第一页却标记完整覆盖。"""
    requested_pages: list[int] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_get(_url, *, params, headers, timeout):
        assert headers["Referer"] == "https://www.sse.com.cn/"
        assert timeout == 15
        page_no = int(params["pageHelp.pageNo"])
        requested_pages.append(page_no)
        return FakeResponse(
            {
                "pageHelp": {
                    "pageCount": 2,
                    "data": [
                        {
                            "SECURITY_CODE": "600519",
                            "TITLE": f"第{page_no}页公告",
                            "SSEDATE": f"2026-08-0{4 - page_no}",
                            "BULLETIN_HEADING": "临时公告",
                            "BULLETIN_TYPE": "其它",
                            "URL": (
                                "/disclosure/listedinfo/announcement/c/new/"
                                f"2026-08-0{4 - page_no}/600519_PAGE_{page_no}.pdf"
                            ),
                            "ADDDATE": f"2026-08-0{3 - page_no} 20:00:00",
                        }
                    ],
                }
            }
        )

    monkeypatch.setattr(announcements_service.requests, "get", fake_get)
    rows = announcements_service.fetch_sse_announcements(
        "600519",
        date(2026, 1, 1),
        date(2026, 8, 5),
    )

    assert requested_pages == [1, 2]
    assert [item["external_id"] for item in rows] == [
        "600519_PAGE_1",
        "600519_PAGE_2",
    ]


def test_szse_announcement_fetch_reads_every_page(monkeypatch) -> None:
    """深交所总条数用于完整分页，并转换官方附件地址。"""
    requested_pages: list[int] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(_url, *, json, headers, timeout):
        assert headers["Referer"].startswith("https://www.szse.cn/")
        assert timeout == 15
        page_no = int(json["pageNum"])
        requested_pages.append(page_no)
        return FakeResponse(
            {
                "announceCount": 51,
                "data": [
                    {
                        "id": f"szse-page-{page_no}",
                        "annId": 1000 + page_no,
                        "title": f"第{page_no}页深交所公告",
                        "publishTime": f"2026-08-0{4 - page_no} 00:00:00",
                        "attachPath": (
                            "/disc/disk03/finalpage/2026-08-03/"
                            f"szse-page-{page_no}.PDF"
                        ),
                        "secCode": ["000858"],
                    }
                ],
            }
        )

    monkeypatch.setattr(announcements_service.requests, "post", fake_post)
    rows = announcements_service.fetch_szse_announcements(
        "000858",
        date(2026, 1, 1),
        date(2026, 8, 5),
    )

    assert requested_pages == [1, 2]
    assert [item["external_id"] for item in rows] == ["1001", "1002"]
    assert rows[0]["exchange"] == "SZSE"
    assert rows[0]["source_url"] == (
        "https://disc.static.szse.cn/download/disc/disk03/finalpage/"
        "2026-08-03/szse-page-1.PDF"
    )


def test_stock_announcements_uses_stale_cache_on_source_failure(monkeypatch) -> None:
    """上交所短时不可用时保留并返回最近一次成功同步的公告。"""
    announcement_date = datetime.now(UTC).date() - timedelta(days=3)
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        fetched_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        db.add(
            CompanyAnnouncement(
                stock_id=stock.id,
                exchange="SSE",
                external_id="600519_STALE_TEST",
                title="缓存公告",
                announcement_date=announcement_date,
                announcement_heading="临时公告",
                announcement_type="其它",
                source_url="https://www.sse.com.cn/stale-test.pdf",
                source_published_at=fetched_at,
                content_hash="d" * 64,
                fetched_at=fetched_at,
            )
        )
        db.add(
            AnnouncementSyncState(
                stock_id=stock.id,
                exchange="SSE",
                covered_from=announcement_date - timedelta(days=400),
                covered_to=datetime.now(UTC).date(),
                last_success_at=fetched_at,
            )
        )
        db.commit()

    def fail_fetch(*_args, **_kwargs):
        raise announcements_service.AnnouncementDataError("上交所暂时不可用")

    monkeypatch.setattr(
        announcements_service,
        "fetch_sse_announcements",
        fail_fetch,
    )
    with TestClient(app) as client:
        register_test_user(client)
        response = client.get("/api/stocks/600519/announcements")

    assert response.status_code == 200, response.text
    assert response.json()["cache_status"] == "STALE"
    assert response.json()["announcements"][0]["title"] == "缓存公告"


def test_stock_announcements_selects_szse_provider(monkeypatch) -> None:
    announcement_date = datetime.now(UTC).date() - timedelta(days=2)

    def fake_announcements(
        symbol: str,
        _begin_date: date,
        _end_date: date,
    ) -> list[dict]:
        assert symbol == "000001"
        return [
            {
                "exchange": "SZSE",
                "external_id": "1225000001",
                "title": "平安银行测试公告",
                "announcement_date": announcement_date,
                "announcement_heading": "上市公司公告",
                "announcement_type": None,
                "source_url": (
                    "https://disc.static.szse.cn/download/disc/"
                    "disk03/finalpage/2026-08-03/test.PDF"
                ),
                "source_published_at": datetime(2026, 8, 3),
                "content_hash": "e" * 64,
            }
        ]

    monkeypatch.setattr(
        announcements_service,
        "fetch_szse_announcements",
        fake_announcements,
    )
    with TestClient(app) as client:
        register_test_user(client)
        response = client.get("/api/stocks/000001/announcements")

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "SZSE"
    assert response.json()["provider_label"] == "深圳证券交易所"
    assert response.json()["announcements"][0]["exchange"] == "SZSE"


def test_stock_regulatory_letters_refreshes_then_uses_cache(monkeypatch) -> None:
    """监管函与交易所直接给出的回复关系会一并缓存。"""
    calls = {"count": 0}
    issued_date = datetime.now(UTC).date() - timedelta(days=5)

    def fake_letters(symbol: str, _begin_date: date, _end_date: date) -> list[dict]:
        calls["count"] += 1
        assert symbol == "000001"
        return [
            {
                "exchange": "SZSE",
                "external_id": "szse-letter-test",
                "title": "平安银行：年报问询函",
                "letter_type": "年报问询函",
                "issued_date": issued_date,
                "source_url": "https://reportdocs.static.szse.cn/letter.pdf",
                "content_hash": "a" * 64,
                "replies": [
                    {
                        "external_id": "szse-reply-test",
                        "title": "关于年报问询函的回复公告",
                        "reply_date": None,
                        "source_url": "https://reportdocs.static.szse.cn/reply.pdf",
                        "match_method": "SOURCE",
                        "announcement_id": None,
                        "content_hash": "b" * 64,
                    }
                ],
            }
        ]

    monkeypatch.setattr(
        regulatory_service,
        "fetch_szse_regulatory_letters",
        fake_letters,
    )
    with TestClient(app) as client:
        register_test_user(client)
        first = client.get("/api/stocks/000001/regulatory-letters")
        second = client.get("/api/stocks/000001/regulatory-letters")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["cache_status"] == "REFRESHED"
    assert second.json()["cache_status"] == "CACHED"
    assert second.json()["letters"][0]["reply_status"] == "REPLIED"
    assert second.json()["letters"][0]["replies"][0]["match_method"] == "SOURCE"
    assert calls == {"count": 1}

    with SessionLocal() as db:
        assert len(db.scalars(select(RegulatoryLetter)).all()) == 1
        assert len(db.scalars(select(RegulatoryLetterReply)).all()) == 1
        assert len(db.scalars(select(RegulatorySyncState)).all()) == 1


def test_sse_regulatory_fetch_reads_catalogue_fields(monkeypatch) -> None:
    """上交所监管目录分页并规范化函件标题、类型、日期和原文链接。"""
    requested_pages: list[int] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_get(_url, *, params, headers, timeout):
        assert headers["Referer"].endswith("/regulation/supervision/inquiries/")
        assert timeout == 15
        page_no = int(params["pageHelp.pageNo"])
        requested_pages.append(page_no)
        return FakeResponse(
            {
                "pageHelp": {"pageCount": 2},
                "result": [
                    {
                        "extSECURITY_CODE": "600519",
                        "docId": f"letter-{page_no}",
                        "docTitle": f"第{page_no}页问询函",
                        "extWTFL": "问询函",
                        "createTime": f"2026-08-0{4 - page_no} 20:00:00",
                        "docURL": f"www.sse.com.cn/letter-{page_no}.pdf",
                    }
                ],
            }
        )

    monkeypatch.setattr(regulatory_service.requests, "get", fake_get)
    rows = regulatory_service.fetch_sse_regulatory_letters(
        "600519",
        date(2026, 1, 1),
        date(2026, 8, 5),
    )

    assert requested_pages == [1, 2]
    assert [item["external_id"] for item in rows] == ["letter-1", "letter-2"]
    assert rows[0]["letter_type"] == "问询函"
    assert rows[0]["source_url"] == "https://www.sse.com.cn/letter-1.pdf"


def test_szse_regulatory_fetch_groups_direct_replies(monkeypatch) -> None:
    """深交所同一函件的多份回复合并为一个函件及多个回复关系。"""

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            letter = (
                "<a href='javascript:void(0)' "
                "encode-open='/UpFiles/zqjghj/letter-one.pdf'>详细内容</a>"
            )
            return [
                {
                    "metadata": {"tabkey": "tab2", "pagecount": 1},
                    "data": [
                        {
                            "gsdm": "000001",
                            "gsjc": "平安银行",
                            "fhrq": "2026-05-21",
                            "hjlb": "年报问询函",
                            "ck": letter,
                            "hfck": (
                                "<a href='javascript:void(0)' "
                                "encode-open='/UpFiles/zqjghj/reply-one.pdf'>"
                                "第一次回复</a>"
                            ),
                        },
                        {
                            "gsdm": "000001",
                            "gsjc": "平安银行",
                            "fhrq": "2026-05-21",
                            "hjlb": "年报问询函",
                            "ck": letter,
                            "hfck": (
                                "<a href='javascript:void(0)' "
                                "encode-open='/UpFiles/zqjghj/reply-two.pdf'>"
                                "补充回复</a>"
                            ),
                        },
                    ],
                },
                {
                    "metadata": {"tabkey": "tab3", "pagecount": 1},
                    "data": [],
                },
            ]

    def fake_get(_url, *, params, headers, timeout):
        assert params["TABKEY"] == "tab2"
        assert params["txtZqdm"] == "000001"
        assert headers["Referer"].startswith("https://www.szse.cn/")
        assert timeout == 15
        return FakeResponse()

    monkeypatch.setattr(regulatory_service.requests, "get", fake_get)
    rows = regulatory_service.fetch_szse_regulatory_letters(
        "000001",
        date(2026, 1, 1),
        date(2026, 8, 5),
    )

    assert len(rows) == 1
    assert rows[0]["title"] == "平安银行：年报问询函"
    assert {reply["title"] for reply in rows[0]["replies"]} == {
        "第一次回复",
        "补充回复",
    }
    assert all(reply["match_method"] == "SOURCE" for reply in rows[0]["replies"])


def test_sse_regulatory_links_cached_reply_announcement(monkeypatch) -> None:
    """上交所公司回复仅在标题、年份和日期均兼容时建立关联。"""
    issued_date = datetime.now(UTC).date() - timedelta(days=20)
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        db.add(
            CompanyAnnouncement(
                stock_id=stock.id,
                exchange="SSE",
                external_id="reply-announcement",
                title="关于2025年年报问询函的回复公告",
                announcement_date=issued_date + timedelta(days=7),
                announcement_heading="临时公告",
                announcement_type="其它",
                source_url="https://www.sse.com.cn/reply-announcement.pdf",
                source_published_at=None,
                content_hash="c" * 64,
                fetched_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        db.commit()

    def fake_letters(_symbol: str, _begin_date: date, _end_date: date) -> list[dict]:
        return [
            {
                "exchange": "SSE",
                "external_id": "sse-letter-test",
                "title": "关于贵州茅台2025年年报有关事项的问询函",
                "letter_type": "定期报告信息披露监管问询函",
                "issued_date": issued_date,
                "source_url": "https://www.sse.com.cn/sse-letter-test.pdf",
                "content_hash": "d" * 64,
                "replies": [],
            }
        ]

    monkeypatch.setattr(
        regulatory_service,
        "fetch_sse_regulatory_letters",
        fake_letters,
    )
    with TestClient(app) as client:
        register_test_user(client)
        response = client.get("/api/stocks/600519/regulatory-letters")

    assert response.status_code == 200, response.text
    reply = response.json()["letters"][0]["replies"][0]
    assert reply["external_id"] == "reply-announcement"
    assert reply["match_method"] == "TITLE_DATE"
    assert response.json()["letters"][0]["reply_status"] == "REPLIED"


def test_stock_regulatory_letters_uses_stale_cache(monkeypatch) -> None:
    """交易所监管目录短时失败时仍返回最近成功缓存。"""
    issued_date = datetime.now(UTC).date() - timedelta(days=10)
    fetched_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        db.add(
            RegulatoryLetter(
                stock_id=stock.id,
                exchange="SSE",
                external_id="stale-regulatory-letter",
                title="缓存问询函",
                letter_type="问询函",
                issued_date=issued_date,
                source_url="https://www.sse.com.cn/stale-letter.pdf",
                content_hash="e" * 64,
                fetched_at=fetched_at,
            )
        )
        db.add(
            RegulatorySyncState(
                stock_id=stock.id,
                exchange="SSE",
                covered_from=issued_date - timedelta(days=400),
                covered_to=datetime.now(UTC).date(),
                last_success_at=fetched_at,
            )
        )
        db.commit()

    def fail_fetch(*_args, **_kwargs):
        raise regulatory_service.RegulatoryDataError("上交所暂时不可用")

    monkeypatch.setattr(
        regulatory_service,
        "fetch_sse_regulatory_letters",
        fail_fetch,
    )
    with TestClient(app) as client:
        register_test_user(client)
        response = client.get("/api/stocks/600519/regulatory-letters")

    assert response.status_code == 200, response.text
    assert response.json()["cache_status"] == "STALE"
    assert response.json()["letters"][0]["title"] == "缓存问询函"


def test_official_pdf_is_downloaded_extracted_and_cached(monkeypatch) -> None:
    """正文首次按需抽取，保留逐页偏移，后续不重复下载。"""
    pdf_bytes = minimal_text_pdf()
    calls = {"count": 0}

    class FakeResponse:
        status_code = 200
        headers = {
            "Content-Type": "application/pdf",
            "Content-Length": str(len(pdf_bytes)),
        }

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            assert chunk_size == 64 * 1024
            yield pdf_bytes[:100]
            yield pdf_bytes[100:]

        def close(self) -> None:
            return None

    def fake_get(url: str, **kwargs):
        calls["count"] += 1
        assert url == "https://www.sse.com.cn/test-official.pdf"
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        return FakeResponse()

    monkeypatch.setattr(documents_service.requests, "get", fake_get)
    temporary_pdf = TEST_DOCUMENT_TEMP_PATH / "download.pdf"
    temporary_pdf.parent.mkdir(parents=True, exist_ok=True)

    def fake_temporary_pdf_path() -> Path:
        temporary_pdf.touch()
        return temporary_pdf

    monkeypatch.setattr(
        documents_service,
        "_temporary_pdf_path",
        fake_temporary_pdf_path,
    )
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        announcement = CompanyAnnouncement(
            stock_id=stock.id,
            exchange="SSE",
            external_id="official-pdf-test",
            title="官方 PDF 按需抽取测试公告",
            announcement_date=datetime.now(UTC).date(),
            announcement_heading="临时公告",
            announcement_type="其它",
            source_url="https://www.sse.com.cn/test-official.pdf",
            source_published_at=None,
            content_hash="f" * 64,
            fetched_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(announcement)
        db.commit()
        db.refresh(announcement)

        first = documents_service.ensure_official_document_text(
            db,
            document_type="ANNOUNCEMENT",
            document_id=announcement.id,
            expected_stock_id=stock.id,
        )
        second = documents_service.ensure_official_document_text(
            db,
            document_type="ANNOUNCEMENT",
            document_id=announcement.id,
            expected_stock_id=stock.id,
        )

        assert first.cache_status == "EXTRACTED"
        assert second.cache_status == "CACHED"
        assert second.extraction_status == "READY"
        assert second.page_count == 1
        assert second.page_text(1) == "Official risk notice"
        assert second.page_offsets[0]["page_number"] == 1
        assert len(second.file_sha256) == 64
        assert len(second.text_sha256) == 64
        assert calls == {"count": 1}
        assert not temporary_pdf.exists()
        assert len(db.scalars(select(OfficialDocumentContent)).all()) == 1
        chunks = db.scalars(select(OfficialDocumentChunk)).all()
        assert len(chunks) == 1
        assert chunks[0].page_number == 1
        assert chunks[0].text == "Official risk notice"
        assert chunks[0].start_char == 0
        assert chunks[0].end_char == len("Official risk notice")


def test_official_pdf_rejects_non_exchange_domain(monkeypatch) -> None:
    """数据库中的异常链接不能把按需下载变成任意地址请求。"""
    def unexpected_get(*_args, **_kwargs):
        raise AssertionError("非交易所域名不应发起网络请求")

    monkeypatch.setattr(documents_service.requests, "get", unexpected_get)
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        announcement = CompanyAnnouncement(
            stock_id=stock.id,
            exchange="SSE",
            external_id="untrusted-pdf-test",
            title="异常链接测试公告",
            announcement_date=datetime.now(UTC).date(),
            announcement_heading="临时公告",
            announcement_type="其它",
            source_url="https://example.com/not-official.pdf",
            source_published_at=None,
            content_hash="0" * 64,
            fetched_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(announcement)
        db.commit()
        db.refresh(announcement)

        with pytest.raises(documents_service.OfficialDocumentError) as exc_info:
            documents_service.ensure_official_document_text(
                db,
                document_type="ANNOUNCEMENT",
                document_id=announcement.id,
                expected_stock_id=stock.id,
            )

    assert "受信任" in str(exc_info.value)


def test_official_pdf_force_refresh_falls_back_to_cache(monkeypatch) -> None:
    """已缓存正文刷新失败时继续返回旧内容，并明确标记 STALE。"""
    pdf_bytes = minimal_text_pdf("Cached official evidence")

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            yield pdf_bytes

        def close(self) -> None:
            return None

    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        announcement = CompanyAnnouncement(
            stock_id=stock.id,
            exchange="SSE",
            external_id="stale-pdf-test",
            title="正文缓存降级测试公告",
            announcement_date=datetime.now(UTC).date(),
            announcement_heading="临时公告",
            announcement_type="其它",
            source_url="https://www.sse.com.cn/stale-official.pdf",
            source_published_at=None,
            content_hash="1" * 64,
            fetched_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(announcement)
        db.commit()
        db.refresh(announcement)
        monkeypatch.setattr(
            documents_service.requests,
            "get",
            lambda *_args, **_kwargs: FakeResponse(),
        )
        first = documents_service.ensure_official_document_text(
            db,
            document_type="ANNOUNCEMENT",
            document_id=announcement.id,
        )

        def fail_get(*_args, **_kwargs):
            raise documents_service.requests.ConnectionError("测试网络故障")

        monkeypatch.setattr(documents_service.requests, "get", fail_get)
        stale = documents_service.ensure_official_document_text(
            db,
            document_type="ANNOUNCEMENT",
            document_id=announcement.id,
            force_refresh=True,
        )

    assert first.cache_status == "EXTRACTED"
    assert stale.cache_status == "STALE"
    assert stale.page_text(1) == "Cached official evidence"
    assert "测试网络故障" in (stale.extraction_warning or "")


def test_official_document_chunks_preserve_page_and_character_ranges() -> None:
    """长正文分段不得跨页，且每段必须能按位置还原原文。"""
    first_page = "风险提示。" * 200
    second_page = "公司应说明现金流、偿债能力及担保风险。"
    text_content = f"{first_page}\n\n{second_page}"
    second_start = len(first_page) + 2

    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        content = OfficialDocumentContent(
            stock_id=stock.id,
            document_type="REGULATORY_LETTER",
            document_id=900001,
            title="关于现金流风险的问询函",
            document_date=datetime.now(UTC).date(),
            source_url="https://www.sse.com.cn/chunk-test.pdf",
            source_content_hash="2" * 64,
            status="READY",
            mime_type="application/pdf",
            file_size=2048,
            file_sha256="3" * 64,
            page_count=2,
            text_content=text_content,
            page_offsets=[
                {
                    "page_number": 1,
                    "start_char": 0,
                    "end_char": len(first_page),
                    "text_sha256": hashlib.sha256(
                        first_page.encode("utf-8")
                    ).hexdigest(),
                },
                {
                    "page_number": 2,
                    "start_char": second_start,
                    "end_char": len(text_content),
                    "text_sha256": hashlib.sha256(
                        second_page.encode("utf-8")
                    ).hexdigest(),
                },
            ],
            text_sha256=hashlib.sha256(text_content.encode("utf-8")).hexdigest(),
            extraction_warning=None,
            downloaded_at=datetime.now(UTC).replace(tzinfo=None),
            extracted_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(content)
        db.flush()
        chunk_count = document_search_service.ensure_document_chunks(db, content)
        db.commit()

        chunks = db.scalars(
            select(OfficialDocumentChunk)
            .where(OfficialDocumentChunk.official_document_content_id == content.id)
            .order_by(OfficialDocumentChunk.chunk_index)
        ).all()
        assert chunk_count == len(chunks) == 3
        assert {chunk.page_number for chunk in chunks} == {1, 2}
        assert chunks[-1].page_number == 2
        for chunk in chunks:
            assert chunk.text == text_content[chunk.start_char:chunk.end_char]
            assert len(chunk.text) <= document_search_service.CHUNK_MAX_CHARS
            assert chunk.text_hash == hashlib.sha256(
                chunk.text.encode("utf-8")
            ).hexdigest()
            page = content.page_offsets[chunk.page_number - 1]
            assert page["start_char"] <= chunk.start_char < chunk.end_char
            assert chunk.end_char <= page["end_char"]


def test_official_document_keyword_search_ranks_and_filters() -> None:
    """检索按标题与正文命中排序，并保留页码、片段和来源。"""
    text_content = "公司应说明现金流变化，以及短期偿债能力和对外担保风险。"
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        content = OfficialDocumentContent(
            stock_id=stock.id,
            document_type="REGULATORY_LETTER",
            document_id=900002,
            title="关于现金流风险的问询函",
            document_date=datetime.now(UTC).date(),
            source_url="https://www.sse.com.cn/search-test.pdf",
            source_content_hash="4" * 64,
            status="READY",
            mime_type="application/pdf",
            file_size=1024,
            file_sha256="5" * 64,
            page_count=1,
            text_content=text_content,
            page_offsets=[
                {
                    "page_number": 1,
                    "start_char": 0,
                    "end_char": len(text_content),
                    "text_sha256": hashlib.sha256(
                        text_content.encode("utf-8")
                    ).hexdigest(),
                }
            ],
            text_sha256=hashlib.sha256(text_content.encode("utf-8")).hexdigest(),
            extraction_warning=None,
            downloaded_at=datetime.now(UTC).replace(tzinfo=None),
            extracted_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(content)
        db.flush()
        document_search_service.ensure_document_chunks(db, content)
        db.commit()

        hits = document_search_service.search_official_document_chunks(
            db,
            stock,
            query="现金流 风险",
            days=365,
            limit=10,
            document_types=["REGULATORY_LETTER"],
        )
        excluded = document_search_service.search_official_document_chunks(
            db,
            stock,
            query="现金流 风险",
            days=365,
            limit=10,
            document_types=["ANNOUNCEMENT"],
        )

    assert len(hits) == 1
    assert hits[0].document_id == 900002
    assert hits[0].page_number == 1
    assert hits[0].score > 0
    assert "现金流" in hits[0].excerpt
    assert hits[0].source_url.endswith("search-test.pdf")
    assert len(hits[0].text_hash) == 64
    assert excluded == []


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
        register_test_user(client)
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
                            "f124": int(
                                datetime(2026, 8, 11, 2, tzinfo=UTC).timestamp()
                            ),
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
    assert quotes["600519"]["quote_trade_date"] == date(2026, 8, 11)
    assert quotes.stats.successful_batches == 1
    assert quotes.stats.success_percent == Decimal("100.0")
    assert quotes.stats.fallback_used is False


def test_history_uses_provider_change_metrics_for_gap_bar(monkeypatch) -> None:
    """跳空低开后收涨的阳线，日涨跌仍须保留数据源给出的负值。"""
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

    rows = history_service.fetch_eastmoney_history("600519", 20)

    assert "f59" in requested_params["fields2"]
    assert "f60" in requested_params["fields2"]
    assert rows[0]["prev_close"] == Decimal("100.00")
    assert rows[0]["change_amount"] == Decimal("-5.00")
    assert rows[0]["change_percent"] == Decimal("-5.00")
    assert rows[0]["open_price"] < rows[0]["close_price"]


def test_latest_snapshot_requires_provider_trade_date() -> None:
    """请求时间不能冒充交易日，缺少源交易日时不得制造新 K 线。"""
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "000001"))
        assert stock is not None
        original_count = len(
            db.scalars(
                select(StockBar.id).where(StockBar.stock_id == stock.id)
            ).all()
        )
        stock.quote_trade_date = None
        stock.price = Decimal("9.50")
        stock.prev_close = Decimal("10.00")

        history_service.upsert_latest_stock_bar(db, stock)
        db.flush()

        assert len(
            db.scalars(
                select(StockBar.id).where(StockBar.stock_id == stock.id)
            ).all()
        ) == original_count
        db.rollback()


def test_latest_snapshot_persists_both_daily_and_candle_directions() -> None:
    """低开反弹应同时保留阳线和日跌幅，供前端分别编码。"""
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "000001"))
        assert stock is not None
        stock.quote_trade_date = date(2026, 8, 10)
        stock.prev_close = Decimal("10.00")
        stock.open_price = Decimal("9.00")
        stock.high_price = Decimal("9.60")
        stock.low_price = Decimal("8.90")
        stock.price = Decimal("9.50")

        history_service.upsert_latest_stock_bar(db, stock)
        db.flush()
        bar = db.scalar(
            select(StockBar).where(
                StockBar.stock_id == stock.id,
                StockBar.trade_date == date(2026, 8, 10),
            )
        )

        assert bar is not None
        assert bar.close_price > bar.open_price
        assert bar.change_amount == Decimal("-0.50")
        assert bar.change_percent == Decimal("-5.0000")
        db.rollback()


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
                "quote_trade_date": date(2026, 8, 11),
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
            "quote_trade_date": stock.quote_trade_date,
            "updated_at": stock.updated_at,
        }
        try:
            assert market.tick_market(db) == 1
            db.refresh(stock)
            assert stock.price == Decimal("1500.25")
            assert stock.volume == 12_345_600
            assert stock.updated_at == fetched_at
        finally:
            db.execute(
                delete(StockBar).where(
                    StockBar.stock_id == stock.id,
                    StockBar.trade_date == date(2026, 8, 11),
                )
            )
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


def test_buy_is_t1_locked_then_sellable_next_day() -> None:
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
        register_test_user(client)
        # ---- 买入 ----
        buy = client.post(
            "/api/orders",
            json={"symbol": "600519", "side": "BUY", "quantity": 100},
            headers=order_headers(),
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

        # ---- 当日卖出应被 T+1 拒绝 ----
        sell = client.post(
            "/api/orders",
            json={"symbol": "600519", "side": "SELL", "quantity": 100},
            headers=order_headers(),
        )
        assert sell.status_code == 400
        assert "T+1" in sell.json()["detail"]

        # 将买入成交移到前一交易日，模拟次日后即可卖出。
        with SessionLocal() as db:
            trade = db.scalar(select(Trade).where(Trade.side == "BUY"))
            assert trade is not None
            trade.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
            db.commit()

        position = next(
            item for item in client.get("/api/positions").json()
            if item["symbol"] == "600519"
        )
        assert position["sellable_quantity"] == 100
        sell = client.post(
            "/api/orders",
            json={"symbol": "600519", "side": "SELL", "quantity": 100},
            headers=order_headers(),
        )
        assert sell.status_code == 201, sell.text


def test_watchlist_add_list_remove() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        created = client.post("/api/watchlist/600519")
        assert created.status_code == 201
        assert created.json()["symbol"] == "600519"
        assert [item["symbol"] for item in client.get("/api/watchlist").json()] == ["600519"]
        assert client.delete("/api/watchlist/600519").status_code == 204
        assert client.get("/api/watchlist").json() == []


def test_order_preview_and_pending_limit_cancel() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        stock = client.get("/api/stocks/600519").json()
        current = Decimal(stock["price"])
        limit_price = current - Decimal("0.01")
        preview = client.post(
            "/api/orders/preview",
            json={
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100,
                "order_type": "LIMIT",
                "limit_price": str(limit_price),
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["allowed"] is True
        assert preview.json()["estimated_fee"] == "5.00"

        created = client.post(
            "/api/orders",
            json={
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100,
                "order_type": "LIMIT",
                "limit_price": str(limit_price),
            },
            headers=order_headers(),
        )
        assert created.status_code == 201, created.text
        order = created.json()
        assert order["status"] == "PENDING"
        assert order["cancelable"] is True
        canceled = client.post(f"/api/orders/{order['order_no']}/cancel")
        assert canceled.status_code == 200
        assert canceled.json()["status"] == "CANCELED"


def test_order_idempotency_returns_original_order_and_rejects_key_reuse() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        key = f"retry-{uuid4().hex}"
        payload = {"symbol": "600519", "side": "BUY", "quantity": 100}

        first = client.post(
            "/api/orders",
            json=payload,
            headers=order_headers(key),
        )
        retried = client.post(
            "/api/orders",
            json=payload,
            headers=order_headers(key),
        )

        assert first.status_code == 201, first.text
        assert retried.status_code == 201, retried.text
        assert retried.json()["order_no"] == first.json()["order_no"]
        assert len(client.get("/api/orders").json()) == 1
        assert len(client.get("/api/trades").json()) == 1
        assert client.get("/api/positions").json()[0]["quantity"] == 100

        conflicting = client.post(
            "/api/orders",
            json={**payload, "quantity": 200},
            headers=order_headers(key),
        )
        assert conflicting.status_code == 409
        assert "已经用于另一笔委托" in conflicting.json()["detail"]
        assert len(client.get("/api/orders").json()) == 1


def test_stock_history_uses_cached_bars() -> None:
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        assert stock is not None
        trade_date = date(2026, 1, 5)
        for offset in range(20):
            while trade_date.weekday() >= 5:
                trade_date += timedelta(days=1)
            price = Decimal("10") + Decimal(offset) / Decimal("10")
            change_percent = (
                Decimal("0.05") / price * Decimal("100")
            ).quantize(Decimal("0.0001"))
            db.add(
                StockBar(
                    stock_id=stock.id,
                    trade_date=trade_date,
                    open_price=price,
                    high_price=price + Decimal("0.10"),
                    low_price=price - Decimal("0.10"),
                    close_price=price + Decimal("0.05"),
                    prev_close=price,
                    change_amount=Decimal("0.05"),
                    change_percent=change_percent,
                    volume=1_000_000,
                    turnover=Decimal("0"),
                )
            )
            trade_date += timedelta(days=1)
        db.commit()

    with TestClient(app) as client:
        register_test_user(client)
        response = client.get("/api/stocks/600519/history?limit=20")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "CACHED_HISTORY"
    assert payload["cached_count"] == 20
    assert payload["target_count"] == 20
    assert payload["complete"] is True
    assert len(payload["bars"]) == 20
    assert payload["bars"][-1]["prev_close"] is not None
    assert Decimal(payload["bars"][-1]["change_percent"]) > 0


def test_history_between_20_and_49_bars_is_still_backfilled(monkeypatch) -> None:
    """缓存达到旧阈值 20 后仍须继续补到详情页需要的 50 根。"""
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "601398"))
        assert stock is not None
        db.execute(delete(StockBar).where(StockBar.stock_id == stock.id))
        for offset in range(25):
            price = Decimal("5") + Decimal(offset) / Decimal("100")
            db.add(
                StockBar(
                    stock_id=stock.id,
                    trade_date=(datetime(2025, 10, 1) + timedelta(days=offset)).date(),
                    open_price=price,
                    high_price=price + Decimal("0.10"),
                    low_price=price - Decimal("0.10"),
                    close_price=price + Decimal("0.01"),
                    volume=1_000_000,
                    turnover=Decimal("0"),
                )
            )
        db.commit()

        calls: list[str] = []

        def fake_history(symbol: str, limit: int):
            calls.append(symbol)
            return [
                {
                    "trade_date": (datetime(2025, 8, 1) + timedelta(days=offset)).date(),
                    "open_price": Decimal("5.00"),
                    "high_price": Decimal("5.10"),
                    "low_price": Decimal("4.90"),
                    "close_price": Decimal("5.05"),
                    "volume": 1_000_000,
                    "turnover": Decimal("0"),
                }
                for offset in range(60)
            ]

        monkeypatch.setattr(history_service, "fetch_eastmoney_history", fake_history)
        source, bars = history_service.stock_history(db, stock, 90)
        assert calls == ["601398"]
        assert source == "EASTMONEY_HISTORY"
        assert len(bars) >= 50


def test_background_history_failure_advances_cursor(monkeypatch) -> None:
    """单只股票补全失败后必须轮换，不能永久卡住整个队列。"""
    attempted: list[str] = []

    def fail_history(symbol: str, *_args, **_kwargs):
        attempted.append(symbol)
        raise market.MarketDataError("测试限流")

    monkeypatch.setattr(history_service, "fetch_eastmoney_history", fail_history)
    with SessionLocal() as db:
        first_cursor, first_symbol, first_complete = history_service.backfill_stock_history_once(db)
    with SessionLocal() as db:
        _second_cursor, second_symbol, second_complete = history_service.backfill_stock_history_once(db, first_cursor)

    assert first_symbol is not None
    assert second_symbol is not None
    assert first_symbol != second_symbol
    assert first_complete is False
    assert second_complete is False
    assert attempted == [first_symbol, second_symbol]


def _agent_test_bars(count: int = 61) -> list[SimpleNamespace]:
    start = date(2026, 1, 1)
    return [
        SimpleNamespace(
            trade_date=start + timedelta(days=offset),
            open_price=Decimal("10") + Decimal(offset) / Decimal("100"),
            high_price=Decimal("10.20") + Decimal(offset) / Decimal("100"),
            low_price=Decimal("9.80") + Decimal(offset) / Decimal("100"),
            close_price=Decimal("10.05") + Decimal(offset) / Decimal("100"),
            volume=1_000_000 + offset,
            turnover=Decimal("10000000") + Decimal(offset),
        )
        for offset in range(count)
    ]


def test_agent_run_persists_adopted_evidence_and_recommendation() -> None:
    """完成态必须同时保存数据截止时间、Token、证据和结构化建议。"""
    with TestClient(app) as client:
        username = register_test_user(client, "agent_audit_user")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        assert user is not None
        assert user.account is not None
        assert stock is not None
        run = start_agent_run(
            db,
            user_id=user.id,
            account_id=user.account.id,
            stock_id=stock.id,
            run_type="STOCK_ANALYSIS",
            model_provider="TEST",
            model_name="test-model",
            request_text="分析公司基本面风险",
        )
        run_id = run.id

        evidence_key = "FINANCIAL_REPORT:600519:2025-12-31"
        completed = complete_agent_run(
            db,
            run_id,
            data_as_of=datetime(2026, 8, 6, 8, 30, tzinfo=UTC),
            evidence=[
                AgentEvidenceRecord(
                    evidence_key=evidence_key,
                    tool_name="get_stock_fundamentals",
                    evidence_type="FINANCIAL_REPORT",
                    title="2025年年度报告",
                    source_url="https://www.sse.com.cn/report.pdf",
                    source_date=date(2026, 3, 20),
                    page_number=5,
                    start_char=100,
                    end_char=220,
                    excerpt="营业收入与归母净利润保持增长。",
                    content_hash="a" * 64,
                    trust_level="UNTRUSTED_SOURCE_CONTENT",
                    metadata={"report_period": "2025-12-31"},
                )
            ],
            recommendation=AgentRecommendationRecord(
                risk_level="MEDIUM",
                confidence=Decimal("0.7200"),
                action="HOLD",
                time_horizon="中期",
                summary="基本面稳定，但仍需跟踪后续经营变化。",
                reasoning="结论仅依据已保存的年度报告证据形成。",
                positive_factors=["收入与利润保持增长"],
                risk_factors=["单一报告期不能代表未来表现"],
                action_conditions=["继续跟踪下一报告期"],
                invalidation_conditions=["后续出现重大会计差错"],
                evidence_keys=[evidence_key],
            ),
            token_usage=AgentTokenUsage(
                input_tokens=1200,
                output_tokens=300,
                total_tokens=1500,
            ),
        )

        assert completed.status == "COMPLETED"
        assert completed.data_as_of == datetime(2026, 8, 6, 8, 30)
        assert completed.total_tokens == 1500
        saved_evidence = db.scalar(
            select(AgentEvidence).where(AgentEvidence.run_id == run_id)
        )
        saved_recommendation = db.scalar(
            select(AgentRecommendation).where(
                AgentRecommendation.run_id == run_id
            )
        )
        assert saved_evidence is not None
        assert saved_evidence.tool_name == "get_stock_fundamentals"
        assert saved_evidence.details["report_period"] == "2025-12-31"
        assert saved_recommendation is not None
        assert saved_recommendation.evidence_keys == [evidence_key]
        assert len(saved_recommendation.output_hash) == 64
        assert "不构成投资建议" in saved_recommendation.disclaimer
        assert db.scalars(select(Order)).all() == []
        assert db.scalars(select(Trade)).all() == []


def test_agent_run_rejects_cross_account_and_fake_evidence_links() -> None:
    """账户归属和建议证据引用都必须由服务端校验。"""
    with TestClient(app) as first_client:
        first_username = register_test_user(first_client, "agent_owner_one")
    with TestClient(app) as second_client:
        second_username = register_test_user(second_client, "agent_owner_two")

    with SessionLocal() as db:
        first_user = db.scalar(select(User).where(User.username == first_username))
        second_user = db.scalar(select(User).where(User.username == second_username))
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        assert first_user is not None and first_user.account is not None
        assert second_user is not None and second_user.account is not None
        assert stock is not None

        with pytest.raises(AgentPersistenceError, match="不属于当前用户"):
            start_agent_run(
                db,
                user_id=first_user.id,
                account_id=second_user.account.id,
                stock_id=stock.id,
                model_provider="TEST",
                model_name="test-model",
            )

        run = start_agent_run(
            db,
            user_id=first_user.id,
            account_id=first_user.account.id,
            stock_id=stock.id,
            model_provider="TEST",
            model_name="test-model",
        )
        evidence = AgentEvidenceRecord(
            evidence_key="real-evidence",
            tool_name="search_company_announcements",
            evidence_type="ANNOUNCEMENT",
            source_url="https://www.sse.com.cn/announcement.pdf",
            excerpt="正式公告证据片段。",
            trust_level="UNTRUSTED_SOURCE_CONTENT",
        )
        recommendation = AgentRecommendationRecord(
            risk_level="UNKNOWN",
            confidence=Decimal("0.2000"),
            action="WATCH",
            summary="证据仍然不足。",
            reasoning="需要更多正式披露才能形成结论。",
            evidence_keys=["fabricated-evidence"],
        )
        with pytest.raises(AgentPersistenceError, match="建议引用"):
            complete_agent_run(
                db,
                run.id,
                data_as_of=datetime.now(UTC),
                evidence=[evidence],
                recommendation=recommendation,
            )

        failed = fail_agent_run(
            db,
            run.id,
            error_code="EVIDENCE_VALIDATION_FAILED",
            error_message="模型返回了不存在的证据引用",
            token_usage=AgentTokenUsage(input_tokens=500, output_tokens=50),
        )
        assert failed.status == "FAILED"
        assert failed.total_tokens == 550
        assert failed.error_code == "EVIDENCE_VALIDATION_FAILED"
        assert db.scalars(
            select(AgentEvidence).where(AgentEvidence.run_id == run.id)
        ).all() == []
        assert db.scalar(
            select(AgentRecommendation).where(AgentRecommendation.run_id == run.id)
        ) is None


def test_stock_analysis_orchestration_calls_all_tools_and_persists(monkeypatch) -> None:
    """单股分析固定调用八个工具，只保存模型实际引用的服务端证据。"""
    called: list[tuple[str, dict]] = []
    now = datetime.now(UTC)

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

        def invoke(self, payload: dict):
            called.append((self.name, payload))
            return {
                "tool": self.name,
                "status": "OK",
                "as_of": now.isoformat(),
                "data": {
                    "symbol": "600519",
                    "source_tool": self.name,
                    "test_value": 1,
                },
                "evidence": [
                    {
                        "evidence_type": "TEST_EVIDENCE",
                        "title": f"{self.name}测试证据",
                        "source_url": f"https://example.com/{self.name}",
                        "document_date": "2026-08-06",
                        "excerpt": f"来自{self.name}的测试证据片段。",
                    }
                ],
                "warnings": [],
            }

    fake_tools = [FakeTool(name) for name in READONLY_TOOL_NAMES]

    def fake_model(prompt_value):
        prompt_text = prompt_value.to_string()
        keys = list(
            dict.fromkeys(
                re.findall(r'"evidence_key":"([^"]+)"', prompt_text)
            )
        )
        assert len(keys) >= 2
        return {
            "raw": AIMessage(
                content="",
                usage_metadata={
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "total_tokens": 1200,
                },
            ),
            "parsed": agent_orchestrator.StockAnalysisDraft(
                risk_level="MEDIUM",
                confidence=Decimal("0.6800"),
                action="WATCH",
                time_horizon="中期",
                summary="当前适合继续观察并跟踪公开证据。",
                reasoning="行情、基本面、官方披露和组合风险需要结合判断。",
                positive_factors=["存在可核验的公开信息"],
                risk_factors=["部分结论仍受数据时效限制"],
                action_conditions=["跟踪后续公告和财报"],
                invalidation_conditions=["出现新的重大风险披露"],
                evidence_keys=keys[:2],
                disclaimer="仅供模拟交易学习，不构成投资建议。",
            ),
            "parsing_error": None,
        }

    runtime = agent_orchestrator.AgentModelRuntime(
        provider="TEST",
        model_name="fake-structured-model",
        runnable=RunnableLambda(fake_model),
    )
    monkeypatch.setattr(
        agent_orchestrator,
        "configured_model_runtime",
        lambda: runtime,
    )
    monkeypatch.setattr(
        agent_orchestrator,
        "build_readonly_tools",
        lambda **_kwargs: fake_tools,
    )

    with TestClient(app) as client:
        register_test_user(client, "stock_agent_user")
        response = client.post(
            "/api/agent/stocks/600519/analyze",
            json={
                "question": "请分析主要风险",
                "focus_keywords": "风险 业绩 监管",
                "history_days": 60,
                "disclosure_days": 365,
                "news_days": 30,
                "max_documents": 1,
            },
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        run_id = payload["id"]
        assert payload["status"] == "COMPLETED"
        assert payload["recommendation"]["action"] == "WATCH"
        assert payload["total_tokens"] == 1200
        assert len(payload["evidence"]) == 2
        assert payload["recommendation"]["evidence_keys"] == [
            item["evidence_key"] for item in payload["evidence"]
        ]

        stored = client.get(f"/api/agent/runs/{run_id}")
        assert stored.status_code == 200
        assert stored.json()["recommendation"]["output_hash"]

    assert [name for name, _payload in called] == list(READONLY_TOOL_NAMES)
    assert called[0][1] == {}
    assert called[1][1] == {"symbol": "600519"}
    assert called[-1][1] == {"history_days": 60}
    with SessionLocal() as db:
        assert db.scalars(select(Order)).all() == []
        assert db.scalars(select(Trade)).all() == []

    with TestClient(app) as other_client:
        register_test_user(other_client, "stock_agent_other")
        hidden = other_client.get(f"/api/agent/runs/{run_id}")
        assert hidden.status_code == 404


@pytest.mark.parametrize("retry_succeeds", [True, False])
def test_stock_analysis_retries_truncated_structured_output_once(
    monkeypatch,
    retry_succeeds: bool,
) -> None:
    """输出被截断时复用工具结果并精简重试，失败时保留准确诊断。"""
    now = datetime.now(UTC)
    tool_calls: list[str] = []
    prompt_lengths: list[int] = []
    model_attempts = 0

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

        def invoke(self, _payload: dict):
            tool_calls.append(self.name)
            return {
                "tool": self.name,
                "status": "OK",
                "as_of": now.isoformat(),
                "data": {
                    "symbol": "600519",
                    "source_tool": self.name,
                    "details": "数" * 6000,
                },
                "evidence": [
                    {
                        "evidence_type": "TEST_EVIDENCE",
                        "title": f"{self.name}测试证据",
                        "source_url": f"https://example.com/{self.name}",
                        "document_date": "2026-08-06",
                        "excerpt": "证" * 1200,
                    }
                ],
                "warnings": [],
            }

    def fake_model(prompt_value):
        nonlocal model_attempts
        model_attempts += 1
        prompt_text = prompt_value.to_string()
        prompt_lengths.append(len(prompt_text))
        if model_attempts == 1 or not retry_succeeds:
            return {
                "raw": AIMessage(
                    content="",
                    response_metadata={"finish_reason": "length"},
                    usage_metadata={
                        "input_tokens": 1000 if model_attempts == 1 else 700,
                        "output_tokens": 4096,
                        "total_tokens": 5096 if model_attempts == 1 else 4796,
                    },
                ),
                "parsed": None,
                "parsing_error": None,
            }

        keys = list(
            dict.fromkeys(
                re.findall(r'"evidence_key":"([^"]+)"', prompt_text)
            )
        )
        return {
            "raw": AIMessage(
                content="",
                response_metadata={"finish_reason": "tool_calls"},
                usage_metadata={
                    "input_tokens": 700,
                    "output_tokens": 300,
                    "total_tokens": 1000,
                },
            ),
            "parsed": agent_orchestrator.StockAnalysisDraft(
                risk_level="MEDIUM",
                confidence=Decimal("0.6500"),
                action="WATCH",
                time_horizon="中期",
                summary="精简重试后完成。",
                reasoning="基于工具证据继续观察。",
                positive_factors=["存在公开证据"],
                risk_factors=["数据存在时效限制"],
                action_conditions=["跟踪后续披露"],
                invalidation_conditions=["出现重大新增风险"],
                evidence_keys=keys[:1],
                disclaimer="仅供模拟交易学习，不构成投资建议。",
            ),
            "parsing_error": None,
        }

    fake_tools = [FakeTool(name) for name in READONLY_TOOL_NAMES]
    runtime = agent_orchestrator.AgentModelRuntime(
        provider="TEST",
        model_name="fake-truncating-model",
        runnable=RunnableLambda(fake_model),
        max_output_tokens=4096,
    )
    monkeypatch.setattr(
        agent_orchestrator,
        "configured_model_runtime",
        lambda: runtime,
    )
    monkeypatch.setattr(
        agent_orchestrator,
        "build_readonly_tools",
        lambda **_kwargs: fake_tools,
    )

    username = "agent_retry_success" if retry_succeeds else "agent_retry_failure"
    with TestClient(app) as client:
        register_test_user(client, username)
        response = client.post("/api/agent/stocks/600519/analyze", json={})
        if retry_succeeds:
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "COMPLETED"
            assert response.json()["total_tokens"] == 6096
        else:
            assert response.status_code == 502, response.text
            detail = response.json()["detail"]
            assert detail["code"] == "MODEL_OUTPUT_TRUNCATED"
            stored = client.get(f"/api/agent/runs/{detail['run_id']}")
            assert stored.status_code == 200
            payload = stored.json()
            assert payload["error_code"] == "MODEL_OUTPUT_TRUNCATED"
            assert "finish_reason=length" in payload["error_message"]
            assert "retried=True" in payload["error_message"]
            assert payload["total_tokens"] == 9892

    assert model_attempts == 2
    assert tool_calls == list(READONLY_TOOL_NAMES)
    assert prompt_lengths[1] < prompt_lengths[0]


def test_agent_evidence_excerpts_respect_persistence_limit() -> None:
    """快照和工具原始片段截断后都不得超过证据表的 8000 字符限制。"""
    oversized_data = "数" * 9000
    oversized_excerpt = "证" * 9000
    candidates = agent_orchestrator.evidence_candidates(
        [
            {
                "tool": "get_stock_fundamentals",
                "status": "OK",
                "as_of": "2026-08-07T03:00:00Z",
                "data": {"oversized": oversized_data},
                "evidence": [
                    {
                        "evidence_type": "FINANCIAL_REPORT",
                        "title": "超长证据边界测试",
                        "excerpt": oversized_excerpt,
                        "source_url": "https://example.com/report.pdf",
                    }
                ],
                "warnings": [],
            }
        ]
    )

    assert len(candidates) == 2
    for candidate in candidates.values():
        assert len(candidate.excerpt) == 8000
        assert candidate.excerpt.endswith("…")


def test_agent_truncate_keeps_requested_maximum_length() -> None:
    """省略号必须包含在指定长度内，极小长度也要保持安全。"""
    assert agent_orchestrator._truncate("abc", 0) == ""
    assert agent_orchestrator._truncate("abc", 1) == "…"
    assert agent_orchestrator._truncate("abc", 2) == "a…"
    assert agent_orchestrator._truncate("ab", 2) == "ab"


def test_stock_analysis_records_model_configuration_failure(monkeypatch) -> None:
    """模型未配置时保留失败记录，而且不会浪费八个工具调用。"""
    tool_builder_called = False

    def missing_model():
        raise agent_orchestrator.AgentModelConfigurationError("测试未配置模型")

    def unexpected_tool_builder(**_kwargs):
        nonlocal tool_builder_called
        tool_builder_called = True
        return []

    monkeypatch.setattr(
        agent_orchestrator,
        "configured_model_runtime",
        missing_model,
    )
    monkeypatch.setattr(
        agent_orchestrator,
        "build_readonly_tools",
        unexpected_tool_builder,
    )

    with TestClient(app) as client:
        register_test_user(client, "agent_no_model")
        response = client.post(
            "/api/agent/stocks/600519/analyze",
            json={},
        )
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["code"] == "MODEL_NOT_CONFIGURED"
        assert detail["run_id"] is not None
        stored = client.get(f"/api/agent/runs/{detail['run_id']}")
        assert stored.status_code == 200
        assert stored.json()["status"] == "FAILED"
        assert stored.json()["error_code"] == "MODEL_NOT_CONFIGURED"
        assert stored.json()["recommendation"] is None

    assert tool_builder_called is False


def test_deepseek_model_uses_key_alias_and_compatible_token_field(monkeypatch) -> None:
    """DeepSeek 可读取常用密钥名，并通过 extra_body 发送 max_tokens。"""
    captured: dict = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def with_structured_output(self, schema, **kwargs):
            captured["schema"] = schema
            captured["structured"] = kwargs
            return RunnableLambda(lambda value: value)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("MONIPAN_AGENT_MODEL_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("MONIPAN_AGENT_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("MONIPAN_AGENT_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MONIPAN_AGENT_MAX_OUTPUT_TOKENS", "2100")
    monkeypatch.delenv("MONIPAN_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")

    runtime = agent_orchestrator.configured_model_runtime()

    assert runtime.provider == "DEEPSEEK"
    assert runtime.model_name == "deepseek-v4-flash"
    assert runtime.max_output_tokens == 2100
    assert captured["api_key"] == "test-deepseek-key"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["extra_body"] == {
        "max_tokens": 2100,
        "thinking": {"type": "disabled"},
    }
    assert "max_completion_tokens" not in captured
    assert captured["structured"]["method"] == "function_calling"


def test_stock_news_search_is_live_bounded_and_source_backed(monkeypatch) -> None:
    """新闻按需搜索、不保存全文，并过滤重复或明显无关的结果。"""
    calls: list[dict] = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "request_id": "news-request-1",
                "results": [
                    {
                        "title": "贵州茅台发布经营动态",
                        "url": "https://news.example.com/maotai",
                        "published_date": "2026-08-05T08:00:00Z",
                        "content": "贵州茅台披露近期经营情况。",
                        "score": 0.95,
                    },
                    {
                        "title": "贵州茅台重复结果",
                        "url": "https://news.example.com/maotai",
                        "content": "贵州茅台重复链接。",
                    },
                    {
                        "title": "其他公司新闻",
                        "url": "https://news.example.com/unrelated",
                        "content": "与目标股票无关。",
                    },
                ],
            }

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(news_service.requests, "post", fake_post)
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        assert stock is not None
        result = news_service.search_stock_news(
            stock,
            query="经营风险",
            days=30,
            limit=5,
        )

    assert result.provider == "TAVILY"
    assert result.request_id == "news-request-1"
    assert len(result.results) == 1
    assert result.results[0]["source_url"].endswith("/maotai")
    assert calls[0]["url"] == news_service.TAVILY_SEARCH_URL
    assert calls[0]["json"]["topic"] == "news"
    assert calls[0]["json"]["max_results"] == 5
    assert calls[0]["json"]["include_raw_content"] is False


def test_langchain_readonly_tool_registry_and_stock_tools(monkeypatch) -> None:
    """八个工具都有受控参数、结构化结果，且无账户时不能读取持仓。"""
    now = datetime.now(UTC)
    bars = _agent_test_bars()

    monkeypatch.setattr(
        agent_tools,
        "stock_history",
        lambda _db, _stock, _limit: ("TEST_HISTORY", bars),
    )
    monkeypatch.setattr(
        agent_tools,
        "fundamentals_for_stock",
        lambda _db, stock: {
            "symbol": stock.symbol,
            "stock_name": stock.name,
            "cache_status": "FRESH",
            "fetched_at": now,
            "reports": [
                {
                    "report_name": "2025年年度报告",
                    "announcement_date": date(2026, 3, 20),
                    "report_period": date(2025, 12, 31),
                    "source_url": "https://www.sse.com.cn/report.pdf",
                }
            ],
            "events": [],
        },
    )

    def fake_evidence(*_args, **_kwargs):
        return EvidenceSearchResult(
            status="OK",
            as_of=now,
            data={"evidence_count": 1},
            evidence=[
                {
                    "source_url": "https://www.sse.com.cn/document.pdf",
                    "document_date": date(2026, 3, 20),
                    "page_number": 2,
                    "excerpt": "测试证据片段",
                    "trust": "UNTRUSTED_SOURCE_CONTENT",
                }
            ],
            warnings=[],
        )

    monkeypatch.setattr(
        agent_tools,
        "search_company_announcement_evidence",
        fake_evidence,
    )
    monkeypatch.setattr(
        agent_tools,
        "search_regulatory_evidence",
        fake_evidence,
    )
    monkeypatch.setattr(
        agent_tools,
        "search_stock_news_values",
        lambda stock, **_kwargs: NewsSearchResult(
            provider="TEST_NEWS",
            query=f"{stock.name} 测试新闻",
            searched_at=now,
            request_id="test-request",
            results=[
                {
                    "title": f"{stock.name}发布测试动态",
                    "source_url": "https://news.example.com/story",
                    "published_at": "2026-03-20T08:00:00Z",
                    "excerpt": f"{stock.name}测试新闻摘要",
                    "score": 0.9,
                }
            ],
        ),
    )

    tools = build_readonly_tools()
    assert tuple(tool.name for tool in tools) == READONLY_TOOL_NAMES
    tool_map = {tool.name: tool for tool in tools}
    for tool in tools:
        assert "account_id" not in tool.args_schema.model_fields

    portfolio = tool_map["get_portfolio"].invoke({})
    quote = tool_map["get_stock_quote"].invoke({"symbol": "600519"})
    history = tool_map["get_stock_history"].invoke(
        {"symbol": "600519", "limit": 60}
    )
    fundamentals = tool_map["get_stock_fundamentals"].invoke(
        {"symbol": "600519"}
    )
    announcements = tool_map["search_company_announcements"].invoke(
        {"symbol": "600519", "query": "年度报告", "max_documents": 1}
    )
    regulatory = tool_map["search_regulatory_letters"].invoke(
        {"symbol": "600519", "query": "问询", "max_documents": 1}
    )
    news = tool_map["search_stock_news"].invoke(
        {"symbol": "600519", "query": "减持", "days": 30, "limit": 5}
    )
    risk = tool_map["calculate_portfolio_risk"].invoke({"history_days": 60})

    results = (
        portfolio,
        quote,
        history,
        fundamentals,
        announcements,
        regulatory,
        news,
        risk,
    )
    assert quote["data"]["symbol"] == "600519"
    assert history["data"]["source"] == "TEST_HISTORY"
    assert fundamentals["evidence"][0]["source_url"].endswith("report.pdf")
    assert announcements["evidence"][0]["page_number"] == 2
    assert regulatory["evidence"][0]["trust"] == "UNTRUSTED_SOURCE_CONTENT"
    assert news["evidence"][0]["trust"] == "UNTRUSTED_WEB_CONTENT"
    assert portfolio["status"] == "FORBIDDEN"
    assert risk["status"] == "FORBIDDEN"
    for result in results:
        assert set(result) == {
            "tool",
            "status",
            "as_of",
            "data",
            "evidence",
            "warnings",
        }


def test_langchain_portfolio_tools_are_account_bound_and_readonly(monkeypatch) -> None:
    """账户由服务端绑定，风险计算只读且不创建委托或成交。"""
    bars = _agent_test_bars()
    monkeypatch.setattr(
        agent_portfolio,
        "stock_history",
        lambda _db, _stock, _limit: ("TEST_HISTORY", bars),
    )

    with TestClient(app) as client:
        username = register_test_user(client, "agent_tool_user")

    with SessionLocal() as db:
        account = db.scalar(
            select(SimulationAccount)
            .join(User)
            .where(User.username == username)
        )
        stock = db.scalar(select(Stock).where(Stock.symbol == "600519"))
        assert account is not None
        assert stock is not None
        account.initial_cash = Decimal("20000")
        account.available_cash = Decimal("5000")
        db.add(
            Position(
                account_id=account.id,
                stock_id=stock.id,
                quantity=1000,
                average_cost=Decimal("9.50"),
            )
        )
        db.commit()
        account_id = account.id

    tool_map = {
        tool.name: tool for tool in build_readonly_tools(account_id=account_id)
    }
    portfolio = tool_map["get_portfolio"].invoke({})
    risk = tool_map["calculate_portfolio_risk"].invoke({"history_days": 60})

    assert portfolio["status"] == "OK"
    assert portfolio["data"]["username"] == username
    assert portfolio["data"]["position_count"] == 1
    assert portfolio["data"]["positions"][0]["symbol"] == "600519"
    assert risk["status"] == "OK"
    assert risk["data"]["common_return_days"] >= 20
    assert risk["data"]["annualized_volatility_percent"] is not None
    assert risk["data"]["methodology"]["volatility"].endswith("252")

    with SessionLocal() as db:
        assert db.scalars(select(Order)).all() == []
        assert db.scalars(select(Trade)).all() == []


def test_reject_invalid_lot() -> None:
    """
    测试非整百股委托被正确拒绝。

    A 股交易规则要求买卖数量必须是 100 股（1 手）的整数倍。
    发送 quantity=1（1 股）的买入委托，期望返回 422 Unprocessable Entity。

    422 状态码表示请求体格式正确但业务校验失败（Pydantic validator 抛出异常）。
    """
    with TestClient(app) as client:
        register_test_user(client)
        response = client.post(
            "/api/orders",
            json={"symbol": "600519", "side": "BUY", "quantity": 1},
            headers=order_headers(),
        )
        # 期望返回 422（Pydantic 校验失败）
        assert response.status_code == 422

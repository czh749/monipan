"""
FastAPI 应用入口模块。

本模块是 MoniPan 后端的启动入口，负责：
    1. 创建 FastAPI 应用实例
    2. 配置 CORS 跨域中间件
    3. 注册 API 路由
    4. 管理应用生命周期（启动/关闭）
    5. 启动后台行情循环任务

启动方式：
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
    或
    uvicorn app.main:app --reload --port 8000  （开发模式，热重载）

Docker 部署：
    CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

生命周期流程：
    启动阶段（yield 之前）：
        1. 自动创建数据库表（如果不存在）
        2. 初始化市场种子数据（200 只股票 + 5 个指数）
        3. 启动后台行情循环任务

    关闭阶段（yield 之后）：
        1. 取消后台行情任务
        2. 等待任务优雅退出
"""

import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .api import router
from .database import Base, SessionLocal, engine, migrate_database
from .runtime_config import validate_runtime_configuration
from .seed import seed_database
from .services.market import (
    MarketDataError,
    backfill_stock_history_once,
    is_a_share_session,
    market_refresh_interval,
    tick_market,
    tick_market_indices,
)


logger = logging.getLogger("uvicorn.error")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
QUIET_REQUEST_PATHS = frozenset({"/health", "/livez", "/readyz"})
INDEX_REFRESH_COOLDOWN_SECONDS = 60
HISTORY_BACKFILL_INITIAL_DELAY_SECONDS = int(
    os.getenv("MONIPAN_HISTORY_INITIAL_DELAY", "90")
)
HISTORY_BACKFILL_SUCCESS_INTERVAL_SECONDS = float(
    os.getenv("MONIPAN_HISTORY_INTERVAL", "4")
)
HISTORY_BACKFILL_FAILURE_INTERVAL_SECONDS = float(
    os.getenv("MONIPAN_HISTORY_FAILURE_INTERVAL", "20")
)
HISTORY_BACKFILL_IDLE_INTERVAL_SECONDS = float(
    os.getenv("MONIPAN_HISTORY_IDLE_INTERVAL", "900")
)


def refresh_market_once() -> tuple[int, int]:
    """在线程中创建独立数据库会话并执行一次行情刷新。"""
    with SessionLocal() as db:
        # 股票池是交易与资产计算的核心，必须占用本轮第一个数据源请求。
        stock_updated = tick_market(db)
        index_updated = 0
        # 公开接口短时间连续请求会主动断开。股票成功后冷却，再单独更新指数。
        time.sleep(INDEX_REFRESH_COOLDOWN_SECONDS)
        try:
            index_updated = tick_market_indices(db)
        except MarketDataError as exc:
            logger.warning("大盘指数刷新失败，保留旧数据：%s", exc)
        return stock_updated, index_updated


# ---------------------------------------------------------------------------
# 后台行情循环
# ---------------------------------------------------------------------------

async def market_loop() -> None:
    """
    后台行情刷新循环。

    东财请求放到工作线程，避免阻塞 FastAPI。应用启动时获取一次快照，
    此后只在交易时段按配置刷新。
    """
    interval = market_refresh_interval()
    first_refresh = True
    consecutive_failures = 0

    while True:
        session_open = is_a_share_session()
        if first_refresh or session_open:
            try:
                stock_updated, index_updated = await asyncio.to_thread(
                    refresh_market_once
                )
                logger.info(
                    "行情刷新完成：provider=eastmoney stocks=%s indices=%s",
                    stock_updated,
                    index_updated,
                )
                consecutive_failures = 0
            except MarketDataError as exc:
                # 不清空数据库，继续向前端提供最后一次成功获取的行情。
                consecutive_failures += 1
                logger.warning("行情刷新失败，保留旧数据：%s", exc)
            except Exception:
                consecutive_failures += 1
                logger.exception("行情刷新出现未预期错误，保留旧数据")

        first_refresh = False
        if consecutive_failures:
            # 整轮失败时指数退避，避免频繁重试导致数据源封禁时间延长。
            sleep_seconds = min(
                300, interval * (2 ** min(consecutive_failures, 3))
            )
        else:
            sleep_seconds = interval if session_open else 60
        await asyncio.sleep(sleep_seconds)


def backfill_history_once(after_stock_id: int) -> tuple[int, str | None, bool]:
    """Create an isolated session for one background history-cache step."""
    with SessionLocal() as db:
        return backfill_stock_history_once(db, after_stock_id)


async def history_backfill_loop() -> None:
    """Gradually warm 50-day K-line caches without creating a request burst."""
    await asyncio.sleep(HISTORY_BACKFILL_INITIAL_DELAY_SECONDS)
    cursor = 0
    while True:
        try:
            cursor, symbol, complete = await asyncio.to_thread(
                backfill_history_once,
                cursor,
            )
        except Exception:
            logger.exception("历史行情后台补全出现未预期错误")
            await asyncio.sleep(HISTORY_BACKFILL_FAILURE_INTERVAL_SECONDS)
            continue

        if symbol is None:
            cursor = 0
            await asyncio.sleep(HISTORY_BACKFILL_IDLE_INTERVAL_SECONDS)
        elif complete:
            await asyncio.sleep(HISTORY_BACKFILL_SUCCESS_INTERVAL_SECONDS)
        else:
            # 当前股票失败后游标仍会前进；冷却后尝试下一只，避免队头阻塞。
            await asyncio.sleep(HISTORY_BACKFILL_FAILURE_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# 应用生命周期
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    FastAPI 应用生命周期管理器。

    使用 asynccontextmanager 装饰器，通过 yield 分隔启动和关闭逻辑：
        - yield 之前的代码在应用启动时执行
        - yield 之后的代码在应用关闭时执行

    替代了旧版 FastAPI 的 @app.on_event("startup") / @app.on_event("shutdown") 写法。
    lifespan 的优势：
        - 启动和关闭逻辑在同一个函数中，逻辑更内聚
        - 支持 async/await，可以执行异步初始化操作
        - 异常处理更清晰
    """
    # ============ 启动阶段 ============

    # 0. 显式生产模式下拒绝开发用数据库密码和非 Secure Cookie。
    runtime = validate_runtime_configuration()
    logger.info("运行配置校验完成：environment=%s", runtime.environment)

    # 1. 自动创建数据库表
    #    create_all 只会创建不存在的表，已存在的表不受影响
    #    bind=engine 指定使用哪个数据库引擎
    Base.metadata.create_all(bind=engine)
    migrate_database()

    # 2. 初始化种子数据
    #    幂等操作：保留已有数据，并按股票代码补齐到 200 只
    with SessionLocal() as db:
        seed_database(db)

    # 3. 启动后台行情循环
    #    asyncio.create_task 创建一个异步任务在后台运行
    #    任务会持续运行直到被取消
    background_enabled = os.getenv("MONIPAN_DISABLE_MARKET_LOOP") != "1"
    market_task = asyncio.create_task(market_loop()) if background_enabled else None
    history_task = (
        asyncio.create_task(history_backfill_loop()) if background_enabled else None
    )

    # ------------ 应用正常运行期间 ------------
    yield
    # ------------ 应用开始关闭 ------------

    # ============ 关闭阶段 ============

    # 1. 取消后台行情任务
    for task in (market_task, history_task):
        if task:
            task.cancel()

    # 2. 等待任务优雅退出
    #    suppress(asyncio.CancelledError): 忽略取消异常（正常关闭行为）
    #    await task: 等待任务真正结束
    for task in (market_task, history_task):
        if task:
            with suppress(asyncio.CancelledError):
                await task


# ---------------------------------------------------------------------------
# FastAPI 应用实例
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MoniPan A股模拟盘 API",
    description="固定200只A股、真实行情与模拟成交的学习型交易系统。",
    version="0.2.0",
    lifespan=lifespan,  # 绑定生命周期管理器
)


def request_id_for(request: Request) -> str:
    """Reuse a well-formed edge request ID, otherwise create a local one."""

    candidate = request.headers.get("x-request-id", "").strip()
    if REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


@app.middleware("http")
async def add_request_observability(request: Request, call_next):
    """Attach a traceable request ID and emit a compact completion log."""

    request_id = request_id_for(request)
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "请求处理失败：request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        raise

    response.headers["X-Request-ID"] = request_id
    if request.url.path in QUIET_REQUEST_PATHS:
        response.headers["Cache-Control"] = "no-store"
    if request.url.path not in QUIET_REQUEST_PATHS:
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "请求完成：request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
    return response

# ---------------------------------------------------------------------------
# CORS 跨域配置
# ---------------------------------------------------------------------------
# 允许前端开发服务器（Vite，默认端口 5173）跨域访问后端 API。
# 在生产环境中（Docker 部署），Nginx 统一代理，不存在跨域问题。
app.add_middleware(
    CORSMiddleware,
    # 允许的源：本地 Vite 开发服务器
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    # 允许携带凭证（Cookie、Authorization 头等）
    allow_credentials=True,
    # 允许所有 HTTP 方法（GET、POST、PUT、DELETE 等）
    allow_methods=["*"],
    # 允许所有请求头
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 注册路由
# ---------------------------------------------------------------------------
# 将 api.py 中定义的所有端点注册到应用上
# 端点的完整路径 = router.prefix + 端点路径
# 例如：prefix="/api" + @router.get("/stocks") → GET /api/stocks
app.include_router(router)


# ---------------------------------------------------------------------------
# 健康检查端点
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    """
    健康检查端点。

    保留此端点供现有外部探针兼容使用。Docker Compose 的 backend
    healthcheck 使用 /readyz；frontend 则只检查自己的静态页面。

    返回简单的 {"status": "ok"}，不依赖数据库（最轻量的检查）。
    """
    return {"status": "ok"}


@app.get("/livez", include_in_schema=False)
def liveness() -> dict[str, str]:
    """Process liveness probe; intentionally does not contact dependencies."""

    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False, response_model=None)
def readiness():
    """Readiness probe that verifies the core database can answer a query."""

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError as exc:
        logger.warning(
            "就绪检查失败：database=unavailable error_type=%s",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "unavailable"},
        )
    return {"status": "ok", "database": "ok"}

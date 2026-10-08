"""
RESTful API 路由层。

本模块定义了 MoniPan 模拟盘系统的全部 HTTP API 端点（路由前缀：/api）。

端点一览：
    股票行情
        GET  /api/stocks           — 股票列表（支持关键词和行业筛选）
        GET  /api/stocks/{symbol}  — 单只股票详情
        GET  /api/market/indices   — 五个主要 A 股指数
        GET  /api/market/status    — 行情源、更新时间与新鲜度状态

    账户与持仓
        GET  /api/account          — 账户概览（总资产、盈亏、收益率）
        GET  /api/positions        — 持仓列表（含浮动盈亏）

    交易
        POST /api/orders           — 创建订单（市价买入/卖出）
        GET  /api/orders           — 订单列表（最近 100 条）
        GET  /api/trades           — 成交记录（最近 100 条）

依赖注入：
    所有需要数据库的端点都通过 Depends(get_db) 注入 Session，
    由 FastAPI 的依赖注入系统管理会话生命周期（请求结束自动关闭）。
"""

from datetime import date
from decimal import Decimal
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from .auth import (
    clear_session_cookie,
    get_current_account,
    get_current_session,
    get_current_user,
    issue_session,
    register_user,
    revoke_session,
    set_session_cookie,
    user_values,
    verify_credentials,
)
from .database import get_db
from .models import AuthSession, DailyReviewNote, MarketIndex, Order, Position, SimulationAccount, Stock, Trade, TradeNote, User, WatchlistItem
from .rate_limit import (
    acquire_analysis_lease,
    clear_fixed_window,
    client_ip,
    enforce_fixed_window,
    release_analysis_lease,
)
from .agent.orchestrator import (
    AgentOrchestrationError,
    StockAnalysisOptions,
    agent_run_values,
    analyze_stock,
    load_agent_run,
)
from .schemas import (
    AccountOut,
    DailySnapshotOut,
    DayReviewOut,
    AgentRunOut,
    CurrentUserOut,
    LoginIn,
    MarketIndexOut,
    MarketStatusOut,
    OrderCreate,
    OrderOut,
    OrderPreviewOut,
    PositionOut,
    RegisterIn,
    ReviewNoteIn,
    ReviewNoteOut,
    StockAnalysisCreate,
    StockHistoryOut,
    StockAnnouncementsOut,
    StockFundamentalsOut,
    StockRegulatoryLettersOut,
    StockOut,
    TradeOut,
    WatchlistItemOut,
)
from .services.announcements import AnnouncementDataError, announcements_for_stock
from .services.fundamentals import FundamentalsDataError, fundamentals_for_stock
from .services.regulatory import RegulatoryDataError, regulatory_letters_for_stock
from .services.market import (
    HISTORY_CACHE_TARGET_BARS,
    market_index_values,
    market_status_values,
    stock_history,
    stock_values,
)
from .services.trading import (
    build_order_preview,
    cancel_order,
    place_order,
    position_sellable_values,
)
from .services.review import cents, day_review, last_completed_date, local_date, rebuild_daily_snapshots, snapshot_values


# ---------------------------------------------------------------------------
# 路由实例
# ---------------------------------------------------------------------------
# 所有端点自动添加 /api 前缀
router = APIRouter(prefix="/api")

REGISTER_IP_LIMIT = max(1, int(os.getenv("MONIPAN_REGISTER_IP_LIMIT", "3")))
REGISTER_WINDOW_SECONDS = max(
    60,
    int(os.getenv("MONIPAN_REGISTER_WINDOW_SECONDS", "3600")),
)
LOGIN_IP_LIMIT = max(1, int(os.getenv("MONIPAN_LOGIN_IP_LIMIT", "10")))
LOGIN_IP_WINDOW_SECONDS = max(
    60,
    int(os.getenv("MONIPAN_LOGIN_IP_WINDOW_SECONDS", "60")),
)
LOGIN_FAILURE_LIMIT = max(
    1,
    int(os.getenv("MONIPAN_LOGIN_FAILURE_LIMIT", "5")),
)
LOGIN_FAILURE_WINDOW_SECONDS = max(
    60,
    int(os.getenv("MONIPAN_LOGIN_FAILURE_WINDOW_SECONDS", "900")),
)
AI_USER_24H_LIMIT = max(1, int(os.getenv("MONIPAN_AI_USER_24H_LIMIT", "5")))
AI_IP_HOURLY_LIMIT = max(1, int(os.getenv("MONIPAN_AI_IP_HOURLY_LIMIT", "20")))
AI_GLOBAL_CONCURRENCY = max(
    1,
    int(os.getenv("MONIPAN_AI_GLOBAL_CONCURRENCY", "2")),
)
AI_CONCURRENCY_TTL_SECONDS = max(
    60,
    int(os.getenv("MONIPAN_AI_CONCURRENCY_TTL_SECONDS", "180")),
)


# ===========================================================================
# 网站用户认证
# ===========================================================================

@router.post("/auth/register", response_model=CurrentUserOut, status_code=201)
def register(
    payload: RegisterIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    enforce_fixed_window(
        scope="auth_register_ip",
        identity=client_ip(request),
        limit=REGISTER_IP_LIMIT,
        window_seconds=REGISTER_WINDOW_SECONDS,
        message="注册尝试过于频繁，请稍后再试",
    )
    user = register_user(db, payload)
    set_session_cookie(response, issue_session(db, user))
    return user_values(user)


@router.post("/auth/login", response_model=CurrentUserOut)
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    enforce_fixed_window(
        scope="auth_login_ip",
        identity=client_ip(request),
        limit=LOGIN_IP_LIMIT,
        window_seconds=LOGIN_IP_WINDOW_SECONDS,
        message="登录请求过于频繁，请稍后再试",
    )
    try:
        user = verify_credentials(db, payload.username, payload.password)
    except HTTPException:
        enforce_fixed_window(
            scope="auth_login_username_failure",
            identity=payload.username,
            limit=LOGIN_FAILURE_LIMIT,
            window_seconds=LOGIN_FAILURE_WINDOW_SECONDS,
            message="该账号登录失败次数过多，请稍后再试",
        )
        raise
    clear_fixed_window(
        scope="auth_login_username_failure",
        identity=payload.username,
    )
    set_session_cookie(response, issue_session(db, user))
    return user_values(user)


@router.get("/auth/me", response_model=CurrentUserOut)
def current_user(user: User = Depends(get_current_user)) -> dict:
    return user_values(user)


@router.post("/auth/logout", status_code=204)
def logout(
    response: Response,
    session: AuthSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    revoke_session(db, session)
    clear_session_cookie(response)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def order_values(order: Order) -> dict:
    """
    将 Order ORM 对象格式化为前端友好的字典。

    与 stock_values() 类似，用于将数据库对象转换为 API 响应。
    提取订单的关键字段，包括关联的股票信息（通过 order.stock）。

    Args:
        order: SQLAlchemy Order ORM 对象（需要预加载 stock 关系）

    Returns:
        dict: 订单详情字典
    """
    return {
        "order_no": order.order_no,          # 订单编号
        "symbol": order.stock.symbol,         # 股票代码（通过关联关系获取）
        "stock_name": order.stock.name,       # 股票名称
        "side": order.side,                   # 买卖方向
        "order_type": order.order_type,       # 订单类型
        "limit_price": order.limit_price,
        "submitted_quote_price": order.submitted_quote_price,
        "submitted_quote_at": order.submitted_quote_at,
        "filled_quote_at": order.filled_quote_at,
        "quantity": order.quantity,           # 委托数量
        "filled_quantity": order.filled_quantity,  # 已成交数量
        "price": order.price,                 # 成交价格
        "fee": order.fee,                     # 交易费用
        "status": order.status,               # 订单状态
        "reject_reason": order.reject_reason, # 拒绝原因
        "cancelable": order.status == "PENDING",
        "created_at": order.created_at,       # 创建时间
    }


# ===========================================================================
# 股票行情
# ===========================================================================

@router.get(
    "/stocks",
    response_model=list[StockOut],
    dependencies=[Depends(get_current_user)],
)
def list_stocks(
    keyword: str = Query(default="", max_length=40),
    industry: str = Query(default="", max_length=40),
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    获取股票列表，支持按关键词和行业筛选。

    查询参数：
        keyword : 搜索关键词，匹配股票代码或名称（模糊匹配）
        industry: 行业筛选，精确匹配行业名称

    示例请求：
        GET /api/stocks                          → 返回全部 200 只股票
        GET /api/stocks?keyword=茅台              → 搜索"茅台"
        GET /api/stocks?industry=银行             → 只看银行股
        GET /api/stocks?keyword=银&industry=银行  → 组合筛选

    排序：按股票代码升序（600xxx 在前，000xxx/002xxx/300xxx 在后）
    """
    # 只展示已经取得有效真实行情的股票，不暴露待行情占位记录。
    query = (
        select(Stock)
        .where(Stock.price > 0, Stock.prev_close > 0)
        .order_by(Stock.symbol)
    )

    # 关键词筛选：模糊匹配代码或名称（OR 条件）
    if keyword:
        query = query.where(
            or_(
                Stock.symbol.contains(keyword),
                Stock.name.contains(keyword),
            )
        )

    # 行业筛选：精确匹配
    if industry:
        query = query.where(Stock.industry == industry)

    # 执行查询并格式化输出
    return [stock_values(stock) for stock in db.scalars(query).all()]


@router.get(
    "/stocks/{symbol}",
    response_model=StockOut,
    dependencies=[Depends(get_current_user)],
)
def get_stock(symbol: str, db: Session = Depends(get_db)) -> dict:
    """
    获取单只股票的详细行情。

    路径参数：
        symbol: 股票代码，如 "600519"

    如果股票不存在，返回 404。
    """
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    if stock.price <= 0 or stock.prev_close <= 0:
        raise HTTPException(status_code=503, detail="该股票尚未取得真实行情")
    return stock_values(stock)


@router.get(
    "/stocks/{symbol}/history",
    response_model=StockHistoryOut,
    dependencies=[Depends(get_current_user)],
)
def get_stock_history(
    symbol: str,
    limit: int = Query(default=90, ge=20, le=240),
    db: Session = Depends(get_db),
) -> dict:
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    source, bars = stock_history(db, stock, limit)
    target_count = min(limit, HISTORY_CACHE_TARGET_BARS)
    serialized_bars: list[dict] = []
    for index, bar in enumerate(bars):
        previous_close = bar.prev_close
        if previous_close is None and index > 0:
            previous_close = bars[index - 1].close_price
        change = bar.change_amount
        if change is None and previous_close is not None:
            change = bar.close_price - previous_close
        change_percent = bar.change_percent
        if (
            change_percent is None
            and previous_close is not None
            and previous_close > 0
            and change is not None
        ):
            change_percent = (
                change / previous_close * Decimal("100")
            ).quantize(Decimal("0.0001"))
        serialized_bars.append(
            {
                "trade_date": bar.trade_date,
                "open_price": bar.open_price,
                "high_price": bar.high_price,
                "low_price": bar.low_price,
                "close_price": bar.close_price,
                "prev_close": previous_close,
                "change": change,
                "change_percent": change_percent,
                "volume": bar.volume,
                "turnover": bar.turnover,
            }
        )
    return {
        "symbol": symbol,
        "period": "DAY",
        "source": source,
        "cached_count": len(bars),
        "target_count": target_count,
        "complete": len(bars) >= target_count,
        "bars": serialized_bars,
    }


@router.get(
    "/stocks/{symbol}/fundamentals",
    response_model=StockFundamentalsOut,
    dependencies=[Depends(get_current_user)],
)
def get_stock_fundamentals(
    symbol: str,
    db: Session = Depends(get_db),
) -> dict:
    """Return normalized reports and performance events from a local cache."""
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    try:
        return fundamentals_for_stock(db, stock)
    except FundamentalsDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/stocks/{symbol}/announcements",
    response_model=StockAnnouncementsOut,
    dependencies=[Depends(get_current_user)],
)
def get_stock_announcements(
    symbol: str,
    days: int = Query(default=365, ge=30, le=1095),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Return official exchange announcement metadata from an on-demand cache."""
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    if stock.exchange not in {"SSE", "SZSE"}:
        raise HTTPException(
            status_code=400,
            detail="当前公告数据源仅支持上交所和深交所股票",
        )
    try:
        return announcements_for_stock(db, stock, days=days, limit=limit)
    except AnnouncementDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/stocks/{symbol}/regulatory-letters",
    response_model=StockRegulatoryLettersOut,
    dependencies=[Depends(get_current_user)],
)
def get_stock_regulatory_letters(
    symbol: str,
    days: int = Query(default=365, ge=30, le=1095),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Return official exchange inquiry letters and linked company replies."""
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    if stock.exchange not in {"SSE", "SZSE"}:
        raise HTTPException(
            status_code=400,
            detail="当前监管函件数据源仅支持上交所和深交所股票",
        )
    try:
        return regulatory_letters_for_stock(db, stock, days=days, limit=limit)
    except RegulatoryDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ===========================================================================
# 单股 AI 风险分析（固定调用八个只读工具，永不下单）
# ===========================================================================

@router.post(
    "/agent/stocks/{symbol}/analyze",
    response_model=AgentRunOut,
    status_code=201,
)
def analyze_stock_endpoint(
    symbol: str,
    payload: StockAnalysisCreate,
    request: Request,
    user: User = Depends(get_current_user),
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> dict:
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if stock is None:
        raise HTTPException(status_code=404, detail="股票不存在")

    enforce_fixed_window(
        scope="ai_user_24h",
        identity=str(user.id),
        limit=AI_USER_24H_LIMIT,
        window_seconds=24 * 60 * 60,
        message=f"每个账号 24 小时最多进行 {AI_USER_24H_LIMIT} 次 AI 分析",
    )
    enforce_fixed_window(
        scope="ai_ip_hourly",
        identity=client_ip(request),
        limit=AI_IP_HOURLY_LIMIT,
        window_seconds=60 * 60,
        message="当前网络的 AI 分析请求过于频繁，请稍后再试",
    )
    lease = acquire_analysis_lease(
        user_id=user.id,
        global_limit=AI_GLOBAL_CONCURRENCY,
        ttl_seconds=AI_CONCURRENCY_TTL_SECONDS,
    )
    try:
        run = analyze_stock(
            db,
            user=user,
            account=account,
            stock=stock,
            options=StockAnalysisOptions(
                question=payload.question,
                focus_keywords=payload.focus_keywords,
                history_days=payload.history_days,
                disclosure_days=payload.disclosure_days,
                news_days=payload.news_days,
                max_documents=payload.max_documents,
            ),
        )
    except AgentOrchestrationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "message": str(exc),
                "run_id": exc.run_id,
            },
        ) from exc
    finally:
        release_analysis_lease(lease)
    stored = load_agent_run(db, run_id=run.id, user_id=user.id)
    if stored is None:
        raise HTTPException(status_code=500, detail="分析结果保存后无法读取")
    return agent_run_values(stored)


@router.get("/agent/runs/{run_id}", response_model=AgentRunOut)
def get_agent_run_endpoint(
    run_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    run = load_agent_run(db, run_id=run_id, user_id=user.id)
    if run is None:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    return agent_run_values(run)


@router.get("/watchlist", response_model=list[WatchlistItemOut])
def list_watchlist(
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    items = db.scalars(
        select(WatchlistItem)
        .where(WatchlistItem.account_id == account.id)
        .options(joinedload(WatchlistItem.stock))
        .order_by(WatchlistItem.created_at.desc())
    ).all()
    return [{"symbol": item.stock.symbol, "created_at": item.created_at} for item in items]


@router.post("/watchlist/{symbol}", response_model=WatchlistItemOut, status_code=201)
def add_watchlist_item(
    symbol: str,
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> dict:
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    item = db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.account_id == account.id,
            WatchlistItem.stock_id == stock.id,
        )
    )
    if item is None:
        item = WatchlistItem(account_id=account.id, stock_id=stock.id)
        db.add(item)
        db.commit()
        db.refresh(item)
    return {"symbol": stock.symbol, "created_at": item.created_at}


@router.delete("/watchlist/{symbol}", status_code=204)
def remove_watchlist_item(
    symbol: str,
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> None:
    item = db.scalar(
        select(WatchlistItem)
        .join(Stock, WatchlistItem.stock_id == Stock.id)
        .where(WatchlistItem.account_id == account.id, Stock.symbol == symbol)
    )
    if item is not None:
        db.delete(item)
        db.commit()


@router.get(
    "/market/status",
    response_model=MarketStatusOut,
    dependencies=[Depends(get_current_user)],
)
def market_status(db: Session = Depends(get_db)) -> dict:
    """返回行情来源、最近刷新、覆盖率和时段感知的新鲜度状态。"""
    return market_status_values(db)


@router.get(
    "/market/indices",
    response_model=list[MarketIndexOut],
    dependencies=[Depends(get_current_user)],
)
def market_indices(db: Session = Depends(get_db)) -> list[dict]:
    """返回已取得真实快照的五个主要 A 股指数。"""
    items = db.scalars(
        select(MarketIndex)
        .where(MarketIndex.price > 0)
        .order_by(MarketIndex.display_order)
    ).all()
    return [market_index_values(item) for item in items]


# ===========================================================================
# 账户与持仓
# ===========================================================================

@router.get("/account", response_model=AccountOut)
def account_summary(
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> dict:
    """
    获取账户概览信息。

    计算逻辑：
        1. 获取当前登录用户的模拟账户
        2. 查询所有持仓（quantity > 0）
        3. 计算持仓总市值 = Σ(当前价 × 持仓数量)
        4. 总资产 = 可用资金 + 持仓市值
        5. 总盈亏 = 总资产 - 初始资金
        6. 总收益率 = 总盈亏 / 初始资金 × 100%

    前端用此数据渲染顶部资产概览卡片。
    """
    # 查询所有有效持仓（quantity > 0），预加载股票信息
    positions = db.scalars(
        select(Position)
        .where(Position.account_id == account.id, Position.quantity > 0)
        .options(joinedload(Position.stock))  # 预加载股票，避免 N+1
    ).all()

    if any(p.stock.price <= 0 for p in positions):
        raise HTTPException(status_code=503, detail="部分持仓尚未取得真实行情")

    # 计算持仓总市值
    # 使用生成器表达式惰性求和，Decimal("0") 作为初始值
    market_value = sum(
        (p.stock.price * p.quantity for p in positions),
        Decimal("0")
    )

    # 总资产 = 现金 + 股票市值
    total_assets = account.available_cash + market_value

    # 总盈亏（相对于初始资金）
    profit_loss = total_assets - account.initial_cash
    floating_profit_loss = cents(market_value - sum(
        (position.average_cost * position.quantity for position in positions),
        Decimal("0"),
    ))
    realized_profit_loss = profit_loss - floating_profit_loss

    # 总收益率（百分比）
    return_percent = (
        profit_loss / account.initial_cash * 100
        if account.initial_cash else Decimal("0")
    )

    return {
        "username": account.user.username,         # 用户名
        "initial_cash": account.initial_cash,       # 初始资金
        "available_cash": account.available_cash,   # 可用资金
        "market_value": market_value,               # 持仓市值
        "total_assets": total_assets,               # 总资产
        "total_profit_loss": profit_loss,           # 总盈亏
        "realized_profit_loss": realized_profit_loss,
        "floating_profit_loss": floating_profit_loss,
        "total_return_percent": return_percent,     # 总收益率(%)
    }


@router.get("/positions", response_model=list[PositionOut])
def list_positions(
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    获取持仓列表。

    只返回有效持仓（quantity > 0），按更新时间倒序排列。
    每条持仓记录计算浮动盈亏：
        - 市值 = 当前价 × 持仓数量
        - 成本 = 均价 × 持仓数量
        - 浮动盈亏 = 市值 - 成本
        - 盈亏百分比 = 浮动盈亏 / 成本 × 100%

    前端用此数据渲染"当前持仓" Tab。
    """
    # 查询所有有效持仓，预加载股票信息，按更新时间倒序
    positions = db.scalars(
        select(Position)
        .where(Position.account_id == account.id, Position.quantity > 0)
        .options(joinedload(Position.stock))
        .order_by(Position.updated_at.desc())
    ).all()

    if any(p.stock.price <= 0 for p in positions):
        raise HTTPException(status_code=503, detail="部分持仓尚未取得真实行情")

    # 格式化输出，逐条计算浮动盈亏
    result = []
    for position in positions:
        # 当前市值 = 当前股价 × 持仓数量
        market_value = position.stock.price * position.quantity

        # 持仓成本 = 持仓均价 × 持仓数量
        cost_value = position.average_cost * position.quantity

        # 浮动盈亏 = 市值 - 成本
        profit_loss = market_value - cost_value

        # 盈亏百分比（避免除零）
        percent = (
            profit_loss / cost_value * 100 if cost_value else Decimal("0")
        )

        result.append({
            "symbol": position.stock.symbol,          # 股票代码
            "stock_name": position.stock.name,        # 股票名称
            "quantity": position.quantity,            # 持仓数量
            "sellable_quantity": position_sellable_values(
                db, account.id, position
            )[2],
            "average_cost": position.average_cost,    # 持仓均价
            "current_price": position.stock.price,    # 当前市价
            "market_value": market_value,             # 当前市值
            "profit_loss": profit_loss,               # 浮动盈亏
            "profit_loss_percent": percent,           # 盈亏百分比
        })

    return result


# ===========================================================================
# 交易
# ===========================================================================

@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(
    payload: OrderCreate,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> dict:
    """
    创建当日有效的模拟委托（市价或限价）。

    请求体（OrderCreate 自动校验）：
        - symbol  : 股票代码
        - side    : "BUY" 或 "SELL"
        - quantity: 委托数量（>0，≤1000000，且必须为 100 的整数倍）

    下单逻辑委托给 place_order() 处理，包含：
        - 资金校验
        - 持仓校验
        - 费用计算
        - 持仓更新
        - 成交记录

    成功返回 201 Created，失败返回 400 或 404。

    市价单在有效连续竞价行情下按快照价模拟成交；未触价限价单进入待成交，收盘后过期。
    """
    # 调用交易引擎执行下单
    order = place_order(
        db,
        account,
        payload.symbol,
        payload.side,
        payload.quantity,
        payload.order_type,
        payload.limit_price,
        idempotency_key,
    )

    # 格式化并返回订单信息
    return order_values(order)


@router.post("/orders/preview", response_model=OrderPreviewOut)
def preview_order(
    payload: OrderCreate,
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> dict:
    return build_order_preview(
        db,
        account,
        payload.symbol,
        payload.side,
        payload.quantity,
        payload.order_type,
        payload.limit_price,
    )


@router.post("/orders/{order_no}/cancel", response_model=OrderOut)
def cancel_pending_order(
    order_no: str,
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> dict:
    return order_values(cancel_order(db, account, order_no))


@router.get("/orders", response_model=list[OrderOut])
def list_orders(
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    获取订单列表（最近 100 条）。

    按创建时间倒序排列，最新的订单在最前面。
    限制 100 条避免数据量过大。

    前端用此数据渲染"委托记录" Tab。
    """
    # 查询最近 100 条订单，预加载股票信息
    orders = db.scalars(
        select(Order)
        .where(Order.account_id == account.id)
        .options(joinedload(Order.stock))  # 预加载股票，避免 N+1 查询
        .order_by(Order.created_at.desc())
        .limit(100)
    ).all()

    return [order_values(order) for order in orders]


@router.get("/trades", response_model=list[TradeOut])
def list_trades(
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    获取成交记录列表（最近 100 条）。

    按成交时间倒序排列，关联查询订单（获取 order_no）和股票信息。
    使用 joinedload 预加载关联对象，避免 N+1 查询问题。

    前端用此数据渲染"成交记录" Tab。
    """
    # 查询最近 100 条成交记录，预加载股票和订单信息
    trades = db.scalars(
        select(Trade)
        .join(Order, Trade.order_id == Order.id)   # 关联订单表
        .where(Trade.account_id == account.id)
        .options(
            joinedload(Trade.stock),  # 预加载股票
            joinedload(Trade.order),  # 预加载订单（获取 order_no）
        )
        .order_by(Trade.created_at.desc())
        .limit(100)
    ).all()

    # 格式化输出
    return [
        {
            "trade_no": trade.trade_no,            # 成交编号
            "order_no": trade.order.order_no,       # 关联订单编号
            "symbol": trade.stock.symbol,           # 股票代码
            "stock_name": trade.stock.name,         # 股票名称
            "side": trade.side,                     # 买卖方向
            "quantity": trade.quantity,             # 成交数量
            "price": trade.price,                   # 成交价格
            "amount": trade.amount,                 # 成交金额
            "fee": trade.fee,                       # 交易费用
            "filled_quote_at": trade.filled_quote_at,
            "created_at": trade.created_at,         # 成交时间
        }
        for trade in trades
    ]


# ===========================================================================
# v0.4 学习复盘：快照由成交、资金流水与历史日线重建
# ===========================================================================

@router.get("/review/snapshots", response_model=list[DailySnapshotOut])
def list_daily_snapshots(
    days: int = Query(default=90, ge=1, le=365),
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    snapshots = rebuild_daily_snapshots(db, account)
    return [snapshot_values(item) for item in snapshots[-days:]]


@router.get("/review/days/{review_date}", response_model=DayReviewOut)
def get_day_review(
    review_date: date,
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> dict:
    return day_review(db, account, review_date)


@router.put("/review/days/{review_date}/note", response_model=ReviewNoteOut)
def save_day_review_note(
    review_date: date,
    payload: ReviewNoteIn,
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> dict:
    if review_date < local_date(account.created_at) or review_date > last_completed_date():
        raise HTTPException(status_code=404, detail="该日期尚无已完成的账户快照")
    note = db.scalar(select(DailyReviewNote).where(
        DailyReviewNote.account_id == account.id,
        DailyReviewNote.review_date == review_date,
    ))
    if not payload.content:
        if note is not None:
            db.delete(note)
    elif note is None:
        db.add(DailyReviewNote(
            account_id=account.id, review_date=review_date, content=payload.content,
        ))
    else:
        note.content = payload.content
    db.commit()
    return {"content": payload.content or None}


@router.put("/review/trades/{trade_no}/note", response_model=ReviewNoteOut)
def save_trade_review_note(
    trade_no: str,
    payload: ReviewNoteIn,
    account: SimulationAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> dict:
    trade = db.scalar(select(Trade).where(
        Trade.account_id == account.id,
        Trade.trade_no == trade_no,
    ))
    if trade is None:
        raise HTTPException(status_code=404, detail="成交记录不存在")
    note = db.scalar(select(TradeNote).where(
        TradeNote.account_id == account.id,
        TradeNote.trade_id == trade.id,
    ))
    if not payload.content:
        if note is not None:
            db.delete(note)
    elif note is None:
        db.add(TradeNote(
            account_id=account.id, trade_id=trade.id, content=payload.content,
        ))
    else:
        note.content = payload.content
    db.commit()
    return {"content": payload.content or None}

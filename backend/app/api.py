"""
RESTful API 路由层。

本模块定义了 MoniPan 模拟盘系统的全部 HTTP API 端点（路由前缀：/api）。

端点一览：
    股票行情
        GET  /api/stocks           — 股票列表（支持关键词和行业筛选）
        GET  /api/stocks/{symbol}  — 单只股票详情
        GET  /api/market/indices   — 五个主要 A 股指数
        GET  /api/market/status    — 行情源、更新时间与新鲜度状态
        POST /api/market/tick      — 手动触发一次行情刷新

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

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from .database import get_db
from .models import MarketIndex, Order, Position, Stock, Trade
from .schemas import (
    AccountOut,
    MarketIndexOut,
    MarketStatusOut,
    OrderCreate,
    OrderOut,
    PositionOut,
    StockOut,
    TradeOut,
)
from .services.market import (
    MarketDataError,
    market_index_values,
    market_status_values,
    stock_values,
    tick_market,
    tick_market_indices,
)
from .services.trading import get_demo_account, place_market_order


# ---------------------------------------------------------------------------
# 路由实例
# ---------------------------------------------------------------------------
# 所有端点自动添加 /api 前缀
router = APIRouter(prefix="/api")


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
        "quantity": order.quantity,           # 委托数量
        "filled_quantity": order.filled_quantity,  # 已成交数量
        "price": order.price,                 # 成交价格
        "fee": order.fee,                     # 交易费用
        "status": order.status,               # 订单状态
        "reject_reason": order.reject_reason, # 拒绝原因
        "created_at": order.created_at,       # 创建时间
    }


# ===========================================================================
# 股票行情
# ===========================================================================

@router.get("/stocks", response_model=list[StockOut])
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


@router.get("/stocks/{symbol}", response_model=StockOut)
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


@router.get("/market/status", response_model=MarketStatusOut)
def market_status(db: Session = Depends(get_db)) -> dict:
    """返回行情来源、最近刷新、覆盖率和时段感知的新鲜度状态。"""
    return market_status_values(db)


@router.get("/market/indices", response_model=list[MarketIndexOut])
def market_indices(db: Session = Depends(get_db)) -> list[dict]:
    """返回已取得真实快照的五个主要 A 股指数。"""
    items = db.scalars(
        select(MarketIndex)
        .where(MarketIndex.price > 0)
        .order_by(MarketIndex.display_order)
    ).all()
    return [market_index_values(item) for item in items]


@router.post("/market/tick", response_model=list[StockOut])
def refresh_market(db: Session = Depends(get_db)) -> list[dict]:
    """
    手动触发一次行情刷新。

    立即执行一次 tick_market()，然后返回刷新后的全部股票行情。
    用于前端手动刷新按钮或调试。

    注意：后台已经有按配置自动刷新的 market_loop，
    此端点只是提供了手动即时刷新的能力。
    """
    try:
        tick_market_indices(db)
        tick_market(db)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 返回刷新后的行情数据
    return [
        stock_values(stock)
        for stock in db.scalars(
            select(Stock)
            .where(Stock.price > 0, Stock.prev_close > 0)
            .order_by(Stock.symbol)
        ).all()
    ]


# ===========================================================================
# 账户与持仓
# ===========================================================================

@router.get("/account", response_model=AccountOut)
def account_summary(db: Session = Depends(get_db)) -> dict:
    """
    获取账户概览信息。

    计算逻辑：
        1. 获取 demo 用户的模拟账户
        2. 查询所有持仓（quantity > 0）
        3. 计算持仓总市值 = Σ(当前价 × 持仓数量)
        4. 总资产 = 可用资金 + 持仓市值
        5. 总盈亏 = 总资产 - 初始资金
        6. 总收益率 = 总盈亏 / 初始资金 × 100%

    前端用此数据渲染顶部资产概览卡片。
    """
    # 获取 demo 账户
    account = get_demo_account(db)

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
        "total_return_percent": return_percent,     # 总收益率(%)
    }


@router.get("/positions", response_model=list[PositionOut])
def list_positions(db: Session = Depends(get_db)) -> dict:
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
    # 获取 demo 账户
    account = get_demo_account(db)

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
def create_order(payload: OrderCreate, db: Session = Depends(get_db)) -> dict:
    """
    创建交易订单（市价买入或卖出）。

    请求体（OrderCreate 自动校验）：
        - symbol  : 股票代码
        - side    : "BUY" 或 "SELL"
        - quantity: 委托数量（>0，≤1000000，且必须为 100 的整数倍）

    下单逻辑委托给 place_market_order() 处理，包含：
        - 资金校验
        - 持仓校验
        - 费用计算
        - 持仓更新
        - 成交记录

    成功返回 201 Created，失败返回 400 或 404。

    注意：当前版本为市价单，提交即成交，无需轮询订单状态。
    """
    # 调用交易引擎执行下单
    order = place_market_order(db, payload.symbol, payload.side, payload.quantity)

    # 格式化并返回订单信息
    return order_values(order)


@router.get("/orders", response_model=list[OrderOut])
def list_orders(db: Session = Depends(get_db)) -> list[dict]:
    """
    获取订单列表（最近 100 条）。

    按创建时间倒序排列，最新的订单在最前面。
    限制 100 条避免数据量过大。

    前端用此数据渲染"委托记录" Tab。
    """
    # 获取 demo 账户
    account = get_demo_account(db)

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
def list_trades(db: Session = Depends(get_db)) -> list[dict]:
    """
    获取成交记录列表（最近 100 条）。

    按成交时间倒序排列，关联查询订单（获取 order_no）和股票信息。
    使用 joinedload 预加载关联对象，避免 N+1 查询问题。

    前端用此数据渲染"成交记录" Tab。
    """
    # 获取 demo 账户
    account = get_demo_account(db)

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
            "created_at": trade.created_at,         # 成交时间
        }
        for trade in trades
    ]

"""
交易引擎。

本模块实现了模拟盘系统的核心交易逻辑，包括：
    - 账户查询
    - 手续费计算（佣金 + 印花税）
    - 市价委托下单（买入/卖出）
    - 持仓管理（成本均价更新、持仓数量变更）
    - 成交记录与资金流水记录

A 股交易费用模型：
    买入费用 = 佣金（成交金额 × 0.03%，最低 5 元）
    卖出费用 = 佣金 + 印花税（成交金额 × 0.05%）

交易规则：
    - 仅支持市价单（MARKET），提交即成交
    - 买卖数量必须是 100 股的整数倍（前端 + 后端双重校验）
    - 买入时检查可用资金是否充足
    - 卖出时检查持仓数量是否足够
    - 持仓均价采用加权平均法计算
"""

from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..models import (
    AccountTransaction,
    Order,
    Position,
    SimulationAccount,
    Stock,
    Trade,
    User,
)


# ===========================================================================
# 常量定义
# ===========================================================================

# 价格精度：人民币最小单位 0.01 元
CENT = Decimal("0.01")

# 佣金费率：成交金额的 0.03%（万分之三）
# 注意：真实 A 股佣金费率各券商不同，一般在万分之二点五到万分之三之间
COMMISSION_RATE = Decimal("0.0003")

# 最低佣金：每笔交易最低收取 5 元
# 即如果按费率算出来的佣金不足 5 元，按 5 元收取
MIN_COMMISSION = Decimal("5.00")

# 卖出印花税率：成交金额的 0.05%（万分之五）
# 注意：A 股印花税仅在卖出时单向收取，买入不收取
SELL_STAMP_DUTY_RATE = Decimal("0.0005")


# ===========================================================================
# 工具函数
# ===========================================================================

def money(value: Decimal) -> Decimal:
    """
    将金额量化到分（0.01 元），四舍五入。

    所有金额计算后都应通过此函数处理，确保精度一致。
    例如：money(Decimal("123.456")) → Decimal("123.46")

    Args:
        value: 原始金额

    Returns:
        保留两位小数的金额
    """
    return value.quantize(CENT, ROUND_HALF_UP)


# ===========================================================================
# 账户查询
# ===========================================================================

def get_demo_account(db: Session) -> SimulationAccount:
    """
    获取 demo 用户的模拟账户。

    通过 username="demo" 查询用户并关联加载账户信息。
    使用 joinedload 预加载 User 关系，避免后续访问 account.user 时
    产生额外的 N+1 查询。

    Args:
        db: 数据库会话

    Returns:
        SimulationAccount: demo 用户的模拟账户

    Raises:
        HTTPException(404): 如果 demo 账户不存在
    """
    account = db.scalar(
        select(SimulationAccount)
        .join(User)                                    # 关联用户表
        .where(User.username == "demo")                # 按用户名筛选
        .options(joinedload(SimulationAccount.user))   # 预加载用户信息
    )
    if not account:
        raise HTTPException(status_code=404, detail="模拟账户不存在")
    return account


# ===========================================================================
# 手续费计算
# ===========================================================================

def calculate_fee(side: str, amount: Decimal) -> Decimal:
    """
    计算单笔交易的手续费总额。

    A 股交易费用构成：
        - 佣金：买入和卖出都收取
          公式：max(5元, 成交金额 × 0.03%)
        - 印花税：仅卖出时收取
          公式：成交金额 × 0.05%

    举例：
        买入 10,000 元股票：佣金 = max(5, 10000×0.0003) = 5 元，总费用 = 5 元
        卖出 10,000 元股票：佣金 = 5 元 + 印花税 = 10000×0.0005 = 5 元，总费用 = 10 元
        买入 50,000 元股票：佣金 = 50000×0.0003 = 15 元，总费用 = 15 元

    Args:
        side: 买卖方向，"BUY" 或 "SELL"
        amount: 成交金额（元）= 价格 × 数量

    Returns:
        总费用（元），保留两位小数
    """
    # 佣金：费率 × 金额，但不低于最低佣金 5 元
    commission = max(MIN_COMMISSION, money(amount * COMMISSION_RATE))

    # 印花税：仅卖出时收取
    stamp_duty = money(amount * SELL_STAMP_DUTY_RATE) if side == "SELL" else Decimal("0")

    # 总费用 = 佣金 + 印花税
    return money(commission + stamp_duty)


# ===========================================================================
# 市价下单
# ===========================================================================

def place_market_order(
    db: Session,
    symbol: str,
    side: str,
    quantity: int,
) -> Order:
    
    # ------------------------------------------------------------------
    # 第一步：获取账户和股票信息
    # ------------------------------------------------------------------
    account = get_demo_account(db)

    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    if stock.price <= 0 or stock.prev_close <= 0:
        raise HTTPException(status_code=503, detail="该股票尚未取得真实行情，暂不可下单")

    # ------------------------------------------------------------------
    # 第二步：计算成交金额和费用
    # ------------------------------------------------------------------
    # 以当前市价作为成交价格
    price = stock.price
    # 成交金额 = 价格 × 数量
    amount = money(price * quantity)
    # 手续费（佣金 + 可能的印花税）
    fee = calculate_fee(side, amount)

    # ------------------------------------------------------------------
    # 第三步：创建订单记录
    # ------------------------------------------------------------------
    # 订单编号格式：O + 16 位大写十六进制（UUID4 前 16 位）
    # 示例：OA1B2C3D4E5F6G7H8
    order = Order(
        order_no=f"O{uuid4().hex[:16].upper()}",
        account_id=account.id,
        stock_id=stock.id,
        side=side,
        order_type="MARKET",       # 市价单
        quantity=quantity,
        filled_quantity=quantity,   # 市价单：提交即全部成交
        price=price,
        fee=fee,
        status="FILLED",            # 直接标记为已成交
    )

    # ------------------------------------------------------------------
    # 第四步：执行交易（在 try 块中确保异常时回滚）
    # ------------------------------------------------------------------
    try:
        # 将订单写入数据库（flush 获取自增 id，但不 commit）
        db.add(order)
        db.flush()

        # 查询该股票是否存在已有持仓
        # 同一账户对同一股票最多一条持仓记录（由唯一约束保证）
        position = db.scalar(
            select(Position).where(
                Position.account_id == account.id,
                Position.stock_id == stock.id,
            )
        )

        # ---- 买入分支 ----
        if side == "BUY":
            # 计算买入总成本 = 成交金额 + 手续费
            total_cost = amount + fee

            # 检查可用资金是否足够
            if account.available_cash < total_cost:
                raise HTTPException(status_code=400, detail="可用资金不足")

            # 记录买入前的持仓数量和成本（用于计算新的加权均价）
            old_quantity = position.quantity if position else 0
            old_cost = (
                position.average_cost * old_quantity if position else Decimal("0")
            )

            # 如果没有持仓记录，创建一条新的
            if not position:
                position = Position(account_id=account.id, stock_id=stock.id)
                db.add(position)

            # 更新持仓数量：旧数量 + 本次买入数量
            position.quantity = old_quantity + quantity

            # 更新持仓均价（加权平均法）
            # 新均价 = (旧总成本 + 本次买入总成本) / 新总数量
            # 注意：分母是更新后的 position.quantity
            position.average_cost = (old_cost + total_cost) / position.quantity

            # 扣减可用资金
            account.available_cash = money(account.available_cash - total_cost)

            # 资金变动金额（负数表示支出）
            cash_change = -total_cost

        # ---- 卖出分支 ----
        else:
            # 检查持仓是否存在且数量足够
            if not position or position.quantity < quantity:
                raise HTTPException(status_code=400, detail="可卖持仓不足")

            # 卖出净收入 = 成交金额 - 手续费（印花税 + 佣金）
            proceeds = amount - fee

            # 减少持仓数量
            position.quantity -= quantity

            # 如果卖完后持仓数量为 0，清空均价
            # （避免后续计算浮动盈亏时除零错误）
            if position.quantity == 0:
                position.average_cost = Decimal("0")

            # 增加可用资金
            account.available_cash = money(account.available_cash + proceeds)

            # 资金变动金额（正数表示收入）
            cash_change = proceeds

        # ------------------------------------------------------------------
        # 第五步：创建成交记录
        # ------------------------------------------------------------------
        # 成交编号格式：T + 16 位大写十六进制
        trade = Trade(
            trade_no=f"T{uuid4().hex[:16].upper()}",
            order_id=order.id,
            account_id=account.id,
            stock_id=stock.id,
            side=side,
            quantity=quantity,
            price=price,
            amount=amount,
            fee=fee,
        )
        db.add(trade)

        # ------------------------------------------------------------------
        # 第六步：创建账户资金流水
        # ------------------------------------------------------------------
        db.add(
            AccountTransaction(
                account_id=account.id,
                order_id=order.id,
                transaction_type=side,       # "BUY" 或 "SELL"
                amount=cash_change,          # 买入为负数，卖出为正数
                balance_after=account.available_cash,  # 交易后余额
            )
        )

        # ------------------------------------------------------------------
        # 第七步：提交事务
        # ------------------------------------------------------------------
        # 所有数据变更在同一事务中提交，保证原子性
        db.commit()

        # 刷新订单对象，使其包含数据库生成的值（如 created_at）
        db.refresh(order)

        # 手动关联股票对象，方便 API 层通过 order.stock.name 获取名称
        order.stock = stock

        return order

    except HTTPException:
        # FastAPI HTTP 异常：回滚事务并重新抛出
        # 这类异常是业务校验失败（资金不足、持仓不足等），不需要记录日志
        db.rollback()
        raise

    except Exception:
        # 其他未知异常：回滚事务并重新抛出
        # 这类异常需要上层（FastAPI 异常处理器）记录日志
        db.rollback()
        raise

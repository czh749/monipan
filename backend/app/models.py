from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """Return naive UTC for SQLite without using deprecated datetime.utcnow()."""
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    account: Mapped["SimulationAccount"] = relationship(back_populates="user")


class SimulationAccount(Base):
    __tablename__ = "simulation_accounts"
    __table_args__ = (
        CheckConstraint("available_cash >= 0", name="ck_account_cash_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("1000000"))
    available_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("1000000"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user: Mapped[User] = relationship(back_populates="account")
    positions: Mapped[list["Position"]] = relationship(back_populates="account")


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(40), index=True)
    exchange: Mapped[str] = mapped_column(String(8))
    industry: Mapped[str] = mapped_column(String(40))
    prev_close: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    open_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    high_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    low_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    volume: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MarketIndex(Base):
    __tablename__ = "market_indices"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(40))
    exchange: Mapped[str] = mapped_column(String(8))
    display_order: Mapped[int] = mapped_column(default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    change: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    change_percent: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    turnover: Mapped[Decimal] = mapped_column(Numeric(22, 2))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("account_id", "stock_id", name="uq_position_account_stock"),
        CheckConstraint("quantity >= 0", name="ck_position_quantity_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("simulation_accounts.id"), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    quantity: Mapped[int] = mapped_column(default=0)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    account: Mapped[SimulationAccount] = relationship(back_populates="positions")
    stock: Mapped[Stock] = relationship()


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("simulation_accounts.id"), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(10), default="MARKET")
    quantity: Mapped[int]
    filled_quantity: Mapped[int] = mapped_column(default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    reject_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    stock: Mapped[Stock] = relationship()


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("simulation_accounts.id"), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[int]
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    stock: Mapped[Stock] = relationship()
    order: Mapped[Order] = relationship()


class AccountTransaction(Base):
    __tablename__ = "account_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("simulation_accounts.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    transaction_type: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    exchange: str
    industry: str
    prev_close: Decimal
    price: Decimal
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    volume: int
    change: Decimal
    change_percent: Decimal
    updated_at: datetime


class MarketStatusOut(BaseModel):
    provider: str
    provider_label: str
    source_type: str
    trading_mode: str
    status: Literal[
        "refreshing",
        "fresh",
        "partial",
        "delayed",
        "stale",
        "closed",
        "source_error",
        "unavailable",
    ]
    status_label: str
    status_message: str
    session: str
    session_label: str
    session_open: bool
    refresh_interval_seconds: int
    fresh_threshold_seconds: int
    stale_threshold_seconds: int
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    latest_quote_at: datetime | None
    quote_age_seconds: int | None
    updated_count: int
    available_count: int
    fresh_count: int
    total_count: int
    coverage_percent: Decimal
    fresh_coverage_percent: Decimal
    round_coverage_percent: Decimal
    healthy_coverage_threshold_percent: Decimal
    batch_total_count: int
    batch_success_count: int
    batch_failed_count: int
    batch_retry_count: int
    batch_recovered_count: int
    batch_request_attempts: int
    batch_pause_count: int
    batch_fallback_used: bool
    batch_success_percent: Decimal
    batch_elapsed_seconds: float
    last_error: str | None
    observed_at: datetime


class MarketIndexOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    exchange: str
    price: Decimal
    change: Decimal
    change_percent: Decimal
    turnover: Decimal
    updated_at: datetime


class OrderCreate(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, le=1_000_000)

    @field_validator("quantity")
    @classmethod
    def validate_lot_size(cls, value: int) -> int:
        if value % 100 != 0:
            raise ValueError("第一版买卖数量必须是100股的整数倍")
        return value


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_no: str
    symbol: str
    stock_name: str
    side: str
    order_type: str
    quantity: int
    filled_quantity: int
    price: Decimal
    fee: Decimal
    status: str
    reject_reason: str | None
    created_at: datetime


class PositionOut(BaseModel):
    symbol: str
    stock_name: str
    quantity: int
    average_cost: Decimal
    current_price: Decimal
    market_value: Decimal
    profit_loss: Decimal
    profit_loss_percent: Decimal


class AccountOut(BaseModel):
    username: str
    initial_cash: Decimal
    available_cash: Decimal
    market_value: Decimal
    total_assets: Decimal
    total_profit_loss: Decimal
    total_return_percent: Decimal


class TradeOut(BaseModel):
    trade_no: str
    order_no: str
    symbol: str
    stock_name: str
    side: str
    quantity: int
    price: Decimal
    amount: Decimal
    fee: Decimal
    created_at: datetime

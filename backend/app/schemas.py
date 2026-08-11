from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=10, max_length=128)
    invite_code: str = Field(min_length=16, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or not all(
            character.isalnum() or character == "_" for character in normalized
        ):
            raise ValueError("用户名只能包含中文、字母、数字和下划线")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not any(character.isalpha() for character in value) or not any(
            character.isdigit() for character in value
        ):
            raise ValueError("密码必须同时包含字母和数字")
        return value

    @field_validator("invite_code")
    @classmethod
    def normalize_invite_code(cls, value: str) -> str:
        return value.strip()


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class CurrentUserOut(BaseModel):
    username: str
    initial_cash: Decimal
    created_at: datetime


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


class StockBarOut(BaseModel):
    trade_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int
    turnover: Decimal


class StockHistoryOut(BaseModel):
    symbol: str
    period: Literal["DAY"] = "DAY"
    source: Literal["EASTMONEY_HISTORY", "CACHED_HISTORY", "LATEST_SNAPSHOT"]
    cached_count: int
    target_count: int
    complete: bool
    bars: list[StockBarOut]


class FinancialReportOut(BaseModel):
    report_period: date
    report_type: Literal["Q1", "H1", "Q3", "ANNUAL", "OTHER"]
    report_name: str
    announcement_date: date
    revenue: Decimal | None
    net_profit_parent: Decimal | None
    basic_eps: Decimal | None
    deducted_eps: Decimal | None
    weighted_roe: Decimal | None
    gross_margin: Decimal | None
    revenue_yoy: Decimal | None
    net_profit_yoy: Decimal | None
    book_value_per_share: Decimal | None
    operating_cash_flow_per_share: Decimal | None
    source_url: str


class PerformanceEventOut(BaseModel):
    event_type: Literal["FORECAST", "REPORT"]
    event_label: str
    report_period: date
    report_name: str
    announcement_date: date
    forecast_type: str | None
    revenue_lower: Decimal | None
    revenue_upper: Decimal | None
    revenue_growth_lower: Decimal | None
    revenue_growth_upper: Decimal | None
    net_profit_lower: Decimal | None
    net_profit_upper: Decimal | None
    net_profit_growth_lower: Decimal | None
    net_profit_growth_upper: Decimal | None
    summary: str | None
    reason: str | None
    source_url: str


class StockFundamentalsOut(BaseModel):
    symbol: str
    stock_name: str
    available: bool
    provider: Literal["EASTMONEY"]
    provider_label: str
    cache_status: Literal["REFRESHED", "CACHED", "STALE"]
    fetched_at: datetime
    latest_report: FinancialReportOut
    reports: list[FinancialReportOut]
    events: list[PerformanceEventOut]


class CompanyAnnouncementOut(BaseModel):
    external_id: str
    title: str
    announcement_date: date
    announcement_heading: str | None
    announcement_type: str | None
    exchange: Literal["SSE", "SZSE"]
    source_url: str
    source_published_at: datetime | None


class StockAnnouncementsOut(BaseModel):
    symbol: str
    stock_name: str
    provider: Literal["SSE", "SZSE"]
    provider_label: str
    cache_status: Literal["REFRESHED", "CACHED", "STALE"]
    fetched_at: datetime
    range_start: date
    range_end: date
    announcements: list[CompanyAnnouncementOut]


class RegulatoryLetterReplyOut(BaseModel):
    external_id: str
    title: str
    reply_date: date | None
    source_url: str
    match_method: Literal["SOURCE", "TITLE_DATE"]


class RegulatoryLetterOut(BaseModel):
    external_id: str
    title: str
    letter_type: str
    issued_date: date
    exchange: Literal["SSE", "SZSE"]
    source_url: str
    reply_status: Literal["REPLIED", "NO_REPLY_FOUND"]
    replies: list[RegulatoryLetterReplyOut]


class StockRegulatoryLettersOut(BaseModel):
    symbol: str
    stock_name: str
    provider: Literal["SSE", "SZSE"]
    provider_label: str
    cache_status: Literal["REFRESHED", "CACHED", "STALE"]
    fetched_at: datetime
    range_start: date
    range_end: date
    letters: list[RegulatoryLetterOut]


class WatchlistItemOut(BaseModel):
    symbol: str
    created_at: datetime


class OrderCreate(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    quantity: int = Field(gt=0, le=1_000_000)

    @field_validator("quantity")
    @classmethod
    def validate_lot_size(cls, value: int) -> int:
        if value % 100 != 0:
            raise ValueError("第一版买卖数量必须是100股的整数倍")
        return value

    @model_validator(mode="after")
    def validate_limit_order(self):
        if self.order_type == "LIMIT" and self.limit_price is None:
            raise ValueError("限价委托必须填写委托价格")
        if self.order_type == "MARKET":
            self.limit_price = None
        return self


class OrderPreviewOut(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    quantity: int
    reference_price: Decimal
    limit_price: Decimal | None
    estimated_amount: Decimal
    estimated_fee: Decimal
    estimated_total: Decimal
    max_quantity: int
    position_quantity: int
    sellable_quantity: int
    frozen_sell_quantity: int
    post_available_cash: Decimal
    post_position_ratio: Decimal
    price_limit_rate: Decimal
    upper_limit: Decimal
    lower_limit: Decimal
    quote_updated_at: datetime
    allowed: bool
    blocking_reason: str | None
    warnings: list[str]


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_no: str
    symbol: str
    stock_name: str
    side: str
    order_type: str
    limit_price: Decimal | None
    quantity: int
    filled_quantity: int
    price: Decimal
    fee: Decimal
    status: str
    reject_reason: str | None
    cancelable: bool
    created_at: datetime


class PositionOut(BaseModel):
    symbol: str
    stock_name: str
    quantity: int
    sellable_quantity: int
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


class StockAnalysisCreate(BaseModel):
    question: str = Field(
        default="请结合公开证据分析这只股票的主要风险和需要跟踪的条件。",
        min_length=1,
        max_length=1000,
    )
    focus_keywords: str = Field(
        default="风险 业绩 减持 诉讼 担保 处罚 重大事项",
        min_length=1,
        max_length=80,
    )
    history_days: int = Field(default=120, ge=60, le=240)
    disclosure_days: int = Field(default=365, ge=30, le=1095)
    news_days: int = Field(default=30, ge=1, le=365)
    max_documents: int = Field(default=3, ge=0, le=3)

    @field_validator("question", "focus_keywords")
    @classmethod
    def normalize_analysis_text(cls, value: str) -> str:
        return " ".join(value.strip().split())


class AgentEvidenceOut(BaseModel):
    evidence_key: str
    tool_name: str
    evidence_type: str
    title: str | None
    source_url: str | None
    source_date: date | None
    source_published_at: datetime | None
    page_number: int | None
    start_char: int | None
    end_char: int | None
    excerpt: str
    content_hash: str | None
    trust_level: str
    metadata: dict


class AgentRecommendationOut(BaseModel):
    risk_level: str
    confidence: Decimal
    action: str
    time_horizon: str | None
    summary: str
    reasoning: str
    positive_factors: list[str]
    risk_factors: list[str]
    action_conditions: list[str]
    invalidation_conditions: list[str]
    evidence_keys: list[str]
    disclaimer: str
    output_hash: str


class AgentRunOut(BaseModel):
    id: int
    run_type: str
    status: str
    symbol: str | None
    stock_name: str | None
    model_provider: str
    model_name: str
    request_text: str | None
    data_as_of: datetime | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    evidence: list[AgentEvidenceOut]
    recommendation: AgentRecommendationOut | None

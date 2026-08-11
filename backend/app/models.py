from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """Return naive UTC for SQLite without using deprecated datetime.utcnow()."""
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    account: Mapped["SimulationAccount"] = relationship(back_populates="user")
    sessions: Mapped[list["AuthSession"]] = relationship(back_populates="user")


class AuthSession(Base):
    """Server-side website session; the raw token is only stored in a cookie."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user: Mapped[User] = relationship(back_populates="sessions")


class RateLimitCounter(Base):
    """Persistent fixed-window counters shared by every API process."""

    __tablename__ = "rate_limit_counters"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "identity_hash",
            "window_start",
            name="uq_rate_limit_scope_identity_window",
        ),
        Index("ix_rate_limit_counter_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(64))
    identity_hash: Mapped[str] = mapped_column(String(64))
    window_start: Mapped[datetime] = mapped_column(DateTime)
    count: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
    )


class RateLimitLease(Base):
    """Short-lived distributed leases used to bound expensive concurrency."""

    __tablename__ = "rate_limit_leases"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "identity_hash",
            name="uq_rate_limit_lease_scope_identity",
        ),
        Index("ix_rate_limit_lease_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(64))
    identity_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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
    volume: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StockBar(Base):
    """Cached daily bars fetched from the public quote provider."""

    __tablename__ = "stock_bars"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_stock_bar_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    high_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    low_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    close_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    volume: Mapped[int] = mapped_column(BigInteger, default=0)
    turnover: Mapped[Decimal] = mapped_column(Numeric(22, 2), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stock: Mapped[Stock] = relationship()


class FinancialReport(Base):
    """Versioned, normalized financial summary for one reporting period."""

    __tablename__ = "financial_reports"
    __table_args__ = (
        UniqueConstraint(
            "stock_id",
            "report_period",
            "statement_scope",
            "version",
            name="uq_financial_report_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    report_period: Mapped[date] = mapped_column(Date, index=True)
    report_type: Mapped[str] = mapped_column(String(16))
    report_name: Mapped[str] = mapped_column(String(40))
    announcement_date: Mapped[date] = mapped_column(Date, index=True)
    statement_scope: Mapped[str] = mapped_column(String(16), default="CONSOLIDATED")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)

    revenue: Mapped[Decimal | None] = mapped_column(Numeric(22, 2), nullable=True)
    net_profit_parent: Mapped[Decimal | None] = mapped_column(
        Numeric(22, 2), nullable=True
    )
    basic_eps: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    deducted_eps: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4), nullable=True
    )
    weighted_roe: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    gross_margin: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    revenue_yoy: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    net_profit_yoy: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    book_value_per_share: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4), nullable=True
    )
    operating_cash_flow_per_share: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4), nullable=True
    )

    source: Mapped[str] = mapped_column(String(32), default="EASTMONEY")
    source_url: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    stock: Mapped[Stock] = relationship()


class PerformanceEvent(Base):
    """A normalized company performance forecast, kept separate from reports."""

    __tablename__ = "performance_events"
    __table_args__ = (
        UniqueConstraint(
            "stock_id",
            "event_type",
            "report_period",
            "announcement_date",
            name="uq_performance_event_period_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(16), default="FORECAST")
    report_period: Mapped[date] = mapped_column(Date, index=True)
    report_name: Mapped[str] = mapped_column(String(40))
    announcement_date: Mapped[date] = mapped_column(Date, index=True)
    forecast_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revenue_lower: Mapped[Decimal | None] = mapped_column(Numeric(22, 2), nullable=True)
    revenue_upper: Mapped[Decimal | None] = mapped_column(Numeric(22, 2), nullable=True)
    revenue_growth_lower: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    revenue_growth_upper: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    net_profit_lower: Mapped[Decimal | None] = mapped_column(
        Numeric(22, 2), nullable=True
    )
    net_profit_upper: Mapped[Decimal | None] = mapped_column(
        Numeric(22, 2), nullable=True
    )
    net_profit_growth_lower: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    net_profit_growth_upper: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="EASTMONEY")
    source_url: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    stock: Mapped[Stock] = relationship()


class CompanyAnnouncement(Base):
    """Official exchange announcement metadata; PDF text is extracted on demand."""

    __tablename__ = "company_announcements"
    __table_args__ = (
        UniqueConstraint(
            "stock_id",
            "exchange",
            "external_id",
            name="uq_company_announcement_stock_exchange_external_id",
        ),
        Index(
            "ix_company_announcement_stock_date",
            "stock_id",
            "announcement_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(8), default="SSE", index=True)
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(500))
    announcement_date: Mapped[date] = mapped_column(Date, index=True)
    announcement_heading: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    announcement_type: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    source_url: Mapped[str] = mapped_column(String(800))
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    stock: Mapped[Stock] = relationship()


class AnnouncementSyncState(Base):
    """Successful metadata coverage for one stock and exchange provider."""

    __tablename__ = "announcement_sync_states"
    __table_args__ = (
        UniqueConstraint(
            "stock_id",
            "exchange",
            name="uq_announcement_sync_stock_exchange",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(8), default="SSE")
    covered_from: Mapped[date] = mapped_column(Date)
    covered_to: Mapped[date] = mapped_column(Date)
    last_success_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stock: Mapped[Stock] = relationship()


class RegulatoryLetter(Base):
    """Official exchange inquiry or concern letter metadata."""

    __tablename__ = "regulatory_letters"
    __table_args__ = (
        UniqueConstraint(
            "stock_id",
            "exchange",
            "external_id",
            name="uq_regulatory_letter_stock_exchange_external_id",
        ),
        Index(
            "ix_regulatory_letter_stock_date",
            "stock_id",
            "issued_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(8), index=True)
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(500))
    letter_type: Mapped[str] = mapped_column(String(128))
    issued_date: Mapped[date] = mapped_column(Date, index=True)
    source_url: Mapped[str] = mapped_column(String(800))
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    stock: Mapped[Stock] = relationship()
    replies: Mapped[list["RegulatoryLetterReply"]] = relationship(
        back_populates="letter",
        cascade="all, delete-orphan",
    )


class RegulatoryLetterReply(Base):
    """Official or conservatively matched company reply to a regulatory letter."""

    __tablename__ = "regulatory_letter_replies"
    __table_args__ = (
        UniqueConstraint(
            "regulatory_letter_id",
            "external_id",
            name="uq_regulatory_reply_letter_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    regulatory_letter_id: Mapped[int] = mapped_column(
        ForeignKey("regulatory_letters.id"),
        index=True,
    )
    announcement_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_announcements.id"),
        nullable=True,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(500))
    reply_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_url: Mapped[str] = mapped_column(String(800))
    match_method: Mapped[str] = mapped_column(String(32), default="SOURCE")
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    letter: Mapped[RegulatoryLetter] = relationship(back_populates="replies")
    announcement: Mapped[CompanyAnnouncement | None] = relationship()


class RegulatorySyncState(Base):
    """Successful regulatory-letter coverage for one stock and exchange."""

    __tablename__ = "regulatory_sync_states"
    __table_args__ = (
        UniqueConstraint(
            "stock_id",
            "exchange",
            name="uq_regulatory_sync_stock_exchange",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(8))
    covered_from: Mapped[date] = mapped_column(Date)
    covered_to: Mapped[date] = mapped_column(Date)
    last_success_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stock: Mapped[Stock] = relationship()


class OfficialDocumentContent(Base):
    """Downloaded official PDF and its page-aware extracted text cache."""

    __tablename__ = "official_document_contents"
    __table_args__ = (
        UniqueConstraint(
            "document_type",
            "document_id",
            name="uq_official_document_type_id",
        ),
        Index(
            "ix_official_document_stock_type",
            "stock_id",
            "document_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[int] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(500))
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_url: Mapped[str] = mapped_column(String(800))
    source_content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="READY", index=True)
    mime_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    file_size: Mapped[int] = mapped_column(BigInteger)
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int]
    text_content: Mapped[str] = mapped_column(
        Text().with_variant(LONGTEXT(), "mysql")
    )
    page_offsets: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    text_sha256: Mapped[str] = mapped_column(String(64))
    extraction_warning: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stock: Mapped[Stock] = relationship()
    chunks: Mapped[list["OfficialDocumentChunk"]] = relationship(
        back_populates="content",
        cascade="all, delete-orphan",
    )


class OfficialDocumentChunk(Base):
    """Page-bound evidence segment derived from official PDF text."""

    __tablename__ = "official_document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "official_document_content_id",
            "chunk_index",
            name="uq_official_document_chunk_content_index",
        ),
        Index(
            "ix_official_document_chunk_stock_type",
            "stock_id",
            "document_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    official_document_content_id: Mapped[int] = mapped_column(
        ForeignKey("official_document_contents.id", ondelete="CASCADE"),
        index=True,
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(32), index=True)
    page_number: Mapped[int] = mapped_column(index=True)
    chunk_index: Mapped[int]
    start_char: Mapped[int]
    end_char: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    content: Mapped[OfficialDocumentContent] = relationship(back_populates="chunks")
    stock: Mapped[Stock] = relationship()


class AgentRun(Base):
    """One auditable AI analysis attempt, independent from trading orders."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "run_type IN ('STOCK_ANALYSIS', 'PORTFOLIO_ANALYSIS')",
            name="ck_agent_run_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_agent_run_status",
        ),
        CheckConstraint(
            "run_type <> 'STOCK_ANALYSIS' OR stock_id IS NOT NULL",
            name="ck_agent_run_stock_scope",
        ),
        CheckConstraint(
            "run_type <> 'PORTFOLIO_ANALYSIS' OR account_id IS NOT NULL",
            name="ck_agent_run_portfolio_scope",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="ck_agent_run_tokens_nonnegative",
        ),
        Index("ix_agent_run_user_created", "user_id", "created_at"),
        Index("ix_agent_run_stock_created", "stock_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("simulation_accounts.id"),
        nullable=True,
        index=True,
    )
    stock_id: Mapped[int | None] = mapped_column(
        ForeignKey("stocks.id"),
        nullable=True,
        index=True,
    )
    run_type: Mapped[str] = mapped_column(String(32), default="STOCK_ANALYSIS")
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    model_provider: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(100))
    request_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
    )
    user: Mapped[User] = relationship()
    account: Mapped[SimulationAccount | None] = relationship()
    stock: Mapped[Stock | None] = relationship()
    evidence: Mapped[list["AgentEvidence"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    recommendation: Mapped["AgentRecommendation | None"] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
    )


class AgentEvidence(Base):
    """A source fragment actually used by one completed Agent run."""

    __tablename__ = "agent_evidence"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "evidence_key",
            name="uq_agent_evidence_run_key",
        ),
        CheckConstraint(
            "page_number IS NULL OR page_number > 0",
            name="ck_agent_evidence_page_positive",
        ),
        CheckConstraint(
            "start_char IS NULL OR start_char >= 0",
            name="ck_agent_evidence_start_nonnegative",
        ),
        CheckConstraint(
            "end_char IS NULL OR end_char >= 0",
            name="ck_agent_evidence_end_nonnegative",
        ),
        CheckConstraint(
            "start_char IS NULL OR end_char IS NULL OR end_char >= start_char",
            name="ck_agent_evidence_range",
        ),
        Index("ix_agent_evidence_run_tool", "run_id", "tool_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        index=True,
    )
    evidence_key: Mapped[str] = mapped_column(String(191))
    tool_name: Mapped[str] = mapped_column(String(64))
    evidence_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    source_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    page_number: Mapped[int | None] = mapped_column(nullable=True)
    start_char: Mapped[int | None] = mapped_column(nullable=True)
    end_char: Mapped[int | None] = mapped_column(nullable=True)
    excerpt: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trust_level: Mapped[str] = mapped_column(
        String(32),
        default="UNTRUSTED_SOURCE_CONTENT",
    )
    details: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    run: Mapped[AgentRun] = relationship(back_populates="evidence")


class AgentRecommendation(Base):
    """One structured, evidence-linked recommendation for an Agent run."""

    __tablename__ = "agent_recommendations"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_agent_recommendation_run"),
        CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'UNKNOWN')",
            name="ck_agent_recommendation_risk_level",
        ),
        CheckConstraint(
            "action IN ('WATCH', 'HOLD', 'AVOID', 'REDUCE', "
            "'CONSIDER_ADD', 'NO_CONCLUSION')",
            name="ck_agent_recommendation_action",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_agent_recommendation_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
    )
    risk_level: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        default=Decimal("0"),
    )
    action: Mapped[str] = mapped_column(String(32), default="NO_CONCLUSION")
    time_horizon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))
    positive_factors: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_factors: Mapped[list[str]] = mapped_column(JSON, default=list)
    action_conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
    invalidation_conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    disclaimer: Mapped[str] = mapped_column(
        String(500),
        default="仅供A股模拟交易学习使用，不构成投资建议。",
    )
    output_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    run: Mapped[AgentRun] = relationship(back_populates="recommendation")


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


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("account_id", "stock_id", name="uq_watchlist_account_stock"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("simulation_accounts.id"), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stock: Mapped[Stock] = relationship()


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "idempotency_key",
            name="uq_order_account_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("simulation_accounts.id"), index=True)
    # Nullable only so databases created before idempotency support can be
    # upgraded without inventing keys for historical orders.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(10), default="MARKET")
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
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

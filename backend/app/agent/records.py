"""Validated persistence helpers for auditable Agent runs and conclusions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AgentEvidence,
    AgentRecommendation,
    AgentRun,
    SimulationAccount,
    Stock,
    User,
    utcnow,
)
from .tools import READONLY_TOOL_NAMES


AgentRunType = Literal["STOCK_ANALYSIS", "PORTFOLIO_ANALYSIS"]
AgentRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
AgentAction = Literal[
    "WATCH",
    "HOLD",
    "AVOID",
    "REDUCE",
    "CONSIDER_ADD",
    "NO_CONCLUSION",
]
AgentTrustLevel = Literal[
    "UNTRUSTED_SOURCE_CONTENT",
    "UNTRUSTED_WEB_CONTENT",
    "MONIPAN_CALCULATED_DATA",
    "MONIPAN_MARKET_CACHE",
]


class AgentPersistenceError(RuntimeError):
    pass


class AgentTokenUsage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def fill_total(self):
        minimum_total = self.input_tokens + self.output_tokens
        if self.total_tokens is None:
            self.total_tokens = minimum_total
        elif self.total_tokens < minimum_total:
            raise ValueError("total_tokens不能小于input_tokens与output_tokens之和")
        return self


class AgentEvidenceRecord(BaseModel):
    evidence_key: str = Field(min_length=1, max_length=191)
    tool_name: str = Field(min_length=1, max_length=64)
    evidence_type: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=1200)
    source_date: date | None = None
    source_published_at: datetime | None = None
    page_number: int | None = Field(default=None, ge=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    excerpt: str = Field(min_length=1, max_length=8000)
    content_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    trust_level: AgentTrustLevel
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_key", "evidence_type", "title", "excerpt")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in READONLY_TOOL_NAMES:
            raise ValueError("证据必须来自已注册的只读工具")
        return normalized

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("证据URL必须是有效的HTTP或HTTPS地址")
        return normalized

    @model_validator(mode="after")
    def validate_character_range(self):
        if (
            self.start_char is not None
            and self.end_char is not None
            and self.end_char < self.start_char
        ):
            raise ValueError("end_char不能小于start_char")
        return self


class AgentRecommendationRecord(BaseModel):
    risk_level: AgentRiskLevel
    confidence: Decimal = Field(ge=0, le=1, decimal_places=4)
    action: AgentAction
    time_horizon: str | None = Field(default=None, max_length=32)
    summary: str = Field(min_length=1, max_length=2000)
    reasoning: str = Field(min_length=1, max_length=30000)
    positive_factors: list[str] = Field(default_factory=list, max_length=20)
    risk_factors: list[str] = Field(default_factory=list, max_length=20)
    action_conditions: list[str] = Field(default_factory=list, max_length=20)
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=20)
    evidence_keys: list[str] = Field(default_factory=list, max_length=50)
    disclaimer: str = Field(
        default="仅供A股模拟交易学习使用，不构成投资建议。",
        min_length=1,
        max_length=500,
    )

    @field_validator(
        "time_horizon",
        "summary",
        "reasoning",
        "disclaimer",
    )
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "positive_factors",
        "risk_factors",
        "action_conditions",
        "invalidation_conditions",
        "evidence_keys",
    )
    @classmethod
    def normalize_lists(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("列表中不能包含重复项")
        return normalized


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _recommendation_hash(record: AgentRecommendationRecord) -> str:
    canonical = json.dumps(
        jsonable_encoder(record.model_dump()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def start_agent_run(
    db: Session,
    *,
    user_id: int,
    model_provider: str,
    model_name: str,
    run_type: AgentRunType = "STOCK_ANALYSIS",
    stock_id: int | None = None,
    account_id: int | None = None,
    request_text: str | None = None,
) -> AgentRun:
    """Start a run only after validating user, stock and account ownership."""
    if db.scalar(select(User.id).where(User.id == user_id)) is None:
        raise AgentPersistenceError("用户不存在")
    if run_type == "STOCK_ANALYSIS":
        if stock_id is None:
            raise AgentPersistenceError("单股分析必须绑定股票")
        if db.scalar(select(Stock.id).where(Stock.id == stock_id)) is None:
            raise AgentPersistenceError("股票不存在")
    if run_type == "PORTFOLIO_ANALYSIS" and account_id is None:
        raise AgentPersistenceError("组合分析必须绑定模拟账户")
    if account_id is not None:
        owned_account = db.scalar(
            select(SimulationAccount.id).where(
                SimulationAccount.id == account_id,
                SimulationAccount.user_id == user_id,
            )
        )
        if owned_account is None:
            raise AgentPersistenceError("模拟账户不属于当前用户")

    provider = model_provider.strip()
    model = model_name.strip()
    if not provider or len(provider) > 32:
        raise AgentPersistenceError("模型提供方不能为空且不能超过32字符")
    if not model or len(model) > 100:
        raise AgentPersistenceError("模型名称不能为空且不能超过100字符")
    normalized_request = request_text.strip() if request_text else None
    if normalized_request and len(normalized_request) > 8000:
        raise AgentPersistenceError("分析请求不能超过8000字符")

    now = utcnow()
    run = AgentRun(
        user_id=user_id,
        account_id=account_id,
        stock_id=stock_id,
        run_type=run_type,
        status="RUNNING",
        model_provider=provider,
        model_name=model,
        request_text=normalized_request,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def complete_agent_run(
    db: Session,
    run_id: int,
    *,
    data_as_of: datetime,
    evidence: list[AgentEvidenceRecord],
    recommendation: AgentRecommendationRecord,
    token_usage: AgentTokenUsage | None = None,
) -> AgentRun:
    """Atomically persist adopted evidence and one structured conclusion."""
    run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None:
        raise AgentPersistenceError("Agent运行记录不存在")
    if run.status != "RUNNING":
        raise AgentPersistenceError("只有RUNNING状态可以完成")
    if run.recommendation is not None or run.evidence:
        raise AgentPersistenceError("该运行记录已经保存过分析结果")

    evidence_keys = [item.evidence_key for item in evidence]
    if len(evidence_keys) != len(set(evidence_keys)):
        raise AgentPersistenceError("同一次分析不能保存重复证据")
    if recommendation.action != "NO_CONCLUSION" and not evidence:
        raise AgentPersistenceError("有操作建议时必须至少关联一条证据")
    if set(recommendation.evidence_keys) != set(evidence_keys):
        raise AgentPersistenceError("建议引用必须与本次实际采用的证据完全一致")

    for item in evidence:
        db.add(
            AgentEvidence(
                run_id=run.id,
                evidence_key=item.evidence_key,
                tool_name=item.tool_name,
                evidence_type=item.evidence_type,
                title=item.title,
                source_url=item.source_url,
                source_date=item.source_date,
                source_published_at=_naive_utc(item.source_published_at),
                page_number=item.page_number,
                start_char=item.start_char,
                end_char=item.end_char,
                excerpt=item.excerpt,
                content_hash=(
                    item.content_hash.lower() if item.content_hash else None
                ),
                trust_level=item.trust_level,
                details=item.metadata,
            )
        )
    db.add(
        AgentRecommendation(
            run_id=run.id,
            risk_level=recommendation.risk_level,
            confidence=recommendation.confidence,
            action=recommendation.action,
            time_horizon=recommendation.time_horizon,
            summary=recommendation.summary,
            reasoning=recommendation.reasoning,
            positive_factors=recommendation.positive_factors,
            risk_factors=recommendation.risk_factors,
            action_conditions=recommendation.action_conditions,
            invalidation_conditions=recommendation.invalidation_conditions,
            evidence_keys=recommendation.evidence_keys,
            disclaimer=recommendation.disclaimer,
            output_hash=_recommendation_hash(recommendation),
        )
    )
    usage = token_usage or AgentTokenUsage()
    run.input_tokens = usage.input_tokens
    run.output_tokens = usage.output_tokens
    run.total_tokens = usage.total_tokens or 0
    run.data_as_of = _naive_utc(data_as_of)
    run.status = "COMPLETED"
    run.completed_at = utcnow()
    run.updated_at = run.completed_at
    run.error_code = None
    run.error_message = None
    db.commit()
    db.refresh(run)
    return run


def fail_agent_run(
    db: Session,
    run_id: int,
    *,
    error_code: str,
    error_message: str,
    token_usage: AgentTokenUsage | None = None,
) -> AgentRun:
    """Finish a pending/running run as failed without fabricating a recommendation."""
    run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None:
        raise AgentPersistenceError("Agent运行记录不存在")
    if run.status not in {"PENDING", "RUNNING"}:
        raise AgentPersistenceError("当前状态不能标记为失败")
    code = error_code.strip()
    message = error_message.strip()
    if not code or len(code) > 64:
        raise AgentPersistenceError("错误代码不能为空且不能超过64字符")
    if not message or len(message) > 2000:
        raise AgentPersistenceError("错误信息不能为空且不能超过2000字符")

    usage = token_usage or AgentTokenUsage()
    run.input_tokens = usage.input_tokens
    run.output_tokens = usage.output_tokens
    run.total_tokens = usage.total_tokens or 0
    run.status = "FAILED"
    run.error_code = code
    run.error_message = message
    run.completed_at = utcnow()
    run.updated_at = run.completed_at
    db.commit()
    db.refresh(run)
    return run

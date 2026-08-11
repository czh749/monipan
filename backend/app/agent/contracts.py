"""Shared input schemas and structured result envelope for LangChain tools."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator


ToolStatus = Literal[
    "OK",
    "PARTIAL",
    "NO_DATA",
    "NOT_FOUND",
    "UNAVAILABLE",
    "FORBIDDEN",
]


class SymbolInput(BaseModel):
    symbol: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="六位A股代码，例如 600519。",
    )

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: Any) -> str:
        return str(value).strip()


class StockHistoryInput(SymbolInput):
    limit: int = Field(
        default=90,
        ge=20,
        le=240,
        description="返回最近多少根日K，范围20到240。",
    )


class EvidenceSearchInput(SymbolInput):
    query: str = Field(
        min_length=1,
        max_length=100,
        description="检索关键词；多个关键词使用空格分隔，不要传入整段自然语言。",
    )
    days: int = Field(default=365, ge=30, le=1095, description="向前检索天数。")
    limit: int = Field(default=8, ge=1, le=20, description="最多返回的证据片段数。")
    max_documents: int = Field(
        default=3,
        ge=0,
        le=3,
        description="本次最多新增下载并抽取的PDF数量；0表示只查已有正文缓存。",
    )

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.strip().split())


class RegulatoryEvidenceSearchInput(EvidenceSearchInput):
    include_replies: bool = Field(
        default=True,
        description="是否同时检索监管函对应的公司回复。",
    )


class StockNewsSearchInput(SymbolInput):
    query: str = Field(
        default="最新动态",
        min_length=1,
        max_length=80,
        description="需要了解的新闻主题，例如 减持、诉讼、行业政策；不要重复股票名称。",
    )
    days: int = Field(default=30, ge=1, le=365, description="向前搜索新闻的天数。")
    limit: int = Field(default=8, ge=1, le=10, description="最多返回的新闻结果数。")

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.strip().split())


class GetPortfolioInput(BaseModel):
    pass


class PortfolioRiskInput(BaseModel):
    history_days: int = Field(
        default=120,
        ge=60,
        le=240,
        description="用于波动率和回撤计算的日K范围。",
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


def tool_result(
    tool: str,
    status: ToolStatus,
    *,
    data: Any = None,
    evidence: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    as_of: datetime | date | None = None,
) -> dict[str, Any]:
    """Return one predictable JSON-safe result for model and audit storage."""
    return jsonable_encoder(
        {
            "tool": tool,
            "status": status,
            "as_of": as_of or utcnow(),
            "data": data if data is not None else {},
            "evidence": evidence or [],
            "warnings": warnings or [],
        }
    )

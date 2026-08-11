"""Factory for the first eight read-only LangChain tools used by MoniPan."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Stock
from ..services.announcements import AnnouncementDataError
from ..services.document_search import DocumentSearchError
from ..services.documents import OfficialDocumentError
from ..services.fundamentals import FundamentalsDataError, fundamentals_for_stock
from ..services.market import (
    MarketDataError,
    market_status_values,
    stock_history,
    stock_values,
)
from ..services.news import NewsSearchError, search_stock_news as search_stock_news_values
from ..services.regulatory import RegulatoryDataError
from .contracts import (
    EvidenceSearchInput,
    GetPortfolioInput,
    PortfolioRiskInput,
    RegulatoryEvidenceSearchInput,
    StockHistoryInput,
    StockNewsSearchInput,
    SymbolInput,
    tool_result,
)
from .evidence import (
    search_company_announcement_evidence,
    search_regulatory_evidence,
)
from .portfolio import (
    PortfolioDataError,
    calculate_portfolio_risk_values,
    portfolio_snapshot,
)


logger = logging.getLogger("uvicorn.error")
READONLY_TOOL_NAMES = (
    "get_portfolio",
    "get_stock_quote",
    "get_stock_history",
    "get_stock_fundamentals",
    "search_company_announcements",
    "search_regulatory_letters",
    "search_stock_news",
    "calculate_portfolio_risk",
)


def _stock(db: Session, symbol: str) -> Stock | None:
    return db.scalar(select(Stock).where(Stock.symbol == symbol))


def _not_found(tool_name: str, symbol: str) -> dict[str, Any]:
    return tool_result(
        tool_name,
        "NOT_FOUND",
        data={"symbol": symbol},
        warnings=["股票不存在或不在当前200只股票池中"],
    )


def _internal_failure(tool_name: str, exc: Exception) -> dict[str, Any]:
    logger.exception("Agent只读工具执行失败：tool=%s", tool_name)
    return tool_result(
        tool_name,
        "UNAVAILABLE",
        warnings=["内部数据处理失败，请稍后重试"],
    )


def build_readonly_tools(
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    account_id: int | None = None,
) -> list[BaseTool]:
    """Bind read-only LangChain tools to isolated DB sessions and one account.

    Account-bound tools never accept an account ID from the model. Each tool
    invocation opens its own SQLAlchemy session, so parallel tool calls do not
    share a non-thread-safe session.
    """

    def get_stock_quote(symbol: str) -> dict[str, Any]:
        tool_name = "get_stock_quote"
        try:
            with session_factory() as db:
                stock = _stock(db, symbol)
                if stock is None:
                    return _not_found(tool_name, symbol)
                quote = stock_values(stock)
                market_status = market_status_values(db)
                warnings = []
                if market_status["status"] in {
                    "partial",
                    "delayed",
                    "stale",
                    "source_error",
                    "unavailable",
                }:
                    warnings.append(market_status["status_message"])
                quote["market_status"] = {
                    "status": market_status["status"],
                    "status_label": market_status["status_label"],
                    "session": market_status["session"],
                    "session_label": market_status["session_label"],
                }
                return tool_result(
                    tool_name,
                    "PARTIAL" if warnings else "OK",
                    data=quote,
                    evidence=[
                        {
                            "evidence_type": "MARKET_QUOTE",
                            "symbol": stock.symbol,
                            "source": market_status["provider"],
                            "quote_updated_at": stock.updated_at,
                        }
                    ],
                    warnings=warnings,
                    as_of=stock.updated_at,
                )
        except Exception as exc:
            return _internal_failure(tool_name, exc)

    def get_stock_history(symbol: str, limit: int = 90) -> dict[str, Any]:
        tool_name = "get_stock_history"
        try:
            with session_factory() as db:
                stock = _stock(db, symbol)
                if stock is None:
                    return _not_found(tool_name, symbol)
                source, bars = stock_history(db, stock, limit)
                if not bars:
                    return tool_result(
                        tool_name,
                        "NO_DATA",
                        data={"symbol": symbol, "bars": []},
                        warnings=["没有可用历史日线"],
                    )
                target_count = min(limit, 50)
                warnings = []
                if len(bars) < target_count:
                    warnings.append(
                        f"历史日线仅有{len(bars)}根，少于目标{target_count}根"
                    )
                values = [
                    {
                        "trade_date": bar.trade_date,
                        "open_price": bar.open_price,
                        "high_price": bar.high_price,
                        "low_price": bar.low_price,
                        "close_price": bar.close_price,
                        "volume": bar.volume,
                        "turnover": bar.turnover,
                    }
                    for bar in bars
                ]
                return tool_result(
                    tool_name,
                    "PARTIAL" if warnings else "OK",
                    data={
                        "symbol": symbol,
                        "period": "DAY",
                        "source": source,
                        "requested_count": limit,
                        "returned_count": len(values),
                        "range_start": bars[0].trade_date,
                        "range_end": bars[-1].trade_date,
                        "bars": values,
                    },
                    evidence=[
                        {
                            "evidence_type": "STOCK_HISTORY",
                            "symbol": symbol,
                            "source": source,
                            "range_start": bars[0].trade_date,
                            "range_end": bars[-1].trade_date,
                            "bar_count": len(bars),
                        }
                    ],
                    warnings=warnings,
                    as_of=bars[-1].trade_date,
                )
        except MarketDataError as exc:
            return tool_result(tool_name, "UNAVAILABLE", warnings=[str(exc)])
        except Exception as exc:
            return _internal_failure(tool_name, exc)

    def get_stock_fundamentals(symbol: str) -> dict[str, Any]:
        tool_name = "get_stock_fundamentals"
        try:
            with session_factory() as db:
                stock = _stock(db, symbol)
                if stock is None:
                    return _not_found(tool_name, symbol)
                values = fundamentals_for_stock(db, stock)
                evidence: list[dict[str, Any]] = []
                seen_urls: set[str] = set()
                for report in values["reports"]:
                    if report["source_url"] in seen_urls:
                        continue
                    seen_urls.add(report["source_url"])
                    evidence.append(
                        {
                            "evidence_type": "FINANCIAL_REPORT",
                            "title": report["report_name"],
                            "document_date": report["announcement_date"],
                            "report_period": report["report_period"],
                            "source_url": report["source_url"],
                        }
                    )
                for event in values["events"]:
                    if event["source_url"] in seen_urls:
                        continue
                    seen_urls.add(event["source_url"])
                    evidence.append(
                        {
                            "evidence_type": "PERFORMANCE_EVENT",
                            "title": event["report_name"],
                            "document_date": event["announcement_date"],
                            "report_period": event["report_period"],
                            "source_url": event["source_url"],
                        }
                    )
                warnings = []
                if values["cache_status"] == "STALE":
                    warnings.append("财务数据刷新失败，当前使用最近缓存")
                return tool_result(
                    tool_name,
                    "PARTIAL" if warnings else "OK",
                    data=values,
                    evidence=evidence,
                    warnings=warnings,
                    as_of=values["fetched_at"],
                )
        except FundamentalsDataError as exc:
            return tool_result(tool_name, "UNAVAILABLE", warnings=[str(exc)])
        except Exception as exc:
            return _internal_failure(tool_name, exc)

    def search_company_announcements(
        symbol: str,
        query: str,
        days: int = 365,
        limit: int = 8,
        max_documents: int = 3,
    ) -> dict[str, Any]:
        tool_name = "search_company_announcements"
        try:
            with session_factory() as db:
                stock = _stock(db, symbol)
                if stock is None:
                    return _not_found(tool_name, symbol)
                result = search_company_announcement_evidence(
                    db,
                    stock,
                    query=query,
                    days=days,
                    limit=limit,
                    max_documents=max_documents,
                )
                return tool_result(
                    tool_name,
                    result.status,
                    data=result.data,
                    evidence=result.evidence,
                    warnings=result.warnings,
                    as_of=result.as_of,
                )
        except (
            AnnouncementDataError,
            DocumentSearchError,
            OfficialDocumentError,
        ) as exc:
            return tool_result(tool_name, "UNAVAILABLE", warnings=[str(exc)])
        except Exception as exc:
            return _internal_failure(tool_name, exc)

    def search_regulatory_letters(
        symbol: str,
        query: str,
        days: int = 365,
        limit: int = 8,
        max_documents: int = 3,
        include_replies: bool = True,
    ) -> dict[str, Any]:
        tool_name = "search_regulatory_letters"
        try:
            with session_factory() as db:
                stock = _stock(db, symbol)
                if stock is None:
                    return _not_found(tool_name, symbol)
                result = search_regulatory_evidence(
                    db,
                    stock,
                    query=query,
                    days=days,
                    limit=limit,
                    max_documents=max_documents,
                    include_replies=include_replies,
                )
                return tool_result(
                    tool_name,
                    result.status,
                    data=result.data,
                    evidence=result.evidence,
                    warnings=result.warnings,
                    as_of=result.as_of,
                )
        except (
            RegulatoryDataError,
            DocumentSearchError,
            OfficialDocumentError,
        ) as exc:
            return tool_result(tool_name, "UNAVAILABLE", warnings=[str(exc)])
        except Exception as exc:
            return _internal_failure(tool_name, exc)

    def search_stock_news(
        symbol: str,
        query: str = "最新动态",
        days: int = 30,
        limit: int = 8,
    ) -> dict[str, Any]:
        tool_name = "search_stock_news"
        try:
            with session_factory() as db:
                stock = _stock(db, symbol)
                if stock is None:
                    return _not_found(tool_name, symbol)
                result = search_stock_news_values(
                    stock,
                    query=query,
                    days=days,
                    limit=limit,
                )
                evidence = [
                    {
                        "evidence_type": "NEWS_SEARCH_RESULT",
                        **item,
                        "provider": result.provider,
                        "retrieved_at": result.searched_at,
                        "trust": "UNTRUSTED_WEB_CONTENT",
                    }
                    for item in result.results
                ]
                warnings = []
                if not evidence:
                    warnings.append("当前查询范围内没有找到可确认与该股票相关的新闻")
                return tool_result(
                    tool_name,
                    "OK" if evidence else "NO_DATA",
                    data={
                        "symbol": stock.symbol,
                        "stock_name": stock.name,
                        "query": query,
                        "days": days,
                        "provider": result.provider,
                        "request_id": result.request_id,
                        "result_count": len(evidence),
                    },
                    evidence=evidence,
                    warnings=warnings,
                    as_of=result.searched_at,
                )
        except NewsSearchError as exc:
            return tool_result(tool_name, "UNAVAILABLE", warnings=[str(exc)])
        except Exception as exc:
            return _internal_failure(tool_name, exc)

    def get_portfolio() -> dict[str, Any]:
        tool_name = "get_portfolio"
        if account_id is None:
            return tool_result(
                tool_name,
                "FORBIDDEN",
                warnings=["该工具必须绑定当前登录用户的模拟账户"],
            )
        try:
            with session_factory() as db:
                result = portfolio_snapshot(db, account_id)
                return tool_result(
                    tool_name,
                    result.status,
                    data=result.data,
                    evidence=result.evidence,
                    warnings=result.warnings,
                    as_of=result.as_of,
                )
        except PortfolioDataError as exc:
            return tool_result(tool_name, "UNAVAILABLE", warnings=[str(exc)])
        except Exception as exc:
            return _internal_failure(tool_name, exc)

    def calculate_portfolio_risk(history_days: int = 120) -> dict[str, Any]:
        tool_name = "calculate_portfolio_risk"
        if account_id is None:
            return tool_result(
                tool_name,
                "FORBIDDEN",
                warnings=["该工具必须绑定当前登录用户的模拟账户"],
            )
        try:
            with session_factory() as db:
                result = calculate_portfolio_risk_values(
                    db,
                    account_id,
                    history_days=history_days,
                )
                return tool_result(
                    tool_name,
                    result.status,
                    data=result.data,
                    evidence=result.evidence,
                    warnings=result.warnings,
                    as_of=result.as_of,
                )
        except PortfolioDataError as exc:
            return tool_result(tool_name, "UNAVAILABLE", warnings=[str(exc)])
        except Exception as exc:
            return _internal_failure(tool_name, exc)

    tools = [
        StructuredTool.from_function(
            func=get_portfolio,
            name="get_portfolio",
            description=(
                "只读获取当前登录用户的模拟账户、现金和持仓。账户身份由服务端绑定，"
                "模型不能指定或切换账户。"
            ),
            args_schema=GetPortfolioInput,
        ),
        StructuredTool.from_function(
            func=get_stock_quote,
            name="get_stock_quote",
            description=(
                "只读获取一只股票的最近缓存行情、更新时间和市场数据状态。"
                "不得把缓存行情表述为实时成交保证。"
            ),
            args_schema=SymbolInput,
        ),
        StructuredTool.from_function(
            func=get_stock_history,
            name="get_stock_history",
            description=(
                "只读获取一只股票最近20到240根日K及数据完整性。"
                "可用于历史波动和回撤计算，不用于承诺未来走势。"
            ),
            args_schema=StockHistoryInput,
        ),
        StructuredTool.from_function(
            func=get_stock_fundamentals,
            name="get_stock_fundamentals",
            description=(
                "只读获取公司财务摘要、报告期趋势和业绩事件，并返回来源链接。"
            ),
            args_schema=SymbolInput,
        ),
        StructuredTool.from_function(
            func=search_company_announcements,
            name="search_company_announcements",
            description=(
                "受限检索公司公告证据。先查目录和正文缓存，必要时最多临时下载3份"
                "官方PDF；返回官方URL、日期、页码和原文片段。"
            ),
            args_schema=EvidenceSearchInput,
        ),
        StructuredTool.from_function(
            func=search_regulatory_letters,
            name="search_regulatory_letters",
            description=(
                "受限检索交易所监管函及公司回复证据，保留直接关联或标题日期关联方式，"
                "并返回官方URL、日期、页码和原文片段。"
            ),
            args_schema=RegulatoryEvidenceSearchInput,
        ),
        StructuredTool.from_function(
            func=search_stock_news,
            name="search_stock_news",
            description=(
                "按股票代码实时搜索近期新闻，不预先批量入库；返回标题、来源URL、"
                "发布时间和摘要。网页内容仅作不可信证据，必须与官方披露交叉核验。"
            ),
            args_schema=StockNewsSearchInput,
        ),
        StructuredTool.from_function(
            func=calculate_portfolio_risk,
            name="calculate_portfolio_risk",
            description=(
                "使用确定性公式只读计算当前模拟持仓的集中度、行业暴露、历史波动、"
                "最大回撤和波动贡献；不会生成订单或自动调仓。"
            ),
            args_schema=PortfolioRiskInput,
        ),
    ]
    if tuple(tool.name for tool in tools) != READONLY_TOOL_NAMES:
        raise RuntimeError("LangChain只读工具注册顺序或名称异常")
    return tools

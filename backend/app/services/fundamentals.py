"""On-demand company fundamentals collection, normalization, and caching."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FinancialReport, PerformanceEvent, Stock


FINANCIAL_SUMMARY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
PERFORMANCE_FORECAST_URL = (
    "https://datacenter.eastmoney.com/securities/api/data/v1/get"
)
FUNDAMENTALS_CACHE_HOURS = int(
    os.getenv("MONIPAN_FUNDAMENTALS_CACHE_HOURS", "12")
)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
    "Connection": "close",
}


class FundamentalsDataError(RuntimeError):
    """Raised when no usable fundamentals can be obtained."""


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", "-"):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None


def _content_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _report_type(qdate: str | None, report_period: date) -> str:
    suffix = (qdate or "").upper().rsplit("Q", 1)[-1]
    by_quarter = {
        "1": "Q1",
        "2": "H1",
        "3": "Q3",
        "4": "ANNUAL",
    }
    if suffix in by_quarter:
        return by_quarter[suffix]
    return {
        3: "Q1",
        6: "H1",
        9: "Q3",
        12: "ANNUAL",
    }.get(report_period.month, "OTHER")


def _report_name(report_period: date, report_type: str) -> str:
    suffix = {
        "Q1": "一季报",
        "H1": "半年报",
        "Q3": "三季报",
        "ANNUAL": "年报",
    }.get(report_type, "定期报告")
    return f"{report_period.year}年{suffix}"


def _source_url(report_period: date, event: str) -> str:
    page = "yjyg" if event == "FORECAST" else "yjbb"
    return f"https://data.eastmoney.com/bbsj/{report_period:%Y%m}/{page}.html"


def _fetch_rows(url: str, params: dict[str, str]) -> list[dict[str, Any]]:
    try:
        response = requests.get(
            url,
            params=params,
            headers=REQUEST_HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise FundamentalsDataError(f"财务数据源请求失败：{exc}") from exc

    if not payload.get("success"):
        raise FundamentalsDataError(
            f"财务数据源返回异常：{payload.get('message') or '未知错误'}"
        )
    rows = (payload.get("result") or {}).get("data") or []
    if not isinstance(rows, list):
        raise FundamentalsDataError("财务数据源返回格式异常")
    return [row for row in rows if isinstance(row, dict)]


def fetch_eastmoney_financial_reports(symbol: str) -> list[dict[str, Any]]:
    rows = _fetch_rows(
        FINANCIAL_SUMMARY_URL,
        {
            "sortColumns": "REPORTDATE",
            "sortTypes": "-1",
            "pageSize": "12",
            "pageNumber": "1",
            "reportName": "RPT_LICO_FN_CPD",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{symbol}")',
        },
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        report_period = _date(row.get("REPORTDATE"))
        announcement_date = _date(row.get("NOTICE_DATE") or row.get("UPDATE_DATE"))
        if report_period is None or announcement_date is None:
            continue
        report_type = _report_type(row.get("QDATE"), report_period)
        normalized = {
            "report_period": report_period,
            "report_type": report_type,
            "report_name": row.get("DATATYPE")
            or _report_name(report_period, report_type),
            "announcement_date": announcement_date,
            "revenue": _decimal(row.get("TOTAL_OPERATE_INCOME")),
            "net_profit_parent": _decimal(row.get("PARENT_NETPROFIT")),
            "basic_eps": _decimal(row.get("BASIC_EPS")),
            "deducted_eps": _decimal(row.get("DEDUCT_BASIC_EPS")),
            "weighted_roe": _decimal(row.get("WEIGHTAVG_ROE")),
            "gross_margin": _decimal(row.get("XSMLL")),
            "revenue_yoy": _decimal(row.get("YSTZ")),
            "net_profit_yoy": _decimal(row.get("SJLTZ")),
            "book_value_per_share": _decimal(row.get("BPS")),
            "operating_cash_flow_per_share": _decimal(row.get("MGJYXJJE")),
            "source_updated_at": _datetime(row.get("EITIME") or row.get("UPDATE_DATE")),
            "source_url": _source_url(report_period, "REPORT"),
        }
        normalized["content_hash"] = _content_hash(normalized)
        result.append(normalized)
    return result


def fetch_eastmoney_performance_forecasts(symbol: str) -> list[dict[str, Any]]:
    rows = _fetch_rows(
        PERFORMANCE_FORECAST_URL,
        {
            "sortColumns": "NOTICE_DATE",
            "sortTypes": "-1",
            "pageSize": "80",
            "pageNumber": "1",
            "reportName": "RPT_PUBLIC_OP_NEWPREDICT",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{symbol}")',
        },
    )
    grouped: dict[tuple[date, date], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        report_period = _date(row.get("REPORT_DATE"))
        announcement_date = _date(row.get("NOTICE_DATE"))
        if report_period and announcement_date:
            grouped[(report_period, announcement_date)].append(row)

    result: list[dict[str, Any]] = []
    for (report_period, announcement_date), event_rows in grouped.items():
        report_type = _report_type(None, report_period)
        normalized: dict[str, Any] = {
            "event_type": "FORECAST",
            "report_period": report_period,
            "report_name": _report_name(report_period, report_type),
            "announcement_date": announcement_date,
            "forecast_type": None,
            "revenue_lower": None,
            "revenue_upper": None,
            "revenue_growth_lower": None,
            "revenue_growth_upper": None,
            "net_profit_lower": None,
            "net_profit_upper": None,
            "net_profit_growth_lower": None,
            "net_profit_growth_upper": None,
            "summary": None,
            "reason": None,
            "source_url": _source_url(report_period, "FORECAST"),
        }
        summaries: list[str] = []
        reasons: list[str] = []
        for row in event_rows:
            metric_code = str(row.get("PREDICT_FINANCE_CODE") or "")
            metric_name = str(row.get("PREDICT_FINANCE") or "")
            prefix = None
            if metric_code == "006" or "营业收入" in metric_name:
                prefix = "revenue"
            elif metric_code == "004" or "净利润" in metric_name:
                prefix = "net_profit"
            if prefix:
                normalized[f"{prefix}_lower"] = _decimal(row.get("PREDICT_AMT_LOWER"))
                normalized[f"{prefix}_upper"] = _decimal(row.get("PREDICT_AMT_UPPER"))
                normalized[f"{prefix}_growth_lower"] = _decimal(row.get("ADD_AMP_LOWER"))
                normalized[f"{prefix}_growth_upper"] = _decimal(row.get("ADD_AMP_UPPER"))
            if row.get("PREDICT_TYPE") and (
                normalized["forecast_type"] is None or prefix == "net_profit"
            ):
                normalized["forecast_type"] = str(row["PREDICT_TYPE"])
            if row.get("PREDICT_CONTENT"):
                summaries.append(str(row["PREDICT_CONTENT"]).strip())
            if row.get("CHANGE_REASON_EXPLAIN"):
                reasons.append(str(row["CHANGE_REASON_EXPLAIN"]).strip())

        normalized["summary"] = "；".join(dict.fromkeys(summaries))[:1200] or None
        normalized["reason"] = "；".join(dict.fromkeys(reasons))[:2000] or None
        normalized["content_hash"] = _content_hash(normalized)
        result.append(normalized)
    return sorted(result, key=lambda item: item["announcement_date"], reverse=True)


def _store_reports(
    db: Session,
    stock: Stock,
    reports: list[dict[str, Any]],
    fetched_at: datetime,
) -> None:
    for values in reports:
        active_reports = db.scalars(
            select(FinancialReport)
            .where(
                FinancialReport.stock_id == stock.id,
                FinancialReport.report_period == values["report_period"],
                FinancialReport.statement_scope == "CONSOLIDATED",
                FinancialReport.status == "ACTIVE",
            )
            .order_by(FinancialReport.version.desc())
        ).all()
        active = active_reports[0] if active_reports else None
        if active and active.content_hash == values["content_hash"]:
            active.fetched_at = fetched_at
            active.source_updated_at = values["source_updated_at"]
            continue

        next_version = (active.version + 1) if active else 1
        for previous in active_reports:
            previous.status = "SUPERSEDED"
        db.add(
            FinancialReport(
                stock_id=stock.id,
                statement_scope="CONSOLIDATED",
                currency="CNY",
                version=next_version,
                status="ACTIVE",
                source="EASTMONEY",
                fetched_at=fetched_at,
                **values,
            )
        )


def _store_forecasts(
    db: Session,
    stock: Stock,
    events: list[dict[str, Any]],
    fetched_at: datetime,
) -> None:
    for values in events:
        event = db.scalar(
            select(PerformanceEvent).where(
                PerformanceEvent.stock_id == stock.id,
                PerformanceEvent.event_type == values["event_type"],
                PerformanceEvent.report_period == values["report_period"],
                PerformanceEvent.announcement_date == values["announcement_date"],
            )
        )
        if event is None:
            db.add(
                PerformanceEvent(
                    stock_id=stock.id,
                    source="EASTMONEY",
                    fetched_at=fetched_at,
                    **values,
                )
            )
            continue
        for key, value in values.items():
            setattr(event, key, value)
        event.fetched_at = fetched_at


def refresh_fundamentals(db: Session, stock: Stock) -> None:
    reports = fetch_eastmoney_financial_reports(stock.symbol)
    if not reports:
        raise FundamentalsDataError("数据源暂未返回该公司的财务报告")

    fetched_at = _utcnow()
    _store_reports(db, stock, reports, fetched_at)
    try:
        forecasts = fetch_eastmoney_performance_forecasts(stock.symbol)
    except FundamentalsDataError:
        # 正式财报仍然可以独立使用；预告接口失败不应丢弃已取得的财务数据。
        forecasts = []
    _store_forecasts(db, stock, forecasts, fetched_at)
    db.commit()


def _report_values(report: FinancialReport) -> dict[str, Any]:
    return {
        "report_period": report.report_period,
        "report_type": report.report_type,
        "report_name": report.report_name,
        "announcement_date": report.announcement_date,
        "revenue": report.revenue,
        "net_profit_parent": report.net_profit_parent,
        "basic_eps": report.basic_eps,
        "deducted_eps": report.deducted_eps,
        "weighted_roe": report.weighted_roe,
        "gross_margin": report.gross_margin,
        "revenue_yoy": report.revenue_yoy,
        "net_profit_yoy": report.net_profit_yoy,
        "book_value_per_share": report.book_value_per_share,
        "operating_cash_flow_per_share": report.operating_cash_flow_per_share,
        "source_url": report.source_url,
    }


def _event_values(event: PerformanceEvent) -> dict[str, Any]:
    return {
        "event_type": event.event_type,
        "event_label": "业绩预告",
        "report_period": event.report_period,
        "report_name": event.report_name,
        "announcement_date": event.announcement_date,
        "forecast_type": event.forecast_type,
        "revenue_lower": event.revenue_lower,
        "revenue_upper": event.revenue_upper,
        "revenue_growth_lower": event.revenue_growth_lower,
        "revenue_growth_upper": event.revenue_growth_upper,
        "net_profit_lower": event.net_profit_lower,
        "net_profit_upper": event.net_profit_upper,
        "net_profit_growth_lower": event.net_profit_growth_lower,
        "net_profit_growth_upper": event.net_profit_growth_upper,
        "summary": event.summary,
        "reason": event.reason,
        "source_url": event.source_url,
    }


def fundamentals_for_stock(db: Session, stock: Stock) -> dict[str, Any]:
    reports = db.scalars(
        select(FinancialReport)
        .where(
            FinancialReport.stock_id == stock.id,
            FinancialReport.status == "ACTIVE",
        )
        .order_by(FinancialReport.report_period.desc())
        .limit(12)
    ).all()

    cache_status = "CACHED"
    stale_before = _utcnow() - timedelta(hours=FUNDAMENTALS_CACHE_HOURS)
    needs_refresh = not reports or max(item.fetched_at for item in reports) < stale_before
    if needs_refresh:
        try:
            refresh_fundamentals(db, stock)
            cache_status = "REFRESHED"
            reports = db.scalars(
                select(FinancialReport)
                .where(
                    FinancialReport.stock_id == stock.id,
                    FinancialReport.status == "ACTIVE",
                )
                .order_by(FinancialReport.report_period.desc())
                .limit(12)
            ).all()
        except FundamentalsDataError:
            db.rollback()
            if not reports:
                raise
            cache_status = "STALE"

    if not reports:
        raise FundamentalsDataError("暂时没有可展示的财务数据")

    forecast_events = db.scalars(
        select(PerformanceEvent)
        .where(PerformanceEvent.stock_id == stock.id)
        .order_by(PerformanceEvent.announcement_date.desc())
        .limit(8)
    ).all()
    events = [_event_values(event) for event in forecast_events]
    events.extend(
        {
            "event_type": "REPORT",
            "event_label": "正式财报",
            "report_period": report.report_period,
            "report_name": report.report_name,
            "announcement_date": report.announcement_date,
            "forecast_type": None,
            "revenue_lower": report.revenue,
            "revenue_upper": report.revenue,
            "revenue_growth_lower": report.revenue_yoy,
            "revenue_growth_upper": report.revenue_yoy,
            "net_profit_lower": report.net_profit_parent,
            "net_profit_upper": report.net_profit_parent,
            "net_profit_growth_lower": report.net_profit_yoy,
            "net_profit_growth_upper": report.net_profit_yoy,
            "summary": None,
            "reason": None,
            "source_url": report.source_url,
        }
        for report in reports[:6]
    )
    events.sort(
        key=lambda item: (item["announcement_date"], item["event_type"] == "REPORT"),
        reverse=True,
    )

    fetched_at = max(report.fetched_at for report in reports)
    return {
        "symbol": stock.symbol,
        "stock_name": stock.name,
        "available": True,
        "provider": "EASTMONEY",
        "provider_label": "东方财富",
        "cache_status": cache_status,
        "fetched_at": fetched_at,
        "latest_report": _report_values(reports[0]),
        "reports": [_report_values(report) for report in reports],
        "events": events[:12],
    }

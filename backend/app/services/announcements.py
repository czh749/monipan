"""On-demand Shanghai Stock Exchange announcement metadata collection."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AnnouncementSyncState, CompanyAnnouncement, Stock


SSE_QUERY_URL = (
    "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
)
SSE_SITE_URL = "https://www.sse.com.cn"
SSE_SECURITY_TYPES = "0101,120100,020100,020200,120200"
SZSE_QUERY_URL = "https://www.szse.cn/api/disc/announcement/annList"
SZSE_DOCUMENT_BASE_URL = "https://disc.static.szse.cn/download/"
ANNOUNCEMENT_CACHE_HOURS = int(
    os.getenv("MONIPAN_ANNOUNCEMENT_CACHE_HOURS", "6")
)
SSE_MAX_PAGES = int(os.getenv("MONIPAN_SSE_ANNOUNCEMENT_MAX_PAGES", "20"))
SZSE_MAX_PAGES = int(os.getenv("MONIPAN_SZSE_ANNOUNCEMENT_MAX_PAGES", "20"))
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36"
    ),
    "Referer": "https://www.sse.com.cn/",
    "Accept": "application/json,text/javascript,*/*;q=0.01",
    "Connection": "close",
}
SZSE_REQUEST_HEADERS = {
    "User-Agent": REQUEST_HEADERS["User-Agent"],
    "Referer": "https://www.szse.cn/disclosure/listed/notice/index.html",
    "Accept": "application/json,text/plain,*/*",
    "Connection": "close",
}


class AnnouncementDataError(RuntimeError):
    """Raised when no usable official announcement metadata can be obtained."""


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


def _content_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _external_id(source_url: str) -> str:
    filename = PurePosixPath(urlparse(source_url).path).stem
    if filename:
        return filename[:128]
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()


def fetch_sse_announcements(
    symbol: str,
    begin_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Fetch and normalize official SSE announcement catalogue rows."""
    base_params = {
        "isPagination": "true",
        "productId": symbol,
        "keyWord": "",
        "securityType": SSE_SECURITY_TYPES,
        "reportType2": "DQGG",
        "reportType": "ALL",
        "beginDate": begin_date.isoformat(),
        "endDate": end_date.isoformat(),
        "pageHelp.pageSize": "100",
        "pageHelp.cacheSize": "1",
    }
    rows: list[Any] = []
    page_no = 1
    page_count = 1
    while page_no <= page_count:
        params = {
            **base_params,
            "pageHelp.pageNo": str(page_no),
            "pageHelp.beginPage": str(page_no),
            "pageHelp.endPage": str(page_no),
        }
        try:
            response = requests.get(
                SSE_QUERY_URL,
                params=params,
                headers=REQUEST_HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AnnouncementDataError(f"上交所公告数据请求失败：{exc}") from exc

        if not isinstance(payload, dict):
            raise AnnouncementDataError("上交所公告数据返回格式异常")
        page_help = payload.get("pageHelp") or {}
        page_rows = page_help.get("data") or []
        if not isinstance(page_rows, list):
            raise AnnouncementDataError("上交所公告数据返回格式异常")
        if page_no == 1:
            try:
                page_count = max(1, int(page_help.get("pageCount") or 1))
            except (TypeError, ValueError) as exc:
                raise AnnouncementDataError("上交所公告分页信息格式异常") from exc
            if page_count > SSE_MAX_PAGES:
                raise AnnouncementDataError(
                    "上交所公告数量超过单次同步上限，请缩短 days 查询范围"
                )
        rows.extend(page_rows)
        if not page_rows:
            break
        page_no += 1

    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("SECURITY_CODE") or "") != symbol:
            continue
        announcement_date = _date(row.get("SSEDATE"))
        title = str(row.get("TITLE") or "").strip()
        relative_url = str(row.get("URL") or "").strip()
        if announcement_date is None or not title or not relative_url:
            continue
        source_url = urljoin(SSE_SITE_URL, relative_url)
        normalized = {
            "exchange": "SSE",
            "external_id": _external_id(source_url),
            "title": title[:500],
            "announcement_date": announcement_date,
            "announcement_heading": (
                str(row.get("BULLETIN_HEADING") or "").strip()[:64] or None
            ),
            "announcement_type": (
                str(row.get("BULLETIN_TYPE") or "").strip()[:128] or None
            ),
            "source_url": source_url,
            "source_published_at": _datetime(row.get("ADDDATE")),
        }
        normalized["content_hash"] = _content_hash(normalized)
        result.append(normalized)

    unique = {item["external_id"]: item for item in result}
    return sorted(
        unique.values(),
        key=lambda item: (item["announcement_date"], item["external_id"]),
        reverse=True,
    )


def fetch_szse_announcements(
    symbol: str,
    begin_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Fetch and normalize official SZSE announcement catalogue rows."""
    page_size = 50
    page_no = 1
    page_count = 1
    rows: list[Any] = []
    while page_no <= page_count:
        request_body = {
            "seDate": [begin_date.isoformat(), end_date.isoformat()],
            "stock": [symbol],
            "channelCode": ["listedNotice_disc"],
            "pageSize": page_size,
            "pageNum": page_no,
        }
        try:
            response = requests.post(
                SZSE_QUERY_URL,
                json=request_body,
                headers=SZSE_REQUEST_HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AnnouncementDataError(f"深交所公告数据请求失败：{exc}") from exc

        if not isinstance(payload, dict):
            raise AnnouncementDataError("深交所公告数据返回格式异常")
        page_rows = payload.get("data") or []
        if not isinstance(page_rows, list):
            raise AnnouncementDataError("深交所公告数据返回格式异常")
        if page_no == 1:
            try:
                total = max(0, int(payload.get("announceCount") or 0))
            except (TypeError, ValueError) as exc:
                raise AnnouncementDataError("深交所公告分页信息格式异常") from exc
            page_count = max(1, math.ceil(total / page_size))
            if page_count > SZSE_MAX_PAGES:
                raise AnnouncementDataError(
                    "深交所公告数量超过单次同步上限，请缩短 days 查询范围"
                )
        rows.extend(page_rows)
        if not page_rows:
            break
        page_no += 1

    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        security_codes = row.get("secCode") or []
        if isinstance(security_codes, str):
            security_codes = [security_codes]
        if symbol not in {str(code) for code in security_codes}:
            continue
        published_at = _datetime(row.get("publishTime"))
        title = str(row.get("title") or "").strip()
        attach_path = str(row.get("attachPath") or "").strip()
        if published_at is None or not title or not attach_path:
            continue
        source_url = urljoin(SZSE_DOCUMENT_BASE_URL, attach_path.lstrip("/"))
        external_id = str(row.get("annId") or row.get("id") or "").strip()
        normalized = {
            "exchange": "SZSE",
            "external_id": (external_id[:128] or _external_id(source_url)),
            "title": title[:500],
            "announcement_date": published_at.date(),
            "announcement_heading": "上市公司公告",
            "announcement_type": None,
            "source_url": source_url,
            "source_published_at": published_at,
        }
        normalized["content_hash"] = _content_hash(normalized)
        result.append(normalized)

    unique = {item["external_id"]: item for item in result}
    return sorted(
        unique.values(),
        key=lambda item: (item["announcement_date"], item["external_id"]),
        reverse=True,
    )


def _store_announcements(
    db: Session,
    stock: Stock,
    announcements: list[dict[str, Any]],
    fetched_at: datetime,
    exchange: str,
) -> None:
    external_ids = [item["external_id"] for item in announcements]
    existing = (
        db.scalars(
            select(CompanyAnnouncement).where(
                CompanyAnnouncement.stock_id == stock.id,
                CompanyAnnouncement.exchange == exchange,
                CompanyAnnouncement.external_id.in_(external_ids),
            )
        ).all()
        if external_ids
        else []
    )
    by_external_id = {item.external_id: item for item in existing}
    for values in announcements:
        item = by_external_id.get(values["external_id"])
        if item is None:
            db.add(
                CompanyAnnouncement(
                    stock_id=stock.id,
                    fetched_at=fetched_at,
                    **values,
                )
            )
            continue
        for key, value in values.items():
            setattr(item, key, value)
        item.stock_id = stock.id
        item.fetched_at = fetched_at


def _announcement_values(item: CompanyAnnouncement) -> dict[str, Any]:
    return {
        "external_id": item.external_id,
        "title": item.title,
        "announcement_date": item.announcement_date,
        "announcement_heading": item.announcement_heading,
        "announcement_type": item.announcement_type,
        "exchange": item.exchange,
        "source_url": item.source_url,
        "source_published_at": item.source_published_at,
    }


def announcements_for_stock(
    db: Session,
    stock: Stock,
    *,
    days: int,
    limit: int,
) -> dict[str, Any]:
    """Return cached official metadata, refreshing its covered range when needed."""
    if stock.exchange not in {"SSE", "SZSE"}:
        raise AnnouncementDataError("当前公告数据源仅支持上交所和深交所股票")

    exchange = stock.exchange
    fetcher = {
        "SSE": fetch_sse_announcements,
        "SZSE": fetch_szse_announcements,
    }[exchange]
    now = _utcnow()
    end_date = now.date()
    begin_date = end_date - timedelta(days=days - 1)
    state = db.scalar(
        select(AnnouncementSyncState).where(
            AnnouncementSyncState.stock_id == stock.id,
            AnnouncementSyncState.exchange == exchange,
        )
    )
    cache_fresh = bool(
        state
        and state.covered_from <= begin_date
        and state.covered_to >= end_date
        and state.last_success_at
        >= now - timedelta(hours=ANNOUNCEMENT_CACHE_HOURS)
    )
    cache_status = "CACHED" if cache_fresh else "REFRESHED"

    if not cache_fresh:
        try:
            fetched = fetcher(stock.symbol, begin_date, end_date)
            _store_announcements(db, stock, fetched, now, exchange)
            if state is None:
                state = AnnouncementSyncState(
                    stock_id=stock.id,
                    exchange=exchange,
                    covered_from=begin_date,
                    covered_to=end_date,
                    last_success_at=now,
                )
                db.add(state)
            else:
                state.covered_from = min(state.covered_from, begin_date)
                state.covered_to = max(state.covered_to, end_date)
                state.last_success_at = now
            db.commit()
        except AnnouncementDataError:
            db.rollback()
            cached_count = db.scalar(
                select(CompanyAnnouncement.id)
                .where(
                    CompanyAnnouncement.stock_id == stock.id,
                    CompanyAnnouncement.announcement_date >= begin_date,
                    CompanyAnnouncement.announcement_date <= end_date,
                )
                .limit(1)
            )
            if cached_count is None:
                raise
            cache_status = "STALE"

    announcements = db.scalars(
        select(CompanyAnnouncement)
        .where(
            CompanyAnnouncement.stock_id == stock.id,
            CompanyAnnouncement.exchange == exchange,
            CompanyAnnouncement.announcement_date >= begin_date,
            CompanyAnnouncement.announcement_date <= end_date,
        )
        .order_by(
            CompanyAnnouncement.announcement_date.desc(),
            CompanyAnnouncement.id.desc(),
        )
        .limit(limit)
    ).all()
    fetched_at = (
        state.last_success_at
        if state is not None
        else max(item.fetched_at for item in announcements)
    )
    return {
        "symbol": stock.symbol,
        "stock_name": stock.name,
        "provider": exchange,
        "provider_label": {
            "SSE": "上海证券交易所",
            "SZSE": "深圳证券交易所",
        }[exchange],
        "cache_status": cache_status,
        "fetched_at": fetched_at,
        "range_start": begin_date,
        "range_end": end_date,
        "announcements": [_announcement_values(item) for item in announcements],
    }

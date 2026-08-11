"""On-demand official regulatory-letter metadata collection and reply linking."""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    CompanyAnnouncement,
    RegulatoryLetter,
    RegulatoryLetterReply,
    RegulatorySyncState,
    Stock,
)


SSE_QUERY_URL = "https://query.sse.com.cn/commonSoaQuery.do"
SSE_SITE_URL = "https://www.sse.com.cn"
SZSE_QUERY_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_DOCUMENT_BASE_URL = "https://reportdocs.static.szse.cn"
REGULATORY_CACHE_HOURS = int(os.getenv("MONIPAN_REGULATORY_CACHE_HOURS", "6"))
SSE_MAX_PAGES = int(os.getenv("MONIPAN_SSE_REGULATORY_MAX_PAGES", "20"))
SZSE_MAX_PAGES = int(os.getenv("MONIPAN_SZSE_REGULATORY_MAX_PAGES", "20"))
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36"
    ),
    "Referer": "https://www.sse.com.cn/regulation/supervision/inquiries/",
    "Accept": "application/json,text/javascript,*/*;q=0.01",
    "Connection": "close",
}
SZSE_REQUEST_HEADERS = {
    **REQUEST_HEADERS,
    "Referer": "https://www.szse.cn/disclosure/supervision/inquire/index.html",
}
LINK_PATH_RE = re.compile(r"encode-open=['\"]([^'\"]+)['\"]", re.IGNORECASE)
LINK_TEXT_RE = re.compile(r">\s*([^<>]+?)\s*</a>", re.IGNORECASE)
YEAR_RE = re.compile(r"20\d{2}")


class RegulatoryDataError(RuntimeError):
    """Raised when no usable official regulatory metadata can be obtained."""


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
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


def _absolute_sse_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("www."):
        return f"https://{raw}"
    return urljoin(SSE_SITE_URL, raw)


def _html_link(value: Any) -> tuple[str, str] | None:
    markup = str(value or "")
    path_match = LINK_PATH_RE.search(markup)
    if not path_match:
        return None
    text_match = LINK_TEXT_RE.search(markup)
    title = html.unescape(text_match.group(1)).strip() if text_match else ""
    source_url = urljoin(SZSE_DOCUMENT_BASE_URL, path_match.group(1))
    return source_url, title


def fetch_sse_regulatory_letters(
    symbol: str,
    begin_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Fetch SSE inquiry-letter catalogue rows for one listed company."""
    base_params = {
        "isPagination": "true",
        "pageHelp.pageSize": "100",
        "pageHelp.cacheSize": "1",
        "sqlId": "BS_KCB_GGLL_NEW",
        "siteId": "28",
        "channelId": "10743,10744,10012",
        "type": "",
        "stockcode": symbol,
        "extGGDL": "",
        "createTime": f"{begin_date.isoformat()} 00:00:00",
        "createTimeEnd": f"{end_date.isoformat()} 23:59:59",
        "order": "createTime|desc,stockcode|asc",
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
            raise RegulatoryDataError(f"上交所监管问询数据请求失败：{exc}") from exc

        if not isinstance(payload, dict):
            raise RegulatoryDataError("上交所监管问询数据返回格式异常")
        page_rows = payload.get("result") or []
        page_help = payload.get("pageHelp") or {}
        if not isinstance(page_rows, list):
            raise RegulatoryDataError("上交所监管问询数据返回格式异常")
        if page_no == 1:
            try:
                page_count = max(1, int(page_help.get("pageCount") or 1))
            except (TypeError, ValueError) as exc:
                raise RegulatoryDataError("上交所监管问询分页信息格式异常") from exc
            if page_count > SSE_MAX_PAGES:
                raise RegulatoryDataError(
                    "上交所监管问询数量超过单次同步上限，请缩短 days 查询范围"
                )
        rows.extend(page_rows)
        if not page_rows:
            break
        page_no += 1

    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_symbol = str(
            row.get("extSECURITY_CODE") or row.get("stockcode") or ""
        ).strip()
        if row_symbol != symbol:
            continue
        issued_date = _date(row.get("createTime"))
        title = str(row.get("docTitle") or "").strip()
        source_url = _absolute_sse_url(row.get("docURL"))
        if issued_date is None or not title or not source_url:
            continue
        external_id = str(row.get("docId") or "").strip() or _external_id(source_url)
        normalized = {
            "exchange": "SSE",
            "external_id": external_id[:128],
            "title": title[:500],
            "letter_type": str(row.get("extWTFL") or "问询函").strip()[:128],
            "issued_date": issued_date,
            "source_url": source_url,
            "replies": [],
        }
        normalized["content_hash"] = _content_hash(
            {key: value for key, value in normalized.items() if key != "replies"}
        )
        result.append(normalized)

    unique = {item["external_id"]: item for item in result}
    return sorted(
        unique.values(),
        key=lambda item: (item["issued_date"], item["external_id"]),
        reverse=True,
    )


def fetch_szse_regulatory_letters(
    symbol: str,
    begin_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Fetch SZSE inquiry letters and the replies linked by the exchange."""
    tab_key = "tab3" if symbol.startswith(("300", "301")) else "tab2"
    page_no = 1
    page_count = 1
    rows: list[Any] = []
    while page_no <= page_count:
        params = {
            "SHOWTYPE": "JSON",
            "CATALOGID": "main_wxhj",
            "TABKEY": tab_key,
            "PAGENO": str(page_no),
            "txtZqdm": symbol,
            "selecthjlb": "",
            "txtStart": begin_date.isoformat(),
            "txtEnd": end_date.isoformat(),
        }
        try:
            response = requests.get(
                SZSE_QUERY_URL,
                params=params,
                headers=SZSE_REQUEST_HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RegulatoryDataError(f"深交所监管问询数据请求失败：{exc}") from exc

        if not isinstance(payload, list):
            raise RegulatoryDataError("深交所监管问询数据返回格式异常")
        selected = next(
            (
                item
                for item in payload
                if isinstance(item, dict)
                and (item.get("metadata") or {}).get("tabkey") == tab_key
            ),
            None,
        )
        if selected is None:
            raise RegulatoryDataError("深交所监管问询数据缺少对应板块")
        page_rows = selected.get("data") or []
        metadata = selected.get("metadata") or {}
        if not isinstance(page_rows, list):
            raise RegulatoryDataError("深交所监管问询数据返回格式异常")
        if page_no == 1:
            try:
                page_count = max(1, int(metadata.get("pagecount") or 1))
            except (TypeError, ValueError) as exc:
                raise RegulatoryDataError("深交所监管问询分页信息格式异常") from exc
            if page_count > SZSE_MAX_PAGES:
                raise RegulatoryDataError(
                    "深交所监管问询数量超过单次同步上限，请缩短 days 查询范围"
                )
        rows.extend(page_rows)
        if not page_rows:
            break
        page_no += 1

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("gsdm") or "") != symbol:
            continue
        issued_date = _date(row.get("fhrq"))
        letter_link = _html_link(row.get("ck"))
        letter_type = str(row.get("hjlb") or "监管函件").strip()
        stock_name = str(row.get("gsjc") or symbol).strip()
        if issued_date is None or letter_link is None:
            continue
        source_url, source_title = letter_link
        external_id = _external_id(source_url)
        item = grouped.setdefault(
            external_id,
            {
                "exchange": "SZSE",
                "external_id": external_id,
                "title": (
                    source_title
                    if source_title and source_title != "详细内容"
                    else f"{stock_name}：{letter_type}"
                )[:500],
                "letter_type": letter_type[:128],
                "issued_date": issued_date,
                "source_url": source_url,
                "replies": [],
            },
        )
        reply_link = _html_link(row.get("hfck"))
        if reply_link is not None:
            reply_url, reply_title = reply_link
            reply = {
                "external_id": _external_id(reply_url),
                "title": (reply_title or "公司回复")[:500],
                "reply_date": None,
                "source_url": reply_url,
                "match_method": "SOURCE",
                "announcement_id": None,
            }
            reply["content_hash"] = _content_hash(reply)
            item["replies"].append(reply)

    result: list[dict[str, Any]] = []
    for item in grouped.values():
        item["replies"] = list(
            {reply["external_id"]: reply for reply in item["replies"]}.values()
        )
        item["content_hash"] = _content_hash(
            {key: value for key, value in item.items() if key != "replies"}
        )
        result.append(item)
    return sorted(
        result,
        key=lambda item: (item["issued_date"], item["external_id"]),
        reverse=True,
    )


def _upsert_reply(
    db: Session,
    letter: RegulatoryLetter,
    values: dict[str, Any],
    fetched_at: datetime,
) -> None:
    reply = db.scalar(
        select(RegulatoryLetterReply).where(
            RegulatoryLetterReply.regulatory_letter_id == letter.id,
            RegulatoryLetterReply.external_id == values["external_id"],
        )
    )
    if reply is None:
        db.add(
            RegulatoryLetterReply(
                regulatory_letter_id=letter.id,
                fetched_at=fetched_at,
                **values,
            )
        )
        return
    for key, value in values.items():
        setattr(reply, key, value)
    reply.fetched_at = fetched_at


def _store_letters(
    db: Session,
    stock: Stock,
    letters: list[dict[str, Any]],
    fetched_at: datetime,
    exchange: str,
) -> None:
    external_ids = [item["external_id"] for item in letters]
    existing = (
        db.scalars(
            select(RegulatoryLetter).where(
                RegulatoryLetter.stock_id == stock.id,
                RegulatoryLetter.exchange == exchange,
                RegulatoryLetter.external_id.in_(external_ids),
            )
        ).all()
        if external_ids
        else []
    )
    by_external_id = {item.external_id: item for item in existing}
    for values in letters:
        reply_values = values["replies"]
        letter_values = {key: value for key, value in values.items() if key != "replies"}
        letter = by_external_id.get(values["external_id"])
        if letter is None:
            letter = RegulatoryLetter(
                stock_id=stock.id,
                fetched_at=fetched_at,
                **letter_values,
            )
            db.add(letter)
            db.flush()
        else:
            for key, value in letter_values.items():
                setattr(letter, key, value)
            letter.stock_id = stock.id
            letter.fetched_at = fetched_at
        for reply in reply_values:
            _upsert_reply(db, letter, reply, fetched_at)


def _reply_markers(letter: RegulatoryLetter) -> tuple[str, ...]:
    combined = f"{letter.letter_type}{letter.title}"
    if "重组" in combined:
        return ("重组", "审核意见函")
    if "年报" in combined or "年度报告" in combined or "定期报告" in combined:
        return ("年报问询函", "年度报告问询函", "定期报告问询函")
    if "关注函" in combined:
        return ("关注函",)
    return ("问询函",)


def _link_sse_announcement_replies(
    db: Session,
    stock: Stock,
    begin_date: date,
    end_date: date,
    fetched_at: datetime,
) -> None:
    """Conservatively link cached SSE reply announcements to the latest matching letter."""
    letters = db.scalars(
        select(RegulatoryLetter).where(
            RegulatoryLetter.stock_id == stock.id,
            RegulatoryLetter.exchange == "SSE",
            RegulatoryLetter.issued_date >= begin_date,
            RegulatoryLetter.issued_date <= end_date,
        )
    ).all()
    if not letters:
        return
    announcements = db.scalars(
        select(CompanyAnnouncement).where(
            CompanyAnnouncement.stock_id == stock.id,
            CompanyAnnouncement.exchange == "SSE",
            CompanyAnnouncement.announcement_date >= begin_date,
            CompanyAnnouncement.announcement_date <= end_date + timedelta(days=180),
            CompanyAnnouncement.title.contains("回复"),
        )
    ).all()
    for announcement in announcements:
        compatible: list[RegulatoryLetter] = []
        announcement_years = set(YEAR_RE.findall(announcement.title))
        for letter in letters:
            if not (
                letter.issued_date <= announcement.announcement_date
                <= letter.issued_date + timedelta(days=180)
            ):
                continue
            if not any(marker in announcement.title for marker in _reply_markers(letter)):
                continue
            letter_years = set(YEAR_RE.findall(letter.title))
            if letter_years and announcement_years and letter_years.isdisjoint(announcement_years):
                continue
            compatible.append(letter)
        if not compatible:
            continue
        letter = max(compatible, key=lambda item: (item.issued_date, item.id))
        values = {
            "announcement_id": announcement.id,
            "external_id": announcement.external_id,
            "title": announcement.title,
            "reply_date": announcement.announcement_date,
            "source_url": announcement.source_url,
            "match_method": "TITLE_DATE",
        }
        values["content_hash"] = _content_hash(values)
        _upsert_reply(db, letter, values, fetched_at)


def _reply_values(item: RegulatoryLetterReply) -> dict[str, Any]:
    return {
        "external_id": item.external_id,
        "title": item.title,
        "reply_date": item.reply_date,
        "source_url": item.source_url,
        "match_method": item.match_method,
    }


def _letter_values(item: RegulatoryLetter) -> dict[str, Any]:
    replies = sorted(
        item.replies,
        key=lambda reply: (reply.reply_date or date.min, reply.id),
        reverse=True,
    )
    return {
        "external_id": item.external_id,
        "title": item.title,
        "letter_type": item.letter_type,
        "issued_date": item.issued_date,
        "exchange": item.exchange,
        "source_url": item.source_url,
        "reply_status": "REPLIED" if replies else "NO_REPLY_FOUND",
        "replies": [_reply_values(reply) for reply in replies],
    }


def regulatory_letters_for_stock(
    db: Session,
    stock: Stock,
    *,
    days: int,
    limit: int,
) -> dict[str, Any]:
    """Return cached official regulatory metadata, refreshing covered range when needed."""
    if stock.exchange not in {"SSE", "SZSE"}:
        raise RegulatoryDataError("当前监管函件数据源仅支持上交所和深交所股票")

    exchange = stock.exchange
    fetcher = {
        "SSE": fetch_sse_regulatory_letters,
        "SZSE": fetch_szse_regulatory_letters,
    }[exchange]
    now = _utcnow()
    end_date = now.date()
    begin_date = end_date - timedelta(days=days - 1)
    state = db.scalar(
        select(RegulatorySyncState).where(
            RegulatorySyncState.stock_id == stock.id,
            RegulatorySyncState.exchange == exchange,
        )
    )
    cache_fresh = bool(
        state
        and state.covered_from <= begin_date
        and state.covered_to >= end_date
        and state.last_success_at >= now - timedelta(hours=REGULATORY_CACHE_HOURS)
    )
    cache_status = "CACHED" if cache_fresh else "REFRESHED"

    if not cache_fresh:
        try:
            fetched = fetcher(stock.symbol, begin_date, end_date)
            _store_letters(db, stock, fetched, now, exchange)
            if exchange == "SSE":
                _link_sse_announcement_replies(db, stock, begin_date, end_date, now)
            if state is None:
                state = RegulatorySyncState(
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
        except RegulatoryDataError:
            db.rollback()
            cached_id = db.scalar(
                select(RegulatoryLetter.id)
                .where(
                    RegulatoryLetter.stock_id == stock.id,
                    RegulatoryLetter.exchange == exchange,
                    RegulatoryLetter.issued_date >= begin_date,
                    RegulatoryLetter.issued_date <= end_date,
                )
                .limit(1)
            )
            if cached_id is None:
                raise
            cache_status = "STALE"
            state = db.scalar(
                select(RegulatorySyncState).where(
                    RegulatorySyncState.stock_id == stock.id,
                    RegulatorySyncState.exchange == exchange,
                )
            )
    elif exchange == "SSE":
        _link_sse_announcement_replies(db, stock, begin_date, end_date, now)
        db.commit()

    letters = db.scalars(
        select(RegulatoryLetter)
        .where(
            RegulatoryLetter.stock_id == stock.id,
            RegulatoryLetter.exchange == exchange,
            RegulatoryLetter.issued_date >= begin_date,
            RegulatoryLetter.issued_date <= end_date,
        )
        .options(selectinload(RegulatoryLetter.replies))
        .order_by(RegulatoryLetter.issued_date.desc(), RegulatoryLetter.id.desc())
        .limit(limit)
    ).all()
    fetched_at = (
        state.last_success_at
        if state is not None
        else max((item.fetched_at for item in letters), default=now)
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
        "letters": [_letter_values(item) for item in letters],
    }

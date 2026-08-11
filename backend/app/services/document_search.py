"""Deterministic page-aware chunking and keyword search for official PDFs."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Iterable

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..models import OfficialDocumentChunk, OfficialDocumentContent, Stock


CHUNK_TARGET_CHARS = int(
    os.getenv("MONIPAN_DOCUMENT_CHUNK_TARGET_CHARS", "700")
)
CHUNK_MIN_CHARS = int(os.getenv("MONIPAN_DOCUMENT_CHUNK_MIN_CHARS", "250"))
CHUNK_MAX_CHARS = int(os.getenv("MONIPAN_DOCUMENT_CHUNK_MAX_CHARS", "900"))
CHUNK_OVERLAP_CHARS = int(
    os.getenv("MONIPAN_DOCUMENT_CHUNK_OVERLAP_CHARS", "80")
)
SEARCH_CANDIDATE_LIMIT = int(
    os.getenv("MONIPAN_DOCUMENT_SEARCH_CANDIDATE_LIMIT", "500")
)
BOUNDARY_RE = re.compile(r"[。！？；!?;\n]")
TERM_RE = re.compile(r"[0-9A-Za-z\u3400-\u9fff]+")


class DocumentSearchError(RuntimeError):
    """Raised for invalid chunking or keyword-search input."""


@dataclass(frozen=True)
class OfficialDocumentSearchHit:
    content_id: int
    document_type: str
    document_id: int
    title: str
    document_date: date | None
    source_url: str
    page_number: int
    chunk_index: int
    start_char: int
    end_char: int
    excerpt: str
    text_hash: str
    score: int
    extracted_at: datetime


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _validate_chunk_settings() -> None:
    if not (
        1 <= CHUNK_MIN_CHARS <= CHUNK_TARGET_CHARS <= CHUNK_MAX_CHARS
        and 0 <= CHUNK_OVERLAP_CHARS < CHUNK_MIN_CHARS
    ):
        raise DocumentSearchError("官方文档分段长度配置无效")


def _trimmed_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _page_chunk_ranges(text: str) -> list[tuple[int, int]]:
    """Split one page at nearby punctuation while keeping bounded overlap."""
    _validate_chunk_settings()
    ranges: list[tuple[int, int]] = []
    cursor = 0
    text_length = len(text)
    while cursor < text_length:
        cursor, _ = _trimmed_range(text, cursor, text_length)
        if cursor >= text_length:
            break
        remaining = text_length - cursor
        if remaining <= CHUNK_MAX_CHARS:
            end = text_length
        else:
            minimum_end = min(text_length, cursor + CHUNK_MIN_CHARS)
            preferred_end = min(text_length, cursor + CHUNK_TARGET_CHARS)
            maximum_end = min(text_length, cursor + CHUNK_MAX_CHARS)
            boundaries = [
                match.end()
                for match in BOUNDARY_RE.finditer(text, minimum_end, maximum_end)
            ]
            before_target = [value for value in boundaries if value <= preferred_end]
            after_target = [value for value in boundaries if value > preferred_end]
            if before_target:
                end = before_target[-1]
            elif after_target:
                end = after_target[0]
            else:
                end = maximum_end
        start, end = _trimmed_range(text, cursor, end)
        if end <= start:
            break
        ranges.append((start, end))
        if end >= text_length:
            break
        next_cursor = max(start + 1, end - CHUNK_OVERLAP_CHARS)
        cursor, _ = _trimmed_range(text, next_cursor, text_length)
    return ranges


def build_document_chunk_values(
    content: OfficialDocumentContent,
) -> list[dict[str, object]]:
    """Build exact character ranges without allowing a chunk to cross pages."""
    values: list[dict[str, object]] = []
    chunk_index = 0
    for page in content.page_offsets or []:
        page_number = int(page["page_number"])
        page_start = int(page["start_char"])
        page_end = int(page["end_char"])
        if not (0 <= page_start <= page_end <= len(content.text_content)):
            raise DocumentSearchError("官方文档页码字符位置无效")
        page_text = content.text_content[page_start:page_end]
        for local_start, local_end in _page_chunk_ranges(page_text):
            start_char = page_start + local_start
            end_char = page_start + local_end
            chunk_text = content.text_content[start_char:end_char]
            values.append(
                {
                    "official_document_content_id": content.id,
                    "stock_id": content.stock_id,
                    "document_type": content.document_type,
                    "page_number": page_number,
                    "chunk_index": chunk_index,
                    "start_char": start_char,
                    "end_char": end_char,
                    "text": chunk_text,
                    "text_hash": hashlib.sha256(
                        chunk_text.encode("utf-8")
                    ).hexdigest(),
                }
            )
            chunk_index += 1
    return values


def ensure_document_chunks(
    db: Session,
    content: OfficialDocumentContent,
    *,
    force: bool = False,
) -> int:
    """Create missing chunks or atomically replace chunks after re-extraction."""
    existing_count = len(
        db.scalars(
            select(OfficialDocumentChunk.id).where(
                OfficialDocumentChunk.official_document_content_id == content.id
            )
        ).all()
    )
    if existing_count and not force:
        return existing_count
    if force and existing_count:
        db.execute(
            delete(OfficialDocumentChunk).where(
                OfficialDocumentChunk.official_document_content_id == content.id
            )
        )
    values = build_document_chunk_values(content)
    db.add_all(OfficialDocumentChunk(**value) for value in values)
    return len(values)


def _query_terms(query: str) -> tuple[str, list[str]]:
    normalized = " ".join(query.strip().split())
    if not normalized:
        raise DocumentSearchError("关键词不能为空")
    if len(normalized) > 100:
        raise DocumentSearchError("关键词不能超过 100 个字符")
    terms = list(dict.fromkeys(TERM_RE.findall(normalized)))
    if not terms:
        raise DocumentSearchError("关键词必须包含文字、字母或数字")
    return normalized.casefold(), [term.casefold() for term in terms]


def _score(title: str, text: str, query: str, terms: list[str]) -> int:
    folded_title = title.casefold()
    folded_text = text.casefold()
    score = 0
    if query in folded_title:
        score += 30
    if query in folded_text:
        score += 15
    for term in terms:
        if term in folded_title:
            score += 10
        score += min(folded_text.count(term), 5) * 3
    return score


def _excerpt(text: str, query: str, terms: list[str], max_chars: int = 320) -> str:
    folded = text.casefold()
    positions = [folded.find(query)] + [folded.find(term) for term in terms]
    positions = [position for position in positions if position >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - 80)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def search_official_document_chunks(
    db: Session,
    stock: Stock,
    *,
    query: str,
    days: int = 365,
    limit: int = 10,
    document_types: Iterable[str] | None = None,
) -> list[OfficialDocumentSearchHit]:
    """Search already-extracted evidence; this function never downloads PDFs."""
    if not 30 <= days <= 1095:
        raise DocumentSearchError("days 必须在 30 到 1095 之间")
    if not 1 <= limit <= 50:
        raise DocumentSearchError("limit 必须在 1 到 50 之间")
    normalized_query, terms = _query_terms(query)
    selected_types = {
        value.strip().upper() for value in (document_types or []) if value.strip()
    }
    supported_types = {
        "ANNOUNCEMENT",
        "REGULATORY_LETTER",
        "REGULATORY_REPLY",
    }
    if selected_types - supported_types:
        raise DocumentSearchError("包含不支持的官方文档类型")

    begin_date = _utc_today() - timedelta(days=days - 1)
    match_conditions = []
    for term in terms:
        match_conditions.extend(
            (
                OfficialDocumentContent.title.contains(term, autoescape=True),
                OfficialDocumentChunk.text.contains(term, autoescape=True),
            )
        )
    statement = (
        select(OfficialDocumentChunk, OfficialDocumentContent)
        .join(
            OfficialDocumentContent,
            OfficialDocumentContent.id
            == OfficialDocumentChunk.official_document_content_id,
        )
        .where(
            OfficialDocumentContent.stock_id == stock.id,
            OfficialDocumentContent.document_date >= begin_date,
            or_(*match_conditions),
        )
        .order_by(
            OfficialDocumentContent.document_date.desc(),
            OfficialDocumentChunk.chunk_index.asc(),
        )
        .limit(SEARCH_CANDIDATE_LIMIT)
    )
    if selected_types:
        statement = statement.where(
            OfficialDocumentContent.document_type.in_(selected_types)
        )

    hits: list[OfficialDocumentSearchHit] = []
    for chunk, content in db.execute(statement).all():
        score = _score(content.title, chunk.text, normalized_query, terms)
        hits.append(
            OfficialDocumentSearchHit(
                content_id=content.id,
                document_type=content.document_type,
                document_id=content.document_id,
                title=content.title,
                document_date=content.document_date,
                source_url=content.source_url,
                page_number=chunk.page_number,
                chunk_index=chunk.chunk_index,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                excerpt=_excerpt(chunk.text, normalized_query, terms),
                text_hash=chunk.text_hash,
                score=score,
                extracted_at=content.extracted_at,
            )
        )
    hits.sort(
        key=lambda item: (
            item.score,
            item.document_date or date.min,
            -item.page_number,
            -item.chunk_index,
        ),
        reverse=True,
    )
    return hits[:limit]

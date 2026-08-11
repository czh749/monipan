"""Bounded official-document discovery, extraction and evidence retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    CompanyAnnouncement,
    OfficialDocumentContent,
    RegulatoryLetter,
    Stock,
)
from ..services.announcements import announcements_for_stock
from ..services.document_search import (
    OfficialDocumentSearchHit,
    search_official_document_chunks,
)
from ..services.documents import (
    OfficialDocumentError,
    ensure_official_document_text,
)
from ..services.regulatory import regulatory_letters_for_stock


TERM_RE = re.compile(r"[0-9A-Za-z\u3400-\u9fff]+")


@dataclass(frozen=True)
class EvidenceCandidate:
    document_type: str
    document_id: int
    title: str
    document_date: date
    match_method: str | None = None


@dataclass(frozen=True)
class EvidenceSearchResult:
    status: str
    as_of: datetime
    data: dict[str, Any]
    evidence: list[dict[str, Any]]
    warnings: list[str]


def _terms(query: str) -> tuple[str, list[str]]:
    normalized = " ".join(query.strip().split()).casefold()
    terms = list(dict.fromkeys(term.casefold() for term in TERM_RE.findall(normalized)))
    return normalized, terms


def _title_score(title: str, query: str, terms: list[str]) -> int:
    folded = title.casefold()
    score = 30 if query and query in folded else 0
    score += sum(10 for term in terms if term in folded)
    return score


def _content_keys(
    db: Session,
    stock_id: int,
    document_types: Iterable[str],
) -> set[tuple[str, int]]:
    rows = db.execute(
        select(
            OfficialDocumentContent.document_type,
            OfficialDocumentContent.document_id,
        ).where(
            OfficialDocumentContent.stock_id == stock_id,
            OfficialDocumentContent.document_type.in_(set(document_types)),
            OfficialDocumentContent.status.in_(("READY", "NO_TEXT")),
        )
    ).all()
    return {(document_type, document_id) for document_type, document_id in rows}


def _rank_candidates(
    candidates: list[EvidenceCandidate],
    query: str,
    terms: list[str],
    cached_keys: set[tuple[str, int]],
) -> tuple[list[EvidenceCandidate], bool]:
    uncached = [
        candidate
        for candidate in candidates
        if (candidate.document_type, candidate.document_id) not in cached_keys
    ]
    scored = [
        (_title_score(candidate.title, query, terms), candidate)
        for candidate in uncached
    ]
    matched = [item for item in scored if item[0] > 0]
    fallback_used = not matched
    selected = matched or scored
    selected.sort(
        key=lambda item: (item[0], item[1].document_date, item[1].document_id),
        reverse=True,
    )
    return [candidate for _, candidate in selected], fallback_used


def _extract_candidates(
    db: Session,
    stock: Stock,
    candidates: list[EvidenceCandidate],
    max_documents: int,
) -> tuple[int, int, list[str]]:
    checked = 0
    downloaded = 0
    warnings: list[str] = []
    for candidate in candidates[:max_documents]:
        checked += 1
        try:
            result = ensure_official_document_text(
                db,
                document_type=candidate.document_type,
                document_id=candidate.document_id,
                expected_stock_id=stock.id,
            )
            if result.cache_status == "EXTRACTED":
                downloaded += 1
            if result.extraction_status == "NO_TEXT":
                warnings.append(f"《{candidate.title}》是扫描件或没有可抽取文字")
            if result.cache_status == "STALE":
                warnings.append(f"《{candidate.title}》正文刷新失败，使用旧缓存")
        except OfficialDocumentError as exc:
            warnings.append(f"《{candidate.title}》正文不可用：{exc}")
    return checked, downloaded, warnings


def _hit_evidence(
    hit: OfficialDocumentSearchHit,
    *,
    match_method: str | None = None,
) -> dict[str, Any]:
    return {
        "evidence_id": (
            f"{hit.document_type}:{hit.document_id}:"
            f"{hit.page_number}:{hit.text_hash[:16]}"
        ),
        "document_type": hit.document_type,
        "document_id": hit.document_id,
        "title": hit.title,
        "document_date": hit.document_date,
        "source_url": hit.source_url,
        "page_number": hit.page_number,
        "start_char": hit.start_char,
        "end_char": hit.end_char,
        "excerpt": hit.excerpt,
        "text_hash": hit.text_hash,
        "score": hit.score,
        "match_method": match_method,
        "extracted_at": hit.extracted_at,
        "trust": "UNTRUSTED_SOURCE_CONTENT",
    }


def _result_status(evidence: list[dict[str, Any]], warnings: list[str]) -> str:
    if evidence and warnings:
        return "PARTIAL"
    if evidence:
        return "OK"
    if warnings:
        return "PARTIAL"
    return "NO_DATA"


def search_company_announcement_evidence(
    db: Session,
    stock: Stock,
    *,
    query: str,
    days: int,
    limit: int,
    max_documents: int,
) -> EvidenceSearchResult:
    metadata = announcements_for_stock(db, stock, days=days, limit=200)
    begin_date = datetime.now(UTC).date() - timedelta(days=days - 1)
    candidates = [
        EvidenceCandidate(
            document_type="ANNOUNCEMENT",
            document_id=item.id,
            title=item.title,
            document_date=item.announcement_date,
        )
        for item in db.scalars(
            select(CompanyAnnouncement)
            .where(
                CompanyAnnouncement.stock_id == stock.id,
                CompanyAnnouncement.announcement_date >= begin_date,
            )
            .order_by(
                CompanyAnnouncement.announcement_date.desc(),
                CompanyAnnouncement.id.desc(),
            )
        ).all()
    ]
    hits = search_official_document_chunks(
        db,
        stock,
        query=query,
        days=days,
        limit=limit,
        document_types=("ANNOUNCEMENT",),
    )
    checked = 0
    downloaded = 0
    warnings: list[str] = []
    if not hits and max_documents:
        normalized, terms = _terms(query)
        cached_keys = _content_keys(db, stock.id, ("ANNOUNCEMENT",))
        ranked, fallback_used = _rank_candidates(
            candidates,
            normalized,
            terms,
            cached_keys,
        )
        checked, downloaded, warnings = _extract_candidates(
            db,
            stock,
            ranked,
            max_documents,
        )
        if fallback_used and checked:
            warnings.append("公告标题未命中关键词，仅补充检查了最近披露文件")
        hits = search_official_document_chunks(
            db,
            stock,
            query=query,
            days=days,
            limit=limit,
            document_types=("ANNOUNCEMENT",),
        )
    evidence = [_hit_evidence(hit) for hit in hits]
    return EvidenceSearchResult(
        status=_result_status(evidence, warnings),
        as_of=metadata["fetched_at"],
        data={
            "symbol": stock.symbol,
            "stock_name": stock.name,
            "query": query,
            "range_start": metadata["range_start"],
            "range_end": metadata["range_end"],
            "metadata_cache_status": metadata["cache_status"],
            "metadata_count": len(candidates),
            "documents_checked": checked,
            "pdfs_downloaded": downloaded,
            "evidence_count": len(evidence),
        },
        evidence=evidence,
        warnings=warnings,
    )


def search_regulatory_evidence(
    db: Session,
    stock: Stock,
    *,
    query: str,
    days: int,
    limit: int,
    max_documents: int,
    include_replies: bool,
) -> EvidenceSearchResult:
    metadata = regulatory_letters_for_stock(db, stock, days=days, limit=200)
    begin_date = datetime.now(UTC).date() - timedelta(days=days - 1)
    letters = db.scalars(
        select(RegulatoryLetter)
        .where(
            RegulatoryLetter.stock_id == stock.id,
            RegulatoryLetter.issued_date >= begin_date,
        )
        .options(selectinload(RegulatoryLetter.replies))
        .order_by(RegulatoryLetter.issued_date.desc(), RegulatoryLetter.id.desc())
    ).all()
    candidates: list[EvidenceCandidate] = []
    match_methods: dict[int, str] = {}
    for letter in letters:
        candidates.append(
            EvidenceCandidate(
                document_type="REGULATORY_LETTER",
                document_id=letter.id,
                title=letter.title,
                document_date=letter.issued_date,
            )
        )
        if include_replies:
            for reply in letter.replies:
                candidates.append(
                    EvidenceCandidate(
                        document_type="REGULATORY_REPLY",
                        document_id=reply.id,
                        title=reply.title,
                        document_date=reply.reply_date or letter.issued_date,
                        match_method=reply.match_method,
                    )
                )
                match_methods[reply.id] = reply.match_method

    selected_types = ["REGULATORY_LETTER"]
    if include_replies:
        selected_types.append("REGULATORY_REPLY")
    hits = search_official_document_chunks(
        db,
        stock,
        query=query,
        days=days,
        limit=limit,
        document_types=selected_types,
    )
    checked = 0
    downloaded = 0
    warnings: list[str] = []
    if not hits and max_documents:
        normalized, terms = _terms(query)
        cached_keys = _content_keys(db, stock.id, selected_types)
        ranked, fallback_used = _rank_candidates(
            candidates,
            normalized,
            terms,
            cached_keys,
        )
        checked, downloaded, warnings = _extract_candidates(
            db,
            stock,
            ranked,
            max_documents,
        )
        if fallback_used and checked:
            warnings.append("监管文件标题未命中关键词，仅补充检查了最近文件")
        hits = search_official_document_chunks(
            db,
            stock,
            query=query,
            days=days,
            limit=limit,
            document_types=selected_types,
        )
    evidence = [
        _hit_evidence(
            hit,
            match_method=(
                match_methods.get(hit.document_id)
                if hit.document_type == "REGULATORY_REPLY"
                else None
            ),
        )
        for hit in hits
    ]
    return EvidenceSearchResult(
        status=_result_status(evidence, warnings),
        as_of=metadata["fetched_at"],
        data={
            "symbol": stock.symbol,
            "stock_name": stock.name,
            "query": query,
            "range_start": metadata["range_start"],
            "range_end": metadata["range_end"],
            "metadata_cache_status": metadata["cache_status"],
            "letter_count": len(letters),
            "reply_count": sum(len(letter.replies) for letter in letters),
            "documents_checked": checked,
            "pdfs_downloaded": downloaded,
            "evidence_count": len(evidence),
        },
        evidence=evidence,
        warnings=warnings,
    )

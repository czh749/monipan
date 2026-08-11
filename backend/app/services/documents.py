"""Safe, on-demand download and text extraction for official exchange PDFs."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CompanyAnnouncement,
    OfficialDocumentContent,
    RegulatoryLetter,
    RegulatoryLetterReply,
)
from .document_search import DocumentSearchError, ensure_document_chunks


DOCUMENT_TYPES = {
    "ANNOUNCEMENT",
    "REGULATORY_LETTER",
    "REGULATORY_REPLY",
}
OFFICIAL_DOMAIN_SUFFIXES = ("sse.com.cn", "szse.cn")
DOCUMENT_MAX_BYTES = int(
    os.getenv("MONIPAN_DOCUMENT_MAX_BYTES", str(25 * 1024 * 1024))
)
DOCUMENT_MAX_PAGES = int(os.getenv("MONIPAN_DOCUMENT_MAX_PAGES", "500"))
DOCUMENT_MAX_TEXT_CHARS = int(
    os.getenv("MONIPAN_DOCUMENT_MAX_TEXT_CHARS", "10000000")
)
DOCUMENT_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("MONIPAN_DOCUMENT_CONNECT_TIMEOUT_SECONDS", "5")
)
DOCUMENT_READ_TIMEOUT_SECONDS = float(
    os.getenv("MONIPAN_DOCUMENT_READ_TIMEOUT_SECONDS", "60")
)
DOCUMENT_MAX_REDIRECTS = int(os.getenv("MONIPAN_DOCUMENT_MAX_REDIRECTS", "5"))
DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
    "Connection": "close",
}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
PDF_HEADER_RE = re.compile(br"^\s*%PDF-\d\.\d")
_LOCKS_GUARD = threading.Lock()
_DOCUMENT_LOCKS: dict[tuple[str, int], threading.Lock] = {}


class OfficialDocumentError(RuntimeError):
    """Raised when an official PDF cannot be safely downloaded or extracted."""


@dataclass(frozen=True)
class DocumentSource:
    document_type: str
    document_id: int
    stock_id: int
    title: str
    document_date: date
    source_url: str
    source_content_hash: str


@dataclass(frozen=True)
class ExtractedPdf:
    page_count: int
    text_content: str
    page_offsets: list[dict[str, Any]]
    text_sha256: str
    status: str
    warning: str | None


@dataclass(frozen=True)
class OfficialDocumentText:
    content_id: int
    document_type: str
    document_id: int
    stock_id: int
    title: str
    source_url: str
    cache_status: str
    extraction_status: str
    file_size: int
    file_sha256: str
    page_count: int
    text_content: str
    page_offsets: list[dict[str, Any]]
    text_sha256: str
    extraction_warning: str | None
    extracted_at: datetime

    def page_text(self, page_number: int) -> str:
        """Return one extracted page using the persisted character offsets."""
        entry = next(
            (
                item
                for item in self.page_offsets
                if item.get("page_number") == page_number
            ),
            None,
        )
        if entry is None:
            raise IndexError(f"PDF 不包含第 {page_number} 页")
        return self.text_content[
            int(entry["start_char"]):int(entry["end_char"])
        ]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _document_lock(document_type: str, document_id: int) -> threading.Lock:
    key = (document_type, document_id)
    with _LOCKS_GUARD:
        return _DOCUMENT_LOCKS.setdefault(key, threading.Lock())


def _normalize_document_type(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in DOCUMENT_TYPES:
        raise OfficialDocumentError(f"不支持的官方文档类型：{value}")
    return normalized


def _valid_official_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in OFFICIAL_DOMAIN_SUFFIXES
    )


def _validate_official_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not _valid_official_host(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise OfficialDocumentError("文档链接不是受信任的沪深交易所 HTTPS 地址")
    return value


def _resolve_source(
    db: Session,
    document_type: str,
    document_id: int,
    expected_stock_id: int | None,
) -> DocumentSource:
    if document_type == "ANNOUNCEMENT":
        item = db.get(CompanyAnnouncement, document_id)
        if item is None:
            raise OfficialDocumentError("公司公告不存在")
        source = DocumentSource(
            document_type=document_type,
            document_id=item.id,
            stock_id=item.stock_id,
            title=item.title,
            document_date=item.announcement_date,
            source_url=item.source_url,
            source_content_hash=item.content_hash,
        )
    elif document_type == "REGULATORY_LETTER":
        item = db.get(RegulatoryLetter, document_id)
        if item is None:
            raise OfficialDocumentError("监管函不存在")
        source = DocumentSource(
            document_type=document_type,
            document_id=item.id,
            stock_id=item.stock_id,
            title=item.title,
            document_date=item.issued_date,
            source_url=item.source_url,
            source_content_hash=item.content_hash,
        )
    else:
        reply = db.scalar(
            select(RegulatoryLetterReply).where(
                RegulatoryLetterReply.id == document_id
            )
        )
        if reply is None:
            raise OfficialDocumentError("监管函回复不存在")
        letter = db.get(RegulatoryLetter, reply.regulatory_letter_id)
        if letter is None:
            raise OfficialDocumentError("监管函回复缺少所属监管函")
        source = DocumentSource(
            document_type=document_type,
            document_id=reply.id,
            stock_id=letter.stock_id,
            title=reply.title,
            document_date=reply.reply_date or letter.issued_date,
            source_url=reply.source_url,
            source_content_hash=reply.content_hash,
        )

    if expected_stock_id is not None and source.stock_id != expected_stock_id:
        raise OfficialDocumentError("官方文档不属于指定股票")
    _validate_official_url(source.source_url)
    return source


def _cache_is_usable(
    cached: OfficialDocumentContent | None,
    source: DocumentSource,
) -> bool:
    return bool(
        cached is not None
        and cached.status in {"READY", "NO_TEXT"}
        and cached.source_url == source.source_url
        and cached.source_content_hash == source.source_content_hash
    )


def _temporary_pdf_path() -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix="monipan-official-",
        suffix=".pdf",
    )
    os.close(descriptor)
    return Path(raw_path)


def _download_pdf(source_url: str) -> tuple[Path, str, int, str]:
    """Stream one official PDF to a temporary file after validating redirects."""
    current_url = _validate_official_url(source_url)
    temporary_path = _temporary_pdf_path()
    response = None
    try:
        for redirect_count in range(DOCUMENT_MAX_REDIRECTS + 1):
            try:
                response = requests.get(
                    current_url,
                    headers=DOWNLOAD_HEADERS,
                    timeout=(
                        DOCUMENT_CONNECT_TIMEOUT_SECONDS,
                        DOCUMENT_READ_TIMEOUT_SECONDS,
                    ),
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise OfficialDocumentError(f"官方 PDF 下载失败：{exc}") from exc

            if response.status_code not in REDIRECT_STATUSES:
                break
            location = response.headers.get("Location")
            response.close()
            response = None
            if not location:
                raise OfficialDocumentError("官方 PDF 重定向缺少目标地址")
            if redirect_count >= DOCUMENT_MAX_REDIRECTS:
                raise OfficialDocumentError("官方 PDF 重定向次数过多")
            current_url = _validate_official_url(urljoin(current_url, location))
        if response is None:
            raise OfficialDocumentError("官方 PDF 下载未返回有效响应")
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OfficialDocumentError(f"官方 PDF 下载失败：{exc}") from exc

        declared_length = response.headers.get("Content-Length")
        if declared_length:
            try:
                if int(declared_length) > DOCUMENT_MAX_BYTES:
                    raise OfficialDocumentError("官方 PDF 超过允许的文件大小")
            except ValueError as exc:
                raise OfficialDocumentError("官方 PDF 文件大小响应头格式异常") from exc

        digest = hashlib.sha256()
        file_size = 0
        header = bytearray()
        with temporary_path.open("wb") as stream:
            try:
                chunks = response.iter_content(chunk_size=64 * 1024)
                for chunk in chunks:
                    if not chunk:
                        continue
                    file_size += len(chunk)
                    if file_size > DOCUMENT_MAX_BYTES:
                        raise OfficialDocumentError("官方 PDF 超过允许的文件大小")
                    if len(header) < 16:
                        header.extend(chunk[: 16 - len(header)])
                    digest.update(chunk)
                    stream.write(chunk)
            except requests.RequestException as exc:
                raise OfficialDocumentError(f"官方 PDF 下载中断：{exc}") from exc

        if not PDF_HEADER_RE.match(bytes(header)):
            raise OfficialDocumentError("官方链接返回的内容不是有效 PDF")
        mime_type = response.headers.get("Content-Type", "application/pdf")
        mime_type = mime_type.split(";", 1)[0].strip().lower() or "application/pdf"
        return temporary_path, digest.hexdigest(), file_size, mime_type
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        if response is not None:
            response.close()


def _normalize_page_text(value: str) -> str:
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in value.split("\n")]
    return "\n".join(lines).strip()


def _extract_pdf(path: Path) -> ExtractedPdf:
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise OfficialDocumentError("官方 PDF 已加密，无法抽取正文")
        page_count = len(reader.pages)
    except (PdfReadError, ValueError, OSError) as exc:
        raise OfficialDocumentError(f"官方 PDF 解析失败：{exc}") from exc

    if page_count < 1:
        raise OfficialDocumentError("官方 PDF 不包含任何页面")
    if page_count > DOCUMENT_MAX_PAGES:
        raise OfficialDocumentError("官方 PDF 超过允许的页数")

    page_texts: list[str] = []
    failed_pages: list[int] = []
    total_chars = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = _normalize_page_text(page.extract_text() or "")
        except Exception:
            text = ""
            failed_pages.append(page_number)
        total_chars += len(text)
        if total_chars > DOCUMENT_MAX_TEXT_CHARS:
            raise OfficialDocumentError("官方 PDF 抽取文本超过允许的字符数")
        page_texts.append(text)

    text_parts: list[str] = []
    page_offsets: list[dict[str, Any]] = []
    cursor = 0
    for page_number, page_text in enumerate(page_texts, start=1):
        if text_parts:
            text_parts.append("\n\n")
            cursor += 2
        start_char = cursor
        text_parts.append(page_text)
        cursor += len(page_text)
        page_offsets.append(
            {
                "page_number": page_number,
                "start_char": start_char,
                "end_char": cursor,
                "text_sha256": hashlib.sha256(
                    page_text.encode("utf-8")
                ).hexdigest(),
            }
        )
    text_content = "".join(text_parts)
    warning = None
    if failed_pages:
        displayed = ", ".join(str(value) for value in failed_pages[:20])
        suffix = " 等" if len(failed_pages) > 20 else ""
        warning = f"第 {displayed}{suffix} 页文字抽取失败"
    status = "READY" if text_content.strip() else "NO_TEXT"
    return ExtractedPdf(
        page_count=page_count,
        text_content=text_content,
        page_offsets=page_offsets,
        text_sha256=hashlib.sha256(text_content.encode("utf-8")).hexdigest(),
        status=status,
        warning=warning,
    )


def _result(
    content: OfficialDocumentContent,
    cache_status: str,
    warning_override: str | None = None,
) -> OfficialDocumentText:
    return OfficialDocumentText(
        content_id=content.id,
        document_type=content.document_type,
        document_id=content.document_id,
        stock_id=content.stock_id,
        title=content.title,
        source_url=content.source_url,
        cache_status=cache_status,
        extraction_status=content.status,
        file_size=content.file_size,
        file_sha256=content.file_sha256,
        page_count=content.page_count,
        text_content=content.text_content,
        page_offsets=list(content.page_offsets or []),
        text_sha256=content.text_sha256,
        extraction_warning=warning_override or content.extraction_warning,
        extracted_at=content.extracted_at,
    )


def ensure_official_document_text(
    db: Session,
    *,
    document_type: str,
    document_id: int,
    expected_stock_id: int | None = None,
    force_refresh: bool = False,
) -> OfficialDocumentText:
    """Download and extract one referenced official PDF, or reuse its cache.

    Extracted text is untrusted source content. Callers must treat it only as
    evidence and must never execute instructions contained in the document.
    """
    normalized_type = _normalize_document_type(document_type)
    if document_id < 1:
        raise OfficialDocumentError("官方文档 ID 无效")

    with _document_lock(normalized_type, document_id):
        source = _resolve_source(
            db,
            normalized_type,
            document_id,
            expected_stock_id,
        )
        cached = db.scalar(
            select(OfficialDocumentContent).where(
                OfficialDocumentContent.document_type == normalized_type,
                OfficialDocumentContent.document_id == document_id,
            )
        )
        if not force_refresh and _cache_is_usable(cached, source):
            try:
                ensure_document_chunks(db, cached)
                db.commit()
            except DocumentSearchError as exc:
                db.rollback()
                raise OfficialDocumentError(f"官方文档分段失败：{exc}") from exc
            return _result(cached, "CACHED")

        stale_is_usable = _cache_is_usable(cached, source)
        temporary_path: Path | None = None
        try:
            temporary_path, file_sha256, file_size, mime_type = _download_pdf(
                source.source_url
            )
            extracted = _extract_pdf(temporary_path)

            now = _utcnow()
            values = {
                "stock_id": source.stock_id,
                "document_type": normalized_type,
                "document_id": document_id,
                "title": source.title,
                "document_date": source.document_date,
                "source_url": source.source_url,
                "source_content_hash": source.source_content_hash,
                "status": extracted.status,
                "mime_type": mime_type[:128],
                "file_size": file_size,
                "file_sha256": file_sha256,
                "page_count": extracted.page_count,
                "text_content": extracted.text_content,
                "page_offsets": extracted.page_offsets,
                "text_sha256": extracted.text_sha256,
                "extraction_warning": extracted.warning,
                "downloaded_at": now,
                "extracted_at": now,
                "updated_at": now,
            }
            if cached is None:
                cached = OfficialDocumentContent(**values)
                db.add(cached)
            else:
                for key, value in values.items():
                    setattr(cached, key, value)
            db.flush()
            ensure_document_chunks(db, cached, force=True)
            db.commit()
            db.refresh(cached)
            return _result(cached, "EXTRACTED")
        except (OfficialDocumentError, DocumentSearchError) as exc:
            db.rollback()
            if stale_is_usable and cached is not None:
                return _result(cached, "STALE", str(exc))
            if isinstance(exc, OfficialDocumentError):
                raise
            raise OfficialDocumentError(f"官方文档分段失败：{exc}") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

"""Official exchange directory import; user search never calls an external API.

Run explicitly after migration: python -m app.services.security_catalog sync
Only public directory metadata is updated; Stock quote rows are not populated.
"""

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET

import requests
from sqlalchemy import case, func, or_, select

from ..models import SecurityCatalogEntry, Stock, WatchlistItem
from .stock_activity import CUSTOM_STOCK_LIMIT, DEFAULT_SYMBOLS

SSE_SOURCE = "https://www.sse.com.cn/assortment/stock/list/share/"
SZSE_SOURCE = "https://www.szse.cn/market/product/stock/list/index.html"
SSE_API = "https://query.sse.com.cn/sseQuery/commonQuery.do"
SZSE_API = "https://www.szse.cn/api/report/ShowReport"
SZSE_COUNT_API = "https://www.szse.cn/api/report/ShowReport/data"
XML_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
MAX_BYTES = 8 * 1024 * 1024


class CatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogRecord:
    symbol: str
    exchange: str
    name: str
    full_name: str | None
    board: str
    industry: str
    listing_date: date | None
    source_url: str


def text_value(value, maximum=256):
    text = str(value or "").strip()
    if "\ufffd" in text or len(text) > maximum or any(ord(c) < 32 for c in text):
        raise CatalogError("Invalid directory text")
    return text


def symbol_value(value):
    text = str(value or "").strip()
    if re.fullmatch(r"\d{1,6}\.0", text):
        text = text[:-2]
    if not re.fullmatch(r"[0-9]{1,6}", text):
        raise CatalogError("Invalid directory security code")
    return text.zfill(6)


def date_value(value):
    text = str(value or "").strip()
    if text in ("", "-", "--"):
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise CatalogError("Invalid listing date")


def parse_sse(payload, stock_type):
    if stock_type not in ("1", "8") or not isinstance(payload, dict):
        raise CatalogError("Unexpected SSE directory")
    rows = payload.get("result")
    total = payload.get("pageHelp", {}).get("total")
    if not isinstance(rows, list) or not rows or len(rows) != int(total or 0):
        raise CatalogError("Incomplete SSE directory snapshot")
    records = []
    for row in rows:
        if row.get("STOCK_TYPE") != stock_type:
            raise CatalogError("Unexpected SSE security type")
        symbol = symbol_value(row.get("A_STOCK_CODE"))
        # Scope exclusion: STAR CDRs (689xxx) are not A shares. This does not
        # infer trading eligibility; every newly added stock is research-only.
        if stock_type == "8" and symbol.startswith("689"):
            continue
        if not symbol.startswith(("600", "601", "603", "605") if stock_type == "1" else ("688",)):
            raise CatalogError("Unexpected SSE A-share identity")
        name = text_value(row.get("SEC_NAME_CN"), 40)
        if not name:
            raise CatalogError("Missing security name")
        records.append(CatalogRecord(symbol, "SSE", name, text_value(row.get("FULL_NAME")) or None,
                                     "主板" if stock_type == "1" else "科创板",
                                     text_value(row.get("CSRC_CODE_DESC"), 80), date_value(row.get("LIST_DATE")), SSE_SOURCE))
    return records


def xlsx_rows(payload):
    if len(payload) > MAX_BYTES:
        raise CatalogError("Directory download too large")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 32 * 1024 * 1024:
                raise CatalogError("Directory workbook too large")
            strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                strings = ["".join(n.itertext()) for n in shared.findall("m:si", XML_NS)]
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            rows = []
            for row in sheet.findall("m:sheetData/m:row", XML_NS):
                cells = {}
                for cell in row.findall("m:c", XML_NS):
                    ref = re.match(r"[A-Z]+", cell.get("r", ""))
                    if ref is None:
                        raise CatalogError("Invalid workbook cell")
                    value = cell.find("m:v", XML_NS)
                    text = value.text if value is not None else ""
                    if cell.get("t") == "s":
                        text = strings[int(text)]
                    elif cell.get("t") == "inlineStr":
                        text = "".join(n.text or "" for n in cell.findall("m:is//m:t", XML_NS))
                    cells[ref.group()] = text or ""
                rows.append(cells)
            if len(rows) < 2 or len(rows) > 10001:
                raise CatalogError("Empty or oversized directory workbook")
            headers = rows[0]
            return [{headers[col]: value for col, value in row.items() if col in headers} for row in rows[1:]]
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, IndexError) as exc:
        raise CatalogError("Invalid directory workbook") from exc


def parse_szse(payload, *, expected_count=None):
    rows = xlsx_rows(payload)
    if expected_count is not None and len(rows) != expected_count:
        raise CatalogError("Incomplete SZSE directory snapshot")
    required = {"A股代码", "A股简称", "板块", "A股上市日期"}
    if not required.issubset(rows[0]):
        raise CatalogError("Unexpected SZSE A-share columns")
    records = []
    for row in rows:
        symbol = symbol_value(row.get("A股代码"))
        if not symbol.startswith(("000", "001", "002", "003", "300", "301", "302")):
            raise CatalogError("Unexpected SZSE A-share identity")
        name = text_value(row.get("A股简称"), 40)
        board = text_value(row.get("板块"), 32)
        if not name or board not in ("主板", "创业板"):
            raise CatalogError("Unexpected SZSE security identity")
        records.append(CatalogRecord(symbol, "SZSE", name, text_value(row.get("公司全称")) or None,
                                     board, text_value(row.get("所属行业"), 80),
                                     date_value(row.get("A股上市日期")), SZSE_SOURCE))
    return records


def download(session, url, params, referer):
    with session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0", "Referer": referer},
                     timeout=(5, 20), stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > MAX_BYTES:
                raise CatalogError("Directory download too large")
            chunks.append(chunk)
        return b"".join(chunks)


def fetch_directory():
    records = []
    with requests.Session() as session:
        session.trust_env = False
        for stock_type in ("1", "8"):
            params = {"sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L", "STOCK_TYPE": stock_type,
                      "COMPANY_STATUS": "2,4,5,7,8", "type": "inParams", "isPagination": "true",
                      "pageHelp.pageSize": "10000", "pageHelp.pageNo": "1"}
            raw = download(session, SSE_API, params, SSE_SOURCE)
            records.extend(parse_sse(json.loads(raw.decode("utf-8-sig")), stock_type))
        counts = json.loads(download(session, SZSE_COUNT_API,
                    {"SHOWTYPE": "JSON", "CATALOGID": "1110", "TABKEY": "tab1", "PAGENO": "1"}, SZSE_SOURCE))
        metadata = next((r["metadata"] for r in counts if r.get("metadata", {}).get("tabkey") == "tab1"), {})
        expected = int(metadata.get("recordcount", 0))
        if expected < 1:
            raise CatalogError("Missing SZSE directory count")
        records.extend(parse_szse(download(session, SZSE_API,
                       {"SHOWTYPE": "xlsx", "CATALOGID": "1110", "TABKEY": "tab1"}, SZSE_SOURCE), expected_count=expected))
    return records


def replace_directory(db, records, *, now=None):
    observed = now or datetime.now(UTC).replace(tzinfo=None)
    keys = {(record.exchange, record.symbol) for record in records}
    if not records or len(keys) != len(records) or {r.exchange for r in records} != {"SSE", "SZSE"}:
        raise CatalogError("Incomplete or duplicate directory identities")
    existing = {(row.exchange, row.symbol): row for row in db.scalars(select(SecurityCatalogEntry)).all()}
    previous_count = sum(row.is_current for row in existing.values())
    if previous_count and len(records) < previous_count * 0.8:
        raise CatalogError("Directory shrank unexpectedly; previous snapshot retained")
    try:
        for row in existing.values():
            row.is_current = False
        for record in records:
            key = (record.exchange, record.symbol)
            row = existing.get(key)
            if row is None:
                row = SecurityCatalogEntry(symbol=record.symbol, exchange=record.exchange)
                db.add(row)
            for name, value in asdict(record).items():
                setattr(row, name, value)
            row.collected_at, row.is_current = observed, True
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"count": len(records), "collected_at": observed.isoformat(), "exchanges": ["SSE", "SZSE"]}


def custom_stock_count(db, account_id):
    return db.scalar(select(func.count()).select_from(WatchlistItem).join(Stock).where(
        WatchlistItem.account_id == account_id, Stock.symbol.not_in(DEFAULT_SYMBOLS))) or 0


def search_directory(db, keyword, account_id, *, limit=20):
    keyword = keyword.strip()
    current = SecurityCatalogEntry.is_current.is_(True)
    count, observed = db.execute(select(func.count(), func.max(SecurityCatalogEntry.collected_at)).where(current)).one()
    added = set(db.scalars(select(Stock.symbol).join(WatchlistItem).where(WatchlistItem.account_id == account_id)).all())
    rows = []
    if keyword:
        matches = db.scalars(select(SecurityCatalogEntry).where(current, or_(
            SecurityCatalogEntry.symbol.contains(keyword, autoescape=True),
            SecurityCatalogEntry.name.contains(keyword, autoescape=True),
            SecurityCatalogEntry.full_name.contains(keyword, autoescape=True))).order_by(
                case((SecurityCatalogEntry.symbol == keyword, 0), (SecurityCatalogEntry.symbol.startswith(keyword), 1), else_=2),
                SecurityCatalogEntry.symbol).limit(limit)).all()
        rows = [{"symbol": row.symbol, "exchange": row.exchange, "name": row.name, "board": row.board,
                 "industry": row.industry, "listing_date": row.listing_date, "source_url": row.source_url,
                 "collected_at": row.collected_at, "trading_status": "UNKNOWN",
                 "is_default": row.symbol in DEFAULT_SYMBOLS, "added": row.symbol in added,
                 "research_only": row.symbol not in DEFAULT_SYMBOLS} for row in matches]
    state = "empty" if not count else ("stale" if observed < datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7) else "ready")
    return {"items": rows, "catalog": {"state": state, "count": count, "collected_at": observed},
            "quota": {"used": custom_stock_count(db, account_id), "limit": CUSTOM_STOCK_LIMIT}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["sync"])
    parser.parse_args()
    from ..database import SessionLocal
    from ..schema import require_current
    with SessionLocal() as db:
        require_current(db.connection())
        records = fetch_directory()
        print(json.dumps(replace_directory(db, records), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"event": "CATALOG_SYNC_FAILED", "error_type": type(exc).__name__,
                          "previous_snapshot_retained": True}))
        raise SystemExit(1)

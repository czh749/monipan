"""On-demand stock news search without pre-ingesting or persisting articles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import requests

from ..models import Stock


TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class NewsSearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class NewsSearchResult:
    provider: str
    query: str
    searched_at: datetime
    request_id: str | None
    results: list[dict[str, Any]]


def _timeout_seconds() -> float:
    try:
        value = float(os.getenv("MONIPAN_NEWS_TIMEOUT_SECONDS", "12"))
    except ValueError:
        value = 12.0
    return min(max(value, 3.0), 30.0)


def _valid_web_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return normalized


def _text(value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length].rstrip()}…"


def search_stock_news(
    stock: Stock,
    *,
    query: str,
    days: int,
    limit: int,
) -> NewsSearchResult:
    """Search recent news through Tavily; results remain untrusted evidence."""
    provider = os.getenv("MONIPAN_NEWS_PROVIDER", "tavily").strip().lower()
    if provider != "tavily":
        raise NewsSearchError(f"不支持的新闻搜索提供方：{provider or '未配置'}")
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise NewsSearchError("新闻搜索尚未配置 TAVILY_API_KEY")

    searched_at = datetime.now(UTC)
    search_query = f'"{stock.name}" {stock.symbol} A股 {query}'
    start_date = (searched_at.date() - timedelta(days=days - 1)).isoformat()
    try:
        response = requests.post(
            TAVILY_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": search_query,
                "topic": "news",
                "search_depth": "basic",
                "max_results": limit,
                "start_date": start_date,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            },
            timeout=_timeout_seconds(),
        )
    except requests.RequestException as exc:
        raise NewsSearchError("新闻搜索服务暂时不可访问") from exc

    if response.status_code in {401, 403}:
        raise NewsSearchError("新闻搜索密钥无效或无权限")
    if response.status_code in {429, 432, 433}:
        raise NewsSearchError("新闻搜索额度不足或请求过于频繁")
    if response.status_code != 200:
        raise NewsSearchError(f"新闻搜索服务返回异常状态 {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise NewsSearchError("新闻搜索服务返回了无法解析的数据") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise NewsSearchError("新闻搜索服务返回的数据结构异常")

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        source_url = _valid_web_url(item.get("url"))
        if source_url is None or source_url in seen_urls:
            continue
        title = _text(item.get("title"), 300)
        excerpt = _text(item.get("content"), 1000)
        relevance_text = f"{title} {excerpt}"
        if stock.name not in relevance_text and stock.symbol not in relevance_text:
            continue
        seen_urls.add(source_url)
        score = item.get("score")
        results.append(
            {
                "title": title or source_url,
                "source_url": source_url,
                "published_at": _text(item.get("published_date"), 80) or None,
                "excerpt": excerpt,
                "score": float(score) if isinstance(score, (int, float)) else None,
            }
        )

    return NewsSearchResult(
        provider="TAVILY",
        query=search_query,
        searched_at=searched_at,
        request_id=_text(payload.get("request_id"), 100) or None,
        results=results,
    )

"""Topic-based scholarly metadata search (Crossref polite pool).

Boundary of this provider: it returns *metadata and abstracts only*. It never
downloads full text, never bypasses a paywall/DRM/CAPTCHA, and never reads
browser state. Every hit is labelled either ``abstract`` (摘要级依据，生成主张
不得超出摘要) or ``metadata`` (仅检索线索，不得当作事实依据)。

Official documentation and compliance assumptions (re-checked 2026-09-14):

- Crossref REST API ``/works`` — https://api.crossref.org/swagger-ui/index.html
  and https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- Polite pool — https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-polite-pool/
  Identify the client with a ``mailto`` query parameter and/or a User-Agent that
  contains a contact address. Crossref does not publish a hard numeric quota for
  the polite pool; it asks clients to stay well below its public limits and to be
  courteous. This module issues at most one request per user-triggered search.
- Attribution: most Crossref bibliographic metadata is openly reusable, but
  abstracts may remain copyrighted by their publishers or authors. Every stored
  snapshot therefore keeps the provider id, DOI, canonical URL and evidence
  level so the reader-facing footnote can attribute the source accurately.
- No API key is required. Nothing secret is passed to the provider, so no
  credential can leak into a response body or a log line.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

# 复用既有的 SSRF/HTTPS 守卫，不另写一套网络校验。
from backend.app.services.literature_access import validate_public_https_url

logger = logging.getLogger(__name__)

CROSSREF_PROVIDER = "crossref"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
CROSSREF_SELECT = "DOI,title,author,issued,URL,abstract,container-title,type"
CROSSREF_ATTRIBUTION = (
    "Crossref REST API；书目元数据通常可开放使用，部分摘要可能受出版社或作者版权保护；"
    "使用时应标注 Crossref、出版方与 DOI"
)
CROSSREF_RATE_LIMIT_NOTE = (
    "Crossref polite pool：无公开硬性配额，官方要求客户端保持克制并在标识中"
    "提供联系方式；本模块每次用户主动检索只发出一次请求。"
)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SEARCH_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
MAX_ATTEMPTS = 2
USER_AGENT_BASE = "StudyAssistant/1.0 (lawful-literature-metadata)"

SUPPORTED_PROVIDERS = {
    CROSSREF_PROVIDER: {
        "label": "Crossref 元数据检索",
        "mode": "metadata_search",
        "attribution": CROSSREF_ATTRIBUTION,
        "rate_limit": CROSSREF_RATE_LIMIT_NOTE,
        "full_text": False,
    },
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class LiteratureSearchError(ValueError):
    """Recoverable, user-facing search failure. Never carries internals."""


def _clean(value: object, limit: int) -> str:
    return _WS_RE.sub(" ", str(value or "")).strip()[:limit]


def _strip_markup(raw: str) -> str:
    """Crossref abstracts are JATS XML; only the plain sentence text is kept."""
    text = _TAG_RE.sub(" ", raw or "")
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _first_text(value: object) -> str:
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _clean(item, 500)
            if text:
                return text
        return ""
    return _clean(value, 500)


def _author_string(value: object) -> str:
    names: list[str] = []
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, dict):
            continue
        name = _clean(entry.get("name"), 120)
        if not name:
            given, family = _clean(entry.get("given"), 60), _clean(entry.get("family"), 60)
            name = " ".join(part for part in (family, given) if part)
        if name and name not in names:
            names.append(name)
    return "、".join(names[:20])


def _year(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        try:
            year = int(parts[0][0])
        except (TypeError, ValueError):
            return None
        if 1000 <= year <= 2999:
            return year
    return None


def _normalize_item(item: object, retrieved_at: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    doi = _clean(item.get("DOI"), 255)
    title = _first_text(item.get("title"))
    if not doi or not title:
        return None
    url = _clean(item.get("URL"), 2000)
    if not url.startswith("https://"):
        # Only a canonical HTTPS locator may reach the client.
        url = f"https://doi.org/{quote(doi, safe='/')}"
    abstract = _strip_markup(str(item.get("abstract") or ""))[:4000]
    return {
        "provider": CROSSREF_PROVIDER,
        "provider_id": doi,
        "title": title[:500],
        "authors": _author_string(item.get("author"))[:1000],
        "year": _year(item.get("issued")),
        "doi": doi,
        "url": url,
        "container_title": _first_text(item.get("container-title"))[:255],
        "type": _clean(item.get("type"), 60),
        "abstract": abstract,
        "retrieved_at": retrieved_at,
        "evidence_level": "abstract" if abstract else "metadata",
    }


def ensure_public_https(url: str) -> str:
    """SSRF guard reused for provider endpoints and for stored snapshot URLs."""
    return validate_public_https_url(url)


async def _request_json(url: str, params: dict, headers: dict) -> dict:
    """Single bounded HTTPS request with a size cap, timeouts and one retry.

    Redirects are refused outright: the provider endpoint is a fixed origin, so
    any redirect is treated as an unexpected (potentially SSRF-ish) response.
    """
    payload = bytearray()
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, follow_redirects=False, headers=headers) as client:
        for attempt in range(MAX_ATTEMPTS):
            payload = bytearray()
            try:
                async with client.stream("GET", url, params=params) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        raise LiteratureSearchError("文献检索服务返回了意外的跳转地址")
                    if response.status_code == 429:
                        raise LiteratureSearchError("文献检索服务暂时限流，请稍后重试")
                    if response.status_code >= 500:
                        if attempt + 1 < MAX_ATTEMPTS:
                            continue
                        raise LiteratureSearchError("文献检索服务暂时不可用，请稍后重试")
                    if response.status_code != 200:
                        raise LiteratureSearchError("文献检索请求未被接受，请调整关键词后重试")
                    async for chunk in response.aiter_bytes(64 * 1024):
                        payload.extend(chunk)
                        if len(payload) > MAX_RESPONSE_BYTES:
                            raise LiteratureSearchError("文献检索结果过大，请缩小关键词范围")
                    break
            except httpx.TimeoutException as exc:
                if attempt + 1 < MAX_ATTEMPTS:
                    continue
                raise LiteratureSearchError("文献检索超时，请稍后重试") from exc
            except httpx.HTTPError as exc:
                if attempt + 1 < MAX_ATTEMPTS:
                    continue
                raise LiteratureSearchError("文献检索网络异常，请稍后重试") from exc
        else:
            raise LiteratureSearchError("文献检索服务暂时不可用，请稍后重试")
    try:
        decoded = json.loads(bytes(payload).decode("utf-8", errors="replace"))
    except (ValueError, TypeError) as exc:
        raise LiteratureSearchError("文献检索返回了无法解析的结果，请稍后重试") from exc
    if not isinstance(decoded, dict):
        raise LiteratureSearchError("文献检索返回了无法解析的结果，请稍后重试")
    return decoded


async def search_crossref(query: str, *, rows: int = 10, mailto: str = "") -> list[dict]:
    """Search Crossref ``/works`` and return normalized metadata snapshots."""
    term = _clean(query, 400)
    if len(term) < 3:
        raise LiteratureSearchError("检索关键词至少需要 3 个字符")
    try:
        url = ensure_public_https(CROSSREF_WORKS_URL)
    except ValueError as exc:
        raise LiteratureSearchError("无法解析文献检索服务地址") from exc
    contact = _clean(mailto, 255)
    params = {"query.bibliographic": term, "rows": str(max(1, min(int(rows), 25))),
              "select": CROSSREF_SELECT}
    if contact:
        params["mailto"] = contact
    headers = {"User-Agent": USER_AGENT_BASE + (f" (mailto:{contact})" if contact else ""),
               "Accept": "application/json"}
    payload = await _request_json(url, params, headers)
    message = payload.get("message")
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        raise LiteratureSearchError("文献检索返回了无法解析的结果，请稍后重试")
    retrieved_at = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    seen: set[str] = set()
    for item in items:
        normalized = _normalize_item(item, retrieved_at)
        if normalized and normalized["provider_id"] not in seen:
            seen.add(normalized["provider_id"])
            results.append(normalized)
    return results


async def search_literature(query: str, *, provider: str = CROSSREF_PROVIDER, rows: int = 10,
                            mailto: str = "") -> list[dict]:
    if provider != CROSSREF_PROVIDER:
        raise ValueError("暂不支持该文献元数据 provider")
    return await search_crossref(query, rows=rows, mailto=mailto)


def provider_capabilities() -> list[dict]:
    return [{"id": key, **value} for key, value in SUPPORTED_PROVIDERS.items()]

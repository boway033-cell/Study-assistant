"""Lawful literature access providers and download verification.

No paywall/DRM/2FA bypass is attempted. Browser-based institutional access is
a handoff URL only; the application never reads browser cookies or credentials.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx

MAX_PDF_BYTES = 200 * 1024 * 1024


@dataclass
class AccessCandidate:
    provider: str
    route: str
    url: str
    label: str
    direct_download: bool = True


def validate_public_https_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("仅允许不含凭据的 HTTPS 地址")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("不允许本地或私有网络地址")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("无法解析资源域名") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("不允许本地、私有或保留网络地址")
    return url.strip()


async def resolve_candidates(query: str, unpaywall_email: str = "") -> list[AccessCandidate]:
    q = query.strip()
    candidates: list[AccessCandidate] = []
    if q.startswith("https://"):
        candidates.append(AccessCandidate("direct_oa", "open_access", validate_public_https_url(q), "用户提供的开放获取地址"))
    arxiv = q.lower().removeprefix("arxiv:").strip()
    if re_match := __import__("re").fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", arxiv):
        candidates.append(AccessCandidate("arxiv", "open_access", f"https://arxiv.org/pdf/{re_match.group(0)}.pdf", "arXiv 开放全文"))
    doi = q.removeprefix("https://doi.org/").removeprefix("http://doi.org/").strip()
    if doi.lower().startswith("doi:"): doi = doi[4:].strip()
    if doi.startswith("10.") and "/" in doi and unpaywall_email:
        url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}?email={quote(unpaywall_email)}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json(); best = data.get("best_oa_location") or {}
                pdf_url = best.get("url_for_pdf")
                if pdf_url:
                    candidates.append(AccessCandidate("unpaywall", "open_access", validate_public_https_url(pdf_url), "Unpaywall 开放全文"))
    # Stable ordering and deduplication make provider expansion predictable.
    unique = {}
    for item in candidates: unique.setdefault(item.url, item)
    return list(unique.values())


async def download_verified_pdf(url: str, destination: Path) -> dict:
    current = validate_public_https_url(url)
    data = bytearray(); content_type = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=8), follow_redirects=False,
                                 headers={"User-Agent": "StudyAssistant/1.0 lawful-open-access"}) as client:
        for _ in range(6):
            async with client.stream("GET", current) as resp:
                if resp.status_code in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("location")
                    if not location: raise ValueError("资源重定向缺少地址")
                    current = validate_public_https_url(urljoin(current, location)); continue
                resp.raise_for_status(); content_type = resp.headers.get("content-type", "").split(";", 1)[0].lower()
                async for chunk in resp.aiter_bytes(1024 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_PDF_BYTES: raise ValueError("PDF 超过 200MB 限制")
                break
        else:
            raise ValueError("资源重定向次数过多")
    if not bytes(data[:1024]).lstrip().startswith(b"%PDF-"):
        raise ValueError("获取结果不是 PDF，可能是登录页或错误页面")
    destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(data)
    return {"resolved_url": current, "mime": content_type or "application/pdf", "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def build_library_handoff(library_url: str, query: str) -> str:
    base = validate_public_https_url(library_url)
    separator = "&" if "?" in base else "?"
    # Generic `q` keeps this provider institution-neutral; schools can configure a search endpoint.
    return f"{base}{separator}q={quote(query.strip(), safe='')}" if query.strip() else base


PROVIDER_CAPABILITIES = [
    {"id": "direct_oa", "mode": "download", "label": "开放获取 PDF", "implemented": True},
    {"id": "arxiv", "mode": "download", "label": "arXiv", "implemented": True},
    {"id": "unpaywall", "mode": "resolve", "label": "Unpaywall", "implemented": True},
    {"id": "institutional_browser", "mode": "browser_handoff", "label": "图书馆 / CARSI（当前 Chrome）", "implemented": True},
    {"id": "publisher_api", "mode": "provider_extension", "label": "出版社授权 API", "implemented": False},
]

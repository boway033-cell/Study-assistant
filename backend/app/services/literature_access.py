"""Lawful literature access providers and download verification.

No paywall/DRM/2FA bypass is attempted. Browser-based institutional access is
a handoff URL only; the application never reads browser cookies or credentials.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx

MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024


@dataclass
class AccessCandidate:
    provider: str
    route: str
    url: str
    label: str
    direct_download: bool = True
    status: str = "ready"
    message: str = ""


def validate_public_https_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("仅允许不含凭据的 HTTPS 地址")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("不允许本地或私有网络地址")
    try:
        literal_host = ipaddress.ip_address(host)
    except ValueError:
        literal_host = None
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("无法解析资源域名") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # Clash 等本机 TUN 代理使用 RFC 2544 的 198.18.0.0/15 作为域名 fake-ip；
        # 仅域名解析结果可放行，用户直接填写该网段 IP 仍拒绝。
        if literal_host is None and ip in ipaddress.ip_network("198.18.0.0/15"):
            continue
        if not ip.is_global:
            raise ValueError("不允许本地、私有或保留网络地址")
    return url.strip()


def discover_pdf_links(page_url: str, source: str) -> list[str]:
    """从静态页面证据发现 PDF；不执行任意 JavaScript。"""
    decoded = html.unescape(source or "")
    patterns = [
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
        r'<(?:a|iframe|embed|object|link)\b[^>]+(?:href|src|data)=["\']([^"\']+)["\']',
    ]
    links: list[str] = []
    for pattern in patterns:
        for value in re.findall(pattern, decoded, flags=re.I):
            lower = value.lower()
            if ("pdf" in lower or "download" in lower or "downpdf" in lower):
                links.append(urljoin(page_url, value))
    # 人大复印报刊资料详情页在脚本中用当前 id 拼接 /qw/DownPdf。
    parsed = urlparse(page_url)
    if parsed.hostname and parsed.hostname.lower().endswith("rdfybk.com"):
        match = re.search(r"(?:[?&]id=|\bid\s*[:=]\s*[\"']?)(\d{3,})", page_url + "\n" + decoded, re.I)
        if match:
            links.append(urljoin(page_url, f"/qw/DownPdf?id={match.group(1)}"))
    unique: list[str] = []
    for item in links:
        try:
            safe = validate_public_https_url(item)
        except ValueError:
            continue
        if safe not in unique:
            unique.append(safe)
    return unique[:12]


async def _read_probe(client: httpx.AsyncClient, url: str, limit: int = MAX_HTML_BYTES) -> tuple[str, str, bytes]:
    current = validate_public_https_url(url)
    for _ in range(6):
        async with client.stream("GET", current, headers={"Range": f"bytes=0-{limit - 1}"}) as resp:
            if resp.status_code in {301, 302, 303, 307, 308}:
                location = resp.headers.get("location")
                if not location:
                    raise ValueError("资源重定向缺少地址")
                current = validate_public_https_url(urljoin(current, location))
                continue
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";", 1)[0].lower()
            data = bytearray()
            async for chunk in resp.aiter_bytes(64 * 1024):
                data.extend(chunk)
                if len(data) >= limit:
                    break
            return current, content_type, bytes(data[:limit])
    raise ValueError("资源重定向次数过多")


async def _resolve_web_url(url: str) -> list[AccessCandidate]:
    headers = {"User-Agent": "StudyAssistant/1.0 lawful-literature-resolver"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8), follow_redirects=False,
                                 headers=headers) as client:
        try:
            current, content_type, data = await _read_probe(client, url)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            if urlparse(url).path.lower().endswith(".pdf"):
                return [AccessCandidate("direct_oa", "open_access", url, "用户提供的 PDF 地址",
                                        message=f"解析探测失败，导入时将再次校验：{exc}")]
            known_links = discover_pdf_links(url, "")
            if known_links:
                return [AccessCandidate("page_pdf", "browser_handoff", known_links[0],
                                        "已发现 PDF 入口（需当前 Chrome 登录）", False,
                                        "login_required", "来源页阻止后台访问；请在已登录 Chrome 中打开精确全文入口")]
            return [AccessCandidate("web_page", "browser_handoff", url, "在 Chrome 中打开来源页",
                                    False, "login_required", "页面需要登录或阻止了后台访问")]
        if data.lstrip().startswith(b"%PDF-") or content_type == "application/pdf":
            return [AccessCandidate("direct_oa", "open_access", current, "已验证的 PDF 地址")]
        if "html" not in content_type and not data.lstrip().startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
            return [AccessCandidate("web_page", "browser_handoff", current, "在 Chrome 中检查来源",
                                    False, "login_required", "后台无法确认该地址为 PDF")]
        source = data.decode("utf-8", errors="replace")
        links = discover_pdf_links(current, source)
        candidates: list[AccessCandidate] = []
        for link in links[:4]:
            try:
                # 用无来源页 Cookie 的新会话复核，确保后续独立导入能够复现。
                async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8), follow_redirects=False,
                                             headers=headers) as probe_client:
                    resolved, candidate_type, head = await _read_probe(probe_client, link, 16 * 1024)
                is_pdf = head.lstrip().startswith(b"%PDF-") or candidate_type == "application/pdf"
            except (httpx.HTTPError, ValueError, OSError):
                resolved, is_pdf = link, False
            if is_pdf:
                candidates.append(AccessCandidate("page_pdf", "open_access", resolved, "页面发现的 PDF"))
            else:
                candidates.append(AccessCandidate("page_pdf", "browser_handoff", link,
                    "已发现 PDF 入口（需当前 Chrome 登录）", False, "login_required",
                    "应用不会读取或保存浏览器 Cookie；请在已登录 Chrome 中打开"))
        if candidates:
            return candidates
        return [AccessCandidate("web_page", "browser_handoff", current, "未发现公开 PDF，打开来源页",
                                False, "login_required", "可在已登录 Chrome 中下载后回到资料库导入")]


async def resolve_candidates(query: str, unpaywall_email: str = "") -> list[AccessCandidate]:
    q = query.strip()
    candidates: list[AccessCandidate] = []
    if q.startswith("https://"):
        candidates.extend(await _resolve_web_url(validate_public_https_url(q)))
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

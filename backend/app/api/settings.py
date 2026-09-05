"""设置 API（docs/03-api.md §6）— 仅 DeepSeek 云端配置"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core import crypto
from backend.app.core.config import DEEPSEEK_MODELS, settings as app_settings
from backend.app.core.database import get_db
from backend.app.models import Book, Chunk, Setting
from backend.app.schemas import ProbeItem, ProbeResp, SettingsResp, SettingsUpdateReq
from backend.app.services.llm import (LLM_TASKS, PROFILES_KEY, ROUTING_KEY, LLMRouter,
                                      load_llm_config, load_provider_routing)
from backend.app.services.vision import VisionProvider, load_vision_config

router = APIRouter(prefix="/api", tags=["settings"])
_PROFILES_KEY = PROFILES_KEY


class CompatibleProviderWrite(BaseModel):
    id: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    capability: str = Field(pattern="^(text|vision)$")
    vendor: str = Field(default="custom", pattern="^(kimi|zhipu|qwen|openai|anthropic|gemini|custom)$")
    protocol: str = Field(default="openai_chat", pattern="^(openai_chat|anthropic_messages|google_generate)$")
    base_url: str = Field(min_length=8, max_length=1000)
    model: str = Field(min_length=1, max_length=120)
    api_key: str | None = Field(default=None, max_length=1000)


class ProviderModelDiscoveryReq(BaseModel):
    provider_id: str | None = Field(default=None, max_length=40)
    protocol: str = Field(default="openai_chat", pattern="^(openai_chat|anthropic_messages|google_generate)$")
    base_url: str = Field(min_length=8, max_length=1000)
    api_key: str | None = Field(default=None, max_length=1000)


class StorageCleanupReq(BaseModel):
    categories: list[str] = Field(min_length=1, max_length=3)


class ProviderRoutingWrite(BaseModel):
    default_provider_id: str = Field(min_length=3, max_length=40)
    task_routes: dict[str, str] = Field(default_factory=dict)
    fallback_provider_ids: list[str] = Field(default_factory=list, max_length=5)

_DEFAULTS = {
    "deepseek_api_key": app_settings.deepseek_api_key,
    "deepseek_base_url": app_settings.deepseek_base_url,
    "deepseek_model": app_settings.deepseek_model,
    "vision_api_key": app_settings.vision_api_key,
    "vision_base_url": app_settings.vision_base_url,
    "vision_model": app_settings.vision_model,
    "rag_top_k": str(app_settings.rag_top_k),
    "vector_search": "false",
}


def _get_setting(db: Session, key: str) -> str:
    s = db.get(Setting, key)
    return s.value if s else _DEFAULTS.get(key, "")


def _set_setting(db: Session, key: str, value: str) -> None:
    s = db.get(Setting, key)
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return key[:3] + "***" + key[-4:]


def _compatible_profiles(db: Session) -> list[dict]:
    raw = _get_setting(db, _PROFILES_KEY)
    try:
        values = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        values = []
    return [item for item in values if isinstance(item, dict)]


def _validate_api_base(value: str) -> str:
    url = value.strip().rstrip("/")
    # 用户经常粘贴完整调用地址；连接配置统一保存到 API 根路径，后续才能
    # 正确拼接 /models、/chat/completions 等资源。
    for suffix in ("/chat/completions", "/responses", "/messages", "/models"):
        if url.lower().endswith(suffix):
            url = url[:-len(suffix)].rstrip("/")
            break
    parsed = urlparse(url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "Base URL 必须为 HTTPS；仅 localhost/127.0.0.1 可使用 HTTP")
    return url


def _model_ids(payload) -> list[str]:
    """兼容 OpenAI、Anthropic、Gemini 以及常见本地网关的模型列表结构。"""
    values = payload if isinstance(payload, list) else None
    if isinstance(payload, dict):
        for key in ("data", "models", "items"):
            if isinstance(payload.get(key), list):
                values = payload[key]
                break
    models = []
    for item in values or []:
        if isinstance(item, dict):
            model_id = item.get("id") or item.get("name") or item.get("model")
        else:
            model_id = str(item)
        if model_id:
            models.append(str(model_id).removeprefix("models/"))
    return sorted(set(models), key=str.casefold)


def _model_list_urls(base_url: str) -> list[str]:
    base = _validate_api_base(base_url)
    urls = [f"{base}/models"]
    path = urlparse(base).path.rstrip("/").lower()
    if not path.endswith(("/v1", "/v1beta", "/v4")):
        urls.append(f"{base}/v1/models")
    return list(dict.fromkeys(urls))


async def _discover_models(cfg: dict) -> dict:
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    api_key = str(cfg.get("api_key") or "")
    if cfg.get("protocol") == "anthropic_messages":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    elif cfg.get("protocol") == "google_generate":
        params = {"key": api_key}
    elif api_key:
        headers = {"Authorization": f"Bearer {api_key}"}
    failures = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5)) as client:
        for url in _model_list_urls(str(cfg.get("base_url") or "")):
            try:
                response = await client.get(url, headers=headers, params=params)
            except httpx.HTTPError as exc:
                failures.append(f"{url}: {type(exc).__name__}")
                continue
            if response.status_code != 200:
                failures.append(f"{url}: HTTP {response.status_code} {response.text[:120]}")
                continue
            try:
                models = _model_ids(response.json())
            except ValueError:
                failures.append(f"{url}: 返回内容不是 JSON")
                continue
            if models:
                return {"items": models, "endpoint": url}
            failures.append(f"{url}: 未返回模型条目")
    detail = "；".join(failures[-3:]) or "接口未返回可用模型"
    raise HTTPException(502, f"模型列表读取失败：{detail}")


@router.get("/settings", response_model=SettingsResp)
def get_settings(db: Session = Depends(get_db)):
    cfg = load_llm_config(db)
    vcfg = load_vision_config(db)
    api_key = cfg["deepseek_api_key"]
    return SettingsResp(
        deepseek_api_key=_mask_key(api_key),
        deepseek_model=cfg["deepseek_model"] if cfg["deepseek_model"] in DEEPSEEK_MODELS else "flash",
        vision_api_key=_mask_key(vcfg["vision_api_key"]),
        vision_base_url=vcfg["vision_base_url"],
        vision_model=vcfg["vision_model"],
        rag_top_k=_get_setting(db, "rag_top_k"),
        vector_search=_get_setting(db, "vector_search") == "true",
        deepseek_configured=bool(api_key),
        vision_configured=bool(vcfg["vision_api_key"]),
        text_provider_configured=bool(cfg.get("configured")),
        active_text_provider=str(cfg.get("provider_name") or "DeepSeek"),
        active_text_model=str(cfg.get("model") or ""),
    )


@router.put("/settings")
def update_settings(req: SettingsUpdateReq, db: Session = Depends(get_db)):
    if req.deepseek_api_key is not None:
        _set_setting(db, "deepseek_api_key", crypto.encrypt(req.deepseek_api_key.strip()))
    if req.deepseek_model is not None:
        if req.deepseek_model not in DEEPSEEK_MODELS:
            raise HTTPException(400, f"deepseek_model 只能是 {list(DEEPSEEK_MODELS)} 之一")
        _set_setting(db, "deepseek_model", req.deepseek_model)
    if req.vision_api_key is not None:
        _set_setting(db, "vision_api_key", crypto.encrypt(req.vision_api_key.strip()))
    if req.vision_base_url is not None:
        _set_setting(db, "vision_base_url", _validate_api_base(req.vision_base_url))
    if req.vision_model is not None:
        _set_setting(db, "vision_model", req.vision_model.strip())
    if req.rag_top_k is not None:
        if not 1 <= req.rag_top_k <= 20:
            raise HTTPException(400, "rag_top_k 范围 1-20")
        _set_setting(db, "rag_top_k", str(req.rag_top_k))
    if req.vector_search is not None:
        _set_setting(db, "vector_search", "true" if req.vector_search else "false")
        # 同步到内存 settings，使向量模块开关即时生效
        import backend.app.core.config as _cfg
        _cfg.settings.vector_search = req.vector_search
        if req.vector_search:
            from backend.app.services.rag import vector
            vector.ensure_model_ready()
    db.commit()
    return {"ok": True}


@router.get("/settings/probe", response_model=ProbeResp)
async def probe(db: Session = Depends(get_db)):
    cfg = load_llm_config(db)
    vcfg = load_vision_config(db)
    text_provider = LLMRouter.get("auto", cfg)
    text_ok, text_reason = await text_provider.check_available()
    vision = VisionProvider(api_key=vcfg["vision_api_key"], base_url=vcfg["vision_base_url"],
                            model=vcfg["vision_model"])
    vision_ok, vision_reason = await vision.check_available()
    return ProbeResp(
        deepseek=ProbeItem(ok=text_ok, reason=text_reason),
        vision=ProbeItem(ok=vision_ok, reason=vision_reason),
        text=ProbeItem(ok=text_ok, reason=text_reason),
    )


@router.get("/settings/providers")
def list_compatible_providers(db: Session = Depends(get_db)):
    deepseek_key = crypto.decrypt(_get_setting(db, "deepseek_api_key"))
    items = [{"id": "deepseek", "name": "DeepSeek", "vendor": "deepseek",
              "capability": "text", "protocol": "openai_chat",
              "base_url": _get_setting(db, "deepseek_base_url") or app_settings.deepseek_base_url,
              "model": _get_setting(db, "deepseek_model") or app_settings.deepseek_model,
              "configured": bool(deepseek_key), "api_key": _mask_key(deepseek_key), "builtin": True}]
    for profile in _compatible_profiles(db):
        key = crypto.decrypt(_get_setting(db, f"compatible_provider_key:{profile.get('id', '')}"))
        local = str(profile.get("base_url") or "").startswith(("http://localhost", "http://127.0.0.1"))
        items.append({**profile, "configured": bool(key) or local, "api_key": _mask_key(key)})
    routing = load_provider_routing(db)
    return {"default_text_provider": routing["default"], "default_vision_provider": "qwen-vl",
            "routing_locked": False, "task_routes": routing["tasks"], "tasks": list(LLM_TASKS),
            "fallback_provider_ids": routing.get("fallbacks", []),
            "items": items}


@router.post("/settings/providers", status_code=201)
def save_compatible_provider(req: CompatibleProviderWrite, db: Session = Depends(get_db)):
    provider_id = (req.id or uuid.uuid4().hex[:12]).strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{3,40}", provider_id):
        raise HTTPException(400, "接口 ID 只能包含小写字母、数字、_ 和 -")
    profiles = _compatible_profiles(db)
    profile = {"id": provider_id, "name": req.name.strip(), "vendor": req.vendor,
               "capability": req.capability,
               "protocol": req.protocol, "base_url": _validate_api_base(req.base_url),
               "model": req.model.strip()}
    existing = next((index for index, item in enumerate(profiles) if item.get("id") == provider_id), None)
    if existing is None:
        profiles.append(profile)
    else:
        profiles[existing] = profile
    _set_setting(db, _PROFILES_KEY, json.dumps(profiles, ensure_ascii=False))
    if req.api_key:
        _set_setting(db, f"compatible_provider_key:{provider_id}", crypto.encrypt(req.api_key.strip()))
    db.commit()
    configured = bool(req.api_key or crypto.decrypt(_get_setting(db, f"compatible_provider_key:{provider_id}")))
    configured = configured or profile["base_url"].startswith(("http://localhost", "http://127.0.0.1"))
    return {**profile, "configured": configured}


@router.delete("/settings/providers/{provider_id}", status_code=204)
def delete_compatible_provider(provider_id: str, db: Session = Depends(get_db)):
    routing = load_provider_routing(db)
    if provider_id == "deepseek":
        raise HTTPException(400, "内置 DeepSeek 连接不可删除")
    if provider_id == routing["default"] or provider_id in routing["tasks"].values() or provider_id in routing.get("fallbacks", []):
        raise HTTPException(409, "该连接正在被默认路由或功能路由使用，请先切换路由")
    profiles = [item for item in _compatible_profiles(db) if item.get("id") != provider_id]
    _set_setting(db, _PROFILES_KEY, json.dumps(profiles, ensure_ascii=False))
    key_row = db.get(Setting, f"compatible_provider_key:{provider_id}")
    if key_row:
        db.delete(key_row)
    db.commit()


@router.post("/settings/providers/{provider_id}/probe")
async def probe_compatible_provider(provider_id: str, db: Session = Depends(get_db)):
    if provider_id == "deepseek":
        provider = LLMRouter.get("auto", {
            "provider_id": "deepseek",
            "api_key": crypto.decrypt(_get_setting(db, "deepseek_api_key")),
            "base_url": _get_setting(db, "deepseek_base_url") or app_settings.deepseek_base_url,
            "deepseek_model": _get_setting(db, "deepseek_model") or app_settings.deepseek_model,
        })
        ok, reason = await provider.check_available()
        return {"ok": ok, "reason": reason}
    profile = next((item for item in _compatible_profiles(db) if item.get("id") == provider_id), None)
    if not profile:
        raise HTTPException(404, "兼容接口不存在")
    key = crypto.decrypt(_get_setting(db, f"compatible_provider_key:{provider_id}"))
    cfg = {**profile, "provider_id": provider_id, "provider_name": profile.get("name", provider_id),
           "api_key": key}
    ok, reason = await LLMRouter.get("auto", cfg).check_available()
    return {"ok": ok, "reason": reason}


@router.put("/settings/providers/routing")
def update_provider_routing(req: ProviderRoutingWrite, db: Session = Depends(get_db)):
    known = {"deepseek", *(item.get("id") for item in _compatible_profiles(db))}
    if req.default_provider_id not in known:
        raise HTTPException(400, "默认连接不存在")
    unknown_tasks = set(req.task_routes) - set(LLM_TASKS)
    unknown_providers = {value for value in req.task_routes.values() if value} - known
    unknown_fallbacks = set(req.fallback_provider_ids) - known
    if unknown_tasks:
        raise HTTPException(400, f"未知功能路由：{', '.join(sorted(unknown_tasks))}")
    if unknown_providers:
        raise HTTPException(400, f"路由连接不存在：{', '.join(sorted(unknown_providers))}")
    if unknown_fallbacks:
        raise HTTPException(400, f"降级连接不存在：{', '.join(sorted(unknown_fallbacks))}")
    value = {"default": req.default_provider_id,
             "tasks": {task: provider_id for task, provider_id in req.task_routes.items()
                       if provider_id and provider_id != req.default_provider_id},
             "fallbacks": [provider_id for provider_id in dict.fromkeys(req.fallback_provider_ids)
                           if provider_id != req.default_provider_id]}
    _set_setting(db, ROUTING_KEY, json.dumps(value, ensure_ascii=False))
    db.commit()
    return value


@router.get("/settings/providers/usage")
def provider_usage(db: Session = Depends(get_db)):
    try:
        rows = json.loads(_get_setting(db, "llm_usage_stats") or "{}")
    except (TypeError, ValueError):
        rows = {}
    items = sorted((value for value in rows.values() if isinstance(value, dict)),
                   key=lambda item: (item.get("provider_id", ""), item.get("task", "")))
    totals = {key: sum(int(item.get(key, 0)) for item in items) for key in
              ("calls", "successes", "failures", "fallback_activations", "input_chars", "output_chars", "estimated_tokens", "elapsed_ms")}
    return {"items": items, "totals": totals,
            "boundary": "Token 为按输入输出字符数除以 4 的本地估算；供应商未返回标准 usage 时不代表账单用量或费用。"}


@router.get("/settings/providers/{provider_id}/models")
async def list_provider_models(provider_id: str, db: Session = Depends(get_db)):
    if provider_id == "deepseek":
        cfg = {"provider_id": "deepseek", "protocol": "openai_chat",
               "api_key": crypto.decrypt(_get_setting(db, "deepseek_api_key")),
               "base_url": _get_setting(db, "deepseek_base_url") or app_settings.deepseek_base_url}
    else:
        profile = next((item for item in _compatible_profiles(db) if item.get("id") == provider_id), None)
        if not profile:
            raise HTTPException(404, "模型连接不存在")
        cfg = {**profile, "provider_id": provider_id,
               "api_key": crypto.decrypt(_get_setting(db, f"compatible_provider_key:{provider_id}"))}
    return await _discover_models(cfg)


@router.post("/settings/providers/models/discover")
async def discover_provider_models(req: ProviderModelDiscoveryReq, db: Session = Depends(get_db)):
    """在连接保存前发现模型；编辑连接时可复用已加密保存的密钥。"""
    api_key = (req.api_key or "").strip()
    if not api_key and req.provider_id:
        if req.provider_id == "deepseek":
            api_key = crypto.decrypt(_get_setting(db, "deepseek_api_key"))
        else:
            api_key = crypto.decrypt(_get_setting(db, f"compatible_provider_key:{req.provider_id}"))
    return await _discover_models({
        "protocol": req.protocol,
        "base_url": req.base_url,
        "api_key": api_key,
    })


def _path_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def _storage_items() -> list[dict]:
    roots = [
        ("originals", "原始文献", app_settings.uploads_dir, False, "不可自动恢复，永不清理"),
        ("database", "知识库数据库", app_settings.db_path, False, "由备份恢复，永不清理"),
        ("backups", "数据库备份", app_settings.data_dir / "backups", False, "按容量策略保留"),
        ("presentations", "PPTX 成品", app_settings.presentations_dir, False, "用户输出，永不清理"),
        ("ocr_cache", "OCR 文字层缓存", app_settings.data_dir / "ocr_cache", True, "再次识别时可重建"),
        ("office_rendered", "Office 页面缓存", app_settings.structured_dir / "office-rendered", True, "再次打开时可重建"),
        ("presentation_previews", "PPTX 预览图", app_settings.presentations_dir / "previews", True, "再次渲染时可重建"),
    ]
    return [{"key": key, "label": label, "bytes": _path_size(path), "clearable": clearable,
             "recoverability": recoverability} for key, label, path, clearable, recoverability in roots]


@router.get("/settings/storage")
def get_storage_usage():
    items = _storage_items()
    # 预览图包含在 presentations 中，汇总时避免重复计算。
    return {"total_bytes": sum(item["bytes"] for item in items if item["key"] != "presentation_previews"),
            "items": items, "protected": ["originals", "database", "backups", "presentations"]}


@router.get("/settings/capacity")
def get_capacity_status(db: Session = Depends(get_db)):
    """Report an explicit local-library tier; thresholds are operational guardrails, not hard limits."""
    books = int(db.scalar(select(func.count()).select_from(Book)) or 0)
    chunks = int(db.scalar(select(func.count()).select_from(Chunk)) or 0)
    database_bytes = _path_size(app_settings.db_path)
    thresholds = {
        "attention_above": {"books": 1000, "chunks": 250_000, "database_bytes": 8 * 1024 ** 3},
        "migration_review_above": {"books": 2000, "chunks": 1_000_000, "database_bytes": 20 * 1024 ** 3},
    }
    migrate = thresholds["migration_review_above"]
    attention = thresholds["attention_above"]
    if books > migrate["books"] or chunks > migrate["chunks"] or database_bytes > migrate["database_bytes"]:
        tier = "migration_review"
        recommendation = "已进入超大库评审区：先运行固定压力测试、完成可恢复备份，再评估 PostgreSQL/外部检索索引迁移。"
    elif books > attention["books"] or chunks > attention["chunks"] or database_bytes > attention["database_bytes"]:
        tier = "attention"
        recommendation = "接近个人库高负载区：建议运行容量基准、检查慢查询与备份恢复耗时，不要只凭资料本数判断。"
    else:
        tier = "normal"
        recommendation = "当前仍在 SQLite 个人知识库常规区间；继续关注检索延迟、数据库体积和备份可恢复性。"
    return {"tier": tier, "books": books, "chunks": chunks, "database_bytes": database_bytes,
            "thresholds": thresholds, "recommendation": recommendation,
            "boundary": "阈值是工程预警线，不是 SQLite 理论容量；迁移决策还应结合设备、全文长度、并发和实测延迟。"}


@router.post("/settings/storage/cleanup")
def cleanup_storage(req: StorageCleanupReq):
    allowed = {
        "ocr_cache": app_settings.data_dir / "ocr_cache",
        "office_rendered": app_settings.structured_dir / "office-rendered",
        "presentation_previews": app_settings.presentations_dir / "previews",
    }
    unknown = set(req.categories) - allowed.keys()
    if unknown:
        raise HTTPException(400, f"不可清理的存储分类：{', '.join(sorted(unknown))}")
    released = 0
    for category in set(req.categories):
        path = allowed[category]
        released += _path_size(path)
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "released_bytes": released, "storage": get_storage_usage()}

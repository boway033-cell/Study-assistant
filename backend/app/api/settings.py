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
from sqlalchemy.orm import Session

from backend.app.core import crypto
from backend.app.core.config import DEEPSEEK_MODELS, settings as app_settings
from backend.app.core.database import get_db
from backend.app.models import Setting
from backend.app.schemas import ProbeItem, ProbeResp, SettingsResp, SettingsUpdateReq
from backend.app.services.llm import DeepSeekProvider, load_llm_config
from backend.app.services.vision import VisionProvider, load_vision_config

router = APIRouter(prefix="/api", tags=["settings"])
_PROFILES_KEY = "compatible_provider_profiles"


class CompatibleProviderWrite(BaseModel):
    id: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    capability: str = Field(pattern="^(text|vision)$")
    protocol: str = Field(default="openai_chat", pattern="^openai_chat$")
    base_url: str = Field(min_length=8, max_length=1000)
    model: str = Field(min_length=1, max_length=120)
    api_key: str | None = Field(default=None, max_length=1000)


class StorageCleanupReq(BaseModel):
    categories: list[str] = Field(min_length=1, max_length=3)

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
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "兼容接口 Base URL 必须是不含凭据的 HTTPS 地址")
    return url


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
    deepseek = DeepSeekProvider(api_key=cfg["deepseek_api_key"], base_url=cfg["deepseek_base_url"],
                                model=cfg["deepseek_model"])
    deepseek_ok, deepseek_reason = await deepseek.check_available()
    vision = VisionProvider(api_key=vcfg["vision_api_key"], base_url=vcfg["vision_base_url"],
                            model=vcfg["vision_model"])
    vision_ok, vision_reason = await vision.check_available()
    return ProbeResp(
        deepseek=ProbeItem(ok=deepseek_ok, reason=deepseek_reason),
        vision=ProbeItem(ok=vision_ok, reason=vision_reason),
    )


@router.get("/settings/providers")
def list_compatible_providers(db: Session = Depends(get_db)):
    items = []
    for profile in _compatible_profiles(db):
        key = crypto.decrypt(_get_setting(db, f"compatible_provider_key:{profile.get('id', '')}"))
        items.append({**profile, "configured": bool(key), "api_key": _mask_key(key)})
    return {"default_text_provider": "deepseek", "default_vision_provider": "qwen-vl",
            "routing_locked": True, "items": items}


@router.post("/settings/providers", status_code=201)
def save_compatible_provider(req: CompatibleProviderWrite, db: Session = Depends(get_db)):
    provider_id = (req.id or uuid.uuid4().hex[:12]).strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{3,40}", provider_id):
        raise HTTPException(400, "接口 ID 只能包含小写字母、数字、_ 和 -")
    profiles = _compatible_profiles(db)
    profile = {"id": provider_id, "name": req.name.strip(), "capability": req.capability,
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
    return {**profile, "configured": bool(req.api_key or crypto.decrypt(_get_setting(db, f"compatible_provider_key:{provider_id}")))}


@router.delete("/settings/providers/{provider_id}", status_code=204)
def delete_compatible_provider(provider_id: str, db: Session = Depends(get_db)):
    profiles = [item for item in _compatible_profiles(db) if item.get("id") != provider_id]
    _set_setting(db, _PROFILES_KEY, json.dumps(profiles, ensure_ascii=False))
    key_row = db.get(Setting, f"compatible_provider_key:{provider_id}")
    if key_row:
        db.delete(key_row)
    db.commit()


@router.post("/settings/providers/{provider_id}/probe")
async def probe_compatible_provider(provider_id: str, db: Session = Depends(get_db)):
    profile = next((item for item in _compatible_profiles(db) if item.get("id") == provider_id), None)
    if not profile:
        raise HTTPException(404, "兼容接口不存在")
    key = crypto.decrypt(_get_setting(db, f"compatible_provider_key:{provider_id}"))
    if not key:
        return {"ok": False, "reason": "未配置 API Key"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            response = await client.get(f"{profile['base_url'].rstrip('/')}/models",
                                        headers={"Authorization": f"Bearer {key}"})
        if response.status_code == 200:
            return {"ok": True, "reason": f"接口可达；已配置模型 {profile['model']}"}
        return {"ok": False, "reason": f"HTTP {response.status_code}: {response.text[:160]}"}
    except httpx.HTTPError as exc:
        return {"ok": False, "reason": f"连接失败：{type(exc).__name__}: {exc}"}


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

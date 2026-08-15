"""设置 API（docs/03-api.md §6）— 仅 DeepSeek 云端配置"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.config import DEEPSEEK_MODELS, settings as app_settings
from backend.app.core.database import get_db
from backend.app.models import Setting
from backend.app.schemas import ProbeItem, ProbeResp, SettingsResp, SettingsUpdateReq
from backend.app.services.llm import DeepSeekProvider, load_llm_config
from backend.app.services.vision import VisionProvider, load_vision_config

router = APIRouter(prefix="/api", tags=["settings"])

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


@router.get("/settings", response_model=SettingsResp)
def get_settings(db: Session = Depends(get_db)):
    cfg = load_llm_config(db)
    vcfg = load_vision_config(db)
    api_key = cfg["deepseek_api_key"]
    return SettingsResp(
        deepseek_api_key=_mask_key(api_key),
        deepseek_model=cfg["deepseek_model"] if cfg["deepseek_model"] in DEEPSEEK_MODELS else "flash",
        vision_api_key=_mask_key(vcfg["vision_api_key"]),
        vision_model=vcfg["vision_model"],
        rag_top_k=_get_setting(db, "rag_top_k"),
        vector_search=_get_setting(db, "vector_search") == "true",
        deepseek_configured=bool(api_key),
        vision_configured=bool(vcfg["vision_api_key"]),
    )


@router.put("/settings")
def update_settings(req: SettingsUpdateReq, db: Session = Depends(get_db)):
    if req.deepseek_api_key is not None:
        _set_setting(db, "deepseek_api_key", req.deepseek_api_key.strip())
    if req.deepseek_model is not None:
        if req.deepseek_model not in DEEPSEEK_MODELS:
            raise HTTPException(400, f"deepseek_model 只能是 {list(DEEPSEEK_MODELS)} 之一")
        _set_setting(db, "deepseek_model", req.deepseek_model)
    if req.vision_api_key is not None:
        _set_setting(db, "vision_api_key", req.vision_api_key.strip())
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

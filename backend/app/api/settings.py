"""设置 API（docs/03-api.md §6）"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.config import settings as app_settings
from backend.app.core.database import get_db
from backend.app.models import Setting
from backend.app.schemas import ProbeItem, ProbeResp, SettingsResp, SettingsUpdateReq
from backend.app.services.llm import DeepSeekProvider, OllamaProvider, load_llm_config

router = APIRouter(prefix="/api", tags=["settings"])

_DEFAULTS = {
    "llm_mode": app_settings.llm_mode,
    "deepseek_api_key": app_settings.deepseek_api_key,
    "deepseek_base_url": app_settings.deepseek_base_url,
    "ollama_base_url": app_settings.ollama_base_url,
    "ollama_model": app_settings.ollama_model,
    "daily_new_cards": str(app_settings.daily_new_cards),
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
    api_key = cfg["deepseek_api_key"]
    return SettingsResp(
        llm_mode=cfg["llm_mode"],
        deepseek_api_key=_mask_key(api_key),
        ollama_model=cfg["ollama_model"],
        daily_new_cards=_get_setting(db, "daily_new_cards"),
        rag_top_k=_get_setting(db, "rag_top_k"),
        vector_search=_get_setting(db, "vector_search") == "true",
        ollama_connected=bool(cfg["ollama_base_url"]),
        deepseek_configured=bool(api_key),
    )


@router.put("/settings")
def update_settings(req: SettingsUpdateReq, db: Session = Depends(get_db)):
    if req.llm_mode is not None:
        if req.llm_mode not in ("local", "cloud"):
            raise HTTPException(400, "llm_mode 只能是 local 或 cloud")
        _set_setting(db, "llm_mode", req.llm_mode)
    if req.deepseek_api_key is not None:
        _set_setting(db, "deepseek_api_key", req.deepseek_api_key.strip())
    if req.daily_new_cards is not None:
        if not 1 <= req.daily_new_cards <= 200:
            raise HTTPException(400, "daily_new_cards 范围 1-200")
        _set_setting(db, "daily_new_cards", str(req.daily_new_cards))
    if req.rag_top_k is not None:
        if not 1 <= req.rag_top_k <= 20:
            raise HTTPException(400, "rag_top_k 范围 1-20")
        _set_setting(db, "rag_top_k", str(req.rag_top_k))
    if req.vector_search is not None:
        _set_setting(db, "vector_search", "true" if req.vector_search else "false")
    if req.ollama_model is not None:
        _set_setting(db, "ollama_model", req.ollama_model.strip())
    db.commit()
    return {"ok": True}


@router.get("/settings/probe", response_model=ProbeResp)
async def probe(db: Session = Depends(get_db)):
    cfg = load_llm_config(db)
    ollama = OllamaProvider(base_url=cfg["ollama_base_url"], model=cfg["ollama_model"])
    ollama_ok, ollama_reason = await ollama.check_available()
    deepseek = DeepSeekProvider(api_key=cfg["deepseek_api_key"], base_url=cfg["deepseek_base_url"])
    deepseek_ok, deepseek_reason = await deepseek.check_available()
    return ProbeResp(
        ollama=ProbeItem(ok=ollama_ok, reason=ollama_reason),
        deepseek=ProbeItem(ok=deepseek_ok, reason=deepseek_reason),
    )

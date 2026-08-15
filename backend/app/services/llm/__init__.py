"""LLM 抽象层：本地 Ollama ⇄ 云端 DeepSeek（docs/01-architecture.md §5）

配置优先级：数据库 settings 表（用户设置页写入）> 环境变量/内存默认值。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from backend.app.core.config import settings
from backend.app.models import Setting


def load_llm_config(db) -> dict[str, str]:
    """从数据库读取 LLM 配置；未设置项回退到环境默认。

    这是「设置页切换模式/填 Key 后生效」的关键：此前 LLM 层只读内存配置，
    导致云端切换与 API Key 全部不生效。
    """
    def _get(key: str, default: str) -> str:
        s = db.get(Setting, key)
        return s.value if s else default

    return {
        "llm_mode": _get("llm_mode", settings.llm_mode),
        "deepseek_api_key": _get("deepseek_api_key", settings.deepseek_api_key),
        "deepseek_base_url": _get("deepseek_base_url", settings.deepseek_base_url),
        "ollama_base_url": _get("ollama_base_url", settings.ollama_base_url),
        "ollama_model": _get("ollama_model", settings.ollama_model),
    }


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """流式对话，逐个产出文本增量。"""
        raise NotImplementedError

    async def check_available(self) -> tuple[bool, str]:
        """探测连通性。返回 (ok, 说明)。"""
        return True, ""


class OllamaProvider(LLMProvider):
    name = "local"

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
            async with client.stream("POST", url, json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:200]
                    raise RuntimeError(f"Ollama 返回 {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        import json
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        yield delta

    async def check_available(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code == 200:
                    models = [m.get("name", "") for m in resp.json().get("models", [])]
                    return True, f"可用，模型: {', '.join(models[:5]) or '无'}"
                return False, f"HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败: {type(e).__name__}"


class DeepSeekProvider(LLMProvider):
    name = "cloud"

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = "deepseek-chat"

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError("未配置 DeepSeek API Key，请在设置页填写")

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                    raise RuntimeError(f"DeepSeek 返回 {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        import json
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        yield delta

    async def check_available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "未配置 API Key"
        return True, "已配置"


class LLMRouter:
    """按 mode 返回 provider。mode: auto/local/cloud。

    cfg 由 load_llm_config(db) 提供（数据库优先）；不传时使用内存默认。
    """

    @staticmethod
    def get(mode: str = "auto", cfg: dict[str, Any] | None = None) -> LLMProvider:
        cfg = cfg or {}
        resolved = mode
        if mode == "auto":
            resolved = cfg.get("llm_mode") or settings.llm_mode
        if resolved == "cloud":
            return DeepSeekProvider(
                api_key=cfg.get("deepseek_api_key"),
                base_url=cfg.get("deepseek_base_url"),
            )
        return OllamaProvider(
            base_url=cfg.get("ollama_base_url"),
            model=cfg.get("ollama_model"),
        )

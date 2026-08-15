"""LLM 抽象层：仅云端 DeepSeek（本地 AI 部署已取消）

配置优先级：数据库 settings 表（用户设置页写入）> .env / 环境变量默认值。
模型档位：flash（deepseek-chat，快速）/ pro（deepseek-reasoner，深度推理）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from backend.app.core.config import DEEPSEEK_MODELS, settings
from backend.app.models import Setting


def load_llm_config(db) -> dict[str, str]:
    """从数据库读取 LLM 配置；未设置项回退到 .env / 内存默认。

    这是「设置页改模型/填 Key 后即时生效」的关键：LLM 层只从 DB 读配置。
    """
    def _get(key: str, default: str) -> str:
        s = db.get(Setting, key)
        return s.value if s else default

    return {
        "deepseek_api_key": _get("deepseek_api_key", settings.deepseek_api_key),
        "deepseek_base_url": _get("deepseek_base_url", settings.deepseek_base_url),
        "deepseek_model": _get("deepseek_model", settings.deepseek_model),
    }


def parse_json_response(text: str):
    """从 LLM 回答中稳健地提取 JSON（容忍 markdown 代码块与前后杂文）。

    依次尝试：直接解析 → 去 markdown 围栏 → 正则提取首个数组/对象块。
    """
    import json as _json
    import re

    if not text:
        return None
    text = text.strip()
    # 1. 直接解析
    try:
        return _json.loads(text)
    except (ValueError, TypeError):
        pass
    # 2. 去掉 markdown 围栏后解析
    fence = chr(96) * 3
    cleaned = re.sub(rf"^\s*{fence}(?:json)?\s*", "", text)
    cleaned = re.sub(rf"\s*{fence}$", "", cleaned).strip()
    try:
        return _json.loads(cleaned)
    except (ValueError, TypeError):
        pass
    # 3. 正则提取首个数组/对象块
    for pat in (r"\[.*?\]", r"\{.*?\}"):
        m = re.search(pat, text, re.DOTALL)
        if m:
            try:
                return _json.loads(m.group(0))
            except (ValueError, TypeError):
                continue
    return None

def resolve_model(model: str | None) -> str:
    """把档位名（flash/pro）解析为 DeepSeek API 模型名；已是模型名则原样返回。"""
    if not model:
        return DEEPSEEK_MODELS.get(settings.deepseek_model, "deepseek-chat")
    return DEEPSEEK_MODELS.get(model, model)


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """流式对话，逐个产出文本增量。"""
        raise NotImplementedError

    async def check_available(self) -> tuple[bool, str]:
        """探测连通性。返回 (ok, 说明)。"""
        return True, ""


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None) -> None:
        self.api_key = api_key or settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        # model 档位（flash/pro）或直接模型名
        self.model = resolve_model(model)

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
        # 轻量探测：列出模型（无需消耗 token）
        try:
            url = f"{self.base_url}/models"
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {self.api_key}"})
                if resp.status_code == 200:
                    return True, f"已连接（模型: {self.model}）"
                return False, f"HTTP {resp.status_code}: {(await resp.aread()).decode('utf-8', errors='replace')[:120]}"
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败: {type(e).__name__}: {e}"


class LLMRouter:
    """DeepSeek 提供器工厂（兼容旧调用方签名；本地模式已取消）。"""

    @staticmethod
    def get(mode: str = "auto", cfg: dict[str, Any] | None = None) -> LLMProvider:
        cfg = cfg or {}
        return DeepSeekProvider(
            api_key=cfg.get("deepseek_api_key"),
            base_url=cfg.get("deepseek_base_url"),
            model=cfg.get("deepseek_model"),
        )

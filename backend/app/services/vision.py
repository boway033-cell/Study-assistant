"""视觉分析提供器：Qwen-VL（阿里百炼，OpenAI 兼容接口）。

用于 PDF 阅读器「AI 解读本页/图表/公式」：前端把页面渲染成图片（dataURL），
后端转发给视觉模型做图文理解。默认 qwen-vl-max；key 在设置页配置。
"""
from __future__ import annotations

import httpx

from backend.app.core.config import settings


class VisionProvider:
    name = "qwen-vl"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None) -> None:
        self.api_key = api_key or settings.vision_api_key
        self.base_url = (base_url or settings.vision_base_url).rstrip("/")
        self.model = model or settings.vision_model

    async def analyze_image(self, image_data_url: str, prompt: str) -> str:
        """发送图片（dataURL）给视觉模型，返回文字分析。"""
        if not self.api_key:
            raise RuntimeError("未配置视觉分析 API Key（Qwen-VL），请在设置页填写")
        if not image_data_url.startswith("data:image"):
            raise ValueError("image 必须是 dataURL")

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": prompt},
                ]},
            ],
            "temperature": 0.3,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                body = resp.text[:300]
                raise RuntimeError(f"Qwen-VL 返回 {resp.status_code}: {body}")
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    async def check_available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "未配置视觉 API Key"
        return True, f"已配置（模型: {self.model}）"


def load_vision_config(db) -> dict[str, str]:
    """从数据库读取视觉配置；未设置项回退到 .env / 默认。"""
    from backend.app.models import Setting

    def _get(key: str, default: str) -> str:
        s = db.get(Setting, key)
        return s.value if s else default

    return {
        "vision_api_key": _get("vision_api_key", settings.vision_api_key),
        "vision_base_url": _get("vision_base_url", settings.vision_base_url),
        "vision_model": _get("vision_model", settings.vision_model),
    }

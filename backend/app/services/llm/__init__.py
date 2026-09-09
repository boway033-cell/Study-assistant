"""统一 LLM 连接层：供应商配置与传输协议分离。"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from backend.app.core import crypto
from backend.app.core.config import DEEPSEEK_MODELS, settings
from backend.app.models import Setting

PROFILES_KEY = "compatible_provider_profiles"
ROUTING_KEY = "llm_provider_routing"
LLM_TASKS = ("chat", "research", "writing", "presentation", "utility")


def _setting(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def _json_setting(db, key: str, default):
    try:
        value = json.loads(_setting(db, key))
        return value if isinstance(value, type(default)) else default
    except (TypeError, ValueError):
        return default


def load_provider_routing(db) -> dict:
    value = _json_setting(db, ROUTING_KEY, {})
    tasks = value.get("tasks") if isinstance(value.get("tasks"), dict) else {}
    fallbacks = value.get("fallbacks") if isinstance(value.get("fallbacks"), list) else []
    return {"default": str(value.get("default") or "deepseek"),
            "tasks": {key: str(pid) for key, pid in tasks.items() if key in LLM_TASKS},
            "fallbacks": list(dict.fromkeys(str(pid) for pid in fallbacks if pid))[:5]}


def _provider_config(db, provider_id: str, task: str, profiles: list[dict] | None = None) -> dict[str, Any]:
    profiles = profiles if profiles is not None else _json_setting(db, PROFILES_KEY, [])
    profile = next((p for p in profiles if isinstance(p, dict) and p.get("id") == provider_id), None)
    if profile:
        key = crypto.decrypt(_setting(db, f"compatible_provider_key:{provider_id}"))
        base = str(profile.get("base_url") or "").rstrip("/")
        model = str(profile.get("model") or "")
        return {"provider_id": provider_id, "provider_name": str(profile.get("name") or provider_id),
                "vendor": str(profile.get("vendor") or "custom"),
                "protocol": str(profile.get("protocol") or "openai_chat"),
                "api_key": key, "base_url": base, "model": model,
                "configured": bool(key) or base.startswith(("http://localhost", "http://127.0.0.1")),
                "task": task, "deepseek_api_key": key, "deepseek_base_url": base,
                "deepseek_model": model}
    legacy_key = crypto.decrypt(_setting(db, "deepseek_api_key", settings.deepseek_api_key))
    legacy_model = _setting(db, "deepseek_model", settings.deepseek_model)
    base = _setting(db, "deepseek_base_url", settings.deepseek_base_url).rstrip("/")
    return {"provider_id": "deepseek", "provider_name": "DeepSeek", "vendor": "deepseek",
            "protocol": "openai_chat", "api_key": legacy_key, "base_url": base,
            "model": legacy_model, "configured": bool(legacy_key), "task": task,
            "deepseek_api_key": legacy_key, "deepseek_base_url": base,
            "deepseek_model": legacy_model}


def load_llm_config(db, task: str = "utility") -> dict[str, Any]:
    """返回任务对应的标准配置；同时保留旧 DeepSeek 字段供旧调用方检查。"""
    routing = load_provider_routing(db)
    provider_id = routing["tasks"].get(task, routing["default"])
    profiles = _json_setting(db, PROFILES_KEY, [])
    primary = _provider_config(db, provider_id, task, profiles)
    primary["fallbacks"] = [cfg for fallback_id in routing["fallbacks"] if fallback_id != primary["provider_id"]
                            for cfg in [_provider_config(db, fallback_id, task, profiles)] if cfg.get("configured")]
    return primary


def parse_json_response(text: str):
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    fence = chr(96) * 3
    cleaned = re.sub(rf"^\s*{fence}(?:json)?\s*", "", text)
    cleaned = re.sub(rf"\s*{fence}$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass
    for pattern in (r"\[.*?\]", r"\{.*?\}"):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (ValueError, TypeError):
                continue
    return None


def resolve_model(model: str | None) -> str:
    return DEEPSEEK_MODELS.get(model or settings.deepseek_model, model or "deepseek-v4-flash")


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]: ...

    async def check_available(self) -> tuple[bool, str]:
        return True, ""


class OpenAIChatProvider(LLMProvider):
    """Chat Completions 协议，供 DeepSeek、Kimi、GLM、通义等供应商复用。"""

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "",
                 name: str = "custom", display_name: str = "自定义模型") -> None:
        self.api_key, self.base_url, self.model = api_key or "", base_url.rstrip("/"), model
        self.name, self.display_name = name, display_name

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        if not self.base_url or not self.model:
            raise RuntimeError(f"{self.display_name} 的 Base URL 或模型名未配置")
        if not self.api_key and not self.base_url.startswith(("http://localhost", "http://127.0.0.1")):
            raise RuntimeError(f"未配置 {self.display_name} API Key")
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=8)) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions",
                                     json={"model": self.model, "messages": messages, "stream": True},
                                     headers=self._headers()) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"{self.display_name} 返回 {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        delta = json.loads(raw).get("choices", [{}])[0].get("delta", {}).get("content", "")
                    except (ValueError, TypeError, IndexError):
                        continue
                    if delta:
                        yield delta

    async def check_available(self) -> tuple[bool, str]:
        if not self.api_key and not self.base_url.startswith(("http://localhost", "http://127.0.0.1")):
            return False, "未配置 API Key"
        try:
            # 只测 GET /models 会漏掉「模型未开通 / 模型名写错」这类真实故障：
            # 出现过 /models 返回 200、设置页显示已连接，而 chat 请求一律 400 的情况。
            # 这里直接发一次最小真实对话，收到首个增量即视为可用。
            out = ""
            async for delta in self.stream_chat([{"role": "user", "content": "只回复两个字：正常"}]):
                out += delta
                break
            if out.strip():
                return True, f"已连接（{self.display_name} · {self.model}）"
            return False, f"{self.display_name} 连接正常但未返回内容，请检查模型名"
        except httpx.HTTPError as exc:
            return False, f"连接失败：{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 — 供应商返回的业务错误要原样告诉用户
            return False, str(exc)[:200]


class DeepSeekProvider(OpenAIChatProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None) -> None:
        super().__init__(api_key or settings.deepseek_api_key, base_url or settings.deepseek_base_url,
                         resolve_model(model), "deepseek", "DeepSeek")


class AnthropicMessagesProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str, name: str, display_name: str) -> None:
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self.name, self.display_name = name, display_name

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError(f"未配置 {self.display_name} API Key")
        system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        chat = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages if m.get("role") != "system"]
        payload: dict[str, Any] = {"model": self.model, "messages": chat, "max_tokens": 8192, "stream": True}
        if system:
            payload["system"] = system
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=8)) as client:
            async with client.stream("POST", f"{self.base_url}/messages", json=payload, headers=headers) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"{self.display_name} 返回 {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            text = json.loads(line[5:].strip()).get("delta", {}).get("text", "")
                        except (ValueError, TypeError):
                            continue
                        if text:
                            yield text

    async def check_available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "未配置 API Key"
        try:
            headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/models", headers=headers)
            return ((True, f"已连接（{self.display_name} · {self.model}）") if response.status_code == 200
                    else (False, f"HTTP {response.status_code}: {response.text[:160]}"))
        except httpx.HTTPError as exc:
            return False, f"连接失败：{type(exc).__name__}: {exc}"


class GoogleGenerateProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str, name: str, display_name: str) -> None:
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self.name, self.display_name = name, display_name

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError(f"未配置 {self.display_name} API Key")
        system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        contents = [{"role": "model" if m.get("role") == "assistant" else "user",
                     "parts": [{"text": str(m.get("content", ""))}]}
                    for m in messages if m.get("role") != "system"]
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self.base_url}/models/{self.model}:streamGenerateContent"
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=8)) as client:
            async with client.stream("POST", url, params={"alt": "sse", "key": self.api_key}, json=payload) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"{self.display_name} 返回 {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        parts = json.loads(line[5:].strip()).get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    except (ValueError, TypeError, IndexError):
                        continue
                    for part in parts:
                        if part.get("text"):
                            yield part["text"]

    async def check_available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "未配置 API Key"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/models", params={"key": self.api_key})
            return ((True, f"已连接（{self.display_name} · {self.model}）") if response.status_code == 200
                    else (False, f"HTTP {response.status_code}: {response.text[:160]}"))
        except httpx.HTTPError as exc:
            return False, f"连接失败：{type(exc).__name__}: {exc}"


_usage_lock = threading.Lock()


def _record_usage(provider_id: str, task: str, *, ok: bool, fallback: bool,
                  input_chars: int, output_chars: int, elapsed_ms: int) -> None:
    """Best-effort local aggregate; token values are explicit character-based estimates."""
    try:
        from backend.app.core.database import SessionLocal
        db = SessionLocal()
        try:
            with _usage_lock:
                stats = _json_setting(db, "llm_usage_stats", {})
                key = f"{provider_id}:{task}"
                row = stats.get(key) if isinstance(stats.get(key), dict) else {}
                row["provider_id"] = provider_id; row["task"] = task
                row["calls"] = int(row.get("calls", 0)) + 1
                row["successes"] = int(row.get("successes", 0)) + int(ok)
                row["failures"] = int(row.get("failures", 0)) + int(not ok)
                row["fallback_activations"] = int(row.get("fallback_activations", 0)) + int(fallback)
                row["input_chars"] = int(row.get("input_chars", 0)) + input_chars
                row["output_chars"] = int(row.get("output_chars", 0)) + output_chars
                row["estimated_tokens"] = int(row.get("estimated_tokens", 0)) + math.ceil((input_chars + output_chars) / 4)
                row["elapsed_ms"] = int(row.get("elapsed_ms", 0)) + elapsed_ms
                stats[key] = row
                setting = db.get(Setting, "llm_usage_stats")
                rendered = json.dumps(stats, ensure_ascii=False)
                if setting:
                    setting.value = rendered
                else:
                    db.add(Setting(key="llm_usage_stats", value=rendered))
                db.commit()
        finally:
            db.close()
    except Exception:  # usage accounting must never break generation
        pass


class RoutedProvider(LLMProvider):
    """Primary plus ordered fallbacks. Fallback is allowed only before any output was emitted."""

    def __init__(self, providers: list[LLMProvider], task: str) -> None:
        self.providers = providers
        self.task = task
        self.name = providers[0].name
        self.model = getattr(providers[0], "model", "")
        self.display_name = getattr(providers[0], "display_name", self.name)

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        input_chars = sum(len(str(message.get("content", ""))) for message in messages)
        errors = []
        for index, provider in enumerate(self.providers):
            emitted = False
            output_chars = 0
            started = time.perf_counter()
            try:
                async for delta in provider.stream_chat(messages):
                    emitted = True; output_chars += len(delta)
                    yield delta
                _record_usage(provider.name, self.task, ok=True, fallback=index > 0,
                              input_chars=input_chars, output_chars=output_chars,
                              elapsed_ms=round((time.perf_counter() - started) * 1000))
                return
            except Exception as exc:
                _record_usage(provider.name, self.task, ok=False, fallback=index > 0,
                              input_chars=input_chars, output_chars=output_chars,
                              elapsed_ms=round((time.perf_counter() - started) * 1000))
                errors.append(f"{getattr(provider, 'display_name', provider.name)}: {exc}")
                if emitted:
                    raise RuntimeError("模型在输出中途断开，为避免拼接不同模型内容，未执行降级：" + errors[-1]) from exc
        raise RuntimeError("所有已配置模型均不可用：" + "；".join(errors))

    async def check_available(self) -> tuple[bool, str]:
        return await self.providers[0].check_available()


class LLMRouter:
    @staticmethod
    def _single(cfg: dict[str, Any]) -> LLMProvider:
        provider_id = str(cfg.get("provider_id") or "deepseek")
        if provider_id == "deepseek":
            return DeepSeekProvider(cfg.get("api_key") or cfg.get("deepseek_api_key"),
                                    cfg.get("base_url") or cfg.get("deepseek_base_url"),
                                    cfg.get("deepseek_model") or cfg.get("model"))
        args = (str(cfg.get("api_key") or ""), str(cfg.get("base_url") or ""),
                str(cfg.get("model") or ""), provider_id, str(cfg.get("provider_name") or provider_id))
        if cfg.get("protocol") == "anthropic_messages":
            return AnthropicMessagesProvider(*args)
        if cfg.get("protocol") == "google_generate":
            return GoogleGenerateProvider(*args)
        return OpenAIChatProvider(*args)

    @staticmethod
    def get(mode: str = "auto", cfg: dict[str, Any] | None = None) -> LLMProvider:
        cfg = cfg or {}
        providers = [LLMRouter._single(cfg), *(LLMRouter._single(item) for item in cfg.get("fallbacks", []))]
        return RoutedProvider(providers, str(cfg.get("task") or "utility"))

"""统一 LLM 连接层：供应商配置与传输协议分离。"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from backend.app.core import crypto
from backend.app.core.config import DEEPSEEK_MODELS, settings
from backend.app.models import Setting

PROFILES_KEY = "compatible_provider_profiles"
ROUTING_KEY = "llm_provider_routing"
LLM_TASKS = ("chat", "research", "writing", "presentation", "utility")
request_reasoning_effort: ContextVar[str | None] = ContextVar("request_reasoning_effort", default=None)
_provider_gates: dict[str, threading.BoundedSemaphore] = {}
_provider_gates_lock = threading.Lock()
_MAX_PARALLEL_PER_PROVIDER = settings.ai_provider_parallel_limit


@asynccontextmanager
async def _provider_slot(provider_id: str):
    """跨后台事件循环限制同一连接的并发请求，等待时不占用线程。"""
    with _provider_gates_lock:
        gate = _provider_gates.setdefault(provider_id, threading.BoundedSemaphore(_MAX_PARALLEL_PER_PROVIDER))
    while not gate.acquire(blocking=False):
        await asyncio.sleep(0.05)
    try:
        yield
    finally:
        gate.release()


def _tls_verify():
    """Windows 上使用系统证书存储验证 TLS，保留完整证书校验。"""
    if sys.platform != "win32":
        return True
    try:
        import ssl
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return True


def _image_parts(content: str | list) -> tuple[list[dict], int]:
    """把统一的图文消息转为供应商所需内容，并给预算一个保守估值。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}], len(content)
    parts = []
    budget_chars = 0
    for part in content:
        if part.get("type") == "text":
            value = str(part.get("text") or "")
            parts.append({"type": "text", "text": value})
            budget_chars += len(value)
        elif part.get("type") == "image_url":
            url = str((part.get("image_url") or {}).get("url") or "")
            match = re.fullmatch(r"data:(image/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/=]+)", url)
            if not match or len(match.group(2)) > 12_000_000:
                raise ValueError("页面图像格式无效或超过 9 MB，请缩小页面后重试")
            parts.append({"type": "image", "media_type": match.group(1), "data": match.group(2), "url": url})
            budget_chars += 4000  # 图像 token 依模型和分辨率变化，只作本地预算估算。
    return parts, budget_chars


def _message_budget_chars(messages: list[dict]) -> int:
    return sum(_image_parts(message.get("content", ""))[1] for message in messages)


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
    if provider_id != "deepseek":
        raise ValueError(f"模型连接不存在：{provider_id}")
    legacy_key = crypto.decrypt(_setting(db, "deepseek_api_key", settings.deepseek_api_key))
    legacy_model = _setting(db, "deepseek_model", settings.deepseek_model)
    base = _setting(db, "deepseek_base_url", settings.deepseek_base_url).rstrip("/")
    return {"provider_id": "deepseek", "provider_name": "DeepSeek", "vendor": "deepseek",
            "protocol": "openai_chat", "api_key": legacy_key, "base_url": base,
            "model": resolve_model(legacy_model),
            "configured": bool(legacy_key) or base.startswith(("http://localhost", "http://127.0.0.1")),
            "task": task,
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


def load_provider_config(db, provider_id: str, task: str = "utility") -> dict[str, Any]:
    """按已保存的连接 ID 取配置；不允许请求指定临时 URL 或密钥。"""
    return _provider_config(db, provider_id, task)


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
    decoder = json.JSONDecoder()
    # 前言后跟嵌套 JSON 时，非贪婪正则会截在第一个内层 }，误判为无效结果。
    for match in list(re.finditer(r"[\[{]", text))[:200]:
        try:
            parsed, _ = decoder.raw_decode(text[match.start():])
            if isinstance(parsed, (dict, list)):
                return parsed
        except (ValueError, TypeError):
            continue
    return None


def resolve_model(model: str | None) -> str:
    return DEEPSEEK_MODELS.get(model or settings.deepseek_model, model or "deepseek-flash")


class LLMProvider(ABC):
    name = "base"
    # 端点最近一次推送（含思考增量）的时间戳。思考型模型会先流式吐 reasoning
    # 增量、正文一个字都不出，此时连接其实是活的；调用方的首字看门狗必须靠
    # 这个信号区分「模型在思考」与「连接卡死」，否则会在思考阶段误杀。
    last_delta_at: float = 0.0
    reasoning_chars: int = 0

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
        self.last_delta_at = time.monotonic()
        self.reasoning_chars = 0
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True}
        effort = request_reasoning_effort.get()
        if (effort in {"low", "medium", "xhigh"} and self.model.startswith("qwen3.8-")
                and urlparse(self.base_url).hostname == "dashscope.aliyuncs.com"):
            payload["reasoning_effort"] = effort
        if (self.model.startswith("deepseek-") and urlparse(self.base_url).hostname == "api.deepseek.com"
                and effort in {"none", "low", "medium", "high", "max"}):
            payload["reasoning_effort"] = {"medium": "high"}.get(effort, effort)
            if effort == "none":
                payload["thinking"] = {"type": "disabled"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=8), verify=_tls_verify()) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions",
                                     json=payload,
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
                    # 任何一帧（含思考增量）都算端点存活，与是否产出正文无关。
                    self.last_delta_at = time.monotonic()
                    try:
                        frame = json.loads(raw)
                        if frame.get("error"):
                            raise RuntimeError(f"{self.display_name} 流式返回错误：{str(frame['error'])[:300]}")
                        delta_obj = frame.get("choices", [{}])[0].get("delta", {}) or {}
                    except (ValueError, TypeError, IndexError):
                        continue
                    # 思考型模型（如通义 Qwen3 系）先吐 reasoning_content 再吐 content。
                    # 只统计不落库：既给进度文案一个「还在思考」的可见信号，
                    # 又不把隐性推理过程留进任何输出。
                    self.reasoning_chars += len(str(delta_obj.get("reasoning_content") or ""))
                    delta = delta_obj.get("content") or ""
                    if delta:
                        yield delta

    async def check_available(self) -> tuple[bool, str]:
        if not self.api_key and not self.base_url.startswith(("http://localhost", "http://127.0.0.1")):
            return False, "未配置 API Key"
        try:
            import asyncio
            # 只测 GET /models 会漏掉「模型未开通 / 模型名写错」这类真实故障：
            # 出现过 /models 返回 200、设置页显示已连接，而 chat 请求一律 400 的情况。
            # 这里直接发一次最小真实对话，收到首个正文增量即视为可用。
            # 注意：思考型模型的首帧是 reasoning_content，不能拿它当「已返回内容」。
            out = ""
            token = request_reasoning_effort.set("low")
            try:
                async with asyncio.timeout(45):
                    async for delta in self.stream_chat([{"role": "user", "content": "只回复两个字：正常"}]):
                        out += delta
                        if out.strip():
                            break
            finally:
                request_reasoning_effort.reset(token)
            if out.strip():
                return True, f"已连接（{self.display_name} · {self.model}）"
            return False, f"{self.display_name} 连接正常但未返回内容，请检查模型名"
        except TimeoutError:
            return False, "检测超过 45 秒；模型可能仍可生成，请选择较快模型或稍后重试"
        except httpx.HTTPError as exc:
            return False, f"连接失败：{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 — 供应商返回的业务错误要原样告诉用户
            return False, str(exc)[:200]


class DeepSeekProvider(OpenAIChatProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None) -> None:
        super().__init__(settings.deepseek_api_key if api_key is None else api_key,
                         base_url or settings.deepseek_base_url,
                         resolve_model(model), "deepseek", "DeepSeek")


class AnthropicMessagesProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str, name: str, display_name: str) -> None:
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self.name, self.display_name = name, display_name

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError(f"未配置 {self.display_name} API Key")
        system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        chat = []
        for message in messages:
            if message.get("role") == "system":
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                blocks = []
                for part in _image_parts(content)[0]:
                    if part["type"] == "image":
                        blocks.append({"type": "image", "source": {"type": "base64", "media_type": part["media_type"], "data": part["data"]}})
                    else:
                        blocks.append(part)
                content = blocks
            chat.append({"role": message.get("role", "user"), "content": content})
        payload: dict[str, Any] = {"model": self.model, "messages": chat, "max_tokens": 8192, "stream": True}
        if system:
            payload["system"] = system
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=8), verify=_tls_verify()) as client:
            async with client.stream("POST", f"{self.base_url}/messages", json=payload, headers=headers) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"{self.display_name} 返回 {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        self.last_delta_at = time.monotonic()
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
            async with httpx.AsyncClient(timeout=10, verify=_tls_verify()) as client:
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
        contents = []
        for message in messages:
            if message.get("role") == "system":
                continue
            parts = []
            for part in _image_parts(message.get("content", ""))[0]:
                if part["type"] == "image":
                    parts.append({"inlineData": {"mimeType": part["media_type"], "data": part["data"]}})
                else:
                    parts.append({"text": part["text"]})
            contents.append({"role": "model" if message.get("role") == "assistant" else "user", "parts": parts})
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self.base_url}/models/{self.model}:streamGenerateContent"
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=8), verify=_tls_verify()) as client:
            async with client.stream("POST", url, params={"alt": "sse", "key": self.api_key}, json=payload) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"{self.display_name} 返回 {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    self.last_delta_at = time.monotonic()
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
            async with httpx.AsyncClient(timeout=10, verify=_tls_verify()) as client:
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
                from backend.app.services.llm.budget import estimate_tokens
                row["estimated_tokens"] = estimate_tokens(row["input_chars"] + row["output_chars"])
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

    def __init__(self, providers: list[LLMProvider], task: str,
                 style_profile: str | None = None) -> None:
        self.providers = providers
        self.task = task
        self.style_profile = style_profile or task
        self.last_style_audit: dict | None = None
        self.name = providers[0].name
        self.model = getattr(providers[0], "model", "")
        self.display_name = getattr(providers[0], "display_name", self.name)
        self.selected_provider_id = self.name
        self.selected_model = self.model

    @property
    def last_delta_at(self) -> float:  # type: ignore[override]
        """当前实际在跑的子通道的存活时间；思考增量也算存活。"""
        return max((getattr(p, "last_delta_at", 0.0) for p in self.providers), default=0.0)

    @property
    def reasoning_chars(self) -> int:  # type: ignore[override]
        return sum(getattr(p, "reasoning_chars", 0) for p in self.providers)

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        from backend.app.services.chinese_writing_style import apply_chinese_writing_style
        from backend.app.services.chinese_style_audit import audit_chinese_style
        messages = apply_chinese_writing_style(messages, self.style_profile)
        self.last_style_audit = None
        input_chars = _message_budget_chars(messages)
        from backend.app.services.llm.budget import BudgetExceeded, current_budget, load_default_budget
        budget = current_budget.get() or load_default_budget()
        errors = []
        for index, provider in enumerate(self.providers):
            if budget is not None:
                budget.start_call(input_chars)
            emitted = False
            output_chars = 0
            audit_parts: list[str] = []
            audit_chars = 0
            started = time.perf_counter()
            try:
                async with _provider_slot(provider.name):
                    async for delta in provider.stream_chat(messages):
                        emitted = True; output_chars += len(delta)
                        if audit_chars < 100000:
                            audit_parts.append(delta[:100000 - audit_chars])
                            audit_chars += min(len(delta), 100000 - audit_chars)
                        if budget is not None:
                            budget.output(len(delta))
                        yield delta
                try:
                    self.last_style_audit = audit_chinese_style("".join(audit_parts))
                    if output_chars > audit_chars:
                        self.last_style_audit["truncated"] = True
                except Exception:  # editorial diagnostics must not interrupt a completed answer
                    self.last_style_audit = None
                self.selected_provider_id = provider.name
                self.selected_model = getattr(provider, "model", "")
                _record_usage(provider.name, self.task, ok=True, fallback=index > 0,
                              input_chars=input_chars, output_chars=output_chars,
                              elapsed_ms=round((time.perf_counter() - started) * 1000))
                return
            except Exception as exc:
                if isinstance(exc, BudgetExceeded):
                    _record_usage(provider.name, self.task, ok=False, fallback=index > 0,
                                  input_chars=input_chars, output_chars=output_chars,
                                  elapsed_ms=round((time.perf_counter() - started) * 1000))
                    raise
                _record_usage(provider.name, self.task, ok=False, fallback=index > 0,
                              input_chars=input_chars, output_chars=output_chars,
                              elapsed_ms=round((time.perf_counter() - started) * 1000))
                errors.append(f"{getattr(provider, 'display_name', provider.name)}: {type(exc).__name__}: {exc}")
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
            return DeepSeekProvider(cfg.get("api_key") if "api_key" in cfg else cfg.get("deepseek_api_key"),
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
        return RoutedProvider(providers, str(cfg.get("task") or "utility"),
                              str(cfg.get("writing_style_profile") or "") or None)

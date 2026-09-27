"""模型配置、批量调用和有限并发的关键回归。"""
import asyncio
import json
import threading
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base


def test_deepseek_v41_flash_uses_configurable_model_and_endpoint(monkeypatch):
    from backend.app.services.llm import DeepSeekProvider, request_reasoning_effort, resolve_model

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"正常"}}]}\n\n')

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))

    async def run():
        token = request_reasoning_effort.set("none")
        try:
            provider = DeepSeekProvider("test-key", "https://api.deepseek.com", "deepseek-flash")
            return "".join([text async for text in provider.stream_chat([{"role": "user", "content": "测试"}])])
        finally:
            request_reasoning_effort.reset(token)

    assert asyncio.run(run()) == "正常"
    assert str(requests[0].url) == "https://api.deepseek.com/chat/completions"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "deepseek-flash"
    assert payload["reasoning_effort"] == "none"
    assert payload["thinking"] == {"type": "disabled"}
    assert resolve_model("flash") == "deepseek-flash"
    assert resolve_model("deepseek-v4-flash") == "deepseek-flash"
    assert resolve_model("deepseek-flash") == "deepseek-flash"


def test_deepseek_local_gateway_does_not_inherit_official_key(monkeypatch):
    from backend.app.api.settings import list_compatible_providers
    from backend.app.models import Setting
    from backend.app.services.llm import LLMRouter, load_llm_config
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "deepseek_api_key", "secret-official-key")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add_all([Setting(key="deepseek_api_key", value=""),
                    Setting(key="deepseek_base_url", value="http://127.0.0.1:9000/v1")])
        db.commit()
        config = load_llm_config(db)
        provider = LLMRouter.get(cfg=config).providers[0]
        assert config["configured"] is True
        assert list_compatible_providers(db)["items"][0]["configured"] is True
        assert provider.api_key == ""
    finally:
        db.close()
        engine.dispose()


def test_builtin_deepseek_endpoint_and_model_can_be_edited_without_replacing_key():
    from backend.app.api.settings import get_settings, list_compatible_providers, update_settings
    from backend.app.models import Setting
    from backend.app.schemas import SettingsUpdateReq
    from backend.app.services.llm import load_llm_config, load_provider_config

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(Setting(key="deepseek_api_key", value="sk-existing"))
        db.commit()
        update_settings(SettingsUpdateReq(deepseek_base_url="https://gateway.example.com/v1/chat/completions",
                                          deepseek_model="deepseek-flash"), db)
        config = load_llm_config(db)
        settings = get_settings(db)
        builtin = list_compatible_providers(db)["items"][0]
        assert config["base_url"] == "https://gateway.example.com/v1"
        assert config["model"] == "deepseek-flash"
        assert config["api_key"] == "sk-existing"
        assert settings.deepseek_base_url == builtin["base_url"]
        assert settings.deepseek_model == builtin["model"] == "deepseek-flash"
        with pytest.raises(ValueError, match="模型连接不存在"):
            load_provider_config(db, "unknown-provider")
    finally:
        db.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_batch_calls_models_and_tasks_in_parallel_with_ordered_partial_results(monkeypatch):
    from backend.app.api import ai
    from backend.app.services.llm.budget import TaskBudget

    active = 0
    peak = 0

    class FakeProvider:
        def __init__(self, cfg):
            self.name = self.selected_provider_id = cfg["provider_id"]
            self.model = self.selected_model = cfg["model"]

        async def stream_chat(self, messages):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.03)
                if self.name == "bad":
                    raise RuntimeError("故意失败")
                yield f"{self.name}：{messages[-1]['content']}"
            finally:
                active -= 1

    monkeypatch.setattr(ai, "load_llm_config", lambda db, task: {"provider_id": "one", "model": "model-one", "configured": True})
    monkeypatch.setattr(ai, "load_provider_config", lambda db, pid, task: {"provider_id": pid, "model": f"model-{pid}", "configured": True})
    monkeypatch.setattr(ai, "load_default_budget", lambda: TaskBudget(max_calls=12, max_tokens=100000))
    monkeypatch.setattr(ai.LLMRouter, "get", lambda mode, cfg: FakeProvider(cfg))
    req = ai.BatchGenerateReq(items=[ai.BatchGenerateItem(id="a", prompt="甲"), ai.BatchGenerateItem(id="b", prompt="乙")],
                              provider_ids=["one", "bad"], max_concurrency=3)
    result = await ai.ai_batch(req, db=None)
    assert [(item["id"], item["provider_id"], item["ok"]) for item in result["results"]] == [
        ("a", "one", True), ("a", "bad", False), ("b", "one", True), ("b", "bad", False)]
    assert result["successes"] == result["failures"] == 2
    assert 1 < peak <= 3


@pytest.mark.asyncio
async def test_same_provider_never_exceeds_global_two_request_limit(monkeypatch):
    from backend.app.services.llm import RoutedProvider
    from backend.app.services.llm.budget import TaskBudget, current_budget

    active = 0
    peak = 0

    class SlowProvider:
        name = "shared-provider"
        model = "model"

        async def stream_chat(self, messages):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.04)
                yield "结果"
            finally:
                active -= 1

    monkeypatch.setattr("backend.app.services.llm._record_usage", lambda *args, **kwargs: None)
    token = current_budget.set(TaskBudget(max_calls=4, max_tokens=100000))
    try:
        async def run():
            return "".join([part async for part in RoutedProvider([SlowProvider()], "utility").stream_chat(
                [{"role": "user", "content": "问题"}])])

        assert await asyncio.gather(*(run() for _ in range(4))) == ["结果"] * 4
    finally:
        current_budget.reset(token)
    assert peak == 2


def test_interactive_worker_runs_two_tasks_while_parser_remains_separate(monkeypatch):
    from backend.app.worker import tasks

    monkeypatch.setattr(tasks, "_persist", lambda _: None)
    started = threading.Event()
    lock = threading.Lock()
    active = 0

    async def slow_ai(record):
        nonlocal active
        with lock:
            active += 1
            if active == 2:
                started.set()
        await asyncio.sleep(0.15)
        with lock:
            active -= 1
        return {"ok": True}

    first = tasks.submit("parallel-test", slow_ai)
    second = tasks.submit("parallel-test", slow_ai)
    assert started.wait(2), "同一 AI 队列中的两个任务应同时运行"
    deadline = time.monotonic() + 2
    while (first.status != "done" or second.status != "done") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert first.status == second.status == "done"


def test_parallel_tasks_persist_their_own_progress(monkeypatch):
    from backend.app.worker import tasks

    saved = []
    monkeypatch.setattr(tasks, "_persist", lambda record: saved.append(record.id))
    first = tasks.TaskRecord(id="first")
    second = tasks.TaskRecord(id="second")
    tasks.update_progress(first, .1)
    tasks.update_progress(second, .2)
    assert saved == ["first", "second"]

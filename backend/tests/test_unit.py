"""后端单元测试：解析、分词、FTS 检索、LLM 配置（DeepSeek 云端）

运行：.venv\\Scripts\\python.exe -m pytest backend/tests/test_unit.py -q
"""
import os
import sys
from pathlib import Path

# 确保可以 import backend 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402


# ---------- 解析 ----------
class TestParser:
    def test_build_chapters_with_toc(self):
        from backend.app.services.parser import TocItem
        from backend.app.services.rag.chunker import build_chapters

        toc = [
            TocItem("第一章", 1, 1),
            TocItem("1.1 节", 2, 2),
            TocItem("1.2 节", 2, 5),
            TocItem("第二章", 1, 10),
        ]
        chapters = build_chapters(toc, total_pages=20)
        assert len(chapters) == 4
        # 第二章 parent_id 为 None，1.1 的 parent 是第一章
        ch1 = chapters[0]
        ch11 = chapters[1]
        assert ch1["parent_id"] is None
        assert ch11["parent_id"] == ch1["order_index"]
        # end_page：第一章到第二章前
        assert chapters[0]["end_page"] == 9
        assert chapters[-1]["end_page"] == 20

    def test_build_chapters_empty_toc(self):
        from backend.app.services.rag.chunker import build_chapters

        chapters = build_chapters([], total_pages=100)
        assert len(chapters) == 1
        assert chapters[0]["start_page"] == 1
        assert chapters[0]["end_page"] == 100

    def test_build_chapters_compacts_missing_intermediate_levels(self):
        from backend.app.services.parser import TocItem
        from backend.app.services.rag.chunker import build_chapters

        toc = [
            TocItem("第一章", 1, 1),
            TocItem("一、概念", 3, 2),
            TocItem("（一）定义", 4, 2),
            TocItem("第二章", 1, 5),
        ]
        chapters = build_chapters(toc, total_pages=8)
        assert [item["level"] for item in chapters] == [1, 2, 3, 1]
        assert chapters[1]["parent_id"] == chapters[0]["order_index"]
        assert chapters[2]["parent_id"] == chapters[1]["order_index"]

    def test_nested_chapter_page_ranges_never_overlap(self):
        from backend.app.services.rag.chunker import build_chapter_pages

        chapters = [
            {"start_page": 1, "end_page": 10, "order_index": 0, "level": 1},
            {"start_page": 1, "end_page": 5, "order_index": 1, "level": 2},
            {"start_page": 1, "end_page": 2, "order_index": 2, "level": 3},
            {"start_page": 3, "end_page": 5, "order_index": 3, "level": 3},
            {"start_page": 6, "end_page": 10, "order_index": 4, "level": 2},
        ]
        intervals = build_chapter_pages(chapters, 10)
        covered = [page for _, start, end in intervals for page in range(start, end + 1)]
        assert covered == list(range(1, 11))
        assert len(covered) == len(set(covered))

    def test_split_pages_into_chunks(self):
        from backend.app.services.rag.chunker import split_pages_into_chunks

        # 两页各 300 字，chunk_size=600，overlap=80：
        # 页1入 buffer(302)，页2加入后超 600 → flush 页1为 chunk0(page 1-1)
        # 保留 80 字重叠 → chunk1 含页1尾部+页2(page 1-2)
        pages = ["a" * 300, "b" * 300]
        chunks = split_pages_into_chunks(pages, [(1, 1, 2)])
        assert len(chunks) == 2
        assert chunks[0]["page_start"] == 1
        assert chunks[0]["page_end"] == 1  # 第一块只含页1
        assert chunks[1]["page_start"] == 1  # 重叠：含页1尾部
        assert chunks[1]["page_end"] == 2    # 含页2
        assert chunks[0]["chapter_id"] == 1
        # 所有 chunk 内容不超过 chunk_size
        assert all(c["word_count"] <= 600 for c in chunks)

    def test_split_long_text_multiple_chunks(self):
        from backend.app.services.rag.chunker import split_pages_into_chunks

        # 单页 2000 字 → 600 字主块 + 后续块（每步 520 前进）
        pages = ["a" * 2000]
        chunks = split_pages_into_chunks(pages, [(1, 1, 1)])
        # 2000 = 0..600, 520..1120, 1040..1640, 1560..2000(440字, ≥260 保留) → 4 块
        assert len(chunks) == 4
        assert all(c["word_count"] <= 600 for c in chunks)

    def test_split_single_chunk_exact_size(self):
        from backend.app.services.rag.chunker import split_pages_into_chunks

        # 单页恰好 600 字 → 1 个 chunk
        pages = ["a" * 600]
        chunks = split_pages_into_chunks(pages, [(1, 1, 1)])
        assert len(chunks) == 1
        assert len(chunks[0]["content"]) == 600


# ---------- 分词 ----------
class TestTokenizer:
    def test_tokenize_chinese(self):
        from backend.app.services.rag.chunker import tokenize

        result = tokenize("拉格朗日中值定理")
        # 分词后应包含空格（多词）
        assert " " in result

    def test_tokenize_query_or_join(self):
        from backend.app.services.rag.chunker import tokenize_query

        expr = tokenize_query("拉格朗日")
        assert '"' in expr
        assert " OR " in expr

    def test_tokenize_query_empty(self):
        from backend.app.services.rag.chunker import tokenize_query

        assert tokenize_query("!!!") == ""


# ---------- FTS 检索 ----------
class TestFTS:
    def test_tokenize_query_safe_chars(self):
        from backend.app.services.rag.chunker import tokenize_query

        # 特殊字符应被过滤，不产生非法 MATCH 表达式
        expr = tokenize_query("概率与统计 2024!")
        assert not any(c in expr for c in ("!", ",", "."))


# ---------- LLM 配置（DeepSeek 云端，设置页即时生效） ----------
class TestLLMConfig:
    """使用独立内存 DB，避免污染真实 study.db（settings 主键冲突）。"""

    @pytest.fixture(autouse=True)
    def _isolated_db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from backend.app.core.database import Base
        import backend.app.models  # noqa: F401  确保 ORM 模型注册到 metadata

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        self._db = Session()
        yield
        self._db.close()
        engine.dispose()

    def test_load_config_from_db(self):
        """数据库中的设置应优先于内存默认（设置页改 Key/模型即时生效）。"""
        from backend.app.models import Setting
        from backend.app.services.llm import load_llm_config

        db = self._db
        db.add(Setting(key="deepseek_api_key", value="sk-test-123456"))
        db.add(Setting(key="deepseek_model", value="pro"))
        db.commit()
        cfg = load_llm_config(db)
        assert cfg["deepseek_api_key"] == "sk-test-123456"
        assert cfg["deepseek_model"] == "pro"

    def test_load_config_fallback_default(self):
        """未在 DB 中设置的项回退到内存默认。"""
        from backend.app.services.llm import load_llm_config

        cfg = load_llm_config(self._db)
        assert cfg["deepseek_model"] in ("flash", "pro")  # 有默认档位
        assert "ollama" not in cfg  # 本地 AI 已取消

    def test_router_returns_deepseek(self):
        """LLMRouter.get 只返回 DeepSeek 提供器（本地模式已取消）。"""
        from backend.app.models import Setting
        from backend.app.services.llm import LLMRouter, load_llm_config

        db = self._db
        db.add(Setting(key="deepseek_api_key", value="sk-test-123456"))
        db.commit()
        cfg = load_llm_config(db)
        provider = LLMRouter.get("auto", cfg)
        assert provider.name == "deepseek"
        assert provider.model == "deepseek-v4-flash"

    def test_resolve_model_mapping(self):
        """flash/pro 档位映射到实际 API 模型名。"""
        from backend.app.services.llm import resolve_model

        assert resolve_model("flash") == "deepseek-v4-flash"
        assert resolve_model("pro") == "deepseek-v4-pro"

    def test_custom_provider_and_task_routing(self):
        """自定义连接应能成为默认，并可由功能路由单独覆盖。"""
        import json
        from backend.app.models import Setting
        from backend.app.services.llm import LLMRouter, load_llm_config

        profiles = [{"id": "kimi-main", "name": "Kimi", "vendor": "kimi",
                     "capability": "text", "protocol": "openai_chat",
                     "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2.6"},
                    {"id": "glm-research", "name": "智谱 GLM", "vendor": "zhipu",
                     "capability": "text", "protocol": "openai_chat",
                     "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.2"}]
        self._db.add_all([
            Setting(key="compatible_provider_profiles", value=json.dumps(profiles)),
            Setting(key="compatible_provider_key:kimi-main", value="sk-kimi-test"),
            Setting(key="compatible_provider_key:glm-research", value="sk-glm-test"),
            Setting(key="llm_provider_routing", value=json.dumps({
                "default": "kimi-main", "tasks": {"research": "glm-research"}})),
        ])
        self._db.commit()

        chat_cfg = load_llm_config(self._db, "chat")
        research_cfg = load_llm_config(self._db, "research")
        assert (chat_cfg["provider_id"], chat_cfg["model"]) == ("kimi-main", "kimi-k2.6")
        assert (research_cfg["provider_id"], research_cfg["model"]) == ("glm-research", "glm-5.2")
        assert LLMRouter.get("auto", chat_cfg).name == "kimi-main"

    def test_native_protocol_adapters_are_selected(self):
        from backend.app.services.llm import AnthropicMessagesProvider, GoogleGenerateProvider, LLMRouter

        base = {"provider_id": "vendor", "provider_name": "Vendor", "api_key": "key",
                "base_url": "https://example.com/v1", "model": "model"}
        assert isinstance(LLMRouter.get(cfg={**base, "protocol": "anthropic_messages"}).providers[0],
                          AnthropicMessagesProvider)
        assert isinstance(LLMRouter.get(cfg={**base, "protocol": "google_generate"}).providers[0],
                          GoogleGenerateProvider)

    @pytest.mark.asyncio
    async def test_router_falls_back_only_before_first_token(self, monkeypatch):
        from backend.app.services.llm import LLMProvider, RoutedProvider

        class Fake(LLMProvider):
            def __init__(self, name, values, error=None): self.name, self.values, self.error = name, values, error
            async def stream_chat(self, messages):
                for value in self.values:
                    yield value
                if self.error:
                    raise RuntimeError(self.error)

        monkeypatch.setattr("backend.app.services.llm._record_usage", lambda *args, **kwargs: None)
        provider = RoutedProvider([Fake("primary", [], "down"), Fake("backup", ["ok"])], "chat")
        assert [value async for value in provider.stream_chat([{"role": "user", "content": "q"}])] == ["ok"]
        provider = RoutedProvider([Fake("primary", ["partial"], "down"), Fake("backup", ["mixed"])], "chat")
        with pytest.raises(RuntimeError, match="未执行降级"):
            _ = [value async for value in provider.stream_chat([{"role": "user", "content": "q"}])]

    def test_provider_settings_api_activates_saved_connection(self):
        from backend.app.api.settings import (CompatibleProviderWrite, ProviderRoutingWrite,
                                              list_compatible_providers, save_compatible_provider,
                                              update_provider_routing)
        from backend.app.services.llm import load_llm_config

        saved = save_compatible_provider(CompatibleProviderWrite(
            id="kimi-api", name="Kimi", vendor="kimi", capability="text",
            protocol="openai_chat", base_url="https://api.moonshot.cn/v1",
            model="kimi-k2.6", api_key="sk-kimi-api-test",
        ), self._db)
        update_provider_routing(ProviderRoutingWrite(
            default_provider_id=saved["id"], task_routes={"research": saved["id"], "chat": ""},
        ), self._db)
        listing = list_compatible_providers(self._db)
        assert listing["routing_locked"] is False
        assert listing["default_text_provider"] == "kimi-api"
        assert load_llm_config(self._db, "chat")["provider_id"] == "kimi-api"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

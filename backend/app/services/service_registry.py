"""服务注册表：统一管理服务层组件的生命周期与依赖

职责：
- 集中初始化各服务组件（数据层、检索层、LLM 层、知识层）
- 提供统一的访问入口，替代各 API 模块各自 import 的散乱方式
- 管理服务启动/关闭顺序

分层架构：
  API 层 (api/)  →  服务注册表  →  业务服务层 (services/)
                          ↓
                    数据层 (core/ + models/)
"""
from __future__ import annotations

from typing import Any


class ServiceRegistry:
    """服务注册表单例：集中管理服务组件。"""

    _instance: "ServiceRegistry | None" = None
    _services: dict[str, Any] = {}
    _initialized: bool = False

    def __new__(cls) -> "ServiceRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def init(self) -> None:
        """初始化所有服务组件（应用启动时调用）。"""
        if self._initialized:
            return

        # 1. 数据层服务
        from backend.app.core.data_manager import run_data_checks
        self._services["data_checks"] = run_data_checks

        # 2. 检索服务
        from backend.app.services.rag import fts, retriever, vector
        self._services["fts"] = fts
        self._services["retriever"] = retriever
        self._services["vector"] = vector

        # 3. 知识层
        from backend.app.services.knowledge_base import (
            get_book_digest,
            get_multi_book_digest,
            get_context_for_quiz,
            get_chapter_summary_cached,
        )
        self._services["knowledge_base"] = {
            "get_book_digest": get_book_digest,
            "get_multi_book_digest": get_multi_book_digest,
            "get_context_for_quiz": get_context_for_quiz,
            "get_chapter_summary_cached": get_chapter_summary_cached,
        }

        # 4. LLM 服务
        from backend.app.services.llm import LLMRouter, load_llm_config, parse_json_response
        self._services["llm"] = {
            "router": LLMRouter,
            "load_config": load_llm_config,
            "parse_json": parse_json_response,
        }

        # 5. 重排服务
        from backend.app.services.rag.reranker import rerank, verify_citations, get_eval_stats
        self._services["reranker"] = {
            "rerank": rerank,
            "verify_citations": verify_citations,
            "get_eval_stats": get_eval_stats,
        }

        # 6. 任务服务
        from backend.app.worker import tasks
        self._services["tasks"] = tasks

        self._initialized = True

    def get(self, name: str) -> Any:
        """获取服务组件。"""
        if not self._initialized:
            self.init()
        return self._services.get(name)

    def shutdown(self) -> None:
        """清理服务资源（应用关闭时调用）。"""
        # 释放向量模型内存
        try:
            self._services.get("vector", None) and self._services["vector"].unload()
        except Exception:
            pass
        self._services.clear()
        self._initialized = False


# 全局单例
registry = ServiceRegistry()

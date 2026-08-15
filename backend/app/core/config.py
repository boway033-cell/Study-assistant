"""应用配置：从环境变量 / .env 读取"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（backend/ 目录）
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class Settings:
    """集中配置。.env 文件为可选，未安装 python-dotenv 时手动导出环境变量。"""

    def __init__(self) -> None:
        self.host: str = _env("HOST", "127.0.0.1")
        self.port: int = int(_env("PORT", "8000"))

        # 数据目录（绝对路径，确保不依赖运行 cwd）
        self.data_dir: Path = Path(_env("DATA_DIR", str(BACKEND_DIR / "data"))).resolve()
        self.uploads_dir: Path = self.data_dir / "uploads"
        self.db_path: Path = self.data_dir / "study.db"
        self.chroma_dir: Path = self.data_dir / "chroma"

        # LLM
        self.llm_mode: str = _env("LLM_MODE", "local")  # local / cloud
        self.ollama_base_url: str = _env("OLLAMA_BASE_URL", "http://localhost:11434")
        self.ollama_model: str = _env("OLLAMA_MODEL", "qwen2.5:3b-instruct")
        self.deepseek_base_url: str = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.deepseek_api_key: str = _env("DEEPSEEK_API_KEY", "")

        # 检索
        self.rag_top_k: int = int(_env("RAG_TOP_K", "5"))
        self.vector_search: bool = _env("VECTOR_SEARCH", "false").lower() == "true"

        # 卡片
        self.daily_new_cards: int = int(_env("DAILY_NEW_CARDS", "20"))

        # 解析
        self.chunk_size: int = int(_env("CHUNK_SIZE", "600"))
        self.chunk_overlap: int = int(_env("CHUNK_OVERLAP", "80"))

        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """确保数据目录存在（仅在本项目目录内创建，不触碰系统文件）。"""
        for d in (self.data_dir, self.uploads_dir, self.chroma_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()

"""应用配置：从环境变量 / .env 读取"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（backend/ 目录）
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


def _load_dotenv() -> None:
    """加载项目根目录 .env（可选，未安装 python-dotenv 时静默跳过）。"""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:  # noqa: BLE001
        pass


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class Settings:
    """集中配置。.env 文件为可选（项目根目录 .env）。"""

    def __init__(self) -> None:
        _load_dotenv()

        self.host: str = _env("HOST", "127.0.0.1")
        self.port: int = int(_env("PORT", "8000"))

        # 数据目录（绝对路径：相对路径按项目根解析，不依赖运行 cwd）
        _data_raw = _env("DATA_DIR", str(BACKEND_DIR / "data"))
        _data_path = Path(_data_raw)
        if not _data_path.is_absolute():
            _data_path = PROJECT_ROOT / _data_path
        self.data_dir: Path = _data_path.resolve()
        self.uploads_dir: Path = self.data_dir / "uploads"
        self.db_path: Path = self.data_dir / "study.db"
        self.chroma_dir: Path = self.data_dir / "chroma"

        # LLM：仅云端 DeepSeek（本地 AI 已取消）
        self.deepseek_base_url: str = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.deepseek_api_key: str = _env("DEEPSEEK_API_KEY", "")
        # 模型档位：flash=deepseek-chat（快）/ pro=deepseek-reasoner（深度思考）
        self.deepseek_model: str = _env("DEEPSEEK_MODEL", "flash")

        # 检索
        self.rag_top_k: int = int(_env("RAG_TOP_K", "5"))
        self.vector_search: bool = _env("VECTOR_SEARCH", "false").lower() == "true"

        # 解析
        self.chunk_size: int = int(_env("CHUNK_SIZE", "600"))
        self.chunk_overlap: int = int(_env("CHUNK_OVERLAP", "80"))

        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """确保数据目录存在（仅在本项目目录内创建，不触碰系统文件）。"""
        for d in (self.data_dir, self.uploads_dir, self.chroma_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()


# DeepSeek 模型档位映射（实测 API 返回的模型名）
# flash → deepseek-v4-flash（快速）；pro → deepseek-v4-pro（深度推理）
DEEPSEEK_MODELS: dict[str, str] = {
    "flash": "deepseek-v4-flash",
    "pro": "deepseek-v4-pro",
}

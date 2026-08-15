"""FastAPI 入口（docs/01-architecture.md §3 / §7）"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api import books, chat, knowledge, quizzes, settings, stats
from backend.app.core.config import settings as app_settings
from backend.app.core.database import Base, engine
from backend.app.services.rag import fts


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 建表（幂等）
    Base.metadata.create_all(bind=engine)
    # FTS5 虚拟表
    fts.init_fts()
    # 从 DB 读取向量检索开关（用户设置持久化）
    try:
        from backend.app.core.database import SessionLocal
        from backend.app.models import Setting
        db = SessionLocal()
        s = db.get(Setting, "vector_search")
        if s is not None:
            app_settings.vector_search = s.value.lower() == "true"
        db.close()
    except Exception:  # noqa: BLE001
        pass
    yield


app = FastAPI(title="保研复习助手", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 仅本地使用
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(books.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(quizzes.router)
app.include_router(stats.router)
app.include_router(settings.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "db": str(app_settings.db_path)}


# 静态资源（前端构建产物）—— 必须放在所有 API 路由之后
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")

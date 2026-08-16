"""FastAPI 入口（docs/01-architecture.md §3 / §7）"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api import ai, annotations, books, chat, deep, graph, knowledge, plan, quizzes, settings, stats, study
from backend.app.core.config import settings as app_settings
from backend.app.core.database import Base, engine
from backend.app.services.rag import fts


def _migrate():
    """轻量迁移：老库补 books.category 列 + 复位中断的深度分析任务。"""
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(books)")).fetchall()]
            if "category" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN category VARCHAR(50)"))
            # 知识树节点新列（类型/掌握度/跨树引用）
            for col, ddl in (("node_type", "VARCHAR(20) DEFAULT 'concept'"), ("mastery", "VARCHAR(10) DEFAULT 'unknown'"), ("ref_node_id", "INTEGER")):
                try:
                    kcols = [r[1] for r in conn.execute(text("PRAGMA table_info(knowledge_nodes)")).fetchall()]
                    if col not in kcols:
                        conn.execute(text(f"ALTER TABLE knowledge_nodes ADD COLUMN {col} {ddl}"))
                except Exception:  # noqa: BLE001
                    pass
            # 重启后复位 stuck 任务（后台任务在内存，重启即丢失）
            try:
                conn.execute(text("UPDATE book_deep SET status='pending', error_msg='服务重启，任务中断，可重新分析' WHERE status='running'"))
            except Exception:  # noqa: BLE001
                pass
            conn.commit()
    except Exception:  # noqa: BLE001
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 建表（幂等）
    Base.metadata.create_all(bind=engine)
    _migrate()
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


app = FastAPI(title="Study assistant", version="0.1.0", lifespan=lifespan)

# 本地访问控制：只允许本服务与本地开发源，拒绝任意来源跨域（防恶意网页调用本地 API）
_ALLOWED_ORIGINS = [
    f"http://127.0.0.1:{app_settings.port}",
    f"http://localhost:{app_settings.port}",
    "http://127.0.0.1:5173",  # vite dev server
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.middleware("http")
async def _guard_local_api(request: Request, call_next):
    """拒绝带非本地 Origin 的 API 请求（防恶意网页跨源调用本地服务）。"""
    if request.url.path.startswith("/api/"):
        origin = (request.headers.get("origin") or "").rstrip("/")
        if origin and origin not in _ALLOWED_ORIGINS:
            return JSONResponse(status_code=403, content={"detail": "跨源请求被拒绝"})
    return await call_next(request)

app.include_router(books.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(quizzes.router)
app.include_router(stats.router)
app.include_router(settings.router)
app.include_router(annotations.router)
app.include_router(ai.router)
app.include_router(deep.router)
app.include_router(study.router)
app.include_router(graph.router)
app.include_router(plan.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "db": str(app_settings.db_path)}


# 静态资源（前端构建产物）—— 必须放在所有 API 路由之后
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")

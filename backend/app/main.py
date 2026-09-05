"""FastAPI 入口（docs/01-architecture.md §3 / §7）"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.api import ai, annotations, books, chat, deep, draw, graph, knowledge, literature, presentations, quizzes, settings, shelves, stats, study, tags, writing
from backend.app.core.config import settings as app_settings
from backend.app.core.database import Base, engine
from backend.app.services.rag import fts


def _migrate():
    """轻量迁移：老库补新增列 + 复位中断的深度分析/导入任务。"""
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            # books 新增列
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(books)")).fetchall()]
            if "category" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN category VARCHAR(50)"))
            if "file_hash" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN file_hash VARCHAR(64)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_books_file_hash ON books(file_hash)"))
            if "duplicate_of" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN duplicate_of INTEGER"))
            if "library_order" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN library_order INTEGER NOT NULL DEFAULT 0"))
                # 初始顺序与旧版列表一致：最近导入的资料在前。
                conn.execute(text("""
                    WITH ranked AS (
                        SELECT id, ROW_NUMBER() OVER (ORDER BY created_at DESC, id DESC) - 1 AS position
                        FROM books
                    )
                    UPDATE books
                    SET library_order = (SELECT position FROM ranked WHERE ranked.id = books.id)
                """))
            # 文献档案的发布、可见性与元数据可信度由用户明确维护，不再从题名推断。
            try:
                profile_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(paper_profiles)")).fetchall()]
                for col, ddl in (
                    ("publication_status", "VARCHAR(24) NOT NULL DEFAULT 'unknown'"),
                    ("visibility", "VARCHAR(20) NOT NULL DEFAULT 'private'"),
                    ("demo_allowed", "INTEGER NOT NULL DEFAULT 0"),
                    ("metadata_confidence", "FLOAT NOT NULL DEFAULT 0.0"),
                ):
                    if col not in profile_cols:
                        conn.execute(text(f"ALTER TABLE paper_profiles ADD COLUMN {col} {ddl}"))
            except Exception:  # noqa: BLE001
                pass
            # 知识树节点新列（类型/掌握度/跨树引用）
            for col, ddl in (("node_type", "VARCHAR(20) DEFAULT 'concept'"), ("mastery", "VARCHAR(10) DEFAULT 'unknown'"), ("ref_node_id", "INTEGER")):
                try:
                    kcols = [r[1] for r in conn.execute(text("PRAGMA table_info(knowledge_nodes)")).fetchall()]
                    if col not in kcols:
                        conn.execute(text(f"ALTER TABLE knowledge_nodes ADD COLUMN {col} {ddl}"))
                except Exception:  # noqa: BLE001
                    pass
            # 重启后复位 stuck 任务
            try:
                conn.execute(text("UPDATE book_deep SET status='pending', error_msg='服务重启，任务中断，可重新分析' WHERE status='running'"))
            except Exception:  # noqa: BLE001
                pass
            try:
                report_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(study_reports)")).fetchall()]
                if "selection_json" not in report_cols:
                    conn.execute(text("ALTER TABLE study_reports ADD COLUMN selection_json TEXT NOT NULL DEFAULT '{}'"))
                if "focus" not in report_cols:
                    conn.execute(text("ALTER TABLE study_reports ADD COLUMN focus TEXT"))
                if "framework" not in report_cols:
                    conn.execute(text("ALTER TABLE study_reports ADD COLUMN framework TEXT"))
                if "claims_json" not in report_cols:
                    conn.execute(text("ALTER TABLE study_reports ADD COLUMN claims_json TEXT NOT NULL DEFAULT '[]'"))
            except Exception:  # noqa: BLE001
                pass
            # 来源链 v2：研究报告派生的笔记/证据保留多书范围、精确锚点和报告回链。
            for table, definitions in {
                "knowledge_notes": (
                    ("source_report_id", "INTEGER"),
                    ("source_scope_json", "TEXT NOT NULL DEFAULT '{}'"),
                    ("source_refs_json", "TEXT NOT NULL DEFAULT '[]'"),
                ),
                "evidence_cards": (
                    ("source_report_id", "INTEGER"),
                    ("source_scope_json", "TEXT NOT NULL DEFAULT '{}'"),
                ),
            }.items():
                try:
                    existing_cols = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
                    for col, ddl in definitions:
                        if col not in existing_cols:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                except Exception:  # noqa: BLE001
                    pass
            # 旧版把普通笔记存成 knowledge_nodes(node_type='note')；幂等复制到独立笔记表。
            try:
                conn.execute(text("""
                    INSERT OR IGNORE INTO knowledge_notes
                    (book_id, chapter_id, page, title, content, source_legacy_node_id, tags_json, origin, created_at)
                    SELECT book_id, chapter_id, NULL, title, COALESCE(note, ''), id, '[]', 'user', COALESCE(created_at, CURRENT_TIMESTAMP)
                    FROM knowledge_nodes WHERE node_type='note' AND book_id IS NOT NULL
                """))
            except Exception:  # noqa: BLE001
                pass
            # 导入任务持久化表中的 running 任务复位为 pending（启动时自动恢复入队）
            try:
                conn.execute(text("UPDATE import_tasks SET status='pending', message='服务重启，自动恢复' WHERE status='running' AND name IN ('import','reimport')"))
                conn.execute(text("UPDATE import_tasks SET status='failed', error='服务重启，请重新生成', message='任务已中断' WHERE status IN ('pending','running') AND name NOT IN ('import','reimport')"))
                conn.execute(text("UPDATE import_tasks SET status='cancelled', message='任务已取消' WHERE status='cancelling'"))
            except Exception:  # noqa: BLE001
                pass
            try:
                conn.execute(text("UPDATE presentation_decks SET status='failed', error_msg='服务重启，生成任务已中断，请重新生成' WHERE status IN ('pending','running','outlining','rendering')"))
            except Exception:  # noqa: BLE001
                pass
            try:
                conn.execute(text("UPDATE writing_dna_profiles SET status='failed', error_msg='服务重启，蒸馏任务已中断，请重新提交完善' WHERE status IN ('pending','running')"))
            except Exception:  # noqa: BLE001
                pass
            # book_deep 新增 chapter_hashes_json 列
            try:
                bd_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(book_deep)")).fetchall()]
                if "chapter_hashes_json" not in bd_cols:
                    conn.execute(text("ALTER TABLE book_deep ADD COLUMN chapter_hashes_json TEXT"))
                if "paper_card" not in bd_cols:
                    conn.execute(text("ALTER TABLE book_deep ADD COLUMN paper_card TEXT"))
                if "card_audit_json" not in bd_cols:
                    conn.execute(text("ALTER TABLE book_deep ADD COLUMN card_audit_json TEXT"))
            except Exception:  # noqa: BLE001
                pass
            # import_tasks 新增 retry_count 列
            try:
                it_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(import_tasks)")).fetchall()]
                if "retry_count" not in it_cols:
                    conn.execute(text("ALTER TABLE import_tasks ADD COLUMN retry_count INTEGER DEFAULT 0"))
            except Exception:  # noqa: BLE001
                pass
            # PDF 批注 v2：多页分段、原文锚点与待重定位状态。
            try:
                ann_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(annotations)")).fetchall()]
                if "schema_version" not in ann_cols:
                    conn.execute(text("ALTER TABLE annotations ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1"))
                if "anchor_json" not in ann_cols:
                    conn.execute(text("ALTER TABLE annotations ADD COLUMN anchor_json TEXT"))
                if "status" not in ann_cols:
                    conn.execute(text("ALTER TABLE annotations ADD COLUMN status VARCHAR(24) NOT NULL DEFAULT 'active'"))
                if "mark_type" not in ann_cols:
                    conn.execute(text("ALTER TABLE annotations ADD COLUMN mark_type VARCHAR(20) NOT NULL DEFAULT 'highlight'"))
                if "origin" not in ann_cols:
                    conn.execute(text("ALTER TABLE annotations ADD COLUMN origin VARCHAR(20) NOT NULL DEFAULT 'user'"))
            except Exception:  # noqa: BLE001
                pass
            # 加密旧的明文 API Key（向后兼容：旧版直接存明文，新版加密存储）
            try:
                from backend.app.core import crypto
                for key_name in ("deepseek_api_key", "vision_api_key"):
                    row = conn.execute(text("SELECT value FROM settings WHERE key=:k"), {"k": key_name}).fetchone()
                    if row and row[0] and not row[0].startswith("enc:"):
                        encrypted = crypto.encrypt(row[0])
                        conn.execute(text("UPDATE settings SET value=:v WHERE key=:k"), {"v": encrypted, "k": key_name})
            except Exception:  # noqa: BLE001
                pass
            conn.commit()
    except Exception:  # noqa: BLE001
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 数据层检查：完整性检测 + 自动备份 + 版本管理
    try:
        from backend.app.core.data_manager import run_data_checks
        checks = run_data_checks()
        if not checks.get("integrity", {}).get("ok", True):
            print(f"[startup] ⚠️ 数据库完整性检查: {checks['integrity']['message']}")
            if checks["integrity"].get("restored_from"):
                print(f"[startup] 已从备份恢复: {checks['integrity']['restored_from']}")
        if checks.get("backup"):
            print(f"[startup] 自动备份: {checks['backup']}")
    except Exception:  # noqa: BLE001
        pass

    # 建表（幂等）
    Base.metadata.create_all(bind=engine)
    _migrate()

    # FTS5 虚拟表（带索引版本检查）
    try:
        from backend.app.core.data_manager import FTS_INDEX_VERSION, get_fts_index_version, set_fts_index_version
        current_fts_ver = get_fts_index_version()
        fts.init_fts(force_rebuild=(current_fts_ver < FTS_INDEX_VERSION))
        set_fts_index_version(FTS_INDEX_VERSION)
    except Exception:  # noqa: BLE001
        fts.init_fts()

    # 恢复中断的导入任务（服务重启后自动续解析）
    try:
        from backend.app.worker.tasks import recover_pending_tasks
        recovered = recover_pending_tasks()
        if recovered:
            print(f"[startup] 恢复 {len(recovered)} 个中断的导入任务")
    except Exception:  # noqa: BLE001
        pass
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

    # 初始化服务注册表（统一管理服务层组件）
    try:
        from backend.app.services.service_registry import registry
        registry.init()
    except Exception:  # noqa: BLE001
        pass

    yield

    # 关闭时清理服务资源
    try:
        from backend.app.services.service_registry import registry
        registry.shutdown()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="Study assistant", version="2.1.0", lifespan=lifespan)


class SPAStaticFiles(StaticFiles):
    """静态资源不存在时回退 index.html，支持刷新和协议深链。"""

    async def get_response(self, path: str, scope):
        if scope.get("path", "").startswith("/api/") or path.lstrip("/").startswith("api/"):
            raise StarletteHTTPException(status_code=404, detail="API endpoint not found")
        try:
            response = await super().get_response(path, scope)
            if response.status_code != 404:
                return response
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
        return await super().get_response("index.html", scope)

# 本地访问控制：只允许本服务与本地开发源，拒绝任意来源跨域（防恶意网页调用本地 API）
# 8000–8010 are runtime fallback ports; 8011 is the documented isolated UI-smoke port.
_LOCAL_APP_PORTS = {*range(8000, 8012), app_settings.port}
_ALLOWED_ORIGINS = [
    f"http://{host}:{port}"
    for host in ("127.0.0.1", "localhost")
    for port in sorted(_LOCAL_APP_PORTS | {5173})
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
app.include_router(tags.router)
app.include_router(shelves.router)
app.include_router(draw.router)
app.include_router(presentations.router)
app.include_router(literature.router)
app.include_router(writing.router)


@app.get("/api/health")
def health():
    # 启动器据此区分占用端口的旧版进程，避免新前端复用缺少新 API 的后端。
    return {
        "status": "ok",
        "app": "study-assistant",
        "api_revision": 4,
        "capabilities": {"shelves_write": True, "knowledge_records": True, "annotation_underline": True,
                         "writing_dna": True, "ai_tone_docx": True, "knowledge_insights": True},
    }


@app.get("/api/health/data")
def health_data():
    """数据层健康状态：完整性、版本（不暴露路径等敏感信息）。"""
    from backend.app.core.data_manager import check_integrity, get_schema_version, SCHEMA_VERSION
    ok, msg = check_integrity()
    ver = get_schema_version()
    from backend.app.core.config import settings as _cfg
    return {
        "integrity_ok": ok,
        "integrity_message": "ok" if ok else "corrupted",
        "schema_version": ver,
        "expected_version": SCHEMA_VERSION,
        "db_size_mb": round(_cfg.db_path.stat().st_size / (1024 * 1024), 1) if _cfg.db_path.exists() else 0,
    }


# 静态资源（前端构建产物）—— 必须放在所有 API 路由之后
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", SPAStaticFiles(directory=_frontend_dist, html=True), name="frontend")

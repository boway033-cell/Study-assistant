"""来源身份与引用校验（P1-1）。

问题：SQLite 删除分块后整数主键会被复用，历史问答/笔记/证据卡里的
`book_id + chunk_id` 只凭整数 ID 读取，重解析后可能静默打开另一段正文。

方案：
- 每次解析生成不可混淆的 `SourceVersion.version_uid`；新分块写入主表时带
  `source_version_id` 与内容指纹 `content_hash`。
- 被替换的旧分块原文归档到 `retired_chunks`，可用 (book_id, chunk_id, hash)
  找回引用时的原文。
- 历史引用读取时调用 verify_chunk_ref：能证明同一内容才返回原文；旧数据没有
  指纹时明确标记 legacy_unverified/needs_review，绝不静默错指新正文。

内容指纹定义与 sensemaking.source_hash 保持一致（sha256(utf-8 hex)）。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Book, Chapter, Chunk, RetiredChunk, SourceVersion

# 引用身份状态（前后端共用字符串）
STATUS_OK = "ok"                                # 内容与引用时完全一致
STATUS_STALE = "stale"                          # 已重解析，返回归档的引用时原文
STATUS_LEGACY_UNVERIFIED = "legacy_unverified"  # 旧引用无指纹，展示当前正文但明确标未验证
STATUS_NEEDS_REVIEW = "needs_review"            # 旧引用无指纹且书已重解析，无法确定原文
STATUS_INVALID = "invalid"                      # 有指纹但对不上任何版本/归档
STATUS_MISSING = "missing"                      # 分块已不存在且无归档

PARSER_VERSION = "v1"


def content_hash(content: str) -> str:
    """分块内容指纹，与 sensemaking.source_hash 定义一致。"""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def chapter_key(parent_key: str | None, title: str | None, level: int, occurrence: int = 0) -> str:
    """章节稳定身份。

    只依赖父章节身份、层级、归一化标题与同名出现序号，不依赖 order_index，
    因此章节整体重排后仍能匹配；同名同层章节用 occurrence 区分。
    """
    normalized = " ".join((title or "").strip().lower().split())
    payload = "\x1f".join(
        [parent_key or "", str(int(level or 1)), normalized, str(int(occurrence))]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def new_version_uid() -> str:
    return uuid.uuid4().hex


def get_version(db: Session, version_id: int | None) -> SourceVersion | None:
    if not version_id:
        return None
    return db.get(SourceVersion, version_id)


def get_version_by_uid(db: Session, version_uid: str | None) -> SourceVersion | None:
    if not version_uid:
        return None
    return db.scalar(select(SourceVersion).where(SourceVersion.version_uid == version_uid))


def active_version(db: Session, book_id: int) -> SourceVersion | None:
    book = db.get(Book, book_id)
    if not book or not book.active_source_version_id:
        return None
    return db.get(SourceVersion, book.active_source_version_id)


def book_has_reparsed(db: Session, book_id: int) -> bool:
    """该书是否在引入来源版本后至少重解析过一次（存在 retired 分块）。"""
    return db.scalar(
        select(RetiredChunk.id).where(RetiredChunk.book_id == book_id).limit(1)
    ) is not None


def _archived(
    db: Session, book_id: int, chunk_id: int, expected_hash: str | None, expected_version_uid: str | None
) -> RetiredChunk | None:
    query = select(RetiredChunk).where(
        RetiredChunk.book_id == book_id, RetiredChunk.chunk_id == chunk_id
    )
    if expected_hash:
        query = query.where(RetiredChunk.content_hash == expected_hash)
    if expected_version_uid:
        version = get_version_by_uid(db, expected_version_uid)
        if version is None:
            # 版本号对不上任何已知版本：不能退化成"任意归档"而误报同一性。
            return None
        query = query.where(RetiredChunk.source_version_id == version.id)
    return db.scalar(query.order_by(RetiredChunk.id.desc()).limit(1))


def _uid_of_version(db: Session, version_id: int | None) -> str | None:
    if not version_id:
        return None
    version = db.get(SourceVersion, version_id)
    return version.version_uid if version else None


def _result(
    status: str,
    *,
    book_id: int,
    chunk_id: int,
    content: str | None = None,
    content_hash_value: str | None = None,
    chapter_id: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    source_version_uid: str | None = None,
    message: str | None = None,
) -> dict:
    verified = status in (STATUS_OK, STATUS_STALE)
    return {
        "status": status,
        "identity_status": status,
        "identity_verified": verified,
        "book_id": book_id,
        "chunk_id": chunk_id,
        "content": content,
        "content_hash": content_hash_value,
        "chapter_id": chapter_id,
        "page_start": page_start,
        "page_end": page_end,
        "source_version_uid": source_version_uid,
        "message": message,
    }


def verify_chunk_ref(
    db: Session,
    book_id: int,
    chunk_id: int,
    expected_hash: str | None = None,
    expected_version_uid: str | None = None,
) -> dict:
    """校验 `book_id/chunk_id` 引用是否仍指向引用时的原文。

    返回 dict（见 STATUS_*）：content 为引用时原文时 identity_verified=True；
    无法证明时 content 为 None，调用方必须显式提示而不是展示当前正文。
    """
    chunk = db.get(Chunk, chunk_id)
    version = active_version(db, book_id)
    version_uid = version.version_uid if version else None

    # 1) 当前主表命中同一本书：优先用内容指纹证明身份。
    if chunk is not None and chunk.book_id == book_id:
        current_hash = chunk.content_hash or content_hash(chunk.content or "")
        if expected_hash:
            if current_hash == expected_hash:
                return _result(
                    STATUS_OK,
                    book_id=book_id,
                    chunk_id=chunk_id,
                    content=chunk.content,
                    content_hash_value=current_hash,
                    chapter_id=chunk.chapter_id,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    source_version_uid=version_uid,
                )
            archived = _archived(db, book_id, chunk_id, expected_hash, expected_version_uid)
            if archived is not None:
                return _result(
                    STATUS_STALE,
                    book_id=book_id,
                    chunk_id=chunk_id,
                    content=archived.content,
                    content_hash_value=archived.content_hash,
                    chapter_id=archived.chapter_id,
                    page_start=archived.page_start,
                    page_end=archived.page_end,
                    source_version_uid=_uid_of_version(db, archived.source_version_id),
                    message="该书已重新解析；此处显示的是引用时保存的原文，当前版本编号可能已指向其他内容。",
                )
            return _result(
                STATUS_INVALID,
                book_id=book_id,
                chunk_id=chunk_id,
                content_hash_value=current_hash,
                source_version_uid=version_uid,
                message="引用内容指纹与当前正文不匹配，且无法在历史版本中找到原文。",
            )
        if expected_version_uid:
            if version_uid and version_uid == expected_version_uid:
                return _result(
                    STATUS_OK,
                    book_id=book_id,
                    chunk_id=chunk_id,
                    content=chunk.content,
                    content_hash_value=current_hash,
                    chapter_id=chunk.chapter_id,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    source_version_uid=version_uid,
                )
            archived = _archived(db, book_id, chunk_id, None, expected_version_uid)
            if archived is not None:
                return _result(
                    STATUS_STALE,
                    book_id=book_id,
                    chunk_id=chunk_id,
                    content=archived.content,
                    content_hash_value=archived.content_hash,
                    chapter_id=archived.chapter_id,
                    page_start=archived.page_start,
                    page_end=archived.page_end,
                    source_version_uid=expected_version_uid or _uid_of_version(db, archived.source_version_id),
                    message="该书已重新解析；此处显示的是引用时保存的原文。",
                )
            return _result(
                STATUS_INVALID,
                book_id=book_id,
                chunk_id=chunk_id,
                source_version_uid=version_uid,
                message="引用来源版本与当前版本不匹配。",
            )
        # 2) 旧引用没有指纹：若书在引入来源版本后重解析过，无法证明编号含义。
        if book_has_reparsed(db, book_id) or (version is not None and version.seq > 1):
            return _result(
                STATUS_NEEDS_REVIEW,
                book_id=book_id,
                chunk_id=chunk_id,
                content=None,
                content_hash_value=current_hash,
                source_version_uid=version_uid,
                message="该引用来自旧版本且没有内容指纹；该书已重新解析，无法确认当前编号是否仍指向引用时的正文。",
            )
        return _result(
            STATUS_LEGACY_UNVERIFIED,
            book_id=book_id,
            chunk_id=chunk_id,
            content=chunk.content,
            content_hash_value=current_hash,
            chapter_id=chunk.chapter_id,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            source_version_uid=version_uid,
            message="该引用来自旧版本且没有内容指纹；该书尚未重新解析，当前正文很可能仍是引用时内容，请核对页码。",
        )

    # 3) 当前主表没有该书的分块（被覆盖/删除/编号属于其他书）：只信归档。
    archived = _archived(db, book_id, chunk_id, expected_hash, expected_version_uid)
    if archived is not None and not expected_hash and not expected_version_uid:
        # 没有指纹/版本号的旧引用：归档可能来自多代重解析，无法证明是哪一代。
        return _result(
            STATUS_NEEDS_REVIEW,
            book_id=book_id,
            chunk_id=chunk_id,
            message="该引用来自旧版本且没有内容指纹；编号已不在当前版本中，无法确认对应原文。",
        )
    if archived is not None:
        return _result(
            STATUS_STALE,
            book_id=book_id,
            chunk_id=chunk_id,
            content=archived.content,
            content_hash_value=archived.content_hash,
            chapter_id=archived.chapter_id,
            page_start=archived.page_start,
            page_end=archived.page_end,
            source_version_uid=_uid_of_version(db, archived.source_version_id),
            message="该分块已被新版本替换；此处显示的是引用时保存的原文。",
        )
    if chunk is None:
        return _result(
            STATUS_MISSING,
            book_id=book_id,
            chunk_id=chunk_id,
            message="该引用对应的分块已不存在，且没有可恢复的历史原文。",
        )
    return _result(
        STATUS_INVALID,
        book_id=book_id,
        chunk_id=chunk_id,
        message="该编号当前属于其他书籍，不能作为本书原文打开。",
    )


def reference_identity(db: Session, book_id: int, chunk_id: int) -> dict:
    """生成新引用时应持久化的身份字段（content_hash + version_uid）。"""
    chunk = db.get(Chunk, chunk_id)
    if chunk is None or chunk.book_id != book_id:
        return {}
    version = active_version(db, book_id)
    return {
        "content_hash": chunk.content_hash or content_hash(chunk.content or ""),
        "source_version_uid": version.version_uid if version else None,
        "source_version_id": chunk.source_version_id,
    }


def decorate_source(db: Session, source: dict) -> dict:
    """给检索结果补身份字段；失败不抛异常（检索可用性优先）。"""
    try:
        book_id = int(source.get("book_id") or 0)
        chunk_id = int(source.get("chunk_id") or 0)
        if not book_id or not chunk_id:
            return source
        identity = reference_identity(db, book_id, chunk_id)
    except Exception:  # noqa: BLE001 - 检索可用性优先，身份补全失败不阻塞搜索
        return source
    if not identity:
        return source
    return {**source, **identity}


__all__ = [
    "STATUS_OK",
    "STATUS_STALE",
    "STATUS_LEGACY_UNVERIFIED",
    "STATUS_NEEDS_REVIEW",
    "STATUS_INVALID",
    "STATUS_MISSING",
    "content_hash",
    "chapter_key",
    "new_version_uid",
    "get_version",
    "get_version_by_uid",
    "active_version",
    "verify_chunk_ref",
    "reference_identity",
    "decorate_source",
]

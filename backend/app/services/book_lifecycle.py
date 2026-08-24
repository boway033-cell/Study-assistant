"""书籍删除与重解析的数据生命周期操作。

所有操作都使用集合更新/删除，避免 ORM 为级联删除加载整本书的章节和分块。
文件删除由 API 在数据库提交成功后执行，防止事务回滚时丢失文件。
"""
from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.app.models import (
    Annotation,
    Attempt,
    Book,
    BookAnalysis,
    BookDeep,
    Chapter,
    ChatLog,
    Chunk,
    ImportTask,
    KnowledgeNode,
    LiteratureAccessAttempt,
    Note,
    PaperProfile,
    PresentationDeck,
    Quiz,
    book_tags,
)


def prepare_book_for_reparse(db: Session, book_id: int) -> None:
    """删除可再生解析产物，同时保留用户笔记、题目和知识节点。

    旧章节 ID 会失效，所以先解除用户产物上的章节关联；重新解析完成后，
    用户仍可按书籍和页码访问这些内容，而不会触发外键错误。
    """
    db.execute(update(Note).where(Note.book_id == book_id).values(chapter_id=None))
    db.execute(update(Quiz).where(Quiz.book_id == book_id).values(chapter_id=None))
    db.execute(update(KnowledgeNode).where(KnowledgeNode.book_id == book_id).values(chapter_id=None))
    db.execute(delete(BookAnalysis).where(BookAnalysis.book_id == book_id))
    db.execute(delete(BookDeep).where(BookDeep.book_id == book_id))
    chapter_ids = select(Chapter.id).where(Chapter.book_id == book_id)
    db.execute(
        update(Chapter).where(Chapter.parent_id.in_(chapter_ids)).values(parent_id=None)
    )
    db.execute(delete(Chunk).where(Chunk.book_id == book_id))
    db.execute(delete(Chapter).where(Chapter.book_id == book_id))
    db.execute(
        update(PaperProfile)
        .where(PaperProfile.book_id == book_id)
        .values(source_map_json=None)
    )


def delete_book_records(db: Session, book_id: int) -> list[str]:
    """按外键依赖顺序删除一本书，返回提交后可安全删除的汇报文件名。"""
    deck_files = list(
        db.scalars(
            select(PresentationDeck.file_path).where(
                PresentationDeck.book_id == book_id,
                PresentationDeck.file_path.is_not(None),
            )
        )
    )

    quiz_ids = select(Quiz.id).where(Quiz.book_id == book_id)
    node_ids = select(KnowledgeNode.id).where(KnowledgeNode.book_id == book_id)
    chapter_ids = select(Chapter.id).where(Chapter.book_id == book_id)

    # 跨书引用也必须先解除，否则删除目标知识节点会被外键阻止。
    db.execute(
        update(Annotation)
        .where(Annotation.knowledge_node_id.in_(node_ids))
        .values(knowledge_node_id=None)
    )
    db.execute(
        update(KnowledgeNode)
        .where(KnowledgeNode.ref_node_id.in_(node_ids))
        .values(ref_node_id=None)
    )
    db.execute(
        update(KnowledgeNode)
        .where(KnowledgeNode.parent_id.in_(node_ids))
        .values(parent_id=None)
    )
    db.execute(
        update(LiteratureAccessAttempt)
        .where(LiteratureAccessAttempt.book_id == book_id)
        .values(book_id=None)
    )
    db.execute(
        update(Chapter).where(Chapter.parent_id.in_(chapter_ids)).values(parent_id=None)
    )

    db.execute(delete(Attempt).where(Attempt.quiz_id.in_(quiz_ids)))
    for model in (
        ImportTask,
        PresentationDeck,
        Annotation,
        BookDeep,
        BookAnalysis,
        ChatLog,
        Note,
        PaperProfile,
        KnowledgeNode,
        Chunk,
        Quiz,
        Chapter,
    ):
        db.execute(delete(model).where(model.book_id == book_id))
    db.execute(delete(book_tags).where(book_tags.c.book_id == book_id))
    db.execute(delete(Book).where(Book.id == book_id))
    return [path for path in deck_files if path]

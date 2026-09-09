"""深度分析 API：三级标题目录提取/核对/AI补全/逐章总结/Markdown + 文献分类"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Book, BookDeep, Chapter, Chunk
from backend.app.services.deep_analysis import (
    audit_paper_card,
    build_paper_card,
    build_chapter_inputs,
    section_key,
    summarize_by_toc,
    to_markdown,
    verify_toc,
)

router = APIRouter(prefix="/api", tags=["deep"])

CATEGORIES = ["数学", "管理学", "经济学", "计算机", "英语", "政治", "物理", "化学", "生物", "法学", "文学", "历史", "哲学", "其他"]


async def run_deep_analysis(record, book_id: int) -> dict:
    """执行深度分析：标题提取→核对→AI补全→逐章总结→Markdown。"""
    from backend.app.core.database import SessionLocal
    from backend.app.services.llm import LLMRouter, load_llm_config
    from backend.app.worker.tasks import TaskCancelled, update_progress

    db = SessionLocal()
    try:
        book = db.get(Book, book_id)
        if not book or book.status != "ready":
            raise ValueError("书籍不可用")

        deep = db.scalar(select(BookDeep).where(BookDeep.book_id == book_id))
        if not deep:
            deep = BookDeep(book_id=book_id, status="running")
            db.add(deep)
        else:
            deep.status = "running"
            deep.error_msg = None
        db.commit()

        update_progress(record, 0.1, "deep", "正在提取三级标题目录...")
        chapters = db.scalars(select(Chapter).where(Chapter.book_id == book_id)
                              .order_by(Chapter.order_index, Chapter.id)).all()
        chunks = db.scalars(select(Chunk).where(Chunk.book_id == book_id)
                           .order_by(Chunk.chunk_index, Chunk.id)).all()
        # Respect the reviewed directory; never replace it with headings extracted
        # from a chunk's first lines. Root ordinal is independent of order_index.
        toc, ch_texts, section_texts = build_chapter_inputs(chapters, chunks)
        if not toc:
            toc = [{"title": book.title, "level": 1, "page": 1}]
            ch_texts = {1: "\n\n".join(f"[B{book_id}-C{c.id}]\n{c.content}" for c in chunks)}
            section_texts = {section_key(toc[0]): "\n\n".join(c.content for c in chunks)}

        verify = verify_toc(toc)
        update_progress(record, 0.3, "deep", f"核对完成：{verify['chapters']}章/{verify['sections']}节，缺失 {len(verify['issues'])} 项")

        # AI 补全（有 Key 时）
        cfg = load_llm_config(db, "research")
        provider = LLMRouter.get("auto", cfg)
        use_ai = bool(cfg.get("configured", cfg.get("deepseek_api_key")))

        # 逐章 AI 总结（增量缓存：跳过内容未变化的章节）
        summaries: list[dict] = []
        import hashlib as _hashlib
        if use_ai:
            update_progress(record, 0.6, "deep", "AI 按目录逐章精读总结...")
            # Version the cache to invalidate old truncated/misaligned summaries.
            ch_hashes = {i: _hashlib.sha256(("full-chapter-v2:" + text).encode()).hexdigest()
                         for i, text in ch_texts.items()}

            # 加载旧缓存：已总结且内容哈希未变的章节跳过
            old_summaries: dict[str, str] = {}  # title -> summary
            old_hashes: dict[int, str] = {}
            if deep.summaries_json:
                try:
                    for s in json.loads(deep.summaries_json):
                        old_summaries[s.get("key", s.get("title", ""))] = s.get("summary", "")
                except (ValueError, TypeError):
                    pass
            if deep.chapter_hashes_json:
                try:
                    for h in json.loads(deep.chapter_hashes_json):
                        old_hashes[int(h["order_index"])] = h["hash"]
                except (ValueError, TypeError):
                    pass

            # 只对内容有变化的章节调 AI；未变的用缓存
            chapters_toc = [t for t in toc if t["level"] == 1]
            need_summarize: dict[int, str] = {}  # 章序号 -> 文本
            cached_summaries: list[dict] = []
            for i, ch_toc in enumerate(chapters_toc, start=1):
                title = ch_toc["title"]
                key = section_key(ch_toc)
                text = ch_texts.get(i, "")
                cur_hash = ch_hashes.get(i, "")
                old_hash = old_hashes.get(i, "")
                if text and cur_hash == old_hash and key in old_summaries:
                    # 内容未变，用缓存
                    cached_summaries.append({"title": title, "key": key, "summary": old_summaries[key]})
                elif text:
                    need_summarize[i] = text
                else:
                    cached_summaries.append({"title": title, "key": key, "summary": "（该章无正文内容）"})

            skipped = len(cached_summaries)
            need_count = len(need_summarize)
            if skipped > 0:
                update_progress(record, 0.62, "deep",
                                f"增量缓存：跳过 {skipped} 章未变，需总结 {need_count} 章")

            # 只对 need_summarize 的章节调 AI
            new_summaries: list[dict] = []
            completed = {s["key"]: s for s in cached_summaries}
            def persist_chapter(i, result):
                completed[result["key"]] = result
                partial = [completed[section_key(t)] for t in chapters_toc if section_key(t) in completed]
                deep.toc_json = json.dumps(toc, ensure_ascii=False)
                deep.summaries_json = json.dumps(partial, ensure_ascii=False)
                deep.chapter_hashes_json = json.dumps([
                    {"order_index": n, "hash": ch_hashes[n]}
                    for n, t in enumerate(chapters_toc, 1) if section_key(t) in completed], ensure_ascii=False)
                deep.markdown = to_markdown(book.title, toc, partial, section_texts)
                db.commit()
                record.result = {"completed_chapters": len(partial), "total_chapters": len(chapters_toc)}
            db.commit()  # no database read transaction held during remote inference
            if need_summarize:
                def _sum_progress(i, total, title):
                    update_progress(record, 0.62 + 0.2 * i / max(total, 1), "deep",
                                    f"AI 精读总结 {i}/{total}：{title[:30]}")
                new_summaries = await summarize_by_toc(provider, book.title, toc, need_summarize,
                                                       on_progress=_sum_progress, on_chapter=persist_chapter)

            # 合并缓存 + 新总结（按目录顺序）
            new_map = {s["key"]: s for s in new_summaries}
            for ch_toc in chapters_toc:
                title = ch_toc["title"]
                key = section_key(ch_toc)
                if key in new_map:
                    summaries.append(new_map[key])
                else:
                    cs = next((c for c in cached_summaries if c["key"] == key), None)
                    if cs:
                        summaries.append(cs)

            # 保存新的哈希表
            deep.chapter_hashes_json = json.dumps(
                [{"order_index": k, "hash": v} for k, v in sorted(ch_hashes.items())],
                ensure_ascii=False)
            update_progress(record, 0.85, "deep", "正在生成 Markdown...")
        else:
            update_progress(record, 0.85, "deep", "未配置 AI，生成纯本地 Markdown（无 AI 总结）...")

        # Markdown
        md = to_markdown(book.title, toc, summaries, section_texts)
        deep.toc_json = json.dumps(toc, ensure_ascii=False)
        deep.summaries_json = json.dumps(summaries, ensure_ascii=False)
        deep.markdown = md
        db.commit()

        # 证据型 Paper Card：复用同一批来源块，不重复提取 PDF。
        if use_ai:
            # 上面的多次 commit 已让 chunks 全部过期（session 默认 expire_on_commit），
            # 这里重取一次：否则 build_paper_card 逐块访问 .content/.page_start 时
            # 会为每个对象单独发一条懒加载查询。
            chunks = db.scalars(select(Chunk).where(Chunk.book_id == book_id)
                                .order_by(Chunk.chunk_index)).all()
            update_progress(record, 0.92, "deep", "正在生成证据型阅读卡...")
            try:
                card = await build_paper_card(provider, book.title, toc, chunks, summaries=summaries,
                    on_progress=lambda _: update_progress(record, .92, "deep", "正在生成阅读卡，章节正文已保存"))
                deep.paper_card = card
                deep.card_audit_json = json.dumps(audit_paper_card(card), ensure_ascii=False)
            except TaskCancelled:
                raise
            except Exception as card_exc:
                # 把供应商的真实拒绝原因透给用户（模型未开通 / 模型名写错 / 限流），
                # 否则界面只有一句笼统的"生成失败"，用户无从下手。
                deep.error_msg = (f"阅读卡生成失败：{str(card_exc)[:200]}；"
                                  "逐章精读与原文已保存，可重新研读重试阅读卡。")

        clean_toc = [{k: v for k, v in t.items() if k != "parent"} for t in toc]
        deep.toc_json = json.dumps(clean_toc, ensure_ascii=False)
        deep.summaries_json = json.dumps(summaries, ensure_ascii=False)
        deep.markdown = md
        deep.status = "done"
        db.commit()
        update_progress(record, 1.0, "deep", deep.error_msg or "完成")
        return {"toc": len(toc), "chapters": verify["chapters"], "sections": verify["sections"],
                "summaries": len(summaries), "markdown_chars": len(md),
                "paper_card": bool(deep.paper_card), "ai": use_ai}
    except TaskCancelled:
        # 用户主动取消 ≠ 研读失败。逐章结果已由 persist_chapter 增量提交，
        # 这里只回滚当前未提交的部分；重新研读时会命中哈希表跳过已完成章节。
        db.rollback()
        raise
    except Exception as e:  # noqa: BLE001
        db.rollback()
        deep = db.scalar(select(BookDeep).where(BookDeep.book_id == book_id))
        if deep:
            deep.status = "failed"
            deep.error_msg = str(e)
            db.commit()
        raise
    finally:
        db.close()


@router.post("/books/{book_id}/deep-analyze", status_code=202)
def deep_analyze(book_id: int, db: Session = Depends(get_db)):
    """手动触发深度分析（后台任务）。"""
    from backend.app.worker.tasks import has_active_task, submit

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    if book.status != "ready":
        raise HTTPException(409, "书籍尚未解析完成")
    # 单 worker 串行队列不会让两个任务并发写坏数据，但会重复计费并在任务中心
    # 留两条同名进度；提交前先告知用户。
    if has_active_task("deep", book_id):
        raise HTTPException(409, "该书已有研读任务在排队或运行中；请在任务中心等待完成或停止后再提交")
    record = submit("deep", lambda rec: run_deep_analysis(rec, book_id), book_id=book_id)
    return {"task_id": record.id, "status": "running"}


@router.get("/books/{book_id}/deep")
def get_deep(book_id: int, db: Session = Depends(get_db)):
    """获取深度分析产物（状态/目录/总结/Markdown）。"""
    deep = db.scalar(select(BookDeep).where(BookDeep.book_id == book_id))
    if not deep:
        return {"status": "none", "toc": [], "summaries": [], "markdown": "", "error_msg": None}
    try:
        toc = json.loads(deep.toc_json) if deep.toc_json else []
    except (ValueError, TypeError):
        toc = []
    try:
        summaries = json.loads(deep.summaries_json) if deep.summaries_json else []
    except (ValueError, TypeError):
        summaries = []
    return {
        "status": deep.status, "toc": toc, "summaries": summaries,
        "markdown": deep.markdown or "", "error_msg": deep.error_msg,
        "paper_card": deep.paper_card or "",
        "card_audit": json.loads(deep.card_audit_json) if deep.card_audit_json else None,
        "updated_at": deep.updated_at.isoformat() if deep.updated_at else None,
    }


# ---------- 文献分类 ----------
def _get_classify_inputs(db: Session, book_id: int) -> tuple[list[str], list[str]]:
    """获取分类用的关键词 + 章节标题。"""
    from backend.app.models import BookAnalysis, Chapter
    keywords: list[str] = []
    analysis = db.scalar(select(BookAnalysis).where(BookAnalysis.book_id == book_id))
    if analysis and analysis.keywords_json:
        try:
            keywords = json.loads(analysis.keywords_json)
        except (ValueError, TypeError):
            keywords = []
    chapters = [c.title for c in db.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index).limit(40)
    ).all()]
    return keywords, chapters


async def _classify_book(provider, book: Book, keywords: list[str]) -> str:
    prompt = [
        {"role": "system", "content": (
            "你是文献分类助手。根据书名、章节与关键词判断所属学科类别。"
            f"只能从这些类别中选择一个：{'、'.join(CATEGORIES)}。只输出类别名，不要解释。"
        )},
        {"role": "user", "content": f"书名：《{book.title}》\n关键词：{','.join(keywords[:30]) or '无'}"},
    ]
    answer = ""
    try:
        async for delta in provider.stream_chat(prompt):
            answer += delta
    except Exception:  # noqa: BLE001
        return "其他"
    answer = answer.strip()
    for c in CATEGORIES:
        if c in answer:
            return c
    return "其他"


@router.post("/books/{book_id}/classify")
async def classify_book(book_id: int, db: Session = Depends(get_db)):
    """分析文献主题并分类（存 books.category）。有 API Key 时用 AI，无则本地降级。"""
    from backend.app.services.llm import LLMRouter, load_llm_config
    from backend.app.services.analyzer.classify import classify_local

    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    keywords, chapters = _get_classify_inputs(db, book_id)
    cfg = load_llm_config(db, "research")
    if not cfg.get("deepseek_api_key"):
        # 本地降级分类
        category = classify_local(book.title, keywords, chapters)
        book.category = category
        db.commit()
        return {"category": category, "method": "local"}
    provider = LLMRouter.get("auto", cfg)
    category = await _classify_book(provider, book, keywords)
    book.category = category
    db.commit()
    return {"category": category, "method": "ai"}


class CategoryReq(BaseModel):
    category: str


@router.patch("/books/{book_id}/category")
def set_category(book_id: int, req: CategoryReq, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    book.category = req.category.strip() or None
    db.commit()
    return {"category": book.category}


@router.get("/deep/status")
def deep_status_all(db: Session = Depends(get_db)):
    """全部书籍的深度分析状态。"""
    from backend.app.models import BookDeep as _BookDeep
    rows = db.execute(select(_BookDeep.book_id, _BookDeep.status)).all()
    return {str(bid): st for bid, st in rows}


@router.post("/books/classify-all")
async def classify_all(db: Session = Depends(get_db)):
    """一键分类全部书籍。有 API Key 时用 AI，无则本地降级。逐个处理，失败跳过。"""
    from backend.app.services.llm import LLMRouter, load_llm_config
    from backend.app.services.analyzer.classify import classify_local

    cfg = load_llm_config(db, "research")
    has_key = bool(cfg.get("deepseek_api_key"))
    provider = LLMRouter.get("auto", cfg) if has_key else None
    books = db.scalars(select(Book).where(Book.status == "ready")).all()
    done = {}
    method = "ai" if has_key else "local"
    for book in books:
        if book.category:
            done[book.id] = book.category
            continue
        keywords, chapters = _get_classify_inputs(db, book.id)
        if has_key:
            cat = await _classify_book(provider, book, keywords)
        else:
            cat = classify_local(book.title, keywords, chapters)
        book.category = cat
        done[book.id] = cat
        db.commit()
    return {"classified": done, "method": method}

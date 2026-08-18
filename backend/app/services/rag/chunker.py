"""章节构建与文本切片（docs/02-database.md §3 的配套逻辑）

- 依据 TocItem 构建多级章节树（含 parent_id / start_page / end_page）
- 按章节把页面文本切成 Chunk（固定窗口 + 重叠）
- P0 检索依赖 FTS5（jieba 分词），因此 chunk 同时产出"原文"与"分词文本"
"""
from __future__ import annotations

import re

from backend.app.core.config import settings
from backend.app.services.parser import ParseResult, TocItem


# ---------- 章节树构建 ----------
def build_chapters(toc: list[TocItem], total_pages: int) -> list[dict]:
    """把扁平目录转成层级结构。

    返回: [{"title","level","order_index","start_page","end_page","parent_id"}, ...]
    """
    if not toc:
        # 无目录：整本作为一章
        return [{"title": "全书", "level": 1, "order_index": 0,
                 "start_page": 1, "end_page": max(total_pages, 1), "parent_id": None}]

    chapters: list[dict] = []
    stack: list[dict] = []  # (level, id_in_list)

    # 归一化 level：若首项 level>1，整体下移
    min_level = min(t.level for t in toc)
    for i, t in enumerate(toc):
        level = max(1, t.level - min_level + 1)
        node = {
            "title": t.title,
            "level": level,
            "order_index": i,
            "start_page": t.page,
            "end_page": None,  # 由下一个兄弟决定
            "parent_id": None,
        }
        # 找父节点：向上找最近 level-1 的节点
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        if stack:
            node["parent_id"] = stack[-1]["id"]
        chapters.append(node)
        stack.append({"level": level, "id": i})

    # 补 end_page：兄弟的 start_page-1，最后一个是 total_pages
    for i, ch in enumerate(chapters):
        nxt = next((c for c in chapters if c["parent_id"] == ch["parent_id"]
                    and c["order_index"] > ch["order_index"]), None)
        if nxt:
            ch["end_page"] = max(ch["start_page"], nxt["start_page"] - 1)
        else:
            ch["end_page"] = max(ch["start_page"], total_pages)

    return chapters


# ---------- 文本切片 ----------
def split_pages_into_chunks(
    pages: list[str],
    chapter_pages: list[tuple[int | None, int, int]],  # (chapter_id, start_page, end_page) 1-based
) -> list[dict]:
    """把每页文本按章节边界切块。

    chapter_pages 中 chapter_id 为 None 表示"未归属章节"的页区间。
    返回: [{"chapter_id","page_start","page_end","content","chunk_index","word_count"}]
    """
    size, overlap = settings.chunk_size, settings.chunk_overlap
    step = max(size - overlap, 50)
    chunks: list[dict] = []
    idx = 0

    for chapter_id, start_page, end_page in chapter_pages:
        buf: list[str] = []          # 当前 chunk 累积的文本片段
        buf_pages: list[int] = []    # 当前 chunk 包含的页码列表
        buf_len = 0

        for p in range(start_page, end_page + 1):
            if not (1 <= p <= len(pages)):
                continue
            page_text = pages[p - 1].strip()
            if not page_text:
                continue

            # 单页超过窗口大小：先把已有 buffer flush，再对该页自身切片
            if len(page_text) > size:
                if buf:
                    chunks.append(_make_chunk_def(chapter_id, buf, buf_pages, idx))
                    idx += 1
                    buf, buf_pages, buf_len = [], [], 0
                i = 0
                while i < len(page_text):
                    seg = page_text[i:i + size].strip()
                    if not seg:
                        i += step
                        continue
                    if i > 0 and len(seg) < step * 0.5:
                        break
                    chunks.append(_make_chunk_def(chapter_id, [seg], [p], idx))
                    idx += 1
                    i += step
                continue

            # 正常页：加入后超窗口则先 flush
            if buf and buf_len + len(page_text) + 2 > size:
                chunks.append(_make_chunk_def(chapter_id, buf, buf_pages, idx))
                idx += 1
                # 重叠：保留 buffer 尾部一部分
                if overlap > 0 and buf:
                    tail = "\n\n".join(buf)
                    keep_text = tail[-overlap:] if len(tail) > overlap else tail
                    buf = [keep_text]
                    buf_pages = [buf_pages[-1]]
                    buf_len = len(keep_text)
                else:
                    buf, buf_pages, buf_len = [], [], 0

            buf.append(page_text)
            if p not in buf_pages:
                buf_pages.append(p)
            buf_len += len(page_text) + 2  # +2 for \n\n separator

        if buf:
            chunks.append(_make_chunk_def(chapter_id, buf, buf_pages, idx))
            idx += 1

    return chunks


def _make_chunk_def(chapter_id: int | None, buf: list[str], buf_pages: list[int], idx: int) -> dict:
    """组装单个 chunk dict，page_start/page_end 取实际覆盖页码区间。"""
    content = "\n\n".join(buf)
    return {
        "chapter_id": chapter_id,
        "page_start": min(buf_pages) if buf_pages else None,
        "page_end": max(buf_pages) if buf_pages else None,
        "content": content,
        "chunk_index": idx,
        "word_count": len(content),
    }


def build_chapter_pages(chapters: list[dict], total_pages: int) -> list[tuple[int | None, int, int]]:
    """章节区间列表；无章节覆盖的页区间归为 chapter_id=None。"""
    intervals: list[tuple[int | None, int, int]] = []
    for i, ch in enumerate(chapters):
        intervals.append((i, ch["start_page"], ch["end_page"]))

    # 补漏：起始页之前 / 章节之间的空白页
    covered = sorted(intervals, key=lambda x: x[1])
    cursor = 1
    merged: list[tuple[int | None, int, int]] = []
    for ch_id, s, e in covered:
        if cursor < s:
            merged.append((None, cursor, s - 1))
        merged.append((ch_id, s, e))
        cursor = max(cursor, e + 1)
    if cursor <= total_pages:
        merged.append((None, cursor, total_pages))
    return merged


# ---------- 中文分词（jieba） ----------
_jieba = None
_TERMS_LOADED = False

# 内置专业术语词典（常见数学/专业课名词，可按需扩充；用户词库见 data/userdict.txt）
_EXTRA_TERMS = [
    "拉格朗日中值定理", "拉格朗日", "中值定理", "罗尔定理", "柯西中值定理",
    "泰勒公式", "泰勒展开", "麦克劳林", "牛顿莱布尼茨", "定积分", "不定积分",
    "微分方程", "偏导数", "全微分", "特征值", "特征向量", "特征方程", "行列式",
    "线性相关", "线性无关", "正交矩阵", "对称矩阵", "伴随矩阵", "逆矩阵",
    "概率分布", "随机变量", "数学期望", "方差", "协方差", "正态分布",
    "泊松分布", "二项分布", "大数定律", "中心极限定理", "傅里叶变换",
    "拉普拉斯变换", "卷积", "梯度下降", "贝叶斯", "极大似然", "熵",
]


def _get_jieba():
    global _jieba, _TERMS_LOADED
    if _jieba is None:
        import jieba
        jieba.setLogLevel(60)  # 静默
        for w in _EXTRA_TERMS:
            jieba.add_word(w)
        # 用户自定义词库（data/userdict.txt，每行一个词，可选）
        userdict = settings.data_dir / "userdict.txt"
        if userdict.exists():
            jieba.load_userdict(str(userdict))
        _jieba = jieba
        _TERMS_LOADED = True
    return _jieba


def tokenize(text: str) -> str:
    """索引分词：cut_for_search 产出丰富子词，空格连接；空结果返回原文。"""
    jb = _get_jieba()
    words = [w.strip() for w in jb.cut_for_search(text) if w.strip()]
    if not words:
        return text
    return " ".join(words)


_MATCH_SAFE = re.compile(r'[^0-9A-Za-z\u4e00-\u9fff]')


def tokenize_query(text: str) -> str:
    """查询分词：cut_for_search 后过滤单字词，用 OR 连接（召回优先）。

    说明：索引已含丰富子词，OR 保证"拉格朗日"→"拉格朗"能命中"拉格朗日中值定理"；
    过滤单字避免"日"这类噪声词导致误召回。
    """
    jb = _get_jieba()
    cleaned = _MATCH_SAFE.sub(" ", text)
    words = [w.strip() for w in jb.cut_for_search(cleaned) if w.strip()]
    # 去重并过滤单字（保留英文/数字单 token，如 x0）
    seen: set[str] = set()
    kept: list[str] = []
    for w in words:
        if w in seen:
            continue
        seen.add(w)
        if len(w) >= 2 or (w.isascii() and len(w) >= 1):
            kept.append(w)
    if not kept:
        return ""
    # OR 连接，并按词长降序（长词优先，BM25 排名更准）
    kept.sort(key=len, reverse=True)
    return " OR ".join(f'"{w}"' for w in kept[:8])
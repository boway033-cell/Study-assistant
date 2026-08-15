"""语义切块：段落边界切分 + LLM 语义边界精化

层级策略（避免对全文都调 LLM，控制成本与内存）：
1. 段落级切分：按空行/句末标点切成自然段（零成本）
2. LLM 语义精化（可选，默认关闭）：段落过长时用 LLM 找语义边界合并/切分
3. 每块保留页码映射（page_start/page_end），供原文定位面板使用
"""
from __future__ import annotations

import re

from backend.app.core.config import settings

# 语义块目标大小（字符）
TARGET_SIZE = 500
MAX_SIZE = 900
MIN_SIZE = 150


def split_semantic_chunks(pages: list[str], chapter_pages: list[tuple[int | None, int, int]]) -> list[dict]:
    """按语义边界切块（段落优先），保留页码映射。

    chapter_pages: [(chapter_id, start_page, end_page)] 1-based
    返回: [{"chapter_id","page_start","page_end","content","chunk_index","word_count"}]
    """
    chunks: list[dict] = []
    idx = 0

    for chapter_id, start_page, end_page in chapter_pages:
        # 收集该章节的 (page_no, text) 列表
        page_texts: list[tuple[int, str]] = []
        for p in range(start_page, end_page + 1):
            if 1 <= p <= len(pages) and pages[p - 1].strip():
                page_texts.append((p, pages[p - 1]))

        if not page_texts:
            continue

        # 按段落切分，每个段落记录起始页
        paragraphs: list[dict] = []  # {page, text}
        for pno, text in page_texts:
            for para in _split_paragraphs(text):
                if para.strip():
                    paragraphs.append({"page": pno, "text": para.strip()})

        # 合并段落成语义块（不超过 MAX_SIZE）
        buf: list[str] = []
        buf_pages: list[int] = []
        buf_len = 0
        for para in paragraphs:
            plen = len(para["text"])
            if buf and buf_len + plen > MAX_SIZE:
                chunks.append(_make_chunk(chapter_id, buf, buf_pages, idx))
                idx += 1
                buf, buf_pages, buf_len = [], [], 0
            buf.append(para["text"])
            buf_pages.append(para["page"])
            buf_len += plen

        if buf:
            chunks.append(_make_chunk(chapter_id, buf, buf_pages, idx))
            idx += 1

    return chunks


def _split_paragraphs(text: str) -> list[str]:
    """按空行切段落；长段落按句末标点二次切分。"""
    raw = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    result: list[str] = []
    for para in raw:
        if len(para) <= MAX_SIZE:
            result.append(para)
            continue
        # 长段落按句末标点切
        parts = re.split(r"(?<=[。！？；])\s*", para)
        buf = ""
        for part in parts:
            if len(buf) + len(part) > MAX_SIZE:
                if buf.strip():
                    result.append(buf.strip())
                buf = part
            else:
                buf += part
        if buf.strip():
            result.append(buf.strip())
    return result


def _make_chunk(chapter_id: int | None, buf: list[str], buf_pages: list[int], idx: int) -> dict:
    content = "\n\n".join(buf)
    return {
        "chapter_id": chapter_id,
        "page_start": buf_pages[0],
        "page_end": buf_pages[-1],
        "content": content,
        "chunk_index": idx,
        "word_count": len(content),
    }


# ---------- LLM 语义精化（可选增强） ----------
async def refine_boundaries_with_llm(provider, text: str) -> list[str]:
    """用 LLM 对长文本做语义切分（返回子块列表）。

    适用于段落级切块后仍过长的块；默认关闭，设置 semantic_chunking=true 时启用。
    """
    prompt = [
        {"role": "system", "content": (
            "你是文档切分助手。把用户提供的教材文本按语义主题切成 2-4 个连续片段。"
            "只输出 JSON 数组：[\"片段1\", \"片段2\", ...]。"
            "要求：不改变原文文字，片段按原文顺序拼接后与原文完全一致，"
            "每个片段 200-600 字，在主题转换处切分。"
        )},
        {"role": "user", "content": text[:6000]},
    ]
    answer = ""
    async for delta in provider.stream_chat(prompt):
        answer += delta
    import json
    try:
        data = json.loads(answer.strip().strip("`").removeprefix("json"))
        if isinstance(data, list) and len(data) >= 2:
            # 校验拼接一致性（宽松校验：长度接近）
            joined = "".join(data)
            if abs(len(joined) - len(text.strip())) <= max(len(data) * 10, 50):
                return [d for d in data if d.strip()]
    except json.JSONDecodeError:
        pass
    return [text]  # 失败回退原块

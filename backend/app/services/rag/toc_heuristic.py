"""启发式章节提取（无书签/目录时的兜底，零成本零依赖）

识别「第X章/第X节/第X编」等标题行，构建目录。
与 LLM 提取互补：启发式优先（快、可靠），失败再用 LLM。
"""
from __future__ import annotations

import re

# 章/节/编 标题模式：如「第1章公共管理导论 3」（允许尾随页码）
_CHAPTER_RE = re.compile(r"^\s*(第[一二三四五六七八九十百千万0-9]+[章篇编部])\s*([\u4e00-\u9fffA-Za-z0-9·\-（）()]{1,30})")
_SECTION_RE = re.compile(r"^\s*(第[一二三四五六七八九十百千万0-9]+[节])\s*([\u4e00-\u9fffA-Za-z0-9·\-（）()]{1,30})")


def extract_toc_heuristic(pages: list[str], min_pages: int = 3) -> list[dict]:
    """从每页文本首行提取章节标题。

    pages: 每页文本列表。返回 [{"title","level","page"}]。
    规则：页码首行匹配「第X章…」→ level1；「第X节…」→ level2。
    要求命中至少 min_pages 个章节才视为有效（避免噪声）。
    """
    results: list[dict] = []
    for pno, text in enumerate(pages, start=1):
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines:
            continue
        # 取前 3 行找标题（章节标题通常在页首）
        for line in lines[:3]:
            m = _CHAPTER_RE.match(line)
            if m:
                title = m.group(1) + m.group(2)
                # 去尾部残留数字（页码等）
                title = re.sub(r"\s*[\d\s]+$", "", title)
                results.append({"title": title, "level": 1, "page": pno})
                break
            m2 = _SECTION_RE.match(line)
            if m2:
                title = m2.group(1) + m2.group(2)
                title = re.sub(r"\s*[\d\s]+$", "", title)
                results.append({"title": title, "level": 2, "page": pno})
                break

    # 过滤：至少 min_pages 个不同章节才有效
    if len(results) < min_pages:
        return []
    # 去重：按「第X章」编号去重，只保留首次出现（页眉重复/噪声标题被丢弃）
    seen_num: set[str] = set()
    dedup: list[dict] = []
    for r in results:
        num = re.match(r"(第[一二三四五六七八九十百千万0-9]+[章篇编部])", r["title"])
        key = num.group(1) if num else r["title"]
        if key in seen_num:
            continue
        seen_num.add(key)
        dedup.append(r)
    return dedup

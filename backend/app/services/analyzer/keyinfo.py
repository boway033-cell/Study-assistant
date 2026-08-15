"""关键信息提取：定义句 / 定理 / 命题 / 关键词（零模型，正则 + jieba）"""
from __future__ import annotations

import re
from collections import Counter

# 定义句触发模式：
# 1) 「X 的概念 / 定义 / 是指 / 称为 / 叫做 / 定义为 / 即」+ 说明
_DEFINE_PATTERNS = [
    re.compile(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·\-()（）]{1,30}?)(?:的概念|的定义)(?:是|为|：|:)?\s*(.{2,150}?)(?:[。；;]|$)"),
    re.compile(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·\-()（）]{1,30}?)(?:是指|指的是|称为|叫做|定义为|即为|即|就是|表示)(.{2,150}?)(?:[。；;]|$)"),
]

# 定理/命题/引理/推论/公理/定律 编号模式
# 兼容「定理 3.2：…」「定理3.2 …」「拉格朗日中值定理：…」三种
_THEOREM_PATTERN = re.compile(
    r"(?:^|[\n。])\s*(定理|定义|命题|引理|推论|公理|定律|性质|公式)\s*([0-9]+(?:\.[0-9]+)*)?\s*[:：]?\s*([^\n]{2,150})"
)

# 命名定理模式：「XXX定理：」「XXX公式：」等（无编号）
_NAMED_THEOREM_PATTERN = re.compile(
    r"(?:^|[\n。])\s*([\u4e00-\u9fffA-Za-z]{2,20}?(?:定理|公式|定律|法则|原理))\s*[:：]\s*([^\n]{2,150})"
)

# 关键词停用词
_STOPWORDS = set("的了在是和我你他她它有这那就都而及与或也又更很到于对从被把让之其因所以如果因为但是然而因此于是从而以及而且不仅并且其中对于关于按照根据随着通过由于".replace(" ", ""))


def extract_definitions(text: str) -> list[dict]:
    """提取定义句。返回 [{term, definition}]"""
    results = []
    for pat in _DEFINE_PATTERNS:
        for m in pat.finditer(text):
            term = m.group(1).strip("：:，。 ")
            definition = m.group(2).strip("，。； ")
            if len(term) >= 2 and len(definition) >= 4:
                results.append({"term": term, "definition": definition[:200]})
    # 去重（按 definition 前缀去重，避免"导数""处的导数"这类重复）
    seen = set()
    uniq = []
    for r in results:
        key = r["definition"][:20]
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq[:30]


def extract_theorems(text: str) -> list[dict]:
    """提取定理/定义/命题等条目。返回 [{type, number, statement}]"""
    results = []
    for m in _THEOREM_PATTERN.finditer(text):
        kind = m.group(1)
        number = m.group(2) or ""
        statement = m.group(3).strip()
        if len(statement) >= 4:
            results.append({"type": kind, "number": number, "statement": statement[:200]})
    # 命名定理（XXX定理：…）
    for m in _NAMED_THEOREM_PATTERN.finditer(text):
        name = m.group(1).strip()
        statement = m.group(2).strip()
        # 避免与编号定理重复
        if any(r["type"] == name or name in r["statement"] for r in results):
            continue
        if len(statement) >= 4:
            results.append({"type": name, "number": "", "statement": statement[:200]})
    return results[:30]


def extract_keywords(text: str, top_n: int = 30) -> list[str]:
    """基于 jieba 词频提取关键词（过滤停用词、单字、数字）。"""
    from backend.app.services.rag.chunker import _get_jieba

    jb = _get_jieba()
    counter: Counter = Counter()
    for w in jb.cut(text):
        w = w.strip()
        if len(w) < 2 or w in _STOPWORDS or w.isdigit():
            continue
        if all(not c.isalnum() for c in w):
            continue
        counter[w] += 1
    # 只保留中文词或中英混合，过滤纯标点
    result = []
    for w, _ in counter.most_common(top_n * 3):
        if any("\u4e00" <= c <= "\u9fff" for c in w):
            result.append(w)
        if len(result) >= top_n:
            break
    return result


def analyze_book_text(pages: list[str]) -> dict:
    """整本书关键信息提取汇总。"""
    full = "\n".join(pages)
    return {
        "definitions": extract_definitions(full),
        "theorems": extract_theorems(full),
        "keywords": extract_keywords(full),
    }

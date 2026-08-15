"""LLM 辅助章节树提取（无目录书籍）

策略：无书签/无标题样式的文档，用 LLM 从文本中提取章节标题与页码。
仅在 toc 为空时启用；失败回退整本一章。
"""
from __future__ import annotations


async def extract_toc_with_llm(provider, pages: list[str], max_pages: int = 40) -> list[dict]:
    """从 PDF 前 N 页文本提取目录。

    pages: 每页文本列表。返回 [{"title","level","page"}] 或 []（失败）。
    """
    # 取前面若干页作为素材（目录通常在开头），并抽样中间页标题行
    sample: list[str] = []
    for i in range(min(max_pages, len(pages))):
        text = pages[i].strip()
        if text:
            sample.append(f"[第{i+1}页]\n{text[:500]}")
    if not sample:
        return []

    prompt = [
        {"role": "system", "content": (
            "你是文档结构分析助手。给定教材前面若干页文本（标注了页码），"
            "提取章节结构（章/节两级）。只输出 JSON 数组，格式："
            '[{"title":"章节标题","level":1,"page":页码}, ...]。'
            "要求：1) 按页码顺序排列；2) level 1 为章、2 为节；"
            "3) 只输出确认为标题的行，不要输出正文；4) 若无明显结构输出 []。"
        )},
        {"role": "user", "content": "\n\n".join(sample)},
    ]

    answer = ""
    try:
        async for delta in provider.stream_chat(prompt):
            answer += delta
    except Exception:  # noqa: BLE001
        return []

    import json
    try:
        data = json.loads(answer.strip().strip("`").removeprefix("json"))
        if not isinstance(data, list):
            return []
        result = []
        for item in data[:60]:
            title = str(item.get("title", "")).strip()
            page = int(item.get("page", 0))
            level = int(item.get("level", 1))
            if title and 1 <= page <= len(pages):
                result.append({"title": title, "level": level, "page": page})
        return result
    except (json.JSONDecodeError, ValueError):
        return []

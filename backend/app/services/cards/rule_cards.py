"""规则式背诵卡片生成（无 LLM 时可用，零成本）

从章节文本 + 智能分析结果生成问答卡片：
1. 定义句卡片：概念 → 定义（"X是指/称为/定义为"）
2. 定理/命题卡片：标题 → 陈述
3. 概念-释义卡片：从关键词 + 上下文提取
4. 重点句卡片：含"最重要/核心/关键"的句子
"""
from __future__ import annotations

import re

from backend.app.services.analyzer.keyinfo import extract_definitions, extract_theorems


def generate_cards_rule_based(book_id: int, chapters: list[dict], chapter_texts: dict[int, str],
                              max_per_chapter: int = 20) -> list[dict]:
    """生成卡片。

    chapters: [{"id","title"}]；chapter_texts: {chapter_id: 全文}
    返回 [{"book_id","chapter_id","front","back","tags"}]
    """
    cards: list[dict] = []
    for ch in chapters:
        text = chapter_texts.get(ch["id"], "")
        if not text:
            continue
        ch_cards = _chapter_cards(book_id, ch, text)
        cards.extend(ch_cards[:max_per_chapter])
    return cards


_NOISE_TERMS = {"是", "即", "就是", "指的", "表示", "称为", "叫做", "而言", "而言是",
                "故被", "或者", "主要", "可以", "认为", "这种", "这些", "这种是", "这一",
                "所谓", "关于", "对于", "但是", "然而", "因此", "因为", "所以", "那么",
                "其", "之", "的", "了", "在", "而"}


def _is_noise_term(term: str) -> bool:
    """过滤噪声词条：过短、无意义、碎片化。"""
    t = term.strip()
    if len(t) < 2:
        return True
    if t in _NOISE_TERMS:
        return True
    # 以虚词结尾（如"的""在""被"）多为碎片
    if t[-1] in "的被了在而是也及与或但于从对":
        return True
    return False


def _chapter_cards(book_id: int, ch: dict, text: str) -> list[dict]:
    cards: list[dict] = []
    seen: set[str] = set()

    # 1. 定义句卡片
    for d in extract_definitions(text):
        term = d["term"]
        if term in seen or _is_noise_term(term):
            continue
        seen.add(term)
        cards.append({
            "book_id": book_id, "chapter_id": ch["id"],
            "front": f"什么是{term}？（{ch['title']}）",
            "back": d["definition"],
            "tags": "定义",
        })

    # 2. 定理/命题卡片
    for t in extract_theorems(text):
        key = t["type"] + t["number"]
        if key in seen:
            continue
        seen.add(key)
        front = f"{t['type']}{t['number']}：？" if t["number"] else f"{t['type']}的内容是什么？"
        cards.append({
            "book_id": book_id, "chapter_id": ch["id"],
            "front": front, "back": t["statement"], "tags": "定理",
        })

    # 3. 概念-释义（从含"即/就是/指"的短句）
    for m in re.finditer(r"([\u4e00-\u9fff]{2,12})(?:即|就是|指的是)([\u4e00-\u9fff、，。；]{4,60})", text):
        term, expl = m.group(1), m.group(2)
        if term in seen or len(term) < 2 or _is_noise_term(term):
            continue
        seen.add(term)
        cards.append({
            "book_id": book_id, "chapter_id": ch["id"],
            "front": f"解释：{term}",
            "back": expl.strip("，。； "),
            "tags": "概念",
        })

    # 4. 重点句卡片（含"最重要/核心/关键/本质"）
    for m in re.finditer(r"([^。\n]{0,20}(?:最重要|核心|关键|本质|根本|首要)[^。\n]{5,80}[。])", text):
        sentence = m.group(1).strip()
        if len(sentence) < 12 or sentence in seen:
            continue
        seen.add(sentence)
        # 正面取句子前半，背面取完整
        split = min(20, len(sentence) // 2)
        front = sentence[:split] + "……" if len(sentence) > split else sentence
        cards.append({
            "book_id": book_id, "chapter_id": ch["id"],
            "front": front, "back": sentence, "tags": "重点",
        })

    return cards

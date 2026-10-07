"""多轮对话的上下文理解：意图分类、指代消解、历史压缩、澄清判定。

设计约束
--------
1. **零额外模型调用**：改写与意图判定全部是本地确定性规则。多轮理解最容易被
   「再调一次大模型做改写」拖垮时延，也最难在 CI 里复现；规则版本化后可回归。
2. **历史是上下文，不是证据**：历史问答只用于消解指代和承接话题，绝不进入引证。
3. **降级优先于猜测**：指代无解、问题欠定时不硬答，返回澄清问题让用户补齐。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ChatLog

# ---------- 词表 ----------

# 指代线索：结合词边界、显式对象与历史判断，不能仅按子串判定。
_ANAPHORA = (
    "它", "他们", "她们", "它们", "这", "那", "这个", "那个", "这些", "那些",
    "该", "此", "上述", "前述", "前者", "后者", "其中", "其", "本人",
    "it", "they", "them", "this", "that", "those", "these",
)

# 省略型追问：句子很短且没有新名词，语义几乎全靠上文。
_ELLIPSIS_HINTS = ("为什么", "凭什么", "怎么做到", "还有吗", "然后呢", "接着",
                   "具体", "举例", "为什么是", "所以", "因此", "还有", "更多")

_SUMMARY_HINTS = ("总结", "概括", "梳理", "要点", "摘要", "归纳", "小结",
                  "综述一下", "整体讲讲", "讲了什么", "说了什么", "summar",
                  "summary", "recap", "tl;dr")

_COMPARE_HINTS = ("哪个更", "哪个不", "区别", "差异", "对比", "相比", "还是",
                  "哪一个", "是不是", "对吗", "错吗", "有没有必要")

# 高频功能词与虚词：留在锚点里只会稀释检索信号。
_STOPWORDS = {
    "什么", "怎么", "怎样", "如何", "为什么", "哪些", "哪个", "是否", "可以", "能否",
    "这个", "那个", "这些", "那些", "它的", "他们", "请问", "我们", "你们", "他们",
    "一下", "一个", "一些", "一种", "进行", "通过", "关于", "对于", "以及", "并且",
    "不是", "还是", "这样", "那样", "因为", "所以", "但是", "如果", "的话", "时候",
    "的", "了", "是", "在", "和", "与", "对", "把", "被", "给", "让", "也", "都",
    "很", "更", "最", "会", "能", "要", "有", "无", "不", "没", "吗", "呢", "吧", "啊",
}
_GENERIC_TERMS = {
    "局限", "前提", "条件", "复杂度", "重要", "内容", "讨论", "总结", "概括", "梳理",
    "要点", "摘要", "归纳", "小结", "关系", "区别", "具体", "举例", "还有", "更多",
    "别的", "刚才", "刚刚", "前面", "上面", "上述", "解释", "补充", "说明", "继续",
    "什么是", "为什么是", "有什么", "是多少", "各自", "分别", "两者", "二者",
    "它", "它们", "这", "那", "其", "该", "此", "请", "吗", "呢",
    "what", "why", "how", "does", "do", "is", "are", "was", "were", "the", "a", "an",
    "and", "or", "it", "its", "they", "them", "this", "that", "those", "these", "of",
    "in", "on", "for", "to", "can", "could", "would", "should", "tell", "me", "about",
    "more", "please", "summary", "summarize", "recap", "discussion", "limitations",
}

# 意图取值
INTENT_NEW = "new_question"
INTENT_FOLLOWUP = "followup"
INTENT_CLARIFY = "clarify"
INTENT_SUMMARIZE = "summarize"

# 判定澄清所需的最小实词数：低于它说明用户没给出可检索的内容。
_MIN_CONTENT_TERMS = 1
_HISTORY_CHAR_BUDGET = 2600


@dataclass
class Turn:
    """一轮已完成的问答。"""

    question: str
    answer: str = ""
    sources: list[dict] = field(default_factory=list)
    log_id: int | None = None


@dataclass
class TurnContext:
    """一次提问在多轮语境下的解析结果。"""

    intent: str
    question: str
    search_query: str
    history_block: str
    anchor: str = ""
    unresolved_reference: bool = False
    clarification: str = ""
    clarification_options: list[str] = field(default_factory=list)
    turn_index: int = 0
    reason: str = ""

    def to_payload(self) -> dict:
        return {"intent": self.intent, "question": self.question, "search_query": self.search_query,
                "anchor": self.anchor, "turn_index": self.turn_index,
                "unresolved_reference": self.unresolved_reference,
                "reason": self.reason,
                "clarification": self.clarification,
                "clarification_options": self.clarification_options}


# ---------- 词法工具 ----------


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _split_words(text: str) -> list[str]:
    """中文按二元组 + 英文数字词切分，避免额外分词依赖在短问句上失真。"""
    compact = re.sub(r"\s+", "", (text or "").lower())
    grams = [compact[i:i + 2] for i in range(max(0, len(compact) - 1))
             if "\u4e00" <= compact[i] <= "\u9fff" and "\u4e00" <= compact[i + 1] <= "\u9fff"]
    return grams + re.findall(r"[a-z0-9]{2,}", (text or "").lower())


def content_terms(text: str) -> list[str]:
    """抽出可作为检索锚点的实词：去功能词、去重复、保序。"""
    terms: list[str] = []
    seen: set[str] = set()
    for candidate in _jieba_terms(text):
        token = candidate.strip().lower()
        if len(token) < 2 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    if terms:
        return terms
    # 全是功能词时不制造锚点；无分词器时只保留去除功能词后的二元组。
    fallback = (text or "").lower()
    for word in sorted(_STOPWORDS | _GENERIC_TERMS, key=len, reverse=True):
        fallback = re.sub(rf"\b{re.escape(word)}\b", " ", fallback) if word.isascii() else fallback.replace(word, " ")
    for gram in _split_words(fallback):
        if gram not in seen:
            seen.add(gram)
            terms.append(gram)
    return terms


def _jieba_terms(text: str) -> list[str]:
    try:
        from backend.app.services.rag.chunker import _get_jieba

        jb = _get_jieba()
        try:
            import jieba.posseg as posseg  # noqa: F401 — 确保 posseg 子模块已注册
        except ImportError:
            return []
        return [word for word, flag in jb.posseg.cut(text or "")
                if flag.startswith(("n", "v", "eng", "j")) or len(word) >= 3]
    except Exception:  # noqa: BLE001 — 分词不可用不应让问答整体失败
        return []


def has_anaphora(text: str) -> bool:
    lowered = (text or "").lower()
    compact = re.sub(r"\s+", "", lowered)
    for word in _ANAPHORA:
        if word.isascii():
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                return True
        elif word in {"其", "该", "此"}:
            if re.search(rf"{word}(?:的|局限|作用|前提|条件|观点|结论|方法|理论|概念)", compact):
                return True
        elif word in compact:
            return True
    return False


def _topic_terms(text: str) -> list[str]:
    return [term for term in content_terms(text) if term not in _GENERIC_TERMS]


def _is_summary_request(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _SUMMARY_HINTS)


def _is_comparison(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _COMPARE_HINTS)


# 集合型指代：本身不指向唯一对象。
_MULTI_REFERENT = ("两者", "二者", "这几项", "这几条", "这些概念", "这些结论",
                   "两个", "几点", "各自", "分别", "都")
# 对象已在本轮显式点名（含书名号、引号、明确术语）时不必再澄清。
_SPECIFIC_TARGET = re.compile(r"[《「【\"']([^》」】\"']{1,40})[》」】\"']")


def _has_multi_referent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _MULTI_REFERENT)


def _has_specific_target(text: str, turns: list[Turn]) -> bool:
    """本轮是否已点名比较对象：有书名号/引号内容，或长度足够承载明确术语。"""
    if _SPECIFIC_TARGET.search(text or ""):
        return True
    # 「A 和 B 哪个更…」——显式给出两个候选时对象是确定的。
    if re.search(r"[^\s]{2,12}(?:和|与|还有|跟)[^\s]{2,12}", text or ""):
        return True
    # 历史里只讨论过一个对象时，集合型指代也能解析。
    return len(turns) == 1 and not _has_multi_referent(turns[0].question)


# ---------- 历史读取 ----------


def load_turns(db: Session, conversation_id: str | None, *, book_id: int | None = None,
               scope_type: str | None = None, scope_id: int | None = None,
               limit: int = 6, before_id: int | None = None) -> list[Turn]:
    """按会话读取历史轮次（时间正序）。范围过滤与问答落库口径保持一致。"""
    if not conversation_id:
        return []
    query = select(ChatLog).where(ChatLog.conversation_id == conversation_id)
    if before_id is not None:
        query = query.where(ChatLog.id < before_id)
    if scope_type == "shelf" and scope_id is not None:
        query = query.where(ChatLog.shelf_id == scope_id)
    elif scope_type == "project" and scope_id is not None:
        query = query.where(ChatLog.project_id == scope_id)
    elif book_id is not None:
        query = query.where(ChatLog.book_id == book_id)
    else:
        query = query.where(ChatLog.book_id.is_(None), ChatLog.shelf_id.is_(None),
                            ChatLog.project_id.is_(None))
    rows = db.scalars(query.order_by(ChatLog.id.desc()).limit(max(1, limit))).all()
    turns = []
    for row in reversed(rows):
        try:
            import json

            sources = json.loads(row.sources_json) if row.sources_json else []
        except (TypeError, ValueError):
            sources = []
        turns.append(Turn(question=row.question or "", answer=row.answer or "",
                          sources=sources if isinstance(sources, list) else [], log_id=row.id))
    return turns


def _answer_gist(answer: str, limit: int = 160) -> str:
    # 本轮重新编号；旧的数字引用不能指向本轮同号、不同原文的资料。
    text = _clean(re.sub(r"\[(?:资料|ref)\d+\]", "", answer or "", flags=re.I))
    if not text:
        return "（上一轮无回答内容）"
    sentences = [s for s in re.split(r"(?<=[。！？!?\n])", text) if s.strip()]
    picked = ""
    for sentence in sentences:
        if len(picked) + len(sentence) > limit and picked:
            break
        picked += sentence.strip()
    return _clean(picked)[:limit] or text[:limit]


def _topic_anchor(turns: list[Turn]) -> str:
    """从最近若干轮的问题里提炼共享话题，作为追问的检索锚点。"""
    for turn in reversed(turns):
        terms = _topic_terms(turn.question)
        if terms:
            return " ".join(terms[:3])
    return ""


def build_history_block(turns: list[Turn], *, budget: int = _HISTORY_CHAR_BUDGET) -> str:
    """把历史压成「已讨论话题 + 最近轮次摘要」。超预算时丢最旧一轮。"""
    if not turns:
        return ""
    selected = list(turns)
    while True:
        lines = []
        anchor = _topic_anchor(selected)
        if anchor:
            lines.append(f"- 已讨论话题：{anchor}")
        for index, turn in enumerate(selected, 1):
            label = f"第{index}轮" if len(selected) > 1 else "上一轮"
            cited = "、".join(
                f"《{str(item.get('book_title') or '').strip() or '未命名资料'}》"
                f"{(' 第%s页' % item['page_start']) if item.get('page_start') else ''}"
                for item in turn.sources[:2] if isinstance(item, dict))
            tail = f"（引证：{cited}）" if cited else ""
            lines.append(f"- {label}问：{_clean(turn.question)[:140]}{tail}")
            lines.append(f"  {label}答：{_answer_gist(turn.answer)}")
        block = "\n".join(lines)
        if len(block) <= budget or len(selected) <= 1:
            break
        selected = selected[1:]
    return block[:budget]


# ---------- 澄清 ----------

_CLARIFY_TEMPLATES = {
    "unresolved_reference": "这一轮我没能确定「它」指的是什么。可以补一个明确对象吗？",
    "too_underdetermined": "这个问题还缺少可定位的内容。补充一个概念、章节或关键词，我再去原文里找。",
    "ambiguous_contrast": "这里有多种可能的对象需要对比。告诉我具体比较哪两项，我按原文给出差别。",
    "summarize_without_history": "当前会话还没有可总结的内容。先问一个问题，或直接指定要总结的资料与章节。",
}


def build_clarification(reason: str, options: list[str] | None = None) -> tuple[str, list[str]]:
    prompt = _CLARIFY_TEMPLATES.get(reason, _CLARIFY_TEMPLATES["too_underdetermined"])
    return prompt, list(options or [])[:4]


def _candidate_options(turns: list[Turn]) -> list[str]:
    """从历史引证里取候选对象，让用户点选而不是重新描述。"""
    options: list[str] = []
    for turn in reversed(turns):
        for item in turn.sources[:3]:
            if not isinstance(item, dict):
                continue
            title = _clean(str(item.get("book_title") or ""))
            chapter = _clean(str(item.get("chapter_title") or ""))
            label = " ".join(part for part in (f"《{title}》" if title else "", chapter) if part)
            if label and label not in options:
                options.append(label)
    return options[:4]


def _comparison_options(turns: list[Turn]) -> list[str]:
    # 对比需要两个对象；单本书标题无法补齐“两者”的含义。
    topics = list(dict.fromkeys(_clean(turn.question)[:70] for turn in reversed(turns)
                                if _topic_terms(turn.question) and not has_anaphora(turn.question)))[:4]
    return [f"「{topics[i]}」与「{topics[j]}」" for i in range(len(topics))
            for j in range(i + 1, len(topics))][:4]


# ---------- 主入口 ----------


def resolve_turn(question: str, turns: list[Turn], *, scope_label: str = "") -> TurnContext:
    """把一次提问解析成多轮语境下的检索与提示词输入。

    返回的 ``search_query`` 一定是可独立检索的句子；``intent`` 决定走哪条
    生成路径；``clarification`` 非空时应当直接澄清而不检索。
    """
    text = _clean(question)
    turn_index = len(turns) + 1
    history_block = build_history_block(turns)
    anchor = _topic_anchor(turns)
    anaphora = has_anaphora(text)
    terms = content_terms(text)

    # 1) 无历史却带指代，或问题欠定到没有可检索实词 → 澄清。
    if not turns:
        if anaphora:
            prompt, options = build_clarification("unresolved_reference")
            return TurnContext(INTENT_CLARIFY, text, text, "", turn_index=turn_index,
                               unresolved_reference=True, clarification=prompt,
                               clarification_options=options, reason="unresolved_reference")
        if len(terms) < _MIN_CONTENT_TERMS and len(text) < 6:
            prompt, options = build_clarification("too_underdetermined")
            return TurnContext(INTENT_CLARIFY, text, text, "", turn_index=turn_index,
                               clarification=prompt, clarification_options=options,
                               reason="too_underdetermined")
        return TurnContext(INTENT_NEW, text, text, "", turn_index=turn_index,
                           reason="first_turn")

    # 明确指定资料的摘要属于独立问题；会话摘要才承接历史。
    conversation_summary = (_is_summary_request(text) and not _SPECIFIC_TARGET.search(text)
                            and (not _topic_terms(text)
                                 or re.search(r"刚才|刚刚|聊|会话|前面|我们.*讨论|discussion|conversation", text, re.I)))
    if conversation_summary:
        summary_query = " ".join(dict.fromkeys(term for prior in turns for term in _topic_terms(prior.question)))[:400]
        return TurnContext(INTENT_SUMMARIZE, text, summary_query or anchor or text, history_block,
                           anchor=anchor, turn_index=turn_index, reason="summary_request")

    # 3) 对比型问题若指代对象不唯一，先澄清——必须排在追问分支之前，
    #    否则「两者哪个更重要」会被短句规则抢走并擅自挑一个对象作答。
    if _is_comparison(text):
        # 「两者/二者/这几项/它们」在历史里指向多条命题时，无法确定比较哪两项。
        if _has_multi_referent(text) and len(turns) > 1 and not _has_specific_target(text, turns):
            prompt, options = build_clarification("ambiguous_contrast", _comparison_options(turns))
            return TurnContext(INTENT_CLARIFY, text, text, history_block, anchor=anchor,
                               turn_index=turn_index, unresolved_reference=True,
                               clarification=prompt, clarification_options=options,
                               reason="ambiguous_contrast")

    # 4) 追问：带指代，或短句且几乎不引入新实词。
    is_followup = ((anaphora and not _SPECIFIC_TARGET.search(text))
                   or (not _topic_terms(text) and any(hint in text for hint in _ELLIPSIS_HINTS))
                   or (bool(re.match(r"^(?:and|also|more)\b", text, re.I)) and len(text.split()) <= 3)
                   or (not _topic_terms(text) and bool(re.match(r"^(?:why|how so)\b", text, re.I))))
    if is_followup:
        if not anchor:
            prompt, options = build_clarification("unresolved_reference", _candidate_options(turns))
            return TurnContext(INTENT_CLARIFY, text, text, history_block, turn_index=turn_index,
                               unresolved_reference=True, clarification=prompt,
                               clarification_options=options, reason="unresolved_reference")
        search_query = f"{anchor} {text}".strip()
        return TurnContext(INTENT_FOLLOWUP, text, search_query, history_block, anchor=anchor,
                           unresolved_reference=anaphora, turn_index=turn_index,
                           reason="coreference" if anaphora else "ellipsis")

    # 5) 独立新问题：历史只留作背景，不参与检索。
    return TurnContext(INTENT_NEW, text, text, history_block, anchor=anchor,
                       turn_index=turn_index, reason="self_contained")

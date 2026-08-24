"""OCR/PDF 文本重排。

目标不是改写原文，而是恢复版面被硬换行打碎之前的段落结构。标题、列表、
公式和来源锚点必须保持独立；正文只在有足够证据时合并。
"""
from __future__ import annotations

import re


_TITLE_PATTERNS = (
    r"第\s*[一二三四五六七八九十百千万0-9]+\s*[章节篇编部]",
    r"[一二三四五六七八九十百千万]+[、.]",
    r"[（(][一二三四五六七八九十百千万0-9]+[）)]",
    r"\d{1,3}(?:[.．]\d{1,3}){1,3}(?:\s+|[、.．])",
    r"(?:abstract|introduction|background|related\s+work|methods?|materials?(?:\s+and\s+methods)?|results?|discussion|conclusions?|references|acknowledg(?:e)?ments?)\b",
)
_TITLE_RE = re.compile(r"^\s*(?:" + "|".join(_TITLE_PATTERNS) + r")", re.IGNORECASE)
_LIST_RE = re.compile(r"^\s*(?:[-*•·]|\d+[)）]|[A-Za-z][)）])\s+")
_SENTENCE_END_RE = re.compile(r"[。！？!?；;.]\s*[\"'”’）)\]]*\s*$")


def remove_repeated_lines(text: str) -> str:
    """去除相邻重复行，同时保留单个空行作为段落证据。"""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue
        if out and out[-1].strip() == stripped:
            continue
        out.append(line)
    return "\n".join(out)


def collapse_repeated_chars(text: str, max_repeat: int = 3) -> str:
    """压缩明显的 OCR 字符/标点抖动；阈值保守，避免破坏正常叠词。"""
    text = re.sub(r"([\u4e00-\u9fff])\1{%d,}" % (max_repeat - 1), r"\1", text)
    return re.sub(r"([，。；：！？、])\1{2,}", r"\1", text)


def _looks_like_title(line: str) -> bool:
    s = re.sub(r"\s+", " ", line.strip())
    if not s or len(s) > 90 or _SENTENCE_END_RE.search(s):
        return False
    return bool(_TITLE_RE.match(s))


def _looks_like_formula(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    math_chars = len(re.findall(r"[=+−×÷∑∫√≤≥≈<>^_]", s))
    return math_chars >= 2 or bool(re.match(r"^(?:Eq\.?\s*)?[（(]?\d+[）)]?\s+[A-Za-z].*=", s))


def _join_lines(prev: str, cur: str) -> str:
    """按语言边界拼接两条正文行。"""
    prev = prev.rstrip()
    cur = cur.lstrip()
    if re.search(r"[A-Za-z]-$", prev) and re.match(r"[a-z]", cur):
        return prev[:-1] + cur
    if re.search(r"[A-Za-z0-9,;:]$", prev) and re.match(r"[A-Za-z0-9]", cur):
        return prev + " " + cur
    if re.search(r"[\u4e00-\u9fff，、：；]$", prev) and re.match(r"[\u4e00-\u9fff]", cur):
        return prev + cur
    return prev + " " + cur


def reflow_paragraphs(text: str) -> str:
    """恢复段落并保护标题、列表和公式。"""
    paragraphs: list[str] = []
    current = ""
    force_boundary = False

    def flush() -> None:
        nonlocal current
        if current.strip():
            paragraphs.append(current.strip())
        current = ""

    for raw in text.splitlines():
        line = re.sub(r"[\t\u3000]+", " ", raw).strip()
        if not line:
            flush()
            force_boundary = True
            continue
        structural = _looks_like_title(line) or bool(_LIST_RE.match(line)) or _looks_like_formula(line)
        if structural:
            flush()
            paragraphs.append(line)
            force_boundary = True
            continue
        if not current:
            current = line
        elif force_boundary or _SENTENCE_END_RE.search(current):
            flush()
            current = line
        else:
            current = _join_lines(current, line)
        force_boundary = False
    flush()
    return "\n\n".join(paragraphs)


def merge_broken_english(text: str) -> str:
    """合并英文断词，但不把两行正常句子末首单词粘在一起。"""
    text = re.sub(r"([A-Za-z])-\n\s*([a-z])", r"\1\2", text)
    # 仅整行都是单个词片段时尝试无连字符拼接（OCR 常见 compu/ter）。
    return re.sub(r"(?m)^([A-Za-z]{2,})\n([a-z]{2,})$", r"\1\2", text)


def merge_broken_chinese(text: str) -> str:
    """兼容旧调用：使用统一段落重排器。"""
    return reflow_paragraphs(text)


def clean_text(text: str, header_lines: set[str] | None = None,
               footer_lines: set[str] | None = None) -> str:
    """过滤页眉页脚并恢复可读段落；不新增或改写原文事实。"""
    header_lines = header_lines or set()
    footer_lines = footer_lines or set()
    kept: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            kept.append("")
            continue
        if s in header_lines or s in footer_lines:
            continue
        if re.fullmatch(r"[-–—\s]*\d{1,4}[-–—\s]*", s):
            continue
        kept.append(line)
    cleaned = remove_repeated_lines("\n".join(kept))
    cleaned = collapse_repeated_chars(cleaned)
    cleaned = merge_broken_english(cleaned)
    return reflow_paragraphs(cleaned).strip()

"""Conservative checks for mechanical Chinese writing issues in model output.

This reports possible issues; it never edits generated text or claims to verify
grammar, meaning, evidence or quotations. Code, links, citations and quotations
are excluded from checks where their original spelling matters.
"""
from __future__ import annotations

import json
import re

from backend.app.services.chinese_writing_style import STYLE_VERSION

_HEADING = re.compile(r"^\s*(#{1,6})[ \t]+(.+?)\s*$")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])[ \t]+")
_INLINE_PROTECTED = re.compile(
    r"`[^`\n]*`|\$[^$\n]+\$|<[^>\n]+>|!?\[[^\]\n]*\]\([^\)\n]*\)|https?://[^\s<>()]+|"
    r"\[(?:资料\d+|B\d+:C\d+:P\d+(?:-\d+)?|NOTE:\d+|EVIDENCE:\d+|REPORT:\d+|WEB:[^\]]+)\]|"
    r"[“「][^”」\n]*[”」]|‘[^’\n]*’|\"[^\"\n]*\""
)
_CJK = r"\u4e00-\u9fff"
_MISSING_SPACE = re.compile(rf"(?<=[{_CJK}])[A-Za-z0-9]|[A-Za-z0-9](?=[{_CJK}])")
_PUNCT_SPACE = re.compile(r"[ \t]+[，。！？；：、）》】]|[（《【][ \t]+")
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _editable(line: str) -> str:
    return _INLINE_PROTECTED.sub("※", line)


def normalize_chinese_format(text: str) -> str:
    """Fix safe typography only; preserve quotations and machine-significant spans.

    Sentence rewriting, evidence interpretation and terminology choice require
    semantic judgment and are intentionally outside this formatter.
    """
    if not text or not re.search(r"[\u4e00-\u9fff]", text):
        return text

    def fix_segment(segment: str) -> str:
        segment = segment.replace("\u3000", " ").translate(_FULLWIDTH_DIGITS)
        segment = re.sub(rf"(?<=[{_CJK}]),(?=[{_CJK}])", "，", segment)
        segment = re.sub(r"！{2,}", "！", segment)
        segment = re.sub(r"[ \t]+(?=[，。！？；：、）》】])", "", segment)
        segment = re.sub(r"(?<=[（《【])[ \t]+", "", segment)
        segment = re.sub(rf"(?<=[{_CJK}])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[{_CJK}])", " ", segment)
        segment = re.sub(r" {2,}", " ", segment)
        return segment

    result: list[str] = []
    fence = ""
    blank = False
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            fence = "" if fence and marker.group(1)[0] == fence else marker.group(1)[0]
            result.append(line)
            blank = False
            continue
        if fence or line.startswith(("    ", "\t")) or line.lstrip().startswith((">", "|")):
            result.append(line)
            blank = False
            continue
        if not line.strip():
            if result and not blank:
                result.append("")
            blank = True
            continue
        blank = False
        pieces: list[str] = []
        cursor = 0
        for match in _INLINE_PROTECTED.finditer(line):
            pieces.append(fix_segment(line[cursor:match.start()]))
            pieces.append(match.group())
            cursor = match.end()
        pieces.append(fix_segment(line[cursor:]))
        normalized = "".join(pieces).rstrip()
        heading = _HEADING.match(normalized)
        if heading and heading.group(2).endswith(("。", "，", "；", "：")):
            normalized = normalized[:-1]
        result.append(normalized)
    return "\n".join(result).rstrip("\n") + ("\n" if text.endswith("\n") else "")


def audit_chinese_style(text: str, *, max_issues: int = 24) -> dict:
    """Return line-based review cues for user-visible Markdown or plain prose."""
    if not text or not text.strip():
        return {"version": STYLE_VERSION, "applicable": False, "issue_count": 0, "issues": []}
    if not re.search(r"[\u4e00-\u9fff]", text):
        return {"version": STYLE_VERSION, "applicable": False, "issue_count": 0, "issues": []}
    try:
        if isinstance(json.loads(text.strip()), (dict, list)):
            return {"version": STYLE_VERSION, "applicable": False, "issue_count": 0, "issues": []}
    except (TypeError, ValueError):
        pass

    issues: list[dict] = []
    paragraphs: list[tuple[int, str]] = []
    paragraph_lines: list[str] = []
    paragraph_start = 1
    heading_stack: list[tuple[int, str]] = []
    last_heading_level = 0
    fence = ""
    blank_run = 0

    def add(rule: str, line: int, message: str, severity: str = "review") -> None:
        if len(issues) < max_issues:
            issues.append({"rule": rule, "line": line, "message": message, "severity": severity})

    def finish_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            paragraphs.append((paragraph_start, " ".join(paragraph_lines)))
            paragraph_lines = []

    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        marker = re.match(r"^\s*(`{3,}|~{3,})", raw)
        if marker:
            finish_paragraph()
            if not fence:
                fence = marker.group(1)[0]
            elif marker.group(1)[0] == fence:
                fence = ""
            blank_run = 0
            continue
        if fence or raw.startswith(("    ", "\t")) or stripped.startswith(">") or stripped.startswith("|"):
            finish_paragraph()
            blank_run = 0
            continue
        if not stripped:
            finish_paragraph()
            blank_run += 1
            if blank_run == 2:
                add("extra_blank_line", number, "段落之间保留一个空行即可。")
            continue
        blank_run = 0

        heading = _HEADING.match(raw)
        if heading:
            finish_paragraph()
            level, title = len(heading.group(1)), heading.group(2).strip()
            if last_heading_level and level > last_heading_level + 1:
                add("heading_jump", number, "标题层级跳跃；应逐级展开。", "error")
            if level > 4:
                add("heading_depth", number, "标题层级过深；建议改为列表或拆分文档。")
            if title.endswith(("。", "，", "；", "：")):
                add("heading_punctuation", number, "标题末尾不使用点号。", "error")
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            if heading_stack and heading_stack[-1][1] == title:
                add("repeated_heading", number, "子标题与上一级标题重复。")
            heading_stack.append((level, title))
            last_heading_level = level
            continue

        visible = _editable(raw)
        if "\u3000" in visible:
            add("fullwidth_space", number, "正文中使用半角空格。", "error")
        if re.search(r"[０-９]", visible):
            add("fullwidth_digit", number, "阿拉伯数字使用半角形式。", "error")
        if _PUNCT_SPACE.search(visible):
            add("punctuation_spacing", number, "中文标点与相邻文字之间不留半角空格。", "error")
        if re.search(rf"(?<=[{_CJK}]),(?=[{_CJK}])", visible):
            add("ascii_comma", number, "中文句子中的逗号使用全角形式。", "error")
        if re.search(r"(?:！{2,}|!{2,})", visible):
            add("repeated_exclamation", number, "避免连续使用感叹号。", "error")
        if _MISSING_SPACE.search(visible):
            add("mixed_spacing", number, "检查汉字与英文或数字之间的半角空格。")

        if _LIST.match(raw):
            finish_paragraph()
            paragraphs.append((number, _LIST.sub("", visible, count=1).strip()))
        else:
            if not paragraph_lines:
                paragraph_start = number
            paragraph_lines.append(visible.strip())
    finish_paragraph()

    for number, paragraph in paragraphs:
        if not re.search(rf"[{_CJK}]", paragraph):
            continue
        if len(paragraph) > 250:
            add("long_paragraph", number, "段落超过约 250 字；检查是否包含多个主题。")
        for sentence in re.split(r"[。！？]", paragraph):
            if len(sentence.strip()) > 100:
                add("long_sentence", number, "句子超过约 100 字；检查主语和断句。")
                break
    return {"version": STYLE_VERSION, "applicable": True,
            "issue_count": len(issues), "issues": issues}

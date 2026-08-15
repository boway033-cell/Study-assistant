"""文本清洗：自动处理重复与残缺（OCR/提取产物的后处理）

- 去页眉页脚重复（跨页反复出现的顶部/底部行）
- 相邻行/块去重（OCR 常见整行重复）
- 连续重复字符压缩（如"拉格朗日日日" → "拉格朗日"）
- 断行合并（英文单词被切断、标点残缺）
"""
from __future__ import annotations

import re


def remove_repeated_lines(text: str) -> str:
    """去除相邻重复行。"""
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
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
    """压缩连续重复字符（中文单字重复、标点重复）。如「日日日日」→「日」。"""
    # 中文单字连续重复 >= max_repeat → 压缩为 1
    text = re.sub(r"([\u4e00-\u9fff])\1{%d,}" % (max_repeat - 1), r"\1", text)
    # 标点重复
    text = re.sub(r"([，。；：！？、])\1{2,}", r"\1", text)
    return text


def merge_broken_english(text: str) -> str:
    """合并被换行切断的英文单词（行尾小写字母 + 行首小写字母）。"""
    # 模式：行尾是英文字母/连字符，行首是小写字母 → 去掉中间换行
    text = re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", text)          # 连字符断行
    text = re.sub(r"([a-z])\n([a-z])", r"\1\2", text)               # 无连字符断行
    return text


def merge_broken_chinese(text: str) -> str:
    """中文断行合并：句末标点缺失时，将下一行拼到上一行（处理 OCR 换行）。"""
    # 行尾非标点结尾 + 行首非缩进/标题 → 合并
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        if not line.strip():
            if out:
                out.append("")
            continue
        if out and out[-1] and not _line_ends_closed(out[-1]) and not _looks_like_title(line):
            out[-1] = out[-1] + line.strip()
        else:
            out.append(line)
    return "\n".join(out)


def _line_ends_closed(line: str) -> bool:
    """行是否以句末标点或明确结束符结尾。"""
    return bool(re.search(r"[。；：！？；，、.?!;:)\]]\s*$", line))


def _looks_like_title(line: str) -> bool:
    """是否像标题（短 + 编号开头或全书式标题）。"""
    s = line.strip()
    if len(s) > 30:
        return False
    if re.match(r"^(第[一二三四五六七八九十\d]+[章节篇]|\d+(\.\d+)*\s|[一二三四五六七八九十]+、)", s):
        return True
    return False


def clean_text(text: str, header_lines: set[str] | None = None,
               footer_lines: set[str] | None = None) -> str:
    """综合清洗。header_lines/footer_lines 为版面分析识别出的页眉页脚。"""
    header_lines = header_lines or set()
    footer_lines = footer_lines or set()

    # 按行过滤页眉页脚
    lines = text.split("\n")
    kept = []
    for line in lines:
        s = line.strip()
        if not s:
            kept.append("")
            continue
        if s in header_lines or s in footer_lines:
            continue
        # 纯页码行（数字/罗马数字，短）
        if re.fullmatch(r"[-–—\s]*\d{1,4}[-–—\s]*", s):
            continue
        kept.append(line)

    text = "\n".join(kept)
    text = remove_repeated_lines(text)
    text = collapse_repeated_chars(text)
    text = merge_broken_english(text)
    text = merge_broken_chinese(text)
    return text.strip()

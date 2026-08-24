"""本地目录/标题结构识别。

支持教材和论文常见的中文、阿拉伯数字及英文 section 标题。提取以来源证据为准，
只推断层级，不凭空补写标题；补缺由用户显式触发的深度分析 AI 完成。

中文教材常见层级固定为“章 → 节 → 一、 → （一）”。PDF 文本层经常把字号较大的
标题拆成两行，或在每个汉字之间插入空格，因此识别前会做保守的标题行续接和标记归一化。
"""
from __future__ import annotations

import re


_CN = "一二三四五六七八九十百千万"
_CHAPTER_RE = re.compile(
    rf"^\s*第\s*([{_CN}0-9]+)\s*([章篇编部])\s*(?:[|｜:：、.]\s*)?(.{{0,80}})$"
)
_PART_RE = re.compile(
    rf"^\s*第\s*([{_CN}0-9]+)\s*部\s*分\s*(?:[|｜:：、.．]\s*)?(.{{0,80}})$"
)
_SECTION_RE = re.compile(
    rf"^\s*第\s*([{_CN}0-9]+)\s*节\s*(?:[|｜:：、.]\s*)?(.{{0,80}})$"
)
_CN_LEVEL_RE = re.compile(rf"^\s*([（(]?)([{_CN}]+)([）)]|[、.．])\s*(.{{2,80}})$")
_DECIMAL_RE = re.compile(
    r"^\s*(\d{1,3}(?:[.．]\d{1,3}){1,3})\s*[、.．:：]?\s*"
    r"([\u4e00-\u9fffA-Za-z][^。！？!?；;]{1,79})$"
)
_PAREN_NUM_RE = re.compile(r"^\s*[（(](\d{1,3})[）)]\s*(.{2,80})$")
_PLAIN_NUM_RE = re.compile(r"^\s*(\d{1,2})\s*[、.．:：]?\s*([\u4e00-\u9fff][^。！？!?；;]{1,70})$")
_ENGLISH_RE = re.compile(
    r"^\s*(?:(\d{1,3}(?:\.\d{1,3}){0,2})\s+)?"
    r"(abstract|introduction|background|related\s+work|literature\s+review|"
    r"materials?(?:\s+and\s+methods)?|methods?|results?|discussion|"
    r"conclusions?|limitations?|references|acknowledg(?:e)?ments?|supplementary\s+information)\b"
    r"(?:\s*[:：-]\s*(.{1,70}))?\s*$", re.IGNORECASE,
)
_BODY_END = re.compile(r"[。！？!?；;.]\s*$")
_SENTENCE_PUNCT = re.compile(r"[。！？!?；;]")
_TOC_DOTS = re.compile(r"[.．·…]{4,}\s*\d*\s*$")


def _clean_title(raw: str) -> str:
    t = re.sub(r"\s+", " ", raw.strip())
    t = re.sub(r"\s+[.．·…]{2,}\s*\d+\s*$", "", t)
    t = re.sub(r"\s+\d{1,4}\s*$", "", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    return t.strip(" ，。、|｜:：-—")


def _compact_heading_spacing(raw: str) -> str:
    """只压缩标题候选中的异常汉字间空格，不改正文。"""
    s = re.sub(r"[\t\u3000]+", " ", raw).strip()
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    # “第 三 节”“（ 一 ）”是 PDF 文字层最常见的标题标记拆散形式。
    s = re.sub(rf"第\s*([{_CN}0-9]+)\s*部\s*分", r"第\1部分", s)
    s = re.sub(rf"第\s*([{_CN}0-9]+)\s*([章节篇编部])", r"第\1\2", s)
    s = re.sub(rf"[（(]\s*([{_CN}0-9]+)\s*[）)]", r"（\1）", s)
    # 标记后的标题若几乎逐字带空格，压缩汉字之间的空白。
    if re.match(rf"^(?:第[{_CN}0-9]+[章节篇编部]|[{_CN}]+、|（[{_CN}0-9]+）)", s):
        s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)
    return s


def classify_heading(line: str) -> tuple[str, int] | None:
    """返回规范标题及层级；正文句和目录点线返回 None。"""
    s = _compact_heading_spacing(line)
    if not s or len(s) > 100 or _TOC_DOTS.search(s) or _SENTENCE_PUNCT.search(s):
        return None
    m = _PART_RE.match(s)
    if m:
        suffix = _clean_title(m.group(2))
        return f"第{m.group(1)}部分" + (f" {suffix}" if suffix else ""), 1
    m = _CHAPTER_RE.match(s)
    if m:
        suffix = _clean_title(m.group(3))
        # “第三部 门……”是“第三部门”的 PDF/OCR 断词，不是“第三部”。
        if m.group(2) == "部" and suffix.startswith("门"):
            return None
        return f"第{m.group(1)}{m.group(2)}" + (f" {suffix}" if suffix else ""), 1
    m = _SECTION_RE.match(s)
    if m:
        suffix = _clean_title(m.group(2))
        return f"第{m.group(1)}节" + (f" {suffix}" if suffix else ""), 2
    m = _DECIMAL_RE.match(s)
    if m:
        if re.match(r"^(?:亿|万|千|百|%|％|人次|万人)", m.group(2)):
            return None
        number = m.group(1).replace("．", ".")
        level = min(3, number.count(".") + 1)
        return f"{number} {_clean_title(m.group(2))}", level
    m = _CN_LEVEL_RE.match(s)
    if m:
        level = 4 if m.group(1) else 3
        marker = f"（{m.group(2)}）" if m.group(1) else f"{m.group(2)}、"
        return marker + _clean_title(m.group(4)), level
    m = _PAREN_NUM_RE.match(s)
    if m:
        if re.match(r"^[\d.．－—-]", m.group(2).strip()):
            return None
        return f"（{m.group(1)}）{_clean_title(m.group(2))}", 4
    m = _ENGLISH_RE.match(s)
    if m:
        number, name, suffix = m.groups()
        level = min(3, number.count(".") + 1) if number else 1
        title = ((number + " ") if number else "") + name.title()
        if suffix:
            title += ": " + _clean_title(suffix)
        return title, level
    return None


def _norm_title_key(title: str) -> str:
    return re.sub(r"[\s\u3000，。、|｜.．:：—-]+", "", title.strip()).lower()


def _deduplicate(results: list[dict]) -> list[dict]:
    # 相同标题可能在不同章节重复出现，只去掉同一页的重复来源。
    seen: set[tuple[str, int, int]] = set()
    seen_numbered_roots: set[str] = set()
    out: list[dict] = []
    for item in sorted(results, key=lambda x: (x["page"], x.get("line", 0), x["level"])):
        key = (_norm_title_key(item["title"]), item["level"], item["page"])
        if key in seen:
            continue
        root_key = _norm_title_key(item["title"])
        if item["level"] == 1 and classify_heading(item["title"]):
            if root_key in seen_numbered_roots:
                continue
            seen_numbered_roots.add(root_key)
        seen.add(key)
        out.append({k: v for k, v in item.items() if k != "line"})
    return out


_HEADING_START_RE = re.compile(
    rf"^\s*(?:第\s*[{_CN}0-9]+\s*[章节篇编部]|[{_CN}]+[、.．]|[（(]\s*[{_CN}0-9]+\s*[）)]|\d{{1,3}}(?:[.．]\d{{1,3}})+)"
)
_CONTINUATION_START_RE = re.compile(
    r"^(?:主义|内容|管理|制度|机制|基础|问题|改革|监督|执行|保障|关系|方法|特点|必要性|重要性)"
)


def _join_heading_lines(lines: list[str], idx: int) -> tuple[str, int]:
    """保守续接被 PDF 文字层拆成两行的标题，返回文本和消费行数。"""
    line = lines[idx].strip()
    if idx + 1 >= len(lines) or not _HEADING_START_RE.match(line):
        return line, 1
    hit = classify_heading(line)
    if not hit:
        return line, 1
    nxt = lines[idx + 1].strip()
    if (
        not nxt or len(nxt) > 30 or classify_heading(nxt) or _BODY_END.search(nxt)
        or re.fullmatch(r"\d{1,3}(?:[.．]\d{1,3}){1,3}", nxt)
    ):
        return line, 1
    title = hit[0]
    suffix = re.sub(
        rf"^(?:第[{_CN}0-9]+[章节篇编部]|[{_CN}]+、|（[{_CN}0-9]+）|\d+(?:\.\d+)+)\s*",
        "", title,
    )
    should_join = (
        len(suffix) <= 2
        or bool(re.search(r"[、，：:与和及的对于]$", suffix))
        or bool(_CONTINUATION_START_RE.match(_compact_heading_spacing(nxt)))
    )
    if not should_join:
        return line, 1
    return line.rstrip() + _compact_heading_spacing(nxt), 2


def extract_toc_heuristic(pages: list[str], min_pages: int = 2) -> list[dict]:
    """扫描整页标题候选，允许同页多个层级标题。"""
    results: list[dict] = []
    for pno, text in enumerate(pages, start=1):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        idx = 0
        while idx < len(lines):
            line, consumed = _join_heading_lines(lines, idx)
            hit = classify_heading(line)
            if not hit:
                idx += consumed
                continue
            title, level = hit
            results.append({"title": title, "level": level, "page": pno, "line": idx})
            idx += consumed
    results = _deduplicate(results)
    if len({x["page"] for x in results}) < min_pages or len(results) < 2:
        return []
    return results


def extract_toc_from_layout(layout) -> list[dict]:
    """从字号/坐标标题块提取，并用文本编号纠正层级。"""
    results: list[dict] = []
    if not layout or not layout.pages:
        return results
    for page_blocks in layout.pages:
        for idx, blk in enumerate(page_blocks):
            if blk.block_type != "title":
                continue
            title = _clean_title(blk.text)
            compact = re.sub(r"\s+", "", title)
            if (
                not title or len(title) > 100
                or re.fullmatch(r"[\d*＊·•—_-]+", compact)
                or re.match(r"^[（(]\d+[）)]\s*[\d.．－—-]", title)
                or compact in {"公共管理学报", "作者简介", "内容简介", "版权所有侵权必究印装差错负责调换"}
                or re.fullmatch(r"[\u4e00-\u9fff]{2,4}[，、][\u4e00-\u9fff]{2,4}", compact)
            ):
                continue
            classified = classify_heading(title)
            if classified:
                title, level = classified
            else:
                plain_number = _PLAIN_NUM_RE.match(_compact_heading_spacing(title))
                if plain_number:
                    title = f"{plain_number.group(1)} {_clean_title(plain_number.group(2))}"
                    level = 2
                    results.append({"title": title, "level": level, "page": blk.page, "line": idx})
                    continue
                # 论文首页中夹在大标题附近的短人名不是章节；保留“导言/引言”等正文标题。
                if (
                    blk.page == 1 and len(compact) <= 4
                    and blk.size < layout.body_size * 1.4
                    and compact not in {"导言", "引言", "摘要", "结论", "前言", "绪论"}
                ):
                    continue
                # 字号显著更大的块是章/文献题名；普通加粗短行是其下的小节。
                level = 1 if blk.size >= layout.body_size * 1.55 else 2
            results.append({"title": title, "level": level, "page": blk.page, "line": idx})
    results = _deduplicate(results)
    return results if len(results) >= 2 else []


def merge_toc_sources(*sources: list[dict]) -> list[dict]:
    """合并书签、文本与版面来源，保留更细的可信结构。"""
    merged: list[dict] = []
    for priority, source in enumerate(sources):
        for item in source or []:
            row = dict(item)
            row["source_priority"] = priority
            merged.append(row)
    by_key: dict[tuple[str, int], dict] = {}
    for row in merged:
        key = (_norm_title_key(row["title"]), int(row.get("page") or 1))
        old = by_key.get(key)
        semantic = classify_heading(row["title"])
        if semantic:
            row["title"], row["level"] = semantic
        if old is None or row["source_priority"] < old["source_priority"]:
            by_key[key] = row
        # 编号语义比 PDF 书签缩进更可信，始终纠正“全部挂在目录下”的坏书签。
        if semantic and key in by_key:
            by_key[key]["title"], by_key[key]["level"] = semantic
            if row.get("line") is not None:
                by_key[key]["line"] = row["line"]
    rows = [
        x for x in by_key.values()
        if _norm_title_key(x["title"]) not in {"目录", "contents"}
        and not re.match(r"^[（(]\d+[）)]\s*[\d.．－—-]", x["title"])
    ]
    semantic_pages = {
        int(x.get("page") or 1) for x in rows if classify_heading(x["title"])
    }
    # 当同页已有编号标题证据时，丢弃书签中的无编号孤立文本；这类文本通常是错误的
    # PDF 书签容器、序言，或跨行标题的后半段。
    rows = [
        x for x in rows
        if not (
            x.get("source_priority") == 0
            and int(x.get("page") or 1) in semantic_pages
            and not classify_heading(x["title"])
        )
    ]
    rows = [
        row for row in rows
        if not (
            not classify_heading(row["title"])
            and any(
                int(other.get("page") or 1) == int(row.get("page") or 1)
                and classify_heading(other["title"])
                and _norm_title_key(row["title"]) in _norm_title_key(other["title"])
                for other in rows if other is not row
            )
        )
    ]
    # 同页同层级同时存在“截断标题”和完整标题时，只保留完整证据。
    pruned: list[dict] = []
    for row in rows:
        key = _norm_title_key(row["title"])
        if any(
            int(other.get("page") or 1) == int(row.get("page") or 1)
            and int(other.get("level") or 1) == int(row.get("level") or 1)
            and key != _norm_title_key(other["title"])
            and _norm_title_key(other["title"]).startswith(key)
            for other in rows
        ):
            continue
        pruned.append(row)
    rows = pruned
    # 有可靠“第一章”时，以正文章脊作为目录起点：封面、序言和目录页中的孤立大字
    # 不应与正文各章并列；章内无编号版面标题统一作为二级候选。
    first_chapter_pages = [
        int(row.get("page") or 1) for row in rows
        if re.match(r"^第(?:一|1)章(?:\s|$)", row["title"])
    ]
    if first_chapter_pages:
        body_start = min(first_chapter_pages)
        rows = [row for row in rows if int(row.get("page") or 1) >= body_start]
        for row in rows:
            if not classify_heading(row["title"]):
                row["level"] = max(2, int(row.get("level") or 2))
    from backend.app.services.rag.toc_evidence import score_toc_rows
    rows = score_toc_rows(rows)
    ordered = sorted(rows, key=lambda x: (
        int(x.get("page") or 1),
        0 if (
            int(x.get("page") or 1) == 1
            and int(x.get("level") or 1) == 1
            and x.get("source_priority") == 2
            and not classify_heading(x["title"])
        ) else 1,
        int(x.get("line", 1000000)),
        x["source_priority"], int(x.get("level") or 1)
    ))
    return [
        {"title": x["title"], "level": max(1, min(4, int(x.get("level") or 1))),
         "page": int(x.get("page") or 1)}
        for x in ordered
    ]

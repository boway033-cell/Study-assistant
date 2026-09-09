"""本地目录/标题结构识别。

支持教材和论文常见的中文、阿拉伯数字及英文 section 标题。提取以来源证据为准，
只推断层级，不凭空补写标题；补缺由用户显式触发的深度分析 AI 完成。

中文教材常见层级固定为“章 → 节 → 一、 → （一）”。PDF 文本层经常把字号较大的
标题拆成两行，或在每个汉字之间插入空格，因此识别前会做保守的标题行续接和标记归一化。
"""
from __future__ import annotations

import re
import unicodedata
from statistics import median


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
    r"theoretical\s+framework|conceptual\s+framework|research\s+questions?|"
    r"materials?(?:\s+and\s+methods)?|methods?|methodology|experimental\s+procedures|"
    r"study\s+(?:area|site|design)|participants?|sampling|data\s+and\s+methods|"
    r"results?|findings?|case\s+(?:study|analysis)|discussion|implications?|"
    r"future\s+(?:directions?|research)|conclusions?|limitations?|references|works\s+cited|"
    r"notes?|appendix|appendices|data\s+availability|ethics\s+statement|"
    r"author\s+contributions?|conflicts?\s+of\s+interest|funding|"
    r"acknowledg(?:e)?ments?|supplementary\s+information)\b"
    r"(?:\s*[:：-]\s*(.{1,70}))?\s*$", re.IGNORECASE,
)
_BODY_END = re.compile(r"[。！？!?；;.]\s*$")
_SENTENCE_PUNCT = re.compile(r"[。！？!?；;]")
_TOC_DOTS = re.compile(r"[.．·…]{4,}\s*\d*\s*$")
_AUTHOR_META_RE = re.compile(
    r"(?:作者|基金项目|通讯作者|责任编辑|收稿日期|doi\s*:|e-?mail|@|大学|学院|研究院|研究所)",
    re.IGNORECASE,
)
_PRIVATE_GLYPH_RE = re.compile(r"[\ue000-\uf8ff\ufffd\u25a1\u25a0]")
_MARKER_ONLY_RE = re.compile(rf"^\s*(?:([{_CN}]+)|[（(]([{_CN}0-9]+)[）)])\s*$")
_DOCUMENT_CHROME_RE = re.compile(
    r"^(?:观点|\[?perspectives?\]?|原创论文(?:researcharticles)?|researcharticles|"
    r"实践视野(?:perspectivesfromfield)?|perspectivesfromfield)$", re.I,
)
_BARE_CN_NUMBERED_RE = re.compile(rf"^([{_CN}])(?=[\u4e00-\u9fff])(.{{2,80}})$")


# 无编号学术论文标题需要语义证据，不能只依赖字号。规则刻意只接受短而稳定的
# 章节名称（或冒号后的短副标题），避免把正文里偶然出现的“结果/讨论”升级成目录。
_ACADEMIC_SECTION_RULES: list[tuple[re.Pattern, str, tuple[str, ...]]] = [
    (re.compile(r"^(?:摘要|abstract)$", re.I), "abstract", ("common",)),
    (re.compile(r"^(?:关键词|key\s*words?)$", re.I), "keywords", ("common",)),
    (re.compile(r"^(?:引言|导言|绪论|前言|introduction)$", re.I), "introduction", ("common",)),
    (re.compile(r"^(?:研究背景|问题提出|研究问题|background|research\s+questions?)$", re.I), "question", ("social_science", "natural_biomedical")),
    (re.compile(r"^(?:文献综述|文献回顾|研究述评|相关研究|related\s+work|literature\s+review)$", re.I), "literature", ("social_science", "humanities")),
    (re.compile(r"^(?:理论框架|概念框架|分析框架|theoretical\s+framework|conceptual\s+framework)$", re.I), "theory", ("social_science", "humanities")),
    (re.compile(r"^(?:研究设计|研究方法|方法论|资料与方法|数据与方法|数据来源|methods?|methodology|data\s+and\s+methods|study\s+design|participants?|sampling)$", re.I), "methods", ("social_science", "natural_biomedical")),
    (re.compile(r"^(?:田野调查|访谈设计|编码与分析)$", re.I), "methods", ("social_science",)),
    (re.compile(r"^(?:材料与方法|实验方法|实验设计|实验过程|研究区域|样本采集|materials?(?:\s+and\s+methods)?|experimental\s+procedures|study\s+(?:area|site))$", re.I), "methods", ("natural_biomedical",)),
    (re.compile(r"^(?:研究发现|实证结果|研究结果|结果|findings?|results?)$", re.I), "results", ("social_science", "natural_biomedical")),
    (re.compile(r"^(?:个案研究|案例研究|案例分析|资料分析|实证分析|case\s+(?:study|analysis))$", re.I), "analysis", ("social_science",)),
    (re.compile(r"^(?:文本分析|作品分析|叙事结构|叙事分析|修辞分析|人物形象|主题分析|意象分析|细读|close\s+reading|textual\s+analysis|narrative\s+analysis)$", re.I), "interpretation", ("humanities",)),
    (re.compile(r"^(?:历史语境|文化语境|时代背景|理论视角|互文性|接受史|historical\s+context|cultural\s+context|intertextuality|reception\s+history)$", re.I), "context", ("humanities",)),
    (re.compile(r"^(?:讨论|综合讨论|discussion)$", re.I), "discussion", ("common",)),
    (re.compile(r"^(?:结论|结语|结论与讨论|讨论与结论|余论|conclusions?|discussion\s+and\s+conclusions?)$", re.I), "conclusion", ("common",)),
    (re.compile(r"^(?:研究启示|理论启示|实践启示|政策启示|implications?)$", re.I), "implications", ("social_science", "natural_biomedical")),
    (re.compile(r"^(?:研究局限|局限与展望|不足与展望|未来研究|limitations?|future\s+(?:directions?|research))$", re.I), "limitations", ("common",)),
    (re.compile(r"^(?:参考文献|引用文献|works\s+cited|references)$", re.I), "references", ("common",)),
    (re.compile(r"^(?:注释|尾注|notes?)$", re.I), "notes", ("humanities",)),
    (re.compile(r"^(?:附录|appendix|appendices)$", re.I), "appendix", ("common",)),
    (re.compile(r"^(?:数据可得性|数据可用性声明|伦理声明|作者贡献|利益冲突|经费资助|data\s+availability|ethics\s+statement|author\s+contributions?|conflicts?\s+of\s+interest|funding)$", re.I), "research_integrity", ("natural_biomedical",)),
]

_ROLE_LABELS = {
    "abstract": "摘要", "keywords": "关键词", "introduction": "引言", "question": "研究问题",
    "literature": "文献综述", "theory": "理论框架", "methods": "研究方法", "results": "研究结果",
    "analysis": "案例/资料分析", "interpretation": "文本细读", "context": "语境/理论视角",
    "discussion": "讨论", "conclusion": "结论", "implications": "研究启示", "limitations": "局限",
    "references": "参考文献", "notes": "注释", "appendix": "附录", "research_integrity": "研究透明度声明",
}


def academic_heading_info(title: str) -> dict | None:
    """识别跨学科论文中的标准章节角色；只返回证据，不生成新标题。"""
    text = _clean_title(str(title or ""))
    text = re.sub(r"^\d{1,3}(?:[.．]\d{1,3}){0,3}\s+", "", text).strip()
    # 允许“方法：样本与变量”一类短副标题；角色只由冒号前的稳定名称决定。
    head = re.split(r"\s*[:：—-]\s*", text, maxsplit=1)[0].strip()
    for pattern, role, disciplines in _ACADEMIC_SECTION_RULES:
        if pattern.fullmatch(head):
            return {"role": role, "role_label": _ROLE_LABELS[role], "disciplines": list(disciplines)}
    return None


def _looks_like_author_or_metadata(title: str) -> bool:
    compact = re.sub(r"\s+", "", title)
    if _AUTHOR_META_RE.search(compact):
        return True
    # 姓名串常被版面模型误判为大标题；要求短、无章节语义且由 2-4 字姓名分隔组成。
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,4}(?:[，,、·]\s*[\u4e00-\u9fff]{2,4}){1,8}", title.strip()))


def _clean_title(raw: str) -> str:
    # NFKC repairs full-width Latin/digits from CJK journal PDFs. Private-use
    # glyphs are font artefacts (often rendered as empty squares), never titles.
    t = unicodedata.normalize("NFKC", str(raw or ""))
    t = _PRIVATE_GLYPH_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t.strip())
    t = re.sub(r"\s+[.．·…]{2,}\s*\d+\s*$", "", t)
    t = re.sub(r"\s+\d{1,4}\s*$", "", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    return t.strip(" ，。、|｜:：-—")


def _compact_heading_spacing(raw: str) -> str:
    """只压缩标题候选中的异常汉字间空格，不改正文。"""
    s = _clean_title(raw)
    s = re.sub(r"[\t\u3000]+", " ", s).strip()
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
    # 必须在清洗之前判断：清洗会删掉点线和印刷页码，丢失目录页负证据。
    if _TOC_DOTS.search(str(line)) or _SENTENCE_PUNCT.search(str(line)):
        return None
    if len(line) > 22 and re.search(r'[，,]', line):
        return None
    s = _compact_heading_spacing(line)
    if (
        not s or len(s) > 100 or _TOC_DOTS.search(s) or _SENTENCE_PUNCT.search(s)
        or (len(s) > 24 and len(re.findall(r"[，,]", s)) >= 3)
    ):
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
    if academic_heading_info(s):
        return _clean_title(s), 1
    return None


def _norm_title_key(title: str) -> str:
    return re.sub(r"[\s\u3000，。、|｜.．:：—-]+", "", title.strip()).lower()


def _deduplicate(results: list[dict]) -> list[dict]:
    # 相同标题可能在不同章节重复出现，只去掉同一页的重复来源。
    seen: set[tuple[str, int, int]] = set()
    out: list[dict] = []
    for item in sorted(results, key=lambda x: (x["page"], x.get("line", 0), x["level"])):
        key = (_norm_title_key(item["title"]), item["level"], item["page"])
        if key in seen:
            continue
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


def contents_page_numbers(pages: list[str]) -> set[int]:
    """识别总目录和分篇目录；印刷页码不是 PDF 物理页定位证据。"""
    found = set()
    for pno, text in enumerate(pages, 1):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        header = any(re.fullmatch(r"(?:目\s*录|总目录|详细目录|contents|table\s+of\s+contents)",
                                  line, re.I) for line in lines[:5])
        entries = sum(bool(re.search(r"\D.{1,90}(?:[.．·…]{2,}\s*|\s+)\d{1,4}\s*$", line))
                      for line in lines)
        headings = sum(bool(_HEADING_START_RE.match(line)) for line in lines)
        separate_numbers = sum(bool(re.fullmatch(r'\d{1,4}', line)) for line in lines)
        # 延续页可能没有“目录”字样；需要多数行呈条目形态，避免误伤正文列表。
        if ((header and (entries >= 2 or headings >= 2))
                or (entries >= 4 and entries >= len(lines) * .45)
                or (headings >= 3 and separate_numbers >= 3
                    and headings + separate_numbers >= len(lines) * .45
                    and sum(bool(_SENTENCE_PUNCT.search(line)) for line in lines) <= 2)):
            found.add(pno)
    return found


def extract_toc_heuristic(pages: list[str], min_pages: int = 2) -> list[dict]:
    """扫描整页标题候选，允许同页多个层级标题。"""
    results: list[dict] = []
    contents_pages = contents_page_numbers(pages)
    for pno, text in enumerate(pages, start=1):
        if pno in contents_pages:
            continue
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
        if contents_page_numbers(["\n".join(blk.text for blk in page_blocks)]):
            continue
        widths = [blk.bbox[2] - blk.bbox[0] for blk in page_blocks
                  if hasattr(blk, 'bbox') and len(blk.text) >= 22 and blk.bbox[2] > blk.bbox[0]]
        body_width = median(widths) if widths else float('inf')
        for idx, blk in enumerate(page_blocks):
            is_ocr = getattr(blk, 'source', '') == 'ocr'
            above = blk.bbox[1] - page_blocks[idx - 1].bbox[3] if is_ocr and idx else 0
            below = page_blocks[idx + 1].bbox[1] - blk.bbox[3] if is_ocr and idx + 1 < len(page_blocks) else 0
            separated = above > layout.body_size * .7 and below > layout.body_size * .3
            if blk.block_type != "title" and not (is_ocr and blk.block_type == 'body' and separated):
                continue
            if _TOC_DOTS.search(blk.text):
                continue
            title = _clean_title(blk.text)
            compact = re.sub(r"\s+", "", title)
            if (
                not title or len(title) > 100
                or re.fullmatch(r"[\d*＊·•—_-]+", compact)
                or re.match(r"^[（(]\d+[）)]\s*[\d.．－—-]", title)
                or compact in {"公共管理学报", "作者简介", "内容简介", "版权所有侵权必究印装差错负责调换"}
                or re.fullmatch(r"[\u4e00-\u9fff]{2,4}[，、][\u4e00-\u9fff]{2,4}", compact)
                or (blk.page <= 2 and _looks_like_author_or_metadata(title))
            ):
                continue
            classified = classify_heading(title)
            if is_ocr and not classified and (
                len(title) < 2 or _SENTENCE_PUNCT.search(blk.text)
                or re.search(r'[，,]', blk.text)
                or (blk.bbox[2] - blk.bbox[0] >= body_width * .92 and blk.size < layout.body_size * 1.35)
                or (blk.size < layout.body_size * 1.3 and not separated)
            ):
                continue
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


def _marker_prefix(title: str) -> str | None:
    match = _MARKER_ONLY_RE.fullmatch(_clean_title(title))
    if not match:
        return None
    return f"{match.group(1)}、" if match.group(1) else f"（{match.group(2)}）"


def _repair_fragmented_rows(rows: list[dict]) -> list[dict]:
    """Rejoin layout blocks that visually form one heading.

    Two-column Chinese journals frequently expose the number and title as
    separate blocks. Some layouts return ``title, (一), next title`` while
    others return ``二, title``; both are handled without inventing text.
    """
    ordered = sorted((dict(row) for row in rows), key=lambda row: (
        int(row.get("page") or 1), int(row.get("line", 1_000_000)),
    ))
    removed: set[int] = set()
    for index, row in enumerate(ordered):
        prefix = _marker_prefix(str(row.get("title") or ""))
        if not prefix:
            continue
        page = int(row.get("page") or 1)
        previous = next((i for i in range(index - 1, -1, -1)
                         if i not in removed and int(ordered[i].get("page") or 1) == page), None)
        following = next((i for i in range(index + 1, len(ordered))
                          if i not in removed and int(ordered[i].get("page") or 1) == page), None)

        def eligible(candidate_index: int | None) -> bool:
            if candidate_index is None:
                return False
            title = _clean_title(str(ordered[candidate_index].get("title") or ""))
            return bool(2 <= len(title) <= 80 and not _marker_prefix(title)
                        and not classify_heading(title) and not _DOCUMENT_CHROME_RE.fullmatch(_norm_title_key(title)))

        target = None
        # A trailing parenthesized marker in alternating title/marker layouts
        # belongs to the preceding title. A bare section numeral after an
        # Introduction belongs to the following block.
        if prefix.startswith("（") and eligible(previous):
            target = previous
        elif eligible(following):
            target = following
        elif eligible(previous):
            target = previous
        elif following is None and previous is not None and academic_heading_info(
            str(ordered[previous].get("title") or "")
        ):
            target = previous
        if target is None:
            continue
        title = prefix + _clean_title(str(ordered[target].get("title") or ""))
        ordered[target]["title"] = title
        semantic = classify_heading(title)
        if semantic:
            ordered[target]["title"], ordered[target]["level"] = semantic
        removed.add(index)
    return [row for index, row in enumerate(ordered) if index not in removed]


def _prune_layout_noise(rows: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for row in rows:
        title = _clean_title(str(row.get("title") or ""))
        if not title:
            continue
        compact = _norm_title_key(title)
        if int(row.get("page") or 1) == 1 and _DOCUMENT_CHROME_RE.fullmatch(compact):
            continue
        item = dict(row); item["title"] = title; cleaned.append(item)

    bare_numbered = [row for row in cleaned if _BARE_CN_NUMBERED_RE.fullmatch(row["title"])]
    if len(bare_numbered) >= 3:
        for row in bare_numbered:
            match = _BARE_CN_NUMBERED_RE.fullmatch(row["title"])
            row["title"] = f"{match.group(1)}、{match.group(2)}"
            semantic = classify_heading(row["title"])
            if semantic:
                row["title"], row["level"] = semantic

    # Merge an article title and subtitle only when layout evidence places two
    # large non-semantic roots next to each other on the first page.
    front = [index for index, row in enumerate(cleaned)
             if int(row.get("page") or 1) == 1 and int(row.get("level") or 1) == 1
             and int(row.get("source_priority", 1)) == 2
             and not classify_heading(row["title"])]
    if len(front) >= 2:
        first, second = front[:2]
        first_line = int(cleaned[first].get("line", 1_000_000))
        second_line = int(cleaned[second].get("line", 1_000_000))
        if second_line - first_line <= 2 and len(cleaned[first]["title"]) + len(cleaned[second]["title"]) <= 100:
            cleaned[first]["title"] = cleaned[first]["title"].rstrip("—- ") + "——" + cleaned[second]["title"].lstrip("—- ")
            cleaned.pop(second)

    # Once a first-page article title root exists, standard paper section roles
    # are children of the article rather than additional document roots.
    has_article_root = any(
        int(row.get("page") or 1) == 1 and int(row.get("level") or 1) == 1
        and not classify_heading(row["title"]) for row in cleaned
    )
    if has_article_root:
        for row in cleaned:
            if academic_heading_info(row["title"]):
                row["level"] = max(2, int(row.get("level") or 1))

    # Mirrored English titles in Chinese journals are often emitted one word
    # per layout block on the final pages. Remove only non-semantic ASCII runs;
    # genuine Abstract/Methods/Results headings remain.
    ascii_by_page: dict[int, list[int]] = {}
    for index, row in enumerate(cleaned):
        title = row["title"]
        if re.fullmatch(r"[A-Za-z][A-Za-z'’\- ]{0,28}", title) and not academic_heading_info(title):
            ascii_by_page.setdefault(int(row.get("page") or 1), []).append(index)
    noisy = {index for indexes in ascii_by_page.values() if len(indexes) >= 4 for index in indexes}
    cleaned = [row for index, row in enumerate(cleaned) if index not in noisy]

    # Chinese journals may append a second English title/abstract after the
    # references. It is metadata, not a second chapter tree.
    reference_pages = [int(row.get("page") or 1) for row in cleaned
                       if (academic_heading_info(row["title"]) or {}).get("role") == "references"]
    if reference_pages:
        reference_page = min(reference_pages)
        trailing = [row for row in cleaned if int(row.get("page") or 1) >= reference_page
                    and not re.search(r"[\u4e00-\u9fff]", row["title"])
                    and ((academic_heading_info(row["title"]) or {}).get("role") in {"abstract", "keywords", "methods"}
                         or bool(re.search(r"[A-Za-z]{2}", row["title"])))]
        if len(trailing) >= 2:
            trailing_ids = {id(row) for row in trailing}
            cleaned = [row for row in cleaned if id(row) not in trailing_ids]
    elif sum(bool(re.search(r"[\u4e00-\u9fff]", row["title"])) for row in cleaned) >= 3:
        max_page = max((int(row.get("page") or 1) for row in cleaned), default=1)
        cleaned = [row for row in cleaned if not (
            int(row.get("page") or 1) == max_page
            and not re.search(r"[\u4e00-\u9fff]", row["title"])
            and not academic_heading_info(row["title"])
        )]
    return cleaned


def merge_toc_sources(*sources: list[dict]) -> list[dict]:
    """合并书签、文本与版面来源，保留更细的可信结构。"""
    merged: list[dict] = []
    for priority, source in enumerate(sources):
        for item in source or []:
            row = dict(item)
            row["source_priority"] = priority
            merged.append(row)
    merged = _prune_layout_noise(_repair_fragmented_rows(merged))
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
    # Native bookmarks and layout candidates may de-duplicate in a way that
    # exposes a lagging marker only after source fusion. Re-run the same
    # evidence-preserving join on the surviving rows.
    rows = _repair_fragmented_rows(rows)
    has_article_root = any(
        int(row.get("page") or 1) == 1 and int(row.get("level") or 1) == 1
        and not classify_heading(row["title"]) for row in rows
    )
    if has_article_root:
        for row in rows:
            if academic_heading_info(row["title"]):
                row["level"] = max(2, int(row.get("level") or 1))
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
    from backend.app.services.rag.toc_logic import auto_repair_toc_rows
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
    normalized = [
        {"title": x["title"], "level": max(1, min(4, int(x.get("level") or 1))),
         "page": int(x.get("page") or 1), "confidence": x.get("confidence")}
        for x in ordered
    ]
    repaired, _audit = auto_repair_toc_rows(normalized)
    return repaired


_ACADEMIC_CORE_GROUPS = {
    "social_science": [
        (("introduction", "question"), "问题与背景"),
        (("literature", "theory"), "文献或理论框架"),
        (("methods",), "研究方法"),
        (("results", "analysis"), "结果或分析"),
        (("discussion", "conclusion"), "讨论或结论"),
        (("references",), "参考文献"),
    ],
    "humanities": [
        (("introduction", "question"), "引言或问题"),
        (("literature", "theory", "context"), "学术对话或语境"),
        (("interpretation", "analysis"), "文本细读或分析"),
        (("discussion", "conclusion"), "结论或余论"),
        (("references", "notes"), "参考文献或注释"),
    ],
    "natural_biomedical": [
        (("introduction", "question"), "引言或研究问题"),
        (("methods",), "材料与方法"),
        (("results",), "结果"),
        (("discussion", "conclusion"), "讨论或结论"),
        (("references",), "参考文献"),
    ],
    "interdisciplinary": [
        (("introduction", "question"), "引言或问题"),
        (("methods", "interpretation", "analysis"), "方法或分析"),
        (("results", "interpretation", "analysis"), "结果或阐释"),
        (("discussion", "conclusion"), "讨论或结论"),
        (("references", "notes"), "参考文献或注释"),
    ],
}

_ACADEMIC_PROFILE_LABELS = {
    "social_science": "社会科学论文结构",
    "humanities": "人文/文学论文结构",
    "natural_biomedical": "自然科学/生物医学论文结构",
    "interdisciplinary": "跨学科学术结构",
}


def analyze_academic_structure(rows: list[dict]) -> dict:
    """对已识别目录做覆盖审计；只报告“未识别”，绝不自动补写章节。"""
    roles: list[str] = []
    discipline_scores = {"social_science": 0, "humanities": 0, "natural_biomedical": 0}
    items: list[dict] = []
    for index, row in enumerate(rows):
        info = academic_heading_info(str(row.get("title") or ""))
        if not info:
            continue
        roles.append(info["role"])
        for discipline in info["disciplines"]:
            if discipline in discipline_scores:
                discipline_scores[discipline] += 1
        items.append({"index": index, "title": row.get("title"), "role": info["role"],
                      "role_label": info["role_label"]})
    unique_roles = list(dict.fromkeys(roles))
    classified = len(unique_roles) >= 3
    if not classified:
        return {"classified": False, "profile": None, "profile_label": None,
                "detected_roles": unique_roles, "detected_role_labels": [_ROLE_LABELS[x] for x in unique_roles],
                "discipline_scores": discipline_scores, "missing_core_roles": [], "coverage": None, "items": items}
    ranked = sorted(discipline_scores.items(), key=lambda item: item[1], reverse=True)
    profile = ranked[0][0] if ranked[0][1] >= 2 and ranked[0][1] > ranked[1][1] else "interdisciplinary"
    groups = _ACADEMIC_CORE_GROUPS[profile]
    present = set(unique_roles)
    missing = [label for choices, label in groups if not present.intersection(choices)]
    coverage = round((len(groups) - len(missing)) / max(1, len(groups)), 3)
    return {"classified": True, "profile": profile, "profile_label": _ACADEMIC_PROFILE_LABELS[profile],
            "detected_roles": unique_roles, "detected_role_labels": [_ROLE_LABELS[x] for x in unique_roles],
            "discipline_scores": discipline_scores, "missing_core_roles": missing,
            "coverage": coverage, "items": items,
            "review_note": "缺项表示当前目录尚未识别到对应结构，请对照原页核验；系统不会自动生成标题。"}

"""深度分析管线（需求：标题目录提取→核对→AI补全→逐章总结→Markdown）

流程：
  1. extract_titles_3level  本地启发式提取 大/中/小 三级标题（章/节/小节）
  2. verify_toc             核对编号连续性，找出缺失项
  3. complete_with_ai       把缺失项告诉 AI → AI 针对性补全 → 合并
  4. summarize_by_toc       AI 按完整目录逐章详细总结
  5. to_markdown            生成 Markdown（目录 + AI 总结 + 正文）

原则：本地启发式优先（零成本），AI 只补缺口；未配置 Key 时降级为纯本地目录。
"""
from __future__ import annotations

import json
import re

# ---------- 1. 三级标题本地启发式提取 ----------
# 大标题：第X章 / 第X篇 / 第X编
_L1_RE = re.compile(
    r"^\s*第\s*[一二三四五六七八九十百千万0-9]+\s*[章篇编部]\s*"
    r"(?:[|｜:：]\s*)?([^|\n]{2,40})"
)
# 中标题：第X节 或 数字.数字 编号
_L2_RE = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百千万0-9]+\s*节\s*|(\d{1,2})\.(\d{1,2})\s*)([^|\n]{2,40})"
)
# 小标题：数字.数字.数字 编号
_L3_RE = re.compile(
    r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{1,2})\s*([^|\n]{2,40})"
)
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn2int(s: str) -> int:
    s = s.strip()
    if s.isdigit():
        return int(s)
    total = 0
    if "十" in s:
        a, _, b = s.partition("十")
        total = (_cn2int(a) if a else 1) * 10 + (_cn2int(b) if b else 0)
        return total
    return sum(_CN_NUM.get(ch, 0) for ch in s)


def _clean(t: str) -> str:
    t = t.strip()
    # 去尾部「页码.」或「 5.」等
    t = re.sub(r"[\s\u3000]*\d+[.．、]?\s*$", "", t)
    # 去尾部纯数字
    t = re.sub(r"[\s\u3000]*\d+$", "", t)
    # 去尾部断行残字（厂 等）与括号尾巴
    t = re.sub(r"[厂][\s]*$", "", t)
    t = re.sub(r"[（(][^）)]*[)）]\s*$", "", t)
    # 去字距空格
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    return t.strip(" ，。、|｜.．")


_CHAPTER_NUM_RE = re.compile(r"第([一二三四五六七八九十百千万0-9]+)[章篇编]")


def _chapter_num(title: str) -> int | None:
    m = _CHAPTER_NUM_RE.search(title)
    if m:
        try:
            return _cn2int(m.group(1))
        except Exception:
            return None
    return None


def _dedup_toc(toc: list[dict]) -> list[dict]:
    """归一化去重：标题先清洗再比较；重复时保留更干净的一条。"""
    kept: dict[str, dict] = {}
    for t in sorted(toc, key=lambda x: (x["page"], x["level"])):
        clean_title = _clean(t["title"])
        if not clean_title:
            continue
        t2 = dict(t, title=clean_title)
        key = _norm_key(clean_title, t2["level"])
        if key not in kept:
            kept[key] = t2
        else:
            # 保留清洗后更短（更干净）的一条
            if len(clean_title) < len(kept[key]["title"]):
                kept[key] = t2
    return list(kept.values())

def _order_toc(toc: list[dict]) -> list[dict]:
    """按层级重排：章按编号升序，节/小节挂在对应章之后。"""
    chapters = sorted([t for t in toc if t["level"] == 1],
                      key=lambda x: (_chapter_num(x["title"]) or 999, x["page"]))
    numbered = [t for t in toc if t["level"] != 1 and re.match(r"\d{1,2}", t["title"])]
    orphan = [t for t in toc if t["level"] != 1 and t not in numbered]

    def ch_of(t: dict) -> int:
        m = re.match(r"(\d{1,2})", t["title"])
        return int(m.group(1)) if m else -1

    # 每个孤儿条目归属"开始页 <= 其页码"的最后一个章（即包含该页的章）
    orphan_by_ch: dict[int, list[dict]] = {}
    for t in orphan:
        owner = None
        for ch in chapters:
            if ch["page"] <= t["page"]:
                owner = ch
            else:
                break
        orphan_by_ch.setdefault(owner["title"] if owner else -1, []).append(t)

    out: list[dict] = []
    for ch in chapters:
        out.append(ch)
        n = _chapter_num(ch["title"]) or -1
        for t in sorted(numbered, key=lambda x: x["page"]):
            if ch_of(t) == n:
                out.append(t)
        for t in sorted(orphan_by_ch.get(ch["title"], []), key=lambda x: x["page"]):
            out.append(t)
    # 无法归属的放最后
    for t in orphan_by_ch.get(-1, []):
        out.append(t)
    return out


def _norm_key(title: str, level: int) -> str:
    """归一化标题用于去重（忽略空白/标点差异）。"""
    t = _clean(title)
    t = re.sub(r"[\s\u3000，。、|｜.．:：—-]+", "", t)
    return f"{level}|{t}"


def extract_titles_3level(pages: list[str], min_items: int = 2) -> list[dict]:
    """扫描全文标题而非只看页首，允许同一页出现多个分标题。"""
    from backend.app.services.rag.toc_heuristic import extract_toc_heuristic
    return extract_toc_heuristic(pages, min_pages=1) if len(pages) else []


# ---------- 2. 核对完整性 ----------
def verify_toc(toc: list[dict]) -> dict:
    """检查编号连续性。返回 {ok, issues:[{type,ref,level}], chapters, sections}。"""
    issues: list[dict] = []
    chapters = [t for t in toc if t["level"] == 1]
    sections = [t for t in toc if t["level"] == 2]
    subs = [t for t in toc if t["level"] == 3]

    # 章编号：第N章 连续性
    ch_nums = []
    for t in chapters:
        m = re.match(r"第([一二三四五六七八九十百千万0-9]+)章", t["title"])
        if m:
            ch_nums.append(_cn2int(m.group(1)))
    if ch_nums:
        for i in range(1, max(ch_nums) + 1):
            if i not in ch_nums:
                issues.append({"type": "missing_chapter", "ref": f"第{i}章", "level": 1})

    # 节编号：每章内同时支持“第一节”和 X.Y；不能只检查阿拉伯小数编号。
    sec_nums: dict[int, set[int]] = {}
    for t in sections:
        m = re.match(r"(\d{1,2})\.(\d{1,2})", t["title"])
        if m:
            ch, sec = int(m.group(1)), int(m.group(2))
            sec_nums.setdefault(ch, set()).add(sec)
    for ch, secs in sec_nums.items():
        for i in range(1, max(secs) + 1):
            if i not in secs:
                issues.append({"type": "missing_section", "ref": f"{ch}.{i}", "level": 2})

    current_chapter = "全书"
    numbered_sections: list[int] = []
    for item in toc + [{"title": "__END__", "level": 1}]:
        if item["level"] == 1:
            if numbered_sections:
                for i in range(1, max(numbered_sections) + 1):
                    if i not in numbered_sections:
                        issues.append({
                            "type": "missing_section",
                            "ref": f"{current_chapter}缺少第{i}节",
                            "level": 2,
                        })
            current_chapter = item["title"]
            numbered_sections = []
            continue
        if item["level"] == 2:
            m = re.match(r"第([一二三四五六七八九十百千万0-9]+)节", item["title"])
            if m:
                numbered_sections.append(_cn2int(m.group(1)))

    # 小节编号连续性（简化：按 章.节 聚合）
    sub_nums: dict[tuple[int, int], set[int]] = {}
    for t in subs:
        m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{1,2})", t["title"])
        if m:
            key = (int(m.group(1)), int(m.group(2)))
            sub_nums.setdefault(key, set()).add(int(m.group(3)))
    for key, ss in sub_nums.items():
        for i in range(1, max(ss) + 1):
            if i not in ss:
                issues.append({"type": "missing_subsection", "ref": f"{key[0]}.{key[1]}.{i}", "level": 3})

    # 中文序号连续性：分别在最近的上级标题范围内检查“一、二、三”和“（一）（二）”。
    for target_level, marker_re in (
        (3, re.compile(r"^([一二三四五六七八九十]+)、")),
        (4, re.compile(r"^（([一二三四五六七八九十]+)）")),
    ):
        groups: dict[str, list[int]] = {}
        parents: list[str] = []
        for item in toc:
            if item["level"] < target_level:
                parents = parents[:item["level"] - 1] + [item["title"]]
            if item["level"] != target_level:
                continue
            match = marker_re.match(item["title"])
            if match:
                parent_key = " / ".join(parents)
                groups.setdefault(parent_key, []).append(_cn2int(match.group(1)))
        for parent, nums in groups.items():
            if not nums:
                continue
            for number in range(1, max(nums) + 1):
                if number not in nums:
                    issues.append({
                        "type": "missing_chinese_sequence",
                        "ref": f"{parent} 下缺少第 {number} 个 {target_level} 级标题",
                        "level": target_level,
                    })

    # 结构质量告警：不是凭空补标题，而是提示 AI 对原文标题候选做一次完整复核。
    if chapters and not sections:
        issues.append({"type": "flat_structure", "ref": "仅识别到一级标题，请复核“一、/（一）/1.1”等分标题", "level": 2})
    if len(toc) == 1:
        issues.append({"type": "sparse_structure", "ref": "目录过少，请从全文标题候选中复核", "level": 2})

    from backend.app.services.rag.toc_logic import analyze_toc_rows
    logic = analyze_toc_rows(toc)
    existing_keys = {(issue["type"], issue.get("ref") or issue.get("message")) for issue in issues}
    for issue in logic["issues"]:
        key = (issue["type"], issue.get("message"))
        if key not in existing_keys:
            issues.append(issue)
            existing_keys.add(key)

    return {
        "ok": len(issues) == 0,
        "issues": issues[:30],
        "chapters": len(chapters),
        "sections": len(sections),
        "subsections": len(subs),
        "logic_summary": logic["summary"],
    }


# ---------- 3. AI 补全缺失标题 ----------
async def complete_with_ai(provider, toc: list[dict], issues: list[dict], pages: list[str],
                           max_items: int = 60) -> list[dict]:
    """让 AI 审核本地结构；代码层只接受可回查候选 ID，不信任提示词自律。"""
    if not issues:
        return toc
    import json
    from backend.app.services.rag.toc_heuristic import classify_heading
    from backend.app.services.rag.toc_evidence import build_review_packet, validate_ai_review

    candidates: list[dict] = []
    page_excerpts: dict[int, str] = {}
    seen: set[tuple[str, int]] = set()
    for pno, page in enumerate(pages, start=1):
        lines = [ln.strip() for ln in page.splitlines() if ln.strip()]
        page_excerpts[pno] = "\n".join(lines[:20])[:600]
        for line in lines:
            heading = classify_heading(line)
            if heading is None:
                continue
            title, level = heading
            key = (_norm_key(title, level), pno)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "title": title,
                "level": level,
                "page": pno,
                "source_priority": 1,
            })
            if len(candidates) >= max_items * 4:
                break
        if len(candidates) >= max_items * 4:
            break
    if not candidates:
        return toc
    packet = build_review_packet(candidates, issues, page_excerpts)

    prompt = [
        {"role": "system", "content": (
            "你是学术文献结构审校助手。只能选择给定 candidate_id；标题必须原样复制，"
            "只能调整 1-4 级层级，不能新增、改写或推断标题。"
            "只输出需要采用或纠正的 JSON 数组："
            "[{\"candidate_id\":0,\"title\":\"候选原文\",\"level\":1}]；找不到就输出 []。"
        )},
        {"role": "user", "content": (
            "候选证据包：\n" + json.dumps(packet, ensure_ascii=False)
        )},
    ]
    answer = ""
    try:
        async for delta in provider.stream_chat(prompt):
            answer += delta
        from backend.app.services.llm import parse_json_response
        data = parse_json_response(answer)
    except Exception:  # noqa: BLE001
        return toc

    accepted = validate_ai_review(candidates, data if isinstance(data, list) else [])
    merged = [dict(item) for item in toc]
    for item in accepted:
        replacement = {key: item[key] for key in ("title", "level", "page")}
        existing = next((entry for entry in merged if (
            _clean(entry["title"]) == _clean(item["title"])
            and int(entry.get("page") or 1) == item["page"]
        )), None)
        if existing is not None:
            existing.update(replacement)
        else:
            merged.append(replacement)
    return _order_toc(_dedup_toc(merged))


# ---------- 4. 按目录逐章 AI 详细总结 ----------
async def summarize_by_toc(provider, book_title: str, toc: list[dict],
                           chapter_texts: dict[int, str], max_chars: int = 12000,
                           on_progress=None) -> list[dict]:
    """对每个一级章节生成详细总结（带进度回调 + 单次重试）。chapter_texts: {章序号: 全文}。

    Fix: match chapters by title not just sequential index to avoid content mismatch.
    """
    chapters = [t for t in toc if t["level"] == 1]
    total = len(chapters)
    out: list[dict] = []
    for i, ch in enumerate(chapters, start=1):
        if on_progress:
            on_progress(i, total, ch["title"])
        text = chapter_texts.get(i, "")
        if not text:
            out.append({"title": ch["title"], "summary": "（该章无正文内容）"})
            continue
        prompt = [
            {"role": "system", "content": (
                "你是复习精读助手。根据教材章节原文，生成详细总结："
                "1) 本节核心主题；2) 关键概念/定义/公式（逐个列出）；3) 主要论点与逻辑；"
                "4) 可能的考点。用中文 Markdown 格式，600-900 字，不要遗漏重要内容。"
            )},
            {"role": "user", "content": f"《{book_title}》{ch['title']}\n\n{text[:max_chars]}"},
        ]
        answer = ""
        ok = False
        for attempt in range(2):  # 失败重试一次（限流/网络抖动）
            try:
                answer = ""
                async for delta in provider.stream_chat(prompt):
                    answer += delta
                if answer.strip():
                    ok = True
                    break
            except Exception:  # noqa: BLE001
                answer = ""
        out.append({"title": ch["title"], "summary": answer.strip() if ok else "（AI 总结失败）"})
    return out


async def build_paper_card(provider, book_title: str, toc: list[dict], chunks,
                           max_chars: int = 26000) -> str:
    """生成固定 01-16 节的来源约束阅读卡。

    每个 source ID 都映射到数据库 chunk 和 PDF 页码；来源不足必须明确标记，
    不允许把模型推断伪装成作者结论。
    """
    sources: list[str] = []
    used = 0
    for chunk in chunks:
        locator = (
            f"PDF第{chunk.page_start}-{chunk.page_end}页"
            if chunk.page_start and chunk.page_end and chunk.page_end != chunk.page_start
            else f"PDF第{chunk.page_start}页" if chunk.page_start else "结构定位"
        )
        block = f"[B{chunk.book_id}-C{chunk.id}|{locator}]\n{chunk.content.strip()}\n"
        if used + len(block) > max_chars:
            break
        sources.append(block)
        used += len(block)
    toc_text = "\n".join(f"{t['level']}级 PDF第{t['page']}页 {t['title']}" for t in toc[:100])
    prompt = [
        {"role": "system", "content": (
            "你是科研文献精读助手。请基于给定原文生成中文 Markdown 阅读卡，固定包含并按顺序使用"
            "“## 01”至“## 16”十六个章节：书目信息、研究定位、研究问题、背景路线、"
            "现有痛点、核心洞见、方法总览、模块逻辑、关键公式、实验设计、结论-证据矩阵、"
            "结论边界、作者局限、批判性分析、知识连接、可检验研究想法。"
            "作者陈述、AI 分析、研究假设必须明确区分。所有关键判断在句末引用提供的"
            "[B书籍ID-C文本块ID]；无法由材料支持时写“材料不足，无法判断”，不得编造页码、"
            "实验、数字、引用或新颖性。研究想法只能写成待验证假设。"
        )},
        {"role": "user", "content": (
            f"题名：《{book_title}》\n\n已识别目录：\n{toc_text}\n\n来源块：\n"
            + "\n".join(sources)
        )},
    ]
    answer = ""
    try:
        async for delta in provider.stream_chat(prompt):
            answer += delta
    except Exception:  # noqa: BLE001
        return ""
    return answer.strip()


def audit_paper_card(card: str) -> dict:
    """轻量结构与来源审计，结果供 UI 明示而不是静默掩盖。"""
    missing = [i for i in range(1, 17) if not re.search(rf"^##\s+0?{i}\b", card, re.M)]
    source_refs = re.findall(r"\[B\d+-C\d+\]", card)
    unsupported_markers = card.count("材料不足，无法判断")
    return {
        "ok": not missing and bool(source_refs),
        "missing_sections": missing,
        "source_reference_count": len(source_refs),
        "not_assessable_count": unsupported_markers,
        "warnings": ([] if source_refs else ["阅读卡缺少文本块来源引用"]),
    }


# ---------- 5. Markdown 转换 ----------
def _downgrade_headers(md_text: str, levels: int = 2) -> str:
    """把 AI 总结内部的 Markdown 标题降级，避免与章节标题层级冲突。

    章节标题从 H2/H3 开始，总结内部标题至少应为 H4+。
    """
    import re as _re
    def _sub(m: "re.Match") -> str:
        hashes = m.group(1)
        return "#" * min(6, len(hashes) + levels) + " " + m.group(2)
    return _re.sub(r"^(#{1,6})\s+(.+)$", _sub, md_text, flags=_re.M)


def to_markdown(book_title: str, toc: list[dict], summaries: list[dict],
                section_texts: dict[str, str]) -> str:
    """生成 Markdown：目录 + 逐章 AI 总结 + 各节正文。"""
    md: list[str] = [f"# 《{book_title}》", "", "> 结构化阅读版：保留原文、PDF 页码与 AI 内容边界。", ""]
    md.append("## 目录")
    for t in toc:
        indent = "  " * (t["level"] - 1)
        md.append(f"{indent}- {t['title']}")
    md.append("")

    summary_map = {s["title"]: s["summary"] for s in summaries}
    # 按层级建树：每个标题挂在最近的上级标题下（用副本，不污染原数据）
    order = sorted([dict(t) for t in toc], key=lambda x: (x["page"], x["level"]))
    stack: list[dict] = []  # 上级标题栈
    for t in order:
        while stack and stack[-1]["level"] >= t["level"]:
            stack.pop()
        t["parent"] = stack[-1] if stack else None
        stack.append(t)

    def walk(items, depth):
        for t in items:
            prefix = "#" * min(6, 1 + t["level"])
            md.append(prefix + " " + t["title"])
            md.append("")
            if t["level"] == 1:
                summary = summary_map.get(t["title"])
                if summary and summary != "（该章无正文内容）":
                    # AI 总结独立折叠卡片，与原文明显分开（用户可点击展开/收起）
                    md.append("<details>")
                    md.append("<summary>AI 精读总结（点击展开/收起）</summary>")
                    md.append("")
                    md.append(_downgrade_headers(summary))
                    md.append("")
                    md.append("</details>")
                    md.append("")
                    md.append("---")
                    md.append("")
            body = section_texts.get(t["title"])
            if body:
                # 仅一级章节显示"章节原文"标题（与 AI 总结分隔）；小节正文直接跟随标题
                if t["level"] == 1:
                    md.append("### 章节原文")
                    md.append("")
                md.append(body)
                md.append("")
            kids = [x for x in order if x.get("parent") is t]
            walk(kids, depth + 1)

    walk([t for t in order if t.get("parent") is None], 0)
    return "\n".join(md)


def build_section_texts(chunks_by_page: list[tuple[int, str]], toc: list[dict]) -> dict[str, str]:
    """把页文本按标题切分到各标题下。chunks_by_page: [(page, text)]。

    Fix: use page-range mapping to avoid content duplication across sections.
    """
    if not toc:
        return {}
    from backend.app.services.analyzer.textclean import reflow_paragraphs
    titles_sorted = list(toc)
    section_texts: dict[str, str] = {t["title"]: "" for t in titles_sorted}
    for page, text in chunks_by_page:
        page_titles = [t for t in titles_sorted if t["page"] == page]
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        located: list[tuple[int, dict]] = []
        used_lines: set[int] = set()
        for title in page_titles:
            key = _norm_key(title["title"], title["level"]).split("|", 1)[1]
            for idx, line in enumerate(lines):
                if idx in used_lines:
                    continue
                line_key = re.sub(r"[\s\u3000，。、|｜.．:：—-]+", "", line)
                if line_key.startswith(key) or key.startswith(line_key):
                    located.append((idx, title))
                    used_lines.add(idx)
                    break
        located.sort(key=lambda x: x[0])
        if located:
            for pos, (line_idx, title) in enumerate(located):
                end = located[pos + 1][0] if pos + 1 < len(located) else len(lines)
                body = reflow_paragraphs("\n".join(lines[line_idx:end]))
                if body:
                    existing = section_texts.get(title["title"], "")
                    section_texts[title["title"]] = (existing + "\n\n" + body).strip()
            continue
        # 无法行级定位时才回退到最近标题，且每个文本块只写入一次。
        eligible = [t for t in titles_sorted if t["page"] <= page]
        if eligible:
            cur = eligible[-1]["title"]
            body = reflow_paragraphs(text)
            existing = section_texts.get(cur, "")
            if body and body not in existing:
                section_texts[cur] = (existing + "\n\n" + body).strip()
    return section_texts

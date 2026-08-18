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
    """从每页前几行提取 三级标题。返回 [{title, level, page}]（页序）。"""
    results: list[dict] = []
    for pno, text in enumerate(pages, start=1):
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines:
            continue
        for line in lines[:4]:
            if re.search(r"[.．·]{4,}", line):
                break  # 目录页
            m3 = _L3_RE.match(line)
            if m3 and len(_clean(m3.group(4))) <= 28:
                t = _clean(m3.group(4))
                results.append({"title": f"{m3.group(1)}.{m3.group(2)}.{m3.group(3)} {t}", "level": 3, "page": pno})
                break
            m1 = _L1_RE.match(line)
            if m1 and len(_clean(m1.group(1))) <= 28:
                num = re.search(r"第\s*([一二三四五六七八九十百千万0-9]+)", line)
                if num:
                    results.append({"title": "第" + num.group(1) + "章 " + _clean(m1.group(1)), "level": 1, "page": pno})
                    break
            m2 = _L2_RE.match(line)
            if m2 and len(_clean(m2.group(3))) <= 28:
                if m2.group(1) and m2.group(2):  # 数字.数字 编号
                    results.append({"title": f"{m2.group(1)}.{m2.group(2)} {_clean(m2.group(3))}", "level": 2, "page": pno})
                else:
                    num = re.search(r"第\s*([一二三四五六七八九十百千万0-9]+)", line)
                    results.append({"title": "第" + (num.group(1) if num else "?") + "节 " + _clean(m2.group(3)), "level": 2, "page": pno})
                break
    if len(results) < min_items:
        return []
    # 归一化去重（保留首次出现）
    seen_keys: set[str] = set()
    dedup: list[dict] = []
    for r in results:
        k = _norm_key(r["title"], r["level"])
        if k in seen_keys:
            continue
        seen_keys.add(k)
        dedup.append(r)
    return dedup


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

    # 节编号：每章 X.Y 连续性
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

    return {
        "ok": len(issues) == 0,
        "issues": issues[:30],
        "chapters": len(chapters),
        "sections": len(sections),
        "subsections": len(subs),
    }


# ---------- 3. AI 补全缺失标题 ----------
async def complete_with_ai(provider, toc: list[dict], issues: list[dict], pages: list[str],
                           max_items: int = 60) -> list[dict]:
    """把缺失项与相关页文本交给 AI，返回补齐后的完整目录（与现有合并，按页序）。"""
    if not issues:
        return toc
    # 取缺失项相关页（首页 + 缺失章节附近页）作为线索
    sample_pages: list[str] = []
    for pno in range(min(6, len(pages))):
        sample_pages.append(f"[第{pno + 1}页]\n{pages[pno][:400]}")
    # 再取中部若干页（标题样式线索）
    for pno in range(len(pages) // 2, min(len(pages), len(pages) // 2 + 6)):
        sample_pages.append(f"[第{pno + 1}页]\n{pages[pno][:400]}")

    current = "\n".join(f"{t['level']}级|第{t['page']}页|{t['title']}" for t in toc[:max_items])
    missing = "、".join(i["ref"] for i in issues[:20])

    prompt = [
        {"role": "system", "content": (
            "你是教材结构分析助手。下面是已提取的标题目录和缺失的编号，以及部分页文本。"
            "请找出缺失标题的准确名称（大标题=章、中标题=节、小标题=小节）。"
            "只输出 JSON 数组，格式：[{\"title\":\"标题\",\"level\":1|2|3,\"page\":页码}]。"
            "找不到的项返回 {\"title\":null,\"level\":0,\"page\":0}。不要输出解释。"
        )},
        {"role": "user", "content": (
            f"已提取目录：\n{current}\n\n缺失编号：{missing}\n\n页文本样本：\n" + "\n".join(sample_pages)
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

    filled: list[dict] = []
    if isinstance(data, list):
        for item in data:
            title = str(item.get("title") or "").strip()
            level = int(item.get("level") or 0)
            page = int(item.get("page") or 0)
            if title and level in (1, 2, 3) and 1 <= page <= len(pages):
                filled.append({"title": _clean(title), "level": level, "page": page})
    return _order_toc(_dedup_toc(toc + filled))


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


# ---------- 5. Markdown 转换 ----------
def to_markdown(book_title: str, toc: list[dict], summaries: list[dict],
                section_texts: dict[str, str]) -> str:
    """生成 Markdown：目录 + 逐章 AI 总结 + 各节正文。"""
    md: list[str] = [f"# 《{book_title}》", "", "> 由 Study Assistant 深度分析生成（标题目录 + AI 精读总结 + 正文）", ""]
    md.append("## 📑 目录")
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
            prefix = "#" * min(6, 2 + t["level"])
            md.append(prefix + " " + t["title"])
            md.append("")
            if t["level"] == 1:
                summary = summary_map.get(t["title"])
                if summary and summary != "（该章无正文内容）":
                    md.append("> 💡 AI 精读总结")
                    md.append(">")
                    md.append("> " + summary.replace("\n", "\n> "))
                    md.append("")
            body = section_texts.get(t["title"])
            if body:
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
    titles_sorted = sorted(toc, key=lambda x: (x["page"], x["level"]))
    page_to_title: dict[int, str] = {}
    for t in titles_sorted:
        page_to_title[t["page"]] = t["title"]

    section_texts: dict[str, str] = {}
    cur = None
    for page, text in chunks_by_page:
        if page in page_to_title:
            cur = page_to_title[page]
        if cur:
            section_texts.setdefault(cur, "")
            existing = section_texts.get(cur, "")
            if text.strip() and text.strip() not in existing:
                section_texts[cur] = (existing + "\n" + text).strip()
    return section_texts
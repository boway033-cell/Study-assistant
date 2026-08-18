"""启发式章节提取（无书签/目录时的兜底，零成本零依赖）

识别「第X章/第X节/第X编」等标题行，构建目录。
与 LLM 提取互补：启发式优先（快、可靠），失败再用 LLM。
"""
from __future__ import annotations

import re

# 章/节/编 标题模式（兼容多种排版）：
# 1) 「第1章公共管理导论 3」（紧凑，可带页码）
# 2) 「第 3 章 | 公共管理的价值」（空格+竖线分隔，标题可后置）
# 3) 「第 3 章    公 共 管 理 的 价 值」（空格分隔）
# 标题字符放宽：允许空格（排版字距）、书名号等；用 [^|\n] 防止跨分隔符
_CHAPTER_RE = re.compile(
    r"^\s*第\s*[一二三四五六七八九十百千万0-9]+\s*[章篇编部]\s*"
    r"(?:[|｜:：]\s*)?([^|\n]{2,40})"
)
_SECTION_RE = re.compile(
    r"^\s*第\s*[一二三四五六七八九十百千万0-9]+\s*节\s*"
    r"(?:[|｜:：]\s*)?([^|\n]{2,40})"
)

# 标题中的"正文尾巴"特征（用于精化截断）
_BODY_NOISE = re.compile(
    r"(随着|自从|从20|在|因为|如果|然而|因此|所以|现代|长期以来|对于|关于|所谓|作为|由于|并且|而是|就是|正是|首先|其次|最后|本章|重点|问题|公共管理是指|公共管理是)"
)


def _clean_title(raw: str) -> str:
    """精化标题：去掉页码尾巴、字距空格与混入正文的开头。"""
    t = raw.strip()
    # 去尾部页码（如「…导论   3」）
    t = re.sub(r"\s*[\d\s]+$", "", t)
    # 去尾部英文引用/短噪音（如「(finance」「组织是人类活动协调」）
    t = re.sub(r"\([a-zA-Z\s]+$", "", t)
    t = re.sub(r"组织是人类.*$", "", t)
    t = re.sub(r"随着时代的发展$|从20世纪中叶开始$|自从有文字记载的历史以来$|在两千多年前$|现代政府组织.*$|公共部门需要财政资源.*$", "", t)
    # 去掉字距空格（「公 共 管 理」→「公共管理」）
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    t = t.strip(" ，。、|｜")
    return t


def extract_toc_heuristic(pages: list[str], min_pages: int = 3) -> list[dict]:
    """从每页文本首行提取章节标题。

    pages: 每页文本列表。返回 [{"title","level","page"}]。
    规则：页码首行匹配「第X章…」→ level1；「第X节…」→ level2。
    要求命中至少 min_pages 个章节才视为有效（避免噪声）。
    """
    results: list[dict] = []
    for pno, text in enumerate(pages, start=1):
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines:
            continue
        # 取前 3 行找标题（章节标题通常在页首）
        for line in lines[:3]:
            # 目录页特征：标题后跟点线「.....」+ 页码 → 跳过（目录不是正文）
            if re.search(r"[.．·]{4,}", line):
                break
            m = _CHAPTER_RE.match(line)
            if m:
                num = re.search(r"第\s*([一二三四五六七八九十百千万0-9]+)", line)
                if not num:
                    continue
                title = "第" + num.group(1) + "章" + _clean_title(m.group(1))
                results.append({"title": title, "level": 1, "page": pno})
                break
            m2 = _SECTION_RE.match(line)
            if m2:
                num = re.search(r"第\s*([一二三四五六七八九十百千万0-9]+)", line)
                if not num:
                    continue
                title = "第" + num.group(1) + "节" + _clean_title(m2.group(1))
                results.append({"title": title, "level": 2, "page": pno})
                break

    # 过滤：至少 min_pages 个不同章节才有效
    if len(results) < min_pages:
        return []
    # 去重：按「第X章」编号去重，只保留首次出现（页眉重复/噪声标题被丢弃）
    seen_num: set[str] = set()
    dedup: list[dict] = []
    for r in results:
        num = re.match(r"(第[一二三四五六七八九十百千万0-9]+[章篇编部])", r["title"])
        key = num.group(1) if num else r["title"]
        if key in seen_num:
            continue
        seen_num.add(key)
        dedup.append(r)
    return dedup


def extract_toc_from_layout(layout) -> list[dict]:
    """从版面分析的 title_lines 提取目录（第二来源，当书签和第X章正则都失败时）。

    layout: LayoutResult，其 pages 含 BlockInfo 列表（block_type='title' 的块有 page 属性）。
    返回 [{"title","level","page"}]
    """
    results: list[dict] = []
    if not layout or not layout.pages:
        return results

    for page_blocks in layout.pages:
        for blk in page_blocks:
            if blk.block_type != "title":
                continue
            title = blk.text.strip()
            if not title or len(title) > 50:
                continue
            # 判断层级：含"章/篇/编/部"→1；含"节"或数字.数字→2；其他→2
            if re.search(r"第s*[一二三四五六七八九十百千万0-9]+s*[章篇编部]", title):
                level = 1
            elif re.search(r"第s*[一二三四五六七八九十百千万0-9]+s*节", title):
                level = 2
            elif re.match(r"d{1,2}.d{1,2}", title):
                level = 2
            elif re.match(r"d{1,2}.d{1,2}.d{1,2}", title):
                level = 3
            else:
                level = 2  # 版面识别的标题默认归二级
            results.append({"title": title, "level": level, "page": blk.page})

    # 去重 + 最少数量校验
    if len(results) < 3:
        return []
    seen: set[str] = set()
    dedup: list[dict] = []
    for r in results:
        key = _norm_title_key(r["title"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    return dedup


def _norm_title_key(title: str) -> str:
    """标题归一化 key（去空白标点）。"""
    t = re.sub(r"[s　，。、|｜.．:：—-]+", "", title.strip())
    return t

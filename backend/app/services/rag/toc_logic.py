"""目录编号逻辑审计与保守自修正。

这里不根据“看起来像目录”给自己高分，而是在整本文献的上下文中验证：
- 编号是否连续，是否重复/倒退；
- 小节编号前缀是否与父标题一致；
- 层级、父级、页码和阅读顺序是否相互一致。

自动修复只修改可由编号证明的层级/父级，不凭空生成缺失标题，
不自动删除重复项。无法确定的问题必须留给用户复核。
"""
from __future__ import annotations

from dataclasses import dataclass
import re


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CN_TOKEN = "零〇一二两三四五六七八九十百千万"


def chinese_number(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    if not value or any(ch not in _CN_DIGITS and ch not in _CN_UNITS for ch in value):
        return None
    total = section = number = 0
    for ch in value:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        else:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = number = 0
            else:
                section += (number or 1) * unit
                number = 0
    return total + section + number


@dataclass(frozen=True)
class NumberToken:
    scheme: str
    path: tuple[int, ...]

    @property
    def number(self) -> int:
        return self.path[-1]


_MAJOR_RE = re.compile(rf"^\s*第\s*([{_CN_TOKEN}0-9]+)\s*(部分|章|篇|编|部)(?:\s|$)")
_SECTION_RE = re.compile(rf"^\s*第\s*([{_CN_TOKEN}0-9]+)\s*节(?:\s|$)")
_DECIMAL_RE = re.compile(r"^\s*(\d{1,3}(?:[.．]\d{1,3}){1,3})(?:\s|[、:：])")
_CN_DOT_RE = re.compile(rf"^\s*([{_CN_TOKEN}]+)[、.．]")
_PAREN_CN_RE = re.compile(rf"^\s*[（(]([{_CN_TOKEN}]+)[）)]")
_PAREN_AR_RE = re.compile(r"^\s*[（(](\d{1,3})[）)]")


def parse_number_token(title: str) -> NumberToken | None:
    text = str(title or "").strip()
    match = _MAJOR_RE.match(text)
    if match:
        number = chinese_number(match.group(1))
        return NumberToken("major", (number,)) if number is not None else None
    match = _SECTION_RE.match(text)
    if match:
        number = chinese_number(match.group(1))
        return NumberToken("section", (number,)) if number is not None else None
    match = _DECIMAL_RE.match(text)
    if match:
        return NumberToken("decimal", tuple(int(part) for part in re.split(r"[.．]", match.group(1))))
    match = _CN_DOT_RE.match(text)
    if match:
        number = chinese_number(match.group(1))
        return NumberToken("cn_dot", (number,)) if number is not None else None
    match = _PAREN_CN_RE.match(text)
    if match:
        number = chinese_number(match.group(1))
        return NumberToken("cn_paren", (number,)) if number is not None else None
    match = _PAREN_AR_RE.match(text)
    return NumberToken("arabic_paren", (int(match.group(1)),)) if match else None


def _infer_level(token: NumberToken | None, stack: list[tuple[int, int, NumberToken | None]], declared: int) -> int:
    if token is None:
        return max(1, min(4, min(declared, (stack[-1][0] + 1) if stack else 1)))
    if token.scheme == "major":
        return 1
    if token.scheme == "decimal":
        return max(1, min(4, len(token.path)))
    if token.scheme == "section":
        return 2 if stack else 1
    if token.scheme == "cn_dot":
        # 有“第X节/1.1”时“一、”是其子级；否则它就是章下的二级标题。
        section_level = next((level for level, _, parent_token in reversed(stack)
                              if parent_token and parent_token.scheme in {"section", "decimal"} and level >= 2), None)
        return min(4, section_level + 1) if section_level else (2 if stack else 1)
    if token.scheme in {"cn_paren", "arabic_paren"}:
        # 同一编号体系连续出现时必为同级兄弟；旧逻辑会把前一个“（一）”当父级，
        # 于是“（一）→（四）”被拆到不同组，跳号完全漏检。
        sibling_level = next((level for level, _, parent_token in reversed(stack)
                              if parent_token and parent_token.scheme == token.scheme), None)
        if sibling_level:
            return sibling_level
        dot_level = next((level for level, _, parent_token in reversed(stack)
                          if parent_token and parent_token.scheme == "cn_dot"), None)
        if dot_level:
            return min(4, dot_level + 1)
        return min(4, (stack[-1][0] + 1) if stack else max(1, declared))
    return max(1, min(4, declared))


def analyze_toc_rows(rows: list[dict]) -> dict:
    """返回项级置信度、问题和安全修复建议。"""
    stack: list[tuple[int, int, NumberToken | None]] = []
    analyzed: list[dict] = []
    sequence_state: dict[tuple[int | None, str, tuple[int, ...]], tuple[int, int]] = {}
    issues: list[dict] = []
    previous_page = 0
    missing_candidates: list[dict] = []

    for index, raw in enumerate(rows):
        title = str(raw.get("title") or "").strip()
        declared = max(1, min(4, int(raw.get("level") or 1)))
        page = max(1, int(raw.get("page") or raw.get("start_page") or 1))
        token = parse_number_token(title)
        body_like = len(title) > 24 and len(re.findall(r"[，,]", title)) >= 3
        inferred = declared if body_like else _infer_level(token, stack, declared)
        while stack and stack[-1][0] >= inferred:
            stack.pop()
        parent_index = stack[-1][1] if stack else None

        item_issues: list[dict] = []
        repairs: dict = {}
        if body_like:
            item_issues.append({"type": "body_like_numbered_line",
                                "message": "包含多个句内逗号，疑似正文换行被误识为目录"})
        # PDF 同页阅读顺序常把前一标题的末个子项放到下一同级标题之后：
        # 二、... / （一）... / （二）... / 三、... / （三）...
        # 若“（三）”可唯一续接前一父项的 1,2 链，就回挂并建议移到“三、”之前。
        if (token and not body_like and token.scheme in {"cn_paren", "arabic_paren"}
                and token.number > 1 and parent_index is not None):
            current_parent = analyzed[parent_index] if parent_index < len(analyzed) else None
            current_parent_token = current_parent.get("number_token") if current_parent else None
            if current_parent_token and current_parent_token.get("scheme") == "cn_dot":
                higher_parent = current_parent.get("inferred_parent_index")
                current_child_numbers = [
                    (child.get("number_token") or {}).get("path", [None])[-1]
                    for child in analyzed
                    if child.get("inferred_parent_index") == parent_index
                    and (child.get("number_token") or {}).get("scheme") == token.scheme
                ]
                candidate_parents = [candidate_index for candidate_index, candidate in enumerate(analyzed[:parent_index])
                                     if candidate.get("inferred_parent_index") == higher_parent
                                     and (candidate.get("number_token") or {}).get("scheme") == "cn_dot"]
                continuation_parents = []
                for candidate_index in candidate_parents[-1:]:  # 只允许续接最近的前一同级父项
                    child_numbers = [
                        (child.get("number_token") or {}).get("path", [None])[-1]
                        for child in analyzed
                        if child.get("inferred_parent_index") == candidate_index
                        and (child.get("number_token") or {}).get("scheme") == token.scheme
                    ]
                    if token.number - 1 in child_numbers and token.number not in child_numbers:
                        continuation_parents.append(candidate_index)
                if token.number - 1 not in current_child_numbers and len(continuation_parents) == 1:
                    corrected_parent = continuation_parents[0]
                    item_issues.append({"type": "misordered_continuation",
                                        "message": f"编号 {token.number} 应续接前一父项，疑似同页阅读顺序错位"})
                    repairs["parent_index"] = corrected_parent
                    repairs["move_before_index"] = parent_index
                    parent_index = corrected_parent
        if declared != inferred:
            item_issues.append({"type": "level_mismatch", "message": f"编号语义应为 {inferred} 级，当前是 {declared} 级"})
            repairs["level"] = inferred
        actual_parent = raw.get("parent_index")
        if "parent_index" in raw and actual_parent != parent_index:
            item_issues.append({"type": "parent_mismatch", "message": "当前父级与编号/阅读顺序不一致"})
            repairs["parent_index"] = parent_index
        if page < previous_page:
            item_issues.append({"type": "page_reversal", "message": f"页码 {page} 早于上一目录页 {previous_page}"})
        previous_page = max(previous_page, page)

        if token and not body_like:
            prefix = token.path[:-1] if token.scheme == "decimal" else ()
            family = "major" if token.scheme == "major" else token.scheme
            group_key = (parent_index, family, prefix)
            prior = sequence_state.get(group_key)
            if prior:
                prior_number, prior_index = prior
                if token.number == prior_number:
                    item_issues.append({"type": "duplicate_number", "message": f"与第 {prior_index + 1} 项编号重复"})
                elif token.number < prior_number:
                    item_issues.append({"type": "sequence_reversal", "message": f"编号从 {prior_number} 倒退到 {token.number}"})
                elif token.number > prior_number + 1:
                    missing = list(range(prior_number + 1, token.number))[:20]
                    item_issues.append({"type": "sequence_gap", "message": f"编号链缺少 {missing}", "missing": missing})
                    missing_candidates.append({
                        "type": "missing_number_placeholder", "scheme": token.scheme,
                        "numbers": missing, "parent_index": parent_index,
                        "after_index": prior_index, "before_index": index,
                        "page_range": [analyzed[prior_index]["page"], page],
                        "status": "suggested",
                        "message": "疑似漏识或阅读顺序错位；仅生成编号占位建议，不编造标题正文",
                    })
            elif token.number != 1:
                item_issues.append({"type": "sequence_start", "message": f"本组从 {token.number} 开始，需确认前序标题是否漏识"})
            sequence_state[group_key] = (token.number, index)

            if token.scheme == "decimal" and len(token.path) > 1 and parent_index is not None:
                parent_token = analyzed[parent_index].get("number_token")
                expected_prefix = token.path[:-1]
                parent_path = tuple(parent_token["path"]) if parent_token else ()
                if parent_path and parent_path != expected_prefix:
                    item_issues.append({"type": "prefix_parent_mismatch",
                                        "message": f"编号前缀 {'.'.join(map(str, expected_prefix))} 与父级不一致"})

        penalty = sum(0.45 if issue["type"] == "body_like_numbered_line"
                      else 0.28 if issue["type"] in {"duplicate_number", "sequence_reversal", "prefix_parent_mismatch"}
                      else 0.18 for issue in item_issues)
        source_confidence = raw.get("confidence")
        base = float(source_confidence) if source_confidence is not None else (0.82 if token else 0.52)
        confidence = round(max(0.05, min(0.98, base + (0.06 if token and not item_issues else 0) - penalty)), 3)
        status = "high" if confidence >= 0.78 and not item_issues else "review" if confidence >= 0.45 else "low"
        number_token = {"scheme": token.scheme, "path": list(token.path)} if token else None
        analyzed.append({**raw, "title": title, "page": page, "declared_level": declared,
                         "inferred_level": inferred, "inferred_parent_index": parent_index,
                         "number_token": number_token, "confidence": confidence,
                         "review_status": status, "issues": item_issues, "repairs": repairs})
        for issue in item_issues:
            issues.append({**issue, "index": index, "title": title, "page": page})
        stack.append((inferred, index, token))

    safe_repairs = sum(1 for item in analyzed if item["repairs"])
    unresolved = sum(1 for issue in issues if issue["type"] not in {
        "level_mismatch", "parent_mismatch", "misordered_continuation",
    })
    return {"ok": not issues, "items": analyzed, "issues": issues,
            "missing_candidates": missing_candidates,
            "summary": {"total": len(analyzed), "high": sum(i["review_status"] == "high" for i in analyzed),
                        "review": sum(i["review_status"] == "review" for i in analyzed),
                        "low": sum(i["review_status"] == "low" for i in analyzed),
                        "safe_repairs": safe_repairs, "unresolved": unresolved}}


def auto_repair_toc_rows(rows: list[dict]) -> tuple[list[dict], dict]:
    """只应用安全的层级推断；返回修正后的扁平目录和审计。"""
    audit = analyze_toc_rows(rows)
    repaired_by_index: dict[int, dict] = {}
    move_before: dict[int, list[int]] = {}
    moved: set[int] = set()
    for index, item in enumerate(audit["items"]):
        row = {key: value for key, value in item.items() if key not in {
            "declared_level", "inferred_level", "inferred_parent_index", "number_token",
            "review_status", "issues", "repairs",
        }}
        row["level"] = item["inferred_level"]
        # unresolved 问题不得被“自动修复完成”掩盖，供目录工作台显式展示。
        unresolved_types = {issue["type"] for issue in item.get("issues", [])} - {
            "level_mismatch", "parent_mismatch", "misordered_continuation",
        }
        if unresolved_types:
            row["review_status"] = "needs_review"
            row["review_issue_types"] = sorted(unresolved_types)
        repaired_by_index[index] = row
        target = item.get("repairs", {}).get("move_before_index")
        if isinstance(target, int) and target < index:
            move_before.setdefault(target, []).append(index)
            moved.add(index)
    repaired = []
    for index in range(len(rows)):
        for moved_index in move_before.get(index, []):
            repaired.append(repaired_by_index[moved_index])
        if index not in moved:
            repaired.append(repaired_by_index[index])
    return repaired, audit

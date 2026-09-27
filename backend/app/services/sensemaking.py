"""Source-bound reading models and discovery cards.

These checks establish that a quote is present in a selected source chunk.
They do not establish that the source semantically supports the model's claim.
"""
from __future__ import annotations

import hashlib
import heapq
import re
from typing import Any

from sqlalchemy import func, select

from backend.app.models import Book, Chapter, Chunk

PROMPT_VERSION = "sensemaking-v4-relaxed-alignment"
READABLE_ARTIFACT_VERSIONS = {"sensemaking-v2-fulltext", "sensemaking-v3-evidence-ids", PROMPT_VERSION}
NODE_KINDS = {"question", "concept", "assumption", "method", "finding", "conclusion", "boundary", "uncertainty"}
MATERIAL_TYPES = {"quantitative", "qualitative", "theoretical", "review", "other"}
COMPARABILITY = {"comparable", "partial", "incomparable", "unknown"}
TENSION_TYPES = {"result", "mechanism", "scope", "measurement", "method", "normative"}
ORIGINS = {"cross_mechanism", "concept_scope", "claim_vs_evidence", "assumption", "negative_case", "concept_transfer"}
FEASIBILITY = {"selected_materials", "findable_source", "new_data"}
REVIEW_STATES = {"unreviewed", "valuable", "false_conflict", "unclear"}
PASS_ROLES = {"body", "frontmatter", "references", "appendix", "other"}
EPISTEMIC_STATES = {"source_observation", "author_interpretation", "ai_inference"}
READING_WINDOW_CHARS = 10000
EVIDENCE_PASSAGE_CHARS = 120


def source_ref(book_id: int, chunk: Chunk) -> str:
    page = f":P{chunk.page_start}" if chunk.page_start else ""
    return f"B{book_id}{page}:C{chunk.id}"


def source_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _candidate(chunk: Chunk) -> dict:
    content = chunk.content or ""
    return {
        "ref": source_ref(chunk.book_id, chunk), "book_id": chunk.book_id,
        "chunk_id": chunk.id, "page": chunk.page_start,
        "text": content[:2200], "hash": source_hash(content),
    }


def document_digest(db, book_id: int) -> dict:
    """Fingerprint every extracted chunk and the original file, in reading order."""
    book_version = db.execute(select(Book.file_hash, Book.file_path, Book.file_size)
                              .where(Book.id == book_id)).first()
    digest = hashlib.sha256()
    digest.update(repr(tuple(book_version) if book_version else ()).encode("utf-8"))
    count = 0
    rows = db.execute(select(Chunk.id, Chunk.chunk_index, Chunk.chapter_id, Chunk.page_start,
                             Chunk.page_end, Chunk.content).where(Chunk.book_id == book_id)
                      .order_by(Chunk.chunk_index, Chunk.id)).yield_per(128)
    for row in rows:
        fingerprint = [row.id, row.chunk_index, row.chapter_id, row.page_start,
                       row.page_end, source_hash(row.content or "")]
        digest.update(repr(fingerprint).encode("utf-8"))
        count += 1
    return {"mode": "whole_document", "book_id": book_id, "chunk_count": count,
            "digest": digest.hexdigest()}


def reading_windows(db, book_id: int, *, max_chars: int = READING_WINDOW_CHARS) -> dict:
    """Partition all extracted text by chapter and bounded model context, with no sampling."""
    if max_chars < 500:
        raise ValueError("阅读窗口过小")
    chapters = {row.id: row.title for row in db.scalars(
        select(Chapter).where(Chapter.book_id == book_id)).all()}
    total_pages = db.scalar(select(Book.total_pages).where(Book.id == book_id))
    covered_pages = set()
    windows = []
    current = []
    current_chapter = None
    current_chars = 0
    chunk_count = nonempty_count = text_chars = missing_pages = 0

    def flush():
        nonlocal current, current_chars
        if current:
            pages = [source["page"] for source in current if source["page"] is not None]
            windows.append({"id": f"w{len(windows) + 1}", "chapter_id": current_chapter,
                            "chapter_title": chapters.get(current_chapter, "未分章"),
                            "page_start": min(pages) if pages else None,
                            "page_end": max(pages) if pages else None,
                            "sources": current, "chars": current_chars})
            current, current_chars = [], 0

    for chunk in db.scalars(select(Chunk).where(Chunk.book_id == book_id)
                            .order_by(Chunk.chunk_index, Chunk.id)).yield_per(128):
        chunk_count += 1
        content = chunk.content or ""
        if not content.strip():
            continue
        nonempty_count += 1
        text_chars += len(content)
        missing_pages += int(chunk.page_start is None)
        if chunk.page_start is not None:
            end_page = chunk.page_end if chunk.page_end is not None else chunk.page_start
            if total_pages and total_pages > 0:
                covered_pages.update(range(max(1, chunk.page_start), min(total_pages, end_page) + 1))
        if current and chunk.chapter_id != current_chapter:
            flush()
        current_chapter = chunk.chapter_id
        part_size = max_chars - 160
        parts = [content[index:index + part_size] for index in range(0, len(content), part_size)]
        for part_index, part in enumerate(parts):
            if current and current_chars + len(part) > max_chars:
                flush()
            ref = source_ref(book_id, chunk) + (f":S{part_index + 1}" if len(parts) > 1 else "")
            current.append({"ref": ref, "book_id": book_id, "chunk_id": chunk.id,
                            "chapter_id": chunk.chapter_id, "page": chunk.page_start,
                            "text": part, "hash": source_hash(content)})
            current_chars += len(part)
    flush()
    return {"windows": windows, "version": document_digest(db, book_id),
            "coverage": {"mode": "all_extracted_text", "total_chunks": chunk_count,
                         "nonempty_chunks": nonempty_count, "empty_chunks": chunk_count - nonempty_count,
                         "text_chars": text_chars, "window_count": len(windows),
                         "chapter_count": len({window["chapter_id"] for window in windows}),
                         "missing_page_anchors": missing_pages,
                         "original_pages": total_pages, "pages_with_extracted_text": len(covered_pages) if total_pages else None,
                         "pages_without_extracted_text": max(0, total_pages - len(covered_pages)) if total_pages else None}}


def reading_evidence_catalog(window: dict) -> list[dict]:
    """Number exact, bounded source passages for citation without model-copied quotes."""
    passages = []
    for source in window["sources"]:
        content = source["text"]
        for start in range(0, len(content), EVIDENCE_PASSAGE_CHARS):
            excerpt = content[start:start + EVIDENCE_PASSAGE_CHARS].strip()
            if excerpt:
                if len(excerpt) < 8 and passages and passages[-1]["ref"] == source["ref"]:
                    passages[-1]["quote"] = content[start - EVIDENCE_PASSAGE_CHARS:].strip()
                    continue
                passages.append({"id": f"E{len(passages) + 1}", "ref": source["ref"],
                                 "quote": excerpt})
    return passages


def numbered_evidence(raw_ids: Any, passages: list[dict], sources: list[dict]) -> list[dict]:
    """Resolve only catalog IDs; source quotes still pass the literal verifier."""
    allowed = {passage["id"]: passage for passage in passages}
    selected = []
    for value in raw_ids[:8] if isinstance(raw_ids, list) else []:
        passage = allowed.get(str(value))
        if passage:
            selected.append({"ref": passage["ref"], "quote": passage["quote"]})
    return verified_evidence(selected, sources)


def claim_evidence_catalog(claims: list[dict]) -> list[dict]:
    """Number previously verified quotes for the whole-book argument map."""
    passages = []
    seen = set()
    for claim in claims:
        for evidence in claim["evidence"]:
            key = (evidence["ref"], evidence["quote"])
            if key not in seen:
                seen.add(key)
                passages.append({"id": f"E{len(passages) + 1}", "ref": key[0], "quote": key[1]})
    return passages


def normalize_reading_pass(raw: Any, window: dict, passages: list[dict] | None = None) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("局部阅读未返回有效 JSON")
    role = str(raw.get("role") or "body")
    if role not in PASS_ROLES:
        role = "body"
    claims = []
    passages = passages if passages is not None else reading_evidence_catalog(window)
    rejected = 0
    for item in raw.get("claims", [])[:7] if isinstance(raw.get("claims"), list) else []:
        if not isinstance(item, dict):
            continue
        evidence = numbered_evidence(item.get("evidence_ids"), passages, window["sources"])
        if not evidence:
            evidence = verified_evidence(item.get("evidence"), window["sources"])
        statement = str(item.get("statement") or "").strip()[:400]
        if statement and evidence:
            kind = str(item.get("kind") or "uncertainty")
            epistemic = str(item.get("epistemic_status") or "ai_inference")
            claims.append({"kind": kind if kind in NODE_KINDS else "uncertainty",
                           "statement": statement, "reasoning": str(item.get("reasoning") or "")[:350],
                           "epistemic_status": epistemic if epistemic in EPISTEMIC_STATES else "ai_inference",
                           "evidence": evidence})
        else:
            rejected += 1
    if role == "body" and not claims:
        raise ValueError(f"正文窗口 {window['id']} 没有可定位的论点：模型返回的 {rejected} 条候选均缺少论点或有效证据编号/引文")
    return {"id": window["id"], "chapter_id": window["chapter_id"],
            "chapter_title": window["chapter_title"], "page_start": window["page_start"],
            "page_end": window["page_end"], "role": role,
            "summary": str(raw.get("summary") or "")[:500], "summary_status": "ai_unchecked",
            "claims": claims,
            "source_refs": [source["ref"] for source in window["sources"]]}


def unresolved_reading_pass(window: dict, reason: str) -> dict:
    """保留未通过引文核对的窗口，让全篇结果明确标记缺口。"""
    return {"id": window["id"], "chapter_id": window["chapter_id"],
            "chapter_title": window["chapter_title"], "page_start": window["page_start"],
            "page_end": window["page_end"], "role": "other", "summary": "本段未取得可核对的 AI 论点，请查阅原文。",
            "summary_status": "unresolved", "claims": [], "unresolved": True,
            "unresolved_reason": reason[:160],
            "source_refs": [source["ref"] for source in window["sources"]]}


def select_interpretation_claims(payload: dict, question: str, max_chars: int = 15000) -> list[dict]:
    """Search the saved full-text claim ledger while keeping the whole-book map in view."""
    roots = [dict(node) for node in payload.get("nodes", []) if node.get("evidence")]
    local = []
    for reading_pass in payload.get("reading_passes", []):
        for claim in reading_pass.get("claims", []):
            local.append({**claim, "chapter_title": reading_pass.get("chapter_title"),
                          "window_id": reading_pass.get("id")})
    query = re.sub(r"\s+", "", question.lower())
    grams = {query[index:index + 2] for index in range(max(0, len(query) - 1))}

    def score(claim: dict) -> tuple[int, int]:
        haystack = re.sub(r"\s+", "", (claim.get("statement", "") + " "
                          + claim.get("reasoning", "") + " "
                          + str(claim.get("chapter_title") or "")).lower())
        return (sum(gram in haystack for gram in grams), len(claim.get("evidence", [])))

    ranked = sorted(local, key=score, reverse=True)
    chosen = []
    seen = set()
    size = 0
    for claim in [*roots, *ranked]:
        key = (claim.get("statement"), tuple(ev.get("ref") for ev in claim.get("evidence", [])))
        if key in seen:
            continue
        encoded = len(str(claim))
        if size + encoded > max_chars:
            continue
        chosen.append(claim)
        seen.add(key)
        size += encoded
    return chosen


def select_question_passages(db, book_id: int, question: str, limit: int = 4) -> list[dict]:
    """Reopen original text inside the selected book for details lost during reduction."""
    if limit <= 0:
        return []
    normalized = re.sub(r"\s+", "", question.lower())
    grams = {normalized[index:index + 2] for index in range(max(0, len(normalized) - 1))
             if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", normalized[index:index + 2])}
    if not grams:
        return []
    ranked = []
    for chunk in db.scalars(select(Chunk).where(Chunk.book_id == book_id)
                            .order_by(Chunk.chunk_index, Chunk.id)).yield_per(128):
        content = chunk.content or ""
        lowered = content.lower()
        score = sum(gram in lowered for gram in grams)
        if not score:
            continue
        candidate = (score, -chunk.chunk_index, chunk.id, chunk)
        if len(ranked) < limit:
            heapq.heappush(ranked, candidate)
        elif candidate[:3] > ranked[0][:3]:
            heapq.heapreplace(ranked, candidate)
    results = []
    for _, _, _, chunk in sorted(ranked, reverse=True):
        content = chunk.content or ""
        lowered = content.lower()
        positions = [lowered.find(gram) for gram in grams if lowered.find(gram) >= 0]
        center = min(positions) if positions else 0
        start = max(0, center - 350)
        results.append({"ref": source_ref(book_id, chunk), "book_id": book_id,
                        "chunk_id": chunk.id, "page": chunk.page_start,
                        "text": content[start:start + 2400], "hash": source_hash(content)})
    return results


def normalize_interpretation(raw: Any, sources: list[dict], question: str, mode: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("理解生成未返回有效 JSON")
    paragraphs = []
    allowed = {"source_observation", "author_interpretation", "ai_inference"}
    for item in raw.get("paragraphs", [])[:7] if isinstance(raw.get("paragraphs"), list) else []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("text") or "").strip()[:800]
        evidence = verified_evidence(item.get("evidence"), sources)
        if not content or not evidence:
            continue
        status = str(item.get("status") or "ai_inference")
        paragraphs.append({"text": content, "status": status if status in allowed else "ai_inference",
                           "evidence": evidence})
    unanswered = [str(item).strip()[:300] for item in raw.get("unanswered", [])[:5]
                  if isinstance(item, str) and item.strip()] if isinstance(raw.get("unanswered"), list) else []
    if not paragraphs and not unanswered:
        raise ValueError("生成结果没有可定位段落，也没有说明材料不足")
    return {"question": question, "mode": mode, "paragraphs": paragraphs,
            "unanswered": unanswered, "semantic_status": "ai_unchecked",
            "source_scope": "saved_fulltext_reading_and_selected_original_passages"}


def select_reading_sources(db, book_id: int, limit: int = 24) -> tuple[list[dict], int]:
    """Read a bounded, evenly distributed sample across the whole document."""
    query = select(Chunk).where(Chunk.book_id == book_id)
    total = db.scalar(select(func.count(Chunk.id)).where(Chunk.book_id == book_id)) or 0
    if total == 0:
        return [], 0
    if total <= limit:
        chunks = db.scalars(query.order_by(Chunk.chunk_index, Chunk.id)).all()
    else:
        slots = sorted(set([0, 1, 2, total - 2, total - 1] +
                           [round(i * (total - 1) / (limit - 1)) for i in range(limit)]))
        chunks = [db.scalar(query.order_by(Chunk.chunk_index, Chunk.id).offset(index).limit(1))
                  for index in slots]
    return [_candidate(chunk) for chunk in chunks if chunk and chunk.content.strip()], total


def select_comparison_sources(db, book_id: int, chunk_ids: list[int], limit: int = 6) -> list[dict]:
    if not chunk_ids:
        return []
    chunks = db.scalars(select(Chunk).where(Chunk.book_id == book_id, Chunk.id.in_(chunk_ids[:limit]))).all()
    by_id = {chunk.id: chunk for chunk in chunks}
    return [_candidate(by_id[cid]) for cid in chunk_ids[:limit] if cid in by_id and by_id[cid].content.strip()]


def source_block(sources: list[dict]) -> str:
    return "\n\n".join(f"[{s['ref']}]\n{s['text']}" for s in sources)


def _align_quote(quote: str, source: dict) -> str | None:
    """对齐空白、标点、全半角及大小写差异，返回原文中的逐字片段。"""
    import unicodedata

    def indexed(value: str):
        return [(folded, index) for index, char in enumerate(value)
                for folded in unicodedata.normalize("NFKC", char).casefold()
                if not folded.isspace() and not unicodedata.category(folded).startswith("P")]

    source_text = source["text"]
    source_chars = indexed(source_text)
    quote_chars = indexed(quote)
    if len(quote_chars) < 8:
        return None
    needle = "".join(char for char, _ in quote_chars)
    haystack = "".join(char for char, _ in source_chars)
    start = haystack.find(needle)
    if start < 0:
        return None
    return source_text[source_chars[start][1]:source_chars[start + len(quote_chars) - 1][1] + 1].strip()


def _align_minor_quote_error(quote: str, source: dict) -> str | None:
    """Allow one non-critical OCR/copy error only when the original span is unique."""
    import unicodedata

    def indexed(value: str):
        return [(folded, index) for index, char in enumerate(value)
                for folded in unicodedata.normalize("NFKC", char).casefold()
                if not folded.isspace() and not unicodedata.category(folded).startswith("P")]

    quote_chars = indexed(quote)
    source_chars = indexed(source["text"])
    if len(quote_chars) < 16:
        return None
    needle = "".join(char for char, _ in quote_chars)
    haystack = "".join(char for char, _ in source_chars)
    critical = set("不无非未没否勿仅只可必应须零一二三四五六七八九十百千万亿两〇幺壹贰叁肆伍陆柒捌玖拾佰仟")

    def one_safe_edit(candidate: str) -> bool:
        if abs(len(candidate) - len(needle)) > 1:
            return False
        left = right = edits = 0
        while left < len(needle) and right < len(candidate):
            if needle[left] == candidate[right]:
                left += 1
                right += 1
                continue
            edits += 1
            if edits > 1:
                return False
            changed = (needle[left] if len(needle) >= len(candidate) else "") + (
                candidate[right] if len(candidate) >= len(needle) else "")
            if any(char.isdigit() or char in critical for char in changed):
                return False
            if len(needle) >= len(candidate):
                left += 1
            if len(candidate) >= len(needle):
                right += 1
        if left < len(needle) or right < len(candidate):
            trailing = needle[left:] + candidate[right:]
            if edits or any(char.isdigit() or char in critical for char in trailing):
                return False
            edits = len(trailing)
        return edits == 1

    # Substitutions are more certain than insertions/deletions; only one span may qualify.
    for length in (len(needle), len(needle) - 1, len(needle) + 1):
        matches = []
        for start in range(max(0, len(haystack) - length + 1)):
            if one_safe_edit(haystack[start:start + length]):
                matches.append(source["text"][source_chars[start][1]:source_chars[start + length - 1][1] + 1].strip())
                if len(matches) > 1:
                    return None
        if matches:
            return matches[0]
    return None


def _quote_in_source(quote: str, source: dict) -> bool:
    return _align_quote(quote, source) is not None


def verified_evidence(raw: Any, sources: list[dict], *, book_id: int | None = None) -> list[dict]:
    eligible = [source for source in sources if book_id is None or source["book_id"] == book_id]
    allowed = {source["ref"]: source for source in eligible}
    result = []
    for item in raw[:8] if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or "")
        quote = str(item.get("quote") or "").strip()[:180]
        source = allowed.get(ref)
        aligned = _align_quote(quote, source) if source else None
        match_status = "exact_ref" if aligned else ""
        if not aligned:
            # Recover a missing/wrong ref only from a substantial, uniquely located quote.
            matches = [(candidate, found) for candidate in eligible
                       if len(quote) >= 12 and (found := _align_quote(quote, candidate))]
            if len(matches) > 1:
                continue
            if len(matches) == 1:
                source, aligned = matches[0]
                match_status = "recovered_ref"
        if not aligned and source:
            aligned = _align_minor_quote_error(quote, source)
            if aligned:
                match_status = "minor_quote_error"
        if not aligned or source is None or any(x["ref"] == source["ref"] for x in result):
            continue
        result.append({
            "ref": source["ref"], "quote": aligned, "book_id": source["book_id"],
            "chunk_id": source["chunk_id"], "page": source["page"],
            "source_hash": source["hash"], "support_status": "ai_unchecked",
            "match_status": match_status,
        })
    return result


def normalize_reading(raw: Any, sources: list[dict], total_chunks: int,
                      passages: list[dict] | None = None) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("模型没有返回有效的论证地图")
    passages = passages or []
    nodes = []
    for index, item in enumerate(raw.get("nodes", [])[:12] if isinstance(raw.get("nodes"), list) else []):
        if not isinstance(item, dict):
            continue
        evidence = numbered_evidence(item.get("evidence_ids"), passages, sources)
        if not evidence:
            evidence = verified_evidence(item.get("evidence"), sources)
        statement = str(item.get("statement") or "").strip()[:400]
        if not evidence or not statement:
            continue
        kind = str(item.get("kind") or "uncertainty")
        nodes.append({
            "id": f"n{index + 1}", "kind": kind if kind in NODE_KINDS else "uncertainty",
            "statement": statement, "reasoning": str(item.get("reasoning") or "").strip()[:600],
            "epistemic_status": str(item.get("epistemic_status"))
            if item.get("epistemic_status") in EPISTEMIC_STATES else "ai_inference",
            "evidence": evidence, "review_status": "unreviewed", "review_note": "", "remaining_doubt": "",
        })
    if len(nodes) < 3:
        raise ValueError("有效论证节点不足：需要至少三条带有原文短引的节点")
    valid_ids = {node["id"] for node in nodes}
    edges = []
    for item in raw.get("edges", [])[:20] if isinstance(raw.get("edges"), list) else []:
        if not isinstance(item, dict):
            continue
        source_id, target_id = str(item.get("from") or ""), str(item.get("to") or "")
        edge_evidence = numbered_evidence(item.get("evidence_ids"), passages, sources)
        if not edge_evidence:
            edge_evidence = verified_evidence(item.get("evidence"), sources)
        reason = str(item.get("reason") or "").strip()[:350]
        if source_id in valid_ids and target_id in valid_ids and source_id != target_id and edge_evidence and reason:
            edges.append({"from": source_id, "to": target_id,
                          "relation": str(item.get("relation") or "supports")[:60],
                          "reason": reason, "evidence": edge_evidence})
    if not edges:
        raise ValueError("论证地图没有带原文依据的有效推理关系")
    material_type = str(raw.get("material_type") or "other")
    question = str(raw.get("teach_back_question") or "").strip()[:300]
    if not question:
        question = f"请用自己的话解释：{nodes[-1]['statement']}"
    return {
        "material_type": material_type if material_type in MATERIAL_TYPES else "other",
        "nodes": nodes, "edges": edges, "teach_back_question": question,
        "coverage": {"selected_chunks": len(sources), "total_chunks": total_chunks,
                     "complete": len(sources) == total_chunks},
        "semantic_status": "ai_unchecked",
    }


def normalize_discovery(raw: Any, sources: list[dict], book_ids: list[int], concept: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("模型没有返回有效的概念对齐结果")
    alignment = raw.get("alignment") if isinstance(raw.get("alignment"), dict) else {}
    raw_definitions = alignment.get("definitions") if isinstance(alignment.get("definitions"), list) else []
    definitions = []
    for book_id in book_ids:
        candidate = next((x for x in raw_definitions
                          if isinstance(x, dict) and x.get("book_id") == book_id), None)
        if candidate:
            evidence = verified_evidence(candidate.get("evidence"), sources, book_id=book_id)
            if evidence:
                definitions.append({
                    "book_id": book_id,
                    "term": str(candidate.get("term") or concept)[:120],
                    "meaning": str(candidate.get("meaning") or "未说明")[:350],
                    "measurement": str(candidate.get("measurement") or "未说明")[:250],
                    "unit": str(candidate.get("unit") or "未说明")[:200],
                    "period_place": str(candidate.get("period_place") or "未说明")[:200],
                    "population": str(candidate.get("population") or "未说明")[:200],
                    "method": str(candidate.get("method") or "未说明")[:200],
                    "result_direction": str(candidate.get("result_direction") or "未说明")[:200],
                    "evidence": evidence,
                })
    status = str(alignment.get("status") or "unknown")
    if status not in COMPARABILITY or len(definitions) != 2:
        status = "unknown"
    candidates = []
    raw_cards = raw.get("discoveries") if isinstance(raw.get("discoveries"), list) else []
    if status in {"comparable", "partial"}:
        for item in raw_cards[:6]:
            if not isinstance(item, dict):
                continue
            evidence = verified_evidence(item.get("evidence"), sources)
            explanations = []
            for rival in item.get("rival_explanations", [])[:3] if isinstance(item.get("rival_explanations"), list) else []:
                if not isinstance(rival, dict):
                    continue
                statement = str(rival.get("statement") or "").strip()[:300]
                assumption = str(rival.get("assumption") or "").strip()[:300]
                prediction = str(rival.get("prediction") or "").strip()[:300]
                if statement and assumption and prediction:
                    explanations.append({"statement": statement, "assumption": assumption, "prediction": prediction})
            if {x["book_id"] for x in evidence} != set(book_ids) or len(explanations) < 2:
                continue
            question = str(item.get("discriminating_question") or "").strip()[:400]
            observation = str(item.get("observation") or "").strip()[:450]
            why_tension = str(item.get("why_tension") or "").strip()[:450]
            tension = str(item.get("tension") or "").strip()[:600]
            answered = str(item.get("already_answered") or "").strip()[:350]
            missing = str(item.get("still_missing") or "").strip()[:350]
            next_step = str(item.get("next_step") or "").strip()[:400]
            if not all((question, observation, why_tension, tension, answered, missing, next_step)):
                continue
            tension_type = str(item.get("tension_type") or "scope")
            origin = str(item.get("candidate_origin") or "cross_mechanism")
            feasibility = str(item.get("feasibility") or "findable_source")
            candidates.append({
                "title": str(item.get("title") or "待解释的张力")[:140],
                "observation": observation, "why_tension": why_tension, "tension": tension,
                "tension_type": tension_type if tension_type in TENSION_TYPES else "scope",
                "candidate_origin": origin if origin in ORIGINS else "cross_mechanism",
                "feasibility": feasibility if feasibility in FEASIBILITY else "findable_source",
                "rival_explanations": explanations, "discriminating_question": question,
                "already_answered": answered, "still_missing": missing,
                "next_step": next_step, "evidence": evidence,
                "review_status": "unreviewed", "review_note": "", "remaining_doubt": "",
                "epistemic_status": "research_idea",
            })
    if status in {"comparable", "partial"} and raw_cards and not candidates:
        raise ValueError("发现卡缺少两篇原文短引或竞争解释的前提与预测")
    # Keep an interpretable ranking rule; never present a synthetic novelty score.
    candidates.sort(key=lambda card: (
        len(card["evidence"]),
        20 <= len(card["discriminating_question"]) <= 250,
        len({rival["prediction"].strip().lower() for rival in card["rival_explanations"]}) >= 2,
        {"selected_materials": 2, "findable_source": 1, "new_data": 0}[card["feasibility"]],
        concept.lower() in (card["title"] + card["tension"] + card["discriminating_question"]).lower(),
    ), reverse=True)
    discoveries = []
    seen_questions = set()
    for card in candidates:
        key = re.sub(r"\W+", "", card["discriminating_question"]).lower()
        if key in seen_questions:
            continue
        seen_questions.add(key)
        card["id"] = f"d{len(discoveries) + 1}"
        discoveries.append(card)
        if len(discoveries) == 3:
            break
    return {
        "concept": concept, "alignment": {"status": status,
            "reason": str(alignment.get("reason") or "需要人工检查概念与测量口径")[:500],
            "definitions": definitions},
        "discoveries": discoveries,
        "no_tension_reason": str(raw.get("no_tension_reason") or "")[:400] if not discoveries else "",
        "semantic_status": "ai_unchecked",
    }


def source_versions(sources: list[dict]) -> list[dict]:
    return [{"book_id": s["book_id"], "chunk_id": s["chunk_id"], "hash": s["hash"]} for s in sources]


def stale_source_refs(db, versions: list[dict] | dict) -> list[int]:
    if isinstance(versions, dict) and versions.get("mode") == "whole_document":
        book_id = versions.get("book_id")
        if not isinstance(book_id, int) or not db.get(Book, book_id):
            return [book_id or 0]
        current = document_digest(db, book_id)
        return [] if (current["digest"] == versions.get("digest")
                      and current["chunk_count"] == versions.get("chunk_count")) else [book_id]
    if not isinstance(versions, list):
        return [0]
    chunk_ids = [item["chunk_id"] for item in versions]
    if not chunk_ids:
        return []
    chunks = db.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids))).all()
    current = {chunk.id: chunk for chunk in chunks}
    return [item["chunk_id"] for item in versions
            if item["chunk_id"] not in current or current[item["chunk_id"]].book_id != item["book_id"]
            or source_hash(current[item["chunk_id"]].content or "") != item["hash"]]

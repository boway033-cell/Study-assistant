"""Version-bound concept senses, comparisons and candidate counterevidence.

Model output is only a proposal. Every saved quote is checked against a chunk in
the frozen snapshot, and semantic support remains for the reader to review.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from math import ceil

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (Book, Chunk, ResearchEvidence, ResearchItem,
                                ResearchRevision, ResearchScopeSnapshot, Shelf, shelf_books)
from backend.app.services import research_archive as archive
from backend.app.services import sensemaking as sm
from backend.app.services.assistant_context import current_reading
from backend.app.services.llm.budget import estimate_tokens, load_default_budget

COUNTER_TYPES = ("opposite_result", "boundary", "negative_case", "alternative_mechanism",
                 "method_limit", "figure_mismatch")
ALIGNMENT_STATES = {"same", "partial", "different", "insufficient"}
MAX_CONCEPT_SOURCES_PER_BOOK = 4
BOOKS_PER_CONCEPT_BATCH = 4
PROMPT_VERSION = "research-b-1"


def _compact(term: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", term or "").casefold(), flags=re.UNICODE)


def normalize_term(term: str) -> str:
    """Conservative lookup key; never treats different words as synonyms."""
    return _compact(term)[:160]


def snapshot_versions(snapshot: ResearchScopeSnapshot) -> dict[int, dict]:
    return {int(row["book_id"]): row for row in json.loads(snapshot.book_versions_json)}


def require_current_snapshot(db: Session, snapshot: ResearchScopeSnapshot) -> dict[int, dict]:
    if not archive.snapshot_diff(db, snapshot)["is_current"]:
        raise HTTPException(409, "资料范围或正文版本已变化，请先冻结当前范围")
    versions = snapshot_versions(snapshot)
    if not versions:
        raise HTTPException(409, "当前范围没有文献")
    return versions


def _book_current(db: Session, book_id: int, version: dict) -> bool:
    book = db.get(Book, book_id)
    if not book or book.status != "ready" or (book.file_hash or "") != version.get("file_hash"):
        return False
    return sm.document_digest(db, book_id)["digest"] == version.get("text_digest")


def _quote_valid(quote: str, content: str) -> bool:
    compact = re.sub(r"\s+", "", quote or "")
    return len(compact) >= 8 and compact in re.sub(r"\s+", "", content or "")


def _ref(book_id: int, chunk: Chunk) -> str:
    return f"B{book_id}:C{chunk.id}:P{chunk.page_start}"


def _claims(payload: dict) -> list[dict]:
    claims = list(payload.get("nodes") or [])
    for section in payload.get("reading_passes") or []:
        claims.extend(section.get("claims") or [])
    return [claim for claim in claims if isinstance(claim, dict)]


def concept_sources(db: Session, snapshot: ResearchScopeSnapshot, term: str,
                    aliases: list[str] | None = None) -> dict:
    versions = require_current_snapshot(db, snapshot)
    words = [term, *(aliases or [])]
    keys = list(dict.fromkeys(key for word in words if (key := normalize_term(word))))
    if not keys or len(keys) > 8:
        raise HTTPException(422, "请输入概念术语，别名最多七个")
    sources = []
    no_match = []
    for book_id, version in versions.items():
        if not _book_current(db, book_id, version):
            raise HTTPException(409, f"文献 {book_id} 的正文与快照不符")
        claim_ids = set()
        artifact = current_reading(db, book_id)
        if artifact:
            try:
                for claim in _claims(json.loads(artifact.payload_json)):
                    if any(key in _compact(str(claim.get("statement") or "") + " " +
                                           str(claim.get("reasoning") or "")) for key in keys):
                        claim_ids.update(ev.get("chunk_id") for ev in claim.get("evidence") or []
                                         if isinstance(ev, dict) and isinstance(ev.get("chunk_id"), int))
            except (ValueError, TypeError, AttributeError):
                claim_ids.clear()
        ranked = []
        for chunk in db.scalars(select(Chunk).where(Chunk.book_id == book_id)
                                .order_by(Chunk.chunk_index, Chunk.id)).yield_per(128):
            if chunk.page_start is None or not (chunk.content or "").strip():
                continue
            compact = _compact(chunk.content)
            hits = sum(compact.count(key) for key in keys)
            if not hits and chunk.id not in claim_ids:
                continue
            ranked.append((hits + (3 if chunk.id in claim_ids else 0), chunk))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].chunk_index, pair[1].id))
        if not ranked:
            no_match.append({"book_id": book_id, "title": version["title"]})
            continue
        chosen, pages = [], set()
        for _, chunk in ranked:
            if chunk.page_start not in pages:
                chosen.append(chunk)
                pages.add(chunk.page_start)
            if len(chosen) >= MAX_CONCEPT_SOURCES_PER_BOOK:
                break
        if len(chosen) < MAX_CONCEPT_SOURCES_PER_BOOK:
            chosen_ids = {chunk.id for chunk in chosen}
            for _, chunk in ranked:
                if chunk.id not in chosen_ids:
                    chosen.append(chunk)
                if len(chosen) >= MAX_CONCEPT_SOURCES_PER_BOOK:
                    break
        for chunk in chosen[:MAX_CONCEPT_SOURCES_PER_BOOK]:
            content = chunk.content or ""
            locations = [content.casefold().find(word.casefold()) for word in words]
            at = min((position for position in locations if position >= 0), default=0)
            start = max(0, at - 350)
            sources.append({"ref": _ref(book_id, chunk), "book_id": book_id,
                            "chunk_id": chunk.id, "page": chunk.page_start,
                            "page_end": chunk.page_end, "book_title": version["title"],
                            "text": content[start:start + 1100], "content": content,
                            "hash": sm.source_hash(content)})
    return {"sources": sources, "no_match": no_match, "matched_books": len({s["book_id"] for s in sources}),
            "total_books": len(versions)}


def concept_preview(db: Session, snapshot: ResearchScopeSnapshot, term: str,
                    aliases: list[str] | None = None) -> dict:
    found = concept_sources(db, snapshot, term, aliases)
    calls = ceil(found["matched_books"] / BOOKS_PER_CONCEPT_BATCH) if found["matched_books"] else 0
    chars = sum(len(s["text"]) for s in found["sources"])
    tokens = estimate_tokens(int(chars * 1.7 + calls * 7000))
    budget = load_default_budget()
    return {"matched_books": found["matched_books"], "total_books": found["total_books"],
            "passages": len(found["sources"]), "no_match": found["no_match"],
            "estimated_calls": calls, "estimated_tokens": tokens,
            "within_budget": (not budget.max_calls or calls <= budget.max_calls)
            and (not budget.max_tokens or tokens <= budget.max_tokens)}


def normalize_concepts(raw: dict, sources: list[dict]) -> list[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("definitions"), list):
        raise ValueError("模型未返回义项列表")
    allowed = {source["ref"]: source for source in sources}
    result, seen, per_book = [], set(), {}
    for entry in raw["definitions"][:24]:
        if not isinstance(entry, dict):
            raise ValueError("义项结构无效")
        source = allowed.get(str(entry.get("ref") or ""))
        book_id = entry.get("book_id")
        term = str(entry.get("original_term") or "").strip()[:120]
        meaning = str(entry.get("meaning") or "").strip()[:500]
        quote = str(entry.get("quote") or "").strip()[:220]
        if (not source or book_id != source["book_id"] or
                (entry.get("page") is not None and entry.get("page") != source["page"]) or
                not term or not meaning or
                not _quote_valid(quote, source["text"]) or
                normalize_term(term) not in _compact(source["text"])):
            raise ValueError("义项文献、原词或原文短引无法核对")
        signature = (book_id, source["chunk_id"], normalize_term(term), quote)
        if signature in seen:
            continue
        if per_book.get(book_id, 0) >= 2:
            continue
        seen.add(signature)
        per_book[book_id] = per_book.get(book_id, 0) + 1
        result.append({"book_id": book_id, "chunk_id": source["chunk_id"],
                       "ref": source["ref"], "quote": quote, "original_term": term,
                       "meaning": meaning, "measurement": str(entry.get("measurement") or "未说明")[:350],
                       "unit": str(entry.get("unit") or "未说明")[:250],
                       "period_place": str(entry.get("period_place") or "未说明")[:250],
                       "population": str(entry.get("population") or "未说明")[:250],
                       "method": str(entry.get("method") or "未说明")[:250],
                       "result_direction": str(entry.get("result_direction") or "未说明")[:250],
                       "conclusion_scope": str(entry.get("conclusion_scope") or "未说明")[:350],
                       "page": source["page"], "page_end": source["page_end"],
                       "chunk_hash": source["hash"]})
    return result


def _source_is_current(db: Session, snapshot: ResearchScopeSnapshot, book_id: int,
                       chunk_id: int, quote: str, page: int | None = None) -> tuple[Book, Chunk]:
    version = snapshot_versions(snapshot).get(book_id)
    if version is None:
        raise HTTPException(422, "候选文献不在范围快照内")
    book = db.get(Book, book_id)
    chunk = db.get(Chunk, chunk_id)
    if not book or not chunk or chunk.book_id != book_id:
        raise HTTPException(409, "候选原文已删除")
    if not _book_current(db, book_id, version):
        raise HTTPException(409, "候选来源版本已变化")
    if chunk.page_start is None or (page is not None and chunk.page_start != page):
        raise HTTPException(422, "候选缺少可核对的原页")
    if not _quote_valid(quote, chunk.content):
        raise HTTPException(422, "候选短引不在对应原文块中")
    return book, chunk


def _attach(db: Session, item: ResearchItem, snapshot: ResearchScopeSnapshot,
            book_id: int, chunk_id: int, quote: str, relation: str, page: int | None = None,
            review_status: str = "unreviewed") -> ResearchEvidence:
    book, chunk = _source_is_current(db, snapshot, book_id, chunk_id, quote, page)
    row = ResearchEvidence(item_id=item.id, book_id=book_id, chunk_id=chunk_id,
                           book_title=book.title, page_start=chunk.page_start, page_end=chunk.page_end,
                           quote=quote, chunk_hash=sm.source_hash(chunk.content or ""),
                           relation=relation, review_status=review_status)
    db.add(row)
    return row


def persist_concepts(db: Session, snapshot: ResearchScopeSnapshot, term: str,
                     definitions: list[dict], model_name: str = "") -> list[int]:
    key = normalize_term(term)
    existing = db.scalars(select(ResearchItem).where(
        ResearchItem.snapshot_id == snapshot.id, ResearchItem.kind == "concept")).all()
    signatures = set()
    for item in existing:
        detail = json.loads(item.detail_json)
        for evidence in db.scalars(select(ResearchEvidence).where(ResearchEvidence.item_id == item.id)):
            signatures.add((evidence.book_id, evidence.chunk_id,
                            normalize_term(detail.get("original_term", "")), evidence.quote))
    ids = []
    try:
        for definition in definitions:
            signature = (definition["book_id"], definition["chunk_id"],
                         normalize_term(definition["original_term"]), definition["quote"])
            if signature in signatures:
                continue
            _source_is_current(db, snapshot, definition["book_id"], definition["chunk_id"],
                               definition["quote"], definition["page"])
            detail = {field: definition[field] for field in (
                "original_term", "meaning", "measurement", "unit", "period_place",
                "population", "method", "result_direction", "conclusion_scope")}
            detail.update(queried_term=term, analysis_model=model_name[:120], prompt_version=PROMPT_VERSION)
            row = ResearchItem(snapshot_id=snapshot.id, kind="concept", concept_key=key,
                               statement=f"{definition['original_term']}：{definition['meaning']}",
                               detail_json=archive._json(detail), origin="ai", review_status="unreviewed")
            db.add(row)
            db.flush()
            _attach(db, row, snapshot, definition["book_id"], definition["chunk_id"],
                    definition["quote"], "defines", definition["page"])
            ids.append(row.id)
            signatures.add(signature)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ids


def concept_cards(db: Session, snapshot: ResearchScopeSnapshot) -> list[dict]:
    rows = db.scalars(select(ResearchItem).where(
        ResearchItem.snapshot_id == snapshot.id,
        ResearchItem.kind.in_(["concept", "alignment"]))
        .order_by(ResearchItem.id)).all()
    grouped: dict[str, dict] = {}
    shelf_ids = json.loads(snapshot.shelf_ids_json)
    memberships = db.execute(select(shelf_books.c.book_id, Shelf.id, Shelf.name)
                             .join(Shelf, Shelf.id == shelf_books.c.shelf_id)
                             .where(Shelf.id.in_(shelf_ids))).all() if shelf_ids else []
    shelf_names: dict[int, list[str]] = {}
    for book_id, _, name in memberships:
        shelf_names.setdefault(book_id, []).append(name)
    sense_keys = {}
    for item in rows:
        if item.kind != "concept":
            continue
        key = item.concept_key
        card = grouped.setdefault(key, {"concept_key": key, "senses": [], "alignments": []})
        row = archive.item_row(db, item, snapshot)
        row["shelves"] = sorted({name for evidence in row["evidence"]
                                 for name in shelf_names.get(evidence["book_id"], [])})
        card["senses"].append(row)
        sense_keys[item.id] = key
    for item in rows:
        if item.kind != "alignment":
            continue
        row = archive.item_row(db, item, snapshot)
        for key in {sense_keys.get(sense_id) for sense_id in row["detail"].get("sense_ids", [])}:
            if key in grouped:
                grouped[key]["alignments"].append(row)
    return [card for card in grouped.values() if card["senses"]]


def merge_concept_key(db: Session, item: ResearchItem, snapshot: ResearchScopeSnapshot,
                      target: str) -> dict:
    if item.kind != "concept":
        raise HTTPException(422, "只有义项可以归并")
    key = normalize_term(target)
    if len(key) < 2:
        raise HTTPException(422, "归并术语太短")
    return archive.update_item(db, item, snapshot, {"concept_key": key})


def _sense_source(db: Session, item: ResearchItem, snapshot: ResearchScopeSnapshot) -> dict:
    if item.kind != "concept" or item.snapshot_id != snapshot.id:
        raise HTTPException(422, "请选择同一快照中的两个概念义项")
    evidence = db.scalar(select(ResearchEvidence).where(ResearchEvidence.item_id == item.id)
                         .order_by(ResearchEvidence.id))
    if evidence is None:
        raise HTTPException(409, "义项缺少来源")
    _source_is_current(db, snapshot, evidence.book_id, evidence.chunk_id,
                       evidence.quote, evidence.page_start)
    return {"item": item, "evidence": evidence, "detail": json.loads(item.detail_json)}


def alignment_inputs(db: Session, snapshot: ResearchScopeSnapshot,
                     first_id: int, second_id: int) -> list[dict]:
    if first_id == second_id:
        raise HTTPException(422, "请选择两个不同义项")
    first = db.get(ResearchItem, first_id)
    second = db.get(ResearchItem, second_id)
    if first is None or second is None:
        raise HTTPException(404, "义项不存在")
    rows = [_sense_source(db, item, snapshot) for item in (first, second)]
    if rows[0]["evidence"].book_id == rows[1]["evidence"].book_id:
        raise HTTPException(422, "请选择来自不同文献的义项")
    return rows


def normalize_alignment(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("模型未返回可比性判断")
    status = str(raw.get("status") or "")
    reason = str(raw.get("reason") or "").strip()[:800]
    unresolved = str(raw.get("unresolved") or "").strip()[:500]
    if status not in ALIGNMENT_STATES or len(reason) < 8:
        raise ValueError("可比性状态或理由无效")
    return {"status": status, "reason": reason, "unresolved": unresolved}


def persist_alignment(db: Session, snapshot: ResearchScopeSnapshot,
                      inputs: list[dict], result: dict, model_name: str = "") -> int:
    ids = sorted(row["item"].id for row in inputs)
    key = inputs[0]["item"].concept_key if inputs[0]["item"].concept_key == inputs[1]["item"].concept_key else normalize_term(inputs[0]["detail"]["original_term"])
    for row in db.scalars(select(ResearchItem).where(
        ResearchItem.snapshot_id == snapshot.id, ResearchItem.kind == "alignment")):
        if json.loads(row.detail_json).get("sense_ids") == ids:
            return row.id
    try:
        item = ResearchItem(snapshot_id=snapshot.id, kind="alignment", concept_key=key,
                            statement=result["reason"],
                            detail_json=archive._json({**result, "sense_ids": ids,
                                                       "analysis_model": model_name[:120],
                                                       "prompt_version": PROMPT_VERSION}),
                            origin="ai", review_status="unreviewed")
        db.add(item)
        db.flush()
        for source in inputs:
            evidence = source["evidence"]
            _attach(db, item, snapshot, evidence.book_id, evidence.chunk_id,
                    evidence.quote, "defines", evidence.page_start)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return item.id


def normalize_queries(raw: dict, statement: str) -> list[dict]:
    proposed = raw.get("queries") if isinstance(raw, dict) else None
    by_type = {}
    for item in proposed[:12] if isinstance(proposed, list) else []:
        if isinstance(item, dict) and item.get("type") in COUNTER_TYPES:
            query = str(item.get("query") or "").strip()[:300]
            if len(query) >= 4:
                by_type.setdefault(item["type"], query)
    fallback = {
        "opposite_result": "相反结果 反向关系", "boundary": "适用边界 群体 时期",
        "negative_case": "负例 反例 例外", "alternative_mechanism": "替代机制 其他解释",
        "method_limit": "方法限制 识别假设 样本偏差", "figure_mismatch": "图表 案例 与结论不一致",
    }
    return [{"type": kind, "query": by_type.get(kind) or f"{statement[:120]} {fallback[kind]}"}
            for kind in COUNTER_TYPES]


def counter_sources(db: Session, snapshot: ResearchScopeSnapshot,
                    queries: list[dict], retrieve) -> list[dict]:
    versions = require_current_snapshot(db, snapshot)
    ids = sorted(versions)
    per_query = min(24, max(6, len(ids) * 2))
    db.rollback()
    raw_hits = [(query, rank, hit)
                for query in queries
                for rank, hit in enumerate(retrieve(query["query"], book_ids=ids, top_k=per_query))]
    found: dict[int, dict] = {}
    for query, rank, hit in raw_hits:
        book_id = hit.get("book_id")
        chunk_id = hit.get("chunk_id")
        if book_id not in versions or not isinstance(chunk_id, int):
            continue
        chunk = db.get(Chunk, chunk_id)
        if not chunk or chunk.book_id != book_id or chunk.page_start is None:
            continue
        if not _book_current(db, book_id, versions[book_id]):
            continue
        score = 1 / (rank + 1)
        if chunk_id in found:
            found[chunk_id]["score"] += score
            found[chunk_id]["query_types"].add(query["type"])
            continue
        content = chunk.content or ""
        if not content.strip():
            continue
        snippet = str(hit.get("snippet") or "")
        at = content.find(snippet[:35]) if len(snippet) >= 35 else -1
        start = max(0, at - 250) if at >= 0 else 0
        found[chunk_id] = {"ref": _ref(book_id, chunk), "book_id": book_id,
                           "chunk_id": chunk_id, "book_title": versions[book_id]["title"],
                           "page": chunk.page_start, "page_end": chunk.page_end,
                           "text": content[start:start + 1000], "content": content,
                           "hash": sm.source_hash(content), "score": score,
                           "query_types": {query["type"]}}
    ranked = sorted(found.values(), key=lambda source: (-source["score"], source["book_id"], source["chunk_id"]))
    first_per_book = []
    seen_books = set()
    for source in ranked:
        if source["book_id"] not in seen_books:
            first_per_book.append(source)
            seen_books.add(source["book_id"])
    ordered = first_per_book + [source for source in ranked if source not in first_per_book]
    return [{**source, "query_types": sorted(source["query_types"])} for source in ordered[:18]]


def normalize_counterevidence(raw: dict, sources: list[dict]) -> list[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("candidates"), list):
        raise ValueError("模型未返回反证候选列表")
    allowed = {source["ref"]: source for source in sources}
    result, seen = [], set()
    for item in raw["candidates"][:8]:
        if not isinstance(item, dict):
            raise ValueError("反证候选结构无效")
        source = allowed.get(str(item.get("ref") or ""))
        quote = str(item.get("quote") or "").strip()[:220]
        reason = str(item.get("reason") or "").strip()[:500]
        kind = str(item.get("type") or "")
        relation = str(item.get("relation") or "")
        if (not source or
                (item.get("book_id") is not None and item.get("book_id") != source["book_id"]) or
                (item.get("page") is not None and item.get("page") != source["page"]) or
                not _quote_valid(quote, source["text"]) or
                kind not in COUNTER_TYPES or relation not in {"challenges", "limits"} or
                len(reason) < 8):
            raise ValueError("反证候选类型、理由或原文短引无法核对")
        signature = (source["book_id"], source["chunk_id"], quote)
        if signature in seen:
            continue
        seen.add(signature)
        result.append({"book_id": source["book_id"], "chunk_id": source["chunk_id"],
                       "page": source["page"], "quote": quote, "type": kind,
                       "relation": relation, "reason": reason,
                       "why_it_might_change": str(item.get("why_it_might_change") or "").strip()[:500]})
    return result[:6]


def counter_candidates(db: Session, parent: ResearchItem,
                       snapshot: ResearchScopeSnapshot) -> list[dict]:
    rows = db.scalars(select(ResearchItem).where(
        ResearchItem.snapshot_id == snapshot.id, ResearchItem.kind == "counterevidence")
        .order_by(ResearchItem.id.desc())).all()
    return [archive.item_row(db, item, snapshot) for item in rows
            if json.loads(item.detail_json).get("parent_item_id") == parent.id]


def persist_counterevidence(db: Session, snapshot: ResearchScopeSnapshot,
                            parent: ResearchItem, candidates: list[dict],
                            model_name: str = "", queries: list[dict] | None = None) -> list[int]:
    existing = counter_candidates(db, parent, snapshot)
    signatures = {(ev["book_id"], ev["chunk_id"], ev["quote"])
                  for row in existing for ev in row["evidence"]}
    ids = []
    try:
        for candidate in candidates:
            signature = (candidate["book_id"], candidate["chunk_id"], candidate["quote"])
            if signature in signatures:
                continue
            _source_is_current(db, snapshot, candidate["book_id"], candidate["chunk_id"],
                               candidate["quote"], candidate["page"])
            item = ResearchItem(snapshot_id=snapshot.id, kind="counterevidence",
                                concept_key=parent.concept_key, statement=candidate["reason"],
                                detail_json=archive._json({"parent_item_id": parent.id,
                                                           "type": candidate["type"],
                                                           "why_it_might_change": candidate["why_it_might_change"],
                                                           "search_queries": queries or [],
                                                           "analysis_model": model_name[:120],
                                                           "prompt_version": PROMPT_VERSION}),
                                origin="ai", review_status="unreviewed")
            db.add(item)
            db.flush()
            _attach(db, item, snapshot, candidate["book_id"], candidate["chunk_id"],
                    candidate["quote"], candidate["relation"], candidate["page"])
            ids.append(item.id)
            signatures.add(signature)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ids


def accept_counterevidence(db: Session, snapshot: ResearchScopeSnapshot,
                           candidate: ResearchItem, parent: ResearchItem,
                           revised_statement: str) -> dict:
    if (candidate.kind != "counterevidence" or parent.kind != "judgment" or
            candidate.snapshot_id != snapshot.id or parent.snapshot_id != snapshot.id or
            json.loads(candidate.detail_json).get("parent_item_id") != parent.id):
        raise HTTPException(422, "反证候选与判断不匹配")
    if candidate.review_status != "unreviewed":
        raise HTTPException(409, "该候选已经处理，请勿重复采纳")
    statement = revised_statement.strip()
    if len(statement) < 2 or len(statement) > 4000:
        raise HTTPException(422, "请写下修订后的判断")
    evidence = db.scalar(select(ResearchEvidence).where(ResearchEvidence.item_id == candidate.id))
    if evidence is None:
        raise HTTPException(409, "候选缺少来源")
    _source_is_current(db, snapshot, evidence.book_id, evidence.chunk_id,
                       evidence.quote, evidence.page_start)
    try:
        before = archive._item_state(parent)
        attached = db.scalar(select(ResearchEvidence).where(
            ResearchEvidence.item_id == parent.id,
            ResearchEvidence.book_id == evidence.book_id,
            ResearchEvidence.chunk_id == evidence.chunk_id,
            ResearchEvidence.quote == evidence.quote,
            ResearchEvidence.relation == evidence.relation))
        if attached is None:
            attached = _attach(db, parent, snapshot, evidence.book_id, evidence.chunk_id,
                               evidence.quote, evidence.relation, evidence.page_start,
                               review_status="confirmed")
        else:
            attached.review_status = "confirmed"
        db.flush()
        parent.statement = statement
        parent.updated_at = datetime.now()
        db.add(ResearchRevision(item_id=parent.id, before_json=archive._json(before),
                                after_json=archive._json(archive._item_state(parent)),
                                trigger_evidence_id=attached.id, actor="user"))
        candidate_before = archive._item_state(candidate)
        candidate.review_status = "confirmed"
        evidence.review_status = "confirmed"
        candidate.updated_at = datetime.now()
        db.add(ResearchRevision(item_id=candidate.id, before_json=archive._json(candidate_before),
                                after_json=archive._json(archive._item_state(candidate)),
                                trigger_evidence_id=evidence.id, actor="user"))
        db.commit()
        db.refresh(parent)
    except Exception:
        db.rollback()
        raise
    return archive.item_row(db, parent, snapshot)

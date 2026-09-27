"""Iteration C: falsifiable reading tasks and source-bound research coaching."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ResearchEvidence, ResearchItem, ResearchRevision, ResearchScopeSnapshot
from backend.app.services import research_archive as archive, research_discovery as discovery
from backend.app.services.assistant_context import _score, _terms, current_reading

PROMPT_VERSION = "research-c-1"
MATERIAL_QUESTIONS = {
    "quantitative": "这一步的变量怎样测量，对照组与识别假设能否排除另一种解释？",
    "qualitative": "这个案例为何被选中，有没有负例，访谈或田野材料怎样支撑作者的解释？",
    "theoretical": "这里的概念前提是什么，哪一步推演使结论成立，竞争理论会怎样解释？",
    "review": "作者如何搜集和纳排材料，综合规则是否遗漏了可能改变结论的研究？",
    "other": "这一步从材料到结论依赖什么前提，哪处原文最值得复核？",
}
OUTCOMES = {"supports_a", "supports_b", "neither", "unclear"}


def rivals(parent: ResearchItem) -> list[dict]:
    if parent.kind != "judgment" or parent.review_status != "confirmed":
        raise HTTPException(422, "请先确认一条研究判断")
    raw = json.loads(parent.detail_json).get("rivals")
    if not isinstance(raw, list) or len(raw) != 2:
        raise HTTPException(422, "请填写两种竞争解释及其前提和可观察预测")
    cleaned = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise HTTPException(422, "竞争解释格式无效")
        item = {field: str(entry.get(field) or "").strip() for field in ("statement", "premise", "prediction")}
        if any(len(value) < 4 or len(value) > 1000 for value in item.values()):
            raise HTTPException(422, "每种解释、前提和预测均须填写 4–1000 字")
        cleaned.append(item)
    if cleaned[0]["statement"] == cleaned[1]["statement"] or cleaned[0]["prediction"] == cleaned[1]["prediction"]:
        raise HTTPException(422, "两种解释及其预测需要能够区分")
    return cleaned


def set_rivals(db: Session, parent: ResearchItem, snapshot: ResearchScopeSnapshot,
               entries: list[dict], interest: str = "") -> dict:
    if parent.kind != "judgment" or parent.review_status != "confirmed":
        raise HTTPException(422, "请先确认一条研究判断")
    detail = json.loads(parent.detail_json)
    detail["rivals"] = entries
    detail["reading_interest"] = interest.strip()[:500]
    # Validate before creating a revision.
    probe = ResearchItem(kind="judgment", review_status="confirmed", detail_json=archive._json(detail))
    rivals(probe)
    return archive.update_item(db, parent, snapshot, {"detail": detail})


def _text(value, limit: int, minimum: int = 4) -> str:
    result = str(value or "").strip()[:limit]
    if len(result) < minimum:
        raise ValueError("区分性任务缺少具体问题、预测或判断理由")
    return result


def normalize_tasks(raw: dict, sources: list[dict]) -> list[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
        raise ValueError("模型没有返回阅读任务列表")
    allowed = {source["ref"]: source for source in sources}
    result, seen = [], set()
    for proposal in raw["tasks"][:6]:
        if not isinstance(proposal, dict):
            raise ValueError("阅读任务格式无效")
        kind = proposal.get("target_type")
        if kind not in {"source_page", "external_data"}:
            raise ValueError("任务目标必须是原页或外部资料")
        question = _text(proposal.get("question"), 500)
        expected_a = _text(proposal.get("expected_a"), 500)
        expected_b = _text(proposal.get("expected_b"), 500)
        why = _text(proposal.get("why_change"), 500)
        gap = _text(proposal.get("evidence_gap"), 500)
        if expected_a == expected_b:
            raise ValueError("两种解释必须有不同的可观察结果")
        source = allowed.get(str(proposal.get("ref") or "")) if kind == "source_page" else None
        quote = str(proposal.get("quote") or "").strip()[:220]
        if kind == "source_page":
            if (not source or (proposal.get("book_id") is not None and proposal["book_id"] != source["book_id"])
                    or (proposal.get("page") is not None and proposal["page"] != source["page"])
                    or not discovery._quote_valid(quote, source["text"])):
                raise ValueError("阅读任务所引原页或短引无法核对")
            target = {"book_id": source["book_id"], "chunk_id": source["chunk_id"],
                      "page": source["page"], "quote": quote}
        else:
            if proposal.get("ref") or proposal.get("quote") or proposal.get("page") or proposal.get("book_id"):
                raise ValueError("外部资料任务不能伪装成当前快照的原页")
            target = {"external_data_type": _text(proposal.get("external_data_type"), 300)}
        signature = (kind, source["chunk_id"] if source else None, question.casefold())
        if signature in seen:
            continue
        seen.add(signature)
        # Ordinal factors are transparent and are not an innovation score.
        factors = proposal.get("ranking") if isinstance(proposal.get("ranking"), dict) else {}
        rank = {field: max(0, min(2, int(factors.get(field, 1)))) for field in
                ("judgment_change", "evidence_gap", "user_interest")}
        rank["page_access"] = 2 if source else 0
        result.append({"target_type": kind, "question": question, "expected_a": expected_a,
                       "expected_b": expected_b, "why_change": why, "evidence_gap": gap,
                       "ranking": rank, **target})
    result.sort(key=lambda item: (-sum(item["ranking"].values()), item["target_type"] != "source_page", item["question"]))
    return result[:3]


def external_fallback(parent: ResearchItem) -> dict:
    """A concrete next step when the selected snapshot has no usable original page."""
    first, second = rivals(parent)
    return {"target_type": "external_data",
            "question": f"什么资料能同时检验‘{first['prediction']}’与‘{second['prediction']}’？"[:500],
            "expected_a": first["prediction"], "expected_b": second["prediction"],
            "why_change": "同一对象上的不同预测能够区分这两种解释。",
            "evidence_gap": "本次分析未筛出可核对的区分性原页；仍需继续检索。",
            "external_data_type": f"同一研究对象上对应两种预测的测量、案例或访谈记录；需核对前提：{first['premise']}；{second['premise']}"[:300],
            "ranking": {"judgment_change": 2, "evidence_gap": 2, "user_interest": 1, "page_access": 0}}


def task_sources(db: Session, snapshot: ResearchScopeSnapshot, parent: ResearchItem, retrieve) -> list[dict]:
    versions = discovery.require_current_snapshot(db, snapshot)
    rival = rivals(parent)
    interest = json.loads(parent.detail_json).get("reading_interest", "")
    queries = [f"{parent.statement} {rival[0]['prediction']} {rival[1]['prediction']} {interest}",
               f"{rival[0]['premise']} {rival[1]['premise']} 反例 测量 范围"]
    db.rollback()
    hits = [hit for query in queries for hit in retrieve(query[:600], book_ids=sorted(versions), top_k=8)]
    result, seen = [], set()
    from backend.app.models import Chunk
    for hit in hits:
        book_id, chunk_id = hit.get("book_id"), hit.get("chunk_id")
        if book_id not in versions or not isinstance(chunk_id, int) or chunk_id in seen:
            continue
        chunk = db.get(Chunk, chunk_id)
        if not chunk or chunk.book_id != book_id or chunk.page_start is None or not (chunk.content or "").strip():
            continue
        if not discovery._book_current(db, book_id, versions[book_id]):
            continue
        seen.add(chunk_id)
        content = chunk.content or ""
        snippet = str(hit.get("snippet") or "")
        at = content.find(snippet[:35]) if len(snippet) >= 35 else -1
        excerpt = content[max(0, at - 350):max(0, at - 350) + 1400] if at >= 0 else content[:1400]
        result.append({"ref": discovery._ref(book_id, chunk), "book_id": book_id,
                       "chunk_id": chunk_id, "book_title": versions[book_id]["title"],
                       "page": chunk.page_start, "text": excerpt})
        if len(result) == 12:
            break
    return result


def reading_tasks(db: Session, parent: ResearchItem, snapshot: ResearchScopeSnapshot) -> list[dict]:
    rows = db.scalars(select(ResearchItem).where(ResearchItem.snapshot_id == snapshot.id,
        ResearchItem.kind == "reading_task").order_by(ResearchItem.id.desc())).all()
    tasks = [archive.item_row(db, row, snapshot) for row in rows
             if json.loads(row.detail_json).get("parent_item_id") == parent.id]
    def priority(row):
        evidence = row["evidence"]
        stale = any(e["source_status"] != "current" for e in evidence)
        unresolved = row["detail"].get("outcome") in (None, "unclear")
        return (not stale, not unresolved, -sum(row["detail"].get("ranking", {}).values()), -row["id"])
    return sorted(tasks, key=priority)


def persist_tasks(db: Session, snapshot: ResearchScopeSnapshot, parent: ResearchItem,
                  tasks: list[dict], model: str = "") -> list[int]:
    discovery.require_current_snapshot(db, snapshot)
    rival = rivals(parent)
    existing = [row["detail"] for row in reading_tasks(db, parent, snapshot)]
    ids = []
    try:
        for task in tasks[:3]:
            if any(old.get("question") == task["question"] and old.get("target_type") == task["target_type"] for old in existing):
                continue
            detail = {**task, "parent_item_id": parent.id, "judgment_at_generation": parent.statement,
                      "rivals": rival,
                      "analysis_model": model[:120], "prompt_version": PROMPT_VERSION}
            item = ResearchItem(snapshot_id=snapshot.id, kind="reading_task",
                                statement=task["question"], detail_json=archive._json(detail),
                                origin="ai", review_status="unreviewed")
            db.add(item)
            db.flush()
            if task["target_type"] == "source_page":
                discovery._attach(db, item, snapshot, task["book_id"], task["chunk_id"],
                                  task["quote"], "unclear", task["page"])
            ids.append(item.id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ids


def record_outcome(db: Session, snapshot: ResearchScopeSnapshot, task: ResearchItem,
                   parent: ResearchItem, outcome: str, note: str, revised_statement: str | None = None,
                   external_reference: str = "") -> dict:
    detail = json.loads(task.detail_json)
    if (task.kind != "reading_task" or parent.kind != "judgment" or
            task.snapshot_id != snapshot.id or parent.snapshot_id != snapshot.id or
            detail.get("parent_item_id") != parent.id or outcome not in OUTCOMES):
        raise HTTPException(422, "阅读任务与判断不匹配")
    if detail.get("outcome"):
        raise HTTPException(409, "该阅读任务已经复核；请生成新任务继续阅读")
    evidence = db.scalar(select(ResearchEvidence).where(ResearchEvidence.item_id == task.id))
    if detail.get("target_type") == "source_page":
        if not evidence:
            raise HTTPException(409, "原页依据缺失")
        discovery._source_is_current(db, snapshot, evidence.book_id, evidence.chunk_id,
                                     evidence.quote, evidence.page_start)
    elif evidence:
        raise HTTPException(422, "外部资料任务不能绑定快照原页")
    if detail.get("target_type") == "external_data" and outcome != "unclear" and len(external_reference.strip()) < 8:
        raise HTTPException(422, "外部资料复核请填写可追溯的来源线索")
    if revised_statement is not None and not 2 <= len(revised_statement.strip()) <= 4000:
        raise HTTPException(422, "修订判断需 2–4000 字")
    before_task, before_parent = archive._item_state(task), archive._item_state(parent)
    detail.update(outcome=outcome, outcome_note=note.strip()[:1200],
                  external_reference=external_reference.strip()[:1000] if not evidence else "",
                  reviewed_at=datetime.now().isoformat())
    task.detail_json = archive._json(detail)
    task.review_status = "unclear" if outcome == "unclear" else "confirmed"
    task.updated_at = datetime.now()
    parent_detail = json.loads(parent.detail_json)
    parent_detail.setdefault("reading_outcomes", []).append({"task_id": task.id,
        "outcome": outcome, "note": note.strip()[:1200],
        "source": "original_page" if evidence else "external_data_user_report",
        "external_reference": external_reference.strip()[:1000] if not evidence else ""})
    parent.detail_json = archive._json(parent_detail)
    if revised_statement is not None:
        parent.statement = revised_statement.strip()
    parent.updated_at = datetime.now()
    # Trigger must belong to the parent. Attach the verified page before recording revision.
    trigger = None
    if evidence:
        attached = db.scalar(select(ResearchEvidence).where(ResearchEvidence.item_id == parent.id,
            ResearchEvidence.book_id == evidence.book_id, ResearchEvidence.chunk_id == evidence.chunk_id,
            ResearchEvidence.quote == evidence.quote))
        if attached is None:
            attached = discovery._attach(db, parent, snapshot, evidence.book_id,
                evidence.chunk_id, evidence.quote, "unclear", evidence.page_start,
                review_status="confirmed")
            db.flush()
        trigger = attached.id
    db.add(ResearchRevision(item_id=task.id, before_json=archive._json(before_task),
        after_json=archive._json(archive._item_state(task)),
        trigger_evidence_id=evidence.id if evidence else None, actor="user"))
    db.add(ResearchRevision(item_id=parent.id, before_json=archive._json(before_parent),
        after_json=archive._json(archive._item_state(parent)), trigger_evidence_id=trigger, actor="user"))
    db.commit()
    return archive.item_row(db, task, snapshot)


def coach_source(db: Session, snapshot: ResearchScopeSnapshot, book_id: int,
                 focus: str = "") -> dict:
    versions = discovery.require_current_snapshot(db, snapshot)
    if book_id not in versions:
        raise HTTPException(422, "文献不在当前范围快照内")
    artifact = current_reading(db, book_id)
    if not artifact:
        raise HTTPException(409, "请先完成这本文献的全文研读")
    payload = json.loads(artifact.payload_json)
    material_type = payload.get("material_type", "other")
    if material_type not in MATERIAL_QUESTIONS:
        material_type = "other"
    claims = discovery._claims(payload)
    terms = _terms(focus)
    claims.sort(key=lambda claim: _score(terms, str(claim.get("statement") or "") + " " +
                                   str(claim.get("reasoning") or "")), reverse=True)
    for claim in claims:
        for ev in claim.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            book = ev.get("book_id", book_id)
            chunk = ev.get("chunk_id")
            quote = str(ev.get("quote") or "")
            if book != book_id or not isinstance(chunk, int):
                continue
            try:
                _, source_chunk = discovery._source_is_current(db, snapshot, book, chunk, quote)
            except HTTPException:
                continue
            content = source_chunk.content or ""
            at = content.find(quote)
            excerpt = content[max(0, at - 450):max(0, at - 450) + 1600] if at >= 0 else content[:1600]
            return {"material_type": material_type, "guidance": MATERIAL_QUESTIONS[material_type],
                    "artifact_id": artifact.id, "node_id": claim.get("id"),
                    "claim": str(claim.get("statement") or "")[:600],
                    "reasoning": str(claim.get("reasoning") or "")[:800],
                    "ref": discovery._ref(book_id, source_chunk), "book_id": book_id,
                    "chunk_id": chunk, "page": source_chunk.page_start, "quote": quote,
                    "text": excerpt}
    raise HTTPException(409, "全文论证地图缺少可核验的原页节点")


def normalize_question(raw: dict, source: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("ref") != source["ref"]:
        raise ValueError("陪练问题缺少有效原页锚点")
    quote = str(raw.get("quote") or "").strip()[:220]
    if not discovery._quote_valid(quote, source["text"]):
        raise ValueError("陪练问题的短引不在原页")
    return {"question": _text(raw.get("question"), 500),
            "weak_point": _text(raw.get("weak_point"), 500), "quote": quote}


def persist_question(db: Session, snapshot: ResearchScopeSnapshot, parent: ResearchItem,
                     source: dict, result: dict, model: str = "") -> int:
    discovery._source_is_current(db, snapshot, source["book_id"], source["chunk_id"], result["quote"], source["page"])
    detail = {**result, "parent_item_id": parent.id, "material_type": source["material_type"],
              "artifact_id": source["artifact_id"], "node_id": source["node_id"],
              "book_id": source["book_id"], "prompt_version": PROMPT_VERSION,
              "analysis_model": model[:120]}
    item = ResearchItem(snapshot_id=snapshot.id, kind="coach_turn",
        statement=result["question"], detail_json=archive._json(detail),
        origin="ai", review_status="unreviewed")
    db.add(item)
    db.flush()
    discovery._attach(db, item, snapshot, source["book_id"], source["chunk_id"],
                      result["quote"], "unclear", source["page"])
    db.commit()
    return item.id


def coach_turns(db: Session, parent: ResearchItem, snapshot: ResearchScopeSnapshot) -> list[dict]:
    rows = db.scalars(select(ResearchItem).where(ResearchItem.snapshot_id == snapshot.id,
        ResearchItem.kind == "coach_turn").order_by(ResearchItem.id.desc())).all()
    result = [archive.item_row(db, row, snapshot) for row in rows
              if json.loads(row.detail_json).get("parent_item_id") == parent.id]
    return sorted(result, key=lambda row: (row["detail"].get("feedback") is not None,
        all(ev["source_status"] == "current" for ev in row["evidence"]), -row["id"]))


def normalize_feedback(raw: dict, source: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("模型未返回陪练反馈")
    source_quote = str(raw.get("source_quote") or "").strip()[:220]
    if raw.get("ref") != source["ref"] or not discovery._quote_valid(source_quote, source["text"]):
        raise ValueError("反馈引用未通过原页核验")
    return {"source_says": source_quote,
            "source_quote": source_quote,
            "ai_source_interpretation": _text(raw.get("source_says"), 900),
            "user_inference": _text(raw.get("user_inference"), 900),
            "ai_suggestion": _text(raw.get("ai_suggestion"), 900),
            "remaining_doubt": str(raw.get("remaining_doubt") or "").strip()[:500]}


def submit_answer(db: Session, turn: ResearchItem, answer: str) -> dict:
    detail = json.loads(turn.detail_json)
    if turn.kind != "coach_turn" or detail.get("answer"):
        raise HTTPException(409, "该回合已经回答")
    detail["answer"] = answer.strip()
    return archive.update_item(db, turn, db.get(ResearchScopeSnapshot, turn.snapshot_id), {"detail": detail})


def persist_feedback(db: Session, snapshot: ResearchScopeSnapshot, turn: ResearchItem,
                     feedback: dict) -> dict:
    detail = json.loads(turn.detail_json)
    if not detail.get("answer") or detail.get("feedback"):
        raise HTTPException(409, "该回合没有待评价的回答")
    evidence = db.scalar(select(ResearchEvidence).where(ResearchEvidence.item_id == turn.id))
    if not evidence:
        raise HTTPException(409, "陪练原页已失效")
    discovery._source_is_current(db, snapshot, evidence.book_id, evidence.chunk_id,
                                 feedback["source_quote"], evidence.page_start)
    detail["feedback"] = feedback
    # AI feedback remains a proposal; the user can revise their judgment separately.
    before = archive._item_state(turn)
    turn.detail_json = archive._json(detail)
    turn.updated_at = datetime.now()
    db.add(ResearchRevision(item_id=turn.id, before_json=archive._json(before),
        after_json=archive._json(archive._item_state(turn)),
        trigger_evidence_id=evidence.id, actor="ai"))
    db.commit()
    return archive.item_row(db, turn, snapshot)

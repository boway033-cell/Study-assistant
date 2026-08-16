"""学习计划与目标：设定考试日期 → 倒推每日任务 → 按掌握度分配 → 打卡日历。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import CheckIn, KnowledgeNode, StudyPlan

router = APIRouter(prefix="/api/plan", tags=["plan"])

_MASTERY_ORDER = {"unknown": 0, "fuzzy": 1, "known": 2}


class PlanReq(BaseModel):
    name: str = "学习计划"
    exam_date: str  # YYYY-MM-DD


class CheckInReq(BaseModel):
    date: str  # YYYY-MM-DD
    content: str = ""
    done: int = 1


def _get_plan(db: Session) -> StudyPlan | None:
    return db.scalar(select(StudyPlan).order_by(StudyPlan.id.desc()).limit(1))


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")


@router.get("")
def get_plan(db: Session = Depends(get_db)):
    plan = _get_plan(db)
    if not plan:
        return {"plan": None, "days": [], "total_days": 0, "node_total": 0}
    exam = _parse_date(plan.exam_date)
    today = date.today()
    total_days = max(0, (exam - today).days + 1)

    nodes = db.scalars(select(KnowledgeNode)).all()
    nodes.sort(key=lambda n: (_MASTERY_ORDER.get(n.mastery, 0), n.order_index, n.id))
    checked = {c.date for c in db.scalars(select(CheckIn).where(CheckIn.plan_id == plan.id)).all()}

    days = []
    per_day = max(1, (len(nodes) + total_days - 1) // total_days) if total_days else 0
    for d in range(total_days):
        day_nodes = nodes[d * per_day:(d + 1) * per_day]
        ds = (today + timedelta(days=d)).isoformat()
        days.append({
            "date": ds,
            "nodes": [{"id": n.id, "title": n.title, "mastery": n.mastery, "node_type": n.node_type} for n in day_nodes],
            "checked": ds in checked,
        })
    return {
        "plan": {"id": plan.id, "name": plan.name, "exam_date": plan.exam_date},
        "days": days,
        "total_days": total_days,
        "node_total": len(nodes),
    }


@router.post("")
def upsert_plan(req: PlanReq, db: Session = Depends(get_db)):
    _parse_date(req.exam_date)
    plan = _get_plan(db)
    if plan:
        plan.name = req.name
        plan.exam_date = req.exam_date
    else:
        plan = StudyPlan(name=req.name, exam_date=req.exam_date)
        db.add(plan)
    db.commit()
    db.refresh(plan)
    return {"id": plan.id, "name": plan.name, "exam_date": plan.exam_date}


@router.post("/checkin")
def checkin(req: CheckInReq, db: Session = Depends(get_db)):
    plan = _get_plan(db)
    if not plan:
        raise HTTPException(400, "请先创建学习计划")
    _parse_date(req.date)
    existing = db.scalar(select(CheckIn).where(CheckIn.plan_id == plan.id, CheckIn.date == req.date))
    if existing:
        existing.done = req.done
        existing.content = req.content
    else:
        db.add(CheckIn(plan_id=plan.id, date=req.date, content=req.content, done=req.done))
    db.commit()
    return {"ok": True}


@router.get("/checkins")
def list_checkins(db: Session = Depends(get_db)):
    plan = _get_plan(db)
    if not plan:
        return []
    rows = db.scalars(select(CheckIn).where(CheckIn.plan_id == plan.id).order_by(CheckIn.date)).all()
    return [{"date": c.date, "content": c.content, "done": c.done} for c in rows]

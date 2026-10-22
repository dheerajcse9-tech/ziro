"""After a student's answer: Eligibility Re-check Agent -> Roster Agent.

When a student supplies missing details or says YES after the placement
officer already ran the drive, the re-check decides whether they now qualify.
If so, the drive's roster workflow continues automatically: an invalidated
proposal is re-proposed (same run), or — when the roster was already sent —
a new run prepares a supplementary list. Either way the approver must approve
the new proposal; nothing is sent to the company without that.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.db.base import utcnow
from app.models.entities import AgentRun, Drive, Student, StudentRequest, User
from app.models.enums import ActorType, RunStatus
from app.services.outreach import recheck_student

ACTOR = "agent:recheck"
_IN_PROGRESS = (RunStatus.VALIDATING_INPUTS, RunStatus.RETRIEVING_EVIDENCE, RunStatus.INTERPRETING_CRITERIA,
                RunStatus.EVALUATING_ROSTER, RunStatus.CHECKING_CONFLICTS, RunStatus.EXECUTING)


def refresh_if_qualifies(db: Session, drive: Drive, student: Student, reason: str) -> dict[str, Any]:
    from app.agent import graph
    from app.agent.planner import FORCE_DETERMINISTIC

    check = recheck_student(db, student, drive)
    db.commit()
    if not check["now_qualifies"]:
        return {"recheck": check, "action": "none"}
    if drive.starts_at <= utcnow():
        return {"recheck": check, "action": "none", "note": "drive already started"}
    last = db.scalar(select(AgentRun).where(AgentRun.drive_id == drive.id, AgentRun.intent == "PREPARE_ROSTER",
                                            AgentRun.is_test_fixture.is_(False)).order_by(AgentRun.id.desc()).limit(1))
    if last is None:
        return {"recheck": check, "action": "none", "note": "the placement officer has not run this drive yet"}
    if last.status in _IN_PROGRESS:
        return {"recheck": check, "action": "none", "note": f"run #{last.id} is in progress"}
    operator = db.get(User, last.created_by_id)
    token = FORCE_DETERMINISTIC.set(True)  # the drive is known; no AI call needed
    try:
        if last.status == RunStatus.NEEDS_REVIEW and (last.pending_question or {}).get("type") == "approval_invalidated":
            run = graph.answer(db, last, operator, "rerun")
            action = "re-proposed"
        elif last.status in (RunStatus.AWAITING_APPROVAL,):
            return {"recheck": check, "action": "none", "note": "the pending proposal is still valid"}
        else:
            run = graph.start_run(db, f"Prepare the eligible roster for drive #{drive.id} ({reason})", operator)
            action = "new run"
    finally:
        FORCE_DETERMINISTIC.reset(token)
    ledger.record(db, actor_type=ActorType.AGENT, actor_id=ACTOR, action="ROSTER_REFRESH_REQUESTED", entity_type="drive",
                  entity_id=drive.id, drive_id=drive.id, run_id=run.id,
                  details={"roll_number": student.roll_number, "reason": reason, "action": action, "run_status": run.status})
    db.commit()
    return {"recheck": check, "action": action, "run_id": run.id, "run_status": run.status}


def after_student_answer(request_id: int) -> None:
    """Background task (own session) after a public form submission."""
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        req = db.get(StudentRequest, request_id)
        if req is None or req.drive_id is None:
            return
        try:
            refresh_if_qualifies(db, req.drive, req.student, f"{req.student.roll_number} answered form #{req.id}")
        except Exception:  # noqa: BLE001 - never surfaces to the student; recorded for officers
            db.rollback()
            import logging

            logging.getLogger("placementos.recheck").exception("Roster refresh failed for request %s", request_id)

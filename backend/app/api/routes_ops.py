"""Agent runs, approvals, uploads, verification, attendance, exceptions, tests, audit, settings."""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import graph
from app.api import serializers as S
from app.api.deps import ANY_ROLE, APPROVER_ROLES, OFFICER_ROLES, require_roles
from app.audit import ledger
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.db.base import utcnow
from app.db.session import get_db
from app.integrations.drive_form import DestinationUnreadable
from app.integrations.registry import adapter_for, mock_admin
from app.models.entities import (
    AgentEvent,
    AgentRun,
    ApprovalRequest,
    AttendanceSheet,
    AuditEvent,
    Destination,
    ExceptionItem,
    SourceEvidence,
    TestRun,
    UploadOperation,
    User,
    VerificationResult,
)
from app.models.enums import ActorType, ExceptionKind, ExceptionStatus, Role
from app.services import app_settings, approvals, attendance, outreach, test_lab, uploads, verification
from app.services import drives as drive_svc
from app.services.seed import reset_database, seed

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- agent

class RunIn(BaseModel):
    objective: str = Field(min_length=3, max_length=2000)


class AnswerIn(BaseModel):
    value: Any


@router.post("/agent/runs", tags=["agent"], status_code=201)
def create_run(body: RunIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    run = graph.start_run(db, body.objective, user)
    return S.run(run)


@router.get("/agent/runs", tags=["agent"])
def list_runs(status: str | None = None, drive_id: int | None = None, limit: int = Query(50, le=200),
              db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    stmt = select(AgentRun).where(AgentRun.is_test_fixture.is_(False)).order_by(AgentRun.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    if drive_id:
        stmt = stmt.where(AgentRun.drive_id == drive_id)
    return [S.run(r, include_state=False) for r in db.scalars(stmt)]


@router.get("/agent/runs/{run_id}", tags=["agent"])
def get_run(run_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    run = graph.get_run(db, run_id)
    evidence = [{"id": e.id, "kind": e.kind, "source_url": e.source_url, "sha256": e.sha256,
                 "retrieved_at": S.iso(e.retrieved_at), "content": e.content}
                for e in db.scalars(select(SourceEvidence).where(SourceEvidence.run_id == run.id))]
    return {**S.run(run), "evidence": evidence}


@router.get("/agent/runs/{run_id}/events", tags=["agent"])
def run_events(run_id: int, after_seq: int = 0, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    graph.get_run(db, run_id)
    events = db.scalars(select(AgentEvent).where(AgentEvent.run_id == run_id, AgentEvent.seq > after_seq).order_by(AgentEvent.seq))
    return [S.event(e) for e in events]


@router.post("/agent/runs/{run_id}/answer", tags=["agent"])
def answer_run(run_id: int, body: AnswerIn, db: Session = Depends(get_db),
               user: User = Depends(require_roles(*OFFICER_ROLES, Role.COORDINATOR, Role.APPROVER))):
    run = graph.answer(db, graph.get_run(db, run_id), user, body.value)
    return S.run(run)


@router.get("/agent/tools", tags=["agent"])
def tool_catalog(_: User = Depends(require_roles(*ANY_ROLE))):
    from app.agent.tools import LLM_ALLOWED_TOOLS, TOOLS

    return [{"name": t.name, "description": t.description, "permission": t.permission,
             "llm_callable": t.name in LLM_ALLOWED_TOOLS, "input_schema": t.json_schema()} for t in TOOLS.values()]


# --------------------------------------------------------------------------- approvals

class ApproveIn(BaseModel):
    proposal_hash: str = Field(min_length=64, max_length=64, description="Hash of the proposal the approver reviewed")
    note: str | None = Field(default=None, max_length=1000)


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


@router.get("/approvals", tags=["approvals"])
def list_approvals(status: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.id.desc())
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    return [S.approval(a, a.decided_by, with_proposal=False) for a in db.scalars(stmt)]


@router.get("/approvals/{approval_id}", tags=["approvals"])
def get_approval(approval_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    from app.services.roster import evaluate_drive

    a = approvals.get_approval(db, approval_id)
    live_hash, live_error = None, None
    try:
        live_hash = evaluate_drive(db, a.proposal.drive).proposal_hash()
    except Exception as exc:  # noqa: BLE001
        live_error = str(exc)
    return {**S.approval(a, a.decided_by), "live_proposal_hash": live_hash, "live_error": live_error,
            "is_current": live_hash == a.proposal_hash}


@router.post("/approvals/{approval_id}/approve", tags=["approvals"])
def approve(approval_id: int, body: ApproveIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*APPROVER_ROLES))):
    a = approvals.approve(db, approval_id, user, expected_hash=body.proposal_hash, note=body.note)
    db.commit()
    run = graph.resume_after_approval(db, a)  # executes upload + verification in this request
    db.refresh(a)
    return {"approval": S.approval(a, a.decided_by), "run": S.run(run) if run else None}


@router.post("/approvals/{approval_id}/reject", tags=["approvals"])
def reject(approval_id: int, body: RejectIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*APPROVER_ROLES))):
    a = approvals.reject(db, approval_id, user, reason=body.reason)
    db.commit()
    graph.mark_rejected(db, a)
    return S.approval(a, a.decided_by)


@router.post("/approvals/{approval_id}/execute", tags=["approvals"])
def execute_manual(approval_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    """Execute an approved proposal that has no agent run attached (or whose run stopped)."""
    op = uploads.execute(db, approval_id, actor=f"user:{user.id}")
    return S.upload(op, verification.latest_verification(db, op.id))


# --------------------------------------------------------------------------- uploads & verification

@router.get("/uploads", tags=["uploads"])
def list_uploads(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    return [S.upload(u, verification.latest_verification(db, u.id))
            for u in db.scalars(select(UploadOperation).order_by(UploadOperation.id.desc()))]


@router.get("/uploads/{upload_id}", tags=["uploads"])
def get_upload(upload_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    op = uploads.get_upload(db, upload_id)
    history = [S.verification(v) for v in db.scalars(select(VerificationResult).where(VerificationResult.upload_id == op.id)
                                                     .order_by(VerificationResult.id.desc()))]
    sheets = [S.sheet(s) for s in db.scalars(select(AttendanceSheet).where(AttendanceSheet.upload_id == op.id))]
    return {**S.upload(op, verification.latest_verification(db, op.id)), "verifications": history, "attendance_sheets": sheets}


@router.post("/uploads/{upload_id}/verify", tags=["uploads"])
def verify_upload(upload_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    v = verification.verify(db, upload_id, actor=f"user:{user.id}")
    if v.status == "VERIFIED" and not db.scalar(select(AttendanceSheet).where(AttendanceSheet.upload_id == upload_id)):
        attendance.generate(db, upload_id, actor=f"user:{user.id}")
    if v.status == "VERIFIED":
        outreach.notify_upload(db, uploads.get_upload(db, upload_id), f"user:{user.id}")
    graph.sync_after_upload_action(db, upload_id)
    return S.verification(v)


@router.post("/uploads/{upload_id}/retry", tags=["uploads"])
def retry_upload(upload_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    """Reconcile-then-retry an UNCERTAIN/FAILED upload. Never creates a duplicate submission."""
    op = uploads.retry(db, upload_id, actor=f"user:{user.id}")
    if op.status == "SUBMITTED":
        v = verification.verify(db, op.id, actor=f"user:{user.id}")
        if v.status == "VERIFIED":
            attendance.generate(db, op.id, actor=f"user:{user.id}")
            outreach.notify_upload(db, op, f"user:{user.id}")
        graph.sync_after_upload_action(db, op.id)
    return S.upload(op, verification.latest_verification(db, op.id))


@router.get("/uploads/{upload_id}/receipt", tags=["uploads"])
def upload_receipt(upload_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    op = uploads.get_upload(db, upload_id)
    v = verification.latest_verification(db, op.id)
    receipt = verification.build_receipt(db, op, v)
    return {**receipt, "receipt_hash": v.receipt_hash if v else None, "generated_at": utcnow().isoformat()}


# --------------------------------------------------------------------------- attendance

class AttendanceIn(BaseModel):
    upload_id: int


@router.get("/attendance", tags=["attendance"])
def list_sheets(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    out = []
    for s in db.scalars(select(AttendanceSheet).order_by(AttendanceSheet.id.desc())):
        item = S.sheet(s)
        item["drive_title"] = s.upload_id and db.get(UploadOperation, s.upload_id).drive.title
        out.append(item)
    return out


@router.post("/attendance", tags=["attendance"], status_code=201)
def create_sheet(body: AttendanceIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    return S.sheet(attendance.generate(db, body.upload_id, actor=f"user:{user.id}"))


@router.get("/attendance/{sheet_id}/download", tags=["attendance"])
def download_sheet(sheet_id: int, format: str = Query("csv", pattern="^(csv|xlsx)$"), db: Session = Depends(get_db),
                   user: User = Depends(require_roles(*OFFICER_ROLES, Role.APPROVER))):
    sheet = attendance.get_sheet(db, sheet_id)
    if sheet.content_csv is None:
        raise HTTPException(status_code=410, detail="Sheet content purged under the retention policy")
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="ATTENDANCE_DOWNLOADED",
                  entity_type="attendance_sheet", entity_id=sheet.id, drive_id=sheet.drive_id, details={"format": format})
    db.commit()
    if format == "xlsx":
        return Response(attendance.to_xlsx(sheet), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={sheet.file_name.replace('.csv', '.xlsx')}"})
    return Response(sheet.content_csv, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={sheet.file_name}"})


# --------------------------------------------------------------------------- exceptions

class ResolveIn(BaseModel):
    resolution: str = Field(pattern="^(PREFER_DRIVE|ACKNOWLEDGED|DISMISSED)$")
    note: str | None = Field(default=None, max_length=1000)
    preferred_drive_id: int | None = None


@router.get("/exceptions", tags=["exceptions"])
def list_exceptions(status: str | None = "OPEN", kind: str | None = None, drive_id: int | None = None,
                    db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    stmt = select(ExceptionItem).order_by(ExceptionItem.id.desc())
    if status and status != "ALL":
        stmt = stmt.where(ExceptionItem.status == status)
    if kind:
        stmt = stmt.where(ExceptionItem.kind == kind)
    if drive_id:
        stmt = stmt.where(ExceptionItem.drive_id == drive_id)
    items = list(db.scalars(stmt))
    counts = dict(db.execute(select(ExceptionItem.kind, func.count(ExceptionItem.id))
                             .where(ExceptionItem.status == ExceptionStatus.OPEN).group_by(ExceptionItem.kind)).all())
    return {"items": [S.exception(e) for e in items], "open_counts_by_kind": counts}


@router.post("/exceptions/{exception_id}/resolve", tags=["exceptions"])
def resolve_exception(exception_id: int, body: ResolveIn, db: Session = Depends(get_db),
                      user: User = Depends(require_roles(*OFFICER_ROLES))):
    exc = db.get(ExceptionItem, exception_id)
    if exc is None:
        raise NotFound("Exception not found")
    if body.resolution == "PREFER_DRIVE":
        if body.preferred_drive_id is None:
            raise ValidationFailed("preferred_drive_id is required")
        exc = drive_svc.resolve_conflict(db, exception_id, body.preferred_drive_id, body.note, user)
    else:
        if exc.kind == ExceptionKind.SCHEDULE_CONFLICT:
            raise ValidationFailed("Schedule conflicts must be resolved by choosing a drive")
        if exc.status != ExceptionStatus.OPEN:
            raise HTTPException(status_code=409, detail="Exception is not open")
        if not body.note:
            raise ValidationFailed("A note is required to acknowledge or dismiss an exception")
        exc.status = ExceptionStatus.RESOLVED if body.resolution == "ACKNOWLEDGED" else ExceptionStatus.DISMISSED
        exc.resolution, exc.resolution_note, exc.resolved_by_id, exc.resolved_at = body.resolution, body.note, user.id, utcnow()
        ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="EXCEPTION_" + body.resolution,
                      entity_type="exception", entity_id=exc.id, drive_id=exc.drive_id,
                      details={"kind": exc.kind, "note": body.note})
    db.commit()
    return S.exception(exc)


# --------------------------------------------------------------------------- test lab

class TestRunIn(BaseModel):
    test_ids: list[str] = Field(default_factory=lambda: list(test_lab.SCENARIOS))


_test_lock = threading.Lock()


@router.get("/tests", tags=["test-lab"])
def list_tests(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    out = []
    for tid, (title, description, _fn) in test_lab.SCENARIOS.items():
        latest = db.scalar(select(TestRun).where(TestRun.test_id == tid).order_by(TestRun.id.desc()).limit(1))
        count = db.scalar(select(func.count(TestRun.id)).where(TestRun.test_id == tid)) or 0
        out.append({"test_id": tid, "title": title, "description": description, "runs": count,
                    "latest": S.test_run(latest) if latest else None})
    return {"environment": test_lab.ENVIRONMENT, "tests": out}


@router.post("/tests/run", tags=["test-lab"])
def run_tests(body: TestRunIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    unknown = [t for t in body.test_ids if t not in test_lab.SCENARIOS]
    if unknown:
        raise ValidationFailed(f"Unknown tests: {unknown}")
    if not _test_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A test run is already in progress")
    try:
        return [S.test_run(test_lab.run_scenario(db, t, user)) for t in body.test_ids]
    finally:
        _test_lock.release()


@router.get("/tests/runs", tags=["test-lab"])
def test_history(test_id: str | None = None, limit: int = Query(50, le=200), db: Session = Depends(get_db),
                 _: User = Depends(require_roles(*ANY_ROLE))):
    stmt = select(TestRun).order_by(TestRun.id.desc()).limit(limit)
    if test_id:
        stmt = stmt.where(TestRun.test_id == test_id)
    return [S.test_run(t) for t in db.scalars(stmt)]


@router.get("/tests/runs.csv", tags=["test-lab"])
def test_history_csv(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    runs = list(db.scalars(select(TestRun).order_by(TestRun.id.desc())))
    return Response(test_lab.export_csv(runs), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=placementos_test_evidence.csv"})


# --------------------------------------------------------------------------- audit & evidence

@router.get("/audit", tags=["audit"])
def audit_log(action: str | None = None, actor_type: str | None = None, drive_id: int | None = None, run_id: int | None = None,
              q: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
              db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if actor_type:
        stmt = stmt.where(AuditEvent.actor_type == actor_type)
    if drive_id:
        stmt = stmt.where(AuditEvent.drive_id == drive_id)
    if run_id:
        stmt = stmt.where(AuditEvent.run_id == run_id)
    if q:
        stmt = stmt.where(AuditEvent.actor_id.ilike(f"%{q}%") | AuditEvent.operation_id.ilike(f"%{q}%") | AuditEvent.action.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(AuditEvent.seq.desc()).offset((page - 1) * page_size).limit(page_size))
    actions = [a for (a,) in db.execute(select(AuditEvent.action).distinct().order_by(AuditEvent.action))]
    return {"items": [S.audit(a) for a in items], "total": total, "page": page, "page_size": page_size, "actions": actions}


@router.get("/audit/verify", tags=["audit"])
def audit_verify(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    return ledger.verify_chain(db)


@router.get("/evidence/{evidence_id}", tags=["audit"])
def get_evidence(evidence_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    e = db.get(SourceEvidence, evidence_id)
    if e is None:
        raise NotFound("Evidence not found")
    return {"id": e.id, "kind": e.kind, "drive_id": e.drive_id, "run_id": e.run_id, "source_url": e.source_url,
            "sha256": e.sha256, "retrieved_at": S.iso(e.retrieved_at), "content": e.content}


# --------------------------------------------------------------------------- settings, integrations, admin

class SettingsPatch(BaseModel):
    institution_name: str | None = None
    timezone: str | None = None
    schedule_buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    attendance_retention_days: int | None = Field(default=None, ge=1, le=3650)
    attendance_fields: list[str] | None = None
    auto_apply_student_data: bool | None = None
    reminder_hours_before_deadline: int | None = Field(default=None, ge=1, le=168)
    reminder_hours_before_drive: int | None = Field(default=None, ge=1, le=168)


@router.get("/settings", tags=["settings"])
def get_app_settings(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    return app_settings.get_all(db)


@router.patch("/settings", tags=["settings"])
def patch_settings(body: SettingsPatch, db: Session = Depends(get_db), user: User = Depends(require_roles(Role.ADMIN))):
    from app.domain.schedule import validate_timezone
    from app.services.approvals import revalidate_open_approvals

    data = body.model_dump(exclude_unset=True)
    if "timezone" in data:
        try:
            validate_timezone(data["timezone"])
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
    if "attendance_fields" in data:
        bad = [f for f in data["attendance_fields"] if f not in attendance.ALLOWED_FIELDS]
        if bad:
            raise ValidationFailed(f"Fields not allowed on attendance sheets: {bad}")
    for k, v in data.items():
        app_settings.set_value(db, k, v)
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="SETTINGS_UPDATED", details=data)
    if "schedule_buffer_minutes" in data:
        revalidate_open_approvals(db, "Schedule buffer setting changed")
    db.commit()
    return app_settings.get_all(db)


@router.get("/users", tags=["settings"])
def list_users(db: Session = Depends(get_db),
               _: User = Depends(require_roles(Role.ADMIN, Role.OFFICER, Role.APPROVER, Role.COORDINATOR))):
    return [S.user(u) for u in db.scalars(select(User).order_by(User.id))]


@router.get("/integrations", tags=["integrations"])
def integrations(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    settings = get_settings()
    out = []
    for d in db.scalars(select(Destination).order_by(Destination.id)):
        status, detail = "unknown", None
        try:
            form = adapter_for(d).describe_form(d.form_ref)
            status, detail = ("reachable" if form.is_open else "form_closed"), form.drive_title
        except DestinationUnreadable as exc:
            status, detail = "unreachable", str(exc)
        except Exception as exc:  # noqa: BLE001 - surfaced to the officer
            status, detail = "blocked", getattr(exc, "message", str(exc))
        out.append({**S.destination(d), "status": status, "detail": detail})
    return {
        "official_organiser_target": {"available": False,
                                      "note": "No organiser mock-form URL or credentials were provided to this deployment. "
                                              "All destinations are the clearly labelled LOCAL MOCK portal."},
        "destinations": out,
        "allowed_destination_hosts": settings.allowed_destination_hosts,
        "llm": {"enabled": settings.llm_enabled, "provider": settings.active_llm, "model": settings.active_llm_model,
                "mode": (f"{'Gemini' if settings.active_llm == 'gemini' else 'Claude'} reads goals and company emails"
                         if settings.llm_enabled else "Rule-based fallback (no AI key configured)")},
        "mock_portal_base_url": settings.mock_portal_base_url,
        "email": _email_status(db),
        "demo_mode": settings.demo_mode,
    }


def _email_status(db: Session) -> dict:
    from app.integrations import gmail
    from app.services.mailer import transport_name

    transport = transport_name(db)
    notes = {"GMAIL_API": f"Emails are sent from {gmail.account_email(db)} through Gmail (Google sign-in)",
             "SMTP": "Emails are delivered through the configured SMTP server",
             "LOCAL_MAILBOX": "No mailbox connected: emails are kept in the local mailbox (Communications page)"}
    return {"transport": transport, "note": notes[transport]}


class FaultIn(BaseModel):
    form_ref: str
    mode: str
    remaining: int = Field(default=1, ge=0, le=10)
    extra: dict = Field(default_factory=dict)


@router.post("/integrations/ai-check", tags=["integrations"])
def ai_check(_: User = Depends(require_roles(Role.ADMIN))):
    """Sends Gemini one tiny prompt and times it, so a slow model or a bad key shows up in plain words."""
    import time

    from app.agent import gemini

    settings = get_settings()
    if settings.active_llm != "gemini":
        return {"ok": False, "model": None, "seconds": None, "error": "no Gemini key is configured", "detail": "",
                "fallbacks": [], "available": []}
    client = gemini.GeminiClient()
    started = time.monotonic()
    try:
        gemini.generate_text(client, "Answer in one word.", "Reply with OK.")
        return {"ok": True, "model": client.used_model, "seconds": round(time.monotonic() - started, 1),
                "error": None, "detail": "", "fallbacks": client.fallbacks, "available": []}
    except Exception as exc:  # noqa: BLE001 - shown to the admin
        seconds = round(time.monotonic() - started, 1)
        try:
            available = [m for m in gemini.list_models(client) if "flash" in m]
        except Exception:  # noqa: BLE001 - the list is only a hint
            available = []
        return {"ok": False, "model": settings.gemini_model, "seconds": seconds, "error": gemini.friendly_error(exc),
                "detail": str(exc)[:300], "fallbacks": client.fallbacks, "available": available[:12]}


@router.post("/integrations/ai-speed", tags=["integrations"])
def ai_speed(_: User = Depends(require_roles(Role.ADMIN))):
    """Times one tiny request on each flash model this key can use (in parallel), fastest first."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from app.agent import gemini

    if get_settings().active_llm != "gemini":
        return {"results": [], "error": "no Gemini key is configured"}
    names = [m for m in gemini.list_models(gemini.GeminiClient()) if "flash" in m and "image" not in m and "tts" not in m
             and "audio" not in m and "live" not in m][:10]

    def timed(name: str) -> dict:
        client = gemini.GeminiClient(model=name, timeout=30)
        client.fallbacks = []
        started = time.monotonic()
        try:
            gemini.generate_text(client, "Answer in one word.", "Reply with OK.")
            return {"model": name, "ok": True, "seconds": round(time.monotonic() - started, 1), "error": None}
        except Exception as exc:  # noqa: BLE001 - shown to the admin
            return {"model": name, "ok": False, "seconds": round(time.monotonic() - started, 1),
                    "error": gemini.friendly_error(exc)}

    with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
        results = list(pool.map(timed, names))
    results.sort(key=lambda r: (not r["ok"], r["seconds"]))
    return {"results": results, "error": None if names else "could not list the models for this key"}


@router.get("/integrations/mock-portal/faults", tags=["integrations"])
def list_faults(_: User = Depends(require_roles(Role.ADMIN))):
    return mock_admin().list_faults()


@router.post("/integrations/mock-portal/faults", tags=["integrations"])
def arm_fault(body: FaultIn, db: Session = Depends(get_db), user: User = Depends(require_roles(Role.ADMIN))):
    if not get_settings().demo_mode:
        raise HTTPException(status_code=403, detail="Fault injection is only available in demo mode")
    result = mock_admin().arm_fault(body.form_ref, body.mode, body.remaining, body.extra)
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="MOCK_FAULT_ARMED", details=result)
    db.commit()
    return result


@router.post("/admin/demo-reset", tags=["settings"])
def demo_reset(db: Session = Depends(get_db), user: User = Depends(require_roles(Role.ADMIN))):
    if not get_settings().demo_mode:
        raise HTTPException(status_code=403, detail="Demo reset is disabled outside demo mode")
    actor = f"user:{user.id} ({user.email})"
    # Keep the connected Gmail account: a reset reloads the data, it should not require signing in to Google again.
    from app.models.entities import OAuthCredential

    keep = [{c.name: getattr(o, c.name) for c in OAuthCredential.__table__.columns if c.name not in ("id", "connected_by_id")}
            for o in db.scalars(select(OAuthCredential))]
    reset_database(db)
    result = seed(db)
    for values in keep:
        db.add(OAuthCredential(**values))
    ledger.record(db, actor_type=ActorType.USER, actor_id=actor, action="DEMO_RESET",
                  details={"note": "All demo data deleted and re-seeded with synthetic records", **result})
    db.commit()
    return result


@router.post("/admin/retention/purge", tags=["settings"])
def retention_purge(db: Session = Depends(get_db), user: User = Depends(require_roles(Role.ADMIN))):
    return {"purged_sheets": attendance.purge_expired(db, actor=f"user:{user.id}")}

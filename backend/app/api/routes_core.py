"""Auth, health, dashboard, students, imports, employers, destinations, drives."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.api.deps import ANY_ROLE, DRIVE_ROLES, OFFICER_ROLES, SESSION_COOKIE, current_user, require_roles
from app.audit import ledger
from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.security import create_access_token, verify_password
from app.db.base import utcnow
from app.db.session import get_db
from app.domain.eligibility import CriteriaSpec
from app.domain.normalize import BRANCH_LABELS
from app.domain.schedule import Interval, to_utc
from app.models.entities import (
    AgentRun,
    ApprovalRequest,
    ConsentRecord,
    Destination,
    Drive,
    DriveAssignment,
    DriveCriteria,
    EligibilityDecision,
    Employer,
    ExceptionItem,
    RosterImport,
    Student,
    StudentRequest,
    UploadOperation,
    User,
    VerificationResult,
)
from app.models.enums import ActorType, ApprovalStatus, DriveStatus, ExceptionStatus, ImportStatus, UploadStatus
from app.services import drives as drive_svc
from app.services import students as student_svc
from app.services.consent import current_consent
from app.services.roster import current_criteria, drive_pair_conflicts, evaluate_drive

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- auth & health

class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login", tags=["auth"])
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user.id, user.role)
    settings = get_settings()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=settings.environment == "production",
                        max_age=settings.jwt_ttl_minutes * 60, path="/")
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="USER_LOGIN", entity_type="user",
                  entity_id=user.id, details={"role": user.role})
    db.commit()
    return {"user": S.user(user), "token": token}


@router.post("/auth/logout", tags=["auth"])
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me", tags=["auth"])
def me(user: User = Depends(current_user)):
    return S.user(user)


@router.get("/auth/demo-accounts", tags=["auth"])
def demo_accounts(db: Session = Depends(get_db)):
    if not get_settings().demo_mode:
        return []
    return [{"email": u.email, "name": u.name, "role": u.role} for u in db.scalars(select(User).order_by(User.id))]


@router.get("/health", tags=["health"])
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded", "database": "ok" if db_ok else "unreachable",
        "database_dialect": db.get_bind().dialect.name, "environment": settings.environment, "demo_mode": settings.demo_mode,
        "planner": "LLM" if settings.llm_enabled else "DETERMINISTIC_FALLBACK",
        "llm_provider": settings.active_llm, "llm_model": settings.active_llm_model, "time": utcnow().isoformat(),
    }


# --------------------------------------------------------------------------- dashboard

@router.get("/dashboard", tags=["dashboard"])
def dashboard(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    now = utcnow()
    students = db.scalar(select(func.count(Student.id))) or 0
    upcoming = list(db.scalars(select(Drive).where(Drive.starts_at >= now, Drive.status.in_([DriveStatus.OPEN, DriveStatus.DRAFT, DriveStatus.ROSTER_SUBMITTED]))
                               .order_by(Drive.starts_at)))
    latest = {}
    for d in db.scalars(select(EligibilityDecision).where(EligibilityDecision.is_stale.is_(False))):
        latest.setdefault(d.drive_id, []).append(d)
    readiness = []
    for d in upcoming:
        decisions = latest.get(d.id, [])
        pending = db.scalar(select(func.count(ApprovalRequest.id)).join(ApprovalRequest.proposal)
                            .where(ApprovalRequest.status == ApprovalStatus.PENDING, ApprovalRequest.proposal.has(drive_id=d.id))) or 0
        op = db.scalar(select(UploadOperation).where(UploadOperation.drive_id == d.id).order_by(UploadOperation.id.desc()))
        ver = db.scalar(select(VerificationResult).where(VerificationResult.upload_id == op.id).order_by(VerificationResult.id.desc())) if op else None
        open_exc = db.scalar(select(func.count(ExceptionItem.id)).where(ExceptionItem.drive_id == d.id, ExceptionItem.status == ExceptionStatus.OPEN)) or 0
        stage = "Not evaluated"
        if decisions:
            stage = "Evaluated"
        if pending:
            stage = "Awaiting approval"
        if op:
            stage = f"Upload {op.status.lower()}"
        if ver:
            stage = "Verified" if ver.status == "VERIFIED" else ver.status.replace("_", " ").title()
        readiness.append({
            "drive_id": d.id, "title": d.title, "employer": d.employer.name, "starts_at": S.iso(d.starts_at),
            "timezone": d.timezone, "status": d.status, "stage": stage,
            "included": sum(x.roster_status == "INCLUDED" for x in decisions),
            "excluded": sum(x.roster_status == "EXCLUDED" for x in decisions),
            "held": sum(x.roster_status == "HELD" for x in decisions),
            "unknown": sum(x.decision == "UNKNOWN" for x in decisions),
            "open_exceptions": open_exc, "pending_approvals": pending,
        })
    all_decisions = [x for ds in latest.values() for x in ds]
    open_exc = db.scalar(select(func.count(ExceptionItem.id)).where(ExceptionItem.status == ExceptionStatus.OPEN)) or 0
    open_conflicts = db.scalar(select(func.count(ExceptionItem.id)).where(ExceptionItem.status == ExceptionStatus.OPEN,
                                                                          ExceptionItem.kind == "SCHEDULE_CONFLICT")) or 0
    pending_approvals = db.scalar(select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == ApprovalStatus.PENDING)) or 0
    ver_problem = 0
    for op in db.scalars(select(UploadOperation)):
        v = db.scalar(select(VerificationResult).where(VerificationResult.upload_id == op.id).order_by(VerificationResult.id.desc()))
        if op.status != UploadStatus.SUBMITTED or v is None or v.status != "VERIFIED":
            ver_problem += 1
    runs = list(db.scalars(select(AgentRun).where(AgentRun.is_test_fixture.is_(False)).order_by(AgentRun.id.desc()).limit(6)))
    return {
        "metrics": {
            "total_students": students,
            "upcoming_drives": len(upcoming),
            "eligible_candidates": sum(x.decision == "ELIGIBLE" for x in all_decisions),
            "included_candidates": sum(x.roster_status == "INCLUDED" for x in all_decisions),
            "excluded_candidates": sum(x.roster_status == "EXCLUDED" for x in all_decisions),
            "unresolved_exceptions": open_exc,
            "pending_approvals": pending_approvals,
            "drive_conflicts": len(drive_pair_conflicts(db)),
            "open_clash_exceptions": open_conflicts,
            "verification_attention": ver_problem,
        },
        "drive_readiness": readiness,
        "recent_runs": [S.run(r, include_state=False) for r in runs],
        "upcoming_schedule": [{"drive_id": d.id, "title": d.title, "employer": d.employer.name, "starts_at": S.iso(d.starts_at),
                               "ends_at": S.iso(d.ends_at), "timezone": d.timezone, "location": d.location} for d in upcoming[:8]],
    }


# --------------------------------------------------------------------------- students

@router.get("/students", tags=["students"])
def list_students(
    q: str | None = None, branch: str | None = None, consent: str | None = Query(None, pattern="^(granted|missing)$"),
    issues: bool | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    sort: str = Query("roll_number", pattern="^-?(roll_number|full_name|cgpa|branch|active_backlogs)$"),
    db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE)),
):
    stmt = select(Student)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Student.roll_number.ilike(like), Student.full_name.ilike(like), Student.email.ilike(like)))
    if branch:
        stmt = stmt.where(Student.branch == branch) if branch != "UNKNOWN" else stmt.where(Student.branch.is_(None))
    all_rows = list(db.scalars(stmt))
    consent_map = current_consent(db, [s.id for s in all_rows], "SHARE_WITH_EMPLOYERS")
    if consent == "granted":
        all_rows = [s for s in all_rows if consent_map.get(s.id, {}).get("granted")]
    elif consent == "missing":
        all_rows = [s for s in all_rows if not consent_map.get(s.id, {}).get("granted")]
    if issues is True:
        all_rows = [s for s in all_rows if s.data_issues or s.cgpa is None or s.active_backlogs is None or s.branch is None]
    key = sort.lstrip("-")
    all_rows.sort(key=lambda s: (getattr(s, key) is None, getattr(s, key) if getattr(s, key) is not None else 0),
                  reverse=sort.startswith("-"))
    total = len(all_rows)
    rows = all_rows[(page - 1) * page_size: page * page_size]
    from app.services.outreach import selected_employers

    offers = selected_employers(db, [s.id for s in rows])
    return {"items": [{**S.student(s, consent_map.get(s.id)), "selected_at": offers.get(s.id, [])} for s in rows],
            "total": total, "page": page, "page_size": page_size,
            "branches": [{"code": c, "label": l} for c, l in BRANCH_LABELS.items()]}


@router.get("/students/{student_id}", tags=["students"])
def get_student(student_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    s = student_svc.get_student(db, student_id)
    consent = current_consent(db, [s.id], "SHARE_WITH_EMPLOYERS").get(s.id)
    history = [{"id": c.id, "scope": c.scope, "status": c.status, "source": c.source, "evidence_ref": c.evidence_ref,
                "effective_at": S.iso(c.effective_at), "recorded_by_id": c.recorded_by_id}
               for c in db.scalars(select(ConsentRecord).where(ConsentRecord.student_id == s.id).order_by(ConsentRecord.id.desc()))]
    decisions = [{"drive_id": d.drive_id, "drive_title": db.get(Drive, d.drive_id).title, "criteria_version": d.criteria_version,
                  "decision": d.decision, "roster_status": d.roster_status, "reasons": d.reasons, "rule_results": d.rule_results,
                  "evaluated_at": S.iso(d.created_at)}
                 for d in db.scalars(select(EligibilityDecision).where(EligibilityDecision.student_id == s.id,
                                                                       EligibilityDecision.is_stale.is_(False)))]
    assignments = [{"drive_id": a.drive_id, "drive_title": a.drive.title, "status": a.status, "note": a.note,
                    "starts_at": S.iso(a.drive.starts_at), "ends_at": S.iso(a.drive.ends_at)}
                   for a in db.scalars(select(DriveAssignment).where(DriveAssignment.student_id == s.id))]
    from app.api.routes_outreach import email_dict
    from app.services import outreach

    requests = [outreach.request_dict(r) for r in db.scalars(select(StudentRequest).where(StudentRequest.student_id == s.id)
                                                            .order_by(StudentRequest.id.desc()))]
    return {**S.student(s, consent), "consent_history": history, "decisions": decisions, "assignments": assignments,
            "placement_history": outreach.placement_history(db, s), "requests": requests,
            "emails": [email_dict(m) for m in outreach.emails_for_student(db, s.id)]}


class StudentPatch(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    branch: str | None = None
    cgpa: float | None = Field(default=None, ge=0, le=10)
    active_backlogs: int | None = Field(default=None, ge=0)
    graduation_year: int | None = None


@router.patch("/students/{student_id}", tags=["students"])
def patch_student(student_id: int, body: StudentPatch, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(*OFFICER_ROLES))):
    s = student_svc.get_student(db, student_id)
    result = student_svc.update_student(db, s, body.model_dump(exclude_unset=True), user)
    db.commit()
    return {**result, "student": S.student(s, current_consent(db, [s.id], "SHARE_WITH_EMPLOYERS").get(s.id))}


class ConsentIn(BaseModel):
    granted: bool
    evidence_ref: str = Field(min_length=3, max_length=255, description="Reference to the signed form / email / record")


@router.post("/students/{student_id}/consent", tags=["students"])
def set_consent(student_id: int, body: ConsentIn, db: Session = Depends(get_db),
                user: User = Depends(require_roles(*OFFICER_ROLES))):
    s = student_svc.get_student(db, student_id)
    result = student_svc.set_consent(db, s, body.granted, body.evidence_ref, user)
    db.commit()
    return result


# --------------------------------------------------------------------------- roster imports

TEMPLATE_CSV = ("roll_number,full_name,email,branch,cgpa,active_backlogs,graduation_year,consent\n"
                "22CS001,Example Student,example@students.example.edu,CSE,7.85,0,2027,yes\n")


@router.get("/roster-imports/template.csv", tags=["imports"])
def import_template():
    return Response(TEMPLATE_CSV, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=placementos_roster_template.csv"})


@router.post("/roster-imports/preview", tags=["imports"])
async def preview_import(file: UploadFile = File(...), mapping: str | None = Form(None), db: Session = Depends(get_db),
                         user: User = Depends(require_roles(*OFFICER_ROLES))):
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")
    parsed_mapping = json.loads(mapping) if mapping else None
    headers, _ = student_svc.read_table(file.filename or "upload.csv", content)
    imp = student_svc.preview_import(db, file.filename or "upload.csv", content, parsed_mapping, user)
    db.commit()
    return {**S.roster_import(imp, include_rows=True), "headers": headers,
            "available_fields": list(student_svc.FIELD_SYNONYMS), "required_fields": list(student_svc.REQUIRED_FIELDS)}


@router.get("/roster-imports", tags=["imports"])
def list_imports(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    return [S.roster_import(i) for i in db.scalars(select(RosterImport).order_by(RosterImport.id.desc()))]


@router.get("/roster-imports/{import_id}", tags=["imports"])
def get_import(import_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    imp = db.get(RosterImport, import_id)
    if imp is None:
        raise NotFound("Import not found")
    return S.roster_import(imp, include_rows=True)


@router.post("/roster-imports/{import_id}/commit", tags=["imports"])
def commit_import(import_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    imp = student_svc.commit_import(db, import_id, user)
    db.commit()
    return S.roster_import(imp)


@router.post("/roster-imports/{import_id}/discard", tags=["imports"])
def discard_import(import_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    imp = db.get(RosterImport, import_id)
    if imp is None:
        raise NotFound("Import not found")
    if imp.status != ImportStatus.PREVIEWED:
        raise HTTPException(status_code=409, detail=f"Import is {imp.status}")
    imp.status = ImportStatus.DISCARDED
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="IMPORT_DISCARDED",
                  entity_type="roster_import", entity_id=imp.id)
    db.commit()
    return S.roster_import(imp)


@router.get("/roster-imports/{import_id}/errors.csv", tags=["imports"])
def import_errors(import_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    imp = db.get(RosterImport, import_id)
    if imp is None:
        raise NotFound("Import not found")
    return Response(student_svc.errors_csv(imp), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=import_{imp.id}_issues.csv"})


# --------------------------------------------------------------------------- employers & destinations

class EmployerIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    industry: str | None = None
    website: str | None = None
    contact_email: str | None = None


@router.get("/employers", tags=["drives"])
def list_employers(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    return [S.employer(e) for e in db.scalars(select(Employer).order_by(Employer.name))]


@router.post("/employers", tags=["drives"], status_code=201)
def create_employer(body: EmployerIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*DRIVE_ROLES))):
    e = drive_svc.create_employer(db, body.model_dump(), user)
    db.commit()
    return S.employer(e)


class DestinationIn(BaseModel):
    name: str
    base_url: str
    form_ref: str
    kind: str = "LOCAL_MOCK_FORM"
    is_enabled: bool = True


class DestinationPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    form_ref: str | None = None
    is_enabled: bool | None = None


@router.get("/destinations", tags=["integrations"])
def list_destinations(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    return [S.destination(d) for d in db.scalars(select(Destination).order_by(Destination.id))]


@router.post("/destinations", tags=["integrations"], status_code=201)
def create_destination(body: DestinationIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    d = drive_svc.upsert_destination(db, body.model_dump(), user)
    db.commit()
    return S.destination(d)


@router.patch("/destinations/{destination_id}", tags=["integrations"])
def patch_destination(destination_id: int, body: DestinationPatch, db: Session = Depends(get_db),
                      user: User = Depends(require_roles(*OFFICER_ROLES))):
    d = db.get(Destination, destination_id)
    if d is None:
        raise NotFound("Destination not found")
    d = drive_svc.upsert_destination(db, body.model_dump(exclude_unset=True), user, destination=d)
    db.commit()
    return S.destination(d)


# --------------------------------------------------------------------------- drives

class CriteriaIn(BaseModel):
    min_cgpa: float | None = Field(default=None, ge=0, le=10)
    backlog_policy: str = "NO_ACTIVE_BACKLOGS"
    max_active_backlogs: int | None = Field(default=None, ge=0)
    allowed_branches: list[str] = []
    graduation_years: list[int] = []
    required_consent_scope: str = "SHARE_WITH_EMPLOYERS"
    source_text: str | None = None
    source_reference: str | None = None
    change_note: str | None = None


class DriveIn(BaseModel):
    employer_id: int
    title: str = Field(min_length=3, max_length=200)
    role_title: str | None = None
    location: str | None = None
    starts_at: datetime = Field(description="Local wall-clock time in `timezone` (or ISO with offset)")
    ends_at: datetime
    timezone: str = "Asia/Kolkata"
    status: str = "OPEN"
    destination_id: int | None = None
    source_reference: str | None = None
    notes: str | None = None
    criteria: CriteriaIn


class DrivePatch(BaseModel):
    title: str | None = None
    role_title: str | None = None
    location: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone: str | None = None
    status: str | None = None
    destination_id: int | None = None
    source_reference: str | None = None
    notes: str | None = None


@router.get("/drives", tags=["drives"])
def list_drives(status: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    stmt = select(Drive).order_by(Drive.starts_at)
    if status:
        stmt = stmt.where(Drive.status == status)
    out = []
    for d in db.scalars(stmt):
        item = S.drive(d, current_criteria(db, d.id))
        decisions = list(db.scalars(select(EligibilityDecision).where(EligibilityDecision.drive_id == d.id,
                                                                     EligibilityDecision.is_stale.is_(False))))
        item["latest_evaluation"] = None if not decisions else {
            "included": sum(x.roster_status == "INCLUDED" for x in decisions),
            "excluded": sum(x.roster_status == "EXCLUDED" for x in decisions),
            "held": sum(x.roster_status == "HELD" for x in decisions),
            "evaluated_at": S.iso(max(x.created_at for x in decisions)),
        }
        out.append(item)
    return out


@router.post("/drives", tags=["drives"], status_code=201)
def create_drive(body: DriveIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*DRIVE_ROLES))):
    data = body.model_dump()
    data["criteria"] = body.criteria.model_dump()
    d = drive_svc.create_drive(db, data, user)
    db.commit()
    return S.drive(d, current_criteria(db, d.id))


@router.get("/drives/{drive_id}", tags=["drives"])
def get_drive(drive_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    d = drive_svc.get_drive(db, drive_id)
    runs = [S.run(r, include_state=False) for r in db.scalars(select(AgentRun).where(AgentRun.drive_id == d.id)
                                                              .order_by(AgentRun.id.desc()).limit(10))]
    approvals = [S.approval(a, with_proposal=False) for a in db.scalars(
        select(ApprovalRequest).join(ApprovalRequest.proposal).where(ApprovalRequest.proposal.has(drive_id=d.id))
        .order_by(ApprovalRequest.id.desc()))]
    uploads = [S.upload(u) for u in db.scalars(select(UploadOperation).where(UploadOperation.drive_id == d.id)
                                                 .order_by(UploadOperation.id.desc()))]
    from app.models.entities import InboundEmail
    from app.services import consent_forms

    source = db.scalar(select(InboundEmail).where(InboundEmail.drive_id == d.id).limit(1))
    return {**S.drive(d, current_criteria(db, d.id)), "runs": runs, "approvals": approvals, "uploads": uploads,
            "consent_form": consent_forms.summary(db, d.id),
            "source_email": None if source is None else {"id": source.id, "from_address": source.from_address,
                                                          "subject": source.subject}}


@router.patch("/drives/{drive_id}", tags=["drives"])
def patch_drive(drive_id: int, body: DrivePatch, db: Session = Depends(get_db), user: User = Depends(require_roles(*DRIVE_ROLES))):
    d = drive_svc.get_drive(db, drive_id)
    data = body.model_dump(exclude_unset=True)  # naive datetimes = wall-clock in the drive's timezone
    result = drive_svc.update_drive(db, d, data, user)
    db.commit()
    return {**result, "drive": S.drive(d, current_criteria(db, d.id))}


@router.get("/drives/{drive_id}/criteria", tags=["drives"])
def criteria_versions(drive_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    drive_svc.get_drive(db, drive_id)
    return [S.criteria(c) for c in db.scalars(select(DriveCriteria).where(DriveCriteria.drive_id == drive_id)
                                              .order_by(DriveCriteria.version.desc()))]


@router.post("/drives/{drive_id}/criteria", tags=["drives"], status_code=201)
def add_criteria(drive_id: int, body: CriteriaIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*DRIVE_ROLES))):
    d = drive_svc.get_drive(db, drive_id)
    row = drive_svc.add_criteria_version(db, d, body.model_dump(), user)
    db.commit()
    return S.criteria(row)


@router.get("/drives/{drive_id}/eligibility", tags=["eligibility"])
def drive_eligibility(drive_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    """Live, read-only evaluation of the current roster against current criteria."""
    d = drive_svc.get_drive(db, drive_id)
    ev = evaluate_drive(db, d)
    proposal_hash = None
    try:
        proposal_hash = ev.proposal_hash()
    except Exception:  # noqa: BLE001 - no destination yet
        pass
    return {"drive_id": d.id, "criteria": ev.criteria.as_dict(), "counts": ev.counts(),
            "exclusion_reasons": ev.exclusion_reason_summary(), "live_proposal_hash": proposal_hash,
            "evaluated_at": utcnow().isoformat(), "students": [o.public() for o in ev.outcomes]}


class SimulateIn(BaseModel):
    min_cgpa: float | None = Field(default=None, ge=0, le=10)
    backlog_policy: str | None = None
    max_active_backlogs: int | None = Field(default=None, ge=0)
    allowed_branches: list[str] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


@router.post("/drives/{drive_id}/simulate", tags=["eligibility"])
def simulate(drive_id: int, body: SimulateIn, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    from app.agent.tools import simulate as run_simulation
    from app.domain.normalize import normalize_branch

    d = drive_svc.get_drive(db, drive_id)
    row = current_criteria(db, d.id)
    if row is None:
        raise HTTPException(status_code=412, detail="Drive has no criteria")
    policy = body.backlog_policy or row.backlog_policy
    max_b = body.max_active_backlogs if body.max_active_backlogs is not None else row.max_active_backlogs
    if policy == "NO_ACTIVE_BACKLOGS":
        max_b = 0
    elif policy == "NOT_CONSIDERED":
        max_b = None
    elif max_b is None:
        raise HTTPException(status_code=422, detail="max_active_backlogs required for MAX_ALLOWED")
    branches = tuple(sorted(row.allowed_branches or []))
    if body.allowed_branches is not None:
        branches = tuple(sorted({b for b in (normalize_branch(x) for x in body.allowed_branches) if b}))
    proposed = CriteriaSpec(version=row.version, min_cgpa=body.min_cgpa if body.min_cgpa is not None else row.min_cgpa,
                            backlog_policy=policy, max_active_backlogs=max_b, allowed_branches=branches,
                            graduation_years=tuple(row.graduation_years or []), required_consent_scope=row.required_consent_scope)
    interval = None
    if body.starts_at or body.ends_at:
        s = to_utc(body.starts_at, d.timezone) if body.starts_at else d.starts_at
        e = to_utc(body.ends_at, d.timezone) if body.ends_at else d.ends_at
        if e <= s:
            raise HTTPException(status_code=422, detail="Simulated end must be after start")
        interval = Interval(d.id, s, e)
    return run_simulation(db, d, proposed, interval)


@router.get("/drives/{drive_id}/conflicts", tags=["conflicts"])
def drive_conflicts(drive_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    d = drive_svc.get_drive(db, drive_id)
    ev = evaluate_drive(db, d)
    return {
        "interval_rule": "half-open [start, end) compared in UTC",
        "overlapping_drives": [p for p in drive_pair_conflicts(db) if d.id in (p["drive_a"]["id"], p["drive_b"]["id"])],
        "students": [{"student_id": o.student_id, "roll_number": o.roll_number, "full_name": o.full_name,
                      "roster_status": o.roster_status, "decision": o.decision, "conflicts": o.conflicts}
                     for o in ev.outcomes if o.conflicts],
    }


@router.get("/conflicts", tags=["conflicts"])
def all_conflicts(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    exc = [S.exception(e) for e in db.scalars(select(ExceptionItem).where(ExceptionItem.kind == "SCHEDULE_CONFLICT")
                                               .order_by(ExceptionItem.status, ExceptionItem.id.desc()))]
    return {"drive_pairs": drive_pair_conflicts(db), "student_conflicts": exc}


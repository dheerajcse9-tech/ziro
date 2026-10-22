"""Student and employer communication: drive invitations, missing-data requests,
notifications after a verified upload, and selection results.

Students answer through a personal, expiring link (`/respond/<token>`). Data a
student submits is never written to their record directly: it waits for an
officer to verify and apply it.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.db.base import utcnow
from app.domain.normalize import BRANCH_LABELS, normalize_branch, parse_backlogs, parse_cgpa, parse_year
from app.domain.schedule import to_local
from app.models.entities import (
    Drive,
    DriveAssignment,
    DriveResult,
    EmailMessage,
    Student,
    StudentRequest,
    UploadOperation,
    User,
)
from app.models.enums import (
    ActorType,
    AssignmentStatus,
    Decision,
    DriveOutcome,
    EmailCategory,
    ExceptionKind,
    ExceptionStatus,
    RequestKind,
    RequestStatus,
    Severity,
    consent_scope_key,
)
from app.services import exceptions, mailer
from app.services.roster import current_criteria, evaluate_drive

RULE_TO_FIELD = {"CGPA": "cgpa", "BACKLOGS": "active_backlogs", "BRANCH": "branch", "GRADUATION_YEAR": "graduation_year"}
FIELD_LABEL = {"cgpa": "CGPA", "active_backlogs": "Number of active backlogs", "branch": "Branch",
               "graduation_year": "Graduation year", "consent": "Consent for this drive"}
DATA_FIELDS = ("cgpa", "active_backlogs", "branch", "graduation_year")
OPEN_INVITATION = (RequestStatus.SENT, RequestStatus.ACCEPTED, RequestStatus.DECLINED)


def _first_name(s: Student) -> str:
    return s.full_name.split(" ")[0]


def _link(token: str) -> str:
    return f"{get_settings().public_app_url.rstrip('/')}/respond/{token}"


def _slot(drive: Drive) -> str:
    start, end = to_local(drive.starts_at, drive.timezone), to_local(drive.ends_at, drive.timezone)
    return f"{start:%d %b %Y}, {start:%H:%M}–{end:%H:%M} ({drive.timezone})"


def _criteria_summary(db: Session, drive: Drive) -> str:
    c = current_criteria(db, drive.id)
    if c is None:
        return "Criteria not configured"
    parts = [f"CGPA ≥ {c.min_cgpa:.2f}" if c.min_cgpa is not None else "No CGPA minimum"]
    parts.append({"NO_ACTIVE_BACKLOGS": "no active backlogs", "MAX_ALLOWED": f"up to {c.max_active_backlogs} active backlogs",
                  "NOT_CONSIDERED": "backlogs not considered"}[c.backlog_policy])
    parts.append("branches: " + (", ".join(c.allowed_branches) if c.allowed_branches else "all"))
    return "; ".join(parts)


def _new_request(db: Session, *, kind: str, student: Student, drive: Drive | None, fields: list[str],
                 criteria_version: int | None, ttl: timedelta | None = None, user: User | None,
                 expires_at: datetime | None = None) -> StudentRequest:
    expires = expires_at or utcnow() + ttl
    if drive is not None and kind == RequestKind.DRIVE_INVITATION:
        expires = min(expires, drive.starts_at)
    req = StudentRequest(kind=kind, token=secrets.token_urlsafe(32), student_id=student.id,
                         drive_id=drive.id if drive else None, status=RequestStatus.SENT, requested_fields=fields,
                         response={}, criteria_version=criteria_version, expires_at=expires,
                         created_by_id=user.id if user else None)
    db.add(req)
    db.flush()
    return req


def _actor(user: User | None, fallback: str = "system") -> str:
    return f"user:{user.id}" if user else fallback


# --------------------------------------------------------------------------- invitations

def invite_eligible(db: Session, drive: Drive, user: User | None, actor: str | None = None,
                    audience: str | None = None) -> dict[str, Any]:
    """Student Outreach Agent: email each recipient a personal link to the drive's consent form.

    The drive's consent form decides the audience: every student (forms made from company emails) or only
    students who already meet the criteria. One link per student per drive; re-running never duplicates.
    """
    from app.services import consent_forms

    if drive.starts_at <= utcnow():
        raise PreconditionFailed("The drive has already started; invitations can no longer be sent")
    actor = actor or _actor(user)
    form, _ = consent_forms.create_form(db, drive, actor, audience or "ELIGIBLE")
    if form.status != "OPEN" or form.closes_at <= utcnow():
        raise PreconditionFailed("The consent form for this drive is closed")
    ev = evaluate_drive(db, drive)
    already = {r.student_id for r in db.scalars(select(StudentRequest).where(
        StudentRequest.drive_id == drive.id, StudentRequest.kind == RequestKind.DRIVE_INVITATION,
        StudentRequest.status.in_(OPEN_INVITATION)))}
    sent, skipped = [], []
    closes = to_local(form.closes_at, drive.timezone)
    for o in ev.outcomes:
        if form.audience == "ELIGIBLE" and o.decision != Decision.ELIGIBLE:
            continue
        if o.student_id in already:
            skipped.append(o.roll_number)
            continue
        s = db.get(Student, o.student_id)
        req = _new_request(db, kind=RequestKind.DRIVE_INVITATION, student=s, drive=drive, fields=[],
                           criteria_version=ev.criteria.version, expires_at=form.closes_at, user=user)
        intro = (f"You meet the eligibility criteria for {drive.title}." if form.audience == "ELIGIBLE" else
                 f"A new placement drive is open: {drive.title}. Please read the eligibility criteria below.")
        body = (f"Dear {_first_name(s)},\n\n{intro}\n\n"
                + "\n".join(f"  {ln}" if ln else "" for ln in form.description.splitlines())
                + f"\n\nAnswer the consent form (YES or NO) with your personal link:\n\n  {_link(req.token)}\n\n"
                f"Please answer by {closes:%d %b %Y, %H:%M}. — Placement Cell")
        mailer.send(db, category=EmailCategory.DRIVE_INVITATION, to_address=s.email, to_name=s.full_name,
                    subject=f"Consent form: {drive.title} — answer by {closes:%d %b}",
                    body=body, dedupe_key=f"invite:{req.id}", sent_by=actor, student_id=s.id, drive_id=drive.id,
                    request_id=req.id)
        sent.append(s.roll_number)
    ledger.record(db, actor_type=ActorType.AGENT if actor.startswith("agent") else ActorType.USER, actor_id=actor,
                  action="INVITATIONS_SENT", entity_type="drive", entity_id=drive.id, drive_id=drive.id,
                  details={"sent": len(sent), "already_invited": len(skipped), "criteria_version": ev.criteria.version,
                           "audience": form.audience, "consent_form_id": form.id})
    db.commit()
    return {"sent": sent, "already_invited": skipped, "criteria_version": ev.criteria.version,
            "audience": form.audience, "consent_form_id": form.id}


def invitation_summary(db: Session, drive_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(select(StudentRequest).where(StudentRequest.drive_id == drive_id,
                                                   StudentRequest.kind == RequestKind.DRIVE_INVITATION)
                      .order_by(StudentRequest.id))
    return [request_dict(r) for r in rows]


# --------------------------------------------------------------------------- missing data

def request_missing_data(db: Session, student: Student, fields: list[str], user: User | None,
                         drive: Drive | None = None, actor: str | None = None,
                         expires_at: datetime | None = None) -> StudentRequest:
    """Follow-up Mailer Agent: a personal form with exactly the student's missing fields (and consent, for a drive)."""
    fields = [f for f in fields if f in FIELD_LABEL and (f != "consent" or drive is not None)]
    if not fields:
        raise ValidationFailed("Choose at least one field to request")
    if expires_at is not None and expires_at <= utcnow():
        raise ValidationFailed("The form must close in the future")
    open_req = db.scalar(select(StudentRequest).where(
        StudentRequest.student_id == student.id, StudentRequest.kind == RequestKind.DATA_REQUEST,
        StudentRequest.status == RequestStatus.SENT, StudentRequest.expires_at > utcnow()))
    if open_req is not None and set(fields) <= set(open_req.requested_fields):
        return open_req
    req = _new_request(db, kind=RequestKind.DATA_REQUEST, student=student, drive=drive, fields=fields,
                       criteria_version=None, ttl=timedelta(days=7), expires_at=expires_at, user=user)
    why = f" so that you can be considered for {drive.title} ({drive.employer.name})" if drive else ""
    tz = drive.timezone if drive else get_settings().default_timezone
    missing = [FIELD_LABEL[f] for f in fields if f != "consent"]
    asks = []
    if missing:
        asks.append(f"your placement record (roll number {student.roll_number}) is missing: {', '.join(missing)}")
    if "consent" in fields:
        asks.append(f"we have not received your consent answer for {drive.title}")
    sentence = "; and ".join(asks)
    body = (f"Dear {_first_name(student)},\n\n"
            f"{sentence[:1].upper() + sentence[1:]}. Please complete your personal form{why}:\n\n"
            f"  {_link(req.token)}\n\n"
            f"The form closes on {to_local(req.expires_at, tz):%d %b %Y at %H:%M}. "
            f"After that it can no longer be submitted.\n\n— Placement Cell")
    mailer.send(db, category=EmailCategory.DATA_REQUEST, to_address=student.email, to_name=student.full_name,
                subject="Action needed: missing details in your placement record", body=body,
                dedupe_key=f"data-request:{req.id}", sent_by=actor or _actor(user), student_id=student.id,
                drive_id=drive.id if drive else None, request_id=req.id)
    ledger.record(db, actor_type=ActorType.USER if user else ActorType.AGENT, actor_id=actor or _actor(user),
                  action="DATA_REQUEST_SENT", entity_type="student", entity_id=student.id,
                  drive_id=drive.id if drive else None, details={"roll_number": student.roll_number, "fields": fields})
    db.commit()
    return req


def request_missing_for_drive(db: Session, drive: Drive, user: User | None) -> dict[str, Any]:
    ev = evaluate_drive(db, drive)
    sent = []
    for o in ev.outcomes:
        if o.decision != Decision.UNKNOWN:
            continue
        fields = [RULE_TO_FIELD[m] for m in o.missing_evidence if m in RULE_TO_FIELD]
        if fields:
            request_missing_data(db, db.get(Student, o.student_id), fields, user, drive=drive)
            sent.append({"roll_number": o.roll_number, "fields": fields})
    return {"requested": sent}


# --------------------------------------------------------------------------- public (student) side

def get_by_token(db: Session, token: str) -> StudentRequest:
    req = db.scalar(select(StudentRequest).where(StudentRequest.token == token))
    if req is None:
        raise NotFound("This link is not valid")
    if req.status == RequestStatus.SENT and req.expires_at <= utcnow():
        req.status = RequestStatus.EXPIRED
        db.commit()
    return req


def public_view(db: Session, req: StudentRequest) -> dict[str, Any]:
    """What the student sees: no personal data beyond their first name and roll number."""
    out: dict[str, Any] = {"kind": req.kind, "status": req.status, "first_name": _first_name(req.student),
                           "roll_number": req.student.roll_number, "expires_at": req.expires_at.isoformat(),
                           "requested_fields": [{"field": f, "label": FIELD_LABEL[f]} for f in req.requested_fields],
                           "branches": [{"code": k, "label": v} for k, v in BRANCH_LABELS.items()]}
    if req.drive is not None:
        from app.services.consent_forms import get_form

        d = req.drive
        out["drive"] = {"title": d.title, "employer": d.employer.name, "slot": _slot(d), "location": d.location,
                        "criteria": _criteria_summary(db, d)}
        form = get_form(db, d.id) if req.kind == RequestKind.DRIVE_INVITATION else None
        if form is not None:
            out["form"] = {"title": form.title, "description": form.description, "questions": form.questions,
                           "status": form.status, "closes_at": form.closes_at.isoformat()}
        if "consent" in req.requested_fields:
            out["consent_question"] = (f"Do you want to take part in {d.title} and allow the Placement Cell to share your "
                                       f"roll number, name, branch, CGPA and backlog count with {d.employer.name}?")
    if req.status == RequestStatus.APPLIED and (req.response or {}).get("recheck"):
        out["recheck"] = req.response["recheck"]
    return out


def respond_invitation(db: Session, req: StudentRequest, accept: bool, comment: str | None = None) -> StudentRequest:
    from app.services.consent_forms import get_form
    from app.services.students import set_consent

    if req.kind != RequestKind.DRIVE_INVITATION:
        raise ValidationFailed("This link is not an invitation")
    form = get_form(db, req.drive_id)
    if form is not None and (form.status != "OPEN" or form.closes_at <= utcnow()):
        raise PreconditionFailed("This consent form has closed. Please contact the Placement Cell.")
    if req.status == RequestStatus.EXPIRED:
        raise PreconditionFailed("This invitation has expired. Please contact the Placement Cell.")
    if req.status not in (RequestStatus.SENT, RequestStatus.ACCEPTED, RequestStatus.DECLINED):
        raise Conflict("This invitation can no longer be answered")
    new_status = RequestStatus.ACCEPTED if accept else RequestStatus.DECLINED
    if req.status == new_status:
        return req
    req.status, req.responded_at = new_status, utcnow()
    req.response = {"accepted": accept, "answered_at": req.responded_at.isoformat(),
                    **({"comment": comment.strip()[:1000]} if comment and comment.strip() else {})}
    criteria = current_criteria(db, req.drive_id)
    scope = consent_scope_key("DRIVE_REGISTRATION", req.drive_id)
    set_consent(db, req.student, accept, f"Invitation response (request #{req.id})", None,
                source=f"invitation:{req.id}", scope=scope, actor=f"student:{req.student.roll_number}")
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"student:{req.student.roll_number}",
                  action="INVITATION_ACCEPTED" if accept else "INVITATION_DECLINED", entity_type="student_request",
                  entity_id=req.id, drive_id=req.drive_id,
                  details={"criteria_version": criteria.version if criteria else None})
    db.commit()
    return req


def _parse_submission(req: StudentRequest, values: dict[str, Any]) -> dict[str, Any]:
    clean, errors = {}, {}
    for f in req.requested_fields:
        raw = values.get(f)
        if f == "consent":
            if isinstance(raw, bool):
                clean[f] = raw
            elif str(raw).strip().lower() in {"yes", "true", "1"}:
                clean[f] = True
            elif str(raw).strip().lower() in {"no", "false", "0"}:
                clean[f] = False
            else:
                errors[f] = "Choose Yes or No"
            continue
        if f == "branch":
            code = normalize_branch(raw)
            clean[f] = code
            if code is None:
                errors[f] = "Choose a branch from the list"
            continue
        parsed = {"cgpa": parse_cgpa, "active_backlogs": parse_backlogs, "graduation_year": parse_year}[f](raw)
        if parsed.value is None:
            errors[f] = parsed.issues[0].message if parsed.issues else "Required"
        clean[f] = parsed.value
    if errors:
        raise ValidationFailed("Please correct the highlighted fields", details={"fields": errors})
    return clean


def _apply_submission(db: Session, req: StudentRequest, actor: str, user: User | None) -> dict[str, Any]:
    """Write submitted values to the student record (data fields marked self-reported) and record consent."""
    from app.services.students import set_consent, update_student

    values = dict(req.response.get("values", {}))
    s = req.student
    data = {k: v for k, v in values.items() if k in DATA_FIELDS}
    result: dict[str, Any] = {}
    if data:
        result = update_student(db, s, data, user, actor=actor)
        s.self_reported = [x for x in (s.self_reported or []) if x.get("field") not in data] + [
            {"field": k, "value": v, "request_id": req.id, "at": utcnow().isoformat()} for k, v in data.items()]
    if "consent" in values and req.drive_id is not None:
        set_consent(db, s, bool(values["consent"]), f"Personal form (request #{req.id})", None,
                    source=f"data-request:{req.id}", scope=consent_scope_key("DRIVE_REGISTRATION", req.drive_id),
                    actor=actor)
        # The drive's consent form answer for this student follows the latest answer.
        for inv in db.scalars(select(StudentRequest).where(
                StudentRequest.drive_id == req.drive_id, StudentRequest.student_id == s.id,
                StudentRequest.kind == RequestKind.DRIVE_INVITATION)):
            inv.status = RequestStatus.ACCEPTED if values["consent"] else RequestStatus.DECLINED
            inv.responded_at = inv.responded_at or utcnow()
    db.flush()
    return result


def recheck_student(db: Session, student: Student, drive: Drive) -> dict[str, Any]:
    """Eligibility Re-check Agent: evaluate one student against one drive (read-only)."""
    ev = evaluate_drive(db, drive)
    o = next(x for x in ev.outcomes if x.student_id == student.id)
    out = {"drive_id": drive.id, "drive_title": drive.title, "decision": o.decision, "roster_status": o.roster_status,
           "reasons": o.reasons, "now_qualifies": o.roster_status == "INCLUDED"}
    ledger.record(db, actor_type=ActorType.AGENT, actor_id="agent:recheck", action="ELIGIBILITY_RECHECKED",
                  entity_type="student", entity_id=student.id, drive_id=drive.id,
                  details={"roll_number": student.roll_number, "decision": o.decision, "roster_status": o.roster_status})
    return out


def submit_data(db: Session, req: StudentRequest, values: dict[str, Any]) -> StudentRequest:
    """A student submits their personal form. The Response Reader Agent validates it and, when the
    institution allows (auto_apply_student_data), applies it at once; otherwise an officer verifies it."""
    from app.services import app_settings

    if req.kind != RequestKind.DATA_REQUEST:
        raise ValidationFailed("This link is not a data request")
    if req.status == RequestStatus.SENT and req.expires_at <= utcnow():
        req.status = RequestStatus.EXPIRED
        db.commit()
    if req.status == RequestStatus.EXPIRED:
        raise PreconditionFailed("This form has closed. Please contact the Placement Cell.")
    if req.status != RequestStatus.SENT:
        raise Conflict("Details were already submitted with this link")
    clean = _parse_submission(req, values)
    req.status, req.responded_at = RequestStatus.SUBMITTED, utcnow()
    req.response = {"values": clean, "submitted_at": req.responded_at.isoformat()}
    s = req.student
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"student:{s.roll_number}", action="STUDENT_DATA_SUBMITTED",
                  entity_type="student_request", entity_id=req.id, drive_id=req.drive_id, details={"fields": list(clean)})
    if app_settings.get(db, "auto_apply_student_data"):
        _apply_submission(db, req, "agent:response_reader", None)
        req.status, req.reviewed_at = RequestStatus.APPLIED, utcnow()
        ledger.record(db, actor_type=ActorType.AGENT, actor_id="agent:response_reader", action="STUDENT_DATA_APPLIED",
                      entity_type="student_request", entity_id=req.id, drive_id=req.drive_id,
                      details={"roll_number": s.roll_number, "fields": list(clean), "self_reported": True})
        if req.drive is not None and current_criteria(db, req.drive_id) is not None:
            req.response = {**req.response, "recheck": recheck_student(db, s, req.drive)}
        db.commit()
        return req
    exceptions.raise_exception(
        db, kind=ExceptionKind.DATA_SUBMITTED, severity=Severity.WARNING,
        title=f"{s.roll_number} submitted missing details — verify and apply",
        dedupe_key=f"data-submitted:{req.id}",
        detail={"request_id": req.id, "roll_number": s.roll_number, "submitted": clean,
                "current": {f: getattr(s, f) for f in clean if f in DATA_FIELDS}},
        drive_id=req.drive_id, student_id=s.id,
    )
    db.commit()
    return req


def review_data(db: Session, request_id: int, user: User, apply: bool, note: str | None = None) -> StudentRequest:
    req = db.get(StudentRequest, request_id)
    if req is None or req.kind != RequestKind.DATA_REQUEST:
        raise NotFound("Data request not found")
    if req.status != RequestStatus.SUBMITTED:
        raise Conflict(f"Request is {req.status}; only submitted details can be reviewed")
    if apply:
        _apply_submission(db, req, f"user:{user.id}", user)
    req.status = RequestStatus.APPLIED if apply else RequestStatus.REJECTED
    req.reviewed_by_id, req.reviewed_at = user.id, utcnow()
    exc = exceptions.get_by_key(db, f"data-submitted:{req.id}")
    if exc and exc.status == ExceptionStatus.OPEN:
        exc.status = ExceptionStatus.RESOLVED
        exc.resolution = "APPLIED" if apply else "REJECTED"
        exc.resolution_note = note or ("Verified and applied to the student record" if apply else "Rejected by officer")
        exc.resolved_by_id, exc.resolved_at = user.id, utcnow()
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}",
                  action="STUDENT_DATA_APPLIED" if apply else "STUDENT_DATA_REJECTED", entity_type="student_request",
                  entity_id=req.id, details={"roll_number": req.student.roll_number, "note": note})
    db.commit()
    return req


def request_dict(r: StudentRequest) -> dict[str, Any]:
    return {"id": r.id, "kind": r.kind, "status": r.status, "student_id": r.student_id,
            "roll_number": r.student.roll_number, "full_name": r.student.full_name, "drive_id": r.drive_id,
            "drive_title": r.drive.title if r.drive else None, "requested_fields": r.requested_fields,
            "response": r.response, "criteria_version": r.criteria_version, "expires_at": r.expires_at.isoformat(),
            "responded_at": r.responded_at.isoformat() if r.responded_at else None,
            "created_at": r.created_at.isoformat()}


# --------------------------------------------------------------------------- after a verified upload

def notify_upload(db: Session, op: UploadOperation, actor: str) -> dict[str, Any]:
    """Email the employer contact the verified roster and tell each sent student. Idempotent."""
    drive = op.drive
    students = {s.roll_number: s for s in db.scalars(select(Student).where(Student.roll_number.in_(op.submitted_roll_numbers)))}
    rows = [students[r] for r in op.submitted_roll_numbers if r in students]
    employer = drive.employer
    lines = "\n".join(f"  {s.roll_number:<10} {s.full_name:<28} {s.branch or '-':<6} {s.cgpa if s.cgpa is not None else '-'}"
                      for s in rows)
    label = "Supplementary list" if op.proposal.kind == "SUPPLEMENT" else "Eligible candidates"
    employer_msg = mailer.send(
        db, category=EmailCategory.EMPLOYER_ROSTER, to_address=employer.contact_email, to_name=f"{employer.name} campus team",
        subject=f"{label} for {drive.title} — {len(rows)} student(s)",
        body=(f"Dear {employer.name} team,\n\n{label} for {drive.title} on {_slot(drive)}. "
              f"These students meet your published criteria, have consented to share their details, and were "
              f"submitted to your drive form (receipt {(op.destination_receipt or {}).get('receipt_no', '—')}).\n\n"
              f"  {'Roll no.':<10} {'Name':<28} {'Branch':<6} CGPA\n{lines}\n\n"
              f"Reference: operation {op.operation_key[:12]}.\n\n— Placement Cell"),
        dedupe_key=f"employer-roster:{op.id}", sent_by=actor, drive_id=drive.id, upload_id=op.id)
    student_msgs = []
    for s in rows:
        student_msgs.append(mailer.send(
            db, category=EmailCategory.PROFILE_SUBMITTED, to_address=s.email, to_name=s.full_name,
            subject=f"Shortlisted for {drive.title} — your profile was sent to {employer.name}",
            body=(f"Dear {_first_name(s)},\n\nYou are shortlisted for {drive.title}: your profile has been sent to "
                  f"{employer.name}.\n\n  Date & time: {_slot(drive)}\n  Venue: {drive.location or 'to be announced'}\n\n"
                  f"Please carry your college ID card. Your attendance will be marked on the day. "
                  f"You will get a reminder before the drive.\n\n— Placement Cell"),
            dedupe_key=f"profile-submitted:{op.id}:{s.id}", sent_by=actor, student_id=s.id, drive_id=drive.id,
            upload_id=op.id))
    not_shortlisted = notify_not_shortlisted(db, drive, actor) if op.proposal.kind != "SUPPLEMENT" else 0
    db.commit()
    return {"employer_email": employer_msg.status, "employer_address": employer.contact_email,
            "students_notified": sum(m.status != "SKIPPED" for m in student_msgs), "emails": len(student_msgs) + 1,
            "not_shortlisted_notified": not_shortlisted}


def notify_not_shortlisted(db: Session, drive: Drive, actor: str) -> int:
    """Tell students who said YES to this drive's consent form but do not meet the criteria, with the reason."""
    criteria = current_criteria(db, drive.id)
    if criteria is None or criteria.required_consent_scope != "DRIVE_REGISTRATION":
        return 0
    sent = 0
    for o in evaluate_drive(db, drive).outcomes:
        if o.decision != Decision.INELIGIBLE or not o.consent_ok:
            continue
        s = db.get(Student, o.student_id)
        reasons = "\n".join(f"  - {r}" for r in o.reasons if "consent form" not in r.lower()) or "  - Eligibility criteria not met"
        msg = mailer.send(
            db, category=EmailCategory.NOT_SHORTLISTED, to_address=s.email, to_name=s.full_name,
            subject=f"Not shortlisted for {drive.title}",
            body=(f"Dear {_first_name(s)},\n\nThank you for registering for {drive.title}. Your profile was not sent to "
                  f"{drive.employer.name} because it does not meet the company's criteria:\n\n{reasons}\n\n"
                  f"If your record is wrong, contact the Placement Cell. Keep an eye on upcoming drives.\n\n— Placement Cell"),
            dedupe_key=f"not-shortlisted:{drive.id}:{s.id}", sent_by=actor, student_id=s.id, drive_id=drive.id)
        sent += msg.status != "SKIPPED"
    return sent


# --------------------------------------------------------------------------- selection results

def results_for_drive(db: Session, drive: Drive) -> list[dict[str, Any]]:
    results = {r.student_id: r for r in db.scalars(select(DriveResult).where(DriveResult.drive_id == drive.id))}
    out = []
    for a in db.scalars(select(DriveAssignment).where(DriveAssignment.drive_id == drive.id,
                                                      DriveAssignment.status == AssignmentStatus.CONFIRMED)):
        r = results.get(a.student_id)
        out.append({"student_id": a.student_id, "roll_number": a.student.roll_number, "full_name": a.student.full_name,
                    "branch": a.student.branch, "outcome": r.outcome if r else None, "note": r.note if r else None,
                    "updated_at": r.updated_at.isoformat() if r else None})
    return sorted(out, key=lambda x: x["roll_number"])


def record_results(db: Session, drive: Drive, items: list[dict[str, Any]], notify: bool, user: User) -> dict[str, Any]:
    confirmed = {a.student_id for a in db.scalars(select(DriveAssignment).where(
        DriveAssignment.drive_id == drive.id, DriveAssignment.status == AssignmentStatus.CONFIRMED))}
    existing = {r.student_id: r for r in db.scalars(select(DriveResult).where(DriveResult.drive_id == drive.id))}
    changed, emailed = [], 0
    for item in items:
        sid, outcome = int(item["student_id"]), item.get("outcome")
        if sid not in confirmed:
            raise ValidationFailed(f"Student {sid} was not on this drive's submitted roster")
        if outcome not in DriveOutcome.__members__.values():
            raise ValidationFailed(f"Unknown outcome {outcome}")
        r = existing.get(sid)
        if r is None:
            r = DriveResult(drive_id=drive.id, student_id=sid, outcome=outcome, note=item.get("note"), recorded_by_id=user.id)
            db.add(r)
        elif r.outcome == outcome and r.note == item.get("note"):
            continue
        else:
            r.outcome, r.note, r.recorded_by_id = outcome, item.get("note"), user.id
        changed.append({"student_id": sid, "outcome": outcome})
        if notify and outcome in (DriveOutcome.SELECTED, DriveOutcome.NOT_SELECTED):
            s = db.get(Student, sid)
            selected = outcome == DriveOutcome.SELECTED
            body = (f"Dear {_first_name(s)},\n\n" + (
                f"Congratulations! {drive.employer.name} has selected you in {drive.title}. "
                f"The Placement Cell will share the offer process and next steps shortly."
                if selected else
                f"Thank you for taking part in {drive.title}. {drive.employer.name} has not selected you this time. "
                f"Keep an eye on upcoming drives — many more opportunities are ahead.")
                + "\n\n— Placement Cell")
            msg = mailer.send(db, category=EmailCategory.SELECTED if selected else EmailCategory.NOT_SELECTED,
                              to_address=s.email, to_name=s.full_name,
                              subject=(f"Congratulations — selected by {drive.employer.name}" if selected
                                       else f"Your result for {drive.title}"),
                              body=body, dedupe_key=f"result:{drive.id}:{sid}:{outcome}", sent_by=f"user:{user.id}",
                              student_id=sid, drive_id=drive.id)
            emailed += msg.status != "SKIPPED"
    db.flush()
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="DRIVE_RESULTS_RECORDED",
                  entity_type="drive", entity_id=drive.id, drive_id=drive.id,
                  details={"changed": changed, "notified": emailed})
    db.commit()
    return {"changed": len(changed), "notified": emailed}


def placement_history(db: Session, student: Student) -> list[dict[str, Any]]:
    """One row per drive the student was evaluated, invited, sent or recorded for."""
    from app.models.entities import EligibilityDecision

    rows: dict[int, dict[str, Any]] = {}

    def row(drive: Drive) -> dict[str, Any]:
        return rows.setdefault(drive.id, {"drive_id": drive.id, "drive_title": drive.title, "employer": drive.employer.name,
                                          "starts_at": drive.starts_at.isoformat(), "timezone": drive.timezone,
                                          "decision": None, "criteria_version": None, "reasons": [], "invitation": None,
                                          "sent_to_employer": False, "outcome": None})

    for d in db.scalars(select(EligibilityDecision).where(EligibilityDecision.student_id == student.id,
                                                          EligibilityDecision.is_stale.is_(False))):
        r = row(db.get(Drive, d.drive_id))
        r.update(decision=d.decision, roster_status=d.roster_status, criteria_version=d.criteria_version, reasons=d.reasons)
    for q in db.scalars(select(StudentRequest).where(StudentRequest.student_id == student.id,
                                                     StudentRequest.kind == RequestKind.DRIVE_INVITATION)):
        row(q.drive)["invitation"] = q.status
    for a in db.scalars(select(DriveAssignment).where(DriveAssignment.student_id == student.id,
                                                      DriveAssignment.status == AssignmentStatus.CONFIRMED)):
        row(a.drive)["sent_to_employer"] = True
    for res in db.scalars(select(DriveResult).where(DriveResult.student_id == student.id)):
        row(res.drive)["outcome"] = res.outcome
    return sorted(rows.values(), key=lambda r: r["starts_at"])


def selected_employers(db: Session, student_ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    if not student_ids:
        return out
    for r in db.scalars(select(DriveResult).where(DriveResult.student_id.in_(student_ids),
                                                  DriveResult.outcome == DriveOutcome.SELECTED)):
        out.setdefault(r.student_id, []).append(r.drive.employer.name)
    return out


def emails_for_student(db: Session, student_id: int) -> list[EmailMessage]:
    return list(db.scalars(select(EmailMessage).where(EmailMessage.student_id == student_id).order_by(EmailMessage.id.desc())))

"""One consent form per drive (Consent Form Agent) and its closing (Deadline Agent).

The form tells students exactly what will be shared, with which company, the
eligibility criteria and when the form closes. Each student answers through a
personal link (a DRIVE_INVITATION request); a YES records consent for this
drive only (`DRIVE:<id>`), a NO records a refusal.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.errors import PreconditionFailed
from app.db.base import utcnow
from app.domain.schedule import to_local
from app.models.entities import ConsentForm, Drive, StudentRequest
from app.models.enums import ActorType, RequestKind, RequestStatus

AUDIENCES = ("ALL_STUDENTS", "ELIGIBLE")


def get_form(db: Session, drive_id: int) -> ConsentForm | None:
    return db.scalar(select(ConsentForm).where(ConsentForm.drive_id == drive_id))


def _actor_type(actor: str) -> str:
    return ActorType.AGENT if actor.startswith("agent") else (ActorType.USER if actor.startswith("user") else ActorType.SYSTEM)


def closing_time(drive: Drive):
    deadline = drive.registration_deadline or drive.starts_at
    return min(deadline, drive.starts_at)


def create_form(db: Session, drive: Drive, actor: str, audience: str = "ALL_STUDENTS") -> tuple[ConsentForm, bool]:
    """Idempotent: a drive has exactly one consent form."""
    from app.services.outreach import _criteria_summary, _slot

    existing = get_form(db, drive.id)
    if existing is not None:
        return existing, False
    if audience not in AUDIENCES:
        raise PreconditionFailed(f"Unknown audience {audience}")
    closes_at = closing_time(drive)
    if closes_at <= utcnow():
        raise PreconditionFailed("The registration deadline for this drive has already passed")
    employer = drive.employer.name
    ctc = next((ln.split(":", 1)[1].strip() for ln in (drive.notes or "").splitlines() if ln.lower().startswith("ctc:")), None)
    lines = [
        f"{employer} is recruiting for the role of {drive.role_title or drive.title}.",
        "",
        f"Date & time: {_slot(drive)}",
        f"Venue: {drive.location or 'to be announced'}",
    ]
    if ctc:
        lines.append(f"CTC / stipend: {ctc}")
    lines += [
        f"Eligibility: {_criteria_summary(db, drive)}",
        "",
        f"If you say YES, the Placement Cell will share your roll number, name, branch, CGPA and number of active "
        f"backlogs with {employer} for this drive only — and only if you meet the eligibility criteria. "
        f"If you say NO, or do not answer, nothing about you is shared for this drive.",
        "",
        f"This form closes on {to_local(closes_at, drive.timezone):%d %b %Y at %H:%M} ({drive.timezone}).",
    ]
    form = ConsentForm(
        drive_id=drive.id, title=f"Consent form — {drive.title}", description="\n".join(lines),
        questions=[
            {"key": "consent", "type": "yes_no", "required": True,
             "label": f"Do you want to take part in this drive and allow the Placement Cell to share your profile with {employer}?"},
            {"key": "comment", "type": "text", "required": False,
             "label": "Anything the Placement Cell should know? (optional)"},
        ],
        audience=audience, closes_at=closes_at, status="OPEN", created_by=actor,
    )
    db.add(form)
    db.flush()
    ledger.record(db, actor_type=_actor_type(actor), actor_id=actor, action="CONSENT_FORM_CREATED", entity_type="consent_form",
                  entity_id=form.id, drive_id=drive.id,
                  details={"audience": audience, "closes_at": closes_at.isoformat(), "questions": [q["key"] for q in form.questions]})
    return form, True


def summary(db: Session, drive_id: int) -> dict[str, Any] | None:
    form = get_form(db, drive_id)
    if form is None:
        return None
    counts = dict(db.execute(select(StudentRequest.status, func.count()).where(
        StudentRequest.drive_id == drive_id, StudentRequest.kind == RequestKind.DRIVE_INVITATION)
        .group_by(StudentRequest.status)).all())
    return {
        "id": form.id, "title": form.title, "description": form.description, "questions": form.questions,
        "audience": form.audience, "status": form.status, "closes_at": form.closes_at.isoformat(),
        "closes_at_local": to_local(form.closes_at, form.drive.timezone).replace(tzinfo=None).isoformat(timespec="minutes"),
        "created_by": form.created_by, "sent": sum(counts.values()),
        "accepted": counts.get(RequestStatus.ACCEPTED, 0), "declined": counts.get(RequestStatus.DECLINED, 0),
        "pending": counts.get(RequestStatus.SENT, 0), "expired": counts.get(RequestStatus.EXPIRED, 0),
    }


def close_due(db: Session, actor: str = "agent:deadline") -> list[dict[str, Any]]:
    """Deadline Agent: close forms past their closing time and expire unanswered links (forms and data requests)."""
    now = utcnow()
    closed = []
    for form in db.scalars(select(ConsentForm).where(ConsentForm.status == "OPEN", ConsentForm.closes_at <= now)):
        form.status, form.closed_at = "CLOSED", now
        expired = 0
        for req in db.scalars(select(StudentRequest).where(
                StudentRequest.drive_id == form.drive_id, StudentRequest.kind == RequestKind.DRIVE_INVITATION,
                StudentRequest.status == RequestStatus.SENT)):
            req.status = RequestStatus.EXPIRED
            expired += 1
        info = summary(db, form.drive_id)
        ledger.record(db, actor_type=ActorType.AGENT, actor_id=actor, action="CONSENT_FORM_CLOSED", entity_type="consent_form",
                      entity_id=form.id, drive_id=form.drive_id,
                      details={"accepted": info["accepted"], "declined": info["declined"], "no_response": expired})
        closed.append({"drive_id": form.drive_id, "accepted": info["accepted"], "declined": info["declined"], "no_response": expired})
    lapsed = 0
    for req in db.scalars(select(StudentRequest).where(StudentRequest.kind == RequestKind.DATA_REQUEST,
                                                       StudentRequest.status == RequestStatus.SENT,
                                                       StudentRequest.expires_at <= now)):
        req.status = RequestStatus.EXPIRED
        lapsed += 1
    if lapsed:
        ledger.record(db, actor_type=ActorType.AGENT, actor_id=actor, action="FORMS_EXPIRED", details={"data_requests": lapsed})
    db.commit()
    return closed + ([{"data_requests_expired": lapsed}] if lapsed else [])

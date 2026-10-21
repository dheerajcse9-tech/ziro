"""Invitations, missing-data requests, selection results, email outbox, and the public student response endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ANY_ROLE, OFFICER_ROLES, require_roles
from app.core.config import get_settings
from app.core.errors import NotFound
from app.db.session import get_db
from app.models.entities import EmailMessage, StudentRequest, User
from app.services import drives as drive_svc
from app.services import outreach
from app.services import students as student_svc

router = APIRouter(prefix="/api")


def email_dict(m: EmailMessage, with_body: bool = False) -> dict[str, Any]:
    out = {"id": m.id, "category": m.category, "to_address": m.to_address, "to_name": m.to_name, "subject": m.subject,
           "status": m.status, "transport": m.transport, "error": m.error, "student_id": m.student_id,
           "drive_id": m.drive_id, "upload_id": m.upload_id, "request_id": m.request_id, "sent_by": m.sent_by,
           "created_at": m.created_at.isoformat(), "sent_at": m.sent_at.isoformat() if m.sent_at else None}
    if with_body:
        out["body_text"] = m.body_text
    return out


# --------------------------------------------------------------------------- invitations & data requests

@router.post("/drives/{drive_id}/invitations", tags=["outreach"])
def send_invitations(drive_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    return outreach.invite_eligible(db, drive_svc.get_drive(db, drive_id), user)


@router.get("/drives/{drive_id}/invitations", tags=["outreach"])
def list_invitations(drive_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    drive_svc.get_drive(db, drive_id)
    items = outreach.invitation_summary(db, drive_id)
    counts: dict[str, int] = {}
    for i in items:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    return {"items": items, "counts": counts}


@router.post("/drives/{drive_id}/missing-data-requests", tags=["outreach"])
def request_missing_for_drive(drive_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    return outreach.request_missing_for_drive(db, drive_svc.get_drive(db, drive_id), user)


class DataRequestIn(BaseModel):
    fields: list[str] = Field(min_length=1)
    drive_id: int | None = None


@router.post("/students/{student_id}/data-requests", tags=["outreach"], status_code=201)
def request_student_data(student_id: int, body: DataRequestIn, db: Session = Depends(get_db),
                         user: User = Depends(require_roles(*OFFICER_ROLES))):
    drive = drive_svc.get_drive(db, body.drive_id) if body.drive_id else None
    req = outreach.request_missing_data(db, student_svc.get_student(db, student_id), body.fields, user, drive=drive)
    return outreach.request_dict(req)


@router.get("/student-requests", tags=["outreach"])
def list_requests(kind: str | None = None, status: str | None = None, student_id: int | None = None,
                  db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    stmt = select(StudentRequest).order_by(StudentRequest.id.desc()).limit(500)
    if kind:
        stmt = stmt.where(StudentRequest.kind == kind)
    if status:
        stmt = stmt.where(StudentRequest.status == status)
    if student_id:
        stmt = stmt.where(StudentRequest.student_id == student_id)
    return [outreach.request_dict(r) for r in db.scalars(stmt)]


class ReviewIn(BaseModel):
    apply: bool
    note: str | None = Field(default=None, max_length=1000)


@router.post("/student-requests/{request_id}/review", tags=["outreach"])
def review_request(request_id: int, body: ReviewIn, db: Session = Depends(get_db),
                   user: User = Depends(require_roles(*OFFICER_ROLES))):
    return outreach.request_dict(outreach.review_data(db, request_id, user, body.apply, body.note))


# --------------------------------------------------------------------------- results

class ResultItem(BaseModel):
    student_id: int
    outcome: str
    note: str | None = None


class ResultsIn(BaseModel):
    results: list[ResultItem]
    notify: bool = True


@router.get("/drives/{drive_id}/results", tags=["outreach"])
def get_results(drive_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    return outreach.results_for_drive(db, drive_svc.get_drive(db, drive_id))


@router.put("/drives/{drive_id}/results", tags=["outreach"])
def put_results(drive_id: int, body: ResultsIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*OFFICER_ROLES))):
    drive = drive_svc.get_drive(db, drive_id)
    summary = outreach.record_results(db, drive, [r.model_dump() for r in body.results], body.notify, user)
    return {**summary, "results": outreach.results_for_drive(db, drive)}


# --------------------------------------------------------------------------- outbox

@router.get("/emails", tags=["outreach"])
def list_emails(category: str | None = None, status: str | None = None, student_id: int | None = None,
                drive_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db), _: User = Depends(require_roles(*OFFICER_ROLES, "approver"))):
    stmt = select(EmailMessage)
    for col, val in ((EmailMessage.category, category), (EmailMessage.status, status),
                     (EmailMessage.student_id, student_id), (EmailMessage.drive_id, drive_id)):
        if val:
            stmt = stmt.where(col == val)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(EmailMessage.id.desc()).offset((page - 1) * page_size).limit(page_size))
    settings = get_settings()
    return {"items": [email_dict(m) for m in items], "total": total, "page": page, "page_size": page_size,
            "transport": "SMTP" if settings.smtp_host else "LOCAL_MAILBOX"}


@router.get("/emails/{email_id}", tags=["outreach"])
def get_email(email_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*OFFICER_ROLES, "approver"))):
    m = db.get(EmailMessage, email_id)
    if m is None:
        raise NotFound("Email not found")
    return email_dict(m, with_body=True)


# --------------------------------------------------------------------------- public student endpoints (token = credential)

class InvitationAnswer(BaseModel):
    accept: bool


class DataAnswer(BaseModel):
    values: dict[str, Any]


@router.get("/public/requests/{token}", tags=["public"])
def public_request(token: str, db: Session = Depends(get_db)):
    return outreach.public_view(db, outreach.get_by_token(db, token))


@router.post("/public/requests/{token}/invitation", tags=["public"])
def public_answer_invitation(token: str, body: InvitationAnswer, db: Session = Depends(get_db)):
    req = outreach.respond_invitation(db, outreach.get_by_token(db, token), body.accept)
    return outreach.public_view(db, req)


@router.post("/public/requests/{token}/data", tags=["public"])
def public_submit_data(token: str, body: DataAnswer, db: Session = Depends(get_db)):
    req = outreach.submit_data(db, outreach.get_by_token(db, token), body.values)
    return outreach.public_view(db, req)


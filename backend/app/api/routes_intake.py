"""Company inbox, Google (Gmail) sign-in, autopilot, the agent crew, follow-up forms and the approval review."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import crew, graph
from app.api import serializers as S
from app.api.deps import ANY_ROLE, DRIVE_ROLES, require_roles
from app.audit import ledger
from app.core.config import get_settings
from app.core.errors import PreconditionFailed, ValidationFailed
from app.core.security import read_state_token
from app.db.base import utcnow
from app.db.session import get_db
from app.domain.schedule import to_utc
from app.integrations import gmail
from app.models.entities import AgentEvent, AgentRun, AuditEvent, InboundEmail, User
from app.models.enums import ActorType, InboundSource, Role
from app.services import app_settings, approval_review, approvals, autopilot, consent_forms, intake
from app.services import drives as drive_svc

router = APIRouter(prefix="/api")
REVIEWERS = (Role.APPROVER, Role.OFFICER, Role.ADMIN)


# --------------------------------------------------------------------------- company inbox

class PastedEmail(BaseModel):
    from_address: str = Field(min_length=3, max_length=255)
    from_name: str | None = Field(default=None, max_length=200)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=50_000)
    process: bool = True


class DetailsIn(BaseModel):
    values: dict[str, Any]


class IgnoreIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class TrustedIn(BaseModel):
    senders: list[str]


def _row_out(db: Session, row: InboundEmail, with_body: bool = False) -> dict[str, Any]:
    out = intake.serialize(row, with_body=with_body)
    run = db.get(AgentRun, row.run_id) if row.run_id else None
    out["run"] = None if run is None else {"id": run.id, "status": run.status, "pending_question": run.pending_question,
                                            "summary": run.summary}
    return out


@router.get("/inbox", tags=["inbox"])
def list_inbox(status: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    stmt = select(InboundEmail).order_by(InboundEmail.received_at.desc())
    if status:
        stmt = stmt.where(InboundEmail.status == status)
    return {"items": [_row_out(db, r) for r in db.scalars(stmt.limit(200))], "counts": intake.counts(db),
            "mailbox": intake.mailbox_status(db), "autopilot": autopilot.status(),
            "trusted_senders": app_settings.get(db, "trusted_company_senders") or []}


@router.get("/inbox/{email_id}", tags=["inbox"])
def get_inbox_email(email_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    return _row_out(db, intake.get(db, email_id), with_body=True)


@router.post("/inbox", tags=["inbox"], status_code=201)
def paste_email(body: PastedEmail, db: Session = Depends(get_db), user: User = Depends(require_roles(*DRIVE_ROLES))):
    row, created = intake.ingest(db, source=InboundSource.MANUAL, from_address=body.from_address, from_name=body.from_name,
                                 subject=body.subject, body=body.body, actor=f"user:{user.id}")
    db.commit()
    if created and body.process:
        graph.start_intake_run(db, row, user)
        db.refresh(row)
    return {**_row_out(db, row, with_body=True), "created": created}


@router.post("/inbox/check", tags=["inbox"])
def check_mailbox(db: Session = Depends(get_db), user: User = Depends(require_roles(*DRIVE_ROLES))):
    """Mailbox Agent now, then the stage 1 agents on every new email."""
    fetched = intake.fetch_mailbox(db, user, actor="agent:mailbox")
    processed = autopilot.process_new_emails(db, user)
    return {"mailbox": fetched, "processed": processed}


@router.post("/inbox/demo", tags=["inbox"])
def load_demo_emails(db: Session = Depends(get_db), user: User = Depends(require_roles(*DRIVE_ROLES))):
    if not get_settings().demo_mode:
        raise PreconditionFailed("Sample emails are only available in demo mode")
    n = intake.seed_demo_inbox(db)
    db.commit()
    return {"added": n}


@router.post("/inbox/{email_id}/process", tags=["inbox"])
def process_email(email_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*DRIVE_ROLES))):
    run = graph.start_intake_run(db, intake.get(db, email_id), user)
    return {"run": S.run(run), "email": _row_out(db, intake.get(db, email_id))}


@router.put("/inbox/{email_id}/details", tags=["inbox"])
def correct_details(email_id: int, body: DetailsIn, db: Session = Depends(get_db),
                    user: User = Depends(require_roles(*DRIVE_ROLES))):
    row = intake.update_details(db, intake.get(db, email_id), body.values, user)
    db.commit()
    return _row_out(db, row, with_body=True)


@router.post("/inbox/{email_id}/ignore", tags=["inbox"])
def ignore_email(email_id: int, body: IgnoreIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*DRIVE_ROLES))):
    from app.services import exceptions

    row = intake.ignore(db, intake.get(db, email_id), user, body.note)
    exceptions.auto_resolve(db, f"intake:{row.id}", "Email ignored")
    run = db.get(AgentRun, row.run_id) if row.run_id else None
    if run is not None and run.status == "NEEDS_REVIEW":
        run.status, run.pending_question, run.summary = "COMPLETED", None, "Company email ignored"
        run.state = {**(run.state or {}), "next_node": None}
    db.commit()
    return _row_out(db, row)


class ReadSinceIn(BaseModel):
    since: Literal["now", "all"]


@router.put("/inbox-settings/read-since", tags=["inbox"])
def set_read_since(body: ReadSinceIn, db: Session = Depends(get_db), user: User = Depends(require_roles(Role.COORDINATOR, Role.ADMIN))):
    """'now': ignore everything already in the mailbox; 'all': read the last INBOX_LOOKBACK_DAYS again."""
    value = utcnow().isoformat() if body.since == "now" else None
    app_settings.set_value(db, "mailbox_read_since", value)
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="MAILBOX_READ_SINCE_SET",
                  details={"read_since": value})
    db.commit()
    return {"read_since": value}


@router.put("/inbox-settings/trusted-senders", tags=["inbox"])
def set_trusted_senders(body: TrustedIn, db: Session = Depends(get_db), user: User = Depends(require_roles(Role.COORDINATOR, Role.ADMIN))):
    senders = sorted({x.strip().lower() for x in body.senders if x.strip()})
    bad = [x for x in senders if "@" not in x or " " in x]
    if bad:
        raise ValidationFailed(f"Not email addresses: {bad}")
    app_settings.set_value(db, "trusted_company_senders", senders)
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="TRUSTED_SENDERS_UPDATED",
                  details={"senders": senders})
    db.commit()
    return {"trusted_senders": senders}


# --------------------------------------------------------------------------- Google (Gmail) sign-in

@router.get("/integrations/google", tags=["integrations"])
def google_status(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    return gmail.status(db)


@router.post("/integrations/google/connect", tags=["integrations"])
def google_connect(db: Session = Depends(get_db), user: User = Depends(require_roles(Role.COORDINATOR, Role.ADMIN))):
    return {"auth_url": gmail.authorization_url(user.id)}


@router.get("/integrations/google/callback", tags=["integrations"], include_in_schema=False)
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    db: Session = Depends(get_db)):
    """Google redirects the browser here after sign-in. The signed `state` names the user who started it."""
    target = f"{get_settings().public_app_url.rstrip('/')}/settings"
    try:
        user_id = read_state_token(state or "", "google_oauth")
        user = db.get(User, user_id)
        if user is None or user.role not in (Role.COORDINATOR, Role.ADMIN):
            raise ValidationFailed("This sign-in was not started by a drive upload officer or admin")
        cred = gmail.complete_sign_in(db, gmail.require_code(code, error), user_id)
        ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user_id}", action="GMAIL_CONNECTED",
                      details={"account": cred.account_email, "scopes": cred.scopes})
        db.commit()
        return RedirectResponse(f"{target}?{urlencode({'google': 'connected', 'account': cred.account_email})}", status_code=303)
    except (jwt.PyJWTError, ValueError) as exc:
        db.rollback()
        return RedirectResponse(f"{target}?{urlencode({'google': 'error', 'message': f'Sign-in link expired or invalid ({exc})'})}",
                                status_code=303)
    except (gmail.GmailError, ValidationFailed, PreconditionFailed) as exc:
        db.rollback()
        return RedirectResponse(f"{target}?{urlencode({'google': 'error', 'message': getattr(exc, 'message', str(exc))})}",
                                status_code=303)


@router.post("/integrations/google/disconnect", tags=["integrations"])
def google_disconnect(db: Session = Depends(get_db), user: User = Depends(require_roles(Role.COORDINATOR, Role.ADMIN))):
    account = gmail.account_email(db)
    gmail.disconnect(db)
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="GMAIL_DISCONNECTED",
                  details={"account": account})
    db.commit()
    return gmail.status(db)


# --------------------------------------------------------------------------- autopilot and the crew

@router.get("/autopilot", tags=["agents"])
def autopilot_status(_: User = Depends(require_roles(*ANY_ROLE))):
    return autopilot.status()


@router.post("/autopilot/run", tags=["agents"])
def autopilot_run(_: User = Depends(require_roles(*DRIVE_ROLES, Role.APPROVER))):
    return autopilot.run_once()


@router.get("/agents", tags=["agents"])
def list_agents(db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    from app.models.enums import TERMINAL_RUN_STATES

    out = []
    for a in crew.describe():
        recent: list[dict[str, Any]] = []
        if a["nodes"]:
            for ev in db.scalars(select(AgentEvent).where(AgentEvent.event_type == "NODE_START", AgentEvent.node.in_(a["nodes"]))
                                 .order_by(AgentEvent.id.desc()).limit(5)):
                recent.append({"at": ev.created_at.isoformat(), "run_id": ev.run_id, "what": ev.node})
        for au in db.scalars(select(AuditEvent).where(AuditEvent.actor_id == a["actor"]).order_by(AuditEvent.seq.desc()).limit(5)):
            recent.append({"at": au.occurred_at.isoformat(), "action": au.action, "drive_id": au.drive_id})
        recent.sort(key=lambda x: x["at"], reverse=True)
        out.append({**a, "recent": recent[:5]})
    waiting = db.scalar(select(AgentRun.id).where(AgentRun.status.notin_(list(TERMINAL_RUN_STATES))).limit(1))
    return {"agents": out, "stages": crew.STAGES, "autopilot": autopilot.status(),
            "ai": {"provider": get_settings().active_llm, "model": get_settings().active_llm_model},
            "mailbox": intake.mailbox_status(db), "has_open_runs": waiting is not None}


# --------------------------------------------------------------------------- stage 3: approval review and follow-up forms

class FollowupIn(BaseModel):
    closes_at: datetime = Field(description="Local wall-clock time in the drive's timezone when the forms close")


@router.get("/approvals/{approval_id}/review", tags=["approvals"])
def approval_agent_review(approval_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*REVIEWERS))):
    result = approval_review.review(db, approvals.get_approval(db, approval_id))
    db.commit()
    return result


@router.get("/drives/{drive_id}/followups", tags=["drives"])
def followup_overview(drive_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    from app.models.entities import StudentRequest
    from app.models.enums import RequestKind
    from app.services.outreach import request_dict

    drive = drive_svc.get_drive(db, drive_id)
    forms = db.scalars(select(StudentRequest).where(StudentRequest.drive_id == drive.id,
                                                    StudentRequest.kind == RequestKind.DATA_REQUEST)
                       .order_by(StudentRequest.id.desc()))
    return {"candidates": approval_review.followup_candidates(db, drive), "forms": [request_dict(r) for r in forms],
            "consent_form": consent_forms.summary(db, drive.id)}


@router.post("/drives/{drive_id}/followups", tags=["drives"])
def start_followups(drive_id: int, body: FollowupIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*REVIEWERS))):
    drive = drive_svc.get_drive(db, drive_id)
    closes = body.closes_at if body.closes_at.tzinfo else to_utc(body.closes_at, drive.timezone)
    run = graph.start_followup_run(db, drive, user, closes)
    return S.run(run)


@router.get("/drives/{drive_id}/consent-form", tags=["drives"])
def consent_form(drive_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles(*ANY_ROLE))):
    drive_svc.get_drive(db, drive_id)
    return consent_forms.summary(db, drive_id)


"""Outbound email with an auditable outbox.

Every message is stored first. With SMTP configured it is then delivered over
SMTP; otherwise it stays in the local mailbox (status DELIVERED_LOCAL), which
the Communications page shows. A dedupe key guarantees the same notification
is never sent twice, even if a workflow step is retried.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage as MimeMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.config import get_settings
from app.db.base import utcnow
from app.models.entities import EmailMessage
from app.models.enums import ActorType, EmailStatus


def transport_name() -> str:
    return "SMTP" if get_settings().smtp_host else "LOCAL_MAILBOX"


def _deliver_smtp(msg: EmailMessage) -> None:
    settings = get_settings()
    mime = MimeMessage()
    mime["From"] = settings.smtp_from
    mime["To"] = f"{msg.to_name} <{msg.to_address}>" if msg.to_name else msg.to_address
    mime["Subject"] = msg.subject
    mime.set_content(msg.body_text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(mime)


def send(
    db: Session, *, category: str, to_address: str | None, to_name: str | None, subject: str, body: str,
    dedupe_key: str, sent_by: str, student_id: int | None = None, drive_id: int | None = None,
    upload_id: int | None = None, request_id: int | None = None,
) -> EmailMessage:
    existing = db.scalar(select(EmailMessage).where(EmailMessage.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    msg = EmailMessage(
        category=category, to_address=to_address, to_name=to_name, subject=subject, body_text=body,
        status=EmailStatus.SKIPPED, transport=transport_name(), dedupe_key=dedupe_key, student_id=student_id,
        drive_id=drive_id, upload_id=upload_id, request_id=request_id, sent_by=sent_by,
    )
    db.add(msg)
    db.flush()
    _attempt(msg)
    _audit(db, msg, sent_by)
    return msg


def _explain(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, OSError) and type(exc).__name__ == "gaierror":
        text += (f" — the SMTP server name '{get_settings().smtp_host}' could not be found. Check SMTP_HOST in "
                 "backend/.env (for Gmail it must be exactly smtp.gmail.com) and your internet connection.")
    elif isinstance(exc, smtplib.SMTPAuthenticationError):
        text += " — the SMTP username or password was rejected. For Gmail, SMTP_PASSWORD must be an App Password."
    return text


def _attempt(msg: EmailMessage) -> None:
    msg.transport, msg.error = transport_name(), None
    if not msg.to_address:
        msg.status, msg.error = EmailStatus.SKIPPED, "No email address on record"
    elif msg.transport == "SMTP":
        try:
            _deliver_smtp(msg)
            msg.status, msg.sent_at = EmailStatus.SENT, utcnow()
        except (OSError, smtplib.SMTPException) as exc:
            msg.status, msg.error = EmailStatus.FAILED, _explain(exc)
    else:
        msg.status, msg.sent_at = EmailStatus.DELIVERED_LOCAL, utcnow()


def retry_failed(db: Session, *, sent_by: str, email_id: int | None = None) -> list[EmailMessage]:
    """Re-attempt delivery of FAILED messages (one, or all of them) with the current SMTP settings."""
    stmt = select(EmailMessage).where(EmailMessage.status == EmailStatus.FAILED)
    if email_id is not None:
        stmt = stmt.where(EmailMessage.id == email_id)
    retried = list(db.scalars(stmt.order_by(EmailMessage.id)))
    for msg in retried:
        _attempt(msg)
        _audit(db, msg, sent_by, retry=True)
    db.flush()
    return retried


def _audit(db: Session, msg: EmailMessage, sent_by: str, retry: bool = False) -> None:
    ledger.record(db, actor_type=ActorType.SYSTEM if sent_by.startswith("system") else
                  (ActorType.AGENT if sent_by.startswith("agent") else ActorType.USER),
                  actor_id=sent_by, action="EMAIL_" + msg.status, entity_type="email", entity_id=msg.id,
                  drive_id=msg.drive_id, details={"category": msg.category, "to": msg.to_address, "subject": msg.subject,
                                                  "transport": msg.transport, "error": msg.error, "retry": retry})
    db.flush()

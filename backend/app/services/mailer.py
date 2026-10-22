"""Outbound email with an auditable outbox.

Every message is stored first, then delivered through the connected Gmail
account (Google sign-in) or, failing that, the configured SMTP server;
otherwise it stays in the local mailbox (status DELIVERED_LOCAL), which the
Communications page shows. A dedupe key guarantees the same notification
is never sent twice, even if a workflow step is retried.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.config import get_settings
from app.db.base import utcnow
from app.models.entities import EmailMessage
from app.models.enums import ActorType, EmailStatus


def transport_name(db: Session | None = None) -> str:
    from app.integrations import gmail

    if db is not None and gmail.connected(db):
        return "GMAIL_API"
    return "SMTP" if get_settings().smtp_host else "LOCAL_MAILBOX"


def _mime(msg: EmailMessage, from_header: str) -> MimeMessage:
    mime = MimeMessage()
    mime["From"] = from_header
    mime["To"] = formataddr((msg.to_name or "", msg.to_address))
    mime["Subject"] = msg.subject
    mime.set_content(msg.body_text)
    return mime


def _deliver_gmail(db: Session, msg: EmailMessage) -> None:
    from app.integrations import gmail

    name = parseaddr(get_settings().smtp_from)[0] or "Placement Cell"
    gmail.send_mime(db, _mime(msg, formataddr((name, gmail.account_email(db) or ""))).as_bytes())


def _deliver_smtp(msg: EmailMessage) -> None:
    settings = get_settings()
    mime = _mime(msg, settings.smtp_from)
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
        existing.created_now = False  # transient: lets callers count only new sends
        return existing
    msg = EmailMessage(
        category=category, to_address=to_address, to_name=to_name, subject=subject, body_text=body,
        status=EmailStatus.SKIPPED, transport=transport_name(db), dedupe_key=dedupe_key, student_id=student_id,
        drive_id=drive_id, upload_id=upload_id, request_id=request_id, sent_by=sent_by,
    )
    db.add(msg)
    db.flush()
    _attempt(db, msg)
    _audit(db, msg, sent_by)
    msg.created_now = True
    return msg


def _explain(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, OSError) and type(exc).__name__ == "gaierror":
        text += (f" — the SMTP server name '{get_settings().smtp_host}' could not be found. Check SMTP_HOST in "
                 "backend/.env (for Gmail it must be exactly smtp.gmail.com) and your internet connection.")
    elif isinstance(exc, smtplib.SMTPAuthenticationError):
        text += " — the SMTP username or password was rejected. For Gmail, SMTP_PASSWORD must be an App Password."
    return text


def _attempt(db: Session, msg: EmailMessage) -> None:
    from app.integrations.gmail import GmailError

    msg.transport, msg.error = transport_name(db), None
    if not msg.to_address:
        msg.status, msg.error = EmailStatus.SKIPPED, "No email address on record"
    elif msg.transport in ("SMTP", "GMAIL_API"):
        try:
            if msg.transport == "GMAIL_API":
                _deliver_gmail(db, msg)
            else:
                _deliver_smtp(msg)
            msg.status, msg.sent_at = EmailStatus.SENT, utcnow()
        except GmailError as exc:
            msg.status, msg.error = EmailStatus.FAILED, str(exc)
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
        _attempt(db, msg)
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

"""Autopilot: the background agents, every AUTOPILOT_INTERVAL_SECONDS.

One tick:
  1. Mailbox Agent   – read new company emails from the connected mailbox
  2. Stage 1 agents  – process each new email (read -> check -> drive -> consent form -> students)
  3. Deadline Agent  – close consent forms and personal forms past their closing time
  4. Reminder Agent  – send due reminders

Company emails are processed on behalf of the drive upload officer (the first
active coordinator account, else an admin), so their runs and audit entries
show who the agents worked for. Anything that needs a person stops and waits.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models.entities import InboundEmail, User
from app.models.enums import InboundStatus, Role

log = logging.getLogger("placementos.autopilot")
_lock = threading.Lock()
_stop = threading.Event()
STATE: dict[str, Any] = {"last_tick_at": None, "last_result": None, "last_error": None, "running": False}


def operator(db: Session) -> User | None:
    for role in (Role.COORDINATOR, Role.ADMIN):
        user = db.scalar(select(User).where(User.role == role, User.is_active.is_(True)).order_by(User.id).limit(1))
        if user is not None:
            return user
    return None


def process_new_emails(db: Session, user: User, limit: int = 20) -> list[dict[str, Any]]:
    from app.agent import graph

    out = []
    for row in list(db.scalars(select(InboundEmail).where(InboundEmail.status == InboundStatus.NEW, InboundEmail.run_id.is_(None))
                               .order_by(InboundEmail.received_at).limit(limit))):
        try:
            run = graph.start_intake_run(db, row, user)
            out.append({"email_id": row.id, "run_id": run.id, "status": run.status})
        except Exception as exc:  # noqa: BLE001 - one bad email must not stop the others
            db.rollback()
            log.exception("Processing company email %s failed", row.id)
            out.append({"email_id": row.id, "error": f"{type(exc).__name__}: {exc}"})
    return out


def tick(db: Session) -> dict[str, Any]:
    from app.services import consent_forms, intake, reminders

    result: dict[str, Any] = {"at": utcnow().isoformat()}
    user = operator(db)
    try:
        result["mailbox"] = intake.fetch_mailbox(db, None, actor="agent:mailbox")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        result["mailbox"] = {"error": getattr(exc, "message", str(exc))}
    result["processed"] = process_new_emails(db, user) if user else []
    try:
        result["deadlines"] = consent_forms.close_due(db)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        result["deadlines"] = {"error": str(exc)}
    try:
        result["reminders"] = reminders.run(db)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        result["reminders"] = {"error": str(exc)}
    return result


def run_once() -> dict[str, Any]:
    from app.db.session import SessionLocal

    if not _lock.acquire(blocking=False):
        return {"skipped": "a tick is already running"}
    STATE["running"] = True
    try:
        with SessionLocal() as db:
            result = tick(db)
        STATE.update(last_tick_at=result["at"], last_result=result, last_error=None)
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("Autopilot tick failed")
        STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        return {"error": STATE["last_error"]}
    finally:
        STATE["running"] = False
        _lock.release()


def _loop(interval: int) -> None:
    _stop.wait(10)  # let the API finish starting
    while not _stop.is_set():
        run_once()
        _stop.wait(interval)


def start() -> None:
    s = get_settings()
    if not s.autopilot_enabled:
        return
    _stop.clear()
    threading.Thread(target=_loop, args=(s.autopilot_interval_seconds,), name="autopilot", daemon=True).start()
    log.info("Autopilot started (every %ss)", s.autopilot_interval_seconds)


def stop() -> None:
    _stop.set()


def status() -> dict[str, Any]:
    s = get_settings()
    return {"enabled": s.autopilot_enabled, "interval_seconds": s.autopilot_interval_seconds, **STATE}

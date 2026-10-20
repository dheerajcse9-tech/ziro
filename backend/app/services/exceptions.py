"""Exception inbox: unresolved issues the officer must look at."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.entities import ExceptionItem
from app.models.enums import ExceptionStatus


def raise_exception(
    db: Session,
    *,
    kind: str,
    severity: str,
    title: str,
    dedupe_key: str,
    detail: dict[str, Any] | None = None,
    drive_id: int | None = None,
    student_id: int | None = None,
    run_id: int | None = None,
) -> ExceptionItem:
    """Create the exception, or refresh an existing OPEN one with the same key.

    Resolved/dismissed exceptions are not reopened: their resolution is a
    deliberate officer decision that the domain logic honours.
    """
    existing = db.scalar(select(ExceptionItem).where(ExceptionItem.dedupe_key == dedupe_key))
    if existing:
        if existing.status == ExceptionStatus.OPEN:
            existing.detail = detail or {}
            existing.title = title
            existing.severity = severity
            if run_id:
                existing.run_id = run_id
        return existing
    item = ExceptionItem(
        kind=kind, severity=severity, status=ExceptionStatus.OPEN, title=title, detail=detail or {},
        dedupe_key=dedupe_key, drive_id=drive_id, student_id=student_id, run_id=run_id,
    )
    db.add(item)
    db.flush()
    return item


def auto_resolve(db: Session, dedupe_key: str, note: str) -> None:
    item = db.scalar(select(ExceptionItem).where(ExceptionItem.dedupe_key == dedupe_key))
    if item and item.status == ExceptionStatus.OPEN:
        item.status = ExceptionStatus.RESOLVED
        item.resolution = "AUTO_RESOLVED"
        item.resolution_note = note
        item.resolved_at = utcnow()


def get_by_key(db: Session, dedupe_key: str) -> ExceptionItem | None:
    return db.scalar(select(ExceptionItem).where(ExceptionItem.dedupe_key == dedupe_key))

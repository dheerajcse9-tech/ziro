"""Consent lookups. Consent history is append-only; the latest record per scope wins."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import ConsentRecord
from app.models.enums import ConsentStatus


def current_consent(db: Session, student_ids: Iterable[int], scope: str) -> dict[int, dict]:
    """Map student_id -> {"granted": bool, "record_id", "status", "effective_at"}.

    Students with no record at all are absent from the map and must be treated
    as *not consented*.
    """
    ids = list(student_ids)
    if not ids:
        return {}
    latest = (
        select(func.max(ConsentRecord.id).label("id"))
        .where(ConsentRecord.student_id.in_(ids), ConsentRecord.scope == scope)
        .group_by(ConsentRecord.student_id)
        .subquery()
    )
    rows = db.scalars(select(ConsentRecord).where(ConsentRecord.id.in_(select(latest.c.id))))
    return {
        r.student_id: {
            "granted": r.status == ConsentStatus.GRANTED,
            "record_id": r.id,
            "status": r.status,
            "effective_at": r.effective_at.isoformat(),
            "source": r.source,
        }
        for r in rows
    }


def is_granted(db: Session, student_id: int, scope: str) -> bool:
    info = current_consent(db, [student_id], scope).get(student_id)
    return bool(info and info["granted"])

"""Append-only, hash-chained audit ledger.

Each event stores sha256(prev_hash + canonical(event body)). Any later edit or
deletion of a row breaks the chain, which `verify_chain` detects. Ordinary ORM
workflows cannot update or delete audit rows (enforced by a flush hook), and on
PostgreSQL a trigger installed by the migration rejects UPDATE/DELETE as well.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.core.hashing import canonical_json, sha256_hex
from app.core.redaction import redact
from app.db.base import utcnow
from app.models.entities import AuditEvent

GENESIS_HASH = "0" * 64
_lock = threading.Lock()


class AuditImmutableError(RuntimeError):
    pass


@event.listens_for(Session, "before_flush")
def _protect_audit_rows(session: Session, flush_context, instances) -> None:
    if session.info.get("allow_audit_purge"):
        return
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, AuditEvent) and (obj in session.deleted or session.is_modified(obj)):
            raise AuditImmutableError("Audit events are append-only and cannot be modified or deleted")


def _body(ev: AuditEvent) -> dict[str, Any]:
    return {
        "seq": ev.seq,
        "occurred_at": ev.occurred_at.isoformat(),
        "actor_type": ev.actor_type,
        "actor_id": ev.actor_id,
        "action": ev.action,
        "entity_type": ev.entity_type,
        "entity_id": ev.entity_id,
        "operation_id": ev.operation_id,
        "run_id": ev.run_id,
        "drive_id": ev.drive_id,
        "details": ev.details,
    }


def record(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    entity_type: str | None = None,
    entity_id: Any = None,
    details: dict[str, Any] | None = None,
    operation_id: str | None = None,
    run_id: int | None = None,
    drive_id: int | None = None,
) -> AuditEvent:
    """Append an event. Joins the caller's transaction; the caller commits."""
    with _lock:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(424242)"))
        last = db.execute(
            select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)
        ).first()
        seq = (last.seq + 1) if last else 1
        prev_hash = last.hash if last else GENESIS_HASH
        ev = AuditEvent(
            seq=seq,
            occurred_at=utcnow(),
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=None if entity_id is None else str(entity_id),
            operation_id=operation_id,
            run_id=run_id,
            drive_id=drive_id,
            details=json.loads(canonical_json(redact(details or {}))),
            prev_hash=prev_hash,
        )
        ev.hash = sha256_hex(prev_hash + sha256_hex(_body(ev)))
        db.add(ev)
        db.flush()
        return ev


def verify_chain(db: Session) -> dict[str, Any]:
    prev = GENESIS_HASH
    count = 0
    for ev in db.scalars(select(AuditEvent).order_by(AuditEvent.seq)):
        count += 1
        expected = sha256_hex(prev + sha256_hex(_body(ev)))
        if ev.prev_hash != prev or ev.hash != expected:
            return {"valid": False, "checked": count, "broken_at_seq": ev.seq}
        prev = ev.hash
    return {"valid": True, "checked": count, "head_hash": prev}


def count(db: Session) -> int:
    return db.scalar(select(func.count(AuditEvent.id))) or 0

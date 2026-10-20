"""Persisted agent event log (the Agent Control Room reads only these rows)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.hashing import canonical_json
from app.core.redaction import redact
from app.models.entities import AgentEvent, AgentRun

MAX_PAYLOAD_CHARS = 60_000


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    payload = redact(payload)
    text = canonical_json(payload)
    if len(text) <= MAX_PAYLOAD_CHARS:
        return payload
    return {"truncated": True, "preview": text[:MAX_PAYLOAD_CHARS]}


def emit(
    db: Session,
    run: AgentRun,
    event_type: str,
    *,
    node: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
    tool_name: str | None = None,
    status: str | None = None,
    duration_ms: int | None = None,
) -> AgentEvent:
    seq = (db.scalar(select(func.max(AgentEvent.seq)).where(AgentEvent.run_id == run.id)) or 0) + 1
    ev = AgentEvent(run_id=run.id, seq=seq, event_type=event_type, node=node, message=message,
                    payload=_compact(payload or {}), tool_name=tool_name, status=status, duration_ms=duration_ms)
    db.add(ev)
    db.flush()
    return ev

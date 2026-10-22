"""Explicit, minimal JSON shapes returned by the API (no ORM objects leak out)."""
from __future__ import annotations

from typing import Any

from app.domain.normalize import BRANCH_LABELS
from app.domain.schedule import to_local
from app.models.entities import (
    AgentEvent,
    AgentRun,
    ApprovalRequest,
    AttendanceSheet,
    AuditEvent,
    Destination,
    Drive,
    DriveCriteria,
    Employer,
    ExceptionItem,
    RosterImport,
    RosterProposal,
    Student,
    TestRun,
    UploadOperation,
    User,
    VerificationResult,
)


def iso(v) -> str | None:
    return v.isoformat() if v is not None else None


def user(u: User | None) -> dict[str, Any] | None:
    if u is None:
        return None
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active}


def student(s: Student, consent: dict | None = None) -> dict[str, Any]:
    return {
        "id": s.id, "roll_number": s.roll_number, "full_name": s.full_name, "email": s.email, "phone": s.phone,
        "branch": s.branch, "branch_label": BRANCH_LABELS.get(s.branch or "", None), "branch_raw": s.branch_raw,
        "cgpa": s.cgpa, "active_backlogs": s.active_backlogs, "graduation_year": s.graduation_year,
        "data_issues": s.data_issues or [], "record_version": s.record_version, "self_reported": s.self_reported or [],
        "consent": consent, "consent_granted": bool(consent and consent.get("granted")),
        "updated_at": iso(s.updated_at), "created_at": iso(s.created_at),
    }


def employer(e: Employer) -> dict[str, Any]:
    return {"id": e.id, "name": e.name, "industry": e.industry, "website": e.website, "contact_email": e.contact_email,
            "drive_count": len(e.drives)}


def destination(d: Destination | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {"id": d.id, "name": d.name, "kind": d.kind, "base_url": d.base_url, "form_ref": d.form_ref,
            "is_enabled": d.is_enabled, "config_version": d.config_version,
            "form_url": f"{d.base_url.rstrip('/')}/forms/{d.form_ref}",
            "is_local_mock": d.kind == "LOCAL_MOCK_FORM"}


def criteria(c: DriveCriteria | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"id": c.id, "version": c.version, "min_cgpa": c.min_cgpa, "cgpa_comparison": ">= (inclusive)",
            "backlog_policy": c.backlog_policy, "max_active_backlogs": c.max_active_backlogs,
            "allowed_branches": c.allowed_branches or [], "graduation_years": c.graduation_years or [],
            "required_consent_scope": c.required_consent_scope, "source_text": c.source_text,
            "source_reference": c.source_reference, "change_note": c.change_note, "created_at": iso(c.created_at),
            "created_by_id": c.created_by_id}


def drive(d: Drive, current_criteria: DriveCriteria | None = None) -> dict[str, Any]:
    ls, le = to_local(d.starts_at, d.timezone), to_local(d.ends_at, d.timezone)
    return {
        "id": d.id, "title": d.title, "role_title": d.role_title, "location": d.location,
        "employer": {"id": d.employer.id, "name": d.employer.name},
        "starts_at": iso(d.starts_at), "ends_at": iso(d.ends_at), "timezone": d.timezone,
        "starts_at_local": ls.replace(tzinfo=None).isoformat(timespec="minutes"),
        "ends_at_local": le.replace(tzinfo=None).isoformat(timespec="minutes"),
        "status": d.status, "schedule_version": d.schedule_version, "source_reference": d.source_reference,
        "notes": d.notes, "destination": destination(d.destination),
        "criteria": criteria(current_criteria), "updated_at": iso(d.updated_at),
        "registration_deadline": iso(d.registration_deadline), "roster_due_at": iso(d.roster_due_at),
        "registration_deadline_local": _local(d.registration_deadline, d.timezone),
        "roster_due_at_local": _local(d.roster_due_at, d.timezone),
    }


def _local(value, tz: str) -> str | None:
    return to_local(value, tz).replace(tzinfo=None).isoformat(timespec="minutes") if value else None


def run(r: AgentRun, include_state: bool = True) -> dict[str, Any]:
    out = {
        "id": r.id, "objective": r.objective, "planner_mode": r.planner_mode, "llm_model": r.llm_model,
        "intent": r.intent, "drive_id": r.drive_id, "drive_title": r.drive.title if r.drive else None,
        "status": r.status, "current_step": r.current_step, "plan": r.plan or [],
        "pending_question": r.pending_question, "summary": r.summary, "error": r.error,
        "criteria_version": r.criteria_version, "proposal_id": r.proposal_id, "created_by_id": r.created_by_id,
        "created_at": iso(r.created_at), "updated_at": iso(r.updated_at), "is_test_fixture": r.is_test_fixture,
    }
    if include_state:
        out["state"] = {k: v for k, v in (r.state or {}).items() if k != "requirements"}
        req = (r.state or {}).get("requirements")
        if req:
            out["state"]["requirements"] = {k: v for k, v in req.items()}
    return out


def event(e: AgentEvent) -> dict[str, Any]:
    return {"id": e.id, "seq": e.seq, "type": e.event_type, "node": e.node, "tool_name": e.tool_name,
            "status": e.status, "message": e.message, "payload": e.payload, "duration_ms": e.duration_ms,
            "created_at": iso(e.created_at)}


def proposal(p: RosterProposal) -> dict[str, Any]:
    return {"id": p.id, "drive_id": p.drive_id, "drive_title": p.drive.title, "run_id": p.run_id,
            "criteria_version": p.criteria_version, "schedule_version": p.schedule_version,
            "destination": destination(p.destination), "destination_fingerprint": p.destination_fingerprint,
            "roster_fingerprint": p.roster_fingerprint, "included_roll_numbers": p.included_roll_numbers,
            "kind": p.kind, "previously_submitted_roll_numbers": p.previously_submitted_roll_numbers,
            "excluded": p.excluded, "held": p.held, "unresolved_issues": p.unresolved_issues,
            "proposal_hash": p.proposal_hash, "status": p.status, "invalidated_reason": p.invalidated_reason,
            "created_at": iso(p.created_at)}


def approval(a: ApprovalRequest, decided_by: User | None = None, with_proposal: bool = True) -> dict[str, Any]:
    out = {"id": a.id, "proposal_id": a.proposal_id, "proposal_hash": a.proposal_hash, "status": a.status,
           "requested_by": a.requested_by, "requested_for_user_id": a.requested_for_user_id,
           "decided_by": user(decided_by), "decided_at": iso(a.decided_at), "decision_note": a.decision_note,
           "invalidated_reason": a.invalidated_reason, "created_at": iso(a.created_at),
           "drive_id": a.proposal.drive_id, "drive_title": a.proposal.drive.title,
           "included_count": len(a.proposal.included_roll_numbers), "excluded_count": len(a.proposal.excluded),
           "held_count": len(a.proposal.held), "kind": a.proposal.kind,
           "previously_submitted_count": len(a.proposal.previously_submitted_roll_numbers)}
    if with_proposal:
        out["proposal"] = proposal(a.proposal)
    return out


def upload(u: UploadOperation, latest: VerificationResult | None = None) -> dict[str, Any]:
    return {"id": u.id, "operation_key": u.operation_key, "approval_id": u.approval_id, "proposal_id": u.proposal_id,
            "drive_id": u.drive_id, "drive_title": u.drive.title, "destination": destination(u.destination),
            "status": u.status, "attempts": u.attempts, "submitted_roll_numbers": u.submitted_roll_numbers,
            "submitted_count": len(u.submitted_roll_numbers), "attempt_log": u.attempt_log,
            "destination_receipt": u.destination_receipt, "error": u.error, "executed_by": u.executed_by,
            "created_at": iso(u.created_at), "completed_at": iso(u.completed_at),
            "latest_verification": verification(latest) if latest else None}


def verification(v: VerificationResult) -> dict[str, Any]:
    return {"id": v.id, "upload_id": v.upload_id, "status": v.status, "method": v.method,
            "expected_count": v.expected_count, "observed_count": v.observed_count,
            "missing_roll_numbers": v.missing_roll_numbers, "unexpected_roll_numbers": v.unexpected_roll_numbers,
            "duplicate_roll_numbers": v.duplicate_roll_numbers, "observed_snapshot": v.observed_snapshot,
            "detail": v.detail, "receipt_hash": v.receipt_hash, "created_at": iso(v.created_at)}


def sheet(s: AttendanceSheet) -> dict[str, Any]:
    return {"id": s.id, "drive_id": s.drive_id, "upload_id": s.upload_id, "verification_id": s.verification_id,
            "file_name": s.file_name, "row_count": s.row_count, "roll_numbers": s.roll_numbers,
            "excluded_at_generation": s.excluded_at_generation, "sha256": s.sha256, "generated_by": s.generated_by,
            "created_at": iso(s.created_at), "purged": s.content_csv is None}


def exception(e: ExceptionItem) -> dict[str, Any]:
    return {"id": e.id, "kind": e.kind, "severity": e.severity, "status": e.status, "title": e.title,
            "detail": e.detail, "drive_id": e.drive_id, "student_id": e.student_id, "run_id": e.run_id,
            "resolution": e.resolution, "resolution_note": e.resolution_note, "resolved_by_id": e.resolved_by_id,
            "resolved_at": iso(e.resolved_at), "created_at": iso(e.created_at), "updated_at": iso(e.updated_at)}


def audit(a: AuditEvent) -> dict[str, Any]:
    return {"id": a.id, "seq": a.seq, "occurred_at": iso(a.occurred_at), "actor_type": a.actor_type,
            "actor_id": a.actor_id, "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id,
            "operation_id": a.operation_id, "run_id": a.run_id, "drive_id": a.drive_id, "details": a.details,
            "prev_hash": a.prev_hash, "hash": a.hash}


def roster_import(i: RosterImport, include_rows: bool = False) -> dict[str, Any]:
    out = {"id": i.id, "filename": i.filename, "file_sha256": i.file_sha256, "status": i.status,
           "column_mapping": i.column_mapping, "total_rows": i.total_rows, "valid_rows": i.valid_rows,
           "error_rows": i.error_rows, "created_count": i.created_count, "updated_count": i.updated_count,
           "unchanged_count": i.unchanged_count, "issue_count": len(i.errors or []),
           "created_at": iso(i.created_at), "committed_at": iso(i.committed_at)}
    if include_rows:
        out["rows"] = i.preview_rows
        out["errors"] = i.errors
    return out


def test_run(t: TestRun) -> dict[str, Any]:
    return {"id": t.id, "test_id": t.test_id, "title": t.title, "description": t.description,
            "input_fixture": t.input_fixture, "expected": t.expected, "actual": t.actual, "passed": t.passed,
            "checks": t.checks, "evidence": t.evidence, "failure_details": t.failure_details,
            "environment": t.environment, "executed_at": iso(t.executed_at), "executed_by_id": t.executed_by_id,
            "duration_ms": t.duration_ms}

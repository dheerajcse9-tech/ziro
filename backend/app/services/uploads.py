"""Approved roster upload with idempotency, pre-execution re-validation and reconciliation.

Execution contract:
1. The approval must be APPROVED (or already CONSUMED by this same operation).
2. The roster is recomputed from live data; its hash must equal the approved
   hash (criteria, schedule, destination, members, record versions).
3. Consent is re-checked for every student in the payload right before sending.
4. The destination must be enabled, allowlisted and unchanged.
5. The operation key is derived from the approved hash and destination, so a
   retry of the same approval can never create a second submission. Before
   any resubmission, the destination is queried for that key (reconciliation).
6. A 200/201 response is recorded but never treated as proof of success:
   verification (app.services.verification) re-reads the destination.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, PreconditionFailed
from app.core.hashing import sha256_hex
from app.db.base import utcnow
from app.integrations.drive_form import DestinationError, DestinationUncertain, DestinationUnreadable
from app.integrations.registry import adapter_for
from app.models.entities import ApprovalRequest, Drive, DriveAssignment, Student, UploadOperation
from app.models.enums import (
    consent_scope_key,
    ActorType,
    ApprovalStatus,
    AssignmentStatus,
    DriveStatus,
    ExceptionKind,
    ProposalStatus,
    Severity,
    UploadStatus,
)
from app.services import exceptions
from app.services.approvals import _invalidate_proposal
from app.services.consent import current_consent
from app.services.roster import evaluate_drive

# Fields the destination may receive (data minimisation). The form's own
# required-field list further narrows this at submission time.
SHAREABLE_FIELDS = ("roll_number", "full_name", "branch", "cgpa", "email")


def operation_key_for(approval: ApprovalRequest, destination_fingerprint: str) -> str:
    return sha256_hex({"approval_hash": approval.proposal_hash, "destination": destination_fingerprint})[:40]


def _log(op: UploadOperation, step: str, **data: Any) -> None:
    op.attempt_log = [*(op.attempt_log or []), {"at": utcnow().isoformat(), "step": step, **data}]


def execute(db: Session, approval_id: int, *, actor: str, run_id: int | None = None) -> UploadOperation:
    approval = db.get(ApprovalRequest, approval_id)
    if approval is None:
        raise NotFound(f"Approval {approval_id} not found")
    proposal = approval.proposal
    drive = db.get(Drive, proposal.drive_id)

    existing = db.scalar(select(UploadOperation).where(UploadOperation.approval_id == approval.id))
    if existing is not None and existing.status == UploadStatus.SUBMITTED:
        ledger.record(db, actor_type=ActorType.AGENT if run_id else ActorType.USER, actor_id=actor,
                      action="UPLOAD_DUPLICATE_PREVENTED", entity_type="upload", entity_id=existing.id,
                      operation_id=existing.operation_key, run_id=run_id, drive_id=drive.id,
                      details={"reason": "Operation already submitted; returning existing result"})
        return existing
    if approval.status not in (ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED):
        raise PreconditionFailed(f"Approval is {approval.status}; an APPROVED approval is required to upload")

    # --- Pre-execution re-validation (never trust earlier state) -----------------
    evaluation = evaluate_drive(db, drive)
    live_hash = evaluation.proposal_hash()
    if live_hash != approval.proposal_hash:
        _invalidate_proposal(db, proposal, "State changed between approval and execution", actor=actor)
        ledger.record(db, actor_type=ActorType.SYSTEM, actor_id=actor, action="UPLOAD_BLOCKED",
                      entity_type="approval", entity_id=approval.id, run_id=run_id, drive_id=drive.id,
                      details={"reason": "proposal hash mismatch", "approved_hash": approval.proposal_hash, "live_hash": live_hash})
        db.commit()  # persist the invalidation even though the request fails
        raise PreconditionFailed("Upload blocked: the roster or its inputs changed after approval. Re-approval required.",
                                 details={"approved_hash": approval.proposal_hash, "live_hash": live_hash})
    roll_numbers = list(proposal.included_roll_numbers)
    students = {s.roll_number: s for s in db.scalars(select(Student).where(Student.roll_number.in_(roll_numbers)))}
    consent = current_consent(db, [s.id for s in students.values()],
                              consent_scope_key(evaluation.criteria.required_consent_scope, drive.id))
    lacking = [r for r in roll_numbers if r not in students or not consent.get(students[r].id, {}).get("granted")]
    if lacking:
        _invalidate_proposal(db, proposal, f"Consent re-check failed for {lacking}", actor=actor)
        ledger.record(db, actor_type=ActorType.SYSTEM, actor_id=actor, action="UPLOAD_BLOCKED",
                      entity_type="approval", entity_id=approval.id, run_id=run_id, drive_id=drive.id,
                      details={"reason": "consent re-check failed", "roll_numbers": lacking})
        db.commit()
        raise PreconditionFailed("Upload blocked: consent re-check failed", details={"roll_numbers": lacking})
    destination = drive.destination
    adapter = adapter_for(destination)  # enforces enabled + allowlist

    if existing is None:
        op = UploadOperation(
            operation_key=operation_key_for(approval, evaluation.destination_fingerprint), approval_id=approval.id,
            proposal_id=proposal.id, drive_id=drive.id, destination_id=destination.id, status=UploadStatus.PENDING,
            submitted_roll_numbers=roll_numbers, executed_by=actor,
        )
        db.add(op)
        db.flush()
    else:
        op = existing
    _log(op, "prevalidated", proposal_hash=live_hash, consent_checked=len(roll_numbers))
    approval.status = ApprovalStatus.CONSUMED
    db.commit()  # durable record of intent before any external side effect

    settings = get_settings()
    payload = [{k: getattr(students[r], k) for k in SHAREABLE_FIELDS} for r in roll_numbers]
    while op.status != UploadStatus.SUBMITTED:
        # Reconcile first whenever a previous attempt may have reached the destination.
        if op.attempts > 0:
            try:
                found = adapter.find_submission(destination.form_ref, op.operation_key)
            except DestinationUnreadable as exc:
                _log(op, "reconcile_failed", error=str(exc))
                op.status, op.error = UploadStatus.UNCERTAIN, f"Cannot reconcile: {exc}"
                db.commit()
                break
            if found is not None:
                _log(op, "reconciled", receipt_no=found.receipt_no)
                _mark_submitted(db, op, found.__dict__, actor, run_id, reconciled=True)
                break
        if op.attempts >= settings.upload_max_attempts:
            op.status, op.error = UploadStatus.FAILED, op.error or "Maximum attempts reached"
            db.commit()
            break
        op.attempts += 1
        op.status = UploadStatus.IN_PROGRESS
        _log(op, "submit", attempt=op.attempts, candidates=len(payload))
        db.commit()
        try:
            receipt = adapter.submit(destination.form_ref, payload, client_reference=op.operation_key,
                                     submitted_by=f"PlaceCrew ({actor})")
        except DestinationUncertain as exc:
            _log(op, "uncertain", attempt=op.attempts, error=str(exc))
            op.status, op.error = UploadStatus.UNCERTAIN, str(exc)
            db.commit()
            continue  # loop reconciles before any retry
        except DestinationError as exc:
            _log(op, "rejected", attempt=op.attempts, error=str(exc))
            op.status, op.error = UploadStatus.FAILED, str(exc)
            db.commit()
            if "HTTP 503" in str(exc) and op.attempts < settings.upload_max_attempts:
                continue  # transient unavailability before commit: safe to retry after reconcile
            break
        _log(op, "receipt", receipt_no=receipt.receipt_no, http_status=receipt.http_status, duplicate=receipt.duplicate)
        _mark_submitted(db, op, receipt.__dict__, actor, run_id, reconciled=False)

    if op.status != UploadStatus.SUBMITTED:
        exceptions.raise_exception(
            db, kind=ExceptionKind.UPLOAD_FAILED, severity=Severity.BLOCKING,
            title=f"Upload for '{drive.title}' is {op.status}: {op.error}", dedupe_key=f"upload-failed:{op.id}",
            detail={"upload_id": op.id, "operation_key": op.operation_key, "attempt_log": op.attempt_log},
            drive_id=drive.id, run_id=run_id,
        )
        ledger.record(db, actor_type=ActorType.AGENT if run_id else ActorType.USER, actor_id=actor,
                      action="UPLOAD_NOT_CONFIRMED", entity_type="upload", entity_id=op.id,
                      operation_id=op.operation_key, run_id=run_id, drive_id=drive.id,
                      details={"status": op.status, "error": op.error, "attempts": op.attempts})
        db.commit()
    return op


def _mark_submitted(db: Session, op: UploadOperation, receipt: dict, actor: str, run_id: int | None, reconciled: bool) -> None:
    op.status = UploadStatus.SUBMITTED
    op.destination_receipt = {**receipt, "reconciled": reconciled}
    op.completed_at = utcnow()
    op.error = None
    proposal = op.proposal
    proposal.status = ProposalStatus.EXECUTED
    included = set(proposal.included_roll_numbers)
    for a in db.scalars(select(DriveAssignment).join(Student).where(
        DriveAssignment.drive_id == op.drive_id, Student.roll_number.in_(included))):
        a.status, a.note = AssignmentStatus.CONFIRMED, f"Uploaded in operation {op.operation_key[:12]}"
    drive = db.get(Drive, op.drive_id)
    drive.status = DriveStatus.ROSTER_SUBMITTED
    ledger.record(db, actor_type=ActorType.AGENT if run_id else ActorType.USER, actor_id=actor,
                  action="UPLOAD_SUBMITTED", entity_type="upload", entity_id=op.id, operation_id=op.operation_key,
                  run_id=run_id, drive_id=op.drive_id,
                  details={"receipt_no": receipt.get("receipt_no"), "claimed_count": receipt.get("candidate_count"),
                           "submitted_count": len(op.submitted_roll_numbers), "attempts": op.attempts,
                           "reconciled": reconciled, "destination_duplicate": receipt.get("duplicate")})
    db.commit()


def get_upload(db: Session, upload_id: int) -> UploadOperation:
    op = db.get(UploadOperation, upload_id)
    if op is None:
        raise NotFound(f"Upload {upload_id} not found")
    return op


def retry(db: Session, upload_id: int, *, actor: str) -> UploadOperation:
    op = get_upload(db, upload_id)
    if op.status == UploadStatus.SUBMITTED:
        raise Conflict("Upload already submitted")
    op.attempts = min(op.attempts, get_settings().upload_max_attempts - 1)
    db.flush()
    return execute(db, op.approval_id, actor=actor)

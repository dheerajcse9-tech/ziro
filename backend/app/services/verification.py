"""Independent read-back verification of submitted rosters, and upload receipts.

Verification never trusts the submission response. It re-reads the
destination's roster page and compares the exact multiset of roll numbers
against the approved set. VERIFIED requires: no missing, no unexpected, no
duplicate IDs, and observed count == approved count. If the destination cannot
be read, the result is PENDING_VERIFICATION, never VERIFIED.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.errors import PreconditionFailed
from app.core.hashing import sha256_hex
from app.integrations.drive_form import DestinationUnreadable
from app.integrations.registry import adapter_for
from app.models.entities import ApprovalRequest, Drive, UploadOperation, User, VerificationResult
from app.models.enums import ActorType, ExceptionKind, Severity, UploadStatus, VerificationStatus
from app.services import exceptions
from app.services.uploads import get_upload


def compare(approved: list[str], observed: list[str]) -> dict[str, Any]:
    approved_set = set(approved)
    counts = Counter(observed)
    return {
        "missing": sorted(approved_set - set(observed)),
        "unexpected": sorted(set(observed) - approved_set),
        "duplicates": sorted(r for r, c in counts.items() if c > 1),
        "expected_count": len(approved),
        "observed_count": len(observed),
    }


def verify(db: Session, upload_id: int, *, actor: str, run_id: int | None = None) -> VerificationResult:
    op = get_upload(db, upload_id)
    if op.status != UploadStatus.SUBMITTED:
        raise PreconditionFailed(f"Upload is {op.status}; only submitted uploads can be verified")
    destination = op.destination
    adapter = adapter_for(destination)
    batch = list(op.submitted_roll_numbers)
    # The destination should hold everything ever sent for this drive (first upload + supplements).
    approved = cumulative_submitted(db, op)
    method = f"{adapter.name}: GET roster page + receipt lookup by operation key"

    try:
        observed = adapter.read_roster(destination.form_ref)
        receipt = adapter.find_submission(destination.form_ref, op.operation_key)
    except DestinationUnreadable as exc:
        result = VerificationResult(
            upload_id=op.id, status=VerificationStatus.PENDING_VERIFICATION, method=method,
            expected_count=len(approved), observed_count=None, detail=f"Destination could not be read: {exc}",
        )
        db.add(result)
        db.flush()
        ledger.record(db, actor_type=ActorType.AGENT if run_id else ActorType.USER, actor_id=actor,
                      action="VERIFICATION_PENDING", entity_type="upload", entity_id=op.id,
                      operation_id=op.operation_key, run_id=run_id, drive_id=op.drive_id, details={"error": str(exc)})
        db.commit()
        return result

    cmp = compare(approved, observed.roll_numbers)
    problems = []
    if cmp["missing"]:
        problems.append(f"{len(cmp['missing'])} approved ID(s) missing at destination")
    if cmp["unexpected"]:
        problems.append(f"{len(cmp['unexpected'])} unexpected ID(s) at destination")
    if cmp["duplicates"]:
        problems.append(f"{len(cmp['duplicates'])} duplicated ID(s) at destination")
    if cmp["observed_count"] != cmp["expected_count"]:
        problems.append(f"count mismatch: expected {cmp['expected_count']}, observed {cmp['observed_count']}")
    if receipt is None:
        problems.append("destination has no submission for this operation key")
    elif receipt.candidate_count != len(batch):
        problems.append(f"destination receipt claims {receipt.candidate_count} candidates, this upload sent {len(batch)}")
    status = VerificationStatus.FAILED_VERIFICATION if problems else VerificationStatus.VERIFIED

    snapshot = {
        "roster_url": observed.url, "observed_roll_numbers": observed.roll_numbers,
        "page_total_label": observed.total_count_label,
        "receipt": receipt.__dict__ if receipt else None,
    }
    result = VerificationResult(
        upload_id=op.id, status=status, method=method, expected_count=cmp["expected_count"],
        observed_count=cmp["observed_count"], missing_roll_numbers=cmp["missing"],
        unexpected_roll_numbers=cmp["unexpected"], duplicate_roll_numbers=cmp["duplicates"],
        observed_snapshot=snapshot, detail="; ".join(problems) or "Exact ID set and count match",
    )
    db.add(result)
    db.flush()
    result.receipt_hash = sha256_hex(build_receipt(db, op, result))
    if problems:
        exceptions.raise_exception(
            db, kind=ExceptionKind.UPLOAD_DISCREPANCY, severity=Severity.BLOCKING,
            title=f"Verification failed for upload #{op.id}: {'; '.join(problems)}",
            dedupe_key=f"verification:{result.id}",
            detail={"upload_id": op.id, **cmp}, drive_id=op.drive_id, run_id=run_id,
        )
    ledger.record(db, actor_type=ActorType.AGENT if run_id else ActorType.USER, actor_id=actor,
                  action="VERIFICATION_" + status, entity_type="upload", entity_id=op.id,
                  operation_id=op.operation_key, run_id=run_id, drive_id=op.drive_id,
                  details={**cmp, "problems": problems, "receipt_hash": result.receipt_hash})
    db.commit()
    return result


def cumulative_submitted(db: Session, op: UploadOperation) -> list[str]:
    """Roll numbers sent by this and all earlier successful uploads for the same drive and destination."""
    rolls: list[str] = []
    for prior in db.scalars(select(UploadOperation).where(
        UploadOperation.drive_id == op.drive_id, UploadOperation.destination_id == op.destination_id,
        UploadOperation.status == UploadStatus.SUBMITTED, UploadOperation.id <= op.id,
    ).order_by(UploadOperation.id)):
        rolls.extend(r for r in prior.submitted_roll_numbers if r not in rolls)
    return rolls


def latest_verification(db: Session, upload_id: int) -> VerificationResult | None:
    return db.scalar(select(VerificationResult).where(VerificationResult.upload_id == upload_id)
                     .order_by(VerificationResult.id.desc()).limit(1))


def build_receipt(db: Session, op: UploadOperation, verification: VerificationResult | None) -> dict[str, Any]:
    proposal = op.proposal
    drive: Drive = op.drive
    approval = db.get(ApprovalRequest, op.approval_id)
    approver = db.get(User, approval.decided_by_id) if approval and approval.decided_by_id else None
    reasons: dict[str, int] = {}
    for row in proposal.excluded + proposal.held:
        for r in row.get("reasons", []):
            reasons[r] = reasons.get(r, 0) + 1
    return {
        "receipt_type": "PlacementOS upload receipt",
        "environment_note": "Destination is the LOCAL MOCK portal" if op.destination.kind == "LOCAL_MOCK_FORM" else "Organiser destination",
        "operation_key": op.operation_key,
        "drive": {"id": drive.id, "title": drive.title, "employer": drive.employer.name,
                  "starts_at": drive.starts_at.isoformat(), "ends_at": drive.ends_at.isoformat(), "timezone": drive.timezone},
        "destination": {"id": op.destination.id, "name": op.destination.name, "kind": op.destination.kind,
                        "base_url": op.destination.base_url, "form_ref": op.destination.form_ref},
        "proposal": {"id": proposal.id, "hash": proposal.proposal_hash, "criteria_version": proposal.criteria_version,
                     "schedule_version": proposal.schedule_version},
        "approval": {"id": approval.id if approval else None, "approved_by": approver.email if approver else None,
                     "approved_at": approval.decided_at.isoformat() if approval and approval.decided_at else None},
        "upload_kind": proposal.kind,
        "counts": {"eligible_uploaded": len(proposal.included_roll_numbers), "excluded": len(proposal.excluded),
                   "held": len(proposal.held), "previously_submitted": len(proposal.previously_submitted_roll_numbers),
                   "total_at_destination_expected": len(cumulative_submitted(db, op))},
        "previously_submitted_roll_numbers": proposal.previously_submitted_roll_numbers,
        "uploaded_roll_numbers": proposal.included_roll_numbers,
        "exclusion_reasons": reasons,
        "excluded": [{"roll_number": e["roll_number"], "reasons": e["reasons"]} for e in proposal.excluded],
        "held": [{"roll_number": h["roll_number"], "reasons": h["reasons"]} for h in proposal.held],
        "submission": {"status": op.status, "attempts": op.attempts, "destination_receipt": op.destination_receipt,
                       "completed_at": op.completed_at.isoformat() if op.completed_at else None},
        "verification": None if verification is None else {
            "id": verification.id, "status": verification.status, "method": verification.method,
            "expected_count": verification.expected_count, "observed_count": verification.observed_count,
            "missing": verification.missing_roll_numbers, "unexpected": verification.unexpected_roll_numbers,
            "duplicates": verification.duplicate_roll_numbers, "verified_at": verification.created_at.isoformat(),
            "detail": verification.detail,
        },
    }

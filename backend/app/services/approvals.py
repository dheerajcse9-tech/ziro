"""Roster proposals and version-bound human approval.

Rules enforced here (server-side, independent of the agent):
- Only users with the approver or admin role can approve or reject.
- Agents have no user identity, so they can never approve.
- Approving recomputes the roster from live state; if the proposal hash no
  longer matches (criteria, schedule, destination, roster or consent changed),
  the approval is invalidated instead of granted.
- Any later change that alters the hash invalidates open approvals.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.errors import Conflict, NotFound, PermissionDenied, PreconditionFailed
from app.db.base import utcnow
from app.models.entities import (
    ApprovalRequest,
    Drive,
    DriveAssignment,
    RosterProposal,
    User,
)
from app.models.enums import (
    ActorType,
    ApprovalStatus,
    AssignmentStatus,
    ExceptionKind,
    ProposalStatus,
    Role,
    Severity,
)
from app.services import exceptions
from app.services.roster import RosterEvaluation, evaluate_drive

APPROVER_ROLES = {Role.APPROVER}
OPEN_APPROVAL = (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)


def create_proposal(
    db: Session, drive: Drive, evaluation: RosterEvaluation, *, run_id: int | None, requested_by: str,
    requested_for_user_id: int | None,
) -> tuple[RosterProposal, ApprovalRequest]:
    if drive.destination is None:
        raise PreconditionFailed("Drive has no destination configured; cannot propose an upload")
    if not drive.destination.is_enabled:
        raise PreconditionFailed("Drive destination is disabled")
    if evaluation.is_supplement and not evaluation.to_upload:
        raise PreconditionFailed("All eligible students were already sent; nothing new for a supplementary upload")
    proposal_hash = evaluation.proposal_hash()

    # Supersede older open proposals for the same drive.
    for old in db.scalars(select(RosterProposal).where(
        RosterProposal.drive_id == drive.id,
        RosterProposal.status.in_([ProposalStatus.PENDING_APPROVAL, ProposalStatus.APPROVED]),
    )):
        _invalidate_proposal(db, old, "Superseded by a newer proposal", actor="system")

    proposal = RosterProposal(
        drive_id=drive.id, run_id=run_id, criteria_version=evaluation.criteria.version,
        schedule_version=evaluation.schedule_version, destination_id=drive.destination.id,
        destination_fingerprint=evaluation.destination_fingerprint, roster_fingerprint=evaluation.roster_fingerprint(),
        included_roll_numbers=[o.roll_number for o in evaluation.to_upload],
        kind="SUPPLEMENT" if evaluation.is_supplement else "FULL",
        previously_submitted_roll_numbers=evaluation.previously_submitted,
        excluded=[{"roll_number": o.roll_number, "full_name": o.full_name, "decision": o.decision, "reasons": o.reasons}
                  for o in evaluation.excluded],
        held=[{"roll_number": o.roll_number, "full_name": o.full_name, "reasons": o.reasons, "conflicts": o.conflicts}
              for o in evaluation.held],
        unresolved_issues=_unresolved(evaluation),
        proposal_hash=proposal_hash, status=ProposalStatus.PENDING_APPROVAL,
    )
    db.add(proposal)
    db.flush()
    approval = ApprovalRequest(
        proposal_id=proposal.id, proposal_hash=proposal_hash, status=ApprovalStatus.PENDING,
        requested_by=requested_by, requested_for_user_id=requested_for_user_id,
    )
    db.add(approval)

    # Tentatively reserve the included students on this drive so that other
    # overlapping drives see the clash *before* anything is confirmed.
    existing = {a.student_id: a for a in db.scalars(select(DriveAssignment).where(DriveAssignment.drive_id == drive.id))}
    included_ids = {o.student_id for o in evaluation.included}
    for o in evaluation.to_upload:
        a = existing.get(o.student_id)
        if a is None:
            db.add(DriveAssignment(student_id=o.student_id, drive_id=drive.id, status=AssignmentStatus.PROPOSED,
                                   source=f"proposal:{proposal.id}"))
        elif a.status in (AssignmentStatus.WITHDRAWN, AssignmentStatus.HELD_CONFLICT):
            a.status, a.source = AssignmentStatus.PROPOSED, f"proposal:{proposal.id}"
    for o in evaluation.held:
        a = existing.get(o.student_id)
        if a is None:
            db.add(DriveAssignment(student_id=o.student_id, drive_id=drive.id, status=AssignmentStatus.HELD_CONFLICT,
                                   source=f"proposal:{proposal.id}", note="Held: schedule clash"))
        elif a.status != AssignmentStatus.CONFIRMED:
            a.status = AssignmentStatus.HELD_CONFLICT
    for sid, a in existing.items():
        if sid not in included_ids and a.status == AssignmentStatus.PROPOSED and sid not in {o.student_id for o in evaluation.held}:
            a.status, a.note = AssignmentStatus.WITHDRAWN, f"Not in proposal {proposal.id}"
    db.flush()
    ledger.record(
        db, actor_type=ActorType.AGENT if run_id else ActorType.USER, actor_id=requested_by,
        action="APPROVAL_REQUESTED", entity_type="approval", entity_id=approval.id, run_id=run_id, drive_id=drive.id,
        details={"proposal_id": proposal.id, "proposal_hash": proposal_hash, "included": len(proposal.included_roll_numbers),
                 "excluded": len(proposal.excluded), "held": len(proposal.held), "kind": proposal.kind,
                 "previously_submitted": len(proposal.previously_submitted_roll_numbers),
                 "criteria_version": proposal.criteria_version, "schedule_version": proposal.schedule_version},
    )
    return proposal, approval


def _unresolved(ev: RosterEvaluation) -> list[dict]:
    issues = []
    for o in ev.no_longer_eligible:
        issues.append({"type": "WITHDRAWAL_REQUIRED", "roll_number": o.roll_number,
                       "detail": "Already sent to the employer but no longer eligible: " + "; ".join(o.reasons)})
    for o in ev.held:
        issues.append({"type": "SCHEDULE_CONFLICT", "roll_number": o.roll_number, "detail": "; ".join(o.reasons)})
    for o in ev.outcomes:
        if o.decision == "UNKNOWN":
            issues.append({"type": "INSUFFICIENT_DATA", "roll_number": o.roll_number, "detail": "; ".join(o.reasons)})
    return issues


def _invalidate_proposal(db: Session, proposal: RosterProposal, reason: str, actor: str) -> None:
    proposal.status = ProposalStatus.INVALIDATED
    proposal.invalidated_reason = reason
    for appr in db.scalars(select(ApprovalRequest).where(
        ApprovalRequest.proposal_id == proposal.id, ApprovalRequest.status.in_(OPEN_APPROVAL)
    )):
        was = appr.status
        appr.status = ApprovalStatus.INVALIDATED
        appr.invalidated_reason = reason
        ledger.record(db, actor_type=ActorType.SYSTEM, actor_id=actor, action="APPROVAL_INVALIDATED",
                      entity_type="approval", entity_id=appr.id, drive_id=proposal.drive_id,
                      details={"reason": reason, "previous_status": was, "proposal_hash": proposal.proposal_hash})
        if was == ApprovalStatus.APPROVED:
            exceptions.raise_exception(
                db, kind=ExceptionKind.APPROVAL_INVALIDATED, severity=Severity.WARNING,
                title=f"Approved roster for drive #{proposal.drive_id} was invalidated: {reason}",
                dedupe_key=f"approval-invalidated:{appr.id}",
                detail={"approval_id": appr.id, "proposal_id": proposal.id, "reason": reason},
                drive_id=proposal.drive_id,
            )
    _notify_run(db, proposal, reason)
    # Release tentative reservations made by this proposal.
    for a in db.scalars(select(DriveAssignment).where(
        DriveAssignment.drive_id == proposal.drive_id, DriveAssignment.status == AssignmentStatus.PROPOSED,
        DriveAssignment.source == f"proposal:{proposal.id}",
    )):
        a.status, a.note = AssignmentStatus.WITHDRAWN, f"Proposal {proposal.id} invalidated"


def _notify_run(db: Session, proposal: RosterProposal, reason: str) -> None:
    """Move the originating agent run back to NEEDS_REVIEW so it never acts on a stale approval."""
    if not proposal.run_id:
        return
    from app.agent.events import emit
    from app.models.entities import AgentRun
    from app.models.enums import RunStatus

    run = db.get(AgentRun, proposal.run_id)
    if run is None or run.status not in (RunStatus.AWAITING_APPROVAL, RunStatus.APPROVED, RunStatus.READY_FOR_APPROVAL):
        return
    run.status = RunStatus.NEEDS_REVIEW
    run.current_step = "approval_invalidated"
    run.pending_question = {
        "type": "approval_invalidated",
        "question": f"The proposal was invalidated ({reason}). Re-run evaluation and create a fresh proposal?",
        "options": [{"value": "rerun", "label": "Re-evaluate and propose again"},
                    {"value": "stop", "label": "Stop this run"}],
    }
    run.state = {**(run.state or {}), "next_node": None}
    emit(db, run, "INTERRUPT", node="approval_gate", status="NEEDS_REVIEW",
         message=f"Approval invalidated: {reason}", payload={"proposal_id": proposal.id})


def revalidate_open_approvals(db: Session, reason: str, drive_ids: set[int] | None = None) -> list[int]:
    """Recompute every open approval; invalidate those whose bound hash no longer matches."""
    invalidated: list[int] = []
    q = select(RosterProposal).where(RosterProposal.status.in_([ProposalStatus.PENDING_APPROVAL, ProposalStatus.APPROVED]))
    if drive_ids is not None:
        q = q.where(RosterProposal.drive_id.in_(drive_ids))
    for proposal in list(db.scalars(q)):
        drive = db.get(Drive, proposal.drive_id)
        try:
            current_hash = evaluate_drive(db, drive).proposal_hash()
        except PreconditionFailed as exc:
            current_hash = f"unavailable: {exc.message}"
        if current_hash != proposal.proposal_hash:
            _invalidate_proposal(db, proposal, reason, actor="system")
            invalidated.append(proposal.id)
    db.flush()
    return invalidated


def _require_approver(user: User) -> None:
    if user.role not in APPROVER_ROLES or not user.is_active:
        raise PermissionDenied("Only an active approver or admin can decide approval requests")


def get_approval(db: Session, approval_id: int) -> ApprovalRequest:
    appr = db.get(ApprovalRequest, approval_id)
    if appr is None:
        raise NotFound(f"Approval {approval_id} not found")
    return appr


def approve(db: Session, approval_id: int, user: User, *, expected_hash: str, note: str | None = None) -> ApprovalRequest:
    _require_approver(user)
    appr = get_approval(db, approval_id)
    if appr.status != ApprovalStatus.PENDING:
        raise Conflict(f"Approval is {appr.status}, not PENDING")
    if expected_hash != appr.proposal_hash:
        raise PreconditionFailed("The proposal you reviewed is not the one pending approval (hash mismatch)",
                                 details={"pending_hash": appr.proposal_hash, "reviewed_hash": expected_hash})
    proposal = appr.proposal
    drive = db.get(Drive, proposal.drive_id)
    live_hash = evaluate_drive(db, drive).proposal_hash()
    if live_hash != appr.proposal_hash:
        _invalidate_proposal(db, proposal, "Roster, criteria, schedule, consent or destination changed since the proposal was made", actor=f"user:{user.id}")
        db.commit()  # persist the invalidation even though the approval request fails
        raise PreconditionFailed("Proposal is stale: underlying data changed. A new proposal is required.",
                                 details={"bound_hash": appr.proposal_hash, "live_hash": live_hash})
    appr.status = ApprovalStatus.APPROVED
    appr.decided_by_id = user.id
    appr.decided_at = utcnow()
    appr.decision_note = note
    proposal.status = ProposalStatus.APPROVED
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="APPROVAL_GRANTED",
                  entity_type="approval", entity_id=appr.id, drive_id=drive.id, run_id=proposal.run_id,
                  details={"proposal_hash": appr.proposal_hash, "approver": user.email, "role": user.role,
                           "included": len(proposal.included_roll_numbers), "note": note})
    db.flush()
    return appr


def reject(db: Session, approval_id: int, user: User, *, reason: str | None) -> ApprovalRequest:
    _require_approver(user)
    appr = get_approval(db, approval_id)
    if appr.status != ApprovalStatus.PENDING:
        raise Conflict(f"Approval is {appr.status}, not PENDING")
    appr.status = ApprovalStatus.REJECTED
    appr.decided_by_id = user.id
    appr.decided_at = utcnow()
    appr.decision_note = reason
    proposal = appr.proposal
    proposal.status = ProposalStatus.REJECTED
    for a in db.scalars(select(DriveAssignment).where(
        DriveAssignment.drive_id == proposal.drive_id, DriveAssignment.status == AssignmentStatus.PROPOSED,
        DriveAssignment.source == f"proposal:{proposal.id}",
    )):
        a.status, a.note = AssignmentStatus.WITHDRAWN, f"Proposal {proposal.id} rejected"
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="APPROVAL_REJECTED",
                  entity_type="approval", entity_id=appr.id, drive_id=proposal.drive_id, run_id=proposal.run_id,
                  details={"reason": reason, "proposal_hash": appr.proposal_hash})
    db.flush()
    return appr

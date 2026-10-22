"""Approval Review Agent and the follow-up planning used by the Form Builder Agent.

The review re-derives everything an approver needs from the live database
instead of trusting the proposal: is the roster unchanged, does every listed
student still meet the criteria and have consent, who was left out and why,
who could still qualify if they supplied details or consent, and how close the
deadlines are. It is advisory: it cannot approve or change anything.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db.base import utcnow
from app.domain.schedule import to_local
from app.models.entities import ApprovalRequest, Drive, Student, StudentRequest
from app.models.enums import ActorType, ConsentScope, Decision, RequestKind, RequestStatus
from app.services.outreach import RULE_TO_FIELD
from app.services.roster import current_criteria, evaluate_drive

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


def _check(key: str, label: str, status: str, detail: str, items: list | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "detail": detail, "items": items or []}


def followup_candidates(db: Session, drive: Drive, ev=None) -> list[dict[str, Any]]:
    """Students who could still qualify: missing details and/or no consent answer (and did not say no)."""
    ev = ev or evaluate_drive(db, drive)
    criteria = current_criteria(db, drive.id)
    per_drive = criteria is not None and criteria.required_consent_scope == ConsentScope.DRIVE_REGISTRATION
    now = utcnow()
    open_invites = set(db.scalars(select(StudentRequest.student_id).where(
        StudentRequest.drive_id == drive.id, StudentRequest.kind == RequestKind.DRIVE_INVITATION,
        StudentRequest.status == RequestStatus.SENT, StudentRequest.expires_at > now)))
    open_forms = set(db.scalars(select(StudentRequest.student_id).where(
        StudentRequest.drive_id == drive.id, StudentRequest.kind == RequestKind.DATA_REQUEST,
        StudentRequest.status == RequestStatus.SENT, StudentRequest.expires_at > now)))
    out = []
    for o in ev.outcomes:
        if o.decision == Decision.INELIGIBLE or o.already_submitted:
            continue
        if o.consent is not None and not o.consent.get("granted"):
            continue  # the student said no
        fields = [RULE_TO_FIELD[m] for m in o.missing_evidence if m in RULE_TO_FIELD] if o.decision == Decision.UNKNOWN else []
        if per_drive and not o.consent_ok:  # no yes/no answer yet (a "no" was skipped above)
            fields.append("consent")
        if fields:
            out.append({"student_id": o.student_id, "roll_number": o.roll_number, "full_name": o.full_name,
                        "fields": fields, "form_open": o.student_id in open_forms,
                        "consent_form_open": o.student_id in open_invites})
    return out


def review(db: Session, approval: ApprovalRequest, actor: str = "agent:review") -> dict[str, Any]:
    proposal = approval.proposal
    drive = proposal.drive
    tz = drive.timezone
    now = utcnow()
    checks: list[dict[str, Any]] = []
    ev = evaluate_drive(db, drive)

    try:
        live_hash = ev.proposal_hash()
    except DomainError as exc:
        live_hash = None
        checks.append(_check("hash", "Roster unchanged since proposal", FAIL, exc.message))
    if live_hash is not None:
        same = live_hash == approval.proposal_hash
        checks.append(_check("hash", "Roster unchanged since proposal", PASS if same else FAIL,
                             "The live roster matches what the Roster Agent proposed" if same else
                             "Something changed after the proposal (student data, consent, criteria or schedule); "
                             "approving would be refused — the Roster Agent must re-propose"))

    outcomes = {o.roll_number: o for o in ev.outcomes}
    bad = []
    for roll in proposal.included_roll_numbers:
        o = outcomes.get(roll)
        if o is None:
            bad.append({"roll_number": roll, "problem": "No longer in the student registry"})
        elif o.decision != Decision.ELIGIBLE:
            bad.append({"roll_number": roll, "problem": "; ".join(o.reasons) or o.decision})
        elif not o.consent_ok:
            bad.append({"roll_number": roll, "problem": "Consent not granted"})
        elif o.roster_status != "INCLUDED":
            bad.append({"roll_number": roll, "problem": "; ".join(o.reasons) or o.roster_status})
    n = len(proposal.included_roll_numbers)
    checks.append(_check("students", f"Every listed student re-checked ({n})", FAIL if bad else PASS,
                         f"{len(bad)} listed student(s) no longer qualify" if bad else
                         f"All {n} listed student(s) meet the criteria, have consent and have no unresolved clash", bad))

    declined = [o for o in ev.outcomes if o.decision == Decision.ELIGIBLE and o.consent is not None and not o.consent.get("granted")]
    ineligible = [o for o in ev.outcomes if o.decision == Decision.INELIGIBLE]
    held = [o for o in ev.outcomes if o.roster_status == "HELD"]
    candidates = followup_candidates(db, drive, ev)
    missing_details = [c for c in candidates if any(f != "consent" for f in c["fields"])]
    no_answer = [c for c in candidates if c["fields"] == ["consent"]]
    checks.append(_check("excluded", "Who was left out and why", INFO,
                         f"{len(ineligible)} do not meet the criteria, {len(declined)} said no, {len(held)} held for a "
                         f"schedule clash, {len(missing_details)} missing details, {len(no_answer)} have not answered consent"))
    if candidates:
        not_asked = [c for c in candidates if not c["form_open"]]
        checks.append(_check("followup", "Students who could still qualify", WARN,
                             f"{len(candidates)} student(s) could qualify with missing details or a consent answer; "
                             f"{len(candidates) - len(not_asked)} already have an open form"
                             + (f", {len(not_asked)} not asked yet" if not_asked else ""),
                             [{"roll_number": c["roll_number"], "full_name": c["full_name"], "fields": c["fields"],
                               "form_open": c["form_open"]} for c in candidates]))
    else:
        checks.append(_check("followup", "Students who could still qualify", PASS, "Nobody is waiting on details or consent"))

    self_reported = []
    for roll in proposal.included_roll_numbers:
        s = db.scalar(select(Student).where(Student.roll_number == roll))
        if s is not None and s.self_reported:
            self_reported.append({"roll_number": roll, "fields": sorted({x["field"] for x in s.self_reported})})
    if self_reported:
        checks.append(_check("self_reported", "Student-supplied data", WARN,
                             f"{len(self_reported)} listed student(s) supplied some of their own data through a form; "
                             f"verify against official records if required", self_reported))

    deadline_bits = []
    status = PASS
    if drive.registration_deadline:
        open_ = drive.registration_deadline > now
        deadline_bits.append(f"registration {'closes' if open_ else 'closed'} {to_local(drive.registration_deadline, tz):%d %b %H:%M}")
        if open_:
            status = WARN
    if drive.roster_due_at:
        hours = (drive.roster_due_at - now).total_seconds() / 3600
        deadline_bits.append(f"company wants the list by {to_local(drive.roster_due_at, tz):%d %b %H:%M}"
                             + (f" ({hours:.0f} h left)" if hours > 0 else " (overdue)"))
        if hours <= 0 and status == PASS:
            status = WARN
    deadline_bits.append(f"drive starts {to_local(drive.starts_at, tz):%d %b %H:%M}")
    if drive.starts_at <= now:
        status = FAIL
    checks.append(_check("deadlines", "Deadlines", status, "; ".join(deadline_bits)
                         + ("; students can still register, so a supplementary list may follow" if status == WARN and
                            drive.registration_deadline and drive.registration_deadline > now else "")))

    dest = drive.destination
    allowed = dest is not None and dest.is_enabled and get_settings().destination_host_allowed(dest.base_url)
    checks.append(_check("destination", "Upload destination", PASS if allowed else FAIL,
                         f"{dest.name} ({dest.kind})" if allowed else "Destination missing, disabled or not on the allowlist"))

    verdict = "DO_NOT_APPROVE" if any(c["status"] == FAIL for c in checks) else (
        "READY_WITH_NOTES" if any(c["status"] == WARN for c in checks) else "READY")
    result = {"approval_id": approval.id, "proposal_id": proposal.id, "drive_id": drive.id, "verdict": verdict,
              "checks": checks, "followup_candidates": len(candidates), "reviewed_at": now.isoformat(),
              "live_hash_matches": live_hash == approval.proposal_hash}
    ledger.record(db, actor_type=ActorType.AGENT, actor_id=actor, action="APPROVAL_REVIEWED", entity_type="approval",
                  entity_id=approval.id, drive_id=drive.id,
                  details={"verdict": verdict, "fails": [c["key"] for c in checks if c["status"] == FAIL],
                           "warnings": [c["key"] for c in checks if c["status"] == WARN]})
    return result

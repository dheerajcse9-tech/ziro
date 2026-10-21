"""Roster evaluation for a drive: eligibility + consent + schedule clashes.

`evaluate_drive` is a pure read of current state (no writes) unless
`persist=True`. The same function backs the agent tools, the what-if
simulator, approval re-validation and pre-execution re-validation, so every
consequential step recomputes from the same deterministic logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import PreconditionFailed
from app.core.hashing import sha256_hex
from app.domain.eligibility import CriteriaSpec, StudentFacts, evaluate
from app.domain.schedule import Interval, overlap_window, overlaps
from app.models.entities import (
    Destination,
    Drive,
    DriveAssignment,
    DriveCriteria,
    EligibilityDecision,
    Student,
)
from app.models.enums import (
    AssignmentStatus,
    Decision,
    DriveStatus,
    ExceptionKind,
    RosterStatus,
    Severity,
)
from app.services import app_settings, exceptions
from app.services.consent import current_consent

ACTIVE_ASSIGNMENT = (AssignmentStatus.PROPOSED, AssignmentStatus.CONFIRMED)


@dataclass
class StudentOutcome:
    student_id: int
    roll_number: str
    full_name: str
    branch: str | None
    cgpa: float | None
    active_backlogs: int | None
    record_version: int
    decision: str
    roster_status: str
    rule_results: list[dict]
    reasons: list[str]
    missing_evidence: list[str]
    consent_ok: bool
    consent: dict | None
    conflicts: list[dict] = field(default_factory=list)
    already_submitted: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "roll_number": self.roll_number,
            "full_name": self.full_name,
            "branch": self.branch,
            "cgpa": self.cgpa,
            "active_backlogs": self.active_backlogs,
            "decision": self.decision,
            "roster_status": self.roster_status,
            "rule_results": self.rule_results,
            "reasons": self.reasons,
            "missing_evidence": self.missing_evidence,
            "consent_ok": self.consent_ok,
            "conflicts": self.conflicts,
            "already_submitted": self.already_submitted,
        }


@dataclass
class RosterEvaluation:
    drive_id: int
    criteria: CriteriaSpec
    schedule_version: int
    destination_fingerprint: str | None
    outcomes: list[StudentOutcome]
    # Roll numbers already submitted to the employer for this drive (CONFIRMED assignments).
    already_submitted: frozenset[str] = frozenset()

    @property
    def included(self) -> list[StudentOutcome]:
        return sorted((o for o in self.outcomes if o.roster_status == RosterStatus.INCLUDED), key=lambda o: o.roll_number)

    @property
    def is_supplement(self) -> bool:
        return bool(self.already_submitted)

    @property
    def to_upload(self) -> list[StudentOutcome]:
        """Included students not yet sent: the whole roster first, only newcomers in a supplement."""
        return [o for o in self.included if o.roll_number not in self.already_submitted]

    @property
    def previously_submitted(self) -> list[str]:
        return sorted(self.already_submitted)

    @property
    def no_longer_eligible(self) -> list[StudentOutcome]:
        """Students already sent to the employer who would not be included today."""
        return [o for o in self.outcomes if o.roll_number in self.already_submitted and o.roster_status != RosterStatus.INCLUDED]

    @property
    def excluded(self) -> list[StudentOutcome]:
        return [o for o in self.outcomes if o.roster_status == RosterStatus.EXCLUDED]

    @property
    def held(self) -> list[StudentOutcome]:
        return [o for o in self.outcomes if o.roster_status == RosterStatus.HELD]

    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.outcomes),
            "eligible": sum(o.decision == Decision.ELIGIBLE for o in self.outcomes),
            "ineligible": sum(o.decision == Decision.INELIGIBLE for o in self.outcomes),
            "unknown": sum(o.decision == Decision.UNKNOWN for o in self.outcomes),
            "included": len(self.included),
            "excluded": len(self.excluded),
            "held": len(self.held),
            "consent_missing": sum(o.decision == Decision.ELIGIBLE and not o.consent_ok for o in self.outcomes),
            "already_submitted": len(self.already_submitted),
            "new_to_upload": len(self.to_upload),
            "no_longer_eligible": len(self.no_longer_eligible),
        }

    def exclusion_reason_summary(self) -> dict[str, int]:
        summary: dict[str, int] = {}
        for o in self.excluded + self.held:
            for reason in o.reasons:
                category = _reason_category(reason)
                summary[category] = summary.get(category, 0) + 1
        return summary

    def roster_fingerprint(self) -> str:
        return sha256_hex([[o.roll_number, o.record_version] for o in self.to_upload])

    def proposal_hash(self) -> str:
        if not self.destination_fingerprint:
            raise PreconditionFailed("Drive has no destination configured")
        body = {
            "drive_id": self.drive_id,
            "criteria_version": self.criteria.version,
            "schedule_version": self.schedule_version,
            "destination": self.destination_fingerprint,
            "included": [[o.roll_number, o.record_version] for o in self.to_upload],
        }
        if self.already_submitted:
            body["previously_submitted"] = self.previously_submitted
        return sha256_hex(body)


def _reason_category(reason: str) -> str:
    r = reason.lower()
    if "cgpa" in r and "missing" in r:
        return "Missing CGPA"
    if "cgpa" in r:
        return "CGPA below minimum"
    if "backlog" in r and "missing" in r:
        return "Missing backlog data"
    if "backlog" in r:
        return "Active backlogs"
    if "branch" in r:
        return "Branch not eligible" if "not in" in r else "Missing/unrecognised branch"
    if "consent" in r:
        return "Consent not granted"
    if "clash" in r or "conflict" in r:
        return "Schedule clash"
    if "batch" in r or "graduation" in r:
        return "Batch not eligible"
    return "Other"


def destination_fingerprint(dest: Destination | None) -> str | None:
    if dest is None:
        return None
    return sha256_hex({
        "id": dest.id, "kind": dest.kind, "base_url": dest.base_url.rstrip("/"),
        "form_ref": dest.form_ref, "config_version": dest.config_version, "enabled": dest.is_enabled,
    })


def current_criteria(db: Session, drive_id: int) -> DriveCriteria | None:
    return db.scalar(
        select(DriveCriteria).where(DriveCriteria.drive_id == drive_id).order_by(DriveCriteria.version.desc()).limit(1)
    )


def criteria_spec(row: DriveCriteria) -> CriteriaSpec:
    return CriteriaSpec(
        version=row.version,
        min_cgpa=row.min_cgpa,
        backlog_policy=row.backlog_policy,
        max_active_backlogs=row.max_active_backlogs,
        allowed_branches=tuple(sorted(row.allowed_branches or [])),
        graduation_years=tuple(sorted(row.graduation_years or [])),
        required_consent_scope=row.required_consent_scope,
    )


def _conflict_map(
    db: Session, drive: Drive, interval: Interval, student_ids: list[int], buffer_minutes: int,
    schedule_overrides: dict[int, Interval] | None = None,
) -> dict[int, list[dict]]:
    """Map student_id -> clashing active assignments on *other* drives."""
    rows = db.execute(
        select(DriveAssignment, Drive)
        .join(Drive, Drive.id == DriveAssignment.drive_id)
        .where(
            DriveAssignment.student_id.in_(student_ids),
            DriveAssignment.drive_id != drive.id,
            DriveAssignment.status.in_(ACTIVE_ASSIGNMENT),
            Drive.status.notin_([DriveStatus.CANCELLED, DriveStatus.CLOSED]),
        )
    ).all()
    out: dict[int, list[dict]] = {}
    for assignment, other in rows:
        other_iv = (schedule_overrides or {}).get(other.id) or Interval(other.id, other.starts_at, other.ends_at)
        if overlaps(interval, other_iv, buffer_minutes):
            window = overlap_window(interval, other_iv)
            out.setdefault(assignment.student_id, []).append({
                "other_drive_id": other.id,
                "other_drive_title": other.title,
                "other_assignment_status": assignment.status,
                "other_starts_at": other_iv.start.isoformat(),
                "other_ends_at": other_iv.end.isoformat(),
                "overlap_start": window[0].isoformat() if window else None,
                "overlap_end": window[1].isoformat() if window else None,
                "buffer_minutes": buffer_minutes,
            })
    return out


def conflict_key(drive_id: int, student_id: int, other_drive_id: int) -> str:
    a, b = sorted([drive_id, other_drive_id])
    return f"conflict:{a}:{b}:student:{student_id}"


def evaluate_drive(
    db: Session,
    drive: Drive,
    *,
    criteria_override: CriteriaSpec | None = None,
    interval_override: Interval | None = None,
    persist: bool = False,
    run_id: int | None = None,
) -> RosterEvaluation:
    criteria_row = current_criteria(db, drive.id)
    if criteria_override is not None:
        criteria = criteria_override
    elif criteria_row is not None:
        criteria = criteria_spec(criteria_row)
    else:
        raise PreconditionFailed("Drive has no eligibility criteria configured", details={"drive_id": drive.id})

    students = list(db.scalars(select(Student).order_by(Student.roll_number)))
    ids = [s.id for s in students]
    consent = current_consent(db, ids, criteria.required_consent_scope)
    interval = interval_override or Interval(drive.id, drive.starts_at, drive.ends_at)
    buffer_minutes = int(app_settings.get(db, "schedule_buffer_minutes") or 0)
    clashes = _conflict_map(db, drive, interval, ids, buffer_minutes)
    submitted_ids = set(db.scalars(select(DriveAssignment.student_id).where(
        DriveAssignment.drive_id == drive.id, DriveAssignment.status == AssignmentStatus.CONFIRMED)))

    outcomes: list[StudentOutcome] = []
    for s in students:
        facts = StudentFacts(s.id, s.roll_number, s.cgpa, s.active_backlogs, s.branch, s.graduation_year)
        result = evaluate(facts, criteria)
        reasons = list(result.reasons)
        consent_info = consent.get(s.id)
        consent_ok = bool(consent_info and consent_info["granted"])
        student_conflicts = clashes.get(s.id, [])

        if result.decision != Decision.ELIGIBLE:
            status = RosterStatus.EXCLUDED
        elif not consent_ok:
            status = RosterStatus.EXCLUDED
            reasons.append(
                "Consent to share with employers revoked" if consent_info else "Consent to share with employers not recorded"
            )
        elif student_conflicts:
            status = RosterStatus.HELD
            for c in student_conflicts:
                exc = exceptions.get_by_key(db, conflict_key(drive.id, s.id, c["other_drive_id"]))
                preferred = (exc.detail or {}).get("preferred_drive_id") if exc and exc.resolution == "PREFER_DRIVE" else None
                c["resolution"] = exc.resolution if exc else None
                c["preferred_drive_id"] = preferred
                if preferred is not None and preferred != drive.id:
                    status = RosterStatus.EXCLUDED
                    reasons.append(f"Schedule clash with '{c['other_drive_title']}': officer assigned the student to that drive")
            if status == RosterStatus.HELD:
                titles = ", ".join(c["other_drive_title"] for c in student_conflicts)
                reasons.append(f"Schedule clash with {titles}: held for officer resolution")
        else:
            status = RosterStatus.INCLUDED

        outcomes.append(StudentOutcome(
            student_id=s.id, roll_number=s.roll_number, full_name=s.full_name, branch=s.branch,
            cgpa=s.cgpa, active_backlogs=s.active_backlogs, record_version=s.record_version,
            decision=result.decision, roster_status=status,
            rule_results=[r.as_dict() for r in result.rule_results], reasons=reasons,
            missing_evidence=result.missing_evidence, consent_ok=consent_ok, consent=consent_info,
            conflicts=student_conflicts, already_submitted=s.id in submitted_ids,
        ))

    evaluation = RosterEvaluation(
        drive_id=drive.id,
        criteria=criteria,
        schedule_version=drive.schedule_version,
        destination_fingerprint=destination_fingerprint(drive.destination),
        outcomes=outcomes,
        already_submitted=frozenset(o.roll_number for o in outcomes if o.already_submitted),
    )
    if persist:
        _persist(db, drive, evaluation, run_id)
    return evaluation


def _persist(db: Session, drive: Drive, ev: RosterEvaluation, run_id: int | None) -> None:
    db.execute(
        update(EligibilityDecision)
        .where(EligibilityDecision.drive_id == drive.id, EligibilityDecision.is_stale.is_(False))
        .values(is_stale=True)
    )
    for o in ev.no_longer_eligible:
        exceptions.raise_exception(
            db, kind=ExceptionKind.WITHDRAWAL_REQUIRED, severity=Severity.BLOCKING,
            title=f"{o.roll_number} was already sent to {drive.title} but is no longer eligible",
            dedupe_key=f"withdrawal:{drive.id}:{o.student_id}:v{o.record_version}:c{ev.criteria.version}",
            detail={"roll_number": o.roll_number, "reasons": o.reasons,
                    "action": "Ask the employer to remove this student; PlacementOS never deletes from an employer form automatically."},
            drive_id=drive.id, student_id=o.student_id, run_id=run_id,
        )
    for o in ev.outcomes:
        db.add(EligibilityDecision(
            drive_id=drive.id, run_id=run_id, criteria_version=ev.criteria.version,
            student_id=o.student_id, roll_number=o.roll_number, student_record_version=o.record_version,
            decision=o.decision, roster_status=o.roster_status, rule_results=o.rule_results,
            reasons=o.reasons, missing_evidence=o.missing_evidence, consent_ok=o.consent_ok,
            conflicts=o.conflicts,
        ))
        if o.decision == Decision.UNKNOWN:
            exceptions.raise_exception(
                db, kind=ExceptionKind.MISSING_STUDENT_DATA, severity=Severity.WARNING,
                title=f"{o.roll_number}: insufficient data to decide eligibility for {drive.title}",
                dedupe_key=f"unknown:{drive.id}:{o.student_id}:v{o.record_version}:c{ev.criteria.version}",
                detail={"roll_number": o.roll_number, "missing": o.missing_evidence, "reasons": o.reasons},
                drive_id=drive.id, student_id=o.student_id, run_id=run_id,
            )
        if o.decision == Decision.ELIGIBLE and not o.consent_ok:
            exceptions.raise_exception(
                db, kind=ExceptionKind.MISSING_CONSENT, severity=Severity.INFO,
                title=f"{o.roll_number}: eligible for {drive.title} but excluded (no employer-sharing consent)",
                dedupe_key=f"consent:{drive.id}:{o.student_id}",
                detail={"roll_number": o.roll_number, "consent": o.consent},
                drive_id=drive.id, student_id=o.student_id, run_id=run_id,
            )
        for c in o.conflicts if o.roster_status == RosterStatus.HELD else []:
            if c.get("resolution"):
                continue
            exceptions.raise_exception(
                db, kind=ExceptionKind.SCHEDULE_CONFLICT, severity=Severity.BLOCKING,
                title=f"{o.roll_number}: '{drive.title}' clashes with '{c['other_drive_title']}'",
                dedupe_key=conflict_key(drive.id, o.student_id, c["other_drive_id"]),
                detail={"roll_number": o.roll_number, "drive_id": drive.id, "drive_title": drive.title, **c},
                drive_id=drive.id, student_id=o.student_id, run_id=run_id,
            )
    db.flush()


def drive_pair_conflicts(db: Session) -> list[dict]:
    """All pairs of active drives whose schedules overlap, with affected students."""
    buffer_minutes = int(app_settings.get(db, "schedule_buffer_minutes") or 0)
    drives = list(db.scalars(
        select(Drive).where(Drive.status.notin_([DriveStatus.CANCELLED, DriveStatus.CLOSED])).order_by(Drive.starts_at)
    ))
    assignments: dict[int, dict[int, str]] = {}
    for a in db.scalars(select(DriveAssignment).where(DriveAssignment.status != AssignmentStatus.WITHDRAWN)):
        assignments.setdefault(a.drive_id, {})[a.student_id] = a.status
    pairs = []
    for i, a in enumerate(drives):
        for b in drives[i + 1:]:
            ia, ib = Interval(a.id, a.starts_at, a.ends_at), Interval(b.id, b.starts_at, b.ends_at)
            if not overlaps(ia, ib, buffer_minutes):
                continue
            window = overlap_window(ia, ib)
            shared = set(assignments.get(a.id, {})) & set(assignments.get(b.id, {}))
            pairs.append({
                "drive_a": {"id": a.id, "title": a.title, "starts_at": a.starts_at.isoformat(), "ends_at": a.ends_at.isoformat(), "timezone": a.timezone},
                "drive_b": {"id": b.id, "title": b.title, "starts_at": b.starts_at.isoformat(), "ends_at": b.ends_at.isoformat(), "timezone": b.timezone},
                "overlap_start": window[0].isoformat() if window else None,
                "overlap_end": window[1].isoformat() if window else None,
                "buffer_minutes": buffer_minutes,
                "students_assigned_to_both": sorted(shared),
            })
    return pairs

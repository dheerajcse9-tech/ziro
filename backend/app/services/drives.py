"""Employers, drives, versioned criteria, destinations and conflict resolution."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.base import utcnow
from app.domain.normalize import normalize_branch
from app.domain.schedule import to_utc, validate_timezone
from app.models.entities import (
    Destination,
    Drive,
    DriveAssignment,
    DriveCriteria,
    Employer,
    ExceptionItem,
    User,
)
from app.models.enums import (
    ActorType,
    AssignmentStatus,
    BacklogPolicy,
    ConsentScope,
    DestinationKind,
    DriveStatus,
    ExceptionKind,
    ExceptionStatus,
)
from app.services.approvals import revalidate_open_approvals


def _actor(user: User | None) -> str:
    return f"user:{user.id}" if user else "system"


def get_drive(db: Session, drive_id: int) -> Drive:
    drive = db.get(Drive, drive_id)
    if drive is None:
        raise NotFound(f"Drive {drive_id} not found")
    return drive


def create_employer(db: Session, data: dict[str, Any], user: User | None) -> Employer:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationFailed("Employer name is required")
    if db.scalar(select(Employer).where(Employer.name == name)):
        raise Conflict(f"Employer '{name}' already exists")
    emp = Employer(name=name, industry=data.get("industry"), website=data.get("website"),
                   contact_email=data.get("contact_email"))
    db.add(emp)
    db.flush()
    ledger.record(db, actor_type=ActorType.USER if user else ActorType.SYSTEM, actor_id=_actor(user),
                  action="EMPLOYER_CREATED", entity_type="employer", entity_id=emp.id, details={"name": name})
    return emp


def _parse_interval(data: dict[str, Any]) -> tuple[datetime, datetime, str]:
    tz = data.get("timezone") or get_settings().default_timezone
    try:
        validate_timezone(tz)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    starts, ends = data.get("starts_at"), data.get("ends_at")
    if starts is None or ends is None:
        raise ValidationFailed("starts_at and ends_at are required")
    if isinstance(starts, str):
        starts = datetime.fromisoformat(starts)
    if isinstance(ends, str):
        ends = datetime.fromisoformat(ends)
    s_utc, e_utc = to_utc(starts, tz), to_utc(ends, tz)
    if e_utc <= s_utc:
        raise ValidationFailed("Drive must end after it starts")
    return s_utc, e_utc, tz


def _validate_criteria(data: dict[str, Any]) -> dict[str, Any]:
    min_cgpa = data.get("min_cgpa")
    if min_cgpa is not None:
        min_cgpa = float(min_cgpa)
        if not 0 <= min_cgpa <= 10:
            raise ValidationFailed("min_cgpa must be between 0 and 10")
    policy = data.get("backlog_policy") or BacklogPolicy.NO_ACTIVE_BACKLOGS
    if policy not in BacklogPolicy.__members__.values():
        raise ValidationFailed(f"Unknown backlog policy {policy}")
    max_backlogs = data.get("max_active_backlogs")
    if policy == BacklogPolicy.MAX_ALLOWED:
        if max_backlogs is None or int(max_backlogs) < 0:
            raise ValidationFailed("max_active_backlogs is required (>= 0) for MAX_ALLOWED policy")
        max_backlogs = int(max_backlogs)
    elif policy == BacklogPolicy.NO_ACTIVE_BACKLOGS:
        max_backlogs = 0
    else:
        max_backlogs = None
    branches = []
    for raw in data.get("allowed_branches") or []:
        code = normalize_branch(raw)
        if code is None:
            raise ValidationFailed(f"Unrecognised branch '{raw}'")
        if code not in branches:
            branches.append(code)
    years = sorted({int(y) for y in data.get("graduation_years") or []})
    scope = data.get("required_consent_scope") or ConsentScope.SHARE_WITH_EMPLOYERS
    if scope not in ConsentScope.__members__.values():
        raise ValidationFailed(f"Unknown consent scope {scope}")
    return {
        "min_cgpa": min_cgpa, "backlog_policy": policy, "max_active_backlogs": max_backlogs,
        "allowed_branches": sorted(branches), "graduation_years": years, "required_consent_scope": scope,
        "source_text": data.get("source_text"), "source_reference": data.get("source_reference"),
    }


def create_drive(db: Session, data: dict[str, Any], user: User | None) -> Drive:
    employer = db.get(Employer, data.get("employer_id"))
    if employer is None:
        raise ValidationFailed("employer_id does not exist")
    starts, ends, tz = _parse_interval(data)
    criteria = _validate_criteria(data.get("criteria") or {})
    drive = Drive(
        employer_id=employer.id, title=data["title"].strip(), role_title=data.get("role_title"),
        location=data.get("location"), starts_at=starts, ends_at=ends, timezone=tz,
        status=data.get("status") or DriveStatus.OPEN, source_reference=data.get("source_reference"),
        notes=data.get("notes"), destination_id=data.get("destination_id"),
    )
    db.add(drive)
    db.flush()
    db.add(DriveCriteria(drive_id=drive.id, version=1, created_by_id=user.id if user else None,
                         change_note="Initial criteria", **criteria))
    db.flush()
    ledger.record(db, actor_type=ActorType.USER if user else ActorType.SYSTEM, actor_id=_actor(user),
                  action="DRIVE_CREATED", entity_type="drive", entity_id=drive.id, drive_id=drive.id,
                  details={"title": drive.title, "starts_at": starts.isoformat(), "ends_at": ends.isoformat(),
                           "timezone": tz, "criteria_v1": criteria})
    return drive


def update_drive(db: Session, drive: Drive, data: dict[str, Any], user: User) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key in ("title", "role_title", "location", "notes", "source_reference"):
        if key in data and data[key] != getattr(drive, key):
            changes[key] = {"from": getattr(drive, key), "to": data[key]}
            setattr(drive, key, data[key])
    if "status" in data and data["status"] != drive.status:
        if data["status"] not in DriveStatus.__members__.values():
            raise ValidationFailed("Invalid drive status")
        changes["status"] = {"from": drive.status, "to": data["status"]}
        drive.status = data["status"]
    schedule_changed = False
    if any(k in data for k in ("starts_at", "ends_at", "timezone")):
        merged = {
            "starts_at": data.get("starts_at") or drive.starts_at.astimezone(validate_timezone(drive.timezone)).replace(tzinfo=None),
            "ends_at": data.get("ends_at") or drive.ends_at.astimezone(validate_timezone(drive.timezone)).replace(tzinfo=None),
            "timezone": data.get("timezone") or drive.timezone,
        }
        starts, ends, tz = _parse_interval(merged)
        if (starts, ends, tz) != (drive.starts_at, drive.ends_at, drive.timezone):
            changes["schedule"] = {"from": [drive.starts_at.isoformat(), drive.ends_at.isoformat(), drive.timezone],
                                   "to": [starts.isoformat(), ends.isoformat(), tz]}
            drive.starts_at, drive.ends_at, drive.timezone = starts, ends, tz
            drive.schedule_version += 1
            schedule_changed = True
    if "destination_id" in data and data["destination_id"] != drive.destination_id:
        if data["destination_id"] is not None and db.get(Destination, data["destination_id"]) is None:
            raise ValidationFailed("destination_id does not exist")
        changes["destination_id"] = {"from": drive.destination_id, "to": data["destination_id"]}
        drive.destination_id = data["destination_id"]
    db.flush()
    db.refresh(drive)
    invalidated: list[int] = []
    if changes:
        ledger.record(db, actor_type=ActorType.USER, actor_id=_actor(user), action="DRIVE_UPDATED",
                      entity_type="drive", entity_id=drive.id, drive_id=drive.id, details=changes)
        # A schedule change can create or remove clashes on any overlapping drive.
        scope = None if schedule_changed else {drive.id}
        invalidated = revalidate_open_approvals(db, f"Drive #{drive.id} changed: {', '.join(changes)}", scope)
    return {"changes": changes, "invalidated_proposals": invalidated}


def add_criteria_version(db: Session, drive: Drive, data: dict[str, Any], user: User) -> DriveCriteria:
    criteria = _validate_criteria(data)
    latest = db.scalar(select(DriveCriteria).where(DriveCriteria.drive_id == drive.id)
                       .order_by(DriveCriteria.version.desc()).limit(1))
    version = (latest.version + 1) if latest else 1
    row = DriveCriteria(drive_id=drive.id, version=version, created_by_id=user.id,
                        change_note=data.get("change_note"), **criteria)
    db.add(row)
    db.flush()
    ledger.record(db, actor_type=ActorType.USER, actor_id=_actor(user), action="CRITERIA_VERSION_CREATED",
                  entity_type="drive", entity_id=drive.id, drive_id=drive.id,
                  details={"version": version, "criteria": criteria, "change_note": data.get("change_note")})
    revalidate_open_approvals(db, f"Eligibility criteria changed to v{version}", {drive.id})
    # Criteria mismatches recorded against the previous version are now moot.
    for exc in db.scalars(select(ExceptionItem).where(
        ExceptionItem.drive_id == drive.id, ExceptionItem.kind == ExceptionKind.CRITERIA_MISMATCH,
        ExceptionItem.status == ExceptionStatus.OPEN,
    )):
        exc.status, exc.resolution = ExceptionStatus.RESOLVED, "CRITERIA_UPDATED"
        exc.resolution_note, exc.resolved_by_id, exc.resolved_at = f"Superseded by criteria v{version}", user.id, utcnow()
    return row


def upsert_destination(db: Session, data: dict[str, Any], user: User, destination: Destination | None = None) -> Destination:
    settings = get_settings()
    base_url = (data.get("base_url") or (destination.base_url if destination else "")).rstrip("/")
    if not settings.destination_host_allowed(base_url):
        raise ValidationFailed(
            "Destination host is not in ALLOWED_DESTINATION_HOSTS; refusing to configure an unapproved upload target",
            details={"base_url": base_url, "allowed_hosts": settings.allowed_destination_hosts},
        )
    kind = data.get("kind") or (destination.kind if destination else DestinationKind.LOCAL_MOCK_FORM)
    if kind not in DestinationKind.__members__.values():
        raise ValidationFailed("Unknown destination kind")
    if destination is None:
        destination = Destination(name=data["name"], kind=kind, base_url=base_url, form_ref=data["form_ref"],
                                  is_enabled=data.get("is_enabled", True))
        db.add(destination)
        action = "DESTINATION_CREATED"
    else:
        destination.name = data.get("name", destination.name)
        destination.kind = kind
        destination.base_url = base_url
        destination.form_ref = data.get("form_ref", destination.form_ref)
        if "is_enabled" in data:
            destination.is_enabled = bool(data["is_enabled"])
        destination.config_version += 1
        action = "DESTINATION_UPDATED"
    db.flush()
    ledger.record(db, actor_type=ActorType.USER, actor_id=_actor(user), action=action, entity_type="destination",
                  entity_id=destination.id, details={"base_url": base_url, "form_ref": destination.form_ref,
                                                     "kind": kind, "config_version": destination.config_version,
                                                     "enabled": destination.is_enabled})
    if action == "DESTINATION_UPDATED":
        drive_ids = set(db.scalars(select(Drive.id).where(Drive.destination_id == destination.id)))
        revalidate_open_approvals(db, f"Destination #{destination.id} configuration changed", drive_ids)
    return destination


def resolve_conflict(db: Session, exception_id: int, preferred_drive_id: int, note: str | None, user: User) -> ExceptionItem:
    exc = db.get(ExceptionItem, exception_id)
    if exc is None or exc.kind != ExceptionKind.SCHEDULE_CONFLICT:
        raise NotFound("Schedule conflict exception not found")
    if exc.status != ExceptionStatus.OPEN:
        raise Conflict("Conflict already resolved")
    detail = dict(exc.detail or {})
    drive_ids = {detail.get("drive_id"), detail.get("other_drive_id")}
    if preferred_drive_id not in drive_ids:
        raise ValidationFailed("preferred_drive_id must be one of the two clashing drives")
    other_drive_id = (drive_ids - {preferred_drive_id}).pop()
    assignments = {a.drive_id: a for a in db.scalars(select(DriveAssignment).where(
        DriveAssignment.student_id == exc.student_id, DriveAssignment.drive_id.in_(drive_ids)))}
    other = assignments.get(other_drive_id)
    if other is not None and other.status == AssignmentStatus.CONFIRMED:
        raise Conflict("The student is already confirmed on the other drive's submitted roster; "
                       "withdraw them with the employer first, then record the change.")
    if other is not None:
        other.status, other.note = AssignmentStatus.WITHDRAWN, f"Conflict #{exc.id} resolved in favour of drive {preferred_drive_id}"
    pref = assignments.get(preferred_drive_id)
    if pref is not None and pref.status == AssignmentStatus.HELD_CONFLICT:
        pref.status, pref.note = AssignmentStatus.PROPOSED, f"Conflict #{exc.id} resolved"
    detail["preferred_drive_id"] = preferred_drive_id
    exc.detail = detail
    exc.status, exc.resolution, exc.resolution_note = ExceptionStatus.RESOLVED, "PREFER_DRIVE", note
    exc.resolved_by_id, exc.resolved_at = user.id, utcnow()
    db.flush()
    ledger.record(db, actor_type=ActorType.USER, actor_id=_actor(user), action="CONFLICT_RESOLVED",
                  entity_type="exception", entity_id=exc.id, drive_id=preferred_drive_id,
                  details={"student_id": exc.student_id, "preferred_drive_id": preferred_drive_id,
                           "withdrawn_from_drive_id": other_drive_id, "note": note})
    revalidate_open_approvals(db, "Schedule conflict resolved; roster membership changed", drive_ids)
    return exc

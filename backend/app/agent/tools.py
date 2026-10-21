"""Typed agent tools.

Each tool has a strict Pydantic input schema, a permission tier and a
predictable JSON output. Tools call domain services; they never get raw
database or browser access, and they cannot approve anything.

Permission tiers
  READ     : read-only queries and analysis (also available to the LLM planner)
  PROPOSE  : creates a proposal + approval request (graph only)
  EXECUTE  : external side effects; requires an APPROVED approval, which the
             services re-validate server-side (graph only)
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.events import emit
from app.audit import ledger
from app.core.errors import DomainError
from app.core.hashing import sha256_hex
from app.db.base import utcnow
from app.domain.criteria_parser import compare_with_configured, parse_criteria_text
from app.domain.eligibility import CriteriaSpec
from app.domain.schedule import Interval, to_local
from app.integrations.drive_form import DestinationUnreadable
from app.integrations.registry import adapter_for
from app.models.entities import AgentRun, Drive, SourceEvidence, Student, User
from app.models.enums import ActorType, DriveStatus
from app.services import approvals, attendance, uploads, verification
from app.services.consent import current_consent
from app.services.roster import criteria_spec, current_criteria, drive_pair_conflicts, evaluate_drive

Permission = Literal["READ", "PROPOSE", "NOTIFY", "EXECUTE"]


class ToolFailure(Exception):
    def __init__(self, tool: str, message: str, retryable: bool = False):
        super().__init__(f"{tool}: {message}")
        self.tool, self.message, self.retryable = tool, message, retryable


@dataclass
class ToolContext:
    db: Session
    run: AgentRun
    user: User | None

    @property
    def actor(self) -> str:
        return f"agent:run:{self.run.id}" + (f" for user:{self.user.id}" if self.user else "")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoInput(_Strict):
    pass


class DriveInput(_Strict):
    drive_id: int = Field(description="Internal drive id")


class SimulateInput(_Strict):
    drive_id: int
    min_cgpa: float | None = Field(default=None, ge=0, le=10, description="Proposed CGPA minimum (inclusive)")
    backlog_policy: Literal["NO_ACTIVE_BACKLOGS", "MAX_ALLOWED", "NOT_CONSIDERED"] | None = None
    max_active_backlogs: int | None = Field(default=None, ge=0)
    allowed_branches: list[str] | None = Field(default=None, description="Branch codes, e.g. CSE, IT, ECE")
    shift_minutes: int | None = Field(default=None, description="Move the drive start/end by this many minutes")


class ApprovalInput(_Strict):
    approval_id: int


class UploadInput(_Strict):
    upload_id: int


class AuditNoteInput(_Strict):
    note: str = Field(max_length=500)


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    permission: Permission
    fn: Callable[[ToolContext, Any], dict[str, Any]]

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        schema["additionalProperties"] = False
        return schema


# --------------------------------------------------------------------------- implementations

def _drive(ctx: ToolContext, drive_id: int) -> Drive:
    drive = ctx.db.get(Drive, drive_id)
    if drive is None:
        raise ToolFailure("drive", f"Drive {drive_id} does not exist")
    return drive


def list_drives(ctx: ToolContext, _: NoInput) -> dict[str, Any]:
    drives = ctx.db.scalars(select(Drive).where(Drive.status.notin_([DriveStatus.CANCELLED, DriveStatus.CLOSED]))
                            .order_by(Drive.starts_at))
    now = utcnow()
    return {"drives": [{
        "drive_id": d.id, "title": d.title, "employer": d.employer.name, "status": d.status,
        "starts_at_local": to_local(d.starts_at, d.timezone).isoformat(), "timezone": d.timezone,
        "is_upcoming": d.starts_at >= now, "has_destination": d.destination_id is not None,
    } for d in drives]}


def get_student_roster(ctx: ToolContext, _: NoInput) -> dict[str, Any]:
    students = list(ctx.db.scalars(select(Student)))
    consent = current_consent(ctx.db, [s.id for s in students], "SHARE_WITH_EMPLOYERS")
    return {
        "total_students": len(students),
        "consent_granted": sum(1 for s in students if consent.get(s.id, {}).get("granted")),
        "consent_missing_or_revoked": sum(1 for s in students if not consent.get(s.id, {}).get("granted")),
        "missing_cgpa": sum(s.cgpa is None for s in students),
        "missing_backlogs": sum(s.active_backlogs is None for s in students),
        "missing_or_unrecognised_branch": sum(s.branch is None for s in students),
        "branches": sorted({s.branch for s in students if s.branch}),
        "roster_snapshot_hash": sha256_hex(sorted([s.roll_number, s.record_version] for s in students)),
    }


def validate_roster(ctx: ToolContext, _: NoInput) -> dict[str, Any]:
    students = list(ctx.db.scalars(select(Student).order_by(Student.roll_number)))
    rolls = [s.roll_number for s in students]
    names: dict[str, list[str]] = {}
    for s in students:
        names.setdefault(s.full_name.strip().lower(), []).append(s.roll_number)
    issues = [{"roll_number": s.roll_number, "issues": [i["code"] + ":" + i["field"] for i in s.data_issues or []]}
              for s in students if s.data_issues]
    return {
        "total": len(students),
        "duplicate_roll_numbers": sorted({r for r in rolls if rolls.count(r) > 1}),
        "shared_names_resolved_by_roll_number": {n: r for n, r in names.items() if len(r) > 1},
        "records_with_data_issues": len(issues),
        "data_issue_samples": issues[:15],
        "identity_rule": "Students are matched exclusively by roll number / internal id, never by name.",
    }


def get_drive_requirements(ctx: ToolContext, args: DriveInput) -> dict[str, Any]:
    drive = _drive(ctx, args.drive_id)
    row = current_criteria(ctx.db, drive.id)
    configured = None if row is None else {**criteria_spec(row).as_dict(), "source_text": row.source_text,
                                           "source_reference": row.source_reference}
    result: dict[str, Any] = {
        "drive": {"drive_id": drive.id, "title": drive.title, "employer": drive.employer.name, "status": drive.status,
                  "starts_at": drive.starts_at.isoformat(), "ends_at": drive.ends_at.isoformat(), "timezone": drive.timezone,
                  "schedule_version": drive.schedule_version},
        "configured_criteria": configured,
        "destination": None,
        "retrieved_source": None,
    }
    if drive.destination is None:
        return result
    dest = drive.destination
    result["destination"] = {"id": dest.id, "name": dest.name, "kind": dest.kind, "form_ref": dest.form_ref,
                             "base_url": dest.base_url, "enabled": dest.is_enabled}
    try:
        form = adapter_for(dest).describe_form(dest.form_ref)
    except DestinationUnreadable as exc:
        raise ToolFailure("get_drive_requirements", f"Employer form could not be retrieved: {exc}", retryable=True) from exc
    except DomainError as exc:
        raise ToolFailure("get_drive_requirements", exc.message) from exc
    parsed = parse_criteria_text(form.published_criteria)
    diffs = compare_with_configured(parsed, configured or {})
    evidence = SourceEvidence(
        kind="EMPLOYER_FORM_DEFINITION", drive_id=drive.id, run_id=ctx.run.id, source_url=form.url,
        content={"form": form.__dict__, "parsed": parsed.as_dict(), "discrepancies": diffs},
        sha256=form.raw_html_sha256,
    )
    ctx.db.add(evidence)
    ctx.db.flush()
    result["retrieved_source"] = {
        "evidence_id": evidence.id, "url": form.url, "sha256": form.raw_html_sha256, "drive_title": form.drive_title,
        "employer": form.employer_name, "schedule_text": form.schedule_text, "form_open": form.is_open,
        "required_fields": form.required_fields,
        # Untrusted text: returned as data for display/cross-checking only.
        "published_criteria_text_untrusted": form.published_criteria,
        "parsed_published_criteria": parsed.as_dict(),
        "discrepancies_vs_configured": diffs,
    }
    return result


def evaluate_eligibility(ctx: ToolContext, args: DriveInput) -> dict[str, Any]:
    drive = _drive(ctx, args.drive_id)
    try:
        ev = evaluate_drive(ctx.db, drive, persist=True, run_id=ctx.run.id)
    except DomainError as exc:
        raise ToolFailure("evaluate_eligibility", exc.message) from exc
    return {
        "criteria_version": ev.criteria.version,
        "criteria": ev.criteria.as_dict(),
        "counts": ev.counts(),
        "exclusion_reasons": ev.exclusion_reason_summary(),
        "included_roll_numbers": [o.roll_number for o in ev.included],
        "already_submitted": ev.previously_submitted,
        "new_to_upload": [o.roll_number for o in ev.to_upload],
        "no_longer_eligible": [{"roll_number": o.roll_number, "reasons": o.reasons} for o in ev.no_longer_eligible],
        "excluded": [{"roll_number": o.roll_number, "decision": o.decision, "reasons": o.reasons} for o in ev.excluded],
        "held": [{"roll_number": o.roll_number, "reasons": o.reasons} for o in ev.held],
        "unknown": [{"roll_number": o.roll_number, "missing": o.missing_evidence} for o in ev.outcomes if o.decision == "UNKNOWN"],
    }


def detect_drive_conflicts(ctx: ToolContext, args: DriveInput) -> dict[str, Any]:
    drive = _drive(ctx, args.drive_id)
    ev = evaluate_drive(ctx.db, drive)
    affected = [{"roll_number": o.roll_number, "roster_status": o.roster_status, "conflicts": o.conflicts}
                for o in ev.outcomes if o.conflicts and o.decision == "ELIGIBLE" and o.consent_ok]
    pairs = [p for p in drive_pair_conflicts(ctx.db) if drive.id in (p["drive_a"]["id"], p["drive_b"]["id"])]
    return {
        "interval_rule": "half-open [start, end) in UTC; touching intervals do not clash",
        "overlapping_drives": pairs,
        "eligible_students_with_clashes": affected,
        "held_for_resolution": [a["roll_number"] for a in affected if a["roster_status"] == "HELD"],
    }


def simulate_roster_changes(ctx: ToolContext, args: SimulateInput) -> dict[str, Any]:
    drive = _drive(ctx, args.drive_id)
    row = current_criteria(ctx.db, drive.id)
    if row is None:
        raise ToolFailure("simulate_roster_changes", "Drive has no criteria")
    base = criteria_spec(row)
    from app.domain.normalize import normalize_branch

    branches = base.allowed_branches
    if args.allowed_branches is not None:
        branches = tuple(sorted({b for b in (normalize_branch(x) for x in args.allowed_branches) if b}))
    policy = args.backlog_policy or base.backlog_policy
    max_b = args.max_active_backlogs if args.max_active_backlogs is not None else base.max_active_backlogs
    if policy == "NO_ACTIVE_BACKLOGS":
        max_b = 0
    proposed = CriteriaSpec(
        version=base.version, min_cgpa=args.min_cgpa if args.min_cgpa is not None else base.min_cgpa,
        backlog_policy=policy, max_active_backlogs=max_b, allowed_branches=branches,
        graduation_years=base.graduation_years, required_consent_scope=base.required_consent_scope,
    )
    interval = None
    if args.shift_minutes:
        from datetime import timedelta

        delta = timedelta(minutes=args.shift_minutes)
        interval = Interval(drive.id, drive.starts_at + delta, drive.ends_at + delta)
    return simulate(ctx.db, drive, proposed, interval)


def simulate(db: Session, drive: Drive, proposed: CriteriaSpec, interval: Interval | None) -> dict[str, Any]:
    baseline = evaluate_drive(db, drive)
    variant = evaluate_drive(db, drive, criteria_override=proposed, interval_override=interval)
    b_status = {o.roll_number: o for o in baseline.outcomes}
    changes = []
    for o in variant.outcomes:
        before = b_status[o.roll_number]
        if before.roster_status != o.roster_status:
            changes.append({"roll_number": o.roll_number, "full_name": o.full_name, "from": before.roster_status,
                            "to": o.roster_status, "reasons_now": o.reasons, "reasons_before": before.reasons})
    return {
        "read_only": True,
        "baseline_criteria": baseline.criteria.as_dict(),
        "proposed_criteria": proposed.as_dict(),
        "schedule_override": None if interval is None else {"starts_at": interval.start.isoformat(), "ends_at": interval.end.isoformat()},
        "baseline_counts": baseline.counts(),
        "proposed_counts": variant.counts(),
        "added_to_roster": [c for c in changes if c["to"] == "INCLUDED"],
        "removed_from_roster": [c for c in changes if c["from"] == "INCLUDED"],
        "other_changes": [c for c in changes if "INCLUDED" not in (c["from"], c["to"])],
    }


def create_approval_request(ctx: ToolContext, args: DriveInput) -> dict[str, Any]:
    drive = _drive(ctx, args.drive_id)
    try:
        ev = evaluate_drive(ctx.db, drive)
        proposal, approval = approvals.create_proposal(
            ctx.db, drive, ev, run_id=ctx.run.id, requested_by=ctx.actor,
            requested_for_user_id=ctx.user.id if ctx.user else None)
    except DomainError as exc:
        raise ToolFailure("create_approval_request", exc.message) from exc
    return {"proposal_id": proposal.id, "approval_id": approval.id, "proposal_hash": proposal.proposal_hash,
            "kind": proposal.kind, "previously_submitted_count": len(proposal.previously_submitted_roll_numbers),
            "included_count": len(proposal.included_roll_numbers), "excluded_count": len(proposal.excluded),
            "held_count": len(proposal.held), "status": approval.status,
            "note": "Awaiting explicit human approval. The agent cannot approve its own proposal."}


def execute_approved_upload(ctx: ToolContext, args: ApprovalInput) -> dict[str, Any]:
    try:
        op = uploads.execute(ctx.db, args.approval_id, actor=ctx.actor, run_id=ctx.run.id)
    except DomainError as exc:
        raise ToolFailure("execute_approved_upload", exc.message) from exc
    return {"upload_id": op.id, "operation_key": op.operation_key, "status": op.status, "attempts": op.attempts,
            "submitted_count": len(op.submitted_roll_numbers), "destination_receipt": op.destination_receipt,
            "error": op.error, "attempt_log": op.attempt_log}


def verify_uploaded_roster(ctx: ToolContext, args: UploadInput) -> dict[str, Any]:
    try:
        v = verification.verify(ctx.db, args.upload_id, actor=ctx.actor, run_id=ctx.run.id)
    except DomainError as exc:
        raise ToolFailure("verify_uploaded_roster", exc.message) from exc
    return {"verification_id": v.id, "status": v.status, "expected_count": v.expected_count,
            "observed_count": v.observed_count, "missing": v.missing_roll_numbers,
            "unexpected": v.unexpected_roll_numbers, "duplicates": v.duplicate_roll_numbers,
            "detail": v.detail, "receipt_hash": v.receipt_hash}


def generate_attendance_sheet(ctx: ToolContext, args: UploadInput) -> dict[str, Any]:
    try:
        sheet = attendance.generate(ctx.db, args.upload_id, actor=ctx.actor, run_id=ctx.run.id)
    except DomainError as exc:
        raise ToolFailure("generate_attendance_sheet", exc.message) from exc
    return {"attendance_sheet_id": sheet.id, "rows": sheet.row_count, "file_name": sheet.file_name,
            "sha256": sheet.sha256, "excluded_at_generation": sheet.excluded_at_generation}


def send_drive_invitations(ctx: ToolContext, args: DriveInput) -> dict[str, Any]:
    from app.services import outreach

    drive = _drive(ctx, args.drive_id)
    try:
        result = outreach.invite_eligible(ctx.db, drive, ctx.user, actor=ctx.actor)
    except DomainError as exc:
        raise ToolFailure("send_drive_invitations", exc.message) from exc
    return {"invited": len(result["sent"]), "already_invited": len(result["already_invited"]),
            "criteria_version": result["criteria_version"], "invited_roll_numbers": result["sent"]}


def request_missing_data(ctx: ToolContext, args: DriveInput) -> dict[str, Any]:
    from app.services import outreach

    drive = _drive(ctx, args.drive_id)
    try:
        result = outreach.request_missing_for_drive(ctx.db, drive, ctx.user)
    except DomainError as exc:
        raise ToolFailure("request_missing_data", exc.message) from exc
    return {"requested": result["requested"], "count": len(result["requested"])}


def send_roster_notifications(ctx: ToolContext, args: UploadInput) -> dict[str, Any]:
    from app.services import outreach

    op = uploads.get_upload(ctx.db, args.upload_id)
    if (verification.latest_verification(ctx.db, op.id) or None) is None or \
            verification.latest_verification(ctx.db, op.id).status != "VERIFIED":
        raise ToolFailure("send_roster_notifications", "Notifications are only sent for VERIFIED uploads")
    return outreach.notify_upload(ctx.db, op, ctx.actor)


def record_audit_event(ctx: ToolContext, args: AuditNoteInput) -> dict[str, Any]:
    ev = ledger.record(ctx.db, actor_type=ActorType.AGENT, actor_id=ctx.actor, action="AGENT_NOTE",
                       run_id=ctx.run.id, drive_id=ctx.run.drive_id, details={"note": args.note})
    return {"audit_seq": ev.seq, "hash": ev.hash}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("list_drives", "List active placement drives with schedule and status.", NoInput, "READ", list_drives),
    Tool("get_student_roster", "Summarise the authorised student roster: counts, consent coverage, missing data.", NoInput, "READ", get_student_roster),
    Tool("validate_roster", "Validate roster integrity: duplicate IDs, shared names, data issues.", NoInput, "READ", validate_roster),
    Tool("get_drive_requirements", "Get configured criteria for a drive and retrieve the employer's published drive form as evidence.", DriveInput, "READ", get_drive_requirements),
    Tool("evaluate_eligibility", "Run the deterministic eligibility engine (CGPA, backlogs, branch, consent) for a drive and persist decisions.", DriveInput, "READ", evaluate_eligibility),
    Tool("detect_drive_conflicts", "Detect schedule clashes between this drive and students' other active assignments.", DriveInput, "READ", detect_drive_conflicts),
    Tool("simulate_roster_changes", "Read-only what-if: preview roster changes under different criteria or schedule.", SimulateInput, "READ", simulate_roster_changes),
    Tool("create_approval_request", "Create a hashed roster proposal and request human approval.", DriveInput, "PROPOSE", create_approval_request),
    Tool("execute_approved_upload", "Upload an APPROVED roster to the allowed destination (idempotent).", ApprovalInput, "EXECUTE", execute_approved_upload),
    Tool("verify_uploaded_roster", "Independently re-read the destination and compare exact IDs and counts.", UploadInput, "EXECUTE", verify_uploaded_roster),
    Tool("generate_attendance_sheet", "Generate an attendance sheet from a VERIFIED upload.", UploadInput, "EXECUTE", generate_attendance_sheet),
    Tool("send_drive_invitations", "Email every eligible student a personal link to accept or decline this drive.", DriveInput, "NOTIFY", send_drive_invitations),
    Tool("request_missing_data", "Email students whose eligibility is UNKNOWN a link to supply the missing fields (officer verifies before applying).", DriveInput, "NOTIFY", request_missing_data),
    Tool("send_roster_notifications", "After a VERIFIED upload, email the employer the roster and tell each sent student.", UploadInput, "NOTIFY", send_roster_notifications),
    Tool("record_audit_event", "Append a note to the audit ledger.", AuditNoteInput, "READ", record_audit_event),
]}

LLM_ALLOWED_TOOLS = {"list_drives", "get_student_roster", "get_drive_requirements", "simulate_roster_changes"}


def call_tool(ctx: ToolContext, name: str, raw_args: dict[str, Any], *, node: str, invoked_by: str,
              allowed: set[str] | None = None, retries: int = 0) -> dict[str, Any]:
    """Validate, permission-check, execute and record one tool call."""
    tool = TOOLS.get(name)
    if tool is None or (allowed is not None and name not in allowed):
        emit(ctx.db, ctx.run, "TOOL_DENIED", node=node, tool_name=name, status="DENIED",
             message=f"Tool '{name}' is not permitted for {invoked_by}", payload={"input": raw_args})
        raise ToolFailure(name, f"Tool not permitted for {invoked_by}")
    try:
        args = tool.input_model.model_validate(raw_args)
    except ValidationError as exc:
        emit(ctx.db, ctx.run, "TOOL_ERROR", node=node, tool_name=name, status="INVALID_INPUT",
             message="Input failed schema validation", payload={"input": raw_args, "errors": exc.errors(include_url=False)})
        raise ToolFailure(name, f"Invalid input: {exc.errors(include_url=False)}") from exc
    attempt = 0
    while True:
        attempt += 1
        emit(ctx.db, ctx.run, "TOOL_CALL", node=node, tool_name=name, status="STARTED",
             message=f"{invoked_by} called {name}" + (f" (attempt {attempt})" if attempt > 1 else ""),
             payload={"input": args.model_dump(), "permission": tool.permission, "invoked_by": invoked_by})
        ctx.db.commit()
        started = time.perf_counter()
        try:
            out = tool.fn(ctx, args)
        except ToolFailure as exc:
            ms = int((time.perf_counter() - started) * 1000)
            ctx.db.rollback()
            emit(ctx.db, ctx.run, "TOOL_ERROR", node=node, tool_name=name, status="FAILED", duration_ms=ms,
                 message=exc.message, payload={"retryable": exc.retryable, "attempt": attempt})
            ctx.db.commit()
            if exc.retryable and attempt <= retries:
                emit(ctx.db, ctx.run, "RETRY", node=node, tool_name=name, message=f"Retrying {name} after transient failure")
                time.sleep(min(0.25 * attempt, 1.0))
                continue
            raise
        ms = int((time.perf_counter() - started) * 1000)
        emit(ctx.db, ctx.run, "TOOL_RESULT", node=node, tool_name=name, status="OK", duration_ms=ms,
             message=summarize(name, out), payload={"output": out})
        ctx.db.commit()
        return out


def summarize(name: str, out: dict[str, Any]) -> str:
    """One-line, human-readable digest of a tool result for the timeline."""
    try:
        if name == "list_drives":
            return f"{len(out['drives'])} active drive(s) found"
        if name == "get_student_roster":
            return (f"{out['total_students']} students; {out['consent_granted']} consented; "
                    f"{out['missing_cgpa']} missing CGPA, {out['missing_backlogs']} missing backlogs")
        if name == "validate_roster":
            shared = len(out["shared_names_resolved_by_roll_number"])
            return (f"{out['total']} records, {len(out['duplicate_roll_numbers'])} duplicate IDs, "
                    f"{shared} shared name(s) disambiguated by roll number, {out['records_with_data_issues']} with data issues")
        if name == "get_drive_requirements":
            src = out.get("retrieved_source")
            crit = out.get("configured_criteria") or {}
            base = f"Configured criteria v{crit.get('version')}" if crit else "No configured criteria"
            if not src:
                return base + "; no destination form retrieved"
            return (f"{base}; employer form retrieved ({src['sha256'][:10]}…), "
                    f"{len(src['discrepancies_vs_configured'])} discrepancy(ies)")
        if name == "evaluate_eligibility":
            c = out["counts"]
            text = (f"{c['included']} included, {c['excluded']} excluded, {c['held']} held — "
                    f"{c['eligible']} eligible, {c['ineligible']} ineligible, {c['unknown']} unknown, "
                    f"{c['consent_missing']} eligible without consent")
            if c.get("already_submitted"):
                text += (f"; {c['already_submitted']} already sent earlier, {c['new_to_upload']} new to send"
                         + (f", {c['no_longer_eligible']} sent but no longer eligible" if c.get("no_longer_eligible") else ""))
            return text
        if name == "detect_drive_conflicts":
            return (f"{len(out['overlapping_drives'])} overlapping drive(s); "
                    f"{len(out['held_for_resolution'])} student(s) held for clash resolution")
        if name == "simulate_roster_changes":
            return (f"Roster {out['baseline_counts']['included']} → {out['proposed_counts']['included']} "
                    f"(+{len(out['added_to_roster'])} / -{len(out['removed_from_roster'])}); nothing saved")
        if name == "create_approval_request":
            label = "supplementary upload of" if out.get("kind") == "SUPPLEMENT" else "upload of"
            return (f"Approval #{out['approval_id']} requested for {label} {out['included_count']} student(s) "
                    f"(hash {out['proposal_hash'][:10]}…)")
        if name == "execute_approved_upload":
            receipt = out.get("destination_receipt") or {}
            return f"Upload {out['status']} after {out['attempts']} attempt(s); receipt {receipt.get('receipt_no', '—')}"
        if name == "verify_uploaded_roster":
            return f"{out['status']}: observed {out['observed_count']} / approved {out['expected_count']}"
        if name == "send_drive_invitations":
            return f"Invitations emailed to {out['invited']} eligible student(s); {out['already_invited']} already invited"
        if name == "request_missing_data":
            return f"Missing-data requests emailed to {out['count']} student(s)"
        if name == "send_roster_notifications":
            return (f"Employer email {out['employer_email'].lower().replace('_', ' ')}; "
                    f"{out['students_notified']} student(s) notified")
        if name == "generate_attendance_sheet":
            return f"Attendance sheet #{out['attendance_sheet_id']} with {out['rows']} rows"
    except (KeyError, TypeError):
        pass
    return f"{name} completed"

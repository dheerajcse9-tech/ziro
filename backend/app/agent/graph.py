"""Durable workflow graph for the placement drive coordinator agent.

The graph is a set of nodes connected by conditional edges chosen from actual
tool results. After every node the run's status, current step, plan progress
and `state` (including `next_node`) are committed, so a run survives process
restarts and can be resumed exactly where it stopped. Human decisions are
interrupts: the run stops in NEEDS_REVIEW or AWAITING_APPROVAL and resumes
only through an authenticated API call.

    PROCESS_COMPANY_EMAIL (stage 1, drive upload officer):
        read_email -> check_email ==(NEEDS_REVIEW if anything is wrong)==> create_drive
        -> consent_form -> publish -> COMPLETED
    PREPARE_ROSTER (stage 2, placement officer):
        interpret_goal -> validate_scope -> retrieve_evidence -> interpret_criteria
        -> evaluate_roster -> check_consent -> check_conflicts -> propose -> review
        ==(AWAITING_APPROVAL)==> [human approves] -> execute_upload -> verify
        -> attendance -> notify -> VERIFIED
    FOLLOW_UP (stage 3, approver): build_forms -> send_forms -> COMPLETED
    WHAT_IF:          ... validate_scope -> simulate -> COMPLETED
    CHECK_CONFLICTS:  ... evaluate_roster -> check_conflicts -> COMPLETED

Each node is executed by one named agent (see app.agent.crew).
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agent.crew import CURRENT_AGENT, agent_for_node
from app.agent.events import emit
from app.agent.planner import make_plan
from app.agent.tools import ToolContext, ToolFailure, call_tool
from app.audit import ledger
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from sqlalchemy import func, select

from app.db.base import utcnow
from app.models.entities import AgentRun, ApprovalRequest, Drive, ExceptionItem, StudentRequest, User
from app.models.enums import (
    TERMINAL_RUN_STATES,
    ActorType,
    ApprovalStatus,
    ConsentScope,
    RequestKind,
    DriveStatus,
    ExceptionKind,
    ExceptionStatus,
    Role,
    RunStatus,
    Severity,
    UploadStatus,
    VerificationStatus,
)
from app.services import exceptions
from app.services.roster import current_criteria

GRAPH = "graph"

PLAN_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "PREPARE_ROSTER": [
        ("interpret_goal", "Interpret the request and identify the drive"),
        ("validate_scope", "Validate scope, permissions and destination"),
        ("retrieve_evidence", "Retrieve configured criteria and the employer's published drive form"),
        ("interpret_criteria", "Cross-check published criteria against configured criteria"),
        ("evaluate_roster", "Validate roster and evaluate eligibility + consent for every student"),
        ("check_consent", "Keep only students who said YES on the drive's consent form"),
        ("check_conflicts", "Detect schedule clashes and hold affected students"),
        ("propose", "Create hashed roster proposal and request human approval"),
        ("review", "Independently re-check the proposal for the approver"),
        ("execute_upload", "Upload the approved roster (idempotent)"),
        ("verify", "Re-read destination and compare exact IDs and counts"),
        ("attendance", "Generate attendance sheet and receipt"),
        ("notify", "Email the employer the verified roster and notify the students sent"),
    ],
    "INVITE_STUDENTS": [
        ("interpret_goal", "Interpret the request and identify the drive"),
        ("validate_scope", "Validate scope and permissions"),
        ("consent_form", "Prepare the drive's consent form"),
        ("invite", "Email each eligible student a personal link to the consent form"),
    ],
    "PROCESS_COMPANY_EMAIL": [
        ("read_email", "Read the company email and extract the drive details (untrusted data)"),
        ("check_email", "Check every detail is present and valid: dates, deadline, criteria, sender"),
        ("create_drive", "Create the drive with versioned criteria and deadlines"),
        ("consent_form", "Prepare the drive's consent form"),
        ("publish", "Email every student a personal link to the consent form"),
    ],
    "FOLLOW_UP": [
        ("build_forms", "Work out each student's missing details or consent"),
        ("send_forms", "Email each of them a personal form with a closing time"),
    ],
    "REQUEST_MISSING_DATA": [
        ("interpret_goal", "Interpret the request and identify the drive"),
        ("validate_scope", "Validate scope and permissions"),
        ("request_data", "Email students with missing data a link to provide it"),
    ],
    "WHAT_IF": [
        ("interpret_goal", "Interpret the request and identify the drive"),
        ("validate_scope", "Validate scope and permissions"),
        ("simulate", "Run read-only what-if simulation and report differences"),
    ],
    "CHECK_CONFLICTS": [
        ("interpret_goal", "Interpret the request and identify the drive"),
        ("validate_scope", "Validate scope and permissions"),
        ("evaluate_roster", "Evaluate eligibility + consent"),
        ("check_conflicts", "Detect and report schedule clashes"),
    ],
}

NODE_STATUS = {
    "interpret_goal": RunStatus.VALIDATING_INPUTS,
    "validate_scope": RunStatus.VALIDATING_INPUTS,
    "retrieve_evidence": RunStatus.RETRIEVING_EVIDENCE,
    "interpret_criteria": RunStatus.INTERPRETING_CRITERIA,
    "evaluate_roster": RunStatus.EVALUATING_ROSTER,
    "check_conflicts": RunStatus.CHECKING_CONFLICTS,
    "simulate": RunStatus.EVALUATING_ROSTER,
    "propose": RunStatus.READY_FOR_APPROVAL,
    "execute_upload": RunStatus.EXECUTING,
    "verify": RunStatus.EXECUTING,
    "attendance": RunStatus.EXECUTING,
    "notify": RunStatus.EXECUTING,
    "invite": RunStatus.EXECUTING,
    "request_data": RunStatus.EXECUTING,
    "check_consent": RunStatus.EVALUATING_ROSTER,
    "review": RunStatus.READY_FOR_APPROVAL,
    "read_email": RunStatus.RETRIEVING_EVIDENCE,
    "check_email": RunStatus.VALIDATING_INPUTS,
    "create_drive": RunStatus.EXECUTING,
    "consent_form": RunStatus.EXECUTING,
    "publish": RunStatus.EXECUTING,
    "build_forms": RunStatus.EVALUATING_ROSTER,
    "send_forms": RunStatus.EXECUTING,
}


@dataclass
class Step:
    next_node: str | None
    status: str | None = None  # terminal/interrupt status when next_node is None
    message: str | None = None


_FIELD_NAMES = {"min_cgpa": "minimum CGPA", "backlog_policy": "backlog rule", "max_active_backlogs": "maximum backlogs",
                "allowed_branches": "branches", "graduation_years": "batch"}


def _describe_diff(d: dict[str, Any]) -> str:
    def show(v: Any) -> str:
        if isinstance(v, list):
            return ", ".join(map(str, v)) or "all"
        return "not stated" if v is None else str(v).replace("_", " ").lower()
    return f"{_FIELD_NAMES.get(d.get('field'), d.get('field'))}: company says {show(d.get('published'))}, drive has {show(d.get('configured'))}"


def _plan(intent: str) -> list[dict[str, Any]]:
    return [{"node": n, "title": t, "status": "pending"} for n, t in PLAN_TEMPLATES[intent]]


def _mark(run: AgentRun, node: str, status: str, note: str | None = None) -> None:
    plan = [dict(p) for p in (run.plan or [])]
    for p in plan:
        if p["node"] == node:
            p["status"] = status
            if note:
                p["note"] = note
    run.plan = plan


def _set_state(run: AgentRun, **values: Any) -> None:
    run.state = {**(run.state or {}), **values}


# --------------------------------------------------------------------------- nodes

def node_interpret_goal(ctx: ToolContext) -> Step:
    run = ctx.run
    plan = make_plan(ctx, run.objective, client=_llm_override.get(run.id))
    run.planner_mode = plan.planner_mode
    run.llm_model = get_settings().active_llm_model if plan.planner_mode == "LLM" else None
    run.intent = plan.intent
    run.plan = _plan(plan.intent)
    _mark(run, "interpret_goal", "done", plan.rationale)
    _set_state(run, simulation=plan.simulation)
    emit(ctx.db, run, "PLAN", node="interpret_goal", status="OK",
         message=f"Intent {plan.intent}; planner {plan.planner_mode}: {plan.rationale}",
         payload={"intent": plan.intent, "drive_id": plan.drive_id, "planner_mode": plan.planner_mode,
                  "steps": run.plan, "simulation": plan.simulation})
    if plan.clarification:
        run.pending_question = plan.clarification
        return Step(None, RunStatus.NEEDS_REVIEW, plan.clarification["question"])
    run.drive_id = plan.drive_id
    return Step("validate_scope")


def node_validate_scope(ctx: ToolContext) -> Step:
    run = ctx.run
    drive = ctx.db.get(Drive, run.drive_id) if run.drive_id else None
    if drive is None:
        raise ToolFailure("validate_scope", "No valid drive selected")
    if ctx.user is None or ctx.user.role not in (Role.OFFICER, Role.ADMIN):
        raise ToolFailure("validate_scope", "Only placement officers or admins can run drive workflows")
    problems = []
    if drive.status not in (DriveStatus.OPEN, DriveStatus.DRAFT, DriveStatus.ROSTER_SUBMITTED) and run.intent == "PREPARE_ROSTER":
        problems.append(f"Drive status is {drive.status}; roster preparation requires an open drive")
    if run.intent == "PREPARE_ROSTER" and drive.destination is None:
        from app.services.intake import ensure_stand_in_form

        ensure_stand_in_form(ctx.db, drive)
    if run.intent == "PREPARE_ROSTER" and drive.destination is None:
        problems.append("No upload destination configured for this drive")
    elif run.intent == "PREPARE_ROSTER" and not drive.destination.is_enabled:
        problems.append("The drive's destination is disabled")
    roster = call_tool(ctx, "get_student_roster", {}, node="validate_scope", invoked_by=GRAPH)
    emit(ctx.db, run, "CHECK", node="validate_scope", status="FAILED" if problems else "OK",
         message="; ".join(problems) or f"Scope valid: drive #{drive.id} '{drive.title}', {roster['total_students']} students on roster",
         payload={"drive_id": drive.id, "problems": problems})
    _mark(run, "validate_scope", "failed" if problems else "done")
    if problems:
        run.pending_question = {"type": "scope_problem", "question": "; ".join(problems) + ". Fix the drive configuration, then re-run.",
                                "options": [{"value": "rerun", "label": "Re-run after fixing"}, {"value": "stop", "label": "Stop"}]}
        return Step(None, RunStatus.NEEDS_REVIEW, "; ".join(problems))
    if run.intent == "WHAT_IF":
        return Step("simulate")
    if run.intent == "CHECK_CONFLICTS":
        return Step("evaluate_roster")
    if run.intent == "INVITE_STUDENTS":
        return Step("consent_form")
    if run.intent == "REQUEST_MISSING_DATA":
        return Step("request_data")
    criteria = current_criteria(ctx.db, drive.id)
    if criteria is not None and criteria.required_consent_scope == ConsentScope.DRIVE_REGISTRATION and not run.state.get("invitations_checked"):
        invited = ctx.db.scalar(select(func.count(StudentRequest.id)).where(
            StudentRequest.drive_id == drive.id, StudentRequest.kind == RequestKind.DRIVE_INVITATION)) or 0
        if not invited:
            run.pending_question = {
                "type": "invitations_needed",
                "question": "This drive only shares students who accept its invitation, and no invitations have been sent yet. "
                            "Email every eligible student a personal accept/decline link now?",
                "options": [{"value": "send_invitations", "label": "Send invitations"},
                            {"value": "continue", "label": "Continue without inviting"},
                            {"value": "stop", "label": "Stop"}],
            }
            return Step(None, RunStatus.NEEDS_REVIEW, "Invitations not yet sent for a registration-based drive")
    return Step("retrieve_evidence")


def node_retrieve_evidence(ctx: ToolContext) -> Step:
    run = ctx.run
    try:
        req = call_tool(ctx, "get_drive_requirements", {"drive_id": run.drive_id}, node="retrieve_evidence",
                        invoked_by=GRAPH, retries=2)
    except ToolFailure as exc:
        if run.state.get("proceed_without_source"):
            emit(ctx.db, run, "DECISION", node="retrieve_evidence", status="OVERRIDDEN",
                 message="Employer form unavailable; officer chose to proceed with configured criteria only")
            _mark(run, "retrieve_evidence", "skipped", "Source unavailable; officer override")
            _set_state(run, requirements=None)
            return Step("evaluate_roster")
        exceptions.raise_exception(
            ctx.db, kind=ExceptionKind.SOURCE_RETRIEVAL_FAILED, severity=Severity.BLOCKING,
            title=f"Could not retrieve employer drive form for drive #{run.drive_id}", dedupe_key=f"retrieval:{run.id}",
            detail={"error": exc.message}, drive_id=run.drive_id, run_id=run.id)
        _mark(run, "retrieve_evidence", "failed", exc.message)
        run.pending_question = {
            "type": "source_unavailable",
            "question": f"The employer's drive form could not be read ({exc.message}). Retry, or proceed using only the configured criteria?",
            "options": [{"value": "retry", "label": "Retry retrieval"},
                        {"value": "proceed_without_source", "label": "Proceed with configured criteria"},
                        {"value": "stop", "label": "Stop"}],
        }
        return Step(None, RunStatus.NEEDS_REVIEW, exc.message)
    if req["configured_criteria"] is None:
        exceptions.raise_exception(
            ctx.db, kind=ExceptionKind.CRITERIA_MISSING, severity=Severity.BLOCKING,
            title=f"Drive #{run.drive_id} has no eligibility criteria", dedupe_key=f"criteria-missing:{run.drive_id}",
            drive_id=run.drive_id, run_id=run.id)
        run.pending_question = {"type": "criteria_missing", "question": "Configure eligibility criteria for this drive, then re-run.",
                                "options": [{"value": "rerun", "label": "Re-run"}, {"value": "stop", "label": "Stop"}]}
        _mark(run, "retrieve_evidence", "failed", "No criteria configured")
        return Step(None, RunStatus.NEEDS_REVIEW, "Criteria missing")
    run.criteria_version = req["configured_criteria"]["version"]
    _set_state(run, requirements=req)
    _mark(run, "retrieve_evidence", "done")
    return Step("interpret_criteria")


def node_interpret_criteria(ctx: ToolContext) -> Step:
    run = ctx.run
    src = (run.state.get("requirements") or {}).get("retrieved_source")
    if not src:
        _mark(run, "interpret_criteria", "skipped", "No retrieved source")
        return Step("evaluate_roster")
    parsed = src["parsed_published_criteria"]
    diffs = src["discrepancies_vs_configured"]
    if parsed.get("suspicious_phrases"):
        exceptions.raise_exception(
            ctx.db, kind=ExceptionKind.SUSPICIOUS_SOURCE_CONTENT, severity=Severity.WARNING,
            title=f"Employer form for drive #{run.drive_id} contains instruction-like text (ignored)",
            dedupe_key=f"suspicious:{run.drive_id}:{src['sha256'][:16]}",
            detail={"phrases": parsed["suspicious_phrases"], "evidence_id": src["evidence_id"]},
            drive_id=run.drive_id, run_id=run.id)
        emit(ctx.db, run, "GUARDRAIL", node="interpret_criteria", status="IGNORED",
             message="Retrieved source contains instruction-like text; treated strictly as data and flagged for review",
             payload={"phrases": parsed["suspicious_phrases"]})
    acknowledged = run.state.get("criteria_mismatch_acknowledged_hash") == src["sha256"]
    blocking = diffs or (parsed.get("ambiguities") and not acknowledged)
    if blocking and not acknowledged:
        exceptions.raise_exception(
            ctx.db, kind=ExceptionKind.CRITERIA_MISMATCH if diffs else ExceptionKind.AMBIGUOUS_CRITERIA,
            severity=Severity.BLOCKING,
            title=f"Published criteria for drive #{run.drive_id} differ from configured criteria" if diffs
            else f"Published criteria for drive #{run.drive_id} are ambiguous",
            dedupe_key=f"criteria:{run.drive_id}:{src['sha256'][:16]}:v{run.criteria_version}",
            detail={"discrepancies": diffs, "ambiguities": parsed.get("ambiguities"), "evidence_id": src["evidence_id"]},
            drive_id=run.drive_id, run_id=run.id)
        _mark(run, "interpret_criteria", "blocked", "Needs officer decision")
        run.pending_question = {
            "type": "criteria_mismatch",
            "question": "The employer's published criteria do not match the configured criteria"
                        + (": " + "; ".join(_describe_diff(d) for d in diffs) if diffs
                           else ": " + "; ".join(str(x) for x in parsed.get("ambiguities") or []))
                        + ". Update the drive criteria and re-run, or confirm the configured criteria are authoritative.",
            "options": [{"value": "use_configured", "label": "Configured criteria are authoritative — continue"},
                        {"value": "rerun", "label": "I updated the criteria — re-run"},
                        {"value": "stop", "label": "Stop"}],
            "evidence_id": src["evidence_id"],
        }
        return Step(None, RunStatus.NEEDS_REVIEW, "Criteria mismatch requires officer decision")
    emit(ctx.db, run, "CHECK", node="interpret_criteria", status="OK",
         message="Published criteria consistent with configured criteria" if not diffs else "Officer confirmed configured criteria",
         payload={"parsed": parsed, "discrepancies": diffs})
    _mark(run, "interpret_criteria", "done")
    return Step("evaluate_roster")


def node_evaluate_roster(ctx: ToolContext) -> Step:
    run = ctx.run
    validation = call_tool(ctx, "validate_roster", {}, node="evaluate_roster", invoked_by=GRAPH)
    if validation["duplicate_roll_numbers"]:
        raise ToolFailure("validate_roster", f"Duplicate roll numbers: {validation['duplicate_roll_numbers']}")
    result = call_tool(ctx, "evaluate_eligibility", {"drive_id": run.drive_id}, node="evaluate_roster", invoked_by=GRAPH)
    run.criteria_version = result["criteria_version"]
    _set_state(run, eligibility={"counts": result["counts"], "exclusion_reasons": result["exclusion_reasons"]})
    _mark(run, "evaluate_roster", "done", f"{result['counts']['included']} included, {result['counts']['excluded']} excluded")
    return Step("check_conflicts" if run.intent == "CHECK_CONFLICTS" else "check_consent")


def node_check_consent(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "consent_status", {"drive_id": run.drive_id}, node="check_consent", invoked_by=GRAPH)
    _set_state(run, consent=result)
    note = (f"{result['eligible_with_consent']}/{result['eligible']} eligible said yes, "
            f"{result['eligible_declined']} no, {result['eligible_no_answer']} no answer")
    _mark(run, "check_consent", "done", note)
    form = result.get("form")
    if result["eligible_no_answer"] and form and form["status"] == "OPEN":
        emit(ctx.db, run, "DECISION", node="check_consent", status="NOTE",
             message=f"The consent form is still open until {form['closes_at_local']}; students who say yes later "
                     f"can be added with a supplementary list")
    return Step("check_conflicts")


def node_check_conflicts(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "detect_drive_conflicts", {"drive_id": run.drive_id}, node="check_conflicts", invoked_by=GRAPH)
    held = result["held_for_resolution"]
    _set_state(run, conflicts={"held": held, "overlapping_drives": len(result["overlapping_drives"])})
    _mark(run, "check_conflicts", "done", f"{len(held)} student(s) held for clash resolution")
    if held:
        emit(ctx.db, run, "DECISION", node="check_conflicts", status="HELD",
             message=f"{len(held)} eligible student(s) held out of the roster due to schedule clashes; flagged in the Exception Inbox",
             payload={"held": held})
    if run.intent == "CHECK_CONFLICTS":
        run.summary = _summary(run)
        return Step(None, RunStatus.COMPLETED, "Conflict check complete")
    return Step("propose")


def node_simulate(ctx: ToolContext) -> Step:
    run = ctx.run
    sim = dict(run.state.get("simulation") or {})
    allowed = {"min_cgpa", "backlog_policy", "max_active_backlogs", "allowed_branches", "shift_minutes"}
    args = {"drive_id": run.drive_id, **{k: v for k, v in sim.items() if k in allowed}}
    result = call_tool(ctx, "simulate_roster_changes", args, node="simulate", invoked_by=GRAPH)
    _set_state(run, simulation_result={"baseline": result["baseline_counts"], "proposed": result["proposed_counts"],
                                       "added": [a["roll_number"] for a in result["added_to_roster"]],
                                       "removed": [r["roll_number"] for r in result["removed_from_roster"]]})
    _mark(run, "simulate", "done")
    run.summary = (f"What-if (read-only, nothing changed): roster would go from {result['baseline_counts']['included']} "
                   f"to {result['proposed_counts']['included']} students "
                   f"(+{len(result['added_to_roster'])} / -{len(result['removed_from_roster'])}).")
    return Step(None, RunStatus.COMPLETED, run.summary)


def node_propose(ctx: ToolContext) -> Step:
    run = ctx.run
    counts = (run.state.get("eligibility") or {}).get("counts", {})
    if counts.get("already_submitted") and not counts.get("new_to_upload"):
        _mark(run, "propose", "skipped", "Nothing new to send")
        note = (f" {counts['no_longer_eligible']} previously sent student(s) are no longer eligible — see the Exception Inbox."
                if counts.get("no_longer_eligible") else "")
        run.summary = (f"All {counts['already_submitted']} eligible student(s) were already sent to the employer; "
                       f"no supplementary upload is needed.{note}")
        return Step(None, RunStatus.COMPLETED, run.summary)
    result = call_tool(ctx, "create_approval_request", {"drive_id": run.drive_id}, node="propose", invoked_by=GRAPH)
    run.proposal_id = result["proposal_id"]
    _set_state(run, approval_id=result["approval_id"], proposal_hash=result["proposal_hash"])
    _mark(run, "propose", "done", f"Approval #{result['approval_id']} requested")
    _set_state(run, included_count=result["included_count"], proposal_id=result["proposal_id"])
    return Step("review")


def node_review(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "review_roster_proposal", {"approval_id": run.state["approval_id"]}, node="review",
                       invoked_by=GRAPH)
    _set_state(run, review={"verdict": result["verdict"], "followup_candidates": result["followup_candidates"],
                            "warnings": [c["label"] for c in result["checks"] if c["status"] == "WARN"],
                            "fails": [c["label"] for c in result["checks"] if c["status"] == "FAIL"]})
    _mark(run, "review", "done", f"Verdict {result['verdict']}")
    run.pending_question = {"type": "approval", "approval_id": run.state["approval_id"],
                            "question": f"Review and approve roster proposal #{run.state['proposal_id']} "
                                        f"({run.state['included_count']} students) in the Approval Center. "
                                        f"Approval Review Agent: {result['verdict'].replace('_', ' ').lower()}."}
    run.summary = _summary(run)
    return Step(None, RunStatus.AWAITING_APPROVAL, "Waiting for human approval")


def node_execute_upload(ctx: ToolContext) -> Step:
    run = ctx.run
    try:
        result = call_tool(ctx, "execute_approved_upload", {"approval_id": run.state["approval_id"]},
                           node="execute_upload", invoked_by=GRAPH)
    except ToolFailure as exc:
        _mark(run, "execute_upload", "failed", exc.message)
        run.error = exc.message
        return Step(None, RunStatus.FAILED, exc.message)
    _set_state(run, upload_id=result["upload_id"])
    if result["status"] == UploadStatus.UNCERTAIN:
        _mark(run, "execute_upload", "uncertain", result["error"])
        run.pending_question = {"type": "reconcile", "upload_id": result["upload_id"],
                                "question": "Upload outcome is uncertain and could not be reconciled yet. Retry reconciliation from the Verification Center."}
        return Step(None, RunStatus.PENDING_RECONCILIATION, "Upload outcome uncertain")
    if result["status"] != UploadStatus.SUBMITTED:
        _mark(run, "execute_upload", "failed", result["error"])
        run.error = result["error"]
        return Step(None, RunStatus.FAILED, f"Upload {result['status']}: {result['error']}")
    _mark(run, "execute_upload", "done", f"Receipt {result['destination_receipt'].get('receipt_no')}")
    return Step("verify")


def node_verify(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "verify_uploaded_roster", {"upload_id": run.state["upload_id"]}, node="verify", invoked_by=GRAPH)
    _set_state(run, verification={k: result[k] for k in ("verification_id", "status", "expected_count", "observed_count",
                                                          "missing", "unexpected", "duplicates")})
    if result["status"] == VerificationStatus.VERIFIED:
        _mark(run, "verify", "done", f"{result['observed_count']}/{result['expected_count']} IDs match exactly")
        return Step("attendance")
    if result["status"] == VerificationStatus.PENDING_VERIFICATION:
        _mark(run, "verify", "pending", result["detail"])
        run.pending_question = {"type": "verification_pending", "upload_id": run.state["upload_id"],
                                "question": "Destination could not be read back. Retry verification from the Verification Center."}
        return Step(None, RunStatus.PENDING_RECONCILIATION, result["detail"])
    _mark(run, "verify", "failed", result["detail"])
    run.error = result["detail"]
    return Step(None, RunStatus.FAILED_VERIFICATION, result["detail"])


def node_attendance(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "generate_attendance_sheet", {"upload_id": run.state["upload_id"]}, node="attendance", invoked_by=GRAPH)
    _set_state(run, attendance_sheet_id=result["attendance_sheet_id"])
    _mark(run, "attendance", "done", f"{result['rows']} rows")
    return Step("notify")


def node_notify(ctx: ToolContext) -> Step:
    run = ctx.run
    try:
        result = call_tool(ctx, "send_roster_notifications", {"upload_id": run.state["upload_id"]}, node="notify",
                           invoked_by=GRAPH)
        _set_state(run, notifications=result)
        _mark(run, "notify", "done", f"employer {result['employer_email'].lower()}, {result['students_notified']} students")
    except ToolFailure as exc:  # an email problem never undoes a verified upload
        _mark(run, "notify", "failed", exc.message)
        emit(ctx.db, run, "DECISION", node="notify", status="WARNING", message=f"Notifications not sent: {exc.message}")
    run.pending_question = None
    run.summary = _summary(run)
    return Step(None, RunStatus.VERIFIED, "Roster uploaded and independently verified")


def node_consent_form(ctx: ToolContext) -> Step:
    run = ctx.run
    audience = "ALL_STUDENTS" if run.intent == "PROCESS_COMPANY_EMAIL" else None
    result = call_tool(ctx, "create_consent_form", {"drive_id": run.drive_id, "audience": audience},
                       node="consent_form", invoked_by=GRAPH)
    _set_state(run, consent_form_id=result["consent_form_id"])
    _mark(run, "consent_form", "done", f"Form #{result['consent_form_id']} closes {result['closes_at_local']}")
    return Step("publish" if run.intent == "PROCESS_COMPANY_EMAIL" else "invite")


def node_invite(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "send_drive_invitations", {"drive_id": run.drive_id}, node="invite", invoked_by=GRAPH)
    _mark(run, "invite", "done", f"{result['invited']} invited")
    run.pending_question = None
    run.summary = (f"Invitations emailed to {result['invited']} eligible student(s) "
                   f"({result['already_invited']} had already been invited). Students who accept are included when you "
                   f"prepare the roster; re-run 'prepare roster' after they respond.")
    return Step(None, RunStatus.COMPLETED, run.summary)


def _email_row(ctx: ToolContext):
    from app.services import intake

    return intake.get(ctx.db, int(ctx.run.state["email_id"]))


def node_read_email(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "read_company_email", {"email_id": run.state["email_id"]}, node="read_email", invoked_by=GRAPH)
    mode = result["mode"]
    run.planner_mode = {"AI": "LLM", "RULES_AI_FAILED": "LLM_FAILED_DETERMINISTIC_FALLBACK"}.get(mode, "DETERMINISTIC_FALLBACK")
    run.llm_model = get_settings().active_llm_model if mode == "AI" else None
    if result.get("ai_error"):
        emit(ctx.db, run, "LLM_ERROR", node="read_email", status="FALLBACK",
             message=f"AI reader failed ({result['ai_error']}); used rule-based reading")
    f = result["fields"]
    _mark(run, "read_email", "done", f"{f.get('company') or '?'} — {f.get('role') or '?'} ({'AI' if mode == 'AI' else 'rules'})")
    return Step("check_email")


def node_check_email(ctx: ToolContext) -> Step:
    from app.services import intake

    run = ctx.run
    result = call_tool(ctx, "check_company_email", {"email_id": run.state["email_id"]}, node="check_email", invoked_by=GRAPH)
    row = _email_row(ctx)
    stopping = intake.blocking(result["issues"], bool(run.state.get("review_acknowledged")))
    if not stopping:
        _mark(run, "check_email", "done", "All details present and valid")
        return Step("create_drive")
    errors = [i for i in stopping if i["level"] == "ERROR"]
    row.status = "NEEDS_REVIEW"
    _mark(run, "check_email", "blocked", f"{len(errors)} error(s), {len(stopping) - len(errors)} to confirm")
    options = [] if errors else [{"value": "confirm", "label": "Details are correct — create the drive and send consent forms"}]
    options += [{"value": "recheck", "label": "I corrected the details in the Company Inbox — check again"},
                {"value": "ignore", "label": "Ignore this email"}]
    question = ("This company email needs the drive upload officer: "
                + "; ".join(i["message"].rstrip(".") for i in stopping)
                + (". Correct the details in the Company Inbox, then choose 'check again'." if errors else "."))
    run.pending_question = {"type": "intake_review", "email_id": row.id, "question": question,
                            "issues": stopping, "options": options}
    exceptions.raise_exception(
        ctx.db, kind=ExceptionKind.INTAKE_REVIEW, severity=Severity.WARNING if not errors else Severity.BLOCKING,
        title=f"Company email '{row.subject[:120]}' needs review", dedupe_key=f"intake:{row.id}",
        detail={"email_id": row.id, "run_id": run.id, "issues": stopping}, run_id=run.id)
    return Step(None, RunStatus.NEEDS_REVIEW, question)


def node_create_drive(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "create_drive_from_email", {"email_id": run.state["email_id"]}, node="create_drive",
                       invoked_by=GRAPH)
    run.drive_id = result["drive_id"]
    exceptions.auto_resolve(ctx.db, f"intake:{run.state['email_id']}", f"Drive #{result['drive_id']} created")
    _mark(run, "create_drive", "done", f"Drive #{result['drive_id']}; registration closes {result['registration_deadline_local']}")
    return Step("consent_form")


def node_publish(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "send_drive_invitations", {"drive_id": run.drive_id}, node="publish", invoked_by=GRAPH)
    row = _email_row(ctx)
    row.status = "PUBLISHED"
    drive = ctx.db.get(Drive, run.drive_id)
    _mark(run, "publish", "done", f"{result['invited']} consent form link(s) sent")
    run.pending_question = None
    run.summary = (f"Drive #{drive.id} '{drive.title}' created from {row.from_address}'s email. Consent form sent to "
                   f"{result['invited']} student(s)" + (f" ({result['already_invited']} already had it)" if result['already_invited'] else "")
                   + ". Students who say YES are considered when the placement officer runs the drive.")
    return Step(None, RunStatus.COMPLETED, run.summary)


def node_build_forms(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "plan_followup_forms", {"drive_id": run.drive_id}, node="build_forms", invoked_by=GRAPH)
    _set_state(run, followup={"forms": result["forms"], "already_open": result["already_open"]})
    _mark(run, "build_forms", "done", f"{len(result['forms'])} form(s) to send")
    if not result["forms"]:
        run.summary = ("Nobody needs a personal form" + (f" ({result['already_open']} already have an open form)."
                                                          if result["already_open"] else "."))
        return Step(None, RunStatus.COMPLETED, run.summary)
    return Step("send_forms")


def node_send_forms(ctx: ToolContext) -> Step:
    run = ctx.run
    ids = [f["student_id"] for f in run.state["followup"]["forms"]]
    result = call_tool(ctx, "send_followup_forms", {"drive_id": run.drive_id, "closes_at": run.state["closes_at"],
                                                    "student_ids": ids}, node="send_forms", invoked_by=GRAPH)
    _mark(run, "send_forms", "done", f"{result['count']} sent")
    run.summary = (f"Personal forms emailed to {result['count']} student(s); they close {result['closes_at_local']}. "
                   f"Answers are applied by the Response Reader Agent and re-checked against the drive.")
    return Step(None, RunStatus.COMPLETED, run.summary)


def node_request_data(ctx: ToolContext) -> Step:
    run = ctx.run
    result = call_tool(ctx, "request_missing_data", {"drive_id": run.drive_id}, node="request_data", invoked_by=GRAPH)
    _mark(run, "request_data", "done", f"{result['count']} requested")
    run.summary = (f"Missing-data requests emailed to {result['count']} student(s). Their answers appear in the "
                   f"Exception Inbox for you to verify and apply; then re-run 'prepare roster'.")
    return Step(None, RunStatus.COMPLETED, run.summary)


NODES: dict[str, Callable[[ToolContext], Step]] = {
    "interpret_goal": node_interpret_goal,
    "validate_scope": node_validate_scope,
    "retrieve_evidence": node_retrieve_evidence,
    "interpret_criteria": node_interpret_criteria,
    "evaluate_roster": node_evaluate_roster,
    "check_conflicts": node_check_conflicts,
    "simulate": node_simulate,
    "propose": node_propose,
    "execute_upload": node_execute_upload,
    "verify": node_verify,
    "attendance": node_attendance,
    "notify": node_notify,
    "invite": node_invite,
    "request_data": node_request_data,
    "check_consent": node_check_consent,
    "review": node_review,
    "consent_form": node_consent_form,
    "read_email": node_read_email,
    "check_email": node_check_email,
    "create_drive": node_create_drive,
    "publish": node_publish,
    "build_forms": node_build_forms,
    "send_forms": node_send_forms,
}


def _summary(run: AgentRun) -> str:
    s = run.state or {}
    parts = []
    elig = s.get("eligibility")
    if elig:
        c = elig["counts"]
        parts.append(f"{c['included']} included, {c['excluded']} excluded, {c['held']} held "
                     f"({c['eligible']} eligible, {c['ineligible']} ineligible, {c['unknown']} unknown, "
                     f"{c['consent_missing']} eligible without consent)")
    if s.get("approval_id"):
        parts.append(f"approval #{s['approval_id']}")
    v = s.get("verification")
    if v:
        parts.append(f"verification {v['status']} ({v['observed_count']}/{v['expected_count']})")
    return "; ".join(parts)


# --------------------------------------------------------------------------- runtime

def _ctx(db: Session, run: AgentRun) -> ToolContext:
    return ToolContext(db=db, run=run, user=db.get(User, run.created_by_id) if run.created_by_id else None,
                       llm_client=_llm_override.get(run.id))


def advance(db: Session, run: AgentRun, max_steps: int = 25) -> AgentRun:
    """Run nodes until an interrupt, a terminal state or the step budget."""
    ctx = _ctx(db, run)
    steps = 0
    while (node := (run.state or {}).get("next_node")) and steps < max_steps:
        steps += 1
        fn = NODES[node]
        run.current_step = node
        run.status = NODE_STATUS.get(node, run.status)
        _mark(run, node, "running")
        agent = agent_for_node(node)
        token = CURRENT_AGENT.set(agent)
        try:
            emit(db, run, "NODE_START", node=node, status=run.status,
                 message=f"{agent.name}: {node}" if agent else f"Entering {node}")
            db.commit()
            try:
                step = fn(ctx)
            except ToolFailure as exc:
                db.rollback()
                run = db.get(AgentRun, run.id)
                ctx.run = run
                _mark(run, node, "failed", exc.message)
                run.status, run.error = RunStatus.FAILED, exc.message
                _set_state(run, next_node=None, failed_node=node)
                run.pending_question = {"type": "failure", "question": f"Step '{node}' failed: {exc.message}",
                                        "options": [{"value": "retry", "label": "Retry this step"}, {"value": "stop", "label": "Stop"}]}
                emit(db, run, "ERROR", node=node, status="FAILED", message=exc.message)
                db.commit()
                return run
            except Exception as exc:  # noqa: BLE001 - unexpected errors stop the run safely
                db.rollback()
                run = db.get(AgentRun, run.id)
                ctx.run = run
                run.status, run.error = RunStatus.FAILED, f"{type(exc).__name__}: {exc}"
                _set_state(run, next_node=None, failed_node=node)
                run.pending_question = {"type": "failure", "question": f"Unexpected error in '{node}'",
                                        "options": [{"value": "retry", "label": "Retry this step"}, {"value": "stop", "label": "Stop"}]}
                emit(db, run, "ERROR", node=node, status="FAILED", message=run.error,
                     payload={"trace": traceback.format_exc()[-3000:]})
                db.commit()
                return run
            _set_state(run, next_node=step.next_node)
            if step.next_node is None:
                run.status = step.status
                emit(db, run, "INTERRUPT" if step.status not in TERMINAL_RUN_STATES else "COMPLETE", node=node,
                     status=step.status, message=step.message)
                ledger.record(db, actor_type=ActorType.AGENT, actor_id=f"agent:run:{run.id}", action=f"AGENT_RUN_{step.status}",
                              entity_type="agent_run", entity_id=run.id, run_id=run.id, drive_id=run.drive_id,
                              details={"node": node, "message": step.message})
            else:
                emit(db, run, "TRANSITION", node=node, status="OK", message=f"{node} -> {step.next_node}")
            db.commit()
        finally:
            CURRENT_AGENT.reset(token)
    return run


def start_run(db: Session, objective: str, user: User, *, llm_client: Any | None = None, is_test_fixture: bool = False) -> AgentRun:
    if user.role not in (Role.OFFICER, Role.ADMIN):
        raise PermissionDenied("Only placement officers or admins can start agent runs")
    objective = (objective or "").strip()
    if not 3 <= len(objective) <= 2000:
        raise ValidationFailed("Objective must be between 3 and 2000 characters")
    run = AgentRun(objective=objective, planner_mode="PENDING", status=RunStatus.DRAFT, created_by_id=user.id,
                   state={"next_node": "interpret_goal"}, plan=[], is_test_fixture=is_test_fixture)
    db.add(run)
    db.flush()
    emit(db, run, "GOAL", node=None, status=RunStatus.DRAFT, message=objective, payload={"user_id": user.id, "role": user.role})
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="AGENT_RUN_STARTED",
                  entity_type="agent_run", entity_id=run.id, run_id=run.id, details={"objective": objective})
    db.commit()
    if llm_client is not None:
        _llm_override[run.id] = llm_client
    try:
        return advance(db, run)
    finally:
        _llm_override.pop(run.id, None)


_llm_override: dict[int, Any] = {}


def _start_workflow(db: Session, user: User, *, objective: str, intent: str, first_node: str, state: dict[str, Any],
                    drive_id: int | None = None, llm_client: Any | None = None) -> AgentRun:
    run = AgentRun(objective=objective, planner_mode="DETERMINISTIC_FALLBACK", status=RunStatus.DRAFT,
                   created_by_id=user.id, intent=intent, drive_id=drive_id, plan=_plan(intent),
                   state={"next_node": first_node, **state})
    db.add(run)
    db.flush()
    emit(db, run, "GOAL", node=None, status=RunStatus.DRAFT, message=objective, payload={"user_id": user.id, "role": user.role})
    emit(db, run, "PLAN", node=None, status="OK", message=f"Workflow {intent}", payload={"intent": intent, "steps": run.plan})
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="AGENT_RUN_STARTED",
                  entity_type="agent_run", entity_id=run.id, run_id=run.id, drive_id=drive_id,
                  details={"objective": objective, "intent": intent})
    db.commit()
    if llm_client is not None:
        _llm_override[run.id] = llm_client
    try:
        return advance(db, run)
    finally:
        _llm_override.pop(run.id, None)


def start_intake_run(db: Session, email_row: Any, user: User, *, llm_client: Any | None = None) -> AgentRun:
    """Stage 1: Email Reader -> Criteria Check -> Drive Builder -> Consent Form -> Student Outreach."""
    if user.role not in (Role.COORDINATOR, Role.OFFICER, Role.ADMIN):
        raise PermissionDenied("Only the drive upload officer, placement officers or admins process company emails")
    if email_row.status in ("DRIVE_CREATED", "PUBLISHED", "IGNORED"):
        raise Conflict(f"This email was already handled ({email_row.status.lower().replace('_', ' ')})")
    if email_row.run_id:
        existing = db.get(AgentRun, email_row.run_id)
        if existing is not None and existing.status in (RunStatus.NEEDS_REVIEW, RunStatus.FAILED):
            return existing
        if existing is not None and existing.status not in TERMINAL_RUN_STATES:
            raise Conflict("This email is already being processed")
    email_row.status = "PROCESSING"
    run = _start_workflow(db, user, objective=f"Process company email #{email_row.id}: {email_row.subject}"[:2000],
                          intent="PROCESS_COMPANY_EMAIL", first_node="read_email", state={"email_id": email_row.id},
                          llm_client=llm_client)
    if email_row.run_id != run.id:
        email_row.run_id = run.id
        db.commit()
    return run


def start_followup_run(db: Session, drive: Drive, user: User, closes_at: Any) -> AgentRun:
    """Stage 3: Form Builder -> Follow-up Mailer, started by the approver (or an officer)."""
    if user.role not in (Role.APPROVER, Role.OFFICER, Role.ADMIN):
        raise PermissionDenied("Only approvers, placement officers or admins can send follow-up forms")
    if closes_at <= utcnow():
        raise ValidationFailed("The forms must close in the future")
    if closes_at > drive.starts_at:
        raise ValidationFailed("The forms must close before the drive starts")
    return _start_workflow(db, user, objective=f"Ask students with missing details or consent for drive #{drive.id}",
                           intent="FOLLOW_UP", first_node="build_forms", drive_id=drive.id,
                           state={"closes_at": closes_at.isoformat()})


def get_run(db: Session, run_id: int) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise NotFound(f"Agent run {run_id} not found")
    return run


def answer(db: Session, run: AgentRun, user: User, value: Any) -> AgentRun:
    """A person's response to a NEEDS_REVIEW / FAILED interrupt."""
    allowed = {Role.OFFICER, Role.ADMIN}
    if run.intent == "PROCESS_COMPANY_EMAIL":
        allowed.add(Role.COORDINATOR)
    if run.intent == "FOLLOW_UP":
        allowed.add(Role.APPROVER)
    if user.role not in allowed:
        raise PermissionDenied("Your role cannot answer this agent's questions")
    q = run.pending_question or {}
    if run.status not in (RunStatus.NEEDS_REVIEW, RunStatus.FAILED):
        raise Conflict(f"Run is {run.status}; it is not waiting for an officer answer")
    qtype = q.get("type")
    emit(db, run, "HUMAN_INPUT", node=run.current_step, status="RECEIVED",
         message=f"Officer {user.email} answered {qtype}: {value}", payload={"question": q, "answer": value})
    ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="AGENT_QUESTION_ANSWERED",
                  entity_type="agent_run", entity_id=run.id, run_id=run.id, drive_id=run.drive_id,
                  details={"question_type": qtype, "answer": value})
    if value == "stop":
        run.status, run.pending_question = RunStatus.REJECTED, None
        _set_state(run, next_node=None)
        run.summary = "Stopped by officer"
        db.commit()
        return run
    next_node: str | None
    if qtype == "choose_drive":
        options = {o["value"] for o in q.get("options", [])}
        if value not in options:
            raise ValidationFailed("Choose one of the offered drives")
        run.drive_id = int(value)
        next_node = "validate_scope"
    elif qtype == "criteria_mismatch" and value == "use_configured":
        src = (run.state.get("requirements") or {}).get("retrieved_source") or {}
        _set_state(run, criteria_mismatch_acknowledged_hash=src.get("sha256"))
        for exc in db.query(ExceptionItem).filter_by(run_id=run.id, status=ExceptionStatus.OPEN):
            if exc.kind in (ExceptionKind.CRITERIA_MISMATCH, ExceptionKind.AMBIGUOUS_CRITERIA):
                exc.status, exc.resolution = ExceptionStatus.RESOLVED, "CONFIGURED_AUTHORITATIVE"
                exc.resolved_by_id, exc.resolved_at = user.id, utcnow()
                exc.resolution_note = "Officer confirmed configured criteria are authoritative"
        next_node = "interpret_criteria"
    elif qtype == "invitations_needed" and value == "send_invitations":
        _set_state(run, invitations_checked=True)
        run.intent = "INVITE_STUDENTS"
        run.plan = _plan("INVITE_STUDENTS")
        _mark(run, "interpret_goal", "done")
        _mark(run, "validate_scope", "done")
        next_node = "consent_form"
    elif qtype == "intake_review" and value in ("confirm", "recheck"):
        from app.services import intake

        row = intake.get(db, int(run.state["email_id"]))
        if value == "confirm":
            if any(i["level"] == "ERROR" for i in intake.check(db, row)):
                raise ValidationFailed("The email still has errors; correct the details first, then check again")
            ledger.record(db, actor_type=ActorType.USER, actor_id=f"user:{user.id}", action="COMPANY_EMAIL_CONFIRMED",
                          entity_type="inbound_email", entity_id=row.id,
                          details={"acknowledged": [i["code"] for i in row.issues if i["level"] == "REVIEW"]})
        _set_state(run, review_acknowledged=value == "confirm")
        _mark(run, "check_email", "pending")
        next_node = "check_email"
    elif qtype == "intake_review" and value == "ignore":
        from app.services import intake

        intake.ignore(db, intake.get(db, int(run.state["email_id"])), user, "Ignored from the agent's question")
        exceptions.auto_resolve(db, f"intake:{run.state['email_id']}", "Email ignored")
        run.status, run.pending_question, run.summary = RunStatus.COMPLETED, None, "Company email ignored"
        _set_state(run, next_node=None)
        db.commit()
        return run
    elif qtype == "invitations_needed" and value == "continue":
        _set_state(run, invitations_checked=True)
        next_node = "retrieve_evidence"
    elif qtype == "source_unavailable" and value == "proceed_without_source":
        _set_state(run, proceed_without_source=True)
        next_node = "retrieve_evidence"
    elif value in ("rerun", "retry"):
        if qtype == "failure":
            next_node = run.state.get("failed_node") or "interpret_goal"
        elif qtype in ("choose_drive",) or run.drive_id is None:
            next_node = "interpret_goal"
        else:
            next_node = "validate_scope"
    else:
        raise ValidationFailed(f"Unsupported answer '{value}' for question type {qtype}")
    run.pending_question = None
    run.error = None
    _set_state(run, next_node=next_node)
    db.commit()
    return advance(db, run)


def resume_after_approval(db: Session, approval: ApprovalRequest) -> AgentRun | None:
    """Called by the API after a human approves; continues the run that requested it."""
    run_id = approval.proposal.run_id
    if not run_id:
        return None
    run = db.get(AgentRun, run_id)
    if run is None or run.state.get("approval_id") != approval.id:
        return None
    if approval.status != ApprovalStatus.APPROVED:
        return run
    run.status = RunStatus.APPROVED
    run.pending_question = None
    _mark(run, "propose", "done", "Approved by human")
    emit(db, run, "APPROVAL", node="approval_gate", status="APPROVED",
         message=f"Human approval #{approval.id} granted by user:{approval.decided_by_id}; resuming",
         payload={"approval_id": approval.id, "proposal_hash": approval.proposal_hash})
    _set_state(run, next_node="execute_upload")
    db.commit()
    return advance(db, run)


def mark_rejected(db: Session, approval: ApprovalRequest) -> None:
    run_id = approval.proposal.run_id
    run = db.get(AgentRun, run_id) if run_id else None
    if run is None or run.state.get("approval_id") != approval.id:
        return
    run.status = RunStatus.REJECTED
    run.pending_question = None
    _set_state(run, next_node=None)
    _mark(run, "propose", "rejected", approval.decision_note)
    emit(db, run, "APPROVAL", node="approval_gate", status="REJECTED",
         message=f"Approval #{approval.id} rejected: {approval.decision_note or 'no reason given'}")
    db.commit()


def sync_after_upload_action(db: Session, upload_id: int) -> None:
    """Re-sync a run after a manual verify/reconcile action in the Verification Center."""
    from sqlalchemy import select

    from app.models.entities import AttendanceSheet, UploadOperation, VerificationResult

    for run in db.scalars(select(AgentRun).where(AgentRun.status.in_([
        RunStatus.PENDING_RECONCILIATION, RunStatus.FAILED_VERIFICATION, RunStatus.FAILED]))):
        if run.state.get("approval_id") is None:
            continue
        op = db.scalar(select(UploadOperation).where(UploadOperation.approval_id == run.state["approval_id"]))
        if op is None or op.id != upload_id:
            continue
        v = db.scalar(select(VerificationResult).where(VerificationResult.upload_id == op.id).order_by(VerificationResult.id.desc()))
        sheet = db.scalar(select(AttendanceSheet).where(AttendanceSheet.upload_id == op.id).order_by(AttendanceSheet.id.desc()))
        _set_state(run, upload_id=op.id)
        if v and v.status == VerificationStatus.VERIFIED:
            run.status = RunStatus.VERIFIED
            run.pending_question = None
            _set_state(run, verification={"verification_id": v.id, "status": v.status, "expected_count": v.expected_count,
                                          "observed_count": v.observed_count, "missing": v.missing_roll_numbers,
                                          "unexpected": v.unexpected_roll_numbers, "duplicates": v.duplicate_roll_numbers},
                       attendance_sheet_id=sheet.id if sheet else None)
            _mark(run, "execute_upload", "done")
            _mark(run, "verify", "done", "Verified after manual reconciliation")
            if sheet:
                _mark(run, "attendance", "done")
                _mark(run, "notify", "done", "Sent after manual verification")
            run.summary = _summary(run)
            emit(db, run, "COMPLETE", node="verify", status=RunStatus.VERIFIED,
                 message="Run verified after manual verification/reconciliation in the Verification Center")
        elif v and v.status == VerificationStatus.FAILED_VERIFICATION:
            run.status = RunStatus.FAILED_VERIFICATION
            emit(db, run, "COMPLETE", node="verify", status=RunStatus.FAILED_VERIFICATION, message=v.detail)
        db.commit()

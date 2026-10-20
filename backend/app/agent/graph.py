"""Durable workflow graph for the placement drive coordinator agent.

The graph is a set of nodes connected by conditional edges chosen from actual
tool results. After every node the run's status, current step, plan progress
and `state` (including `next_node`) are committed, so a run survives process
restarts and can be resumed exactly where it stopped. Human decisions are
interrupts: the run stops in NEEDS_REVIEW or AWAITING_APPROVAL and resumes
only through an authenticated API call.

    interpret_goal -> validate_scope -> retrieve_evidence -> interpret_criteria
        -> evaluate_roster -> check_conflicts -> propose ==(AWAITING_APPROVAL)==>
        [human approves] -> execute_upload -> verify -> attendance -> VERIFIED
    WHAT_IF:          ... validate_scope -> simulate -> COMPLETED
    CHECK_CONFLICTS:  ... evaluate_roster -> check_conflicts -> COMPLETED
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agent.events import emit
from app.agent.planner import make_plan
from app.agent.tools import ToolContext, ToolFailure, call_tool
from app.audit import ledger
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.db.base import utcnow
from app.models.entities import AgentRun, ApprovalRequest, Drive, ExceptionItem, User
from app.models.enums import (
    TERMINAL_RUN_STATES,
    ActorType,
    ApprovalStatus,
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

GRAPH = "graph"

PLAN_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "PREPARE_ROSTER": [
        ("interpret_goal", "Interpret the request and identify the drive"),
        ("validate_scope", "Validate scope, permissions and destination"),
        ("retrieve_evidence", "Retrieve configured criteria and the employer's published drive form"),
        ("interpret_criteria", "Cross-check published criteria against configured criteria"),
        ("evaluate_roster", "Validate roster and evaluate eligibility + consent for every student"),
        ("check_conflicts", "Detect schedule clashes and hold affected students"),
        ("propose", "Create hashed roster proposal and request human approval"),
        ("execute_upload", "Upload the approved roster (idempotent)"),
        ("verify", "Re-read destination and compare exact IDs and counts"),
        ("attendance", "Generate attendance sheet and receipt"),
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
}


@dataclass
class Step:
    next_node: str | None
    status: str | None = None  # terminal/interrupt status when next_node is None
    message: str | None = None


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
    run.llm_model = get_settings().llm_model if plan.planner_mode.startswith("LLM") else None
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
    if drive.status not in (DriveStatus.OPEN, DriveStatus.DRAFT) and run.intent == "PREPARE_ROSTER":
        problems.append(f"Drive status is {drive.status}; roster preparation requires an OPEN drive")
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
                        + (f": {diffs}" if diffs else f": {parsed.get('ambiguities')}")
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
    result = call_tool(ctx, "create_approval_request", {"drive_id": run.drive_id}, node="propose", invoked_by=GRAPH)
    run.proposal_id = result["proposal_id"]
    _set_state(run, approval_id=result["approval_id"], proposal_hash=result["proposal_hash"])
    _mark(run, "propose", "done", f"Approval #{result['approval_id']} requested")
    run.pending_question = {"type": "approval", "approval_id": result["approval_id"],
                            "question": f"Review and approve roster proposal #{result['proposal_id']} "
                                        f"({result['included_count']} students) in the Approval Center."}
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
    run.pending_question = None
    run.summary = _summary(run)
    return Step(None, RunStatus.VERIFIED, "Roster uploaded and independently verified")


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
    return ToolContext(db=db, run=run, user=db.get(User, run.created_by_id) if run.created_by_id else None)


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
        emit(db, run, "NODE_START", node=node, status=run.status, message=f"Entering {node}")
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


def get_run(db: Session, run_id: int) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise NotFound(f"Agent run {run_id} not found")
    return run


def answer(db: Session, run: AgentRun, user: User, value: Any) -> AgentRun:
    """Officer's response to a NEEDS_REVIEW / FAILED interrupt."""
    if user.role not in (Role.OFFICER, Role.ADMIN):
        raise PermissionDenied("Only officers or admins can answer agent questions")
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
            run.summary = _summary(run)
            emit(db, run, "COMPLETE", node="verify", status=RunStatus.VERIFIED,
                 message="Run verified after manual verification/reconciliation in the Verification Center")
        elif v and v.status == VerificationStatus.FAILED_VERIFICATION:
            run.status = RunStatus.FAILED_VERIFICATION
            emit(db, run, "COMPLETE", node="verify", status=RunStatus.FAILED_VERIFICATION, message=v.detail)
        db.commit()

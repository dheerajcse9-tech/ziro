"""Official scenario runner (TC1-TC4).

Every run builds a fresh sandbox (in-memory DB + in-process LOCAL MOCK portal),
loads a synthetic fixture, drives the *real* agent graph, approval service,
upload adapter, verification and attendance generation, then compares actual
state against expectations. Nothing is hard-coded to pass: each check reads
the resulting database rows and the destination's read-back.
"""
from __future__ import annotations

import csv
import io
import time
import traceback
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import graph
from app.core.errors import PreconditionFailed
from app.db.base import utcnow
from app.models.entities import (
    AttendanceSheet,
    EligibilityDecision,
    ExceptionItem,
    Student,
    TestRun,
    UploadOperation,
    User,
)
from app.models.enums import ApprovalStatus, ExceptionKind, Role, RunStatus
from app.services import approvals, students, uploads
from app.services.roster import drive_pair_conflicts
from app.services.sandbox import Sandbox, ist

ENVIRONMENT = "Isolated sandbox: in-memory SQLite + in-process LOCAL MOCK portal over the HTTP form adapter; deterministic planner; no external service contacted"


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def eq(self, name: str, expected: Any, actual: Any) -> None:
        self.items.append({"name": name, "expected": expected, "actual": actual, "passed": expected == actual})

    def true(self, name: str, actual: bool, detail: Any = None) -> None:
        self.items.append({"name": name, "expected": True, "actual": bool(actual), "passed": bool(actual), "detail": detail})

    @property
    def passed(self) -> bool:
        return bool(self.items) and all(i["passed"] for i in self.items)


def _approve_and_execute(sb: Sandbox, run, approver: User):
    appr = approvals.approve(sb.db, run.state["approval_id"], approver, expected_hash=run.state["proposal_hash"])
    sb.db.commit()
    return graph.resume_after_approval(sb.db, appr)


def _decisions(db: Session, drive_id: int) -> dict[str, EligibilityDecision]:
    return {d.roll_number: d for d in db.scalars(select(EligibilityDecision).where(
        EligibilityDecision.drive_id == drive_id, EligibilityDecision.is_stale.is_(False)))}


def _pipeline_evidence(sb: Sandbox, run) -> dict[str, Any]:
    op = sb.db.scalar(select(UploadOperation).where(UploadOperation.approval_id == run.state.get("approval_id")))
    sheet = sb.db.scalar(select(AttendanceSheet).where(AttendanceSheet.upload_id == op.id)) if op else None
    return {
        "agent_run": {"id": run.id, "status": run.status, "planner_mode": run.planner_mode, "summary": run.summary,
                      "tool_calls": [e.tool_name for e in run.events if e.event_type == "TOOL_CALL"]},
        "proposal_hash": run.state.get("proposal_hash"),
        "upload": None if op is None else {"operation_key": op.operation_key, "status": op.status,
                                           "submitted": op.submitted_roll_numbers, "receipt": op.destination_receipt},
        "verification": run.state.get("verification"),
        "attendance_rows": [] if sheet is None else sheet.roll_numbers,
    }


def _portal_roster(sb: Sandbox, form_ref: str) -> list[str]:
    from app.integrations.drive_form import HtmlDriveFormAdapter

    return HtmlDriveFormAdapter("http://mockportal", client=sb.portal).read_roster(form_ref).roll_numbers


# --------------------------------------------------------------------------- TC1

def tc1(sb: Sandbox) -> dict[str, Any]:
    officer, approver = sb.user(Role.OFFICER), sb.user(Role.APPROVER)
    fixture = [
        ("TC1-A", "Asha Below", "CSE", 6.99, 0), ("TC1-B", "Bala Boundary", "CSE", 7.00, 0),
        ("TC1-C", "Chitra Above", "IT", 8.50, 0), ("TC1-D", "Dev OneBacklog", "CSE", 8.10, 1),
        ("TC1-E", "Esha ThreeBacklogs", "ECE", 9.00, 3), ("TC1-F", "Faiz NoCgpa", "CSE", None, 0),
        ("TC1-G", "Gita NoBacklogData", "IT", 7.50, None), ("TC1-H", "Hari Both", "ME", 6.50, 2),
    ]
    for roll, name, branch, cgpa, bl in fixture:
        sb.student(roll, name, branch, cgpa, bl)
    drive = sb.drive("TC1 Drive (CGPA>=7, no backlogs)", ist(2026, 12, 1, 10), ist(2026, 12, 1, 12))
    run = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{drive.id} and request approval", officer)
    decisions = _decisions(sb.db, drive.id)
    run = _approve_and_execute(sb, run, approver)

    expected_decisions = {"TC1-A": "INELIGIBLE", "TC1-B": "ELIGIBLE", "TC1-C": "ELIGIBLE", "TC1-D": "INELIGIBLE",
                          "TC1-E": "INELIGIBLE", "TC1-F": "UNKNOWN", "TC1-G": "UNKNOWN", "TC1-H": "INELIGIBLE"}
    actual_decisions = {r: d.decision for r, d in sorted(decisions.items())}
    c = Checks()
    c.eq("Per-student decisions", expected_decisions, actual_decisions)
    c.eq("Eligible list uploaded (exact IDs)", ["TC1-B", "TC1-C"], sorted(_pipeline_evidence(sb, run)["upload"]["submitted"]))
    c.true("CGPA exactly 7.00 passes (inclusive threshold)",
           any(r["rule"] == "CGPA" and r["outcome"] == "PASS" for r in decisions["TC1-B"].rule_results))
    c.true("CGPA 6.99 fails with CGPA reason", any("below minimum" in x for x in decisions["TC1-A"].reasons))
    c.true("1 backlog fails with backlog reason", any("backlog" in x for x in decisions["TC1-D"].reasons))
    c.eq("Missing CGPA -> UNKNOWN with missing evidence", ["CGPA"], decisions["TC1-F"].missing_evidence)
    c.eq("Missing backlog data -> UNKNOWN with missing evidence", ["BACKLOGS"], decisions["TC1-G"].missing_evidence)
    c.eq("Destination read-back equals approved IDs", ["TC1-B", "TC1-C"], sorted(_portal_roster(sb, drive.destination.form_ref)))
    c.eq("Run outcome", RunStatus.VERIFIED, run.status)
    return {
        "input": {"criteria": "CGPA >= 7.0 (inclusive), 0 active backlogs, all branches",
                  "students": [dict(zip(["roll", "name", "branch", "cgpa", "backlogs"], f)) for f in fixture]},
        "expected": {"eligible_ids": ["TC1-B", "TC1-C"], "decisions": expected_decisions},
        "actual": {"eligible_ids": sorted(r for r, d in decisions.items() if d.decision == "ELIGIBLE"),
                   "decisions": actual_decisions,
                   "reasons": {r: d.reasons for r, d in sorted(decisions.items()) if d.reasons}},
        "checks": c, "evidence": _pipeline_evidence(sb, run),
    }


# --------------------------------------------------------------------------- TC2

def tc2(sb: Sandbox) -> dict[str, Any]:
    officer, approver = sb.user(Role.OFFICER), sb.user(Role.APPROVER)
    csv_v1 = ("Roll No,Name,Branch,CGPA,Backlogs,Consent\n"
              "21CS102,Rahul Kumar,CSE,8.20,0,yes\n"
              "21ME102,Rahul Kumar,Mechanical,6.50,2,yes\n"
              "21CS103,Rahul Kumar ,CSE,,0,yes\n")
    imp = students.preview_import(sb.db, "tc2_roster.csv", csv_v1.encode(), None, officer)
    students.commit_import(sb.db, imp.id, officer)
    sb.db.commit()
    # Re-import with a corrected CGPA for one Rahul: must update only that roll number.
    csv_v2 = "Roll No,Name,Branch,CGPA,Backlogs,Consent\n21CS103,Rahul Kumar,CSE,7.10,1,yes\n"
    imp2 = students.preview_import(sb.db, "tc2_correction.csv", csv_v2.encode(), None, officer)
    students.commit_import(sb.db, imp2.id, officer)
    sb.db.commit()
    rahuls = {s.roll_number: s for s in sb.db.scalars(select(Student).where(Student.full_name == "Rahul Kumar"))}

    drive = sb.drive("TC2 Drive (CSE, CGPA>=7)", ist(2026, 12, 2, 10), ist(2026, 12, 2, 12), branches=["CSE"])
    run = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{drive.id}", officer)
    decisions = _decisions(sb.db, drive.id)
    run = _approve_and_execute(sb, run, approver)
    ev = _pipeline_evidence(sb, run)
    portal = _portal_roster(sb, drive.destination.form_ref)

    c = Checks()
    c.eq("Three distinct Rahul Kumar records keyed by roll number", ["21CS102", "21CS103", "21ME102"], sorted(rahuls))
    c.eq("Correction re-import updated only 21CS103 (CGPA)", 7.10, rahuls["21CS103"].cgpa)
    c.eq("Correction did not touch 21CS102", 8.20, rahuls["21CS102"].cgpa)
    c.eq("21CS102 decision", "ELIGIBLE", decisions["21CS102"].decision)
    c.eq("21ME102 decision", "INELIGIBLE", decisions["21ME102"].decision)
    c.eq("21CS103 decision (1 backlog after correction)", "INELIGIBLE", decisions["21CS103"].decision)
    c.eq("Uploaded IDs contain only the intended Rahul", ["21CS102"], sorted(ev["upload"]["submitted"]))
    c.eq("Destination read-back", ["21CS102"], sorted(portal))
    c.eq("Attendance sheet rows", ["21CS102"], ev["attendance_rows"])
    c.eq("Run outcome", RunStatus.VERIFIED, run.status)
    return {
        "input": {"import_v1": csv_v1, "import_v2_correction": csv_v2, "criteria": "CSE only, CGPA >= 7, no backlogs"},
        "expected": {"eligible_ids": ["21CS102"], "excluded": ["21ME102", "21CS103"], "matching": "by roll number"},
        "actual": {"students_named_rahul": {r: {"branch": s.branch, "cgpa": s.cgpa, "backlogs": s.active_backlogs,
                                                "record_version": s.record_version} for r, s in sorted(rahuls.items())},
                   "decisions": {r: decisions[r].decision for r in sorted(rahuls)},
                   "uploaded": ev["upload"]["submitted"], "destination": portal},
        "checks": c, "evidence": {**ev, "imports": [imp.id, imp2.id]},
    }


# --------------------------------------------------------------------------- TC3

def tc3(sb: Sandbox) -> dict[str, Any]:
    officer, approver = sb.user(Role.OFFICER), sb.user(Role.APPROVER)
    sb.student("TC3-OK", "Consenting Student", "CSE", 8.0, 0, consent="GRANTED")
    sb.student("TC3-NONE", "No Consent Record", "CSE", 9.2, 0, consent=None)
    sb.student("TC3-REV", "Revoked Consent", "IT", 8.7, 0, consent="REVOKED")
    late = sb.student("TC3-LATE", "Revokes After Approval", "CSE", 7.8, 0, consent="GRANTED")
    drive = sb.drive("TC3 Drive", ist(2026, 12, 3, 10), ist(2026, 12, 3, 12))
    run = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{drive.id}", officer)
    decisions = _decisions(sb.db, drive.id)
    run = _approve_and_execute(sb, run, approver)
    ev = _pipeline_evidence(sb, run)
    portal = _portal_roster(sb, drive.destination.form_ref)

    # Phase B: consent revoked *after* approval must block execution (re-check before upload).
    drive_b = sb.drive("TC3 Drive B (revocation after approval)", ist(2026, 12, 4, 10), ist(2026, 12, 4, 12))
    run_b = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{drive_b.id}", officer)
    appr_b = approvals.approve(sb.db, run_b.state["approval_id"], approver, expected_hash=run_b.state["proposal_hash"])
    sb.db.commit()
    # Revocation bypassing the normal service (simulating a race just before execution).
    from app.models.entities import ConsentRecord

    sb.db.add(ConsentRecord(student_id=late.id, scope="SHARE_WITH_EMPLOYERS", status="REVOKED", source="race-simulation"))
    sb.db.commit()
    blocked_message = None
    try:
        uploads.execute(sb.db, appr_b.id, actor="testlab")
    except PreconditionFailed as exc:
        blocked_message = exc.message
    sb.db.refresh(appr_b)
    portal_b = _portal_roster(sb, drive_b.destination.form_ref)

    c = Checks()
    c.eq("Eligible without consent record -> excluded", "EXCLUDED", decisions["TC3-NONE"].roster_status)
    c.eq("Eligible with revoked consent -> excluded", "EXCLUDED", decisions["TC3-REV"].roster_status)
    c.true("Exclusion reason states consent", any("Consent" in r for r in decisions["TC3-NONE"].reasons), decisions["TC3-NONE"].reasons)
    c.true("Not in submission payload", not {"TC3-NONE", "TC3-REV"} & set(ev["upload"]["submitted"]), ev["upload"]["submitted"])
    c.true("Not in destination roster", not {"TC3-NONE", "TC3-REV"} & set(portal), portal)
    c.true("Not in attendance sheet", not {"TC3-NONE", "TC3-REV"} & set(ev["attendance_rows"]), ev["attendance_rows"])
    c.eq("Consented students uploaded", ["TC3-LATE", "TC3-OK"], sorted(ev["upload"]["submitted"]))
    c.true("Phase B: consent revoked after approval blocks the upload", blocked_message is not None, blocked_message)
    c.eq("Phase B: approval invalidated", ApprovalStatus.INVALIDATED, appr_b.status)
    c.eq("Phase B: nothing reached the destination", [], portal_b)
    return {
        "input": {"students": ["TC3-OK (granted)", "TC3-NONE (no record)", "TC3-REV (revoked)", "TC3-LATE (granted, revoked after approval in phase B)"],
                  "criteria": "CGPA >= 7, no backlogs"},
        "expected": {"uploaded": ["TC3-LATE", "TC3-OK"], "excluded_for_consent": ["TC3-NONE", "TC3-REV"],
                     "phase_b": "upload blocked, approval invalidated, destination empty"},
        "actual": {"uploaded": ev["upload"]["submitted"], "destination": portal, "attendance": ev["attendance_rows"],
                   "decisions": {r: {"decision": d.decision, "roster_status": d.roster_status, "reasons": d.reasons}
                                 for r, d in sorted(decisions.items())},
                   "phase_b": {"blocked": blocked_message, "approval_status": appr_b.status, "destination": portal_b}},
        "checks": c, "evidence": ev,
    }


# --------------------------------------------------------------------------- TC4

def tc4(sb: Sandbox) -> dict[str, Any]:
    from zoneinfo import ZoneInfo
    from datetime import datetime

    officer, approver = sb.user(Role.OFFICER), sb.user(Role.APPROVER)
    sb.student("TC4-BOTH", "Eligible For Both", "CSE", 8.5, 0)
    sb.student("TC4-BONLY", "Eligible Only For B", "ME", 8.0, 0)
    drive_a = sb.drive("TC4 Drive A (10:00-13:00 IST)", ist(2026, 12, 5, 10), ist(2026, 12, 5, 13), branches=["CSE"])
    drive_b = sb.drive("TC4 Drive B (11:30-14:00 IST)", ist(2026, 12, 5, 11, 30), ist(2026, 12, 5, 14))
    drive_c = sb.drive("TC4 Drive C (13:00-15:00 IST, touches A)", ist(2026, 12, 5, 13), ist(2026, 12, 5, 15))
    utc = ZoneInfo("UTC")
    drive_d = sb.drive("TC4 Drive D (05:00-06:00 UTC = 10:30-11:30 IST)", datetime(2026, 12, 5, 5, 0, tzinfo=utc),
                       datetime(2026, 12, 5, 6, 0, tzinfo=utc), tz="UTC")

    run_a = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{drive_a.id}", officer)
    run_b = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{drive_b.id}", officer)
    decisions_b = _decisions(sb.db, drive_b.id)
    conflict_exc = sb.db.scalar(select(ExceptionItem).where(ExceptionItem.kind == ExceptionKind.SCHEDULE_CONFLICT,
                                                             ExceptionItem.drive_id == drive_b.id))
    flagged_before_approval = conflict_exc is not None and run_b.status == RunStatus.AWAITING_APPROVAL
    pairs = {tuple(sorted((p["drive_a"]["id"], p["drive_b"]["id"]))) for p in drive_pair_conflicts(sb.db)}
    run_b = _approve_and_execute(sb, run_b, approver)
    ev_b = _pipeline_evidence(sb, run_b)
    portal_b = _portal_roster(sb, drive_b.destination.form_ref)

    c = Checks()
    c.eq("Drive A proposal includes TC4-BOTH", RunStatus.AWAITING_APPROVAL, run_a.status)
    c.eq("TC4-BOTH held on drive B (clash)", "HELD", decisions_b["TC4-BOTH"].roster_status)
    c.true("Clash flagged as an exception before approval", flagged_before_approval,
           conflict_exc.title if conflict_exc else None)
    c.true("Clash evidence includes overlap window", bool(decisions_b["TC4-BOTH"].conflicts and
                                                          decisions_b["TC4-BOTH"].conflicts[0].get("overlap_start")),
           decisions_b["TC4-BOTH"].conflicts)
    c.true("A and B overlap", tuple(sorted((drive_a.id, drive_b.id))) in pairs)
    c.true("A and C touch at 13:00 but do not overlap (half-open)", tuple(sorted((drive_a.id, drive_c.id))) not in pairs)
    c.true("A (IST) and D (UTC) overlap after timezone conversion", tuple(sorted((drive_a.id, drive_d.id))) in pairs)
    label = {drive_a.id: "A", drive_b.id: "B", drive_c.id: "C", drive_d.id: "D"}
    c.eq("Exact set of overlapping drive pairs", ["A-B", "A-D", "B-C"], sorted("-".join(sorted((label[a], label[b]))) for a, b in pairs))
    c.eq("Drive B upload excludes held student", ["TC4-BONLY"], sorted(ev_b["upload"]["submitted"]))
    c.eq("Drive B destination read-back", ["TC4-BONLY"], sorted(portal_b))
    return {
        "input": {"drives": {"A": "10:00-13:00 IST (CSE)", "B": "11:30-14:00 IST (all branches)",
                             "C": "13:00-15:00 IST", "D": "05:00-06:00 UTC"},
                  "students": ["TC4-BOTH (CSE, eligible A+B)", "TC4-BONLY (ME, eligible B)"],
                  "sequence": "Agent prepares A (reserves TC4-BOTH), then prepares B"},
        "expected": {"held_on_B": ["TC4-BOTH"], "uploaded_to_B": ["TC4-BONLY"],
                     "overlapping_pairs": ["A-B", "A-D", "B-C"], "flagged_before_approval": True},
        "actual": {"drive_b_statuses": {r: d.roster_status for r, d in sorted(decisions_b.items())},
                   "conflict_exception": conflict_exc.title if conflict_exc else None,
                   "overlapping_pairs": sorted("-".join(sorted((label[a], label[b]))) for a, b in pairs),
                   "uploaded_to_B": ev_b["upload"]["submitted"], "destination_B": portal_b},
        "checks": c, "evidence": {**ev_b, "drive_a_run": run_a.id},
    }


SCENARIOS: dict[str, tuple[str, str, Callable[[Sandbox], dict[str, Any]]]] = {
    "TC1": ("CGPA threshold and backlogs", "CGPA >= 7 (inclusive) and no backlogs produce the correct eligible list; missing data yields UNKNOWN.", tc1),
    "TC2": ("Duplicate names", "Two (here three) students named Rahul Kumar are matched by roll number through import, evaluation, upload and attendance.", tc2),
    "TC3": ("Missing consent", "Eligible students without consent are excluded from payload, destination and attendance; revocation after approval blocks upload.", tc3),
    "TC4": ("Conflicting drives", "Overlapping drive slots are detected (with timezone conversion and half-open boundaries) and the clash is flagged before assignment.", tc4),
}


def run_scenario(db: Session, test_id: str, user: User | None) -> TestRun:
    title, description, fn = SCENARIOS[test_id]
    started = time.perf_counter()
    result: dict[str, Any] = {}
    failure = None
    try:
        with Sandbox() as sb:
            result = fn(sb)
    except Exception as exc:  # noqa: BLE001 - a crash is a FAIL with details, never a pass
        failure = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-4000:]}"
    checks: Checks | None = result.get("checks")
    passed = failure is None and checks is not None and checks.passed
    if failure is None and checks is not None and not checks.passed:
        failure = "; ".join(f"{i['name']}: expected {i['expected']!r}, got {i['actual']!r}" for i in checks.items if not i["passed"])
    tr = TestRun(
        test_id=test_id, title=title, description=description, input_fixture=_jsonable(result.get("input", {})),
        expected=_jsonable(result.get("expected", {})), actual=_jsonable(result.get("actual", {})), passed=passed,
        checks=_jsonable(checks.items if checks else []), evidence=_jsonable(result.get("evidence", {})),
        failure_details=failure, environment=ENVIRONMENT, executed_at=utcnow(),
        executed_by_id=user.id if user else None, duration_ms=int((time.perf_counter() - started) * 1000),
    )
    db.add(tr)
    db.flush()
    from app.audit import ledger

    ledger.record(db, actor_type="USER" if user else "SYSTEM", actor_id=f"user:{user.id}" if user else "system",
                  action="TEST_SCENARIO_RUN", entity_type="test_run", entity_id=tr.id,
                  details={"test_id": test_id, "passed": passed, "checks": len(tr.checks)})
    db.commit()
    return tr


def _jsonable(value: Any) -> Any:
    import json

    from app.core.hashing import canonical_json

    return json.loads(canonical_json(value))


def export_csv(runs: list[TestRun]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["test_id", "title", "passed", "executed_at", "checks_passed", "checks_total", "failure_details"])
    for r in runs:
        w.writerow([r.test_id, r.title, "PASS" if r.passed else "FAIL", r.executed_at.isoformat(),
                    sum(1 for c in r.checks if c["passed"]), len(r.checks), r.failure_details or ""])
    return buf.getvalue()

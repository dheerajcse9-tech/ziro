"""Guided walkthrough for evaluators.

Each press of "Yes, continue" runs the next step of a full placement drive on the real code (agents, checks,
approvals, upload, read-back verification, emails) inside a private sandbox: an in-memory database and the
LOCAL MOCK portal. Each request builds a fresh sandbox and replays the earlier steps before running the new one,
so no state is kept between requests (works on serverless hosts). Nothing touches the institute's data and no real email is sent. Every step returns what
happened (emails, lists, agent chains) and the checks it passed, read back from the sandbox database.
"""
from __future__ import annotations

import secrets
import time
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import select

from app.agent import crew, graph
from app.api import serializers as S
from app.db.base import utcnow
from app.models.entities import Drive, EmailMessage, ExceptionItem, Student, StudentRequest, User
from app.models.enums import ExceptionKind, InboundSource, RequestKind, Role, RunStatus
from app.services import app_settings, approval_review, approvals, intake, mailer, outreach, reminders, roster_refresh, test_lab
from app.services.sandbox import Sandbox



class Tour:
    """One step's sandbox. Nothing is kept between requests, so it works on serverless hosts too."""

    def __init__(self) -> None:
        self.id = secrets.token_urlsafe(8)
        self.sb = Sandbox()
        self.state: dict[str, Any] = {}


# --------------------------------------------------------------------------- helpers

def _check(name: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {"name": name, "expected": expected, "actual": actual, "passed": expected == actual}


def _email(m: EmailMessage) -> dict[str, Any]:
    return {"type": "email", "from": "driveuploadtesting@gmail.com (placement mailbox)", "to": m.to_address,
            "subject": m.subject, "body": m.body_text}


def _mails(db, category: str, drive_id: int | None = None) -> list[EmailMessage]:
    q = select(EmailMessage).where(EmailMessage.category == category)
    if drive_id is not None:
        q = q.where(EmailMessage.drive_id == drive_id)
    return list(db.scalars(q.order_by(EmailMessage.id)))


def _flow(run) -> dict[str, Any]:
    return {"type": "flow", "run": S.run(run, include_state=False)}


def _form(db, drive: Drive, roll: str, kind: str = RequestKind.DRIVE_INVITATION) -> StudentRequest:
    return next(r for r in db.scalars(select(StudentRequest).where(StudentRequest.drive_id == drive.id,
                                                                    StudentRequest.kind == kind))
                if r.student.roll_number == roll)


def _receive(t: Tour, index: int):
    mail = dict(intake.company_test_emails("Asia/Kolkata")[index])
    case, expect = mail.pop("case"), mail.pop("expect")
    row, _ = intake.ingest(t.sb.db, source=InboundSource.MANUAL, message_id=f"<tour-{t.id}-{index}@sandbox>", **mail)
    t.sb.db.commit()
    run = graph.start_intake_run(t.sb.db, row, t.state["people"][Role.COORDINATOR])
    t.sb.db.refresh(row)
    codes = sorted({x["code"] for x in row.issues or [] if x["level"] != "INFO" or x["code"] == "OVERLAPS"})
    return {"case": case, "expect": expect, "mail": mail, "row": row, "run": run, "status": run.status,
            "drive_id": row.drive_id, "codes": codes,
            "problems": [x["message"] for x in row.issues or [] if x["level"] in ("ERROR", "REVIEW") or x["code"] == "OVERLAPS"]}


def _company_email_block(mail: dict[str, Any]) -> dict[str, Any]:
    return {"type": "email", "from": f"{mail['from_name']} <{mail['from_address']}>", "to": "driveuploadtesting@gmail.com",
            "subject": mail["subject"], "body": mail["body"]}


def _decision_table(db, drive_id: int) -> dict[str, Any]:
    rows = []
    for roll, d in sorted(test_lab._decisions(db, drive_id).items()):
        s = db.scalar(select(Student).where(Student.roll_number == roll))
        result = {"INCLUDED": "On the list", "HELD": "Held: schedule clash"}.get(d.roster_status) or (
            "Missing details" if d.decision == "UNKNOWN" else "Left out")
        rows.append([roll, s.full_name, s.branch or "—", "—" if s.cgpa is None else f"{s.cgpa:g}",
                     "—" if s.active_backlogs is None else str(s.active_backlogs), result, "; ".join(d.reasons) or "Qualifies and said YES"])
    return {"type": "table", "columns": ["Roll", "Name", "Branch", "CGPA", "Backlogs", "Result", "Why"], "rows": rows}


# --------------------------------------------------------------------------- steps

def step_setup(t: Tour) -> dict[str, Any]:
    t.state["people"] = test_lab._institute(t.sb)
    students = list(t.sb.db.scalars(select(Student).order_by(Student.roll_number)))
    return {
        "role": "Setup", "title": "The institute: 16 test students and 4 staff accounts",
        "explain": "A fresh, private copy of PlaceCrew was just created for you. It holds the institute's 16 test students "
                   "(each one stands for one test case) and the staff accounts. Company emails from the three test company "
                   "mailboxes are trusted senders.",
        "blocks": [
            {"type": "table", "columns": ["Roll", "Name", "Branch", "CGPA", "Backlogs", "Batch"],
             "rows": [[s.roll_number, s.full_name, s.branch or "—", "—" if s.cgpa is None else f"{s.cgpa:g}",
                       "—" if s.active_backlogs is None else str(s.active_backlogs), str(s.graduation_year)] for s in students]},
            {"type": "kv", "items": [
                ["Drive upload officer", "Reads company emails, creates drives, sends consent forms (step 1 of every drive)"],
                ["Placement officer", "Runs the drive: prepares the student list (step 2)"],
                ["Approver", "Checks and approves the list (step 3). Only the approver can approve."],
                ["Admin", "Sets the system up; cannot approve lists"]]},
        ],
        "checks": [_check("Students loaded", 16, len(students)),
                   _check("Missing data present for the test (Diya's CGPA, Eshan's backlogs)", 2,
                          sum(1 for s in students if s.cgpa is None or s.active_backlogs is None))],
    }


def step_company_emails(t: Tour) -> dict[str, Any]:
    e1, e2 = _receive(t, 0), _receive(t, 1)
    t.state["A"], t.state["B"] = e1["drive_id"], e2["drive_id"]
    db = t.sb.db
    invites = _mails(db, "DRIVE_INVITATION", e1["drive_id"])
    return {
        "role": "Drive upload officer · Stage 1 agents", "title": "Two companies email the placement mailbox",
        "explain": "Nimbus Softech and Orion Analytics email driveuploadtesting@gmail.com. The Mailbox Agent picks the emails "
                   "up; the Email Reader Agent reads out the company, role, date, time, deadline and criteria; the Criteria "
                   "Check Agent checks them; the Drive Builder creates each drive; the Consent Form Agent prepares one consent "
                   "form per drive; and the Student Outreach Agent emails every student a personal link. Orion's drive is on the "
                   "same day and overlaps Nimbus's, which the agents flag.",
        "blocks": [
            {"type": "heading", "text": "Email 1 — from companytestmailone@gmail.com"}, _company_email_block(e1["mail"]),
            {"type": "heading", "text": "How the agents handled it"}, _flow(e1["run"]),
            {"type": "heading", "text": "Email 2 — from companytestmailtwo@gmail.com"}, _company_email_block(e2["mail"]),
            {"type": "note", "text": "Agents' notice: " + " ".join(e2["problems"])},
            {"type": "heading", "text": "One of the 16 consent-form emails the students received"}, _email(invites[0]),
        ],
        "checks": [_check("Nimbus drive created by the agents", RunStatus.COMPLETED, e1["status"]),
                   _check("Orion drive created by the agents", RunStatus.COMPLETED, e2["status"]),
                   _check("Orion flagged as overlapping Nimbus", True, "OVERLAPS" in e2["codes"]),
                   _check("Consent form emailed to every student for Nimbus", 16, len(invites)),
                   _check("Consent form emailed to every student for Orion", 16, len(_mails(db, "DRIVE_INVITATION", e2["drive_id"])))],
    }


def step_bad_emails(t: Tour) -> dict[str, Any]:
    cases = [_receive(t, i) for i in (2, 3, 4, 5)]
    drives = t.sb.db.query(Drive).count()
    blocks: list[dict[str, Any]] = []
    for c in cases:
        blocks += [{"type": "heading", "text": c["case"]}, _company_email_block(c["mail"]),
                   {"type": "note", "text": "Agents stopped it: " + " ".join(c["problems"])}]
    return {
        "role": "Drive upload officer · Criteria Check Agent", "title": "Four problem emails are stopped, not turned into drives",
        "explain": "The same email sent twice, an email without a registration deadline, one whose deadline is after the drive, "
                   "and one from an unknown sender that tries to instruct the system (\"skip the consent step\"). The Criteria "
                   "Check Agent stops each one and asks the drive upload officer; nothing is sent to students.",
        "blocks": blocks,
        "checks": [_check("E3 same email twice → duplicate", True, "DRIVE_EXISTS" in cases[0]["codes"]),
                   _check("E4 no deadline → stopped", True, "MISSING" in cases[1]["codes"]),
                   _check("E5 deadline after the drive → stopped", True, "AFTER_DRIVE" in cases[2]["codes"]),
                   _check("E6 unknown sender + instruction-like text → stopped", ["NEW_COMPANY", "SUSPICIOUS"], cases[3]["codes"]),
                   _check("Still only the 2 valid drives exist", 2, drives)],
    }


ANSWERS_A = {"22CSE001": True, "22CSE002": False, "22CSE003": True, "22IT002": True, "22CSE004": True, "22AIDS001": True,
             "22CSE005": True, "22CSE006": True, "22CSE007": True, "22IT004": True, "22IT003": True, "22ECE001": True,
             "21CE001": True}
ANSWERS_B = {"22ECE001": True, "22ME001": True, "22AIDS001": True, "22CSE004": True}


def step_students_answer(t: Tour) -> dict[str, Any]:
    db = t.sb.db
    A, B = db.get(Drive, t.state["A"]), db.get(Drive, t.state["B"])
    for roll, yes in ANSWERS_A.items():
        outreach.respond_invitation(db, _form(db, A, roll), yes)
    for roll, yes in ANSWERS_B.items():
        outreach.respond_invitation(db, _form(db, B, roll), yes)
    db.commit()
    rows = []
    for s in db.scalars(select(Student).order_by(Student.roll_number)):
        a = ANSWERS_A.get(s.roll_number)
        b = ANSWERS_B.get(s.roll_number)
        rows.append([s.roll_number, s.full_name, "YES" if a else "NO" if a is False else "no answer",
                     "YES" if b else "no answer"])
    yes = sum(1 for r in db.scalars(select(StudentRequest).where(StudentRequest.drive_id == A.id)) if r.status == "ACCEPTED")
    # The forms close in 12 days; to show the Reminder Agent now, the reminder window (Settings) is set to 13 days.
    app_settings.set_value(db, "reminder_hours_before_deadline", 13 * 24)
    reminders.run(db)
    app_settings.set_value(db, "reminder_hours_before_deadline", 24)
    db.commit()
    sent = _mails(db, "REMINDER")
    charan = [m for m in sent if m.to_address.startswith("22it001")]
    answered = [m for m in sent if m.request_id and db.get(StudentRequest, m.request_id).status != "SENT"]
    return {
        "role": "Students · Reminder Agent", "title": "Students answer the consent forms; the rest get a reminder",
        "explain": "Each student opens their personal link and answers YES or NO. Only a YES lets the agents put a student "
                   "on a company's list. Charan and Manav do not answer; Bhavya says NO; Ishaan says YES to both "
                   "overlapping drives. The Reminder Agent then emails only the students who have not answered yet. "
                   "(The forms close in 12 days; to show the reminder now, this walkthrough sets the reminder time in "
                   "Settings to 13 days before closing. Normally it is 24 hours.)",
        "blocks": [{"type": "table", "columns": ["Roll", "Name", "Nimbus (A)", "Orion (B)"], "rows": rows},
                   {"type": "heading", "text": f"The reminder Charan received (one of {len(sent)} reminders)"}, _email(charan[0])],
        "checks": [_check("YES answers recorded for Nimbus", 12, yes),
                   _check("Charan (no answer) gets a reminder", True, bool(charan)),
                   _check("Nobody who already answered is reminded", 0, len(answered))],
    }


def step_officer_runs(t: Tour) -> dict[str, Any]:
    db, people = t.sb.db, t.state["people"]
    A, B = db.get(Drive, t.state["A"]), db.get(Drive, t.state["B"])
    run_b = graph.start_run(db, f"Prepare the eligible roster for drive #{B.id}", people[Role.OFFICER])
    run_a = graph.start_run(db, f"Prepare the eligible roster for drive #{A.id}", people[Role.OFFICER])
    t.state["run_a"], t.state["run_b"] = run_a.id, run_b.id
    first = approvals.get_approval(db, run_a.state["approval_id"])
    t.state["first_approval"] = first.id
    dec = test_lab._decisions(db, A.id)
    return {
        "role": "Placement officer · Stage 2 agents", "title": "The placement officer runs both drives",
        "explain": "The officer clicks \"Prepare the student list\". The Goal Planner picks the drive; the Evidence Agent compares "
                   "the company's criteria with the drive's; the Eligibility Agent applies CGPA, backlogs, branch and batch to "
                   "every student; the Consent Agent keeps only YES answers; the Conflict Agent holds students booked on an "
                   "overlapping drive; the Roster Agent sends the list to the approver, and the Approval Review Agent re-checks it.",
        "blocks": [{"type": "heading", "text": "How the agents prepared Nimbus's list"}, _flow(run_a),
                   {"type": "heading", "text": "Every student's result for Nimbus (CGPA ≥ 7.0, no backlogs, CSE / IT / AI & DS)"},
                   _decision_table(db, A.id)],
        "checks": [_check("Nimbus list waits for the approver", RunStatus.AWAITING_APPROVAL, run_a.status),
                   _check("Nimbus list", ["22CSE001", "22CSE005", "22CSE007"], first.proposal.included_roll_numbers),
                   _check("CGPA exactly 7.0 qualifies (Jiya)", "INCLUDED", dec["22CSE005"].roster_status),
                   _check("CGPA 6.99 does not (Kabir)", "INELIGIBLE", dec["22CSE006"].decision),
                   _check("Said NO → left out (Bhavya)", "EXCLUDED", dec["22CSE002"].roster_status),
                   _check("Missing CGPA → missing details (Diya)", "UNKNOWN", dec["22CSE003"].decision),
                   _check("YES to two overlapping drives → held (Ishaan)", "HELD", dec["22AIDS001"].roster_status),
                   _check("Two students named Rahul Kumar kept apart by roll number", ("INCLUDED", "INELIGIBLE"),
                          (dec["22CSE007"].roster_status, dec["22IT004"].decision)),
                   _check("Orion list", ["22AIDS001", "22ECE001", "22ME001"],
                          approvals.get_approval(db, run_b.state["approval_id"]).proposal.included_roll_numbers)],
    }


def step_ask(t: Tour) -> dict[str, Any]:
    from app.agent import assistant

    db, people = t.sb.db, t.state["people"]
    qa = []
    for q in ("Why isn't Farah on the Nimbus list?", "What if Nimbus lowered the CGPA to 6.5?", "Send personal forms for Nimbus"):
        qa.append((q, assistant.ask(db, people[Role.APPROVER], q)))
    blocks: list[dict[str, Any]] = []
    for q, r in qa:
        how = "Gemini, with tools" if r["mode"] == "AI" else f"built-in rules (AI not used: {r.get('ai_error')})"
        blocks += [{"type": "heading", "text": f"“{q}”"},
                   {"type": "kv", "items": [["Answer", r["answer"]],
                                            ["How it worked", f"{how} · " + " → ".join(s["label"] for s in r["steps"])]]
                    + ([["Proposed action (needs a person's OK)", f"{p['label']} · {p.get('drive', '')}"] for p in r["proposals"]])}]
    farah, what_if, forms = (r for _q, r in qa)
    return {
        "role": "Approver · Ask PlaceCrew", "title": "Ask the crew in plain language",
        "explain": "Staff can ask questions in their own words. The assistant agent plans which read-only tools to use (on live "
                   "data), answers with the real reasons, can run a what-if without changing anything, and can only PROPOSE "
                   "actions: a person must confirm them. On the deployed app it uses Gemini when a key is configured; otherwise "
                   "the same tools are driven by built-in rules, and the answer says so.",
        "blocks": blocks,
        "checks": [_check("Farah's answer quotes the real reason", True, "CGPA 6.40 below minimum 7.00" in farah["answer"]
                          or "6.4" in farah["answer"]),
                   _check("What-if finds Kabir (6.99) would be added, nothing changed", True,
                          "Kabir" in what_if["answer"] and "Kabir" not in str(approvals.get_approval(db, t.state["first_approval"])
                                                                            .proposal.included_roll_numbers)),
                   _check("The action is only proposed, not done", ("send_personal_forms", 0),
                          (forms["proposals"][0]["action"] if forms["proposals"] else None, len(_mails(db, "DATA_REQUEST"))))],
    }


def step_approver_forms(t: Tour) -> dict[str, Any]:
    db, people = t.sb.db, t.state["people"]
    A = db.get(Drive, t.state["A"])
    first = approvals.get_approval(db, t.state["first_approval"])
    review = approval_review.review(db, first)
    follow = {i["roll_number"]: i["fields"] for i in next(c for c in review["checks"] if c["key"] == "followup")["items"]}
    fu = graph.start_followup_run(db, A, people[Role.APPROVER], utcnow() + timedelta(days=2))
    sent = _mails(db, "DATA_REQUEST", A.id)
    return {
        "role": "Approver · Stage 3 agents", "title": "Before approving, the approver asks students for what is missing",
        "explain": "The Approval Review Agent lists who could still qualify: Diya (no CGPA on record), Eshan (no backlog count) "
                   "and Charan (no consent answer). The approver clicks one button; the Form Builder Agent makes each of them a "
                   "personal form with exactly those questions and a closing time, and the Follow-up Mailer emails the links.",
        "blocks": [{"type": "kv", "items": [[r, "Missing: " + ", ".join(f)] for r, f in follow.items()]},
                   {"type": "heading", "text": "How the agents sent the forms"}, _flow(fu),
                   {"type": "heading", "text": "The email Diya received"}, _email(next(m for m in sent if m.to_address.startswith("22cse003")))],
        "checks": [_check("Who needs a form", {"22CSE003": ["cgpa"], "22IT001": ["consent"], "22IT002": ["active_backlogs"]}, follow),
                   _check("Forms emailed before approval", ["22CSE003", "22IT001", "22IT002"],
                          sorted(m.to_address.split("@")[0].upper() for m in sent))],
    }


def step_students_reply(t: Tour) -> dict[str, Any]:
    db = t.sb.db
    A = db.get(Drive, t.state["A"])
    outreach.submit_data(db, _form(db, A, "22CSE003", RequestKind.DATA_REQUEST), {"cgpa": "8.4"})
    outreach.submit_data(db, _form(db, A, "22IT001", RequestKind.DATA_REQUEST), {"consent": "yes"})
    db.commit()
    old = approvals.get_approval(db, t.state["first_approval"]).status
    diya = db.scalar(select(Student).where(Student.roll_number == "22CSE003"))
    refreshed = roster_refresh.refresh_if_qualifies(db, A, diya, "Diya answered her form")
    run = graph.get_run(db, refreshed["run_id"])
    final = approvals.get_approval(db, run.state["approval_id"])
    t.state["final_run"] = run.id
    return {
        "role": "Students · Response Reader and Re-check agents", "title": "Two students reply; the list updates itself",
        "explain": "Diya enters her CGPA (8.4) and Charan says YES. The Response Reader Agent checks the values and updates their "
                   "records (marked self-reported). The Eligibility Re-check Agent finds both now qualify, so the old list is "
                   "cancelled and the Roster Agent prepares a new one. Eshan never replies, so he stays out.",
        "blocks": [{"type": "heading", "text": "How the agents rebuilt the list"}, _flow(run)],
        "checks": [_check("Old list cancelled because the data changed", "INVALIDATED", old),
                   _check("New list adds Diya and Charan", ["22CSE001", "22CSE003", "22CSE005", "22CSE007", "22IT001"],
                          final.proposal.included_roll_numbers)],
    }


def step_approve(t: Tour) -> dict[str, Any]:
    db, people = t.sb.db, t.state["people"]
    run = graph.get_run(db, t.state["final_run"])
    final = approvals.get_approval(db, run.state["approval_id"])
    blocked = []
    for role in (Role.ADMIN, Role.OFFICER):
        try:
            approvals.approve(db, final.id, people[role], expected_hash=final.proposal_hash)
            blocked.append(False)
        except Exception:  # noqa: BLE001 - PermissionDenied is the expected outcome
            db.rollback()
            blocked.append(True)
    done = test_lab._approve_and_execute(t.sb, run, people[Role.APPROVER])
    A = db.get(Drive, t.state["A"])
    company = _mails(db, "EMPLOYER_ROSTER", A.id)
    short = _mails(db, "PROFILE_SUBMITTED", A.id)
    nots = _mails(db, "NOT_SHORTLISTED", A.id)
    return {
        "role": "Approver · Upload & Verification and Employer Update agents", "title": "The approver approves; the company gets the list",
        "explain": "Only the approver can approve (the admin, the officer and every agent are refused). After approval the Upload & "
                   "Verification Agent submits exactly the approved students, reads the list back from the company's form and "
                   "checks every roll number. Then the Employer Update Agent emails the company the list from the placement "
                   "mailbox, tells each listed student they are shortlisted, and tells students who said YES but did not "
                   "qualify why.",
        "blocks": [{"type": "heading", "text": "How the agents finished"}, _flow(done),
                   {"type": "heading", "text": "Email to the company"}, _email(company[0]),
                   {"type": "heading", "text": "One shortlisted student's email"}, _email(short[0]),
                   {"type": "heading", "text": "One not-shortlisted student's email"}, _email(nots[0])],
        "checks": [_check("Admin cannot approve", True, blocked[0]), _check("Placement officer cannot approve", True, blocked[1]),
                   _check("Sent and verified by read-back", RunStatus.VERIFIED, done.status),
                   _check("Read back from the company's form = the approved list", sorted(final.proposal.included_roll_numbers),
                          sorted(test_lab._portal_roster(t.sb, A.destination.form_ref))),
                   _check("List emailed to the company", ["companytestmailone@gmail.com"], [m.to_address for m in company]),
                   _check("Shortlisted emails", 5, len(short)),
                   _check("Not-shortlisted emails, each with the reason", 6, len(nots))],
    }


def step_clash(t: Tour) -> dict[str, Any]:
    from app.services import drives as drive_svc

    db, people = t.sb.db, t.state["people"]
    B = db.get(Drive, t.state["B"])
    ishaan = db.scalar(select(Student).where(Student.roll_number == "22AIDS001"))
    exc = db.scalar(select(ExceptionItem).where(ExceptionItem.kind == ExceptionKind.SCHEDULE_CONFLICT,
                                                ExceptionItem.student_id == ishaan.id))
    drive_svc.resolve_conflict(db, exc.id, B.id, "Ishaan prefers Orion", people[Role.OFFICER])
    db.commit()
    run_b = graph.get_run(db, t.state["run_b"])
    approval_b = approvals.get_approval(db, run_b.state["approval_id"])
    if approval_b.status != "PENDING":  # the decision changed the list: prepare it again
        run_b = graph.start_run(db, f"Prepare the eligible roster for drive #{B.id}", people[Role.OFFICER])
    done = test_lab._approve_and_execute(t.sb, run_b, people[Role.APPROVER])
    company = _mails(db, "EMPLOYER_ROSTER", B.id)
    return {
        "role": "Placement officer → Approver", "title": "The schedule clash is settled and Orion's list is sent",
        "explain": "Ishaan said YES to two drives at the same time. The Conflict Agent held him instead of guessing. The officer "
                   "records that Ishaan will attend Orion; the approver approves Orion's list, which goes to Orion's mailbox.",
        "blocks": [{"type": "heading", "text": "Email to Orion Analytics"}, _email(company[0])],
        "checks": [_check("Clash resolved by a person, not an agent", "PREFER_DRIVE", db.get(ExceptionItem, exc.id).resolution),
                   _check("Orion list sent and verified", RunStatus.VERIFIED, done.status),
                   _check("Ishaan is on Orion's list (read back from its form)", True,
                          "22AIDS001" in test_lab._portal_roster(t.sb, B.destination.form_ref)),
                   _check("List emailed to Orion", ["companytestmailtwo@gmail.com"], [m.to_address for m in company])],
    }


CATEGORY = {"DRIVE_INVITATION": "Consent forms", "REMINDER": "Reminders", "DATA_REQUEST": "Personal forms (missing details)",
            "EMPLOYER_ROSTER": "Lists sent to companies", "PROFILE_SUBMITTED": "Shortlisted", "NOT_SHORTLISTED": "Not shortlisted (with reason)"}


ACTION_WORDS = {"EMAIL_DELIVERED_LOCAL": "Email written (kept in the app: the walkthrough sends no real email)",
                "VERIFICATION_VERIFIED": "Upload read back and verified", "AGENT_RUN_VERIFIED": "Task finished and verified",
                "ROSTER_REFRESH_REQUESTED": "List re-prepared after new data"}


def step_records(t: Tour) -> dict[str, Any]:
    from app.audit import ledger
    from app.models.entities import AuditEvent

    db = t.sb.db
    A, B = db.get(Drive, t.state["A"]), db.get(Drive, t.state["B"])
    counts: dict[str, int] = {}
    for m in db.scalars(select(EmailMessage)):
        counts[m.category] = counts.get(m.category, 0) + 1
    chain = ledger.verify_chain(db)
    names = {Role.ADMIN: "Admin", Role.OFFICER: "Placement officer", Role.APPROVER: "Approver",
             Role.COORDINATOR: "Drive upload officer", Role.VIEWER: "Viewer"}
    role_of = {f"user:{u.id}": names.get(r, str(r)) for r, u in t.state["people"].items()}

    def who(actor: str) -> str:
        person = next((name for uid, name in role_of.items() if actor == uid or actor.endswith(" " + uid)), None)
        if actor.startswith("agent:"):
            return "Agent" + (f" (for the {person})" if person else "")
        return person or actor.split(":")[0].title()

    events, seen = [], set()
    for e in db.scalars(select(AuditEvent).order_by(AuditEvent.seq.desc())):
        if e.action not in seen:
            seen.add(e.action)
            events.append(e)
        if len(events) == 12:
            break
    lists = {d.employer.name: test_lab._portal_roster(t.sb, d.destination.form_ref) for d in (A, B)}
    return {
        "role": "Records", "title": "Everything is on record: emails, audit log and the dashboard",
        "explain": "Nothing happens off the record. Every email an agent wrote is kept in Emails sent, every action (by a "
                   "person or an agent) is written to an audit log where each entry is chained to the one before it with a "
                   "hash, so any change to history is detected, and the dashboard shows how many students reached each company.",
        "blocks": [{"type": "heading", "text": "Emails sent (the Emails sent page)"},
                   {"type": "table", "columns": ["Kind of email", "How many"],
                    "rows": [[CATEGORY.get(k, k), str(v)] for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]},
                   {"type": "heading", "text": "Shortlisted per company (the dashboard)"},
                   {"type": "table", "columns": ["Company", "Students on the verified list"],
                    "rows": [[name, f"{len(rolls)}: {', '.join(rolls)}"] for name, rolls in lists.items()]},
                   {"type": "heading", "text": f"Audit log: {chain['checked']} entries; the latest of each kind (the Audit log page)"},
                   {"type": "table", "columns": ["#", "Who", "What"],
                    "rows": [[str(e.seq), who(e.actor_id), ACTION_WORDS.get(e.action, e.action.replace("_", " ").capitalize())]
                             for e in events]}],
        "checks": [_check("Audit log chain intact (no entry changed or removed)", True, chain["valid"]),
                   _check("Every company list is on record as an email", 2, counts.get("EMPLOYER_ROSTER", 0)),
                   _check("Lists that reached the companies", {A.employer.name: 5, B.employer.name: 3},
                          {k: len(v) for k, v in lists.items()})],
    }


def step_test_lab(t: Tour) -> dict[str, Any]:
    rows, checks = [], []
    for test_id, (title, _desc, fn) in test_lab.SCENARIOS.items():
        started = time.perf_counter()
        try:
            with Sandbox() as sb:
                result = fn(sb)
            items = result["checks"].items
            ok = result["checks"].passed
        except Exception as exc:  # noqa: BLE001 - a crash is shown as a failure
            items, ok = [], False
            title = f"{title} (error: {type(exc).__name__})"
        rows.append([test_id, title, f"{sum(1 for i in items if i['passed'])}/{len(items)}",
                     f"{int((time.perf_counter() - started) * 1000)} ms", "PASS" if ok else "FAIL"])
        checks.append(_check(f"{test_id} {title}", True, ok))
    return {
        "role": "Proof", "title": "All test scenarios, run again right now",
        "explain": "The four official hackathon scenarios (TC1–TC4) and the two institute scenarios (TC5 company emails, TC6 every "
                   "student's journey) were just executed on the real code, each in its own fresh sandbox. The same scenarios "
                   "can be re-run any time from the Test Lab.",
        "blocks": [{"type": "table", "columns": ["Test", "Scenario", "Checks passed", "Time", "Result"], "rows": rows}],
        "checks": checks,
    }


STEPS: list[tuple[str, Callable[[Tour], dict[str, Any]]]] = [
    ("setup", step_setup), ("company_emails", step_company_emails), ("bad_emails", step_bad_emails),
    ("students_answer", step_students_answer), ("officer_runs", step_officer_runs), ("ask", step_ask), ("approver_forms", step_approver_forms),
    ("students_reply", step_students_reply), ("approve", step_approve), ("clash", step_clash), ("records", step_records),
    ("test_lab", step_test_lab),
]
OUTLINE = ["Institute data", "Companies email the mailbox", "Problem emails stopped", "Students answer; reminders",
           "Officer runs the drives", "Ask the crew", "Approver sends forms", "Students reply; list updates", "Approval and company email",
           "Schedule clash settled", "Emails, audit log, dashboard", "All test scenarios"]


# --------------------------------------------------------------------------- the guide shown around the steps

# Every feature of the app, where evaluators see it in the walkthrough and where it lives in the app.
FEATURES: list[dict[str, str]] = [
    {"id": "roles", "group": "People", "name": "Four roles, each with one job",
     "what": "Drive upload officer, placement officer, approver and admin. Every button checks the role.",
     "where": "Login page (click a role to fill it in)"},
    {"id": "mailbox", "group": "Stage 1 · company email → drive", "name": "Reads the placement Gmail",
     "what": "The Mailbox Agent reads new company emails from the placement Gmail (Google sign-in, read-only), only from the time window set in Settings.",
     "where": "Company emails → Check mailbox now · Settings → Gmail"},
    {"id": "ai_reading", "group": "Stage 1 · company email → drive", "name": "Gemini reads each email; rules double-check it",
     "what": "The Email Reader Agent pulls out company, role, date, time, venue, deadline and criteria with Gemini function calling; rules read it too and every field is compared.",
     "where": "Company emails → open an email (\"Read by Gemini, checked by rules\")"},
    {"id": "email_checks", "group": "Stage 1 · company email → drive", "name": "Stops bad company emails",
     "what": "Duplicates, a missing deadline, a deadline after the drive, unknown senders and instruction-like text are stopped for a person.",
     "where": "Company emails (status of each email)"},
    {"id": "overlap", "group": "Stage 1 · company email → drive", "name": "Spots overlapping drives",
     "what": "Drives at the same time are flagged as soon as the email arrives (time-zone aware).",
     "where": "Company emails · Issues"},
    {"id": "drives", "group": "Stage 1 · company email → drive", "name": "Creates drives (or a person adds one)",
     "what": "The Drive Builder creates the drive with versioned criteria. Staff can also add a drive with the New drive form; it goes through the same checks.",
     "where": "Drives · Drives → New drive"},
    {"id": "consent", "group": "Stage 1 · company email → drive", "name": "A consent form for every student",
     "what": "Each student gets a personal YES/NO link by email, sent from the placement Gmail.",
     "where": "Emails sent · the student's inbox"},
    {"id": "reminders", "group": "Stage 1 · company email → drive", "name": "Reminds students who haven't answered",
     "what": "The Reminder Agent emails only students who have not answered, before the form closes (time set in Settings).",
     "where": "Settings → Agent rules · Agents → Run background agents now"},
    {"id": "eligibility", "group": "Stage 2 · run the drive", "name": "Exact eligibility rules",
     "what": "CGPA, backlogs, branch and batch. 7.00 passes and 6.99 fails; missing data is UNKNOWN, never guessed.",
     "where": "Drives → a drive → Prepare the student list → Who qualifies"},
    {"id": "duplicates", "group": "Stage 2 · run the drive", "name": "Never mixes up students with the same name",
     "what": "Students are matched by roll number everywhere, so two Rahul Kumars stay apart.",
     "where": "Students · Who qualifies"},
    {"id": "consent_only", "group": "Stage 2 · run the drive", "name": "Shares only students who said YES",
     "what": "No consent, no sharing: not in the list, not in the upload, not in the company email.",
     "where": "Who qualifies"},
    {"id": "clash", "group": "Stage 2 · run the drive", "name": "Clashes are held for a person",
     "what": "A student booked on two overlapping drives is held, never guessed; a person picks the drive.",
     "where": "Issues → Schedule clashes → Resolve"},
    {"id": "agent_chain", "group": "Agents", "name": "Every hand-over between agents is shown",
     "what": "Each task shows one card per agent with arrows, and a person card wherever a human decides.",
     "where": "Drive pages · Agents · every task"},
    {"id": "ask", "group": "Agents", "name": "Ask PlaceCrew (chat agent)",
     "what": "Ask in plain words. Gemini picks its own read-only tools, answers from live data with the real reasons, runs what-ifs without changing anything, and only proposes actions.",
     "where": "Ask PlaceCrew button (bottom right, every page)"},
    {"id": "review", "group": "Stage 3 · approve and send", "name": "Approval review, summarised by Gemini",
     "what": "Before approving, the Approval Review Agent re-checks every student and lists who could still qualify.",
     "where": "Approvals → open a list"},
    {"id": "followup", "group": "Stage 3 · approve and send", "name": "Personal forms for missing details",
     "what": "One click emails each student a form asking only for what is missing, with a closing time.",
     "where": "Approvals → Email N student(s) a personal form"},
    {"id": "recheck", "group": "Stage 3 · approve and send", "name": "The list updates itself",
     "what": "When a student replies, they are re-checked; the old list is cancelled and a new one prepared.",
     "where": "Approvals"},
    {"id": "approval", "group": "Stage 3 · approve and send", "name": "Only the approver can approve",
     "what": "No agent has an approve tool; the admin and officer are refused. An approval is bound to a hash of the exact list.",
     "where": "Approvals → Approve upload"},
    {"id": "verify", "group": "Stage 3 · approve and send", "name": "Upload checked by reading it back",
     "what": "The approved list is submitted, read back from the company's form and compared roll number by roll number.",
     "where": "Drive page → upload status"},
    {"id": "results", "group": "Stage 3 · approve and send", "name": "Company and students are told",
     "what": "The company gets the verified list by email; students get shortlisted or not-shortlisted emails with the reason.",
     "where": "Emails sent · the inboxes"},
    {"id": "emails_log", "group": "Records", "name": "Every email kept",
     "what": "Every email the agents wrote is listed with its status and can be opened.", "where": "Emails sent"},
    {"id": "audit", "group": "Records", "name": "Tamper-proof audit log",
     "what": "Every action by a person or an agent, hash-chained so any change is detected.", "where": "Audit log"},
    {"id": "dashboard", "group": "Records", "name": "Dashboard",
     "what": "Students, upcoming drives, consent answers, your to-do list, shortlisted per company, agents at work.", "where": "Home"},
    {"id": "test_lab", "group": "Proof", "name": "Test Lab",
     "what": "Re-runs every scenario on the real code in a sandbox and explains why each check passed; evidence CSV.",
     "where": "Test Lab → Run all scenarios → Details"},
    {"id": "settings", "group": "Setup", "name": "Settings",
     "what": "Gmail connection, which company emails to read, trusted senders, agent rules and reminder times, Test the AI, demo reset.",
     "where": "Settings (admin)"},
]

STEP_INFO: dict[str, dict[str, Any]] = {
    "setup": {"why": "Each test student stands for one case, so every rule in the system is exercised.",
              "in_app": "Students page · the login page lists the roles", "features": ["roles"], "proves": []},
    "company_emails": {"why": "Today someone copies every company email into a sheet and mails students by hand.",
                       "in_app": "Company emails → Check mailbox now (drive upload officer)",
                       "features": ["mailbox", "ai_reading", "drives", "overlap", "consent", "agent_chain"], "proves": ["TC4", "TC5"]},
    "bad_emails": {"why": "A wrong deadline or a fake sender would otherwise reach every student.",
                   "in_app": "Company emails (status of each email)", "features": ["email_checks"], "proves": ["TC5"]},
    "students_answer": {"why": "Sharing a student's details without consent is the most serious mistake a placement cell can make.",
                        "in_app": "The student's personal link · Emails sent", "features": ["consent", "reminders"], "proves": ["TC3"]},
    "officer_runs": {"why": "Checking CGPA, backlogs and branches in a spreadsheet is slow and error-prone.",
                     "in_app": "Drives → open a drive → Prepare the student list → Who qualifies",
                     "features": ["eligibility", "duplicates", "consent_only", "clash", "agent_chain"],
                     "proves": ["TC1", "TC2", "TC3", "TC4"]},
    "ask": {"why": "Staff get answers in seconds instead of digging through pages.",
            "in_app": "Ask PlaceCrew button (bottom right of every page)", "features": ["ask"], "proves": []},
    "approver_forms": {"why": "Good students are often dropped only because one detail is missing.",
                       "in_app": "Approvals → open the list → Email N student(s) a personal form",
                       "features": ["review", "followup", "agent_chain"], "proves": ["TC6"]},
    "students_reply": {"why": "The approver must always approve exactly what is true now.",
                       "in_app": "Approvals (the new list appears by itself)", "features": ["recheck", "agent_chain"], "proves": ["TC6"]},
    "approve": {"why": "A wrong list sent to a company cannot be taken back.",
                "in_app": "Approvals → Approve upload → Approve and send",
                "features": ["approval", "verify", "results", "agent_chain"], "proves": ["TC1", "TC3", "TC6"]},
    "clash": {"why": "A student cannot attend two interviews at once; only a person should choose.",
              "in_app": "Issues → Schedule clashes → Resolve", "features": ["clash", "approval", "results"], "proves": ["TC4"]},
    "records": {"why": "Placement decisions affect careers; every step must be traceable.",
                "in_app": "Emails sent · Audit log · Home", "features": ["emails_log", "audit", "dashboard"], "proves": []},
    "test_lab": {"why": "Proof, not promises: every scenario runs again on the real code.",
                 "in_app": "Test Lab → Run all scenarios → Details", "features": ["test_lab"],
                 "proves": ["TC1", "TC2", "TC3", "TC4", "TC5", "TC6"]},
}

TEST_CASES = [
    {"id": "TC1", "name": "CGPA cutoff and backlogs", "what": "7.00 passes, 6.99 fails, any backlog fails, missing data is UNKNOWN"},
    {"id": "TC2", "name": "Duplicate names", "what": "Two students called Rahul Kumar never mixed up"},
    {"id": "TC3", "name": "Missing consent", "what": "No YES means never shared, even after approval"},
    {"id": "TC4", "name": "Conflicting drives", "what": "Overlapping drives caught; a person decides"},
    {"id": "TC5", "name": "Company emails", "what": "Bad emails stopped, good ones become drives"},
    {"id": "TC6", "name": "Every student's journey", "what": "Missing details asked before approval; late qualifiers added"},
]

ROLES = [
    {"role": "Drive upload officer", "login": "driveuploadtesting@gmail.com", "job": "Company email → drive → consent forms (stage 1)"},
    {"role": "Placement officer", "login": "officer@placementos.demo", "job": "Runs the drive and settles clashes (stage 2)"},
    {"role": "Approver", "login": "approver@placementos.demo", "job": "Asks for missing details and approves (stage 3)"},
    {"role": "Admin", "login": "admin@placementos.demo", "job": "Setup, Test Lab, audit log. Cannot approve."},
]

BUILT_WITH = [
    ["Agents", "21 named agents, each allowed only its own tools; a durable agent graph that pauses for people"],
    ["AI", "Gemini with function calling (email reading, the planner, Ask PlaceCrew, review summaries), always checked by rules"],
    ["Email", "Gmail API with Google sign-in (read + send), SMTP as a backup"],
    ["Safety", "Hash-bound approvals, read-back verification, hash-chained audit log, email text treated as data"],
    ["Backend", "FastAPI, SQLAlchemy, PostgreSQL"],
    ["Frontend", "Next.js, Tailwind"],
    ["Tests", "pytest and Playwright, plus the Test Lab (walkthrough step 12)"],
]


# --------------------------------------------------------------------------- API helpers

def start() -> dict[str, Any]:
    steps_of = {f["id"]: [i + 1 for i, (key, _fn) in enumerate(STEPS) if f["id"] in STEP_INFO[key]["features"]]
                for f in FEATURES}
    return {"outline": OUTLINE, "total": len(STEPS), "agents": crew.describe(),
            "features": [{**f, "steps": steps_of[f["id"]]} for f in FEATURES],
            "test_cases": [{**tc, "steps": [i + 1 for i, (key, _fn) in enumerate(STEPS) if tc["id"] in STEP_INFO[key]["proves"]]}
                           for tc in TEST_CASES],
            "roles": ROLES, "built_with": BUILT_WITH}


def run_step(index: int) -> dict[str, Any]:
    """Run step `index` on a fresh sandbox: the earlier steps are replayed first (same data, same results),
    then this step runs and its output is returned."""
    if not 0 <= index < len(STEPS):
        raise ValueError("No such step")
    tour = Tour()
    local = mailer.LOCAL_ONLY.set(True)
    tour.sb.activate()
    started = time.perf_counter()
    try:
        for _key, fn in STEPS[:index]:
            fn(tour)
        started = time.perf_counter()
        out = STEPS[index][1](tour)
    except Exception as exc:  # noqa: BLE001 - shown to the evaluator as a failed step, never hidden
        tour.sb.db.rollback()
        out = {"role": "", "title": OUTLINE[index], "explain": "This step failed.", "blocks": [],
               "checks": [_check("Step completed", True, False)], "error": f"{type(exc).__name__}: {exc}"}
    finally:
        tour.sb.deactivate()
        mailer.LOCAL_ONLY.reset(local)
        tour.sb.close()
    out.update(STEP_INFO[STEPS[index][0]])
    out.update({"index": index, "key": STEPS[index][0], "ms": int((time.perf_counter() - started) * 1000),
                "passed": all(c["passed"] for c in out["checks"]), "finished": index + 1 >= len(STEPS)})
    return out

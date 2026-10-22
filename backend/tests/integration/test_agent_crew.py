"""The agent crew end to end: company email -> drive -> consent forms -> roster -> approver follow-ups.

External services are replaced at their boundary only: Gemini by a fake client with the same
`generate()` method, Google/Gmail by an httpx MockTransport speaking the documented REST shapes.
"""
import base64
import email
import email.policy
import json
import re
from datetime import timedelta
from email.message import EmailMessage as Mime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.agent import crew, gemini, graph
from app.agent.tools import ToolContext, ToolFailure, call_tool
from app.core.config import get_settings
from app.core.errors import PermissionDenied, PreconditionFailed, ValidationFailed
from app.db.base import utcnow
from app.integrations import gmail
from app.models.entities import (
    AgentEvent, ConsentForm, Drive, DriveAssignment, EmailMessage, Employer, ExceptionItem, InboundEmail,
    OAuthCredential, StudentRequest,
)
from app.models.enums import InboundSource, RequestKind, Role, RunStatus
from app.services import app_settings, approvals, autopilot, consent_forms, intake, outreach, reminders, roster_refresh
from app.services.sandbox import ist


@pytest.fixture
def settings_override():
    s = get_settings()
    saved = {}

    def apply(**values):
        for k, v in values.items():
            saved.setdefault(k, getattr(s, k))
            object.__setattr__(s, k, v)

    yield apply
    for k, v in saved.items():
        object.__setattr__(s, k, v)


@pytest.fixture
def crew_people(sb):
    return {"coordinator": sb.user(Role.COORDINATOR), "officer": sb.user(Role.OFFICER),
            "approver": sb.user(Role.APPROVER), "viewer": sb.user(Role.VIEWER)}


@pytest.fixture
def students(sb):
    return [sb.student("A001", "Asha Rao", "CSE", 8.0, 0, consent=None),
            sb.student("A002", "Bala Iyer", "IT", 7.9, 0, consent=None),
            sb.student("A003", "Chitra Das", "ECE", 9.0, 0, consent=None),
            sb.student("A004", "Dev Mehta", "CSE", None, 0, consent=None)]


def demo(i):
    return intake.demo_emails("Asia/Kolkata")[i]


def receive(sb, mail, **extra):
    row, _ = intake.ingest(sb.db, source=InboundSource.MANUAL, **{**mail, **extra})
    sb.db.commit()
    return row


def emails(sb, category):
    return list(sb.db.scalars(select(EmailMessage).where(EmailMessage.category == category).order_by(EmailMessage.id)))


def node_agents(sb, run):
    return [e.payload.get("agent") for e in sb.db.scalars(select(AgentEvent).where(
        AgentEvent.run_id == run.id, AgentEvent.event_type == "NODE_START").order_by(AgentEvent.seq))]


# --------------------------------------------------------------------------- stage 1

def test_partner_email_becomes_a_drive_and_every_student_gets_one_consent_form(sb, crew_people, students):
    sb.db.add(Employer(name="Orbitra Technologies", contact_email="campus@orbitra.example"))
    sb.db.commit()
    row = receive(sb, demo(0))
    run = graph.start_intake_run(sb.db, row, crew_people["coordinator"])

    assert run.status == RunStatus.COMPLETED, run.pending_question
    assert node_agents(sb, run) == ["email_reader", "criteria_check", "drive_builder", "consent_form", "outreach"]
    sb.db.refresh(row)
    assert row.status == "PUBLISHED" and row.drive_id == run.drive_id
    drive = sb.db.get(Drive, run.drive_id)
    crit = drive.criteria_versions[-1]
    assert (crit.min_cgpa, crit.backlog_policy, crit.allowed_branches) == (7.5, "NO_ACTIVE_BACKLOGS", ["AIDS", "CSE", "IT"])
    assert crit.required_consent_scope == "DRIVE_REGISTRATION" and crit.graduation_years == [2026]
    assert drive.registration_deadline and drive.roster_due_at and drive.registration_deadline < drive.starts_at
    assert drive.destination is not None and "LOCAL MOCK" in drive.destination.name

    form = consent_forms.get_form(sb.db, drive.id)
    assert form.audience == "ALL_STUDENTS" and form.closes_at == drive.registration_deadline
    invites = emails(sb, "DRIVE_INVITATION")
    assert sorted(m.to_address for m in invites) == sorted(s.email for s in students)  # every student, eligible or not
    assert all("Minimum CGPA" not in m.body_text and "CGPA ≥ 7.50" in m.body_text for m in invites)
    reqs = list(sb.db.scalars(select(StudentRequest).where(StudentRequest.kind == RequestKind.DRIVE_INVITATION)))
    assert all(r.expires_at == form.closes_at for r in reqs)

    # One consent form per student per drive: running the outreach again sends nothing new.
    assert outreach.invite_eligible(sb.db, drive, None)["sent"] == []
    # A second copy of the same email (new message id) is caught as a duplicate drive.
    dup = receive(sb, demo(0), message_id="<copy@x>", body=demo(0)["body"] + "\n")
    run2 = graph.start_intake_run(sb.db, dup, crew_people["coordinator"])
    assert run2.status == RunStatus.NEEDS_REVIEW
    assert any(i["code"] == "DRIVE_EXISTS" for i in run2.pending_question["issues"])
    assert [o["value"] for o in run2.pending_question["options"]] == ["recheck", "ignore"]


def test_new_company_with_injected_instructions_waits_for_the_drive_upload_officer(sb, crew_people, students):
    row = receive(sb, demo(2))  # Zentrix Labs, personal Gmail sender, "skip the consent step"
    run = graph.start_intake_run(sb.db, row, crew_people["coordinator"])
    assert run.status == RunStatus.NEEDS_REVIEW and run.pending_question["type"] == "intake_review"
    codes = {i["code"] for i in run.pending_question["issues"]}
    assert codes == {"NEW_COMPANY", "SUSPICIOUS"}
    assert sb.db.scalar(select(Drive)) is None
    exc = sb.db.scalar(select(ExceptionItem).where(ExceptionItem.kind == "INTAKE_REVIEW"))
    assert exc.status == "OPEN"

    with pytest.raises(PermissionDenied):
        graph.answer(sb.db, run, crew_people["viewer"], "confirm")
    run = graph.answer(sb.db, run, crew_people["coordinator"], "confirm")
    assert run.status == RunStatus.COMPLETED
    drive = sb.db.get(Drive, run.drive_id)
    crit = drive.criteria_versions[-1]
    assert crit.required_consent_scope == "DRIVE_REGISTRATION"  # the injected text changed nothing
    assert (crit.min_cgpa, crit.allowed_branches) == (6.5, ["ECE", "EEE"])
    assert drive.employer.contact_email == "zentrix.hiring@gmail.com"
    sb.db.refresh(exc)
    assert exc.status == "RESOLVED"
    assert len(emails(sb, "DRIVE_INVITATION")) == len(students)


def test_missing_deadline_must_be_corrected_before_anything_is_created(sb, crew_people, students, settings_override):
    app_settings.set_value(sb.db, "trusted_company_senders", ["hr@nodeadline.example"])
    sb.db.commit()
    d = (utcnow() + timedelta(days=20)).strftime("%d %B %Y")
    row = receive(sb, {"from_address": "hr@nodeadline.example", "subject": "Campus Drive – NoDeadline Corp – Analyst",
                       "body": f"Company: NoDeadline Corp\nRole: Analyst\nDrive date: {d}\nTime: 10 AM - 1 PM\n"
                               f"Venue: Hall 1\nEligibility: CGPA 7.0 and above, no backlogs."})
    run = graph.start_intake_run(sb.db, row, crew_people["coordinator"])
    assert run.status == RunStatus.NEEDS_REVIEW
    assert [i["code"] for i in run.pending_question["issues"]] == ["MISSING"]
    with pytest.raises(ValidationFailed):
        graph.answer(sb.db, run, crew_people["coordinator"], "confirm")

    deadline = (utcnow() + timedelta(days=10)).strftime("%Y-%m-%dT17:00")
    intake.update_details(sb.db, row, {"registration_deadline": deadline}, crew_people["coordinator"])
    sb.db.commit()
    run = graph.answer(sb.db, run, crew_people["coordinator"], "recheck")
    assert run.status == RunStatus.COMPLETED
    drive = sb.db.get(Drive, run.drive_id)
    assert drive.criteria_versions[-1].min_cgpa == 7.0
    assert any(i["code"] == "TRUSTED" for i in intake.get(sb.db, row.id).issues)


def test_permissions_by_role(sb, crew_people, students):
    row = receive(sb, demo(1))
    with pytest.raises(PermissionDenied):
        graph.start_intake_run(sb.db, row, crew_people["approver"])
    d = sb.drive("Perm Drive", ist(2026, 12, 5, 10), ist(2026, 12, 5, 12))
    with pytest.raises(PermissionDenied):
        graph.start_followup_run(sb.db, d, crew_people["coordinator"], utcnow() + timedelta(days=2))


# --------------------------------------------------------------------------- AI reader (Gemini) and planner

class FakeGemini:
    model = "fake-gemini"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def generate(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs)))
        return self.replies.pop(0)


def call(name, args, extra=None):
    return {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": name, "args": args},
                                                                    **(extra or {})}]},
                            "finishReason": "STOP"}]}


def test_gemini_email_reader_is_cross_checked_against_the_rules(sb, crew_people, students):
    sb.db.add(Employer(name="Kestrel Finance", contact_email="careers@kestrelfinance.example"))
    sb.db.commit()
    rules = intake.email_intake.extract(demo(1)["subject"], demo(1)["body"])
    fake = FakeGemini([call("submit_drive_details", {
        "company": "Kestrel Finance", "role": "Graduate Analyst", "drive_date": rules.drive_date, "start_time": "14:00",
        "end_time": "17:00", "venue": "Seminar Hall A", "registration_deadline": rules.registration_deadline,
        "roster_due": rules.roster_due, "ctc": None, "graduation_years": [], "min_cgpa": 8.0,  # disagrees with "7.0"
        "backlog_policy": "MAX_ALLOWED", "max_active_backlogs": 1, "allowed_branches": [], "unclear_points": []})])
    row = receive(sb, demo(1))
    run = graph.start_intake_run(sb.db, row, crew_people["coordinator"], llm_client=fake)

    req = fake.calls[0]
    assert req["mode"] == "ANY" and req["allowed"] == ["submit_drive_details"]
    params = req["declarations"][0]["parameters"]
    assert params["type"] == "OBJECT" and params["properties"]["min_cgpa"] == {"type": "NUMBER", "nullable": True}
    assert "<email>" in req["contents"][0]["parts"][0]["text"]
    assert run.planner_mode == "LLM" and intake.get(sb.db, row.id).extraction["mode"] == "AI"
    assert run.status == RunStatus.NEEDS_REVIEW
    assert any("disagree on minimum CGPA: 8.0 vs 7.0" in i["message"] for i in run.pending_question["issues"])


def test_gemini_failure_falls_back_to_rules_and_is_labelled(sb, crew_people, students):
    class Broken:
        def generate(self, **_):
            raise gemini.GeminiError("Gemini API returned HTTP 404: model not found")

    sb.db.add(Employer(name="Kestrel Finance", contact_email="careers@kestrelfinance.example"))
    sb.db.commit()
    run = graph.start_intake_run(sb.db, receive(sb, demo(1)), crew_people["coordinator"], llm_client=Broken())
    assert run.status == RunStatus.COMPLETED and run.planner_mode == "LLM_FAILED_DETERMINISTIC_FALLBACK"
    assert sb.db.scalar(select(AgentEvent).where(AgentEvent.run_id == run.id, AgentEvent.event_type == "LLM_ERROR"))


def test_gemini_planner_uses_function_calling_and_returns_signatures(sb, people, basic_drive):
    fake = FakeGemini([
        call("list_drives", {}, {"thoughtSignature": "c2lnLTE="}),
        call("submit_plan", {"intent": "CHECK_CONFLICTS", "drive_id": basic_drive.id, "rationale": "named",
                             "clarification_question": None, "candidate_drive_ids": [],
                             "simulation": {"min_cgpa": None, "max_active_backlogs": None, "allowed_branches": None,
                                            "shift_minutes": None}}),
    ])
    run = graph.start_run(sb.db, "Check clashes for Basic Drive", people["officer"], llm_client=fake)
    assert run.planner_mode == "LLM" and run.intent == "CHECK_CONFLICTS" and run.status == RunStatus.COMPLETED
    second = fake.calls[1]["contents"]
    assert second[1]["parts"][0]["thoughtSignature"] == "c2lnLTE="  # model turn returned unchanged
    assert second[2]["role"] == "function" and "functionResponse" in second[2]["parts"][0]
    names = {d["name"] for d in fake.calls[0]["declarations"]}
    assert names == {"list_drives", "get_student_roster", "get_drive_requirements", "simulate_roster_changes", "submit_plan"}


def test_gemini_http_request_shape(settings_override):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["key"] = str(request.url), request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=call("f", {"a": 1}))

    client = gemini.GeminiClient(api_key="k-123", model="gemini-test", transport=httpx.MockTransport(handler))
    out = client.generate(system="sys", contents=[{"role": "user", "parts": [{"text": "hi"}]}],
                          declarations=[gemini.declaration("f", "d", {"type": "object", "properties": {"a": {"type": "integer"}}})],
                          mode="ANY", allowed=["f"])
    assert seen["url"].endswith("/v1beta/models/gemini-test:generateContent") and "key=" not in seen["url"]
    assert seen["key"] == "k-123"
    assert seen["body"]["toolConfig"] == {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["f"]}}
    assert seen["body"]["tools"][0]["functionDeclarations"][0]["parameters"]["properties"]["a"] == {"type": "INTEGER"}
    assert gemini.function_calls(gemini.first_content(out)) == [{"name": "f", "args": {"a": 1}}]

    bad = gemini.GeminiClient(api_key="k", model="m", transport=httpx.MockTransport(
        lambda r: httpx.Response(404, json={"error": {"message": "models/m is not found"}})))
    with pytest.raises(gemini.GeminiError, match="404"):
        bad.generate(system="s", contents=[], declarations=[])


# --------------------------------------------------------------------------- Gmail (Google sign-in)

class FakeGoogle:
    """Speaks the Google OAuth token endpoint and Gmail API v1 shapes used by app.integrations.gmail."""

    def __init__(self, inbox):
        self.inbox, self.sent, self.token_requests, self.fail_refresh = inbox, [], [], False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if str(url) == gmail.TOKEN_URL:
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            if form["grant_type"] == ["refresh_token"] and self.fail_refresh:
                return httpx.Response(400, json={"error": "invalid_grant"})
            body = {"access_token": "at-1", "expires_in": 3599, "token_type": "Bearer", "scope": " ".join(gmail.SCOPES)}
            if form["grant_type"] == ["authorization_code"]:
                body["refresh_token"] = "rt-secret"
            return httpx.Response(200, json=body)
        assert request.headers["authorization"] == "Bearer at-1"
        if url.path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "driveuploadtesting@gmail.com"})
        if url.path.endswith("/messages/send"):
            self.sent.append(base64.urlsafe_b64decode(json.loads(request.content)["raw"]))
            return httpx.Response(200, json={"id": f"sent-{len(self.sent)}"})
        if url.path.endswith("/messages"):
            self.last_query = url.params["q"]
            return httpx.Response(200, json={"messages": [{"id": str(i)} for i in range(len(self.inbox))][::-1]})
        mid = int(url.path.rsplit("/", 1)[-1])
        assert url.params["format"] == "raw"
        return httpx.Response(200, json={"id": str(mid), "raw": base64.urlsafe_b64encode(self.inbox[mid]).decode().rstrip("=")})


def rfc822(sender, subject, body, mid):
    m = Mime()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, "driveuploadtesting@gmail.com", subject, mid
    m["Date"] = "Wed, 21 Oct 2026 10:00:00 +0530"
    m.set_content(body)
    return m.as_bytes()


@pytest.fixture
def google(settings_override, monkeypatch):
    settings_override(google_client_id="cid.apps.googleusercontent.com", google_client_secret="csecret",
                      public_app_url="http://localhost:3000", google_redirect_uri="")
    mail = demo(0)
    fake = FakeGoogle([rfc822(f"Priya Raman <{mail['from_address']}>", mail["subject"], mail["body"], "<m1@orbitra>"),
                       rfc822("Friend <friend@gmail.com>", "Lunch on Friday?", "Personal note", "<m2@x>")])
    monkeypatch.setattr(gmail, "TRANSPORT", httpx.MockTransport(fake))
    gmail._access_cache.clear()
    yield fake
    gmail._access_cache.clear()


def test_google_sign_in_reads_company_mail_and_sends_from_the_officer_mailbox(sb, crew_people, students, google):
    url = urlparse(gmail.authorization_url(crew_people["coordinator"].id))
    q = parse_qs(url.query)
    assert url.netloc == "accounts.google.com" and q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["scope"] == [" ".join(gmail.SCOPES)] and q["redirect_uri"] == ["http://localhost:3000/api/integrations/google/callback"]
    from app.core.security import read_state_token
    assert read_state_token(q["state"][0], "google_oauth") == crew_people["coordinator"].id

    cred = gmail.complete_sign_in(sb.db, "auth-code", crew_people["coordinator"].id)
    sb.db.commit()
    assert cred.account_email == "driveuploadtesting@gmail.com" and "rt-secret" not in cred.refresh_token_encrypted
    assert google.token_requests[0]["code"] == ["auth-code"] and google.token_requests[0]["client_secret"] == ["csecret"]

    sb.db.add(Employer(name="Orbitra Technologies", contact_email="campus@orbitra.example"))
    sb.db.commit()
    result = intake.fetch_mailbox(sb.db, None, actor="agent:mailbox")
    assert result["source"] == "GMAIL" and result["new"] == 1  # the personal email is not a "Campus Drive" email
    assert 'subject:"Campus Drive"' in google.last_query and "-from:me" in google.last_query
    assert intake.fetch_mailbox(sb.db, None)["new"] == 0  # read-only and idempotent

    processed = autopilot.process_new_emails(sb.db, crew_people["coordinator"])
    assert processed[0]["status"] == RunStatus.COMPLETED
    sent = [email.message_from_bytes(b, policy=email.policy.default) for b in google.sent]
    assert len(sent) == len(students) and all("driveuploadtesting@gmail.com" in m["From"] for m in sent)
    assert all(m.status == "SENT" and m.transport == "GMAIL_API" for m in emails(sb, "DRIVE_INVITATION"))

    google.fail_refresh = True
    gmail._access_cache.clear()
    msg = outreach.request_missing_data(sb.db, students[3], ["cgpa"], None)
    assert sb.db.get(EmailMessage, sb.db.scalar(select(EmailMessage.id).where(EmailMessage.request_id == msg.id))).status == "FAILED"
    assert sb.db.scalar(select(OAuthCredential)).status == "ERROR"


def test_autopilot_tick_runs_mailbox_deadline_and_reminder_agents(sb, crew_people, students, google, monkeypatch):
    gmail.complete_sign_in(sb.db, "auth-code", crew_people["coordinator"].id)
    sb.db.add(Employer(name="Orbitra Technologies", contact_email="campus@orbitra.example"))
    sb.db.commit()
    result = autopilot.tick(sb.db)
    assert result["mailbox"]["new"] == 1 and result["processed"][0]["status"] == RunStatus.COMPLETED
    assert sb.db.scalar(select(InboundEmail)).status == "PUBLISHED"
    assert "error" not in result["deadlines"] and "error" not in result["reminders"]


# --------------------------------------------------------------------------- stage 2 + 3

@pytest.fixture
def registration(sb, crew_people):
    """A registration-consent drive with every kind of student the approver must handle."""
    d = sb.drive("Crew Drive", ist(2026, 12, 15, 10), ist(2026, 12, 15, 13), min_cgpa=7.0)
    d.criteria_versions[-1].required_consent_scope = "DRIVE_REGISTRATION"
    d.registration_deadline = utcnow() + timedelta(days=5)
    sb.db.commit()
    people = {
        "yes": sb.student("Y001", "Yamini Yes", "CSE", 8.0, 0, consent=None),
        "missing": sb.student("M001", "Mohan Missing", "CSE", None, 0, consent=None),
        "noanswer": sb.student("N001", "Nila Noanswer", "IT", 8.5, 0, consent=None),
        "no": sb.student("D001", "Dinesh Declines", "CSE", 9.0, 0, consent=None),
        "low": sb.student("L001", "Latha Low", "CSE", 5.0, 0, consent=None),
    }
    form, _ = consent_forms.create_form(sb.db, d, "agent:consent_form", "ALL_STUDENTS")
    sb.db.commit()
    outreach.invite_eligible(sb.db, d, None, actor="agent:outreach")
    tokens = {r.student.roll_number: r.token for r in sb.db.scalars(select(StudentRequest))}
    for key, accept in (("yes", True), ("no", False), ("low", True)):
        outreach.respond_invitation(sb.db, outreach.get_by_token(sb.db, tokens[people[key].roll_number]), accept)
    # Nila's consent link lapsed without an answer
    lapsed = sb.db.scalar(select(StudentRequest).where(StudentRequest.student_id == people["noanswer"].id))
    lapsed.status = "EXPIRED"
    sb.db.commit()
    return d, people


def test_officer_run_consent_agent_and_approval_review(sb, crew_people, registration):
    d, p = registration
    run = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{d.id}", crew_people["officer"])
    assert run.status == RunStatus.AWAITING_APPROVAL
    agents = node_agents(sb, run)
    assert agents[agents.index("eligibility") + 1] == "consent" and agents[-2:] == ["roster", "review"]
    assert run.state["consent"]["eligible_with_consent"] == 1 and run.state["consent"]["eligible_declined"] == 1
    proposal = approvals.get_approval(sb.db, run.state["approval_id"]).proposal
    assert proposal.included_roll_numbers == ["Y001"]
    assert run.state["review"]["verdict"] == "READY_WITH_NOTES"

    from app.services import approval_review
    rev = approval_review.review(sb.db, approvals.get_approval(sb.db, run.state["approval_id"]))
    checks = {c["key"]: c for c in rev["checks"]}
    assert checks["hash"]["status"] == "PASS" and checks["students"]["status"] == "PASS"
    follow = {i["roll_number"]: i["fields"] for i in checks["followup"]["items"]}
    assert follow == {"M001": ["cgpa", "consent"], "N001": ["consent"]}  # Dinesh said no, Latha does not qualify
    assert checks["deadlines"]["status"] == "WARN"  # registration still open


def test_followup_forms_response_reader_recheck_and_shortlist_emails(sb, crew_people, registration):
    d, p = registration
    run = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{d.id}", crew_people["officer"])
    first_approval = run.state["approval_id"]

    closes = utcnow() + timedelta(days=2)
    fu = graph.start_followup_run(sb.db, d, crew_people["approver"], closes)
    assert fu.status == RunStatus.COMPLETED and node_agents(sb, fu) == ["form_builder", "followup_mailer"]
    forms = {r.student.roll_number: r for r in sb.db.scalars(select(StudentRequest).where(
        StudentRequest.kind == RequestKind.DATA_REQUEST))}
    assert set(forms) == {"M001", "N001"} and forms["M001"].requested_fields == ["cgpa", "consent"]
    assert abs((forms["M001"].expires_at - closes).total_seconds()) < 1
    # Re-running the Form Builder does not send duplicates.
    again = graph.start_followup_run(sb.db, d, crew_people["approver"], closes)
    assert "already have an open form" in again.summary

    with pytest.raises(ValidationFailed):
        outreach.submit_data(sb.db, forms["M001"], {"cgpa": "8.1"})  # consent answer is required too
    req = outreach.submit_data(sb.db, forms["M001"], {"cgpa": "8.1", "consent": "yes"})
    assert req.status == "APPLIED" and req.response["recheck"]["now_qualifies"] is True
    sb.db.refresh(p["missing"])
    assert p["missing"].cgpa == 8.1 and p["missing"].self_reported[0]["field"] == "cgpa"
    assert approvals.get_approval(sb.db, first_approval).status == "INVALIDATED"  # roster changed

    refreshed = roster_refresh.refresh_if_qualifies(sb.db, d, p["missing"], "test")
    assert refreshed["action"] == "re-proposed" and refreshed["run_status"] == RunStatus.AWAITING_APPROVAL
    run = graph.get_run(sb.db, refreshed["run_id"])
    approval = approvals.get_approval(sb.db, run.state["approval_id"])
    assert approval.proposal.included_roll_numbers == ["M001", "Y001"]

    with pytest.raises(PermissionDenied):
        approvals.approve(sb.db, approval.id, crew_people["coordinator"], expected_hash=approval.proposal_hash)
    approvals.approve(sb.db, approval.id, crew_people["approver"], expected_hash=approval.proposal_hash)
    sb.db.commit()
    run = graph.resume_after_approval(sb.db, approval)
    assert run.status == RunStatus.VERIFIED
    assert sorted(m.to_address for m in emails(sb, "PROFILE_SUBMITTED")) == ["m001@sandbox.test", "y001@sandbox.test"]
    assert "Shortlisted" in emails(sb, "PROFILE_SUBMITTED")[0].subject
    rejected = emails(sb, "NOT_SHORTLISTED")
    assert [m.to_address for m in rejected] == ["l001@sandbox.test"] and "CGPA" in rejected[0].body_text


def test_deadline_and_reminder_agents(sb, crew_people, registration, settings_override):
    d, p = registration
    form = consent_forms.get_form(sb.db, d.id)
    form.closes_at = utcnow() + timedelta(hours=6)
    for r in sb.db.scalars(select(StudentRequest).where(StudentRequest.status == "SENT")):
        r.expires_at = form.closes_at
    sb.db.commit()
    first = reminders.run(sb.db)
    assert first["consent"] == 1  # only Mohan's form is still unanswered and open
    assert reminders.run(sb.db)["consent"] == 0  # never repeated

    form.closes_at = utcnow() - timedelta(minutes=1)
    sb.db.commit()
    closed = consent_forms.close_due(sb.db)
    assert closed[0]["drive_id"] == d.id and closed[0]["no_response"] == 1
    req = sb.db.scalar(select(StudentRequest).where(StudentRequest.student_id == p["missing"].id))
    assert req.status == "EXPIRED"
    with pytest.raises(PreconditionFailed):
        outreach.respond_invitation(sb.db, req, True)

    # Shortlisted students are reminded before the drive.
    soon = sb.drive("Tomorrow Drive", utcnow() + timedelta(hours=10), utcnow() + timedelta(hours=12))
    sb.db.add(DriveAssignment(student_id=p["yes"].id, drive_id=soon.id, status="CONFIRMED", source="test"))
    sb.db.commit()
    assert reminders.run(sb.db)["drive"] == 1
    assert reminders.run(sb.db)["drive"] == 0


def test_each_agent_may_only_use_its_own_tools(sb, people, basic_drive):
    run = graph.start_run(sb.db, f"Check clashes for drive #{basic_drive.id}", people["officer"])
    ctx = ToolContext(sb.db, run, people["officer"])
    token = crew.CURRENT_AGENT.set(crew.BY_KEY["consent_form"])
    try:
        with pytest.raises(ToolFailure, match="Consent Form Agent"):
            call_tool(ctx, "execute_approved_upload", {"approval_id": 1}, node="consent_form", invoked_by="graph")
    finally:
        crew.CURRENT_AGENT.reset(token)
    denied = sb.db.scalar(select(AgentEvent).where(AgentEvent.event_type == "TOOL_DENIED"))
    assert denied.payload["agent"] == "consent_form"
    assert not any(t.startswith(("approve", "reject")) for a in crew.AGENTS for t in a.tools)  # no agent can approve
    for node in graph.NODES:
        assert crew.agent_for_node(node) is not None, node


def test_agents_api_lists_the_crew(sb):
    assert len(crew.AGENTS) == 21 and {a.stage for a in crew.AGENTS} == {1, 2, 3}
    names = [a["name"] for a in crew.describe()]
    assert "Email Reader Agent" in names and "Approval Review Agent" in names
    assert re.match(r"agent:\w+", crew.BY_KEY["reminder"].actor)

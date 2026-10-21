"""Invitations, missing-data requests, notifications and selection results (LOCAL mailbox transport)."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.agent import graph
from app.core.errors import Conflict, PreconditionFailed, ValidationFailed
from app.db.base import utcnow
from app.models.entities import DriveCriteria, EmailMessage, ExceptionItem, Student, StudentRequest
from app.models.enums import RunStatus
from app.services import approvals, outreach
from app.services.sandbox import ist


def token_from(email: EmailMessage) -> str:
    return re.search(r"/respond/([A-Za-z0-9_-]+)", email.body_text).group(1)


def emails(sb, category):
    return list(sb.db.scalars(select(EmailMessage).where(EmailMessage.category == category).order_by(EmailMessage.id)))


def approve_latest(sb, people, run):
    a = approvals.approve(sb.db, run.state["approval_id"], people["approver"], expected_hash=run.state["proposal_hash"])
    sb.db.commit()
    return graph.resume_after_approval(sb.db, a)


@pytest.fixture
def registration_drive(sb):
    sb.student("R001", "Accepts Invite", "CSE", 8.0, 0, consent=None)
    sb.student("R002", "Declines Invite", "CSE", 8.5, 0, consent=None)
    sb.student("R003", "Not Eligible", "CSE", 5.0, 0)
    d = sb.drive("Registration Drive", ist(2026, 12, 20, 10), ist(2026, 12, 20, 12))
    sb.db.scalar(select(DriveCriteria).where(DriveCriteria.drive_id == d.id)).required_consent_scope = "DRIVE_REGISTRATION"
    sb.db.commit()
    return d


def test_invitations_drive_registration_consent(sb, people, registration_drive):
    d = registration_drive
    run = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{d.id}", people["officer"])
    assert run.status == RunStatus.NEEDS_REVIEW and run.pending_question["type"] == "invitations_needed"
    run = graph.answer(sb.db, run, people["officer"], "send_invitations")
    assert run.status == RunStatus.COMPLETED and "Invitations emailed to 2" in run.summary
    invites = emails(sb, "DRIVE_INVITATION")
    assert sorted(m.to_address for m in invites) == ["r001@sandbox.test", "r002@sandbox.test"]
    assert all(m.status == "DELIVERED_LOCAL" for m in invites)

    view = outreach.public_view(sb.db, outreach.get_by_token(sb.db, token_from(invites[0])))
    assert view["drive"]["title"] == "Registration Drive" and "email" not in str(view) and "8.0" not in str(view)

    outreach.respond_invitation(sb.db, outreach.get_by_token(sb.db, token_from(invites[0])), True)
    outreach.respond_invitation(sb.db, outreach.get_by_token(sb.db, token_from(invites[1])), False)
    run = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{d.id}", people["officer"])
    assert run.status == RunStatus.AWAITING_APPROVAL
    proposal = approvals.get_approval(sb.db, run.state["approval_id"]).proposal
    assert proposal.included_roll_numbers == ["R001"]
    declined = next(x for x in proposal.excluded if x["roll_number"] == "R002")
    assert "Declined this drive's invitation" in declined["reasons"]

    run = approve_latest(sb, people, run)
    assert run.status == RunStatus.VERIFIED and run.state["notifications"]["students_notified"] == 1
    assert [m.to_address for m in emails(sb, "PROFILE_SUBMITTED")] == ["r001@sandbox.test"]
    assert emails(sb, "EMPLOYER_ROSTER")[0].status == "SKIPPED"  # sandbox employer has no contact email
    # Re-sending invitations never duplicates
    assert outreach.invite_eligible(sb.db, d, people["officer"])["sent"] == []


def test_missing_data_request_requires_officer_verification(sb, people):
    s = sb.student("M001", "Missing Cgpa", "CSE", None, 0)
    d = sb.drive("Data Drive", ist(2026, 12, 21, 10), ist(2026, 12, 21, 12))
    run = graph.start_run(sb.db, f"Email students with missing data for drive #{d.id}", people["officer"])
    assert run.intent == "REQUEST_MISSING_DATA" and run.status == RunStatus.COMPLETED
    msg = emails(sb, "DATA_REQUEST")[0]
    assert "CGPA" in msg.body_text
    token = token_from(msg)
    with pytest.raises(ValidationFailed):
        outreach.submit_data(sb.db, outreach.get_by_token(sb.db, token), {"cgpa": "12"})
    req = outreach.submit_data(sb.db, outreach.get_by_token(sb.db, token), {"cgpa": "8.25"})
    assert req.status == "SUBMITTED"
    sb.db.refresh(s)
    assert s.cgpa is None  # not applied until an officer verifies
    exc = sb.db.scalar(select(ExceptionItem).where(ExceptionItem.kind == "DATA_SUBMITTED"))
    assert exc.detail["submitted"] == {"cgpa": 8.25}
    with pytest.raises(Conflict):
        outreach.submit_data(sb.db, outreach.get_by_token(sb.db, token), {"cgpa": "9"})
    outreach.review_data(sb.db, req.id, people["officer"], apply=True)
    sb.db.refresh(s)
    sb.db.refresh(exc)
    assert s.cgpa == 8.25 and exc.status == "RESOLVED"


def test_expired_link_cannot_be_used(sb, people, registration_drive):
    outreach.invite_eligible(sb.db, registration_drive, people["officer"])
    req = sb.db.scalar(select(StudentRequest).limit(1))
    req.expires_at = utcnow() - timedelta(minutes=1)
    sb.db.commit()
    req = outreach.get_by_token(sb.db, req.token)
    assert req.status == "EXPIRED"
    with pytest.raises(PreconditionFailed):
        outreach.respond_invitation(sb.db, req, True)


def test_selection_results_notify_and_show_in_history(sb, people, basic_drive):
    run = graph.start_run(sb.db, f"Prepare the eligible roster for drive #{basic_drive.id}", people["officer"])
    approve_latest(sb, people, run)
    s1 = sb.db.scalar(select(Student).where(Student.roll_number == "S001"))
    s2 = sb.db.scalar(select(Student).where(Student.roll_number == "S002"))
    s3 = sb.db.scalar(select(Student).where(Student.roll_number == "S003"))
    with pytest.raises(ValidationFailed):  # not on the submitted roster
        outreach.record_results(sb.db, basic_drive, [{"student_id": s3.id, "outcome": "SELECTED"}], True, people["officer"])
    sb.db.rollback()
    out = outreach.record_results(sb.db, basic_drive, [{"student_id": s1.id, "outcome": "SELECTED"},
                                                       {"student_id": s2.id, "outcome": "NOT_SELECTED"}], True, people["officer"])
    assert out == {"changed": 2, "notified": 2}
    assert [m.to_address for m in emails(sb, "SELECTED")] == ["s001@sandbox.test"]
    assert [m.to_address for m in emails(sb, "NOT_SELECTED")] == ["s002@sandbox.test"]
    again = outreach.record_results(sb.db, basic_drive, [{"student_id": s1.id, "outcome": "SELECTED"}], True, people["officer"])
    assert again["changed"] == 0 and len(emails(sb, "SELECTED")) == 1
    history = outreach.placement_history(sb.db, s1)
    assert history[0]["outcome"] == "SELECTED" and history[0]["sent_to_employer"] and history[0]["decision"] == "ELIGIBLE"
    assert outreach.selected_employers(sb.db, [s1.id, s2.id]) == {s1.id: [basic_drive.employer.name]}

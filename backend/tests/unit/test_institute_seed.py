"""Institute starting data and the rule that invented addresses are never emailed."""
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.entities import Drive, Student, User
from app.services import app_settings
from app.services.mailer import is_demo_address
from app.services.seed import seed_institute


@pytest.mark.parametrize("addr,demo", [
    ("22cse001@students.example.edu", True), ("x@sandbox.test", True), ("a@placementos.demo", True),
    ("hr@example.com", True), ("someone@gmail.com", False), ("dean@college.ac.in", False), ("", True),
])
def test_demo_addresses_are_never_sent(addr, demo):
    assert is_demo_address(addr) is demo


def test_institute_seed(sb):
    s = get_settings()
    saved = s.real_student_emails
    object.__setattr__(s, "real_student_emails", "22CSE005:pooja.real@gmail.com, 22it018:shreya.real@gmail.com")
    try:
        result = seed_institute(sb.db)
    finally:
        object.__setattr__(s, "real_student_emails", saved)
    assert result["students"] == 120 and result["drives"] == 0
    assert sb.db.scalar(select(func.count(Drive.id))) == 0
    roles = {u.email: u.role for u in sb.db.scalars(select(User))}
    assert roles["driveuploadtesting@gmail.com"] == "coordinator" and roles["approver@placementos.demo"] == "approver"
    real = {st.roll_number: st.email for st in sb.db.scalars(select(Student)) if not is_demo_address(st.email)}
    assert real == {"22CSE005": "pooja.real@gmail.com", "22IT018": "shreya.real@gmail.com"}
    assert sum(1 for st in sb.db.scalars(select(Student)) if st.branch is None) == 0
    assert sum(1 for st in sb.db.scalars(select(Student)) if st.cgpa is None) == 4
    assert "companytestmailone@gmail.com" in app_settings.get(sb.db, "trusted_company_senders")

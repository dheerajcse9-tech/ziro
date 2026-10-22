"""The official hackathon scenarios (TC1-TC4) and the institute test cases (TC5-TC6), executed through the same runner as the Test Lab."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services import test_lab


@pytest.fixture(scope="module")
def evidence_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        yield db


@pytest.mark.parametrize("test_id", ["TC1", "TC2", "TC3", "TC4", "TC5", "TC6"])
def test_official_scenario(evidence_db, test_id):
    result = test_lab.run_scenario(evidence_db, test_id, None)
    failed = [c for c in result.checks if not c["passed"]]
    assert result.passed, result.failure_details or failed
    assert len(result.checks) >= 8


def test_scenarios_never_send_real_email(evidence_db, monkeypatch):
    """TC5/TC6 mail real company addresses; inside the Test Lab every message must stay in the sandbox mailbox."""
    from app.core.config import get_settings
    from app.services import mailer

    monkeypatch.setattr(mailer, "_deliver_smtp", lambda msg: (_ for _ in ()).throw(AssertionError("SMTP used")))
    s = get_settings()
    saved = s.smtp_host
    object.__setattr__(s, "smtp_host", "smtp.gmail.com")
    try:
        assert test_lab.run_scenario(evidence_db, "TC6", None).passed
    finally:
        object.__setattr__(s, "smtp_host", saved)
    assert mailer.LOCAL_ONLY.get() is False  # the guard is reset after the run

"""The four official hackathon scenarios, executed through the same runner as the Test Lab."""
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


@pytest.mark.parametrize("test_id", ["TC1", "TC2", "TC3", "TC4"])
def test_official_scenario(evidence_db, test_id):
    result = test_lab.run_scenario(evidence_db, test_id, None)
    failed = [c for c in result.checks if not c["passed"]]
    assert result.passed, result.failure_details or failed
    assert len(result.checks) >= 8

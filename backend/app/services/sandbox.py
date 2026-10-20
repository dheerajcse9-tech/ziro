"""Isolated execution environment for the Test Lab and the automated test suite.

Each sandbox is a fresh in-memory SQLite database plus its own in-process
LOCAL MOCK portal reached over the real HTTP adapter (ASGI transport). It
never touches the demo database and never contacts an external service.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.planner import FORCE_DETERMINISTIC
from app.core.security import hash_password
from app.db.base import Base
from app.integrations.drive_form import HtmlDriveFormAdapter, MockPortalAdmin
from app.integrations.registry import _admin_override, _factory_override
from app.mock_portal.app import create_mock_portal_app
from app.mock_portal.models import MockPortalForm
from app.models.entities import ConsentRecord, Destination, Drive, DriveCriteria, Employer, Student, User
from app.models.enums import BacklogPolicy, ConsentScope, ConsentStatus, DestinationKind, DriveStatus, Role

SANDBOX_BASE_URL = "http://127.0.0.1/sandbox-mock-portal"  # allowlisted host; routed in-process
TOKEN = "sandbox-admin-token"
TZ = "Asia/Kolkata"


class Sandbox:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def _fk(dbapi_conn, _):  # pragma: no cover - driver hook
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        # The mock portal gets its own database, like a real external system would.
        self.portal_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.portal_engine)
        self.PortalSession = sessionmaker(bind=self.portal_engine, autoflush=False, expire_on_commit=False)
        self.portal = TestClient(create_mock_portal_app(self.PortalSession, TOKEN), base_url="http://mockportal")
        self.db: Session = self.Session()
        self._tokens: list = []

    def __enter__(self) -> "Sandbox":
        self._tokens = [
            (_factory_override, _factory_override.set(lambda dest: HtmlDriveFormAdapter("http://mockportal", client=self.portal))),
            (_admin_override, _admin_override.set(lambda: MockPortalAdmin("http://mockportal", TOKEN, client=self.portal))),
            (FORCE_DETERMINISTIC, FORCE_DETERMINISTIC.set(True)),
        ]
        return self

    def __exit__(self, *exc) -> None:
        for var, token in reversed(self._tokens):
            var.reset(token)
        self.db.close()
        self.portal.close()
        self.engine.dispose()
        self.portal_engine.dispose()

    # ------------------------------------------------------------------ fixtures
    def admin(self) -> MockPortalAdmin:
        return MockPortalAdmin("http://mockportal", TOKEN, client=self.portal)

    def user(self, role: Role, email: str | None = None) -> User:
        u = User(email=email or f"{role}@sandbox.test", name=f"Sandbox {role}", role=role,
                 password_hash=hash_password("sandbox-only"))
        self.db.add(u)
        self.db.commit()
        return u

    def student(self, roll: str, name: str, branch: str | None, cgpa: float | None, backlogs: int | None,
                consent: str | None = "GRANTED", year: int = 2026) -> Student:
        s = Student(roll_number=roll, full_name=name, branch=branch, branch_raw=branch, cgpa=cgpa,
                    active_backlogs=backlogs, graduation_year=year, email=f"{roll.lower()}@sandbox.test", data_issues=[])
        self.db.add(s)
        self.db.flush()
        if consent == "GRANTED":
            self.db.add(ConsentRecord(student_id=s.id, scope=ConsentScope.SHARE_WITH_EMPLOYERS, status=ConsentStatus.GRANTED, source="fixture"))
        elif consent == "REVOKED":
            self.db.add(ConsentRecord(student_id=s.id, scope=ConsentScope.SHARE_WITH_EMPLOYERS, status=ConsentStatus.GRANTED, source="fixture"))
            self.db.flush()
            self.db.add(ConsentRecord(student_id=s.id, scope=ConsentScope.SHARE_WITH_EMPLOYERS, status=ConsentStatus.REVOKED, source="fixture"))
        self.db.commit()
        return s

    def drive(self, title: str, start: datetime, end: datetime, *, min_cgpa: float | None = 7.0,
              backlog_policy: str = BacklogPolicy.NO_ACTIVE_BACKLOGS, max_backlogs: int | None = 0,
              branches: list[str] | None = None, published: str | None = None, tz: str = TZ,
              form_ref: str | None = None) -> Drive:
        emp = Employer(name=f"{title} Employer")
        self.db.add(emp)
        self.db.flush()
        form_ref = form_ref or f"SBX-{emp.id}"
        dest = Destination(name=f"{title} form (sandbox mock)", kind=DestinationKind.LOCAL_MOCK_FORM,
                           base_url=SANDBOX_BASE_URL, form_ref=form_ref)
        self.db.add(dest)
        self.db.flush()
        d = Drive(employer_id=emp.id, title=title, starts_at=start, ends_at=end, timezone=tz,
                  status=DriveStatus.OPEN, destination_id=dest.id)
        self.db.add(d)
        self.db.flush()
        self.db.add(DriveCriteria(drive_id=d.id, version=1, min_cgpa=min_cgpa, backlog_policy=backlog_policy,
                                  max_active_backlogs=max_backlogs, allowed_branches=branches or [], graduation_years=[],
                                  required_consent_scope=ConsentScope.SHARE_WITH_EMPLOYERS))
        if published is None:
            parts = []
            if min_cgpa is not None:
                parts.append(f"CGPA >= {min_cgpa}")
            if backlog_policy == BacklogPolicy.NO_ACTIVE_BACKLOGS:
                parts.append("no active backlogs")
            elif backlog_policy == BacklogPolicy.MAX_ALLOWED:
                parts.append(f"maximum {max_backlogs} backlogs")
            published = ", ".join(parts) + "." + (f"\nBranches: {', '.join(branches)}." if branches else "")
        self.db.commit()
        with self.PortalSession() as ps:
            ps.add(MockPortalForm(form_ref=form_ref, employer_name=emp.name, drive_title=title,
                                  published_criteria=published, schedule_text=f"{start.isoformat()} - {end.isoformat()}",
                                  required_fields=["roll_number", "full_name", "branch", "cgpa"]))
            ps.commit()
        return d


def ist(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(TZ))

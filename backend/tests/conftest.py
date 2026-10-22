import os
import sys
import tempfile

# Isolated database for the API process under test; must be set before app import.
_tmp = tempfile.mkdtemp(prefix="placementos-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ.setdefault("JWT_SECRET", "test-secret-for-automated-tests-only-0123456789")
os.environ.setdefault("API_TEST_PORT", "8765")
os.environ["PLACEMENTOS_IGNORE_ENV_FILE"] = "1"  # a developer's backend/.env (real keys) must not leak into tests
os.environ.setdefault("AUTOPILOT", "false")
os.environ.setdefault("SEED_PROFILE", "demo")  # tests use the synthetic dataset with sample drives  # tests drive the background agents explicitly
for _k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_CLIENT_ID", "SMTP_HOST", "INBOX_IMAP_HOST"):
    os.environ.pop(_k, None)  # never call real services from tests
os.environ.setdefault("MOCK_PORTAL_BASE_URL", f"http://127.0.0.1:{os.environ['API_TEST_PORT']}/mock-portal")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402

import app.models  # noqa: E402,F401
from app.models.enums import Role  # noqa: E402
from app.services.sandbox import Sandbox, ist  # noqa: E402


@pytest.fixture
def sb():
    with Sandbox() as s:
        yield s


@pytest.fixture
def people(sb):
    return {
        "officer": sb.user(Role.OFFICER),
        "approver": sb.user(Role.APPROVER),
        "viewer": sb.user(Role.VIEWER),
        "admin": sb.user(Role.ADMIN),
    }


@pytest.fixture
def basic_drive(sb):
    sb.student("S001", "Asha", "CSE", 8.0, 0)
    sb.student("S002", "Bala", "CSE", 7.5, 0)
    sb.student("S003", "Chitra", "CSE", 6.0, 0)
    return sb.drive("Basic Drive", ist(2026, 12, 10, 10), ist(2026, 12, 10, 12))

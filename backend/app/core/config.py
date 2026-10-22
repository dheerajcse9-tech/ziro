"""Application configuration, loaded from environment variables.

Values may also come from a `backend/.env` file (KEY=VALUE lines) for local
development. Real environment variables always take precedence over the file.

Secrets (JWT signing key, LLM API key, database URL) are read server-side only
and never returned by any API endpoint.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Populate os.environ from a simple KEY=VALUE file without overriding existing variables."""
    if not path.is_file() or os.getenv("PLACEMENTOS_IGNORE_ENV_FILE"):
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> list[str]:
    return [p.strip() for p in os.getenv(name, default).split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = "PlaceCrew"
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", True))
    # What `seed` / Demo reset loads: "institute" (student list in app/data, staff logins, company senders, no drives)
    # or "demo" (the synthetic hackathon dataset with sample drives, used by the automated tests).
    seed_profile: str = field(default_factory=lambda: os.getenv("SEED_PROFILE", "institute").strip().lower())
    # Students who should receive real email: "ROLL:address,ROLL:address". Kept in backend/.env, not in git.
    real_student_emails: str = field(default_factory=lambda: os.getenv("REAL_STUDENT_EMAILS", ""))
    # Company addresses whose recruitment emails are trusted from the start (comma separated).
    company_senders: str = field(default_factory=lambda: os.getenv(
        "COMPANY_SENDERS", "companytestmailone@gmail.com,companytestmailtwo@gmail.com,companytestmailthree@gmail.com"))
    institution_name: str = field(default_factory=lambda: os.getenv("INSTITUTION_NAME", "Institute Placement Cell"))
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./placementos.db")
    )
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", ""))
    jwt_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("JWT_TTL_MINUTES", "720")))
    cors_origins: list[str] = field(
        default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:3000")
    )
    default_timezone: str = field(default_factory=lambda: os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata"))

    # AI model used by the Goal Planner and Email Reader agents. Keys are optional: without one the agents
    # use their rule-based fallbacks and label every run accordingly. LLM_PROVIDER=auto picks Gemini when
    # GEMINI_API_KEY is set, otherwise Claude when ANTHROPIC_API_KEY is set.
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "auto").strip().lower())
    llm_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-opus-5-5"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    gemini_base_url: str = field(default_factory=lambda: os.getenv(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"))
    llm_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "60")))

    # Google sign-in for the placement mailbox (Gmail API: read company emails, send all outgoing email).
    google_client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_SECRET", ""))
    google_redirect_uri: str = field(default_factory=lambda: os.getenv("GOOGLE_REDIRECT_URI", ""))

    # Autopilot: background agents (mailbox, deadlines, reminders) every N seconds.
    autopilot_enabled: bool = field(default_factory=lambda: _bool("AUTOPILOT", True))
    autopilot_interval_seconds: int = field(default_factory=lambda: max(30, int(os.getenv("AUTOPILOT_INTERVAL_SECONDS", "120"))))

    # Destination integration. Only hosts in this allowlist may receive rosters.
    mock_portal_base_url: str = field(
        default_factory=lambda: os.getenv("MOCK_PORTAL_BASE_URL", "http://127.0.0.1:8000/mock-portal")
    )
    allowed_destination_hosts: list[str] = field(
        default_factory=lambda: _list("ALLOWED_DESTINATION_HOSTS", "127.0.0.1,localhost")
    )
    upload_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("UPLOAD_TIMEOUT_SECONDS", "8")))
    upload_max_attempts: int = field(default_factory=lambda: int(os.getenv("UPLOAD_MAX_ATTEMPTS", "3")))

    # Outbound email. Without SMTP_HOST every message is kept in the local mailbox (visible in the app).
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: os.getenv("SMTP_FROM", "PlaceCrew <placements@example.edu>"))
    smtp_starttls: bool = field(default_factory=lambda: _bool("SMTP_STARTTLS", True))
    # Base URL students open from emailed links (the frontend).
    public_app_url: str = field(default_factory=lambda: os.getenv("PUBLIC_APP_URL", "http://localhost:3000"))

    # Company inbox. With INBOX_IMAP_HOST set, "Check mailbox" reads matching emails from that mailbox
    # (read-only: messages are never marked read, moved or deleted). User/password default to the SMTP ones.
    inbox_address: str = field(default_factory=lambda: os.getenv("INBOX_ADDRESS", "") or os.getenv("SMTP_USER", "")
                               or "placements@college.example.edu")
    inbox_imap_host: str = field(default_factory=lambda: os.getenv("INBOX_IMAP_HOST", ""))
    inbox_imap_port: int = field(default_factory=lambda: int(os.getenv("INBOX_IMAP_PORT", "993")))
    inbox_imap_user: str = field(default_factory=lambda: os.getenv("INBOX_IMAP_USER", "") or os.getenv("SMTP_USER", ""))
    inbox_imap_password: str = field(default_factory=lambda: os.getenv("INBOX_IMAP_PASSWORD", "")
                                     or os.getenv("SMTP_PASSWORD", ""))
    inbox_imap_folder: str = field(default_factory=lambda: os.getenv("INBOX_IMAP_FOLDER", "INBOX"))
    # Only emails whose subject contains this text are read, so personal mail in the same mailbox is never touched.
    inbox_subject_filter: str = field(default_factory=lambda: os.getenv("INBOX_SUBJECT_FILTER", "Campus Drive"))
    inbox_lookback_days: int = field(default_factory=lambda: int(os.getenv("INBOX_LOOKBACK_DAYS", "30")))

    # Serve the built static frontend from the API process (single-service deploys).
    frontend_dist: str = field(default_factory=lambda: os.getenv("FRONTEND_DIST", ""))

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def active_llm(self) -> str:
        """'gemini', 'anthropic' or 'none'."""
        if self.llm_provider == "gemini":
            return "gemini" if self.gemini_api_key else "none"
        if self.llm_provider == "anthropic":
            return "anthropic" if self.llm_api_key else "none"
        if self.llm_provider == "none":
            return "none"
        if self.gemini_api_key:
            return "gemini"
        return "anthropic" if self.llm_api_key else "none"

    @property
    def active_llm_model(self) -> str | None:
        return {"gemini": self.gemini_model, "anthropic": self.llm_model}.get(self.active_llm)

    @property
    def llm_enabled(self) -> bool:
        return self.active_llm != "none"

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def google_callback_url(self) -> str:
        return self.google_redirect_uri or f"{self.public_app_url.rstrip('/')}/api/integrations/google/callback"

    def destination_host_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        return (parsed.hostname or "") in self.allowed_destination_hosts


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.jwt_secret:
        if settings.environment == "production":
            raise RuntimeError("JWT_SECRET must be set in production")
        # Development-only fallback; never used when APP_ENV=production.
        object.__setattr__(settings, "jwt_secret", "dev-only-insecure-secret-change-me")
    return settings

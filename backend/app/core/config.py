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
    if not path.is_file():
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
    app_name: str = "PlacementOS"
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", True))
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./placementos.db")
    )
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", ""))
    jwt_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("JWT_TTL_MINUTES", "720")))
    cors_origins: list[str] = field(
        default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:3000")
    )
    default_timezone: str = field(default_factory=lambda: os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata"))

    # LLM planner. The key is optional: without it the agent runs its
    # deterministic fallback planner and labels every run accordingly.
    llm_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-opus-5-5"))
    llm_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "60")))

    # Destination integration. Only hosts in this allowlist may receive rosters.
    mock_portal_base_url: str = field(
        default_factory=lambda: os.getenv("MOCK_PORTAL_BASE_URL", "http://127.0.0.1:8000/mock-portal")
    )
    allowed_destination_hosts: list[str] = field(
        default_factory=lambda: _list("ALLOWED_DESTINATION_HOSTS", "127.0.0.1,localhost")
    )
    upload_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("UPLOAD_TIMEOUT_SECONDS", "8")))
    upload_max_attempts: int = field(default_factory=lambda: int(os.getenv("UPLOAD_MAX_ATTEMPTS", "3")))

    # Serve the built static frontend from the API process (single-service deploys).
    frontend_dist: str = field(default_factory=lambda: os.getenv("FRONTEND_DIST", ""))

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

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

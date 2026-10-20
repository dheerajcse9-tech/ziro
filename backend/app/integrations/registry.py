"""Resolves the adapter for a configured destination, enforcing the host allowlist."""
from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

from app.core.config import get_settings
from app.core.errors import PreconditionFailed
from app.integrations.drive_form import DriveFormAdapter, HtmlDriveFormAdapter, MockPortalAdmin
from app.models.entities import Destination
from app.models.enums import DestinationKind

# Context-local so an isolated Test Lab run can never redirect a concurrent real request.
_factory_override: ContextVar[Callable[[Destination], DriveFormAdapter] | None] = ContextVar("adapter_factory", default=None)
_admin_override: ContextVar[Callable[[], MockPortalAdmin] | None] = ContextVar("mock_admin_factory", default=None)


def set_adapter_factory(factory: Callable[[Destination], DriveFormAdapter] | None):
    """Route destinations to an in-process client (tests). Allowlist checks still apply."""
    return _factory_override.set(factory)


def adapter_for(destination: Destination) -> DriveFormAdapter:
    settings = get_settings()
    if not destination.is_enabled:
        raise PreconditionFailed(f"Destination '{destination.name}' is disabled")
    if not settings.destination_host_allowed(destination.base_url):
        raise PreconditionFailed("Destination host is not in the allowlist", details={"base_url": destination.base_url})
    override = _factory_override.get()
    if override is not None:
        return override(destination)
    if destination.kind in (DestinationKind.LOCAL_MOCK_FORM, DestinationKind.ORGANISER_FORM):
        return HtmlDriveFormAdapter(destination.base_url, timeout=settings.upload_timeout_seconds)
    raise PreconditionFailed(f"No adapter for destination kind {destination.kind}")


def mock_admin_token() -> str:
    import hashlib

    return hashlib.sha256(("mock-portal:" + get_settings().jwt_secret).encode()).hexdigest()[:32]


def set_mock_admin_factory(factory: Callable[[], MockPortalAdmin] | None):
    return _admin_override.set(factory)


def mock_admin() -> MockPortalAdmin:
    override = _admin_override.get()
    if override is not None:
        return override()
    return MockPortalAdmin(get_settings().mock_portal_base_url, mock_admin_token())

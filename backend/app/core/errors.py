"""Domain errors mapped to HTTP responses by the API layer."""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class PermissionDenied(DomainError):
    status_code = 403
    code = "forbidden"


class ValidationFailed(DomainError):
    status_code = 422
    code = "validation_failed"


class PreconditionFailed(DomainError):
    """A consequential action's preconditions (approval binding, consent...) no longer hold."""

    status_code = 412
    code = "precondition_failed"

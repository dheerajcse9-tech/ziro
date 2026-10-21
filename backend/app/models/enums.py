"""Enumerations used across the domain. Stored as strings in the database."""
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OFFICER = "officer"  # placement officer: prepares drives, runs the agent
    APPROVER = "approver"  # placement head: approves external submissions
    VIEWER = "viewer"


class ConsentScope(StrEnum):
    SHARE_WITH_EMPLOYERS = "SHARE_WITH_EMPLOYERS"


class ConsentStatus(StrEnum):
    GRANTED = "GRANTED"
    REVOKED = "REVOKED"


class ImportStatus(StrEnum):
    PREVIEWED = "PREVIEWED"
    COMMITTED = "COMMITTED"
    DISCARDED = "DISCARDED"


class DriveStatus(StrEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    ROSTER_SUBMITTED = "ROSTER_SUBMITTED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class BacklogPolicy(StrEnum):
    NO_ACTIVE_BACKLOGS = "NO_ACTIVE_BACKLOGS"
    MAX_ALLOWED = "MAX_ALLOWED"
    NOT_CONSIDERED = "NOT_CONSIDERED"


class Decision(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNKNOWN = "UNKNOWN"


class RosterStatus(StrEnum):
    """Final disposition of a student for a specific roster proposal."""

    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"
    HELD = "HELD"  # eligible but blocked pending officer resolution (e.g. schedule clash)


class AssignmentStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    HELD_CONFLICT = "HELD_CONFLICT"
    WITHDRAWN = "WITHDRAWN"


class RunStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING_INPUTS = "VALIDATING_INPUTS"
    RETRIEVING_EVIDENCE = "RETRIEVING_EVIDENCE"
    INTERPRETING_CRITERIA = "INTERPRETING_CRITERIA"
    EVALUATING_ROSTER = "EVALUATING_ROSTER"
    CHECKING_CONFLICTS = "CHECKING_CONFLICTS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    FAILED_VERIFICATION = "FAILED_VERIFICATION"
    COMPLETED = "COMPLETED"  # read-only tasks (e.g. what-if analysis) that finish without upload


TERMINAL_RUN_STATES = {
    RunStatus.VERIFIED,
    RunStatus.REJECTED,
    RunStatus.FAILED,
    RunStatus.FAILED_VERIFICATION,
    RunStatus.COMPLETED,
}


class ProposalStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    EXECUTED = "EXECUTED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    CONSUMED = "CONSUMED"  # used by an executed upload


class UploadStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    UNCERTAIN = "UNCERTAIN"  # timeout / unknown outcome: must reconcile before any resubmit
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"  # pre-execution guard refused (stale approval, consent change...)


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED_VERIFICATION = "FAILED_VERIFICATION"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"


class ExceptionKind(StrEnum):
    MISSING_STUDENT_DATA = "MISSING_STUDENT_DATA"
    INVALID_STUDENT_DATA = "INVALID_STUDENT_DATA"
    DUPLICATE_IDENTIFIER = "DUPLICATE_IDENTIFIER"
    MISSING_CONSENT = "MISSING_CONSENT"
    SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"
    CRITERIA_MISSING = "CRITERIA_MISSING"
    CRITERIA_MISMATCH = "CRITERIA_MISMATCH"
    AMBIGUOUS_CRITERIA = "AMBIGUOUS_CRITERIA"
    SOURCE_RETRIEVAL_FAILED = "SOURCE_RETRIEVAL_FAILED"
    SUSPICIOUS_SOURCE_CONTENT = "SUSPICIOUS_SOURCE_CONTENT"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    UPLOAD_DISCREPANCY = "UPLOAD_DISCREPANCY"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    WITHDRAWAL_REQUIRED = "WITHDRAWAL_REQUIRED"  # already sent to employer, no longer eligible


class ExceptionStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class ActorType(StrEnum):
    USER = "USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class DestinationKind(StrEnum):
    LOCAL_MOCK_FORM = "LOCAL_MOCK_FORM"
    ORGANISER_FORM = "ORGANISER_FORM"

"""ORM entities for PlacementOS.

Design notes
- Students are identified by an internal integer id and a unique, normalised
  roll number. Names are never used as identifiers.
- Eligibility criteria are versioned per drive; every decision references the
  criteria version that produced it.
- Proposals carry a content hash that binds approvals to an exact roster,
  criteria version, schedule version and destination.
- Audit events are append-only and hash-chained (see app.audit.ledger).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime, utcnow


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RosterImport(TimestampMixin, Base):
    __tablename__ = "roster_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    # Normalised rows and per-row issues kept so the commit step applies exactly
    # what was previewed.
    preview_rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    committed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Student(TimestampMixin, Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    roll_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    branch: Mapped[str | None] = mapped_column(String(20), index=True)  # normalised code, e.g. CSE
    branch_raw: Mapped[str | None] = mapped_column(String(120))
    cgpa: Mapped[float | None] = mapped_column(Float)
    active_backlogs: Mapped[int | None] = mapped_column(Integer)
    graduation_year: Mapped[int | None] = mapped_column(Integer)
    data_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    record_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    last_import_id: Mapped[int | None] = mapped_column(ForeignKey("roster_imports.id"))

    consents: Mapped[list["ConsentRecord"]] = relationship(
        back_populates="student", order_by="ConsentRecord.id"
    )

    __table_args__ = (
        CheckConstraint("cgpa IS NULL OR (cgpa >= 0 AND cgpa <= 10)", name="cgpa_range"),
        CheckConstraint("active_backlogs IS NULL OR active_backlogs >= 0", name="backlogs_nonneg"),
    )


class ConsentRecord(TimestampMixin, Base):
    """Append-only consent history. The latest record per (student, scope) is current."""

    __tablename__ = "consent_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)  # e.g. "import:3", "manual"
    evidence_ref: Mapped[str | None] = mapped_column(String(255))
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    effective_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    student: Mapped[Student] = relationship(back_populates="consents")

    __table_args__ = (Index("ix_consent_student_scope", "student_id", "scope", "id"),)


class Employer(TimestampMixin, Base):
    __tablename__ = "employers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    industry: Mapped[str | None] = mapped_column(String(120))
    website: Mapped[str | None] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(255))

    drives: Mapped[list["Drive"]] = relationship(back_populates="employer")


class Destination(TimestampMixin, Base):
    """An allowed upload target (employer drive form)."""

    __tablename__ = "destinations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    form_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Drive(TimestampMixin, Base):
    __tablename__ = "drives"
    id: Mapped[int] = mapped_column(primary_key=True)
    employer_id: Mapped[int] = mapped_column(ForeignKey("employers.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    role_title: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    destination_id: Mapped[int | None] = mapped_column(ForeignKey("destinations.id"))
    schedule_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    employer: Mapped[Employer] = relationship(back_populates="drives")
    destination: Mapped[Destination | None] = relationship()
    criteria_versions: Mapped[list["DriveCriteria"]] = relationship(
        back_populates="drive", order_by="DriveCriteria.version"
    )

    __table_args__ = (CheckConstraint("ends_at > starts_at", name="interval_positive"),)


class DriveCriteria(TimestampMixin, Base):
    """Immutable, versioned eligibility criteria for a drive."""

    __tablename__ = "drive_criteria"
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    min_cgpa: Mapped[float | None] = mapped_column(Float)
    backlog_policy: Mapped[str] = mapped_column(String(30), nullable=False)
    max_active_backlogs: Mapped[int | None] = mapped_column(Integer)
    allowed_branches: Mapped[list[str]] = mapped_column(JSON, default=list)  # empty = all branches
    graduation_years: Mapped[list[int]] = mapped_column(JSON, default=list)  # empty = any
    required_consent_scope: Mapped[str] = mapped_column(String(40), nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(String(500))
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    drive: Mapped[Drive] = relationship(back_populates="criteria_versions")

    __table_args__ = (UniqueConstraint("drive_id", "version", name="uq_drive_criteria_version"),)


class DriveAssignment(TimestampMixin, Base):
    """A student's placement on a drive roster. Used for schedule-clash detection."""

    __tablename__ = "drive_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    student: Mapped[Student] = relationship()
    drive: Mapped[Drive] = relationship()

    __table_args__ = (UniqueConstraint("student_id", "drive_id", name="uq_assignment_student_drive"),)


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    planner_mode: Mapped[str] = mapped_column(String(40), nullable=False)  # LLM | DETERMINISTIC_FALLBACK
    llm_model: Mapped[str | None] = mapped_column(String(80))
    intent: Mapped[str | None] = mapped_column(String(40))
    drive_id: Mapped[int | None] = mapped_column(ForeignKey("drives.id"))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(80))
    plan: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pending_question: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    criteria_version: Mapped[int | None] = mapped_column(Integer)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("roster_proposals.id", use_alter=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_test_fixture: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    drive: Mapped[Drive | None] = relationship()
    events: Mapped[list["AgentEvent"]] = relationship(back_populates="run", order_by="AgentEvent.seq")


class AgentEvent(TimestampMixin, Base):
    """Every node transition, tool call and tool result of an agent run."""

    __tablename__ = "agent_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    node: Mapped[str | None] = mapped_column(String(60))
    tool_name: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str | None] = mapped_column(String(40))
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    run: Mapped[AgentRun] = relationship(back_populates="events")

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_agent_event_seq"),)


class EligibilityDecision(TimestampMixin, Base):
    __tablename__ = "eligibility_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("roster_proposals.id", use_alter=True))
    criteria_version: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    roll_number: Mapped[str] = mapped_column(String(40), nullable=False)
    student_record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    roster_status: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    consent_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (Index("ix_decision_drive_stale", "drive_id", "is_stale"),)


class RosterProposal(TimestampMixin, Base):
    __tablename__ = "roster_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    criteria_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_id: Mapped[int] = mapped_column(ForeignKey("destinations.id"), nullable=False)
    destination_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    roster_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    included_roll_numbers: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    held: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    unresolved_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    invalidated_reason: Mapped[str | None] = mapped_column(Text)

    drive: Mapped[Drive] = relationship()
    destination: Mapped[Destination] = relationship()


class ApprovalRequest(TimestampMixin, Base):
    __tablename__ = "approval_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("roster_proposals.id", ondelete="CASCADE"), nullable=False)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)  # e.g. "agent:run:5 for user:2"
    requested_for_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
    invalidated_reason: Mapped[str | None] = mapped_column(Text)

    proposal: Mapped[RosterProposal] = relationship()
    decided_by: Mapped[User | None] = relationship(foreign_keys=[decided_by_id])


class UploadOperation(TimestampMixin, Base):
    __tablename__ = "upload_operations"
    id: Mapped[int] = mapped_column(primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    approval_id: Mapped[int] = mapped_column(ForeignKey("approval_requests.id"), nullable=False)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("roster_proposals.id"), nullable=False)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id"), nullable=False)
    destination_id: Mapped[int] = mapped_column(ForeignKey("destinations.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    submitted_roll_numbers: Mapped[list[str]] = mapped_column(JSON, default=list)
    attempt_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    destination_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    executed_by: Mapped[str] = mapped_column(String(120), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    drive: Mapped[Drive] = relationship()
    destination: Mapped[Destination] = relationship()
    proposal: Mapped[RosterProposal] = relationship()


class VerificationResult(TimestampMixin, Base):
    __tablename__ = "verification_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("upload_operations.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    method: Mapped[str] = mapped_column(String(80), nullable=False)
    expected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_count: Mapped[int | None] = mapped_column(Integer)
    missing_roll_numbers: Mapped[list[str]] = mapped_column(JSON, default=list)
    unexpected_roll_numbers: Mapped[list[str]] = mapped_column(JSON, default=list)
    duplicate_roll_numbers: Mapped[list[str]] = mapped_column(JSON, default=list)
    observed_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    detail: Mapped[str | None] = mapped_column(Text)
    receipt_hash: Mapped[str | None] = mapped_column(String(64))


class AttendanceSheet(TimestampMixin, Base):
    __tablename__ = "attendance_sheets"
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), nullable=False)
    upload_id: Mapped[int] = mapped_column(ForeignKey("upload_operations.id"), nullable=False)
    verification_id: Mapped[int] = mapped_column(ForeignKey("verification_results.id"), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    roll_numbers: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_at_generation: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    content_csv: Mapped[str | None] = mapped_column(Text)  # purged after the retention window
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_by: Mapped[str] = mapped_column(String(120), nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SourceEvidence(TimestampMixin, Base):
    """Retrieved source material (employer form definition, published criteria, read-backs)."""

    __tablename__ = "source_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    drive_id: Mapped[int | None] = mapped_column(ForeignKey("drives.id", ondelete="SET NULL"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    upload_id: Mapped[int | None] = mapped_column(ForeignKey("upload_operations.id", ondelete="SET NULL"))
    source_url: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ExceptionItem(TimestampMixin, Base):
    __tablename__ = "exception_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    drive_id: Mapped[int | None] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    resolution: Mapped[str | None] = mapped_column(String(60))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AuditEvent(Base):
    """Append-only, hash-chained audit ledger entry."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    operation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    drive_id: Mapped[int | None] = mapped_column(Integer, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


class TestRun(Base):
    __tablename__ = "test_runs"
    __test__ = False  # not a pytest class
    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_fixture: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actual: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_details: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    executed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

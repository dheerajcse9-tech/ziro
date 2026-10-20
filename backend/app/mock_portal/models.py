"""Tables owned exclusively by the LOCAL MOCK employer drive portal.

They share the database with PlacementOS only for deployment simplicity. The
core application never reads or writes these tables directly: it can only see
the portal through the HTTP adapter, exactly like an external system.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime, utcnow


class MockPortalForm(Base):
    __tablename__ = "mock_portal_forms"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_ref: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    employer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    drive_title: Mapped[str] = mapped_column(String(200), nullable=False)
    published_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_text: Mapped[str] = mapped_column(String(200), nullable=False)
    required_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_open: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class MockPortalSubmission(Base):
    __tablename__ = "mock_portal_submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("mock_portal_forms.id", ondelete="CASCADE"), nullable=False)
    receipt_no: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    client_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(200), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fault_applied: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("form_id", "client_reference", name="uq_mock_submission_ref"),)


class MockPortalCandidate(Base):
    __tablename__ = "mock_portal_candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("mock_portal_forms.id", ondelete="CASCADE"), nullable=False)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("mock_portal_submissions.id", ondelete="CASCADE"), nullable=False
    )
    roll_number: Mapped[str] = mapped_column(String(40), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    branch: Mapped[str | None] = mapped_column(String(40))
    cgpa: Mapped[str | None] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(String(255))


class MockPortalFault(Base):
    """Fault injection switch used by reliability tests and the Test Lab."""

    __tablename__ = "mock_portal_faults"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_ref: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(40), nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

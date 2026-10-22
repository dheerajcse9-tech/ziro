"""Runtime-configurable institutional settings (explicit, not invented policy)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import AppSetting

DEFAULTS: dict[str, Any] = {
    "institution_name": "Demo Institute of Technology (synthetic)",
    "timezone": None,  # filled from env DEFAULT_TIMEZONE
    "schedule_buffer_minutes": 0,
    "attendance_retention_days": 180,
    "attendance_fields": ["roll_number", "full_name", "branch"],
    # Company email addresses whose recruitment emails are processed without a sender review.
    "trusted_company_senders": [],
    # Response Reader Agent applies values students submit on their personal form (marked self-reported).
    # When false, an officer verifies each submission before it is applied.
    "auto_apply_student_data": True,
    # Reminder Agent timing (hours before a form closes / before the drive starts).
    "reminder_hours_before_deadline": 24,
    "reminder_hours_before_drive": 24,
    # The Mailbox Agent ignores emails received before this time (ISO, UTC). Set when the data is (re)loaded,
    # so a reset does not re-read company emails that were already handled. None = the last INBOX_LOOKBACK_DAYS.
    "mailbox_read_since": None,
}


def get_all(db: Session) -> dict[str, Any]:
    values = dict(DEFAULTS)
    values["timezone"] = get_settings().default_timezone
    for row in db.query(AppSetting).all():
        values[row.key] = row.value
    return values


def get(db: Session, key: str) -> Any:
    return get_all(db).get(key)


def set_value(db: Session, key: str, value: Any) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))

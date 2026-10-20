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

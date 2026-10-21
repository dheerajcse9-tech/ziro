"""Attendance sheet generation from a VERIFIED upload only.

Rows are the intersection of the approved roster and the IDs observed at the
destination, with consent re-checked at generation time. Only the minimum
fields configured in settings are included.
"""
from __future__ import annotations

import csv
import io
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.core.errors import NotFound, PreconditionFailed
from app.core.hashing import sha256_hex
from app.db.base import utcnow
from app.domain.normalize import BRANCH_LABELS
from app.domain.schedule import to_local
from app.models.entities import AttendanceSheet, Student
from app.models.enums import ActorType, VerificationStatus
from app.services import app_settings
from app.services.consent import current_consent
from app.services.uploads import get_upload
from app.services.verification import cumulative_submitted, latest_verification

ALLOWED_FIELDS = {"roll_number", "full_name", "branch", "email"}


def generate(db: Session, upload_id: int, *, actor: str, run_id: int | None = None) -> AttendanceSheet:
    op = get_upload(db, upload_id)
    verification = latest_verification(db, op.id)
    if verification is None or verification.status != VerificationStatus.VERIFIED:
        raise PreconditionFailed("Attendance sheets can only be generated from a VERIFIED upload",
                                 details={"verification_status": verification.status if verification else None})
    drive = op.drive
    observed = set(verification.observed_snapshot.get("observed_roll_numbers", []))
    rolls = [r for r in cumulative_submitted(db, op) if r in observed]  # whole drive roster, incl. supplements
    students = {s.roll_number: s for s in db.scalars(select(Student).where(Student.roll_number.in_(rolls)))}
    consent = current_consent(db, [s.id for s in students.values()], "SHARE_WITH_EMPLOYERS")
    excluded = [{"roll_number": r, "reason": "Consent no longer granted"} for r in rolls
                if not consent.get(students[r].id, {}).get("granted")]
    excluded_rolls = {e["roll_number"] for e in excluded}
    final = [r for r in rolls if r not in excluded_rolls]

    fields = [f for f in app_settings.get(db, "attendance_fields") or [] if f in ALLOWED_FIELDS] or ["roll_number", "full_name"]
    if "roll_number" not in fields:
        fields.insert(0, "roll_number")
    local_start = to_local(drive.starts_at, drive.timezone)
    local_end = to_local(drive.ends_at, drive.timezone)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["S.No", *[f.replace("_", " ").title() for f in fields], "Drive", "Employer", "Date", "Slot",
                "Attendance (P/A)", "Signature", "Remarks"])
    for i, r in enumerate(final, start=1):
        s = students[r]
        values = [BRANCH_LABELS.get(s.branch, s.branch or "") if f == "branch" else getattr(s, f) or "" for f in fields]
        w.writerow([i, *values, drive.title, drive.employer.name, local_start.strftime("%d %b %Y"),
                    f"{local_start:%H:%M}-{local_end:%H:%M} {drive.timezone}", "", "", ""])
    content = buf.getvalue()
    sheet = AttendanceSheet(
        drive_id=drive.id, upload_id=op.id, verification_id=verification.id,
        file_name=f"attendance_drive{drive.id}_{local_start:%Y%m%d}_{op.operation_key[:8]}.csv",
        row_count=len(final), roll_numbers=final, excluded_at_generation=excluded, content_csv=content,
        sha256=sha256_hex(content.encode()), generated_by=actor,
    )
    db.add(sheet)
    db.flush()
    ledger.record(db, actor_type=ActorType.AGENT if run_id else ActorType.USER, actor_id=actor,
                  action="ATTENDANCE_GENERATED", entity_type="attendance_sheet", entity_id=sheet.id,
                  operation_id=op.operation_key, run_id=run_id, drive_id=drive.id,
                  details={"rows": len(final), "excluded": excluded, "sha256": sheet.sha256, "fields": fields})
    db.commit()
    return sheet


def get_sheet(db: Session, sheet_id: int) -> AttendanceSheet:
    sheet = db.get(AttendanceSheet, sheet_id)
    if sheet is None:
        raise NotFound("Attendance sheet not found")
    return sheet


def to_xlsx(sheet: AttendanceSheet) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    if sheet.content_csv is None:
        raise PreconditionFailed("Sheet content was purged under the retention policy")
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    for row in csv.reader(io.StringIO(sheet.content_csv)):
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(10, min(40, max(len(str(c.value or "")) for c in col) + 2))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def purge_expired(db: Session, *, actor: str) -> int:
    days = int(app_settings.get(db, "attendance_retention_days") or 180)
    cutoff = utcnow() - timedelta(days=days)
    purged = 0
    for sheet in db.scalars(select(AttendanceSheet).where(AttendanceSheet.created_at < cutoff,
                                                         AttendanceSheet.content_csv.is_not(None))):
        sheet.content_csv = None
        sheet.purged_at = utcnow()
        purged += 1
    ledger.record(db, actor_type=ActorType.USER, actor_id=actor, action="RETENTION_PURGE",
                  details={"retention_days": days, "sheets_purged": purged})
    db.commit()
    return purged

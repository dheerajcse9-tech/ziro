"""Reminder Agent: one reminder per student per situation, never repeated.

  * consent form not answered, and the form closes within N hours
  * personal (missing details) form not submitted, and it closes within N hours
  * shortlisted for a drive that starts within N hours

N comes from Settings (reminder_hours_before_deadline / _before_drive).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import ledger
from app.db.base import utcnow
from app.domain.schedule import to_local
from app.models.entities import Drive, DriveAssignment, StudentRequest
from app.models.enums import ActorType, AssignmentStatus, DriveStatus, EmailCategory, RequestKind, RequestStatus
from app.services import app_settings, mailer, outreach
from app.services.outreach import FIELD_LABEL, _first_name, _link, _slot

ACTOR = "agent:reminder"


def run(db: Session) -> dict[str, Any]:
    now = utcnow()
    before_deadline = timedelta(hours=int(app_settings.get(db, "reminder_hours_before_deadline") or 24))
    before_drive = timedelta(hours=int(app_settings.get(db, "reminder_hours_before_drive") or 24))
    counts = {"consent": 0, "details": 0, "drive": 0}

    for req in db.scalars(select(StudentRequest).where(
            StudentRequest.status == RequestStatus.SENT, StudentRequest.expires_at > now,
            StudentRequest.expires_at <= now + before_deadline)):
        s, drive = req.student, req.drive
        tz = drive.timezone if drive else "UTC"
        closes = to_local(req.expires_at, tz)
        if req.kind == RequestKind.DRIVE_INVITATION:
            subject = f"Reminder: answer the consent form for {drive.title} by {closes:%d %b %H:%M}"
            what = f"you have not yet answered the consent form for {drive.title} ({drive.employer.name})"
            key, bucket = f"reminder:consent:{req.id}", "consent"
        else:
            fields = ", ".join(FIELD_LABEL.get(f, f) for f in req.requested_fields)
            subject = f"Reminder: your placement form closes on {closes:%d %b %H:%M}"
            what = f"we still need: {fields}" + (f" (for {drive.title})" if drive else "")
            key, bucket = f"reminder:details:{req.id}", "details"
        msg = mailer.send(db, category=EmailCategory.REMINDER, to_address=s.email, to_name=s.full_name,
                          subject=f"{subject} {outreach.ref(req)}",
                          body=(f"Dear {_first_name(s)},\n\nA reminder that {what}.\n\n  {_link(req.token)}\n\n"
                                f"The form closes on {closes:%d %b %Y at %H:%M}. After that it can no longer be "
                                f"submitted.\n\n— Placement Cell"),
                          dedupe_key=key, sent_by=ACTOR, student_id=s.id, drive_id=drive.id if drive else None,
                          request_id=req.id)
        counts[bucket] += msg.created_now

    upcoming = db.scalars(select(Drive).where(Drive.status.in_([DriveStatus.OPEN, DriveStatus.ROSTER_SUBMITTED]),
                                              Drive.starts_at > now, Drive.starts_at <= now + before_drive))
    for drive in upcoming:
        for a in db.scalars(select(DriveAssignment).where(DriveAssignment.drive_id == drive.id,
                                                          DriveAssignment.status == AssignmentStatus.CONFIRMED)):
            s = a.student
            msg = mailer.send(db, category=EmailCategory.REMINDER, to_address=s.email, to_name=s.full_name,
                              subject=f"Reminder: {drive.title} on {to_local(drive.starts_at, drive.timezone):%d %b at %H:%M}",
                              body=(f"Dear {_first_name(s)},\n\nYou are shortlisted for {drive.title}.\n\n"
                                    f"  Date & time: {_slot(drive)}\n  Venue: {drive.location or 'to be announced'}\n\n"
                                    f"Please be on time and carry your college ID card.\n\n— Placement Cell"),
                              dedupe_key=f"reminder:drive:{drive.id}:{s.id}", sent_by=ACTOR, student_id=s.id,
                              drive_id=drive.id)
            counts["drive"] += msg.created_now
    if any(counts.values()):
        ledger.record(db, actor_type=ActorType.AGENT, actor_id=ACTOR, action="REMINDERS_SENT", details=counts)
    db.commit()
    return counts

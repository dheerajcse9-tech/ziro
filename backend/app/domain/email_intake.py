"""Extract drive details from a company's recruitment email, and validate them.

The email is untrusted *data*. Extraction only fills a closed set of fields
(company, role, date, time, venue, deadlines, criteria); nothing in the email
can change what the system does. Instruction-like phrases are flagged for the
drive upload officer and otherwise ignored.

Dates are read day-first (25/11/2026 is 25 November), the usual convention in
Indian recruitment emails. Every value records the line it came from so a
human can check it against the original email.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.domain.criteria_parser import parse_criteria_text

DEFAULT_DURATION = timedelta(hours=3)

_MONTHS = {m: i for i, names in enumerate([
    (), ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
    ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
    ("dec", "december")]) for m in names}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+" + _MONTH_RE + r"\.?,?[\s\-]+(\d{4})\b", re.I), "d_mon_y"),
    (re.compile(r"\b" + _MONTH_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I), "mon_d_y"),
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b"), "dmy"),
]
_TIME = r"(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?"
_TIME_RANGE = re.compile(_TIME + r"\s*(?:-|–|—|to|till|until)\s*" + _TIME, re.I)
_SINGLE_TIME = re.compile(r"\b" + _TIME + r"\b", re.I)

# Field label -> accepted spellings at the start of a line ("Role: ...", "Last date to register - ...").
_LABELS: dict[str, list[str]] = {
    "company": ["company name", "company", "organisation", "organization", "employer", "recruiter"],
    "role": ["job role", "job title", "role", "position", "designation", "profile", "opening"],
    "registration_deadline": ["registration deadline", "last date for student registration", "last date for registration",
                              "last date to register", "last date to apply", "registration closes on",
                              "registration closes", "registrations close", "consent deadline", "apply by", "deadline"],
    "roster_due": ["list required by", "shortlist required by", "send the list by", "student list by",
                   "share the list by", "roster due"],
    "drive_date": ["date of drive", "drive date", "date of the drive", "interview date", "date"],
    "time": ["reporting time", "timings", "timing", "time"],
    "venue": ["venue", "location", "place", "mode"],
    "ctc": ["ctc", "package", "salary", "stipend", "compensation"],
    "batch": ["eligible batch", "batch", "year of passing", "passing year", "graduation year"],
}
_LABEL_RE = {key: re.compile(r"^\s*(?:[-*•]\s*)?(?:" + "|".join(re.escape(x) for x in names) + r")\s*(?:\([^)]*\))?\s*[:\-–]\s*(.+)$", re.I)
             for key, names in _LABELS.items()}
_ROSTER_SENTENCE = re.compile(r"(?:share|send|submit|provide|forward)[^.\n]{0,80}?\b(?:list|shortlist|names|roster|details)\b[^.\n]{0,60}?\bby\b\s*([^.\n]+)", re.I)
_DEADLINE_SENTENCE = re.compile(r"(?:register|registration|apply|consent)[^.\n]{0,40}?\b(?:by|before|on or before)\b\s*([^.\n]+)", re.I)
_SUBJECT_SPLIT = re.compile(r"\s+[–—|:-]\s+|\s*[|:]\s*")
_SUBJECT_NOISE = re.compile(r"^(?:re|fw|fwd)\s*:\s*|campus\s+(?:recruitment\s+)?drive|campus\s+hiring|placement\s+drive|"
                            r"recruitment\s+drive|invitation\s+for|hiring", re.I)


def _month(name: str) -> int:
    name = name.lower()
    return _MONTHS.get(name) or _MONTHS[name[:3]]


def parse_date(text: str) -> date | None:
    for pattern, kind in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            if kind == "ymd":
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if kind == "d_mon_y":
                return date(int(m.group(3)), _month(m.group(2)), int(m.group(1)))
            if kind == "mon_d_y":
                return date(int(m.group(3)), _month(m.group(1)), int(m.group(2)))
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except (ValueError, KeyError):
            continue
    return None


def _to_time(h: str, mm: str | None, ampm: str | None, fallback_ampm: str | None = None) -> time | None:
    hour, minute = int(h), int(mm or 0)
    marker = (ampm or fallback_ampm or "").replace(".", "").lower()
    if marker == "pm" and hour < 12:
        hour += 12
    elif marker == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def parse_time_range(text: str) -> tuple[time | None, time | None]:
    m = _TIME_RANGE.search(text)
    if m:
        h1, m1, a1, h2, m2, a2 = m.groups()
        if a1 or a2 or m1 or m2:  # avoid reading "2026-11" style numbers as times
            start = _to_time(h1, m1, a1, None if a1 else a2)
            # "10 AM to 1 PM": a start without a marker takes the end's only when that keeps start < end
            if start and not a1 and a2:
                end_probe = _to_time(h2, m2, a2)
                if end_probe and start >= end_probe:
                    start = _to_time(h1, m1, "am")
            return start, _to_time(h2, m2, a2)
    for m in _SINGLE_TIME.finditer(text):
        h, mm, ampm = m.groups()
        if ampm or mm:
            return _to_time(h, mm, ampm), None
    return None, None


@dataclass
class Extraction:
    company: str | None = None
    role: str | None = None
    drive_date: str | None = None  # ISO date, local to the institution
    start_time: str | None = None  # HH:MM
    end_time: str | None = None
    venue: str | None = None
    registration_deadline: str | None = None  # ISO local datetime
    roster_due: str | None = None
    ctc: str | None = None
    graduation_years: list[int] = field(default_factory=list)
    min_cgpa: float | None = None
    backlog_policy: str | None = None
    max_active_backlogs: int | None = None
    allowed_branches: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    suspicious_phrases: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)  # field -> source line
    mode: str = "RULES"

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _deadline(value: str) -> tuple[str | None, str | None]:
    d = parse_date(value)
    if d is None:
        return None, None
    start, _ = parse_time_range(value)
    return datetime.combine(d, start or time(23, 59)).isoformat(timespec="minutes"), value.strip()


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().strip(".").strip()


def extract(subject: str, body: str) -> Extraction:
    """Rule-based extraction. Never raises on odd input; missing values stay None."""
    out = Extraction()
    lines = [ln.rstrip() for ln in (body or "").splitlines()]
    found: dict[str, str] = {}
    for ln in lines:
        for key, rx in _LABEL_RE.items():
            if key in found:
                continue
            m = rx.match(ln)
            if m:
                found[key] = m.group(1)
                out.evidence[key] = ln.strip()
                break  # one label per line; earlier keys are more specific

    if "company" in found:
        out.company = _clean(found["company"])
    if "role" in found:
        out.role = _clean(found["role"])
    if not out.company or not out.role:  # "Campus Drive – Orbitra Technologies – Associate Software Engineer"
        parts = [_clean(_SUBJECT_NOISE.sub("", p)) for p in _SUBJECT_SPLIT.split(subject or "")]
        parts = [p for p in parts if len(p) > 1]
        if not out.company and parts:
            out.company = parts[0]
            out.evidence["company"] = f"Subject: {subject}"
        if not out.role and len(parts) > 1:
            out.role = parts[-1]
            out.evidence["role"] = f"Subject: {subject}"

    if "drive_date" in found:
        d = parse_date(found["drive_date"])
        if d:
            out.drive_date = d.isoformat()
        start, end = parse_time_range(found["drive_date"])  # "Date: 12 Nov 2026, 10 AM - 1 PM"
        if start:
            out.start_time, out.end_time = start.strftime("%H:%M"), end.strftime("%H:%M") if end else None
            out.evidence.setdefault("time", out.evidence["drive_date"])
    if "time" in found:
        start, end = parse_time_range(found["time"])
        if start:
            out.start_time = start.strftime("%H:%M")
            out.end_time = end.strftime("%H:%M") if end else None

    if "venue" in found:
        out.venue = _clean(found["venue"])
    if "ctc" in found:
        out.ctc = _clean(found["ctc"])
    if "batch" in found:
        out.graduation_years = sorted({int(y) for y in re.findall(r"\b(20\d{2})\b", found["batch"])})

    if "registration_deadline" in found:
        out.registration_deadline, _ = _deadline(found["registration_deadline"])
    if out.registration_deadline is None:
        for ln in lines:
            m = _DEADLINE_SENTENCE.search(ln)
            if m and "list" not in ln.lower():
                out.registration_deadline, _ = _deadline(m.group(1))
                if out.registration_deadline:
                    out.evidence["registration_deadline"] = ln.strip()
                    break
    if "roster_due" in found:
        out.roster_due, _ = _deadline(found["roster_due"])
    if out.roster_due is None:
        for ln in lines:
            m = _ROSTER_SENTENCE.search(ln)
            if m:
                out.roster_due, _ = _deadline(m.group(1))
                if out.roster_due:
                    out.evidence["roster_due"] = ln.strip()
                    break

    criteria = parse_criteria_text(body or "")
    out.min_cgpa = criteria.min_cgpa
    out.backlog_policy = criteria.backlog_policy
    out.max_active_backlogs = criteria.max_active_backlogs
    out.allowed_branches = criteria.branches
    out.ambiguities = list(criteria.ambiguities)
    out.suspicious_phrases = criteria.suspicious_phrases
    for ln in lines:
        low = ln.lower()
        if "cgpa" in low or "cpi" in low or "backlog" in low:
            out.evidence.setdefault("criteria", ln.strip())
        if re.search(r"branch|discipline|stream", low):
            out.evidence.setdefault("branches", ln.strip())
    return out


# --------------------------------------------------------------------------- validation

FIELD_LABEL = {
    "company": "Company name", "role": "Role", "drive_date": "Drive date", "start_time": "Start time",
    "end_time": "End time", "venue": "Venue", "registration_deadline": "Registration deadline",
    "roster_due": "Student list due", "criteria": "Eligibility criteria", "sender": "Sender",
    "content": "Email content", "duplicate": "Duplicate",
}


def issue(field_: str, code: str, message: str, level: str) -> dict[str, Any]:
    """level: ERROR (must be corrected), REVIEW (a person must confirm), INFO (noted, does not stop)."""
    return {"field": field_, "code": code, "message": message, "level": level}


def effective(extraction: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(extraction or {})
    for k, v in (overrides or {}).items():
        merged[k] = v
    return merged


def drive_interval(data: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """Naive local start/end of the drive, or None when date/start time are missing."""
    if not data.get("drive_date") or not data.get("start_time"):
        return None
    d = date.fromisoformat(data["drive_date"])
    start = datetime.combine(d, time.fromisoformat(data["start_time"]))
    end = datetime.combine(d, time.fromisoformat(data["end_time"])) if data.get("end_time") else start + DEFAULT_DURATION
    return start, end


def validate(data: dict[str, Any], now_local: datetime) -> list[dict[str, Any]]:
    """Checks that do not need the database. `now_local` is naive wall-clock time in the drive's timezone."""
    out: list[dict[str, Any]] = []
    for key in ("company", "role"):
        if not (data.get(key) or "").strip():
            out.append(issue(key, "MISSING", f"{FIELD_LABEL[key]} not found in the email", "ERROR"))
    if not data.get("drive_date"):
        out.append(issue("drive_date", "MISSING", "Drive date not found in the email", "ERROR"))
    if not data.get("start_time"):
        out.append(issue("start_time", "MISSING", "Drive start time not found in the email", "ERROR"))
    elif not data.get("end_time"):
        out.append(issue("end_time", "ASSUMED", "No end time stated; assuming the drive lasts 3 hours", "INFO"))
    interval = None
    try:
        interval = drive_interval(data)
    except ValueError:
        out.append(issue("drive_date", "INVALID", "Drive date or time is not a valid date/time", "ERROR"))
    if interval:
        start, end = interval
        if end <= start:
            out.append(issue("end_time", "INVALID", "The drive must end after it starts", "ERROR"))
        if start <= now_local:
            out.append(issue("drive_date", "PAST", f"The drive date {start:%d %b %Y %H:%M} has already passed", "ERROR"))
    deadline = None
    if not data.get("registration_deadline"):
        out.append(issue("registration_deadline", "MISSING",
                         "No registration deadline stated; ask the company when students must register by", "ERROR"))
    else:
        try:
            deadline = datetime.fromisoformat(data["registration_deadline"])
        except ValueError:
            out.append(issue("registration_deadline", "INVALID", "Registration deadline is not a valid date", "ERROR"))
    if deadline:
        if deadline <= now_local:
            out.append(issue("registration_deadline", "PAST", f"The registration deadline {deadline:%d %b %Y %H:%M} has already passed", "ERROR"))
        if interval and deadline > interval[0]:
            out.append(issue("registration_deadline", "AFTER_DRIVE", "The registration deadline is after the drive starts", "ERROR"))
    if data.get("roster_due") and interval:
        try:
            due = datetime.fromisoformat(data["roster_due"])
            if due > interval[0]:
                out.append(issue("roster_due", "AFTER_DRIVE", "The company wants the student list after the drive starts", "INFO"))
            if deadline and due < deadline:
                out.append(issue("roster_due", "BEFORE_DEADLINE",
                                 "The student list is due before student registration closes; late registrations will need a supplementary list",
                                 "INFO"))
        except ValueError:
            out.append(issue("roster_due", "INVALID", "Student list due date is not a valid date", "ERROR"))
    cgpa = data.get("min_cgpa")
    if cgpa is not None and not 0 <= float(cgpa) <= 10:
        out.append(issue("criteria", "INVALID", "Minimum CGPA must be between 0 and 10", "ERROR"))
    if cgpa is None:
        out.append(issue("criteria", "NO_CGPA", "No minimum CGPA stated; every CGPA will qualify", "INFO"))
    if not data.get("backlog_policy"):
        out.append(issue("criteria", "NO_BACKLOG_RULE", "No backlog rule stated; students with backlogs will not be excluded", "INFO"))
    for a in data.get("ambiguities") or []:
        out.append(issue("criteria", "AMBIGUOUS", a, "REVIEW"))
    if data.get("suspicious_phrases"):
        out.append(issue("content", "SUSPICIOUS", "The email contains instruction-like text, which was ignored: "
                         + "; ".join(f"“{p}”" for p in data["suspicious_phrases"]), "REVIEW"))
    return out

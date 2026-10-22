"""Rule-based reading of company recruitment emails, and the validation that decides when a person must look."""
from datetime import date, datetime, time

import pytest

from app.agent.gemini import to_gemini_schema
from app.domain.email_intake import extract, parse_date, parse_time_range, validate


@pytest.mark.parametrize("text,expected", [
    ("12 November 2026", date(2026, 11, 12)),
    ("12th Nov, 2026", date(2026, 11, 12)),
    ("Nov 12, 2026", date(2026, 11, 12)),
    ("25/11/2026", date(2026, 11, 25)),  # day-first
    ("05-12-2026", date(2026, 12, 5)),
    ("2026-11-12", date(2026, 11, 12)),
    ("Sept 3, 2026", date(2026, 9, 3)),
    ("no date here", None),
    ("31/02/2026", None),
])
def test_dates(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text,start,end", [
    ("10:00 AM to 1:00 PM", time(10), time(13)),
    ("10 AM - 1 PM", time(10), time(13)),
    ("14:00 - 17:00", time(14), time(17)),
    ("9:30 AM – 12:30 PM", time(9, 30), time(12, 30)),
    ("2 - 5 PM", time(14), time(17)),
    ("Reporting at 9:30 AM", time(9, 30), None),
])
def test_times(text, start, end):
    assert parse_time_range(text) == (start, end)


def test_free_form_email_with_sentences_instead_of_labels():
    x = extract("Campus Drive - Acme Corp - Data Engineer", """Hi team,
Acme Corp would like to visit on 20 December 2026 from 10 AM to 1 PM at the Main Auditorium.
Students with CGPA 7.5 and above and no backlogs from CSE and IT can apply.
Branches: CSE, IT
Students must register by 10 December 2026 5 PM.
Please send the list of interested students by 12 December 2026.""")
    assert (x.company, x.role) == ("Acme Corp", "Data Engineer")  # from the subject
    assert x.evidence["company"].startswith("Subject:")
    assert x.min_cgpa == 7.5 and x.backlog_policy == "NO_ACTIVE_BACKLOGS" and x.allowed_branches == ["CSE", "IT"]
    assert x.registration_deadline == "2026-12-10T17:00" and x.roster_due == "2026-12-12T23:59"
    assert x.drive_date is None  # no "Date:" label: a person is asked rather than guessing from prose


def test_validation_levels():
    now = datetime(2026, 10, 22, 9, 0)
    good = {"company": "A", "role": "B", "drive_date": "2026-11-20", "start_time": "10:00", "end_time": "13:00",
            "registration_deadline": "2026-11-10T17:00", "min_cgpa": 7.0, "backlog_policy": "NO_ACTIVE_BACKLOGS"}
    assert validate(good, now) == []
    codes = {(i["field"], i["code"], i["level"]) for i in validate({**good, "registration_deadline": "2026-11-21T10:00",
                                                                     "end_time": None, "min_cgpa": None}, now)}
    assert ("registration_deadline", "AFTER_DRIVE", "ERROR") in codes
    assert ("end_time", "ASSUMED", "INFO") in codes and ("criteria", "NO_CGPA", "INFO") in codes
    assert any(c[1] == "PAST" for c in {(i["field"], i["code"]) for i in validate({**good, "drive_date": "2026-10-01"}, now)})
    assert validate({**good, "suspicious_phrases": ["skip the consent"]}, now)[0]["level"] == "REVIEW"


def test_gemini_schema_conversion():
    schema = {"type": "object", "additionalProperties": False, "required": ["a"], "properties": {
        "a": {"type": ["string", "null"], "enum": ["X", "Y", None]},
        "b": {"anyOf": [{"type": "integer"}, {"type": "null"}], "description": "n"},
        "c": {"type": "array", "items": {"type": "string"}}}}
    out = to_gemini_schema(schema)
    assert out == {"type": "OBJECT", "required": ["a"], "properties": {
        "a": {"type": "STRING", "enum": ["X", "Y"], "nullable": True},
        "b": {"type": "INTEGER", "nullable": True, "description": "n"},
        "c": {"type": "ARRAY", "items": {"type": "STRING"}}}}

"""Deterministic normalisation and validation of raw student fields."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Canonical branch codes and the raw spellings each accepts (compared after
# lower-casing and collapsing punctuation/whitespace).
BRANCH_ALIASES: dict[str, list[str]] = {
    "CSE": ["cse", "cs", "computer science", "computer science and engineering",
            "computer science engineering", "comp sci", "btech cse", "b tech cse", "cse core"],
    "IT": ["it", "information technology", "info tech"],
    "AIDS": ["aids", "ai ds", "ai and ds", "artificial intelligence and data science", "ai data science"],
    "ECE": ["ece", "ec", "electronics and communication", "electronics and communication engineering",
            "electronics communication", "e and c", "electronics"],
    "EEE": ["eee", "ee", "electrical", "electrical and electronics", "electrical and electronics engineering",
            "electrical engineering"],
    "ME": ["me", "mech", "mechanical", "mechanical engineering"],
    "CE": ["ce", "civil", "civil engineering"],
    "CHE": ["che", "chemical", "chemical engineering"],
}
BRANCH_LABELS: dict[str, str] = {
    "CSE": "Computer Science & Engineering",
    "IT": "Information Technology",
    "AIDS": "AI & Data Science",
    "ECE": "Electronics & Communication",
    "EEE": "Electrical & Electronics",
    "ME": "Mechanical Engineering",
    "CE": "Civil Engineering",
    "CHE": "Chemical Engineering",
}

_ALIAS_INDEX = {alias: code for code, aliases in BRANCH_ALIASES.items() for alias in aliases}
_ROLL_RE = re.compile(r"^[A-Z0-9][A-Z0-9/-]{3,39}$")


def _squash(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_branch(raw: str | None) -> str | None:
    """Return the canonical branch code, or None when the value is unrecognised."""
    if raw is None or not str(raw).strip():
        return None
    squashed = _squash(str(raw))
    if squashed.upper() in BRANCH_ALIASES:
        return squashed.upper()
    return _ALIAS_INDEX.get(squashed)


def normalize_roll(raw: str | None) -> str | None:
    if raw is None:
        return None
    roll = re.sub(r"\s+", "", str(raw)).upper()
    if roll.endswith(".0") and roll[:-2].isdigit():  # spreadsheets turn numeric IDs into floats
        roll = roll[:-2]
    return roll or None


def is_valid_roll(roll: str | None) -> bool:
    return bool(roll and _ROLL_RE.match(roll))


def clean_name(raw: str | None) -> str | None:
    if raw is None:
        return None
    name = re.sub(r"\s+", " ", str(raw)).strip()
    return name or None


@dataclass
class FieldIssue:
    field: str
    code: str
    message: str
    severity: str = "ERROR"  # ERROR blocks the row; WARNING is imported and flagged

    def as_dict(self) -> dict:
        return {"field": self.field, "code": self.code, "message": self.message, "severity": self.severity}


@dataclass
class ParsedValue:
    value: object | None
    issues: list[FieldIssue] = field(default_factory=list)


def parse_cgpa(raw: object) -> ParsedValue:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ParsedValue(None, [FieldIssue("cgpa", "MISSING", "CGPA is missing", "WARNING")])
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        return ParsedValue(None, [FieldIssue("cgpa", "INVALID", f"CGPA '{raw}' is not a number")])
    if value < 0 or value > 10:
        return ParsedValue(None, [FieldIssue("cgpa", "OUT_OF_RANGE", f"CGPA {value} is outside 0-10")])
    return ParsedValue(float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))


def parse_backlogs(raw: object) -> ParsedValue:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ParsedValue(None, [FieldIssue("active_backlogs", "MISSING", "Backlog count is missing", "WARNING")])
    text = str(raw).strip()
    if text.lower() in {"none", "nil", "no"}:
        return ParsedValue(0)
    try:
        number = Decimal(text)
    except InvalidOperation:
        return ParsedValue(None, [FieldIssue("active_backlogs", "INVALID", f"Backlogs '{raw}' is not a number")])
    if number != number.to_integral_value() or number < 0:
        return ParsedValue(None, [FieldIssue("active_backlogs", "INVALID", f"Backlogs '{raw}' must be a whole number >= 0")])
    return ParsedValue(int(number))


def parse_consent(raw: object) -> ParsedValue:
    """Consent must be explicit. Blank or unrecognised values are treated as *not granted*."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ParsedValue(None, [FieldIssue("consent", "MISSING", "Consent not recorded; treated as not granted", "WARNING")])
    text = str(raw).strip().lower()
    if text in {"yes", "y", "true", "1", "granted", "consented", "given"}:
        return ParsedValue(True)
    if text in {"no", "n", "false", "0", "revoked", "denied", "withdrawn"}:
        return ParsedValue(False)
    return ParsedValue(None, [FieldIssue("consent", "AMBIGUOUS", f"Consent value '{raw}' is ambiguous; treated as not granted", "WARNING")])


def parse_year(raw: object) -> ParsedValue:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ParsedValue(None)
    try:
        year = int(Decimal(str(raw).strip()))
    except (InvalidOperation, ValueError):
        return ParsedValue(None, [FieldIssue("graduation_year", "INVALID", f"Year '{raw}' is invalid", "WARNING")])
    if year < 2000 or year > 2100:
        return ParsedValue(None, [FieldIssue("graduation_year", "OUT_OF_RANGE", f"Year {year} is implausible", "WARNING")])
    return ParsedValue(year)

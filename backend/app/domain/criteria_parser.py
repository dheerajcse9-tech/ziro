"""Deterministic extraction of eligibility criteria from retrieved employer text.

Retrieved documents are *data*, never instructions. This parser only looks for
a closed set of patterns (CGPA threshold, backlog policy, branch list). It also
flags instruction-like phrases so a human can review suspicious sources; such
phrases never change what the system does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.normalize import normalize_branch
from app.models.enums import BacklogPolicy

_CGPA_PATTERNS = [
    re.compile(r"(?:cgpa|cpi|gpa)\s*(?:of\s*)?(>=|≥|=>|at least|minimum(?: of)?|min\.?|above|>)\s*(\d+(?:\.\d+)?)", re.I),
    re.compile(r"(?:minimum|min\.?)\s*(?:cgpa|cpi|gpa)\s*(?:of|:)?\s*(\d+(?:\.\d+)?)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:cgpa|cpi)\s*(?:and above|or above|\+)", re.I),
    re.compile(r"(?:cgpa|cpi|gpa)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*(?:and above|or above|or more|&\s*above|\+)", re.I),
]
_NO_BACKLOG = re.compile(r"\bno\s+(?:active\s+|current\s+|standing\s+)?backlogs?\b|\bzero\s+(?:active\s+)?backlogs?\b|\bbacklogs?\s*:\s*(?:none|0|not allowed)\b", re.I)
_MAX_BACKLOG = re.compile(r"(?:max(?:imum)?|up to|upto|at most|<=)\s*(\d+)\s*(?:active\s+)?backlogs?", re.I)
_BRANCH_LINE = re.compile(r"(?:branches?|disciplines?|streams?)\s*(?:eligible|allowed)?\s*[:\-]\s*([^\n.;]+)", re.I)
_INJECTION = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above) instructions|disregard (?:the )?(?:rules|instructions)|"
    r"you are now|system prompt|auto-?approve|approve (?:this|the) (?:roster|upload) automatically|"
    r"skip (?:the )?(?:approval|consent|verification)|include all students",
    re.I,
)


@dataclass
class ParsedCriteria:
    min_cgpa: float | None = None
    backlog_policy: str | None = None
    max_active_backlogs: int | None = None
    branches: list[str] = field(default_factory=list)
    unrecognised_branches: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    suspicious_phrases: list[str] = field(default_factory=list)
    strict_greater: bool = False

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def parse_criteria_text(text: str) -> ParsedCriteria:
    out = ParsedCriteria()
    if not text:
        out.ambiguities.append("No published criteria text")
        return out

    thresholds: set[float] = set()
    for pattern in _CGPA_PATTERNS:
        for m in pattern.finditer(text):
            groups = m.groups()
            op = groups[0] if len(groups) == 2 else None
            value = float(groups[-1])
            if 0 < value <= 10:
                thresholds.add(value)
                if op and op.strip().lower() in {">", "above"}:
                    out.strict_greater = True
    if len(thresholds) == 1:
        out.min_cgpa = thresholds.pop()
    elif len(thresholds) > 1:
        out.ambiguities.append(f"Multiple CGPA thresholds stated: {sorted(thresholds)}")
    if out.strict_greater:
        out.ambiguities.append("CGPA comparison stated as strictly greater-than; confirm whether the threshold is inclusive")

    no_backlog = bool(_NO_BACKLOG.search(text))
    max_match = _MAX_BACKLOG.search(text)
    if no_backlog and max_match:
        out.ambiguities.append("Text states both 'no backlogs' and a maximum backlog count")
    elif no_backlog:
        out.backlog_policy = BacklogPolicy.NO_ACTIVE_BACKLOGS
        out.max_active_backlogs = 0
    elif max_match:
        out.backlog_policy = BacklogPolicy.MAX_ALLOWED
        out.max_active_backlogs = int(max_match.group(1))

    branch_match = _BRANCH_LINE.search(text)
    if branch_match:
        for token in re.split(r",|/|;", branch_match.group(1)):
            token = token.strip()
            if not token:
                continue
            if token.lower() in {"all", "all branches", "any"}:
                out.branches = []
                break
            code = normalize_branch(token)
            if code and code not in out.branches:
                out.branches.append(code)
            elif not code:
                out.unrecognised_branches.append(token)
        if out.unrecognised_branches:
            out.ambiguities.append(f"Unrecognised branch names: {out.unrecognised_branches}")

    out.suspicious_phrases = sorted({m.group(0) for m in _INJECTION.finditer(text)})
    return out


def compare_with_configured(parsed: ParsedCriteria, configured: dict) -> list[dict]:
    """Return a list of discrepancies between published text and configured criteria."""
    diffs: list[dict] = []
    if parsed.min_cgpa is not None and configured.get("min_cgpa") is not None:
        if abs(parsed.min_cgpa - float(configured["min_cgpa"])) > 1e-9:
            diffs.append({"field": "min_cgpa", "published": parsed.min_cgpa, "configured": configured["min_cgpa"]})
    elif parsed.min_cgpa is not None and configured.get("min_cgpa") is None:
        diffs.append({"field": "min_cgpa", "published": parsed.min_cgpa, "configured": None})
    if parsed.backlog_policy and parsed.backlog_policy != configured.get("backlog_policy"):
        diffs.append({"field": "backlog_policy", "published": parsed.backlog_policy, "configured": configured.get("backlog_policy")})
    elif (
        parsed.backlog_policy == BacklogPolicy.MAX_ALLOWED
        and parsed.max_active_backlogs != configured.get("max_active_backlogs")
    ):
        diffs.append({"field": "max_active_backlogs", "published": parsed.max_active_backlogs,
                      "configured": configured.get("max_active_backlogs")})
    if parsed.branches and sorted(parsed.branches) != sorted(configured.get("allowed_branches") or []):
        diffs.append({"field": "allowed_branches", "published": sorted(parsed.branches),
                      "configured": sorted(configured.get("allowed_branches") or [])})
    return diffs

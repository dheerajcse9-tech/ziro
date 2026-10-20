"""Deterministic eligibility rules.

Each applicable criterion is evaluated independently and returns PASS, FAIL,
UNKNOWN (essential evidence missing) or NOT_APPLICABLE. The overall decision is:
  any FAIL      -> INELIGIBLE
  else UNKNOWN  -> UNKNOWN
  else          -> ELIGIBLE
No language model participates in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.models.enums import BacklogPolicy, Decision

PASS, FAIL, UNKNOWN, NOT_APPLICABLE = "PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"


@dataclass(frozen=True)
class CriteriaSpec:
    version: int
    min_cgpa: float | None
    backlog_policy: str
    max_active_backlogs: int | None
    allowed_branches: tuple[str, ...] = ()
    graduation_years: tuple[int, ...] = ()
    required_consent_scope: str = "SHARE_WITH_EMPLOYERS"

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "min_cgpa": self.min_cgpa,
            "cgpa_comparison": ">= (inclusive)",
            "backlog_policy": self.backlog_policy,
            "max_active_backlogs": self.max_active_backlogs,
            "allowed_branches": list(self.allowed_branches),
            "graduation_years": list(self.graduation_years),
            "required_consent_scope": self.required_consent_scope,
        }


@dataclass(frozen=True)
class StudentFacts:
    student_id: int
    roll_number: str
    cgpa: float | None
    active_backlogs: int | None
    branch: str | None
    graduation_year: int | None


@dataclass
class RuleResult:
    rule: str
    outcome: str
    expected: str
    observed: str | None
    reason: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class EligibilityResult:
    decision: str
    rule_results: list[RuleResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)


def _cgpa_rule(facts: StudentFacts, c: CriteriaSpec) -> RuleResult:
    if c.min_cgpa is None:
        return RuleResult("CGPA", NOT_APPLICABLE, "no minimum", _fmt(facts.cgpa), "Employer sets no CGPA minimum")
    expected = f">= {c.min_cgpa:.2f}"
    if facts.cgpa is None:
        return RuleResult("CGPA", UNKNOWN, expected, None, "CGPA missing from roster")
    # Decimal comparison avoids binary float artefacts (e.g. 6.999999 vs 7.0).
    observed = Decimal(str(facts.cgpa)).quantize(Decimal("0.01"))
    threshold = Decimal(str(c.min_cgpa)).quantize(Decimal("0.01"))
    if observed >= threshold:
        return RuleResult("CGPA", PASS, expected, f"{observed}", f"CGPA {observed} meets minimum {threshold}")
    return RuleResult("CGPA", FAIL, expected, f"{observed}", f"CGPA {observed} below minimum {threshold}")


def _backlog_rule(facts: StudentFacts, c: CriteriaSpec) -> RuleResult:
    policy = c.backlog_policy
    if policy == BacklogPolicy.NOT_CONSIDERED:
        return RuleResult("BACKLOGS", NOT_APPLICABLE, "not considered", _fmt(facts.active_backlogs), "Employer does not restrict backlogs")
    limit = 0 if policy == BacklogPolicy.NO_ACTIVE_BACKLOGS else c.max_active_backlogs
    if limit is None:
        return RuleResult("BACKLOGS", UNKNOWN, "limit not specified", _fmt(facts.active_backlogs),
                          "Backlog limit not specified in criteria")
    expected = "0 active backlogs" if limit == 0 else f"<= {limit} active backlogs"
    if facts.active_backlogs is None:
        return RuleResult("BACKLOGS", UNKNOWN, expected, None, "Active backlog count missing from roster")
    if facts.active_backlogs <= limit:
        return RuleResult("BACKLOGS", PASS, expected, str(facts.active_backlogs), f"{facts.active_backlogs} active backlog(s) within limit")
    return RuleResult("BACKLOGS", FAIL, expected, str(facts.active_backlogs),
                      f"{facts.active_backlogs} active backlog(s) exceeds limit of {limit}")


def _branch_rule(facts: StudentFacts, c: CriteriaSpec) -> RuleResult:
    if not c.allowed_branches:
        return RuleResult("BRANCH", NOT_APPLICABLE, "all branches", facts.branch, "Employer accepts all branches")
    expected = "one of " + ", ".join(sorted(c.allowed_branches))
    if facts.branch is None:
        return RuleResult("BRANCH", UNKNOWN, expected, None, "Branch missing or unrecognised")
    if facts.branch in c.allowed_branches:
        return RuleResult("BRANCH", PASS, expected, facts.branch, f"Branch {facts.branch} is eligible")
    return RuleResult("BRANCH", FAIL, expected, facts.branch, f"Branch {facts.branch} not in eligible branches")


def _year_rule(facts: StudentFacts, c: CriteriaSpec) -> RuleResult:
    if not c.graduation_years:
        return RuleResult("GRADUATION_YEAR", NOT_APPLICABLE, "any", _fmt(facts.graduation_year), "No batch restriction")
    expected = "one of " + ", ".join(str(y) for y in sorted(c.graduation_years))
    if facts.graduation_year is None:
        return RuleResult("GRADUATION_YEAR", UNKNOWN, expected, None, "Graduation year missing")
    if facts.graduation_year in c.graduation_years:
        return RuleResult("GRADUATION_YEAR", PASS, expected, str(facts.graduation_year), "Batch is eligible")
    return RuleResult("GRADUATION_YEAR", FAIL, expected, str(facts.graduation_year),
                      f"Batch {facts.graduation_year} not eligible")


def _fmt(v) -> str | None:
    return None if v is None else str(v)


def evaluate(facts: StudentFacts, criteria: CriteriaSpec) -> EligibilityResult:
    rules = [
        _cgpa_rule(facts, criteria),
        _backlog_rule(facts, criteria),
        _branch_rule(facts, criteria),
        _year_rule(facts, criteria),
    ]
    fails = [r for r in rules if r.outcome == FAIL]
    unknowns = [r for r in rules if r.outcome == UNKNOWN]
    if fails:
        decision = Decision.INELIGIBLE
    elif unknowns:
        decision = Decision.UNKNOWN
    else:
        decision = Decision.ELIGIBLE
    return EligibilityResult(
        decision=decision,
        rule_results=rules,
        reasons=[r.reason for r in fails + unknowns],
        missing_evidence=[r.rule for r in unknowns],
    )

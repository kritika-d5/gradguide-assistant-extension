"""Evaluate a course's eligibility rules against a student's confirmed profile.

Each rule returns pass, fail or unknown. Unknown means the profile field it needs has not been
confirmed yet; it never counts as a failure.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .models import Course, EligibilityRule, RuleType, StudentProfile

Outcome = Literal["pass", "fail", "unknown"]

# Which profile field each rule depends on. Used by the gap tracker.
RULE_FIELD: dict[RuleType, str] = {
    RuleType.MIN_GPA_PERCENT: "gpa_percent",
    RuleType.MIN_IELTS_OVERALL: "ielts_overall",
    RuleType.MIN_IELTS_BAND: "ielts_min_band",
    RuleType.ACCEPTS_3YR_DEGREE: "degree_years",
    RuleType.MAX_BACKLOGS: "backlogs",
    RuleType.REQUIRED_BACKGROUND: "background_tags",
    RuleType.MIN_WORK_EXP_MONTHS: "work_exp_months",
}


class RuleResult(BaseModel):
    type: RuleType
    outcome: Outcome
    severity: Literal["hard", "soft"]
    field: str
    message: str


class EligibilityResult(BaseModel):
    status: Literal["eligible", "eligible_with_flags", "unconfirmed", "ineligible"]
    rules: list[RuleResult]

    @property
    def hard_failures(self) -> list[RuleResult]:
        return [r for r in self.rules if r.outcome == "fail" and r.severity == "hard"]

    @property
    def soft_failures(self) -> list[RuleResult]:
        return [r for r in self.rules if r.outcome == "fail" and r.severity == "soft"]

    @property
    def unknown(self) -> list[RuleResult]:
        return [r for r in self.rules if r.outcome == "unknown"]


def _fmt_tags(tags) -> str:
    return ", ".join(t.replace("_", " ") for t in tags)


def evaluate_rule(rule: EligibilityRule, profile: StudentProfile) -> RuleResult:
    field = RULE_FIELD[rule.type]
    have = profile.confirmed(field)
    v = rule.value

    def result(outcome: Outcome, message: str) -> RuleResult:
        return RuleResult(type=rule.type, outcome=outcome, severity=rule.severity, field=field, message=message)

    if rule.type == RuleType.ACCEPTS_3YR_DEGREE and v:
        return result("pass", "Accepts 3 year degrees")

    if have is None:
        needs = {
            RuleType.MIN_GPA_PERCENT: f"GPA (minimum {v}%)",
            RuleType.MIN_IELTS_OVERALL: f"IELTS overall (minimum {v})",
            RuleType.MIN_IELTS_BAND: f"lowest IELTS band (minimum {v})",
            RuleType.ACCEPTS_3YR_DEGREE: "degree length (needs 16 years of education)",
            RuleType.MAX_BACKLOGS: f"backlog count (maximum {v})",
            RuleType.REQUIRED_BACKGROUND: "academic background",
            RuleType.MIN_WORK_EXP_MONTHS: f"work experience (minimum {v} months)",
        }[rule.type]
        return result("unknown", f"Needs {needs}")

    if rule.type == RuleType.MIN_GPA_PERCENT:
        ok = have >= v
        return result("pass" if ok else "fail", f"GPA {have:g}% vs minimum {v:g}%")
    if rule.type == RuleType.MIN_IELTS_OVERALL:
        ok = have >= v
        return result("pass" if ok else "fail", f"IELTS {have:g} vs minimum {v:g}")
    if rule.type == RuleType.MIN_IELTS_BAND:
        ok = have >= v
        return result("pass" if ok else "fail", f"Lowest IELTS band {have:g} vs minimum {v:g}")
    if rule.type == RuleType.ACCEPTS_3YR_DEGREE:
        ok = have >= 4
        note = rule.note or "Does not accept 3 year degrees"
        return result("pass" if ok else "fail", f"{have} year degree" if ok else note)
    if rule.type == RuleType.MAX_BACKLOGS:
        ok = have <= v
        return result("pass" if ok else "fail", f"{have} backlogs vs maximum {v}")
    if rule.type == RuleType.REQUIRED_BACKGROUND:
        ok = bool(set(have) & set(v))
        msg = "Background fits" if ok else f"Background must be one of: {_fmt_tags(v)}"
        return result("pass" if ok else "fail", msg)
    if rule.type == RuleType.MIN_WORK_EXP_MONTHS:
        ok = have >= v
        return result("pass" if ok else "fail", f"{have} months experience vs minimum {v}")
    raise ValueError(f"unhandled rule type {rule.type}")  # pragma: no cover


def evaluate(course: Course, profile: StudentProfile) -> EligibilityResult:
    rules = [evaluate_rule(r, profile) for r in course.eligibility_rules]
    res = EligibilityResult(status="eligible", rules=rules)
    if res.hard_failures:
        res.status = "ineligible"
    elif res.unknown:
        res.status = "unconfirmed"
    elif res.soft_failures:
        res.status = "eligible_with_flags"
    return res


def field_label(field: Optional[str]) -> str:
    return (field or "").replace("_", " ")

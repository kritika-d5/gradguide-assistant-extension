"""Deterministic recommendation engine.

Pipeline: eligibility -> gates -> weighted geometric mean -> bands and reasons, plus a stability
check and a gap tracker. Pure functions of (catalog, profile, preset, as_of): the same inputs
always give the same ranking and the same reasons. See docs/scoring.md.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

from . import eligibility
from .config import SUBSCORES, EngineConfig, Preset, default_config
from .models import Catalog, Course, Intake, StudentProfile
from .taxonomy import tag_similarity

REPUTATION = {"top100": 1.0, "top300": 0.75, "other": 0.5}
COUNTRY_LABELS = {"uk": "the UK", "usa": "the USA"}
FULL_WORK_RIGHTS_MONTHS = 36
DEADLINE_WARNING_DAYS = 30

# Profile fields behind each sub score (None means it comes purely from course data).
SUBSCORE_FIELD: dict[str, Optional[str]] = {
    "field_fit": "interest_tags",
    "career_fit": "career_tags",
    "budget_fit": "budget_inr",
    "academic_fit": "gpa_percent",
    "preference_fit": "target_countries",
    "outcome_fit": None,
    "reputation_fit": None,
}

GAP_QUESTIONS = {
    "budget_inr": "What total budget is the family comfortable with, including living costs?",
    "interest_tags": "Which subjects do you most enjoy, or want to go deeper into?",
    "career_tags": "What role would you like to be in two years after graduating?",
    "gpa_percent": "What is your current aggregate or CGPA?",
    "target_countries": "Are there countries you are leaning towards, or ruling out?",
    "target_intake": "Which intake are you aiming for, and is that flexible?",
    "target_levels": "Are you set on a master's, or open to a one year postgraduate diploma?",
    "ielts_overall": "Have you taken IELTS or another English test yet? What score?",
    "ielts_min_band": "What was your lowest individual IELTS band?",
    "degree_years": "Is your bachelor's a 3 year or 4 year degree?",
    "backlogs": "Do you have any backlogs, cleared or active?",
    "background_tags": "What was your undergraduate major?",
    "work_exp_months": "Do you have any full time work experience?",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class Reason(BaseModel):
    kind: Literal["positive", "warning", "info"]
    text: str


class RankedCourse(BaseModel):
    course_id: str
    course_name: str
    university: str
    country: str
    city: str
    score: float
    band: Literal["Strong match", "Good match", "Stretch"]
    academic_tier: Optional[Literal["safe", "match", "reach"]]
    eligibility: str
    total_cost_inr: float
    subscores: dict[str, float]
    reasons: list[Reason]
    source_url: Optional[str]
    data_status: str


class SetAsideCourse(BaseModel):
    course_id: str
    course_name: str
    university: str
    country: str
    gate: Literal["eligibility", "country", "level", "budget", "intake"]
    reasons: list[str]


class CloseCall(BaseModel):
    higher: str
    lower: str
    note: str


class Gap(BaseModel):
    field: str
    importance: float
    question: str
    courses_waiting: int


class Recommendation(BaseModel):
    preset: str
    preset_label: str
    config_version: str
    as_of: date
    confidence: float
    ranked: list[RankedCourse]
    set_aside: list[SetAsideCourse]
    close_calls: list[CloseCall]
    gaps: list[Gap]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def inr(amount: float) -> str:
    """Indian style short amounts: ₹27.4L, ₹1.2Cr."""
    if amount >= 1e7:
        return f"₹{amount / 1e7:.2f}Cr"
    return f"₹{amount / 1e5:.1f}L"


def _tags(tags) -> str:
    return ", ".join(t.replace("_", " ") for t in sorted(tags))


def _intake_label(term: str, year: int) -> str:
    return f"{term.capitalize()} {year}"


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def _intake_gate(course: Course, profile: StudentProfile, as_of: date) -> tuple[Optional[str], list[Reason]]:
    """Returns (set aside reason or None, reasons to show if the course stays)."""
    target = profile.confirmed("target_intake")
    reasons: list[Reason] = []

    def is_open(i: Intake) -> bool:
        if i.status == "closed":
            return False
        return i.application_deadline is None or i.application_deadline >= as_of

    if target is None:
        upcoming = sorted((i for i in course.intakes if is_open(i)), key=lambda i: (i.year, ["jan", "may", "sep"].index(i.term)))
        if not upcoming:
            return "No open intakes", reasons
        nxt = upcoming[0]
        reasons.append(Reason(kind="info", text=f"Next open intake: {_intake_label(nxt.term, nxt.year)}"))
        return None, reasons

    match = next((i for i in course.intakes if i.term == target.term and i.year == target.year), None)
    label = _intake_label(target.term, target.year)
    if match is None:
        offered = ", ".join(_intake_label(i.term, i.year) for i in course.intakes)
        return f"No {label} intake (offers {offered})", reasons
    if not is_open(match):
        when = f" on {match.application_deadline:%d %b %Y}" if match.application_deadline else ""
        return f"{label} applications closed{when}", reasons
    if match.application_deadline is not None:
        days = (match.application_deadline - as_of).days
        if days <= DEADLINE_WARNING_DAYS:
            reasons.append(Reason(kind="warning", text=f"{label} deadline in {days} days ({match.application_deadline:%d %b})"))
    if match.status == "unknown":
        reasons.append(Reason(kind="info", text=f"{label} intake status not verified"))
    return None, reasons


def _budget_ceiling(profile: StudentProfile) -> Optional[float]:
    budget = profile.confirmed("budget_inr")
    if budget is None:
        return None
    return budget * (1 + profile.budget_stretch_pct / 100)


# ---------------------------------------------------------------------------
# Sub scores
# ---------------------------------------------------------------------------

def _overlap(wanted: Optional[list[str]], offered: list[str]):
    if not wanted:
        return None, set(), set()
    return tag_similarity(wanted, offered)


def _budget_fit(cost: float, profile: StudentProfile) -> Optional[float]:
    budget = profile.confirmed("budget_inr")
    if budget is None:
        return None
    ratio = cost / budget
    if ratio <= 0.85:
        return 1.0
    if ratio <= 1.0:
        return 1.0 - (ratio - 0.85) / 0.15 * 0.25  # 1.0 -> 0.75
    stretch = profile.budget_stretch_pct / 100
    if stretch <= 0:
        return 0.3
    return 0.75 - min((ratio - 1.0) / stretch, 1.0) * 0.45  # 0.75 -> 0.3 at the ceiling


def _academic(course: Course, profile: StudentProfile) -> tuple[Optional[float], Optional[str]]:
    gpa = profile.confirmed("gpa_percent")
    typical = course.typical_admit_gpa_percent
    if gpa is None or typical is None:
        return None, None
    # A good match is the academic ideal. A safe option is likely admission but may undersell a
    # strong student, so it scores slightly lower; a reach is a real admission risk.
    if gpa >= typical + 5:
        return 0.9, "safe"
    if gpa >= typical:
        return 1.0, "match"
    return 0.55, "reach"


def _work_rights_months(course: Course, catalog: Catalog) -> int:
    if course.post_study_work_months is not None:
        return course.post_study_work_months
    return catalog.country_of(course).post_study_work_months


def compute_subscores(course: Course, profile: StudentProfile, catalog: Catalog, cfg: EngineConfig):
    """Returns (subscores with unknowns replaced by neutral, raw subscores, reasons, academic tier)."""
    uni = catalog.university(course)
    cost = catalog.total_cost_inr(course)
    reasons: list[Reason] = []
    raw: dict[str, Optional[float]] = {}

    raw["field_fit"], hit, near = _overlap(profile.confirmed("interest_tags"), course.field_tags)
    if raw["field_fit"] is not None:
        if hit:
            reasons.append(Reason(kind="positive", text=f"Matches interests: {_tags(hit)}"))
        if near:
            reasons.append(Reason(kind="info", text=f"Related to interests: {_tags(near)}"))
        if not hit and not near:
            reasons.append(Reason(kind="warning", text="No overlap with stated interests"))

    raw["career_fit"], hit, near = _overlap(profile.confirmed("career_tags"), course.career_tags)
    if raw["career_fit"] is not None:
        if hit:
            reasons.append(Reason(kind="positive", text=f"Leads towards: {_tags(hit)}"))
        elif near:
            reasons.append(Reason(kind="info", text=f"Adjacent to career goal (typical roles: {_tags(course.career_tags)})"))
        else:
            reasons.append(Reason(kind="warning", text=f"Career goal not a typical outcome (usually {_tags(course.career_tags)})"))

    raw["budget_fit"] = _budget_fit(cost, profile)
    budget = profile.confirmed("budget_inr")
    if budget is None:
        reasons.append(Reason(kind="info", text=f"Total cost about {inr(cost)} (budget not confirmed)"))
    elif cost <= budget:
        reasons.append(Reason(kind="positive", text=f"Within budget ({inr(cost)} of {inr(budget)})"))
    else:
        reasons.append(Reason(kind="warning", text=f"Over budget by {inr(cost - budget)} (within the agreed stretch)"))

    raw["academic_fit"], tier = _academic(course, profile)
    if tier:
        gpa = profile.confirmed("gpa_percent")
        kind = "warning" if tier == "reach" else "positive"
        reasons.append(Reason(kind=kind, text=f"{tier.capitalize()}: GPA {gpa:g}% vs typical {course.typical_admit_gpa_percent:g}%"))

    countries = profile.confirmed("target_countries")
    if countries is None:
        raw["preference_fit"] = None
    elif uni.country in countries:
        raw["preference_fit"] = 1.0
    else:
        raw["preference_fit"] = 0.4
        reasons.append(Reason(kind="info", text=f"Outside preferred countries: worth a look in {uni.country.title()}"))

    months = _work_rights_months(course, catalog)
    raw["outcome_fit"] = 0.3 + 0.7 * min(months, FULL_WORK_RIGHTS_MONTHS) / FULL_WORK_RIGHTS_MONTHS
    reasons.append(Reason(kind="info", text=f"About {months} months post study work rights"))

    raw["reputation_fit"] = REPUTATION[uni.ranking_band]

    subscores = {
        k: max(cfg.subscore_floor, cfg.neutral_subscore if raw[k] is None else raw[k]) for k in SUBSCORES
    }
    return subscores, raw, reasons, tier


def geometric_score(subscores: dict[str, float], weights: dict[str, float]) -> float:
    """100 x product of subscore^weight, rounded to one decimal so ties are stable."""
    log_sum = sum(weights[k] * math.log(subscores[k]) for k in SUBSCORES)
    return round(100 * math.exp(log_sum), 1)


def band_for(score: float, cfg: EngineConfig) -> str:
    if score >= cfg.bands.strong:
        return "Strong match"
    if score >= cfg.bands.good:
        return "Good match"
    return "Stretch"


def _order(items: list[tuple[str, float]]) -> list[str]:
    """Score descending, then course id ascending. The only ordering rule in the engine."""
    return [cid for cid, _ in sorted(items, key=lambda x: (-x[1], x[0]))]


# ---------------------------------------------------------------------------
# Stability check and gaps
# ---------------------------------------------------------------------------

def _nudged(weights: dict[str, float], key: str, delta: float) -> dict[str, float]:
    w = dict(weights)
    w[key] = max(0.0, w[key] + delta)
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def close_calls(subs: dict[str, dict[str, float]], weights: dict[str, float], cfg: EngineConfig, names: dict[str, str]) -> list[CloseCall]:
    base = _order([(cid, geometric_score(s, weights)) for cid, s in subs.items()])
    top = base[:3]
    if len(top) < 2:
        return []
    swaps: dict[tuple[str, str], list[str]] = {}
    for key in SUBSCORES:
        for delta in (cfg.stability_nudge, -cfg.stability_nudge):
            w = _nudged(weights, key, delta)
            order = _order([(cid, geometric_score(s, w)) for cid, s in subs.items()])
            pos = {cid: i for i, cid in enumerate(order)}
            for i, a in enumerate(top):
                for b in top[i + 1:]:
                    if pos[a] > pos[b]:
                        direction = "more" if delta > 0 else "less"
                        swaps.setdefault((a, b), []).append(f"{direction} weight on {key.replace('_', ' ')}")
    return [
        CloseCall(
            higher=names[a],
            lower=names[b],
            note=f"Order flips with {', '.join(sorted(set(why))[:2])}: treat as a close call",
        )
        for (a, b), why in sorted(swaps.items())
    ]


def find_gaps(profile: StudentProfile, preset: Preset, waiting: dict[str, int], total_courses: int) -> list[Gap]:
    gaps: list[Gap] = []
    weight_of = {f: preset.weights[k] for k, f in SUBSCORE_FIELD.items() if f}
    gate_fields = {"target_intake": 0.1, "target_levels": 0.08}  # fields that only drive gates
    candidates = set(weight_of) | set(waiting) | set(gate_fields)
    for field in candidates:
        if profile.confirmed(field) is not None:
            continue
        share = waiting.get(field, 0) / max(total_courses, 1)
        importance = weight_of.get(field, 0.0) + 0.3 * share + gate_fields.get(field, 0.0)
        gaps.append(Gap(field=field, importance=round(importance, 3), question=GAP_QUESTIONS[field], courses_waiting=waiting.get(field, 0)))
    return sorted(gaps, key=lambda g: (-g.importance, g.field))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def recommend(
    catalog: Catalog,
    profile: StudentProfile,
    preset_key: str = "balanced",
    as_of: Optional[date] = None,
    cfg: Optional[EngineConfig] = None,
    limit: Optional[int] = None,
) -> Recommendation:
    cfg = cfg or default_config()
    if preset_key not in cfg.presets:
        raise KeyError(f"unknown preset {preset_key!r}; options: {sorted(cfg.presets)}")
    preset = cfg.presets[preset_key]
    as_of = as_of or date.today()
    ceiling = _budget_ceiling(profile)

    ranked: list[RankedCourse] = []
    set_aside: list[SetAsideCourse] = []
    subs_by_id: dict[str, dict[str, float]] = {}
    names: dict[str, str] = {}
    waiting: dict[str, int] = {}

    for course in sorted(catalog.courses, key=lambda c: c.id):
        uni = catalog.university(course)
        elig = eligibility.evaluate(course, profile)
        for r in elig.unknown:
            waiting[r.field] = waiting.get(r.field, 0) + 1

        def aside(gate, reasons):
            set_aside.append(SetAsideCourse(course_id=course.id, course_name=course.name, university=uni.name, country=uni.country, gate=gate, reasons=reasons))

        if elig.hard_failures:
            aside("eligibility", [r.message for r in elig.hard_failures])
            continue
        excluded = profile.confirmed("excluded_countries")
        if excluded is not None and uni.country in excluded:
            aside("country", [f"The student ruled out {COUNTRY_LABELS.get(uni.country, uni.country.title())}"])
            continue
        levels = profile.confirmed("target_levels")
        if levels is not None and course.degree_level not in levels:
            aside("level", [f"{course.degree_level.replace('_', ' ')} is not a level the student wants"])
            continue
        cost = catalog.total_cost_inr(course)
        if ceiling is not None and cost > ceiling:
            aside("budget", [f"Total cost {inr(cost)} exceeds the ceiling of {inr(ceiling)}"])
            continue
        intake_block, intake_reasons = _intake_gate(course, profile, as_of)
        if intake_block:
            aside("intake", [intake_block])
            continue

        subs, _raw, reasons, tier = compute_subscores(course, profile, catalog, cfg)
        reasons += intake_reasons
        reasons += [Reason(kind="warning", text=r.message) for r in elig.soft_failures]
        if elig.unknown:
            needs = "; ".join(r.message.removeprefix("Needs ") for r in elig.unknown)
            reasons.append(Reason(kind="info", text=f"Eligibility unconfirmed: needs {needs}"))
        if course.data_status != "verified":
            reasons.append(Reason(kind="info", text="Course data not yet verified against the official page"))

        score = geometric_score(subs, preset.weights)
        subs_by_id[course.id] = subs
        names[course.id] = f"{course.name}, {uni.name}"
        ranked.append(
            RankedCourse(
                course_id=course.id,
                course_name=course.name,
                university=uni.name,
                country=uni.country,
                city=uni.city,
                score=score,
                band=band_for(score, cfg),
                academic_tier=tier,
                eligibility=elig.status,
                total_cost_inr=round(cost),
                subscores={k: round(v, 3) for k, v in subs.items()},
                reasons=reasons,
                source_url=course.source_url,
                data_status=course.data_status,
            )
        )

    order = _order([(r.course_id, r.score) for r in ranked])
    by_id = {r.course_id: r for r in ranked}
    ranked = [by_id[cid] for cid in order]

    confidence = sum(
        preset.weights[k] for k, f in SUBSCORE_FIELD.items() if f is None or profile.confirmed(f) is not None
    )

    return Recommendation(
        preset=preset.key,
        preset_label=preset.label,
        config_version=cfg.version,
        as_of=as_of,
        confidence=round(confidence, 2),
        ranked=ranked[:limit] if limit else ranked,
        set_aside=set_aside,
        close_calls=close_calls(subs_by_id, preset.weights, cfg, names),
        gaps=find_gaps(profile, preset, waiting, len(catalog.courses)),
    )

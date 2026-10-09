"""Data models for courses, countries and student profiles.

Everything that drives a decision is structured, sourced and dated, so that recommendations are
explainable and the data can be updated without touching code.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Course data
# ---------------------------------------------------------------------------

RankingBand = Literal["top100", "top300", "other"]
Term = Literal["jan", "may", "sep"]
Currency = Literal["GBP", "CAD", "EUR", "INR"]


class RuleType(str, Enum):
    MIN_GPA_PERCENT = "min_gpa_percent"
    MIN_IELTS_OVERALL = "min_ielts_overall"
    MIN_IELTS_BAND = "min_ielts_band"
    ACCEPTS_3YR_DEGREE = "accepts_3yr_degree"
    MAX_BACKLOGS = "max_backlogs"
    REQUIRED_BACKGROUND = "required_background"
    MIN_WORK_EXP_MONTHS = "min_work_exp_months"


class EligibilityRule(BaseModel):
    type: RuleType
    value: Any
    severity: Literal["hard", "soft"] = "hard"
    note: Optional[str] = None


class Intake(BaseModel):
    term: Term
    year: int
    application_deadline: Optional[date] = None
    status: Literal["open", "closed", "unknown"] = "unknown"


class University(BaseModel):
    id: str
    name: str
    country: str
    city: str
    ranking_band: RankingBand
    website: Optional[str] = None
    last_verified: Optional[date] = None


class Course(BaseModel):
    id: str
    university_id: str
    name: str
    degree_level: Literal["masters", "pg_diploma", "bachelors"]
    field_tags: list[str]
    career_tags: list[str]
    duration_months: int = Field(gt=0)
    tuition_total: float = Field(ge=0)
    currency: Currency
    typical_admit_gpa_percent: Optional[float] = None
    # Overrides the country default when work rights depend on the programme (e.g. college certificates).
    post_study_work_months: Optional[int] = None
    intakes: list[Intake]
    eligibility_rules: list[EligibilityRule] = []
    source_url: Optional[str] = None
    last_verified: Optional[date] = None
    data_status: Literal["verified", "illustrative"] = "illustrative"


class CountryProfile(BaseModel):
    country: str
    currency: Currency
    living_cost_monthly: float = Field(gt=0)
    post_study_work_months: int = Field(ge=0)
    part_time_work_hours_cap: Optional[int] = None
    last_verified: Optional[date] = None


class Catalog(BaseModel):
    """The full dataset the engine works on, plus exchange rates into INR."""

    universities: dict[str, University]
    courses: list[Course]
    countries: dict[str, CountryProfile]
    fx_to_inr: dict[str, float]

    def university(self, course: Course) -> University:
        return self.universities[course.university_id]

    def country_of(self, course: Course) -> CountryProfile:
        return self.countries[self.university(course).country]

    def total_cost_inr(self, course: Course) -> float:
        """Tuition for the whole course plus living costs for its duration, in INR."""
        country = self.country_of(course)
        tuition = course.tuition_total * self.fx_to_inr[course.currency]
        living = country.living_cost_monthly * course.duration_months * self.fx_to_inr[country.currency]
        return tuition + living


# ---------------------------------------------------------------------------
# Student profile
# ---------------------------------------------------------------------------

T = TypeVar("T")


class FieldStatus(str, Enum):
    CONFIRMED = "confirmed"  # counsellor accepted this value; the engine uses it
    PENDING = "pending"  # heard in captions, waiting for the counsellor
    UNKNOWN = "unknown"


class ProfileField(BaseModel, Generic[T]):
    value: Optional[T] = None
    status: FieldStatus = FieldStatus.UNKNOWN
    source: Literal["manual", "caption", "none"] = "none"


class TargetIntake(BaseModel):
    term: Term
    year: int


class StudentProfile(BaseModel):
    """Every field carries a status. Only confirmed values reach the scoring engine."""

    gpa_percent: ProfileField[float] = ProfileField()
    degree_years: ProfileField[int] = ProfileField()
    backlogs: ProfileField[int] = ProfileField()
    ielts_overall: ProfileField[float] = ProfileField()
    ielts_min_band: ProfileField[float] = ProfileField()
    work_exp_months: ProfileField[int] = ProfileField()
    background_tags: ProfileField[list[str]] = ProfileField()
    interest_tags: ProfileField[list[str]] = ProfileField()
    career_tags: ProfileField[list[str]] = ProfileField()
    budget_inr: ProfileField[float] = ProfileField()
    target_countries: ProfileField[list[str]] = ProfileField()
    target_intake: ProfileField[TargetIntake] = ProfileField()
    target_levels: ProfileField[list[Literal["masters", "pg_diploma", "bachelors"]]] = ProfileField()

    # Counsellor setting, not a student fact: how far over budget a course may go and still rank.
    budget_stretch_pct: float = Field(default=0.0, ge=0, le=50)

    def confirmed(self, name: str):
        """Return the value only if the counsellor confirmed it, otherwise None."""
        f: ProfileField = getattr(self, name)
        if f.status != FieldStatus.CONFIRMED:
            return None
        if isinstance(f.value, list) and not f.value:
            return None
        return f.value

    @field_validator("*", mode="before")
    @classmethod
    def _allow_bare_values(cls, v, info):
        # Convenience for tests and the API: {"gpa_percent": 82} means a confirmed manual value.
        if info.field_name == "budget_stretch_pct":
            return v
        if v is None or isinstance(v, ProfileField):
            return v
        if isinstance(v, dict) and ({"value", "status"} & v.keys()):
            return v
        return {"value": v, "status": "confirmed", "source": "manual"}

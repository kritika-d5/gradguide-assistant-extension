import math
import random
from datetime import date

import pytest

from app.calibrate import check, load_personas
from app.catalog import load_catalog
from app.config import SUBSCORES, default_config
from app.engine import geometric_score, recommend
from app.models import StudentProfile

AS_OF = date(2026, 10, 9)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def strong_student(**overrides):
    base = dict(
        gpa_percent=82, degree_years=4, ielts_overall=7.0, ielts_min_band=6.5, backlogs=0,
        background_tags=["computer_science", "engineering"], interest_tags=["data_science", "ml"],
        career_tags=["ml_engineer"], budget_inr=4_000_000, target_countries=["canada", "germany"],
        target_intake={"term": "sep", "year": 2027}, target_levels=["masters"],
    )
    base.update(overrides)
    return StudentProfile(**base)


def ids(rec):
    return [r.course_id for r in rec.ranked]


def aside(rec):
    return {s.course_id: s for s in rec.set_aside}


# ---------------------------------------------------------------- determinism

def test_same_input_same_output(catalog):
    a = recommend(catalog, strong_student(), "balanced", AS_OF)
    b = recommend(catalog, strong_student(), "balanced", AS_OF)
    assert a.model_dump() == b.model_dump()


def test_catalog_order_does_not_matter(catalog):
    shuffled = catalog.model_copy(deep=True)
    random.Random(7).shuffle(shuffled.courses)
    a = recommend(catalog, strong_student(), "balanced", AS_OF)
    b = recommend(shuffled, strong_student(), "balanced", AS_OF)
    assert a.model_dump() == b.model_dump()


def test_ties_break_by_course_id(catalog):
    # With no profile at all, courses with identical course data scores must order by id.
    rec = recommend(catalog, StudentProfile(), "balanced", AS_OF)
    pairs = list(zip(rec.ranked, rec.ranked[1:]))
    for x, y in pairs:
        assert (x.score > y.score) or (x.score == y.score and x.course_id < y.course_id)


# ---------------------------------------------------------------- dealbreakers

def test_geometric_mean_punishes_a_weak_dimension():
    weights = {"field_fit": .25, "career_fit": .20, "budget_fit": .20, "academic_fit": .15,
               "preference_fit": .12, "outcome_fit": .08, "reputation_fit": 0.0}
    lopsided = {k: 1.0 for k in SUBSCORES} | {"budget_fit": 0.05}
    even = {k: 0.75 for k in SUBSCORES}
    weighted_sum = sum(weights[k] * lopsided[k] for k in SUBSCORES) * 100
    assert round(weighted_sum) == 81  # the old approach ranks the lopsided course first
    assert geometric_score(lopsided, weights) < geometric_score(even, weights)


def test_unaffordable_courses_are_set_aside_not_ranked(catalog):
    rec = recommend(catalog, strong_student(), "balanced", AS_OF)
    for cid, s in aside(rec).items():
        if s.gate == "budget":
            assert cid not in ids(rec)
    assert aside(rec)["ca_toronto_mscac"].gate == "budget"


def test_budget_stretch_keeps_course_with_warning(catalog):
    # UBC costs about 44L; a 40L budget with 15% stretch allows up to 46L.
    rec = recommend(catalog, strong_student(budget_stretch_pct=15), "balanced", AS_OF)
    ubc = next(r for r in rec.ranked if r.course_id == "ca_ubc_mds")
    assert any("Over budget" in x.text for x in ubc.reasons)


def test_three_year_degree_rule(catalog):
    rec = recommend(catalog, strong_student(degree_years=3), "balanced", AS_OF)
    assert aside(rec)["de_rwth_msc_cs"].gate == "eligibility"
    assert "de_srh_msc_bdai" in ids(rec)  # private university accepts 3 year degrees


def test_soft_rule_failure_stays_ranked_with_warning(catalog):
    prof = strong_student(ielts_min_band=6.0, budget_inr=9_000_000, target_countries=["uk"])
    rec = recommend(catalog, prof, "balanced", AS_OF)
    ucl = next(r for r in rec.ranked if r.course_id == "uk_ucl_msc_dsml")
    assert any("Lowest IELTS band" in x.text and x.kind == "warning" for x in ucl.reasons)


def test_ruled_out_country_is_a_gate(catalog):
    rec = recommend(catalog, strong_student(excluded_countries=["uk"], target_countries=["uk", "canada"]), "balanced", AS_OF)
    assert aside(rec)["uk_ucl_msc_dsml"].gate == "country"
    assert not any(r.country == "uk" for r in rec.ranked)
    # A value heard in captions but not confirmed changes nothing.
    heard = {"value": ["uk"], "status": "pending", "source": "caption"}
    rec = recommend(catalog, strong_student(excluded_countries=heard, target_countries=["uk", "canada"]), "balanced", AS_OF)
    assert not [s for s in rec.set_aside if s.gate == "country"]


def test_level_gate(catalog):
    rec = recommend(catalog, strong_student(), "balanced", AS_OF)
    assert aside(rec)["ca_conestoga_gc_aiml"].gate == "level"


# ---------------------------------------------------------------- intakes

def test_closed_intake_is_set_aside(catalog):
    prof = strong_student(target_intake={"term": "jan", "year": 2027}, budget_inr=6_000_000,
                          target_levels=["masters", "pg_diploma"])
    rec = recommend(catalog, prof, "balanced", AS_OF)
    assert aside(rec)["ca_concordia_meng_soft"].gate == "intake"
    assert aside(rec)["uk_manchester_msc_acs"].gate == "intake"  # no January intake


def test_deadline_warning_within_30_days(catalog):
    prof = strong_student(target_intake={"term": "jan", "year": 2027}, budget_inr=6_000_000,
                          target_levels=["masters", "pg_diploma"], target_countries=["uk", "canada"])
    rec = recommend(catalog, prof, "balanced", AS_OF)
    conestoga = next(r for r in rec.ranked if r.course_id == "ca_conestoga_gc_aiml")
    assert any("deadline in 6 days" in x.text for x in conestoga.reasons)


def test_deadline_passes_with_time(catalog):
    prof = strong_student(target_intake={"term": "jan", "year": 2027}, budget_inr=6_000_000,
                          target_levels=["masters", "pg_diploma"])
    rec = recommend(catalog, prof, "balanced", date(2026, 10, 20))
    assert aside(rec)["ca_conestoga_gc_aiml"].gate == "intake"


# ---------------------------------------------------------------- unknowns and pending

def test_unknown_fields_never_set_courses_aside(catalog):
    rec = recommend(catalog, StudentProfile(), "balanced", AS_OF)
    assert not [s for s in rec.set_aside if s.gate == "eligibility"]
    assert rec.confidence < 0.2


def test_pending_values_are_ignored_until_confirmed(catalog):
    pending = StudentProfile(budget_inr={"value": 1_000_000, "status": "pending", "source": "caption"})
    unknown = StudentProfile()
    a = recommend(catalog, pending, "balanced", AS_OF)
    b = recommend(catalog, unknown, "balanced", AS_OF)
    assert ids(a) == ids(b)


def test_confidence_rises_as_gaps_close(catalog):
    sparse = recommend(catalog, StudentProfile(interest_tags=["data_science"]), "balanced", AS_OF)
    full = recommend(catalog, strong_student(), "balanced", AS_OF)
    assert sparse.confidence < full.confidence == 1.0
    assert sparse.gaps and sparse.gaps[0].field == "gpa_percent"
    assert not full.gaps


# ---------------------------------------------------------------- explainability

def test_every_ranked_course_has_reasons_and_a_band(catalog):
    rec = recommend(catalog, strong_student(), "balanced", AS_OF)
    for r in rec.ranked:
        assert r.reasons
        assert r.band in {"Strong match", "Good match", "Stretch"}
    assert rec.config_version == default_config().version


def test_presets_change_the_ranking_and_are_reported(catalog):
    a = recommend(catalog, strong_student(), "balanced", AS_OF)
    b = recommend(catalog, strong_student(), "prestige_first", AS_OF)
    assert ids(a) != ids(b)
    assert b.preset_label == "Prestige first"


def test_presets_are_normalised():
    for p in default_config().presets.values():
        assert math.isclose(sum(p.weights.values()), 1.0)


def test_unknown_preset_raises(catalog):
    with pytest.raises(KeyError):
        recommend(catalog, StudentProfile(), "vibes", AS_OF)


# ---------------------------------------------------------------- calibration personas

_as_of, _personas = load_personas()


@pytest.mark.parametrize("persona", _personas, ids=[p["name"] for p in _personas])
def test_persona_expectations(catalog, persona):
    rec = recommend(catalog, StudentProfile(**persona["profile"]), persona["preset"], _as_of)
    failures = [c.description for c in check(persona, rec) if not c.passed]
    assert not failures, failures

"""Consistency lens: similarity, divergence rules, storage and the API."""
import pytest
from fastapi.testclient import TestClient

from app import sessions
from app.api import app
from app.sessions import HistoryStore, PastSession, lens, similar_sessions, similarity

DS_STUDENT = {
    "gpa_percent": 76, "interest_tags": ["data_science"], "career_tags": ["data_scientist"],
    "budget_inr": 4_000_000, "target_countries": ["germany", "canada"], "target_levels": ["masters"],
}


def past(i, recommended, counsellor="A", **changes):
    return PastSession(id=i, counsellor=counsellor, as_of="2026-09-01", preset="balanced",
                       profile={**DS_STUDENT, **changes}, recommended=recommended)


# ------------------------------------------------------------------ similarity

def test_identical_profiles_are_fully_similar():
    assert similarity(DS_STUDENT, DS_STUDENT) == (1.0, 0.95)  # no background field to compare


def test_fields_missing_on_one_side_are_skipped_not_penalised():
    score, shared = similarity({"gpa_percent": 76, "interest_tags": ["data_science"]}, DS_STUDENT)
    assert score == 1.0 and shared == 0.4


def test_budget_twice_as_large_scores_zero_on_budget():
    assert sessions.field_similarity("budget_inr", 4_000_000, 8_000_000) == 0
    assert sessions.field_similarity("budget_inr", 4_000_000, 4_000_000) == 1


def test_related_tags_get_partial_credit():
    near = sessions.field_similarity("career_tags", ["data_scientist"], ["ml_engineer"])
    assert 0 < near < 1
    assert sessions.field_similarity("career_tags", ["data_scientist"], ["robotics_engineer"]) == 0


def test_very_different_students_are_not_similar():
    robotics = {"gpa_percent": 90, "interest_tags": ["robotics"], "career_tags": ["robotics_engineer"], "budget_inr": 9_000_000}
    assert similar_sessions(DS_STUDENT, [past(1, ["x"], **robotics)]) == []


def test_too_little_overlap_is_not_similar():
    # Only the degree level can be compared: not enough to call two students similar.
    assert similar_sessions({"target_levels": ["masters"]}, [past(1, ["x"])]) == []


# ------------------------------------------------------------------ the lens

HISTORY = [
    past(1, ["de_dortmund_msc_ds", "de_srh_msc_bdai"], "A"),
    past(2, ["de_dortmund_msc_ds", "ca_ubc_mds"], "B"),
    past(3, ["de_dortmund_msc_ds", "de_srh_msc_bdai"], "C"),
    past(4, ["uk_coventry_msc_dsci"], "C"),
]
NAMES = {}


def test_often_recommended_course_missing_from_picks_is_flagged_with_its_status_here():
    out = lens(DS_STUDENT, HISTORY, picked=["uk_coventry_msc_dsci"], ranked=["de_dortmund_msc_ds", "de_srh_msc_bdai"],
               set_aside={}, names=NAMES)
    assert out.similar == 4 and out.counsellors == 3 and out.compared == "your_picks"
    missing = {d.course_id: d for d in out.divergences if d.kind == "often_missing"}
    assert set(missing) == {"de_dortmund_msc_ds", "de_srh_msc_bdai"}
    assert "3 of 4" in missing["de_dortmund_msc_ds"].text and "Ranked #1" in missing["de_dortmund_msc_ds"].text


def test_set_aside_reason_explains_a_divergence():
    out = lens(DS_STUDENT, HISTORY, picked=["de_srh_msc_bdai"], ranked=["de_srh_msc_bdai"],
               set_aside={"de_dortmund_msc_ds": "No Sep 2027 intake"}, names=NAMES)
    dortmund = next(d for d in out.divergences if d.course_id == "de_dortmund_msc_ds")
    assert "Set aside for this student: No Sep 2027 intake" in dortmund.text


def test_pick_no_similar_student_received_is_flagged():
    out = lens(DS_STUDENT, HISTORY, picked=["de_dortmund_msc_ds", "uk_herts_msc_ba"], ranked=[], set_aside={}, names=NAMES)
    rare = [d for d in out.divergences if d.kind == "rarely_given"]
    assert [d.course_id for d in rare] == ["uk_herts_msc_ba"] and rare[0].text.startswith("None of 4")


def test_without_picks_the_engine_top_five_is_compared():
    out = lens(DS_STUDENT, HISTORY, picked=[], ranked=["de_dortmund_msc_ds", "de_srh_msc_bdai", "uk_herts_msc_ba"], set_aside={}, names=NAMES)
    # Nobody similar got Herts, but the counsellor did not choose it, so it is not flagged.
    assert out.compared == "top_ranked" and out.divergences == []


def test_fewer_than_three_similar_students_is_not_a_pattern():
    out = lens(DS_STUDENT, HISTORY[:2], picked=["uk_herts_msc_ba"], ranked=[], set_aside={}, names=NAMES)
    assert out.divergences == [] and out.message


def test_lens_is_deterministic():
    args = dict(picked=["uk_coventry_msc_dsci"], ranked=["de_dortmund_msc_ds"], set_aside={}, names=NAMES)
    assert lens(DS_STUDENT, HISTORY, **args) == lens(DS_STUDENT, list(reversed(HISTORY)), **args)


# ------------------------------------------------------------------ storage

def test_demo_sessions_seed_once_and_are_flagged(tmp_path):
    db = tmp_path / "h.db"
    store = HistoryStore(db)
    n = store.count()
    assert n == 12 and all(s.demo for s in store.all())
    assert HistoryStore(db).count() == n  # reopening does not seed again


def test_save_and_update_a_session_but_never_overwrite_demo_rows(tmp_path):
    store = HistoryStore(tmp_path / "h.db", seed_demo=False)
    sid = store.save("Kritika", "2026-10-09", "balanced", "v2.0", DS_STUDENT, ["de_dortmund_msc_ds"])
    assert store.save("Kritika", "2026-10-09", "balanced", "v2.0", DS_STUDENT, ["de_srh_msc_bdai"], session_id=sid) == sid
    assert store.all()[0].recommended == ["de_srh_msc_bdai"] and store.count() == 1


# ------------------------------------------------------------------ API

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GG_SESSIONS_DB", str(tmp_path / "api.db"))
    monkeypatch.setattr(sessions, "_store", None)
    return TestClient(app)


def body(**extra):
    return {"profile": DS_STUDENT, "preset": "balanced", "as_of": "2026-10-09", **extra}


def test_consistency_endpoint_uses_demo_history(client):
    r = client.post("/consistency", json=body(recommended=["uk_coventry_msc_dsci"]))
    assert r.status_code == 200
    data = r.json()
    assert data["similar"] >= 3 and data["demo"] == data["similar"]
    assert any(d["course_id"] == "de_dortmund_msc_ds" and d["kind"] == "often_missing" for d in data["divergences"])


def test_saved_session_is_not_compared_with_itself(client):
    saved = client.post("/sessions", json=body(recommended=["uk_herts_msc_ba"], counsellor="K"))
    assert saved.status_code == 200
    sid = saved.json()["id"]
    with_self = client.post("/consistency", json=body(recommended=["uk_herts_msc_ba"])).json()
    without_self = client.post("/consistency", json=body(recommended=["uk_herts_msc_ba"], session_id=sid)).json()
    assert with_self["similar"] == without_self["similar"] + 1


def test_save_rejects_unknown_courses_and_empty_picks(client):
    assert client.post("/sessions", json=body(recommended=["nope"])).status_code == 400
    assert client.post("/sessions", json=body(recommended=[])).status_code == 400

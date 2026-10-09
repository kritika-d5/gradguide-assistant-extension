from fastapi.testclient import TestClient

from app.api import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["courses"] > 0


def test_recommend_round_trip():
    body = {
        "profile": {
            "gpa_percent": 82,
            "interest_tags": ["data_science"],
            "budget_inr": {"value": 4000000, "status": "pending", "source": "caption"},
        },
        "preset": "budget_first",
        "as_of": "2026-10-09",
        "limit": 5,
    }
    r = client.post("/recommend", json=body)
    assert r.status_code == 200
    data = r.json()
    assert len(data["ranked"]) == 5
    assert data["preset"] == "budget_first"
    assert any(g["field"] == "budget_inr" for g in data["gaps"])  # pending counts as a gap


def test_bad_preset_is_400():
    r = client.post("/recommend", json={"profile": {}, "preset": "nope"})
    assert r.status_code == 400


def test_course_search():
    r = client.get("/courses", params={"q": "data science", "country": "germany"})
    assert r.status_code == 200
    results = r.json()
    assert results and all(c["country"] == "germany" for c in results)
    assert "total_cost_label" in results[0]


def test_course_detail_404():
    assert client.get("/courses/nope").status_code == 404


def test_vocabulary_comes_from_catalogue():
    v = client.get("/vocabulary").json()
    assert "data_science" in v["interest_tags"]
    assert "ml_engineer" in v["career_tags"]
    assert "computer_science" in v["background_tags"]
    assert v["countries"] == sorted(v["countries"]) and "canada" in v["countries"]
    assert {"term": "sep", "year": 2027} in v["intakes"]

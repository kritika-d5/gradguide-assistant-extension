"""HTTP API used by the browser extension's side panel.

Run locally:  uvicorn app.api:app --reload   (from the backend folder)
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .catalog import default_catalog
from .config import default_config
from .engine import Recommendation, inr, recommend
from .models import StudentProfile

app = FastAPI(title="GradGuide Counsellor Assist", version="0.1.0")

# The side panel runs on a chrome-extension:// origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://.*|http://localhost(:\d+)?)$",
    allow_methods=["*"],
    allow_headers=["*"],
)


class RecommendRequest(BaseModel):
    profile: StudentProfile
    preset: str = "balanced"
    as_of: Optional[date] = None
    limit: Optional[int] = None


@app.get("/health")
def health():
    cat = default_catalog()
    return {"status": "ok", "courses": len(cat.courses), "config_version": default_config().version}


@app.get("/presets")
def presets():
    cfg = default_config()
    return [{"key": p.key, "label": p.label, "weights": p.weights} for p in cfg.presets.values()]


@app.post("/recommend", response_model=Recommendation)
def post_recommend(req: RecommendRequest):
    try:
        return recommend(default_catalog(), req.profile, req.preset, req.as_of, limit=req.limit)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _course_view(course):
    cat = default_catalog()
    uni = cat.university(course)
    country = cat.country_of(course)
    return {
        **course.model_dump(mode="json"),
        "university": uni.name,
        "country": uni.country,
        "city": uni.city,
        "ranking_band": uni.ranking_band,
        "total_cost_inr": round(cat.total_cost_inr(course)),
        "total_cost_label": inr(cat.total_cost_inr(course)),
        "post_study_work_months": course.post_study_work_months or country.post_study_work_months,
    }


@app.get("/courses")
def search_courses(
    q: str = "",
    country: Optional[str] = None,
    tag: Optional[str] = None,
    max_cost_inr: Optional[float] = None,
    limit: int = Query(20, le=100),
):
    """Keyword search for the counsellor's search box. Deterministic: matches are ordered by the
    number of query words found, then by course id."""
    cat = default_catalog()
    words = [w for w in q.lower().split() if w]
    hits = []
    for course in cat.courses:
        uni = cat.university(course)
        if country and uni.country != country.lower():
            continue
        if tag and tag not in course.field_tags + course.career_tags:
            continue
        if max_cost_inr is not None and cat.total_cost_inr(course) > max_cost_inr:
            continue
        haystack = " ".join([course.name, uni.name, uni.city, uni.country, *course.field_tags, *course.career_tags]).lower().replace("_", " ")
        matched = sum(1 for w in words if w in haystack)
        if words and matched == 0:
            continue
        hits.append((-matched, course.id, course))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [_course_view(c) for _, _, c in hits[:limit]]


@app.get("/courses/{course_id}")
def get_course(course_id: str):
    course = next((c for c in default_catalog().courses if c.id == course_id), None)
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    return _course_view(course)

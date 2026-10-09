"""HTTP API used by the browser extension's side panel.

Run locally:  uvicorn app.api:app --reload   (from the backend folder)
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import llm_extract, sessions
from .catalog import default_catalog
from .config import default_config
from .engine import Recommendation, inr, recommend
from .models import RuleType, StudentProfile

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


@app.get("/vocabulary")
def vocabulary():
    return _vocabulary()


def _vocabulary():
    """Values the side panel offers as chips, derived from the catalogue so the panel never hard
    codes course data. Sorted so the panel layout is stable."""
    cat = default_catalog()
    backgrounds = {
        tag
        for c in cat.courses
        for rule in c.eligibility_rules
        if rule.type == RuleType.REQUIRED_BACKGROUND
        for tag in rule.value
    }
    intakes = {(i.year, ["jan", "may", "sep"].index(i.term), i.term) for c in cat.courses for i in c.intakes}
    return {
        "interest_tags": sorted({t for c in cat.courses for t in c.field_tags}),
        "career_tags": sorted({t for c in cat.courses for t in c.career_tags}),
        "background_tags": sorted(backgrounds),
        "countries": sorted(cat.countries),
        "levels": sorted({c.degree_level for c in cat.courses}),
        "intakes": [{"term": term, "year": year} for year, _, term in sorted(intakes)],
    }


class SessionRequest(BaseModel):
    profile: StudentProfile
    preset: str = "balanced"
    as_of: Optional[date] = None
    recommended: list[str] = Field(default_factory=list, max_length=30)
    counsellor: str = Field("", max_length=60)
    # The saved session being updated (on save) or excluded from comparison (on consistency).
    session_id: Optional[int] = None


def _check_request(req: SessionRequest):
    if req.preset not in default_config().presets:
        raise HTTPException(status_code=400, detail=f"unknown preset {req.preset!r}")
    known = {c.id for c in default_catalog().courses}
    unknown = sorted(set(req.recommended) - known)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown course ids: {', '.join(unknown)}")


@app.post("/sessions")
def save_session(req: SessionRequest):
    """Save what was recommended to this student, for the consistency lens. Only confirmed
    profile values are stored; no names, no transcript."""
    _check_request(req)
    if not req.recommended:
        raise HTTPException(status_code=400, detail="mark at least one recommended course before saving")
    store = sessions.default_store()
    session_id = store.save(
        req.counsellor.strip(), req.as_of or date.today(), req.preset, default_config().version,
        sessions.confirmed_values(req.profile), req.recommended, session_id=req.session_id,
    )
    return {"id": session_id, "history_total": store.count()}


@app.post("/consistency", response_model=sessions.Lens)
def post_consistency(req: SessionRequest):
    """Compare this shortlist with what similar past students were recommended. Informational
    only: it never changes the ranking."""
    _check_request(req)
    cat = default_catalog()
    rec = recommend(cat, req.profile, req.preset, req.as_of)
    names = {c.id: f"{c.name}, {cat.university(c).name}" for c in cat.courses}
    history = [s for s in sessions.default_store().all() if s.id != req.session_id]
    return sessions.lens(
        sessions.confirmed_values(req.profile),
        history,
        picked=req.recommended,
        ranked=[r.course_id for r in rec.ranked],
        set_aside={s.course_id: "; ".join(s.reasons) for s in rec.set_aside},
        names=names,
    )


class ExtractRequest(BaseModel):
    # The panel sends a rolling window of recent captions or typed notes.
    transcript: str = Field(min_length=1, max_length=12000)


@app.get("/extract/status")
def extract_status():
    cfg = llm_extract.config_from_env()
    return {"enabled": cfg.enabled, "provider": cfg.provider, "model": cfg.model}


@app.post("/extract")
def post_extract(req: ExtractRequest):
    """Model assisted reading of noisy captions. Every value is checked against the transcript
    and returned as a proposal only; the panel shows it as a pending chip."""
    try:
        return llm_extract.extract(req.transcript, _vocabulary())
    except llm_extract.ExtractionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except llm_extract.ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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

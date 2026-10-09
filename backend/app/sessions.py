"""Session history and the consistency lens.

GradGuide's core problem is that similar students get different shortlists depending on the
counsellor. The ranking engine is deterministic, but what reaches the student is what the
counsellor *recommends*. So each counsellor can save a session (confirmed profile plus the courses
they recommended), and the lens compares a live shortlist with what similar past students were
recommended:

* "Similar students were often recommended X, which is not on your shortlist" (and, if X is set
  aside for this student, the reason, because then the difference is explained);
* "None of the similar students were recommended Y".

It never blocks or reorders anything; it only points out differences for the counsellor to judge.

Storage is one SQLite file shared by every counsellor using the same server. Only confirmed,
structured profile values are stored: no names, no transcript. The similarity and lens functions
are pure and deterministic; only `HistoryStore` touches the database.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel

from .models import StudentProfile
from .taxonomy import NO_MATCH_FLOOR, tag_similarity

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BACKEND_DIR / "var" / "sessions.db"
DEMO_FILE = BACKEND_DIR / "data" / "demo_sessions.yaml"

# How much each field counts when comparing two students. Fields that drive both the gates and
# the score dominate; fields only one profile has are skipped rather than counted as different.
SIMILARITY_WEIGHTS = {
    "interest_tags": 0.25,
    "career_tags": 0.20,
    "budget_inr": 0.20,
    "gpa_percent": 0.15,
    "target_countries": 0.10,
    "background_tags": 0.05,
    "target_levels": 0.05,
}
SIMILAR_AT = 0.70  # a past student counts as similar at or above this score
MIN_SHARED_WEIGHT = 0.5  # and only if at least half the weight could be compared
MAX_SIMILAR = 10
MIN_SIMILAR_FOR_LENS = 3  # fewer than this is not a pattern
OFTEN_SHARE = 0.5  # recommended to at least half the similar students
GPA_SPAN = 15.0  # percentage points at which GPA similarity reaches zero
BUDGET_SPAN = math.log(2)  # a budget twice as large (or half) has zero similarity

PROFILE_FIELDS = list(StudentProfile.model_fields)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PastSession(BaseModel):
    id: int
    counsellor: str
    as_of: date
    preset: str
    profile: dict  # confirmed values only
    recommended: list[str]
    demo: bool = False


class LensCourse(BaseModel):
    course_id: str
    course_name: str
    count: int  # similar students who were recommended this course
    of: int
    here: Literal["picked", "ranked", "set_aside", "not_listed"]
    rank: Optional[int] = None
    note: Optional[str] = None


class Divergence(BaseModel):
    kind: Literal["often_missing", "rarely_given"]
    course_id: str
    course_name: str
    count: int
    of: int
    text: str


class Lens(BaseModel):
    similar: int
    counsellors: int
    demo: int  # how many of the similar sessions are illustrative demo data
    history_total: int
    compared: Literal["your_picks", "top_ranked"]
    common: list[LensCourse]
    divergences: list[Divergence]
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Similarity (pure)
# ---------------------------------------------------------------------------

def confirmed_values(profile: StudentProfile) -> dict:
    out = {}
    for name in PROFILE_FIELDS:
        if name == "budget_stretch_pct":
            continue
        value = profile.confirmed(name)
        if value is not None:
            out[name] = value.model_dump() if isinstance(value, BaseModel) else value
    return out


def _jaccard(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 1.0


def _tags(a: list, b: list) -> float:
    """Symmetric tag match with partial credit for related tags, scaled to 0..1."""
    forward = tag_similarity(a, b)[0]
    backward = tag_similarity(b, a)[0]
    return ((forward + backward) / 2 - NO_MATCH_FLOOR) / (1 - NO_MATCH_FLOOR)


def field_similarity(field: str, a, b) -> float:
    if field == "gpa_percent":
        return max(0.0, 1 - abs(a - b) / GPA_SPAN)
    if field == "budget_inr":
        if a <= 0 or b <= 0:
            return 0.0
        return max(0.0, 1 - abs(math.log(a / b)) / BUDGET_SPAN)
    if field in ("interest_tags", "career_tags"):
        return _tags(a, b)
    return _jaccard(a, b)


def similarity(a: dict, b: dict) -> tuple[float, float]:
    """Returns (similarity 0..1, share of the weight that could be compared)."""
    total = 0.0
    weight = 0.0
    for field, w in SIMILARITY_WEIGHTS.items():
        if field in a and field in b:
            total += w * field_similarity(field, a[field], b[field])
            weight += w
    if weight == 0:
        return 0.0, 0.0
    return round(total / weight, 3), round(weight, 3)


def similar_sessions(profile: dict, history: list[PastSession]) -> list[tuple[float, PastSession]]:
    scored = []
    for s in history:
        score, shared = similarity(profile, s.profile)
        if score >= SIMILAR_AT and shared >= MIN_SHARED_WEIGHT:
            scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    return scored[:MAX_SIMILAR]


# ---------------------------------------------------------------------------
# The lens (pure)
# ---------------------------------------------------------------------------

def lens(
    profile: dict,
    history: list[PastSession],
    picked: list[str],
    ranked: list[str],
    set_aside: dict[str, str],
    names: dict[str, str],
) -> Lens:
    """Compare a shortlist with what similar past students were recommended.

    picked: courses the counsellor marked as recommended (compared when present).
    ranked: the engine's ranked course ids, best first (top five compared when nothing is picked).
    set_aside: course id -> reason it is set aside for this student.
    """
    similar = similar_sessions(profile, history)
    compared = "your_picks" if picked else "top_ranked"
    shortlist = list(picked) if picked else ranked[:5]
    base = dict(
        similar=len(similar),
        counsellors=len({s.counsellor for _, s in similar}),
        demo=sum(1 for _, s in similar if s.demo),
        history_total=len(history),
        compared=compared,
    )
    if len(similar) < MIN_SIMILAR_FOR_LENS:
        return Lens(**base, common=[], divergences=[], message="Not enough similar past sessions to compare yet")

    n = len(similar)
    counts: dict[str, int] = {}
    for _, s in similar:
        for cid in set(s.recommended):
            counts[cid] = counts.get(cid, 0) + 1

    def where(cid: str) -> tuple[str, Optional[int], Optional[str]]:
        if cid in picked:
            return "picked", None, None
        if cid in ranked:
            rank = ranked.index(cid) + 1
            return "ranked", rank, f"Ranked #{rank} for this student"
        if cid in set_aside:
            return "set_aside", None, f"Set aside for this student: {set_aside[cid]}"
        return "not_listed", None, None

    common = []
    for cid, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:5]:
        here, rank, note = where(cid)
        common.append(LensCourse(course_id=cid, course_name=names.get(cid, cid), count=count, of=n, here=here, rank=rank, note=note))

    divergences = []
    for cid, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        if count / n >= OFTEN_SHARE and cid not in shortlist:
            _, _, note = where(cid)
            text = f"{count} of {n} similar students were recommended this; it is not on {'your picks' if picked else 'the top five'}"
            if note:
                text += f". {note}"
            divergences.append(Divergence(kind="often_missing", course_id=cid, course_name=names.get(cid, cid), count=count, of=n, text=text))
    # "Nobody similar got this" is about the counsellor's own choices, so it needs real picks;
    # the engine's lower ranked courses are not worth interrupting for.
    for cid in picked:
        if counts.get(cid, 0) == 0:
            divergences.append(Divergence(
                kind="rarely_given", course_id=cid, course_name=names.get(cid, cid), count=0, of=n,
                text=f"None of {n} similar students were recommended this",
            ))
    return Lens(**base, common=common, divergences=divergences)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class HistoryStore:
    """A thin SQLite wrapper. One row per saved session."""

    def __init__(self, path: Path | str, seed_demo: bool = True):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                saved_at TEXT NOT NULL,
                counsellor TEXT NOT NULL,
                as_of TEXT NOT NULL,
                preset TEXT NOT NULL,
                config_version TEXT NOT NULL,
                profile TEXT NOT NULL,
                recommended TEXT NOT NULL,
                demo INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._conn.commit()
        if seed_demo and self.count() == 0:
            self.seed_demo()

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def seed_demo(self, file: Path = DEMO_FILE) -> int:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        for s in data["sessions"]:
            profile = confirmed_values(StudentProfile(**s["profile"]))
            self.save(s["counsellor"], s["as_of"], s["preset"], "demo", profile, s["recommended"], demo=True)
        return len(data["sessions"])

    def save(self, counsellor: str, as_of, preset: str, config_version: str, profile: dict,
             recommended: list[str], demo: bool = False, session_id: Optional[int] = None) -> int:
        row = (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            counsellor or "Unnamed counsellor",
            str(as_of),
            preset,
            config_version,
            json.dumps(profile, sort_keys=True),
            json.dumps(sorted(set(recommended))),
            int(demo),
        )
        with self._lock:
            if session_id is not None:
                cur = self._conn.execute(
                    "UPDATE sessions SET saved_at=?, counsellor=?, as_of=?, preset=?, config_version=?, profile=?, recommended=?, demo=? WHERE id=? AND demo=0",
                    (*row, session_id),
                )
                if cur.rowcount:
                    self._conn.commit()
                    return session_id
            cur = self._conn.execute(
                "INSERT INTO sessions (saved_at, counsellor, as_of, preset, config_version, profile, recommended, demo) VALUES (?,?,?,?,?,?,?,?)",
                row,
            )
            self._conn.commit()
            return cur.lastrowid

    def all(self) -> list[PastSession]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, counsellor, as_of, preset, profile, recommended, demo FROM sessions ORDER BY id"
            ).fetchall()
        return [
            PastSession(id=r[0], counsellor=r[1], as_of=r[2], preset=r[3], profile=json.loads(r[4]), recommended=json.loads(r[5]), demo=bool(r[6]))
            for r in rows
        ]


_store: Optional[HistoryStore] = None


def default_store() -> HistoryStore:
    """The shared store, created on first use. GG_SESSIONS_DB moves it; GG_SEED_DEMO=0 skips demo data."""
    global _store
    path = os.getenv("GG_SESSIONS_DB", str(DEFAULT_DB))
    if _store is None or _store.path != path:
        _store = HistoryStore(path, seed_demo=os.getenv("GG_SEED_DEMO", "1") != "0")
    return _store

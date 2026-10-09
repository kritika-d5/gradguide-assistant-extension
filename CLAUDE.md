# GradGuide Counsellor Assist

Hiring assignment for GradGuide (study abroad counselling). Built by Kritika, a final year
B.Tech student. Full brief: `docs/assignment.md`. Ranking design: `docs/scoring.md`.

## What we are building

A course recommendation assistant a counsellor uses **during a live Google Meet session**. It is
a Chrome MV3 extension with a private side panel next to Meet, backed by the FastAPI service in
`backend/`.

## Product principles (every feature must obey these)

1. **Assist, never replace.** The counsellor decides. Nothing is auto applied; suggestions are
   chips the counsellor accepts, edits or dismisses.
2. **Never speak to the student.** All output is written for the counsellor's eyes only.
3. **Quiet by default.** Mid call the counsellor can glance for about 2 seconds. Only high value
   flags interrupt; everything else waits.
4. **Always show the why.** Every recommendation or flag carries a one line reason.
5. **Fill the gaps a human cannot:** catalogue breadth, fine print under pressure (eligibility,
   deadlines, true cost), remembering everything said, and consistency across counsellors.

## Decisions already made (and why)

* **Extension, not a Meet bot.** A bot is visible to the student, has no private channel, needs a
  fragile login or restricted API, and raises consent issues. The extension reads Meet's live
  captions from the DOM (opt in) instead. Meet class names are obfuscated, so target ARIA roles
  and always keep manual input as a fallback.
* **Deterministic ranking, no LLM in the ranking path.** Consistency is GradGuide's core problem.
  An LLM is only for grounded Q&A over course records, with source links.
* **Gates for dealbreakers** (eligibility, degree level, budget ceiling, intake), then a
  **weighted geometric mean** so one strong dimension cannot hide a weak one. A plain weighted
  sum was rejected for exactly that reason.
* **Priority presets** (balanced, budget_first, career_first, work_rights_first, prestige_first)
  in `backend/config/presets.yaml`, versioned. Every result records preset and config version.
* **Only confirmed profile fields count.** Caption derived values are `pending` until confirmed.
  Unknown fields get a neutral sub score and lower a `confidence` value instead of a penalty.
* **Bands and templated reasons**, not raw scores, because inputs are coarse.
* **Related tags get partial credit** (`backend/app/taxonomy.py`). Found during calibration: exact
  only matching made "data scientist" vs "ML engineer" a total mismatch.
* **A "match" outranks a "safe" academically, and degree level is a gate.** Found during
  calibration: a strong student was being steered to a private college certificate.

## The three original features

1. **Fine print guard:** silent eligibility, deadline and true cost checks with flags.
2. **Live profile and gap tracker:** captions propose pending chips; the panel shows unknown
   fields ranked by importance with a suggested next question (`gaps` in the API).
3. **Consistency lens:** compare the shortlist with what similar past profiles received and
   highlight divergence, without blocking. Not built yet; needs session logging.

The stability check (`close_calls`) and session summary export are supporting ideas.

## Status

* Done: backend engine, eligibility, gates, presets, gap tracker, stability check, search API,
  calibration personas, 32 passing tests.
* Next: Chrome MV3 side panel (profile chips, ranked list with bands and reasons, set aside
  list, gaps with question, preset switcher, search). Then caption listening, consistency
  lens with session logging, grounded Q&A, session summary export, write up and video.

## Commands

```bash
cd backend
pip install -r requirements.txt
pytest                          # must stay green
python -m app.catalog           # validate course data after editing YAML
python -m app.calibrate --all   # persona agreement; rerun after changing weights
uvicorn app.api:app --reload    # API on :8000, docs at /docs
```

## Conventions

* Python 3.11+, Pydantic v2, FastAPI. Engine functions stay pure: same inputs, same output. Pass
  `as_of` explicitly instead of reading the clock inside scoring.
* Course data lives in `backend/data/*.yaml`; never hard code course rules in Python.
* Bundled course data is **illustrative**; never present it as verified.
* When changing scoring, update `docs/scoring.md` and add or adjust personas in
  `backend/tests/personas.yaml`.
* Kritika prefers **no hyphens in written prose** (docs, README, UI copy). Code identifiers are fine.
* Interview defensibility matters: prefer simple, explainable designs she can justify.

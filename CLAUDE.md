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
  An LLM is only for grounded Q&A over course records, with source links, and for *proposing*
  profile values from noisy captions (see next point).
* **Hybrid caption reading: rules plus an optional model.** Found in a real test call: Meet's
  captions mishear words ("eyelid" for IELTS, "btec" for B.Tech) and add stray full stops, so
  rules alone miss too much. Browser rules (`extension/extract.js`) give instant offline chips;
  when the counsellor opts in, the backend (`backend/app/llm_extract.py`) sends a rolling
  transcript window to a model at most every 20 seconds. Safeguards: every value must quote the
  transcript or it is rejected, values come back as strings and are parsed and range checked in
  Python, tags are limited to the catalogue, and results are pending chips only. Provider is
  pluggable: Groq (default, free tier, `openai/gpt-oss-120b`) or Anthropic, via `LLM_PROVIDER`
  and keys in `backend/.env`. Without a key everything still works on rules.
* **Gates for dealbreakers** (eligibility, ruled out country, degree level, budget ceiling,
  intake), then a
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
   highlight divergence, without blocking. Counsellors star recommended courses and save the
   session (`/sessions`, SQLite in `backend/var/`, git ignored); `/consistency` finds similar
   past students (`backend/app/sessions.py`, pure similarity and lens functions) and flags
   courses most of them got that are missing, with the set aside reason when there is one, and
   picks none of them got. Seeded once with 12 illustrative sessions
   (`backend/data/demo_sessions.yaml`, `GG_SEED_DEMO=0` to skip).

The stability check (`close_calls`) and session summary export are supporting ideas.

## Status

* Done: backend engine, eligibility, gates, presets, gap tracker, stability check, search and
  vocabulary API, calibration personas, consistency lens with shared session history, 61
  passing tests. Chrome MV3 side panel in `extension/`
  (plain ES modules, no build step): profile chips with pending accept/edit/dismiss, ask next,
  ranked list with bands, reasons and flags, close calls, set aside list, preset switcher,
  confidence, search, GradGuide branding with bundled fonts. Opt in caption listening
  (`captions.js` content script on Meet) and a notes box, both using the rule based extractor
  `extract.js` (24 Node tests, noise tolerant, includes two real Meet caption transcripts) and
  producing pending chips with the heard sentence. Ruled out countries (a gate) and a preset
  suggestion when the student names their top priority. Optional AI reading (`/extract`, Groq or Anthropic) with quote checking and per
  student consent. `start-all.bat` for one click start on Windows.
* Next: grounded Q&A over course records, session summary export, video walkthrough.

## Commands

```bash
cd backend
pip install -r requirements.txt
pytest                          # must stay green
python -m app.catalog           # validate course data after editing YAML
python -m app.calibrate --all   # persona agreement; rerun after changing weights
uvicorn app.api:app --reload    # API on :8000, docs at /docs
node --test extension/tests/extract.test.js   # from the repo root; must stay green
```

On Windows `start-all.bat` does the backend setup and start in one go.

## Conventions

* Python 3.11+, Pydantic v2, FastAPI. Engine functions stay pure: same inputs, same output. Pass
  `as_of` explicitly instead of reading the clock inside scoring.
* Course data lives in `backend/data/*.yaml`; never hard code course rules in Python.
* Bundled course data is **illustrative**; never present it as verified.
* When changing scoring, update `docs/scoring.md` and add or adjust personas in
  `backend/tests/personas.yaml`.
* Kritika prefers **no hyphens in written prose** (docs, README, UI copy). Code identifiers are fine.
* Interview defensibility matters: prefer simple, explainable designs she can justify.

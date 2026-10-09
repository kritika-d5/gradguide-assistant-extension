# GradGuide Counsellor Assist

A course recommendation assistant that sits beside a live Google Meet counselling session as a
private side panel. It supports the counsellor rather than replacing them: it never speaks to the
student, never applies anything without the counsellor's confirmation, and focuses on what is hard
for a human to do mid conversation (catalogue breadth, fine print, tracking what was said, and
staying consistent across counsellors).

> **Status:** backend engine and API complete. Chrome extension side panel is next.

## What is in this repo

```
backend/
  app/
    models.py        course, country and student profile models (every profile field has a status)
    catalog.py       loads and validates the YAML catalogue
    eligibility.py   rules as data: pass, fail or unknown
    taxonomy.py      related field and career tags for partial credit
    engine.py        gates, weighted geometric mean, bands, reasons, stability check, gap tracker
    config.py        loads priority presets
    calibrate.py     checks the engine against counsellor style personas
    api.py           FastAPI endpoints for the side panel
  config/presets.yaml   versioned weights for each priority preset
  data/                 courses.yaml and reference.yaml (illustrative values)
  tests/                engine, API and persona tests
docs/scoring.md         how ranking works and why
```

## Run it

```bash
cd backend
pip install -e ".[dev]"
pytest                          # 32 tests, including determinism and persona checks
python -m app.catalog           # validate the course data
python -m app.calibrate --all   # agreement with persona expectations under every preset
uvicorn app.api:app --reload    # API on http://localhost:8000, docs at /docs
```

## Recommendation approach (short version)

1. **Only confirmed facts count.** Anything heard in captions is a pending chip until the
   counsellor confirms it.
2. **Dealbreakers are gates, not weights.** Ineligible, over budget, wrong degree level or closed
   intake courses are set aside with the reason, never silently hidden.
3. **Weighted geometric mean** for the rest, so one strong dimension cannot hide a weak one.
4. **Priority presets** (Balanced, Budget first, Career outcome first, Work rights first, Prestige
   first) let the counsellor explore alternatives. Each result records preset and config version.
5. **Unknowns are neutral** and lower a confidence value instead of penalising the course.
6. **Bands and templated reasons** instead of false precision; no LLM in the ranking path.
7. **Stability check** reports close calls whose order flips under small weight changes.

Full detail in [docs/scoring.md](docs/scoring.md).

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status, course count, config version |
| GET | `/presets` | available priority presets and their weights |
| POST | `/recommend` | profile + preset → ranked, set aside, close calls, gaps |
| GET | `/courses?q=&country=&tag=&max_cost_inr=` | deterministic keyword search |
| GET | `/courses/{id}` | full course record with total cost and work rights |

Profile fields can be sent as bare values (treated as confirmed) or as
`{"value": ..., "status": "pending", "source": "caption"}`.

## Updating course data

Course data is configuration, not code. Edit `backend/data/courses.yaml` (eligibility rules are
rows there too), run `python -m app.catalog` to validate, then `pytest` to make sure the personas
still behave. Living costs, work rights and exchange rates live once in `reference.yaml`.

**The bundled data is illustrative.** Institution names are real, but fees, deadlines, costs and
visa rules are approximations for the demo and must be verified before real use.

## Roadmap

- [ ] Chrome MV3 side panel next to Google Meet (profile chips, ranked list, gaps, search)
- [ ] Opt in caption listening that proposes pending profile chips
- [ ] Consistency lens: compare with similar past session profiles
- [ ] Grounded Q&A over course records with source links
- [ ] Session summary export for the student

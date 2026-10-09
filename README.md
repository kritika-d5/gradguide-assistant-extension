# GradGuide Counsellor Assist

A course recommendation assistant that sits beside a live Google Meet counselling session as a
**private Chrome side panel**. It listens (only if the counsellor opts in) to what the student
says, builds the profile as the conversation goes, and keeps a ranked, explained shortlist ready,
so the counsellor can recommend consistently without breaking eye contact with the student.

It assists and never replaces: the student never sees it, nothing is applied without the
counsellor's confirmation, and every recommendation carries its reason.

> 🎥 **Video walkthrough:** _link to be added_
> <!-- Replace with the walkthrough link, e.g. [Watch the walkthrough](https://...) -->


---

## Contents

1. [How the brief is covered](#how-the-brief-is-covered)
2. [The problem as I understood it](#the-problem-as-i-understood-it)
3. [Interaction model: a private side panel](#interaction-model-a-private-side-panel)
4. [A session, step by step](#a-session-step-by-step)
5. [Recommendation approach](#recommendation-approach)
6. [Handling incomplete information](#handling-incomplete-information)
7. [Three original features](#three-original-features)
8. [Course data: structure and updates](#course-data-structure-and-updates)
9. [Run it](#run-it)
10. [Testing and quality](#testing-and-quality)
11. [Limitations and next steps](#limitations-and-next-steps)
12. [Repository layout and API](#repository-layout-and-api)

---

## How the brief is covered

| The brief asks for | Where it lives |
|---|---|
| Recommend relevant courses from the profile | Deterministic engine: gates, then a weighted geometric mean ([details](#recommendation-approach)) |
| Show university, country, fees, intake, eligibility | Every shortlist card shows university, country and **true total cost** (tuition plus living, in ₹); expanding it shows the intake, eligibility status, any close deadline and the source link. Search shows every intake with its deadline and all entry rules |
| Explain why a course is relevant | A one line reason on every card, the full list of templated reasons and sub scores on click, and a reason for every course that was set aside |
| Explore alternative recommendations | Five **priority presets** (Balanced, Budget first, Career outcome first, Work rights first, Prestige first) with arrows showing what moved; "Show all ranked"; the set aside list; close call notes |
| Search or ask questions about courses | Keyword search across the catalogue with each result's standing for this student. Free form Q&A is not built yet ([next steps](#limitations-and-next-steps)) |
| Work alongside Google Meet without disrupting it | Chrome MV3 side panel, private to the counsellor; reads Meet's own captions, never joins the call |
| Decide profile capture, matching, ranking, explanation, incomplete info | [Session flow](#a-session-step-by-step), [recommendation approach](#recommendation-approach), [incomplete information](#handling-incomplete-information) |
| Explain course data structure and updates | [Course data](#course-data-structure-and-updates) |
| Three original features | [Live profile from the call](#1-live-profile-from-the-call), [Fine print guard](#2-fine-print-guard), [Consistency lens](#3-consistency-lens) |

---

## The problem as I understood it

GradGuide's stated problem is **consistency**: two similar students can get different
shortlists depending on the counsellor. Watching how a session actually runs, the causes are
practical rather than a lack of expertise:

* **Nobody can hold the whole catalogue in their head**, so each counsellor leans on the courses
  they know best.
* **Fine print is easy to miss under pressure:** a 3 year degree a German university will not
  accept, a lowest IELTS band below the minimum, a deadline in two weeks, living costs that push
  a "cheap" course over budget.
* **Details get lost mid conversation:** the student mentions a budget early and a refused
  country later, and the shortlist in the counsellor's head does not update.
* **Judgement calls are invisible:** when two courses are nearly tied, which one gets mentioned
  first depends on the person.

So the assistant's job is to carry the parts a human finds hard in real time (breadth, fine
print, memory, consistency) while leaving the judgement and the relationship with the
counsellor. Five product principles follow and every feature obeys them: assist never replace,
never speak to the student, quiet by default (a two second glance mid call), always show the
why, and fill only the gaps a human cannot.

---

## Interaction model: a private side panel

I chose a **Chrome extension with a side panel** next to the Meet tab, and rejected a meeting
bot.

| | Side panel (chosen) | Meeting bot |
|---|---|---|
| Visible to the student | No | Yes, as a participant |
| Private channel to the counsellor | Yes | Needs a separate app anyway |
| Setup | Load once, click the icon | Bot login, restricted Meet APIs, admin approval |
| Hearing the conversation | Reads Meet's own live captions in the counsellor's browser (opt in) | Records audio, which raises consent issues |
| If something breaks | Manual entry and a notes box always work | The session loses the tool |

Meet's class names are obfuscated and change often, so the caption reader finds the captions
area by its ARIA role and label, and manual input is always available as a fallback.

The panel follows the GradGuide brand from gradguide.in (cream background, brand red and coral,
blush badges, black pill buttons, Plus Jakarta Sans over Inter, fonts bundled so it works
offline).

---

## A session, step by step

1. **Open the panel** beside the Meet call. The shortlist already shows, marked low confidence.
2. **Turn on Listen to captions** (and Meet's CC). As the student talks, values appear as amber
   **pending chips**, each showing the sentence it came from: "GPA 76%? ✓ ✎ ✕".
3. **Accept, edit or dismiss** each chip with one click. Only accepted values reach the ranking.
   Dismissed values are not proposed again.
4. **Ask next** suggests the most valuable missing fact and the question to ask, for example
   "What is your current aggregate or CGPA? Eligibility of 19 courses waits on this".
5. **The shortlist updates** with every accepted value: top five, each with a band (Strong,
   Good, Stretch), a one line reason and at most one warning flag (a deadline, a reach, a soft
   rule).
6. **Explore alternatives** by switching preset. If the student says "budget is my top
   priority", the panel suggests **Switch to Budget first**; it never switches by itself.
7. **Check anything** with Search, which also shows why a course is not on the list ("Set
   aside: over budget").
8. **Mark what you recommend** with ☆. The **consistency check** above the shortlist compares
   your picks with what similar past students were recommended, and flags differences ("5 of 6
   similar students were recommended TU Dortmund; it is not on your picks").
9. **Save session** at the end, so the next counsellor's consistency check includes this
   student.

Typing is always an option: click any chip, or paste notes into "Type or paste what the student
said" and the same extractor runs.

---

## Recommendation approach

The ranking is a **deterministic engine**, no LLM in the ranking path. The same confirmed
profile, preset and date always give the same ranking and the same reasons, which is the point
of a consistency tool. Full design: [docs/scoring.md](docs/scoring.md).

```
confirmed profile ─► eligibility ─► gates ─► fit score ─► bands + reasons
                                      │                       │
                                      ▼                       ▼
                              set aside list          stability check, gap tracker
                              (with reasons)
```

1. **Only confirmed facts count.** Anything heard in captions waits as a pending chip.
2. **Eligibility rules are data, not code.** Each course lists rules (minimum GPA, IELTS overall
   and lowest band, 3 year degree accepted, backlogs, required background, work experience).
   Each rule returns pass, fail or **unknown**. A failed hard rule sets the course aside; a
   failed soft rule keeps it with a warning.
3. **Dealbreakers are gates, not weights.** A plain weighted sum lets strengths hide a
   dealbreaker: an unaffordable course can still score 80. So ineligibility, a **ruled out
   country**, the wrong degree level, cost above the budget ceiling, and a closed or missing
   intake each set the course aside, with the reason. Set aside courses are listed, never
   hidden, because the counsellor may know something the data does not.
4. **A weighted geometric mean ranks the rest.** Seven sub scores (field fit, career fit, budget
   fit, academic fit, country preference, post study work rights, reputation) are combined as
   `100 × Π subscore^weight`. Unlike a sum, one very weak dimension pulls the score down, so a
   course that is perfect except for budget drops from 81 under a sum to about 55.
5. **Related tags get partial credit**, so "data scientist" and "ML engineer" are neighbours, not
   a total mismatch (found during calibration).
6. **A good academic match outranks a safe option**, so a strong student is not steered to an
   easy admit (also found during calibration).
7. **Priority presets** set the weights per student: the counsellor picks one and can switch at
   any time to explore. Every result records the preset and config version.
8. **Bands and templated reasons, not raw scores.** Inputs like tag overlap and living costs are
   coarse, so the panel shows Strong match, Good match or Stretch with reasons such as "Within
   budget (₹27.4L of ₹30L)" or "Reach: GPA 78% vs typical 85%".

---

## Handling incomplete information

Early in a session almost nothing is known, and the assistant must still be useful.

* **Unknown is neutral, not a penalty.** A sub score whose input is unknown gets a neutral 0.6,
  so missing data does not punish any course.
* **Confidence is shown.** The header shows the share of the preset's weight backed by confirmed
  data ("Confidence 13%"), and the shortlist says when the order is provisional.
* **Eligibility can be "unconfirmed".** A rule that needs an unknown field never fails; the card
  says what is missing ("Eligibility unconfirmed: needs IELTS overall (minimum 6.5)").
* **Ask next turns gaps into questions.** Unknown fields are ranked by their weight in the
  current preset plus how many eligibility rules wait on them, and the top one comes with a
  suggested question. The counsellor always knows the most useful thing to ask.

---

## Three original features

### 1. Live profile from the call

**Problem.** Counsellors cannot type a profile while holding a conversation, so facts said early
(a budget, a refused country) are forgotten by the time courses are discussed.

**Why it matters.** A shortlist is only as good as the profile behind it. If the tool needs
typing, it will not be used mid call.

**How it works.**
* **Opt in caption reading.** A content script reads Meet's live captions in the counsellor's
  browser. It never joins the call, never changes the Meet page, and switches off when the
  browser restarts.
* **Pending chips, never auto applied.** Each heard value becomes a chip with accept, edit and
  dismiss, and shows the sentence it came from. Only accepted values reach the ranking.
* **Hybrid extraction, built from real test calls.** I tested on real Meet calls and found the
  captions noisy: "eyelid score" for IELTS, "btec" for B.Tech, "1.5 CRS" for crore, stray full
  stops ("My GPA is. 8"). So there are two layers:
  * **Browser rules** (`extension/extract.js`): instant and offline. They correct common
    mishearings, read spelled out numbers, rejoin broken sentences, tell interests from careers
    from background by context ("did my B.Tech in mechanical" vs "want to study data science" vs
    "become a data scientist"), respect negation ("not the UK or the US" becomes a **Ruled
    out** chip), and use a counsellor's question as context for the answer that follows.
  * **Optional AI reading** (`backend/app/llm_extract.py`): for mishearings rules cannot
    anticipate, a model reads the recent transcript, at most every 20 seconds. It is fenced in:
    opt in per student with a consent prompt; **every value must quote the transcript or it is
    rejected**, so it cannot invent a fact; values are parsed and range checked in Python (an
    IELTS score of 99 is rejected); tags are limited to the catalogue; and results are still
    only pending chips. The provider is pluggable: Groq's free tier by default, Claude as an
    alternative, and without a key the rules carry on.
* **Priority suggestion.** "The most important priority is budget" produces a suggestion to
  switch to the Budget first preset, which the counsellor accepts or ignores.

### 2. Fine print guard

**Problem.** The details that decide whether an application can succeed (eligibility rules,
deadlines, true cost) are exactly what is easy to miss while talking.

**Why it matters.** Recommending a course the student cannot get into, cannot afford, or has
already missed the deadline for wastes the student's time and the counsellor's credibility.

**How it works.**
* **Eligibility as data.** Every course carries its rules; each returns pass, fail or unknown,
  with hard and soft severity, so failures set a course aside and soft issues become warnings.
* **True total cost.** Cost is tuition plus living costs for the course length, converted to
  rupees, so a course with low tuition in an expensive city is not presented as cheap. A budget
  stretch (0 by default) is set by the counsellor, not assumed.
* **Deadline flags.** An intake whose deadline is within 30 days shows a flag on the card
  ("Jan 2027 deadline in 6 days"); a closed intake sets the course aside.
* **Ruled out countries are a gate.** When a student refuses a country, those courses are set
  aside with the reason instead of quietly scoring lower.
* **Quiet by default.** Only high value issues show on the card as a single flag; everything
  else waits behind a click.

### 3. Consistency lens

**Problem.** GradGuide's core problem: similar students get different shortlists depending on
who counsels them. A deterministic engine fixes the *suggestions*, but what reaches the student
is what the counsellor *recommends*, and nobody sees how that compares across counsellors.

**Why it matters.** Students and parents compare notes. A recommendation that differs from what
similar students got, without a reason, undermines trust in the whole service. Equally, a
difference can be right (this student refused the UK), so the tool must explain, not block.

**How it works.**
* **Recording what was recommended.** The counsellor marks recommended courses with ☆ and saves
  the session. The shared history stores only the confirmed profile, the preset, the config
  version and the recommended course ids: no names, no transcript.
* **Finding similar students.** A plain weighted similarity over the fields both profiles know:
  interests and career goal (with partial credit for related tags), budget (on a log scale, so
  ₹40L vs ₹80L is as different as ₹20L vs ₹40L), GPA, countries, background and degree level.
  A past student counts as similar at 0.7 or above, and only if at least half the weight could
  be compared.
* **Flagging differences, quietly.** With at least three similar students, the panel shows:
  * courses recommended to at least half of them that are not on your picks ("5 of 6 similar
    students were recommended this. Ranked #1 for this student"), and, when the course is set
    aside for *this* student, the reason ("Set aside for this student: Total cost ₹48.0L exceeds
    the ceiling of ₹40.0L"), so an explained difference reads as explained;
  * picks that none of the similar students were given.
  When everything agrees it shrinks to one line, "✓ Consistent with 5 similar past students".
  It never reorders or blocks anything, and a saved student is never compared with itself.
* **Shared across counsellors.** The history is one SQLite file on the backend, so every
  counsellor using the same server feeds and benefits from it. To demonstrate the feature, it
  starts with 12 clearly labelled illustrative sessions from three fictional counsellors,
  including one who steers data science students to cheaper UK and college options.

The lens sits on top of guarantees that make comparison meaningful:
* **Deterministic and versioned.** Same confirmed profile, preset and date give the same output,
  ties broken by course id. Every result records its preset and config version, so any
  shortlist can be reproduced later.
* **Stability check.** Each weight is nudged by ±0.05 and the top three recomputed. Any pair
  that swaps is reported as a **close call** ("Order flips with less weight on field fit: treat
  as a close call"), so the counsellor knows when two courses are effectively tied and the
  choice is theirs.
* **Calibrated against personas.** Nine student personas encode what an experienced counsellor
  would expect ("must be in the top 3", "must be set aside for budget"); `python -m
  app.calibrate --all` reports agreement under every preset (currently 102 of 105). Two of the
  design rules above came from failures found this way.
* **Explainable by construction.** Reasons are templates over the same sub scores the ranking
  uses, so the explanation can never disagree with the ranking.

---

## Course data: structure and updates

Course data is configuration, not code, in two YAML files validated by Pydantic models:

* `backend/data/courses.yaml`: one entry per course, including its eligibility rules.
* `backend/data/reference.yaml`: universities, country living costs and post study work rights,
  and exchange rates, each stored once.

```yaml
- id: uk_ucl_msc_dsml
  university_id: uk_ucl
  name: MSc Data Science and Machine Learning
  degree_level: masters
  field_tags: [data_science, ml, computer_science]
  career_tags: [data_scientist, ml_engineer]
  duration_months: 12
  tuition_total: 41000
  currency: GBP
  typical_admit_gpa_percent: 85
  intakes: [{term: sep, year: 2027, application_deadline: 2027-03-31, status: open}]
  eligibility_rules:
    - {type: min_gpa_percent, value: 75}
    - {type: min_ielts_overall, value: 7.0}
    - {type: min_ielts_band, value: 6.5, severity: soft}
    - {type: required_background, value: [computer_science, engineering, ...]}
```

**To update:** edit the YAML, run `python -m app.catalog` to validate (it rejects unknown rule
types, currencies and universities), then `pytest` so the personas confirm rankings still make
sense. Priority preset weights live in `backend/config/presets.yaml` with a version number. Tag
relationships for partial credit live in `backend/app/taxonomy.py`. Every course carries
`source_url`, `last_verified` and `data_status`, so stale or unverified data is visible.

**The bundled data is illustrative:** 19 courses at 16 real universities in the UK, Canada and
Germany, with approximate fees, deadlines, costs and visa rules. The panel labels it
"Illustrative data" and it must be verified before real use.

---

## Run it

**Windows: double click `start-all.bat`.** It creates the Python environment on first run,
installs requirements, checks the course data, starts the API in its own window and opens
`chrome://extensions`. Then, once:

1. Turn on **Developer mode**, click **Load unpacked** and choose the `extension` folder.
2. Pin the GradGuide icon, open a Google Meet call and click it.

**macOS, Linux or by hand** (Python 3.11+):

```bash
cd backend
pip install -r requirements.txt
uvicorn app.api:app --reload    # API on http://localhost:8000, interactive docs at /docs
```

then load the `extension` folder in Chrome as above.

**Optional AI reading:** put a free Groq key from https://console.groq.com/keys in
`backend/.env` as `GROQ_API_KEY=...` (see `backend/.env.example`; `ANTHROPIC_API_KEY` switches to
Claude), restart the API, and click **AI off** next to **Listen to captions** to turn it on.
Everything else works without a key.

**Trying it without a Meet call:** paste a transcript into "Type or paste what the student
said". For a demo of the caption path, the panel's console has `ggTranscript("line one", "line
two")`.

---

## Testing and quality

| Check | Command | Status |
|---|---|---|
| Engine, API, personas, consistency lens, AI extraction safeguards (model mocked) | `cd backend && pytest` | 61 passing |
| Caption extractor, including three real Meet transcripts | `node --test extension/tests/extract.test.js` | 26 passing |
| Persona agreement under every preset | `cd backend && python -m app.calibrate --all` | 102 / 105 |
| Course data validation | `cd backend && python -m app.catalog` | 19 courses OK |

Engine functions are pure (the date is passed in as `as_of`, never read from the clock), which
is what makes determinism testable. The AI extraction tests replace the model with a fake, so
they check the safeguards (quote grounding, range checks, vocabulary) without network calls.

**Privacy.** Student data stays in `chrome.storage.session` (cleared when the browser closes or
with **New student**). Caption text leaves the browser only if the counsellor turns on AI
reading for that student, after a consent prompt. Saved sessions keep only confirmed profile
values and recommended course ids, in `backend/var/sessions.db` (git ignored). API keys live only in `backend/.env`, which is
git ignored.

---

## Limitations and next steps

Being explicit about what is not done:

* **Free form Q&A about courses** ("does this course accept a 3 year degree?") is not built;
  search and course details answer most of it today. The plan is grounded answers over the
  course records with source links, using the same model setup.
* **The consistency history starts with illustrative sessions.** Similarity weights and the
  "half of similar students" threshold are hand set; with real saved sessions they should be
  tuned the same way the ranking weights were, against counsellor judgement.
* **Session summary export** for the student after the call.
* **Small illustrative catalogue** (19 courses, 3 countries); the structure is ready for real,
  verified data.
* **Caption reading depends on Meet's page** and is tuned for English captions. If Meet changes,
  the panel says "Turn on captions in Meet" and manual entry keeps working.
* **CGPA to percent uses × 9.5**, a common Indian convention; the chip states the conversion so
  the counsellor can correct it for universities that use another formula.
* **The extension is not on the Chrome Web Store**; it loads unpacked.

---

## Repository layout and API

```
backend/
  app/
    models.py         course, country and student profile models (every field has a status)
    catalog.py        loads and validates the YAML catalogue
    eligibility.py    rules as data: pass, fail or unknown
    taxonomy.py       related tags for partial credit
    engine.py         gates, geometric mean, bands, reasons, stability check, gap tracker
    config.py         loads versioned priority presets
    calibrate.py      persona agreement report
    sessions.py       session history and the consistency lens
    llm_extract.py    optional AI reading of captions, with quote grounding
    api.py            FastAPI endpoints for the panel
  config/presets.yaml
  data/               courses.yaml, reference.yaml, demo_sessions.yaml (illustrative)
  tests/              engine, API, persona, lens and extraction tests
extension/
  manifest.json       Chrome MV3: side panel plus a content script on meet.google.com
  sidepanel.*         the counsellor's panel
  captions.js         opt in caption reader on the Meet page
  extract.js          rule based extraction, noise tolerant
  fields.js, api.js   field metadata and the backend client
  fonts/              bundled brand fonts
  tests/              extractor tests
docs/scoring.md       ranking design in full
start-all.bat         one click start on Windows
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status, course count, config version |
| GET | `/presets` | priority presets and their weights |
| GET | `/vocabulary` | tags, countries, levels and intakes offered as chips |
| POST | `/recommend` | profile + preset → ranked, set aside, close calls, gaps |
| GET | `/courses?q=&country=&tag=&max_cost_inr=` | deterministic keyword search |
| GET | `/courses/{id}` | full course record with total cost and work rights |
| POST | `/sessions` | save (or update) a session: confirmed profile and recommended courses |
| POST | `/consistency` | compare a shortlist with similar past students |
| GET | `/extract/status` | whether AI reading is configured |
| POST | `/extract` | transcript → quote checked profile values (pending only) |

Profile fields can be sent as bare values (treated as confirmed) or as
`{"value": ..., "status": "pending", "source": "caption"}`.

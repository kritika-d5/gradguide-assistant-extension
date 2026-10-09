# How recommendations are ranked

The assistant ranks courses with a deterministic engine. The same confirmed profile, the same
priority preset and the same `as_of` date always produce the same ranking and the same reasons.
An LLM is never involved in ranking; it is reserved for free form questions about courses.

The score is a consistent, transparent heuristic for ordering options. It is not a measure of
truth, and the counsellor always makes the final call.

## Pipeline

```
confirmed profile ──► 1. eligibility ──► 2. gates ──► 3. fit score ──► 4. bands + reasons
                                              │                               │
                                              ▼                               ▼
                                     "set aside" list               5. stability check
                                     (with reasons)                 6. gap tracker
```

Only fields the counsellor has **confirmed** feed the engine. Values heard in captions stay as
pending chips until confirmed.

## 1. Eligibility

Every course carries rules as data (`eligibility_rules`), never as code. Each rule returns
**pass**, **fail** or **unknown**.

| Rule type | Example |
|---|---|
| `min_gpa_percent` | 60 |
| `min_ielts_overall` / `min_ielts_band` | 6.5 / 6.0 |
| `accepts_3yr_degree` | false (common in Germany) |
| `max_backlogs` | 0 |
| `required_background` | any of `computer_science`, `engineering` |
| `min_work_exp_months` | 24 |

Rules have a severity. A failed **hard** rule sets the course aside; a failed **soft** rule keeps
it ranked with a warning. A rule that depends on an unknown field marks eligibility as
**unconfirmed** and feeds a question into the gap tracker.

## 2. Gates (dealbreakers are filtered, not weighed)

A plain weighted sum lets strengths hide dealbreakers: a course that is unaffordable can still
score 80 if it is perfect on everything else. So anything a counsellor would never trade away is
a gate, and courses that fail a gate are listed separately with the reason.

| Gate | Fails when |
|---|---|
| Eligibility | a hard rule fails |
| Budget ceiling | total cost exceeds budget × (1 + stretch). Stretch defaults to 0 and is set by the counsellor |
| Intake | the target intake is not offered, or its deadline is before `as_of` |

Set aside courses are never hidden. The counsellor may know something the data does not.

## 3. Fit score (weighted geometric mean)

Each surviving course gets sub scores between 0 and 1:

| Sub score | Computation |
|---|---|
| `field_fit` | share of the student's interest tags found in the course's field tags |
| `career_fit` | share of the student's career tags found in the course's career tags |
| `budget_fit` | 1.0 up to 85% of budget, easing to 0.75 at exactly the budget, then down to 0.3 at the stretch ceiling |
| `academic_fit` | GPA vs the course's typical admit: safe 1.0, match 0.85, reach 0.5 |
| `preference_fit` | 1.0 if the country is one the student wants, 0.4 otherwise |
| `outcome_fit` | post study work rights, 36 months or more scores 1.0 |
| `reputation_fit` | ranking band: top 100 is 1.0, top 300 is 0.75, otherwise 0.5 |

The combination is a **weighted geometric mean**:

```
score = 100 × Π subscore_i ^ w_i      (weights sum to 1, every subscore floored at 0.05)
```

Unlike a sum, a very weak sub score pulls the whole result down, so one excellent dimension cannot
paper over a poor one. With the old weights, a course scoring 1.0 everywhere except a budget
score of 0.05 drops from 81 under a sum to about 55 under the geometric mean.

**Ties** are broken by course id after rounding to one decimal, so order never wobbles.

### Priority presets

Weights follow the student, not one global config. The counsellor picks a preset at the start of
the session and can switch at any time to explore alternatives. Every result states the preset
and config version that produced it.

| Preset | Emphasis |
|---|---|
| `balanced` | even spread |
| `budget_first` | self funded or cost sensitive students |
| `career_first` | students with a clear target role |
| `work_rights_first` | students planning to work abroad after study |
| `prestige_first` | students who value university reputation |

The values live in `backend/config/presets.yaml`.

### Unknown fields

An unknown field produces a **neutral** sub score of 0.6 rather than a penalty, and lowers
**confidence**: the share of total weight backed by confirmed data. Early in a session rankings are
usable but marked low confidence; they sharpen as gaps close.

## 4. Bands and reasons

The panel shows a band rather than a precise number, because inputs like tag overlap and living
cost estimates are coarse.

| Score | Band |
|---|---|
| 75 or more | Strong match |
| 55 to 74.9 | Good match |
| below 55 | Stretch |

Reasons are generated from templates over the sub scores, never by an LLM, for example
"Within budget (₹27.4L of ₹30L)" or "Reach: GPA 78% vs typical 85%".

## 5. Stability check

Each weight is nudged by ±0.05 (then renormalised) and the top 3 recomputed. Any pair of courses
that swap places is reported as a **close call**, so the counsellor knows when a ranking hinges on
the exact weights rather than a clear difference.

## 6. Gap tracker

Unknown fields are ranked by how much they matter: their weight in the current preset plus the
number of eligibility rules waiting on them. The top gap comes with a suggested question.

## Validation

`backend/tests/personas.yaml` holds student personas with expectations of the form
"must appear in the top 5" and "must be set aside". `python -m app.calibrate` reports how many
expectations hold under each preset. The included personas were written from the design intent;
they should be replaced or extended with shortlists labelled by real counsellors, and the preset
weights tuned until agreement is high.

// Run with: node --test extension/tests
const test = require("node:test");
const assert = require("node:assert/strict");
const { extract } = require("../extract.js");

/** Map of field -> value for compact assertions. */
function values(text) {
  return Object.fromEntries(extract(text).map((r) => [r.field, r.value]));
}

test("CGPA on a 10 point scale is converted to percent with the conversion noted", () => {
  const [r] = extract("My CGPA is 8.2 out of 10.");
  assert.equal(r.field, "gpa_percent");
  assert.equal(r.value, 77.9);
  assert.match(r.note, /9\.5/);
  assert.equal(values("I have an 8.5 CGPA").gpa_percent, 80.8);
});

test("percentages count as GPA only in an academic context", () => {
  assert.equal(values("I scored 78 percent aggregate in my B.Tech").gpa_percent, 78);
  assert.equal(values("I got 72% in college").gpa_percent, 72);
  assert.equal(values("there was a 20% fee hike").gpa_percent, undefined);
});

test("IELTS overall and lowest band", () => {
  assert.deepEqual(values("I got 7 in IELTS with 6 in writing"), { ielts_overall: 7, ielts_min_band: 6 });
  assert.deepEqual(values("IELTS overall 6.5, lowest band 6"), { ielts_overall: 6.5, ielts_min_band: 6 });
  assert.equal(values("my IELTS score was 7.5").ielts_overall, 7.5);
  assert.deepEqual(values("I haven't taken IELTS yet"), {});
});

test("budget in lakhs and crores, taking the upper end of a range", () => {
  assert.equal(values("Our budget is around 40 lakhs").budget_inr, 4000000);
  assert.equal(values("we can go up to 1.2 crore").budget_inr, 12000000);
  const [r] = extract("Somewhere between 30 and 45 lakhs in total");
  assert.equal(r.value, 4500000);
  assert.match(r.note, /Upper end/);
  assert.equal(values("I will take a loan of 20 lakhs").budget_inr, undefined);
});

test("backlogs and work experience", () => {
  assert.equal(values("I have no backlogs").backlogs, 0);
  assert.equal(values("I had two backlogs in second year").backlogs, 2);
  assert.equal(values("I have 2 years of work experience at TCS").work_exp_months, 24);
  assert.equal(values("I've been working for one and a half years").work_exp_months, 18);
  assert.equal(values("I'm a fresher").work_exp_months, 0);
  assert.equal(values("I did a 6 month internship").work_exp_months, undefined);
});

test("degree length, stated or inferred from the degree name", () => {
  assert.equal(values("It's a four year degree").degree_years, 4);
  const [r] = extract("I did my BTech from VIT").filter((x) => x.field === "degree_years");
  assert.equal(r.value, 4);
  assert.match(r.note, /Inferred/);
  assert.equal(values("I finished my BSc in statistics").degree_years, 3);
  assert.equal(values("I want to be a better person").degree_years, undefined);
});

test("intake needs a real cue, and 'may' must not be read as a verb", () => {
  assert.deepEqual(values("I'm aiming for the January 2027 intake").target_intake, { term: "jan", year: 2027 });
  assert.deepEqual(values("fall intake would be ideal").target_intake, { term: "sep", year: null });
  assert.equal(values("I may consider other options").target_intake, undefined);
});

test("interests, career and background are told apart by context", () => {
  const v = values("I did my B.Tech in mechanical but I want to study data science and become a data scientist");
  assert.deepEqual(v.background_tags, ["engineering", "mechanical"]);
  assert.deepEqual(v.interest_tags, ["data_science"]);
  assert.deepEqual(v.career_tags, ["data_scientist"]);
});

test("a job title is not also counted as an interest", () => {
  const v = values("I'm really interested in working as an ML engineer");
  assert.deepEqual(v.career_tags, ["ml_engineer"]);
  assert.equal(v.interest_tags, undefined);
});

test("countries need a preference cue and respect negation", () => {
  assert.deepEqual(values("I'm leaning towards Canada or Germany, not the UK").target_countries, ["canada", "germany"]);
  assert.equal(values("My uncle lives in Canada").target_countries, undefined);
});

test("degree level", () => {
  assert.deepEqual(values("I only want a master's").target_levels, ["masters"]);
  assert.deepEqual(values("I'm open to a PG diploma too").target_levels, ["pg_diploma"]);
});

test("questions are skipped because they are usually the counsellor asking", () => {
  assert.deepEqual(values("Is your budget around 40 lakhs?"), {});
});

test("later mentions win, the way people correct themselves", () => {
  assert.equal(values("My IELTS was 6.5. Sorry, IELTS 7 actually.").ielts_overall, 7);
});

test("every result carries the sentence it came from", () => {
  for (const r of extract("Our budget is 40 lakhs. I have no backlogs.")) {
    assert.ok(r.evidence.length > 0);
  }
});

test("answers to the counsellor's question use the question as context", () => {
  assert.deepEqual(values("Have you taken IELTS? Yes I got 7 overall, 6.5 in writing."), { ielts_overall: 7, ielts_min_band: 6.5 });
  assert.equal(values("What's your CGPA? It's 8.2.").gpa_percent, 77.9);
  assert.equal(values("Any backlogs? No, none.").backlogs, 0);
  assert.equal(values("What did you score in IELTS? 6.5").ielts_overall, 6.5);
});

test("abbreviations like B.E. do not end a sentence", () => {
  assert.deepEqual(values("I did my B.E. in computer science from Pune.").background_tags, ["computer_science", "engineering"]);
});

test("lists accumulate across sentences", () => {
  const v = values("I want to work as a data scientist. Eventually I want to be an ML engineer.");
  assert.deepEqual(v.career_tags, ["data_scientist", "ml_engineer"]);
  assert.deepEqual(values("I'm not sure about the country, maybe Canada.").target_countries, ["canada"]);
});

test("a speaker name line between question and answer keeps the link", () => {
  assert.equal(values("Counsellor\nHave you taken IELTS?\nRiya\nYes I got 7 overall").ielts_overall, 7);
  // but the question does not reach answers far away
  assert.equal(values("What's your CGPA? I'm from Pune. I like cricket. It's 8.").gpa_percent, undefined);
});

// Real Meet captions from a test call: misheard words and stray full stops.
const MEET_CAPTIONS =
  "scenario. So, any country where the? Global conflict and the global scenario is not great. " +
  "I would not want to. Go there for my parents sake. And my GPA is. 8, 8.0, and. Um, I don't " +
  "have any backlogs. My eyelid score is a 99. Yeah. So, I have completed my btec. In mechanical " +
  "engineering. And I want to pursue.";

test("real noisy Meet captions: stray full stops and misheard words", () => {
  const v = values(MEET_CAPTIONS);
  assert.equal(v.gpa_percent, 76); // "my GPA is. 8" rejoined
  assert.equal(v.backlogs, 0);
  assert.equal(v.degree_years, 4); // "btec" heard for B.Tech
  assert.deepEqual(v.background_tags, ["engineering", "mechanical"]); // "btec. In mechanical engineering"
  assert.equal(v.ielts_overall, undefined); // "eyelid score is a 99" is garbled: propose nothing
});

test("common mishearings are normalised", () => {
  assert.equal(values("my eyelid score is 7").ielts_overall, 7);
  assert.equal(values("we can spend 40 lacks").budget_inr, 4000000);
  assert.equal(values("I did my b tech in computer science").degree_years, 4);
});

test("'pursue' marks an interest", () => {
  assert.deepEqual(values("And I want to pursue data science.").interest_tags, ["data_science"]);
});

// Second real test call: ruled out countries, spelled out numbers, "CRS", stated priority.
const SECOND_CALL =
  "preferably abroad, so? But I do not want to be pursuing any. Course in the UK or the US because " +
  "of the global conflicts so preferably in a country that's considered safe for Indian students as " +
  "such. With, like a good Indian Community, because? The most important priority actually. Budget. " +
  "I was thinking around maybe one to two years unders to CR, but preferably under 1.5 CRS. Um, I have " +
  "no backlogs. My cgpa is eight and. Yeah. I just want to be working as a data scientist after I " +
  "complete my. Degree.";

test("second real call: everything the student said is picked up", () => {
  const v = values(SECOND_CALL);
  assert.deepEqual(v.excluded_countries, ["uk", "usa"]);
  assert.equal(v.target_countries, undefined);
  assert.equal(v.priority_preset, "budget_first");
  assert.equal(v.budget_inr, 15000000);
  assert.equal(v.backlogs, 0);
  assert.equal(v.gpa_percent, 76);
  assert.deepEqual(v.career_tags, ["data_scientist"]);
});

test("spelled out numbers", () => {
  assert.equal(values("my cgpa is eight point two").gpa_percent, 77.9);
  assert.equal(values("I scored seventy eight percent in my degree").gpa_percent, 78);
  assert.equal(values("my IELTS was seven point five").ielts_overall, 7.5);
});

test("negation stops at a change of mind", () => {
  assert.deepEqual(values("I don't want Germany and would prefer Canada").target_countries, ["canada"]);
  assert.deepEqual(values("I don't want Germany and would prefer Canada").excluded_countries, ["germany"]);
  assert.deepEqual(values("I'm not sure, maybe Canada").target_countries, ["canada"]);
});

// Third real test call: the refusal sits in its own "but" clause, background said as "have done".
const THIRD_CALL =
  "science aspect, and I know I have done mechanicals, so my courses do not exactly align, but I want " +
  "to do that and. Uh, I would prefer it to be abroad, but not in the UK or the US because of the " +
  "global conflict so. Specifically, a country where it is considered safe, especially for Indian " +
  "students so. That's one of the main priorities. My cgpa is around eight and. My eyelid score is. " +
  "99. Yeah, I want to pursue data science further.";

test("third real call: refusal without a preference word, 'have done mechanicals'", () => {
  const v = values(THIRD_CALL);
  assert.deepEqual(v.excluded_countries, ["uk", "usa"]);
  assert.deepEqual(v.background_tags, ["mechanical"]);
  assert.deepEqual(v.interest_tags, ["data_science"]);
  assert.equal(v.gpa_percent, 76);
  assert.equal(v.ielts_overall, undefined); // "eyelid score is. 99" stays garbled
  assert.equal(v.priority_preset, undefined); // the priority named was safety, not a preset
});

test("mentioning a country is not wanting or refusing it", () => {
  assert.deepEqual(values("My uncle lives in Canada"), {});
  assert.deepEqual(values("I don't know anyone in Canada"), {});
});

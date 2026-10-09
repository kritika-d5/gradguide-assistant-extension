// Rule based extraction of student profile values from caption text or typed notes.
//
// Deliberately simple and predictable: regular expressions and a small phrase list, no LLM. Every
// result is only a *proposal*. The panel shows it as a pending chip with the sentence it came from,
// and nothing counts until the counsellor accepts it. Runs entirely in the browser.
//
// Loaded as a classic script (content scripts cannot be ES modules), so it attaches to globalThis
// and also exports for the Node tests in tests/extract.test.js.

(function (root) {
  "use strict";

  // ---------------------------------------------------------------------------
  // Phrase lists. Keys are the tags the backend catalogue uses.
  // ---------------------------------------------------------------------------

  const INTEREST_TERMS = {
    data_science: ["data science"],
    ml: ["machine learning", "ml", "artificial intelligence", "ai", "deep learning"],
    computer_science: ["computer science", "cs", "cse"],
    software: ["software engineering", "software development", "software", "programming", "coding"],
    cybersecurity: ["cyber security", "cybersecurity", "information security", "security"],
    business_analytics: ["business analytics", "analytics"],
    statistics: ["statistics", "stats"],
    robotics: ["robotics"],
    mechanical: ["mechanical"],
    electrical: ["electrical", "electronics"],
    management: ["management", "mba"],
  };

  // Job titles: specific enough to count as a career goal wherever they appear.
  const CAREER_TERMS = {
    data_scientist: ["data scientist"],
    ml_engineer: ["ml engineer", "machine learning engineer", "ai engineer"],
    data_analyst: ["data analyst"],
    software_engineer: ["software engineer", "software developer", "developer", "sde", "programmer"],
    security_analyst: ["security analyst"],
    business_analyst: ["business analyst"],
    product_manager: ["product manager"],
    robotics_engineer: ["robotics engineer"],
    mechanical_engineer: ["mechanical engineer"],
  };

  const BACKGROUND_TERMS = {
    computer_science: ["computer science", "computer engineering", "cs", "cse"],
    information_technology: ["information technology"],
    mechanical: ["mechanicals", "mechanical"],
    electrical: ["electricals", "electrical"],
    electronics: ["electronics", "ece", "entc"],
    mathematics: ["mathematics", "maths", "math"],
    statistics: ["statistics"],
  };

  const COUNTRY_TERMS = {
    uk: ["uk", "u.k.", "united kingdom", "england", "britain", "scotland"],
    canada: ["canada"],
    germany: ["germany"],
    usa: ["usa", "u.s.", "the us", "united states", "america", "the states"],
    australia: ["australia"],
    ireland: ["ireland"],
    new_zealand: ["new zealand"],
  };

  const LEVEL_TERMS = {
    masters: ["master's", "masters", "master", "ms", "msc", "m.s.", "meng"],
    pg_diploma: ["pg diploma", "postgraduate diploma", "post graduate diploma", "graduate certificate", "diploma", "college program"],
  };

  // Cue words that say which kind of statement a clause is.
  const CUE = {
    career: /\b(become|be an?|work as|working as|role|job|career|position|aim|goal|end up)\b/,
    background: /\b(b\.?\s?tech|bachelor'?s?|undergrad\w*|ug|graduat\w*|major\w*|degree in|studied|did my|doing my|branch|have done|had done|completed|finished|background)\b|\bb\.e\./,
    interest: /\b(interest\w*|like|love|enjoy|passion\w*|keen|study|studying|pursue|pursuing|master'?s in|ms in|msc in|speciali[sz]\w*|focus\w*|subject|field)\b/,
    country: /\b(prefer\w*|want|interested|thinking|looking|consider\w*|option|like|leaning|keen|plan\w*|go to|study in|apply\w*|open to|only|maybe|probably|definitely|either)\b/,
  };

  // A negation reaches forward through the clause ("I do not want to be pursuing any. Course in the
  // UK") until a word that turns it around ("not the UK, rather Canada").
  const NEGATION_WORD = /\b(not(?! sure)|don'?t(?! know| mind)|do not(?! know| mind)|never|avoid\w*|except|rule out|ruling out|rather than|instead of|isn'?t|aren'?t|won'?t|wouldn'?t|scared of|worried about)\b/g;
  const NEGATION_ENDS = /\b(prefer\w*|rather|instead|maybe|happy with|fine with|okay with|ok with|open to)\b/;
  const NEGATION_REACH = 80;

  // What the student says matters most, mapped to a priority preset (a suggestion, never applied).
  const PRIORITY_CUE = /\b(most important|top|main|biggest|first|number 1|key) (priority|concern|factor|thing|criteria|criterion)\b|\bmatters (the )?most\b|\bpriority (is|would be)\b/;
  const PRIORITY_TOPICS = [
    [/\b(budget|cost|money|fees?|afford\w*|expens\w*|cheap\w*)\b/, "budget_first"],
    [/\b(career|job|placements?|salary|package)\b/, "career_first"],
    [/\b(work rights|work permit|stay back|post study work|psw|settle\w*|pr|permanent residen\w*)\b/, "work_rights_first"],
    [/\b(ranking|reputation|prestige|brand|top university|big name)\b/, "prestige_first"],
  ];

  const NUMBER_WORDS = {
    zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6,
    seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
  };
  const UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
  const TENS = { twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 };

  /** Captions often spell numbers out: "eight", "seventy eight", "one point five". */
  function wordsToDigits(s) {
    const unit = UNITS.join("|");
    const tens = Object.keys(TENS).join("|");
    return s
      .replace(new RegExp(String.raw`\b(${tens})(?: (one|two|three|four|five|six|seven|eight|nine))?\b`, "g"),
        (_, t, u) => String(TENS[t] + (u ? UNITS.indexOf(u) : 0)))
      .replace(new RegExp(String.raw`\b(${unit})\b`, "g"), (w) => String(UNITS.indexOf(w)))
      .replace(/\b(\d+) point (\d+)\b/g, "$1.$2");
  }
  const NUM = String.raw`(\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)`;

  const TERM_MONTH = { jan: "jan", january: "jan", spring: "jan", may: "may", summer: "may", sep: "sep", sept: "sep", september: "sep", fall: "sep", autumn: "sep" };

  // Common Indian conversion from a 10 point CGPA to a percentage. Shown in the evidence so the
  // counsellor can correct it for universities that use a different formula.
  const CGPA_TO_PERCENT = 9.5;

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function toNumber(s) {
    s = s.toLowerCase();
    return s in NUMBER_WORDS ? NUMBER_WORDS[s] : parseFloat(s);
  }

  function escape(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function termRegex(term) {
    // Word boundaries that also work for terms ending in a dot, like "u.s."
    return new RegExp(`(^|[^a-z0-9])${escape(term)}(?![a-z0-9])`, "g");
  }

  // Words Meet's speech recognition commonly mishears in counselling calls. Applied before the
  // rules run; the model assisted pass on the backend catches what this list misses.
  const MISHEARD = [
    [/\b(?:btec|b tec|b tech|be tech|bee tech|bitech|bi tech|beta tech)\b/g, "btech"],
    [/\b(?:eyelids?|eye lids?|eyelets?|eye lets|i lets|i elts|eye elts|i e l t s|aisle ts|ielts)\b/g, "ielts"],
    [/\b(?:see gpa|c gpa|c g p a|cgp a|see g p a)\b/g, "cgpa"],
    [/\bg p a\b/g, "gpa"],
    [/(\d)\s*(?:lacks?|lax|locks?|lakh?s)\b/g, "$1 lakhs"],
  ];

  function normalise(text) {
    let s = text
      .toLowerCase()
      .replace(/[‘’]/g, "'")
      .replace(/(\d),(\d)/g, "$1$2")
      .replace(/(\w)-(\w)/g, "$1 $2")
      .replace(/\s+/g, " ")
      .trim();
    s = wordsToDigits(s);
    for (const [pattern, replacement] of MISHEARD) s = s.replace(pattern, replacement);
    return s;
  }

  // Meet often puts a full stop mid sentence: "My GPA is. 8, 8.0." or "my btec. In mechanical".
  const DANGLING_END = /\b(is|was|are|am|my|of|the|a|an|and|in|to|about|around|be|got|scored|have|had|did|from|for|with|like|maybe|um|uh|any|some|actually)[.!]$/i;
  const CONTINUATION_START = /^(in|of|with|from|for|at|on|and then|which|that)\b/i;
  const SHORT_FRAGMENT = /^\S+(?:\s\S+)?[.!]?$/; // one or two words: "Budget." or "Degree."

  function sentences(text) {
    const parts = text
      // Split after ". " but not after one letter abbreviations such as "B.E." or "U.K."
      .split(/(?<=(?:[a-z]{2}|[0-9]|[)"'])[.!?])\s+|(?<=\?)\s+|\n+/i)
      .map((s) => s.trim())
      .filter(Boolean);
    // Rejoin fragments split by stray full stops, so "GPA is" and "8" stay in one sentence.
    const out = [];
    for (const part of parts) {
      const last = out[out.length - 1];
      // Never merge across a question mark, or a statement would be skipped as a question.
      const joinable = last && !last.endsWith("?") && !part.endsWith("?");
      if (joinable && (DANGLING_END.test(last) || CONTINUATION_START.test(part) || SHORT_FRAGMENT.test(part))) {
        out[out.length - 1] = `${last} ${part}`;
      } else {
        out.push(part);
      }
    }
    return out;
  }

  function clauses(sentence) {
    return sentence.split(/\s*(?:[;,]|\bbut\b|\bwhereas\b|\bhowever\b|\balthough\b|\bthough\b)\s*/).filter(Boolean);
  }

  function isNegated(text, index) {
    const before = text.slice(Math.max(0, index - NEGATION_REACH), index);
    const last = [...before.matchAll(NEGATION_WORD)].pop();
    return !!last && !NEGATION_ENDS.test(before.slice(last.index + last[0].length));
  }

  /** Tags from a phrase list found in the text, split into wanted and ruled out. */
  function findTags(text, terms) {
    const wanted = new Set();
    const ruledOut = new Set();
    let rest = text;
    for (const [tag, phrases] of Object.entries(terms)) {
      // Longest phrases first so "machine learning engineer" is consumed before "machine learning".
      for (const phrase of [...phrases].sort((a, b) => b.length - a.length)) {
        rest = rest.replace(termRegex(phrase), (match, lead, offset) => {
          (isNegated(text, offset) ? ruledOut : wanted).add(tag);
          return lead + " ".repeat(match.length - lead.length);
        });
      }
    }
    for (const t of ruledOut) wanted.delete(t);
    return { tags: [...wanted].sort(), ruledOut: [...ruledOut].sort(), rest };
  }

  function evidence(text) {
    return text.length > 90 ? text.slice(0, 87) + "…" : text;
  }

  // ---------------------------------------------------------------------------
  // Field extractors. Each takes one normalised sentence and returns results.
  // ---------------------------------------------------------------------------

  function gpa(s, prev) {
    // An answer to a question about grades: "What's your CGPA?" "It's 8.2."
    if (/\b(c?gpa|pointer|percentage|aggregate|grades?)\b/.test(prev) && !/\b(c?gpa|pointer)\b/.test(s)) {
      const a = s.match(/^(?:[a-z']+\s){0,4}?(\d{1,2}(?:\.\d{1,2})?)\s*(%|percent)?(?=[\s.]|$)/);
      if (a) s = /\b(c?gpa|pointer)\b/.test(prev) && !a[2] ? `cgpa ${a[1]}` : `${a[1]} percent aggregate`;
    }
    let m = s.match(/\b(?:c?gpa|sgpa|pointer)\b\D{0,20}?(\d{1,2}(?:\.\d{1,2})?)(?:\s*(?:out of|\/)\s*(10|4))?/)
      || s.match(/\b(\d{1,2}(?:\.\d{1,2})?)\s*(?:out of\s*(10|4)\s*)?(?:c?gpa|pointer|cgpa)\b/);
    if (m) {
      const v = parseFloat(m[1]);
      const scale = m[2] ? Number(m[2]) : v <= 10 ? 10 : 100;
      if (scale === 4 && v <= 4) return [{ field: "gpa_percent", value: round1((v / 4) * 100), note: `GPA ${v}/4 converted to percent` }];
      if (scale === 10 && v <= 10) return [{ field: "gpa_percent", value: round1(v * CGPA_TO_PERCENT), note: `CGPA ${v} × ${CGPA_TO_PERCENT}` }];
      if (v >= 40 && v <= 100) return [{ field: "gpa_percent", value: v }];
    }
    m = s.match(/\b(\d{2}(?:\.\d+)?)\s*(?:%|percent\b|percentage\b)/);
    if (m && /\b(aggregate|percentage|marks|scored|got|gpa|grades?|academics?|b\.?\s?tech|degree|graduat\w*|ug|college|university)\b/.test(s)
        && !/\b(ielts|budget|scholarship|loan|hike|discount|fee|fees|tax)\b/.test(s)) {
      const v = parseFloat(m[1]);
      if (v >= 40 && v <= 100) return [{ field: "gpa_percent", value: v }];
    }
    return [];
  }

  function ielts(s, prev) {
    const topic = /\b(ielts|band|bands|english test)\b/;
    if (!topic.test(s) && !topic.test(prev)) return [];
    const out = [];
    const SCORE = String.raw`(\d(?:\.5)?)`;
    const SECTION = "(listening|reading|writing|speaking)";

    // Section scores: "6 in writing", "writing 6".
    const sections = [];
    for (const m of s.matchAll(new RegExp(String.raw`\b${SCORE}\s*(?:in|on|for)\s*(?:the\s*)?${SECTION}\b`, "g"))) sections.push(parseFloat(m[1]));
    for (const m of s.matchAll(new RegExp(String.raw`\b${SECTION}\s*(?:score\s*)?(?:of|is|was)?\s*${SCORE}\b`, "g"))) sections.push(parseFloat(m[2]));
    const each = s.match(new RegExp(String.raw`\b${SCORE}\s*(?:in\s*)?each\b`)) || s.match(new RegExp(String.raw`\b(?:lowest|minimum|min)\s*(?:band|score)?\s*(?:is|was|of)?\s*${SCORE}\b`));
    if (each) sections.push(parseFloat(each[1]));
    const valid = sections.filter((v) => v >= 0 && v <= 9);
    if (valid.length) out.push({ field: "ielts_min_band", value: Math.min(...valid) });

    // Overall score, avoiding numbers that belong to a section.
    let overall =
      s.match(new RegExp(String.raw`\boverall\s*(?:score|band)?\s*(?:of|is|was)?\s*${SCORE}\b`)) ||
      s.match(new RegExp(String.raw`\b${SCORE}\s*(?:bands?\s*)?overall\b`)) ||
      s.match(new RegExp(String.raw`\bielts\b\D{0,25}?${SCORE}\b(?!\s*(?:in|on|for)\s*(?:the\s*)?${SECTION})(?!\s*each)`)) ||
      s.match(new RegExp(String.raw`\b${SCORE}\s*(?:bands?\s*)?(?:in|on)\s*(?:the\s*|my\s*)?ielts\b`));
    if (!overall && !topic.test(s)) {
      // A bare answer to "What did you get in IELTS?": "6.5"
      overall = s.match(new RegExp(String.raw`^(?:[a-z']+\s){0,4}?${SCORE}(?=[\s.]|$)(?!\s*(?:in|on|for)\s*(?:the\s*)?${SECTION})`));
    }
    if (overall) {
      const v = parseFloat(overall[1]);
      if (v >= 0 && v <= 9) out.push({ field: "ielts_overall", value: v });
    }
    return out;
  }

  function budget(s) {
    if (/\b(loan|salary|package|lpa|ctc|stipend|per annum|scholarship)\b/.test(s) && !/\bbudget\b/.test(s)) return [];
    const UNIT = String.raw`(lakhs?|lacs?|lakh|l|crores?|crs?)\b`;
    // "one to two crore, but preferably under 1.5" -> the preferred limit wins over the range.
    const preferred = s.match(new RegExp(String.raw`\bprefer\w*\s+(?:\w+\s+){0,3}?(\d+(?:\.\d+)?)\s*${UNIT}`));
    if (preferred) {
      return [{ field: "budget_inr", value: Math.round(parseFloat(preferred[1]) * (/^c/.test(preferred[2]) ? 1e7 : 1e5)), note: "The limit the student said they prefer" }];
    }
    const amounts = [];
    for (const m of s.matchAll(new RegExp(String.raw`(?:₹|rs\.?\s*|inr\s*)?\b(\d+(?:\.\d+)?)\s*(?:to|-|and|or)\s*(\d+(?:\.\d+)?)\s*${UNIT}`, "g"))) {
      const mult = /^c/.test(m[3]) ? 1e7 : 1e5;
      amounts.push(parseFloat(m[1]) * mult, parseFloat(m[2]) * mult);
    }
    for (const m of s.matchAll(new RegExp(String.raw`(?:₹|rs\.?\s*|inr\s*)?\b(\d+(?:\.\d+)?)\s*${UNIT}`, "g"))) {
      amounts.push(parseFloat(m[1]) * (/^c/.test(m[2]) ? 1e7 : 1e5));
    }
    if (!amounts.length) return [];
    const value = Math.max(...amounts);
    const ranged = new Set(amounts).size > 1;
    return [{ field: "budget_inr", value: Math.round(value), note: ranged ? "Upper end of the range mentioned" : undefined }];
  }

  function backlogs(s, prev) {
    const topic = /\bbacklogs?\b|\bkt\b|\batkt\b/;
    if (!topic.test(s)) {
      // "Any backlogs?" "No, none."
      return topic.test(prev) && /^(no|none|nope|zero|not any)\b/.test(s) ? [{ field: "backlogs", value: 0 }] : [];
    }
    if (/\b(no|zero|never had any|never had a|not a single|don'?t have any)\s+(?:active\s+)?(backlogs?|kt|atkt)\b/.test(s)) return [{ field: "backlogs", value: 0 }];
    const m = s.match(new RegExp(String.raw`\b${NUM}\s+(?:active\s+|cleared\s+)?(?:backlogs?|kts?|atkts?)\b`)) || s.match(/\b(a|an)\s+backlog\b/);
    if (!m) return [];
    const v = m[1] === "a" || m[1] === "an" ? 1 : toNumber(m[1]);
    return Number.isInteger(v) ? [{ field: "backlogs", value: v }] : [];
  }

  function workExperience(s) {
    if (/\b(fresher|no (?:full time )?(?:work )?experience|haven'?t worked|have not worked|not worked anywhere)\b/.test(s)) {
      return [{ field: "work_exp_months", value: 0 }];
    }
    if (!/\b(work|worked|working|experience|job|employed)\b/.test(s) || /\bintern/.test(s)) return [];
    const m = s.match(new RegExp(String.raw`\b${NUM}(\s+and a half)?\s*(years?|yrs?|months?)\b`));
    if (!m) return [];
    // "a 4 year degree" is about the degree, not work.
    if (/\b(degree|course|program|b\.?\s?tech|bachelor)/.test(s.slice(m.index, m.index + m[0].length + 20))) return [];
    let n = toNumber(m[1]) + (m[2] ? 0.5 : 0);
    if (/^y/.test(m[3])) n *= 12;
    return Number.isFinite(n) ? [{ field: "work_exp_months", value: Math.round(n) }] : [];
  }

  function degreeYears(s, original) {
    const m = s.match(/\b(three|four|3|4)\s*years?\s*(?:long\s*)?(?:degree|bachelor'?s?|b\.?\s?tech|b\.?\s?sc|bca|program|programme|course|ug|undergrad\w*)/);
    if (m) return [{ field: "degree_years", value: toNumber(m[1]) }];
    if (/\b(b\.?\s?tech|btech)\b/.test(s) || /\bB\.?E\.?(?![a-z])/.test(original)) {
      return [{ field: "degree_years", value: 4, note: "Inferred: B.Tech and BE are 4 year degrees" }];
    }
    if (/\b(b\.?\s?sc|bsc|bca|b\.?\s?com|bcom|bba)\b/.test(s)) {
      return [{ field: "degree_years", value: 3, note: "Inferred: BSc, BCA, BCom and BBA are usually 3 years" }];
    }
    return [];
  }

  function intake(s) {
    const m = s.match(/\b(january|jan|september|sept|sep|may|fall|autumn|spring|summer)\b\s*(intake|session|batch|term|semester)?\s*(?:of\s*)?(20\d\d)?/);
    if (!m) return [];
    const [, word, intakeWord, year] = m;
    const context = /\b(intake|start|join|apply|begin|aim\w*|target\w*|planning)\b/.test(s);
    // "may" is usually a verb, so it needs an explicit intake word or a year.
    if (word === "may" ? !(intakeWord || year) : !(intakeWord || year || context)) return [];
    return [{ field: "target_intake", value: { term: TERM_MONTH[word], year: year ? Number(year) : null } }];
  }

  function priority(s) {
    if (!PRIORITY_CUE.test(s)) return [];
    const hit = PRIORITY_TOPICS.find(([pattern]) => pattern.test(s));
    return hit ? [{ field: "priority_preset", value: hit[1] }] : [];
  }

  function tagsFromClauses(s) {
    const out = { interest_tags: new Set(), career_tags: new Set(), background_tags: new Set(), target_countries: new Set(), excluded_countries: new Set(), target_levels: new Set() };
    for (const clause of clauses(s)) {
      const career = findTags(clause, CAREER_TERMS);
      career.tags.forEach((t) => out.career_tags.add(t));
      const rest = career.rest; // job titles removed, so "ml engineer" does not also count as "ml"

      if (CUE.background.test(rest)) {
        findTags(rest, BACKGROUND_TERMS).tags.forEach((t) => out.background_tags.add(t));
        if (/\b(b\.?\s?tech|btech|engineering)\b|\bb\.e\./.test(rest)) out.background_tags.add("engineering");
      } else if (CUE.interest.test(rest)) {
        findTags(rest, INTEREST_TERMS).tags.forEach((t) => out.interest_tags.add(t));
      }
      // A wanted country needs a preference word ("my uncle lives in Canada" is not a wish), but a
      // negated one counts on its own: "but not in the UK" is clear even without "want".
      const countries = findTags(rest, COUNTRY_TERMS);
      if (CUE.country.test(rest)) countries.tags.forEach((t) => out.target_countries.add(t));
      countries.ruledOut.forEach((t) => out.excluded_countries.add(t));
      findTags(rest, LEVEL_TERMS).tags.forEach((t) => out.target_levels.add(t));
    }
    return Object.entries(out)
      .filter(([, tags]) => tags.size)
      .map(([field, tags]) => ({ field, value: [...tags].sort() }));
  }

  function round1(n) {
    return Math.round(n * 10) / 10;
  }

  // ---------------------------------------------------------------------------
  // Entry point
  // ---------------------------------------------------------------------------

  /**
   * Returns [{field, value, evidence, note?}] for every value recognised in the text.
   * Questions are skipped: they are usually the counsellor asking, not the student answering.
   * Later mentions of the same field win, matching how people correct themselves.
   */
  function extract(text) {
    const found = new Map();
    let question = "";
    let sinceQuestion = 0;
    for (const original of sentences(String(text || ""))) {
      const s = normalise(original);
      if (original.endsWith("?")) {
        question = s; // the question is context for the answer that follows
        sinceQuestion = 0;
        continue;
      }
      // Two sentences of reach, so a speaker name line in captions does not break the link.
      const prev = ++sinceQuestion <= 2 ? question : "";
      const results = [
        ...gpa(s, prev), ...ielts(s, prev), ...budget(s), ...backlogs(s, prev), ...workExperience(s),
        ...degreeYears(s, original), ...intake(s), ...tagsFromClauses(s), ...priority(s),
      ];
      for (const r of results) {
        const earlier = found.get(r.field);
        const item = { field: r.field, value: r.value, evidence: evidence(original) };
        if (r.note) item.note = r.note;
        // Lists accumulate across sentences; single values take the latest mention.
        if (Array.isArray(r.value) && earlier) {
          item.value = [...new Set([...earlier.value, ...r.value])].sort();
          item.evidence = evidence(`${earlier.evidence} … ${original}`);
        }
        found.set(r.field, item);
      }
    }
    return [...found.values()];
  }

  const api = { extract, sentences, normalise, CGPA_TO_PERCENT };
  root.GGExtract = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

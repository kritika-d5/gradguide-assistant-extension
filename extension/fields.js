// Profile fields the panel can capture, and how to show values to the counsellor.
// This is UI metadata only: course rules and tag vocabularies come from the backend.

export const FIELDS = [
  { key: "interest_tags", label: "Interests", group: "Goals", kind: "tags", vocab: "interest_tags" },
  { key: "career_tags", label: "Career goal", group: "Goals", kind: "tags", vocab: "career_tags" },
  { key: "target_countries", label: "Countries", group: "Goals", kind: "tags", vocab: "countries", countries: true },
  { key: "excluded_countries", label: "Ruled out", group: "Goals", kind: "tags", vocab: "countries", countries: true },
  { key: "target_levels", label: "Degree level", group: "Goals", kind: "tags", vocab: "levels" },
  { key: "target_intake", label: "Intake", group: "Goals", kind: "intake" },
  { key: "budget_inr", label: "Budget", group: "Money", kind: "lakhs" },
  { key: "gpa_percent", label: "GPA", group: "Academics", kind: "number", min: 0, max: 100, step: 0.1, unit: "%" },
  { key: "degree_years", label: "Degree length", group: "Academics", kind: "choice", options: [3, 4], unit: " yr" },
  { key: "backlogs", label: "Backlogs", group: "Academics", kind: "number", min: 0, max: 50, step: 1 },
  { key: "ielts_overall", label: "IELTS", group: "Academics", kind: "number", min: 0, max: 9, step: 0.5 },
  { key: "ielts_min_band", label: "IELTS lowest band", group: "Academics", kind: "number", min: 0, max: 9, step: 0.5 },
  { key: "background_tags", label: "UG background", group: "Academics", kind: "tags", vocab: "background_tags" },
  { key: "work_exp_months", label: "Work experience", group: "Academics", kind: "number", min: 0, max: 600, step: 1, unit: " mo" },
];

export const FIELD_BY_KEY = Object.fromEntries(FIELDS.map((f) => [f.key, f]));
export const GROUPS = ["Goals", "Money", "Academics"];

export const SUBSCORE_LABELS = {
  field_fit: "Field",
  career_fit: "Career",
  budget_fit: "Budget",
  academic_fit: "Academic",
  preference_fit: "Country",
  outcome_fit: "Work rights",
  reputation_fit: "Reputation",
};

// Countries a student may name beyond the catalogue (same list as the backend's EXTRA_COUNTRIES).
export const EXTRA_COUNTRIES = ["usa", "australia", "ireland", "new_zealand"];

export const GATE_LABELS = {
  country: "Ruled out country",
  eligibility: "Not eligible",
  level: "Degree level",
  budget: "Over budget",
  intake: "Intake",
};

const PRETTY = { uk: "UK", usa: "USA", ml: "ML", pg_diploma: "PG diploma" };

export function pretty(tag) {
  if (PRETTY[tag]) return PRETTY[tag];
  return String(tag).replace(/_/g, " ");
}

export function country(tag) {
  if (PRETTY[tag]) return PRETTY[tag];
  return pretty(tag).replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Indian style short amounts, matching the backend: ₹27.4L, ₹1.20Cr. */
export function inr(amount) {
  if (amount >= 1e7) return `₹${(amount / 1e7).toFixed(2)}Cr`;
  return `₹${(amount / 1e5).toFixed(1)}L`;
}

export function intakeLabel(intake) {
  return `${intake.term.charAt(0).toUpperCase()}${intake.term.slice(1)} ${intake.year}`;
}

export function formatValue(field, value) {
  if (value == null) return "";
  switch (field.kind) {
    case "tags":
      return value.map(field.countries ? country : pretty).join(", ");
    case "lakhs":
      return inr(value);
    case "intake":
      return intakeLabel(value);
    default:
      return `${value}${field.unit || ""}`;
  }
}

/** Turn editor input into a profile value. Returns {value} or {error}. */
export function parseValue(field, raw) {
  if (field.kind === "tags") {
    const tags = (Array.isArray(raw) ? raw : String(raw).split(","))
      .map((t) => t.trim().toLowerCase().replace(/\s+/g, "_"))
      .filter(Boolean);
    return tags.length ? { value: [...new Set(tags)] } : { error: "Pick at least one" };
  }
  if (field.kind === "intake") {
    return raw ? { value: raw } : { error: "Pick an intake" };
  }
  const n = Number(raw);
  if (raw === "" || raw == null || Number.isNaN(n)) return { error: "Enter a number" };
  if (field.kind === "lakhs") {
    return n > 0 ? { value: Math.round(n * 1e5) } : { error: "Budget must be above zero" };
  }
  if (field.min != null && n < field.min) return { error: `Minimum is ${field.min}` };
  if (field.max != null && n > field.max) return { error: `Maximum is ${field.max}` };
  if (field.step === 1 && !Number.isInteger(n)) return { error: "Whole numbers only" };
  return { value: n };
}

export function ruleText(rule) {
  const v = rule.value;
  const text = {
    min_gpa_percent: `GPA at least ${v}%`,
    min_ielts_overall: `IELTS ${v} overall`,
    min_ielts_band: `IELTS ${v} in every band`,
    accepts_3yr_degree: v ? "Accepts 3 year degrees" : "Needs a 4 year degree",
    max_backlogs: v === 0 ? "No backlogs" : `At most ${v} backlogs`,
    required_background: `Background in ${Array.isArray(v) ? v.map(pretty).join(", ") : v}`,
    min_work_exp_months: `${v} months work experience`,
  }[rule.type] || `${pretty(rule.type)}: ${v}`;
  return rule.severity === "soft" ? `${text} (preferred)` : text;
}

// GradGuide side panel. Everything here is for the counsellor's eyes only.
//
// The panel holds the profile (confirmed values plus pending proposals), sends confirmed values to
// the backend, and renders what comes back. It never ranks or reorders courses itself, so every
// counsellor sees the same deterministic result for the same inputs.

import { DEFAULT_API_BASE, makeApi } from "./api.js";
import {
  EXTRA_COUNTRIES, FIELDS, FIELD_BY_KEY, GROUPS, GATE_LABELS, SUBSCORE_LABELS,
  country, formatValue, inr, intakeLabel, parseValue, pretty, ruleText,
} from "./fields.js";

const SHORTLIST_SIZE = 5;
const LOW_CONFIDENCE = 0.6;

const state = {
  // Session data (cleared when the browser closes, or with "New student").
  confirmed: {}, // field -> {value, source}
  pending: {}, // field -> {value, source, evidence, note}: heard but not yet accepted
  dismissed: {}, // field -> values the counsellor dismissed, so captions do not keep proposing them
  stretch: 0, // budget_stretch_pct, a counsellor setting
  preset: "balanced",

  // Loaded from the backend.
  presets: [],
  vocab: null,
  result: null,
  prevOrder: [],
  error: null,
  busy: false,

  // View state.
  editing: null, // {key, fromPending}
  showAll: false,
  expanded: new Set(),
  tab: "shortlist",
  search: { q: "", country: "", results: null, expanded: new Set(), error: null },
  captions: { listen: false, state: "off" },
  ai: { available: false, provider: null, model: null, on: false, busy: false, error: "", lastRunAt: 0 },
  notesMessage: "",
  suggestion: null, // {preset, evidence}: a preset the student's words point to

  // Consistency lens: what the counsellor recommends, and the comparison with past students.
  picks: [], // course ids marked with the star
  savedId: null, // history row for this student once saved
  savedSignature: "", // what was saved, to show "Saved" until something changes
  lens: null,
};

let api = makeApi(DEFAULT_API_BASE);
let apiBase = DEFAULT_API_BASE;
let counsellorName = ""; // recorded with saved sessions so the lens can tell counsellors apart

// ---------------------------------------------------------------------------
// Storage: student data in session storage only; the API address in local settings.
// ---------------------------------------------------------------------------

const ext = globalThis.chrome?.storage ? globalThis.chrome : null;

async function loadSession() {
  try {
    if (ext) return (await ext.storage.session.get("gg")).gg || null;
    return JSON.parse(sessionStorage.getItem("gg"));
  } catch {
    return null;
  }
}

function saveSession() {
  const data = { confirmed: state.confirmed, pending: state.pending, dismissed: state.dismissed, stretch: state.stretch, preset: state.preset, suggestion: state.suggestion, aiOn: state.ai.on,
    picks: state.picks, savedId: state.savedId, savedSignature: state.savedSignature };
  try {
    if (ext) ext.storage.session.set({ gg: data });
    else sessionStorage.setItem("gg", JSON.stringify(data));
  } catch {
    // storage is a convenience; the panel works without it
  }
}

async function loadApiBase() {
  try {
    if (ext) return (await ext.storage.local.get("apiBase")).apiBase || DEFAULT_API_BASE;
    return localStorage.getItem("apiBase") || DEFAULT_API_BASE;
  } catch {
    return DEFAULT_API_BASE;
  }
}

async function loadCounsellor() {
  try {
    if (ext) return (await ext.storage.local.get("counsellor")).counsellor || "";
    return localStorage.getItem("counsellor") || "";
  } catch {
    return "";
  }
}

function saveCounsellor(value) {
  try {
    if (ext) ext.storage.local.set({ counsellor: value });
    else localStorage.setItem("counsellor", value);
  } catch {
    // ignore
  }
}

function saveApiBase(value) {
  try {
    if (ext) ext.storage.local.set({ apiBase: value });
    else localStorage.setItem("apiBase", value);
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function today() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function longDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

const bandClass = (band) => ({ "Strong match": "strong", "Good match": "good" })[band] || "stretch";

function buildProfile() {
  const profile = { budget_stretch_pct: state.stretch };
  for (const f of FIELDS) {
    const c = state.confirmed[f.key];
    const p = state.pending[f.key];
    if (c) profile[f.key] = { value: c.value, status: "confirmed", source: c.source };
    else if (p) profile[f.key] = { value: p.value, status: "pending", source: p.source };
  }
  return profile;
}

// ---------------------------------------------------------------------------
// Profile actions
// ---------------------------------------------------------------------------

/** An intake heard without a year ("September intake") means the next one the catalogue offers. */
function resolveIntake(value) {
  if (!value?.term) return null;
  if (value.year) return { term: value.term, year: value.year };
  const next = (state.vocab?.intakes || []).find((i) => i.term === value.term);
  return next ? { term: next.term, year: next.year } : null;
}

/** The student said what matters most: suggest the matching preset, never switch by itself. */
function suggestPreset(preset, evidence) {
  const dismissed = state.dismissed.priority_preset || [];
  if (preset === state.preset || dismissed.includes(preset) || state.suggestion?.preset === preset) return false;
  if (state.presets.length && !state.presets.some((p) => p.key === preset)) return false;
  state.suggestion = { preset, evidence };
  saveSession();
  renderHeader();
  return true;
}

function answerSuggestion(accept) {
  const s = state.suggestion;
  if (!s) return;
  if (accept) {
    state.preset = s.preset;
    refresh();
  } else {
    state.dismissed.priority_preset = [...(state.dismissed.priority_preset || []), s.preset];
  }
  state.suggestion = null;
  saveSession();
  renderHeader();
}

/**
 * Called by the caption listener and the notes box (and from the console for demos).
 * Only ever creates a pending chip; nothing counts until the counsellor accepts it.
 */
function propose(field, value, { source = "caption", evidence = "", note = "" } = {}) {
  if (field === "priority_preset") return suggestPreset(value, evidence);
  const f = FIELD_BY_KEY[field];
  if (!f || value == null) return false;
  const dismissed = state.dismissed[field] || [];
  const confirmed = state.confirmed[field]?.value;

  if (f.kind === "intake") {
    value = resolveIntake(value);
    if (!value) return false;
  }
  if (f.kind === "tags") {
    // Lists grow: propose what is already known plus anything new that was not dismissed.
    const fresh = value.filter((t) => !dismissed.includes(t) && !(confirmed || []).includes(t));
    if (!fresh.length) return false;
    const base = state.pending[field]?.value || [];
    value = [...new Set([...(confirmed || []), ...base, ...fresh])].sort();
  } else if (dismissed.includes(JSON.stringify(value))) {
    return false;
  }
  if (same(confirmed, value) || same(state.pending[field]?.value, value)) return false;

  state.pending[field] = { value, source, evidence, note };
  saveSession();
  renderProfile();
  return true;
}

function confirmValue(key, value, source) {
  state.confirmed[key] = { value, source };
  delete state.pending[key];
  state.editing = null;
  saveSession();
  renderProfile();
  refresh();
}

function clearValue(key) {
  delete state.confirmed[key];
  state.editing = null;
  saveSession();
  renderProfile();
  refresh();
}

function dismissPending(key) {
  const p = state.pending[key];
  if (p) {
    const confirmed = state.confirmed[key]?.value || [];
    const values = Array.isArray(p.value) ? p.value.filter((t) => !confirmed.includes(t)) : [JSON.stringify(p.value)];
    state.dismissed[key] = [...new Set([...(state.dismissed[key] || []), ...values])];
  }
  delete state.pending[key];
  if (state.editing?.key === key) state.editing = null;
  saveSession();
  renderProfile();
}

function openEditor(key, fromPending = false) {
  state.editing = { key, fromPending };
  renderProfile();
  const first = document.querySelector(".editor input, .editor select, .editor .tag-opt");
  first?.focus();
}

async function newStudent() {
  if (!confirm("Start a new student? This clears the profile in this panel.")) return;
  if (state.picks.length && sessionSignature() !== state.savedSignature &&
      confirm("Save this session to the shared history first? It helps the consistency check for future students.")) {
    await saveToHistory();
  }
  Object.assign(state, {
    confirmed: {}, pending: {}, dismissed: {}, suggestion: null, stretch: 0, editing: null, prevOrder: [], result: null,
    showAll: false, notesMessage: "", picks: [], savedId: null, savedSignature: "", lens: null,
  });
  state.ai.on = false; // consent is per student
  transcript.length = 0;
  state.expanded.clear();
  saveSession();
  renderAll();
  refresh();
}

// ---------------------------------------------------------------------------
// Backend calls
// ---------------------------------------------------------------------------

let seq = 0;
let timer = null;

function refresh() {
  clearTimeout(timer);
  timer = setTimeout(runRecommend, 150);
}

async function runRecommend() {
  const id = ++seq;
  state.busy = true;
  renderHeader();
  try {
    const result = await api.recommend({ profile: buildProfile(), preset: state.preset, as_of: today() });
    if (id !== seq) return;
    state.prevOrder = state.result ? state.result.ranked.map((r) => r.course_id) : [];
    state.result = result;
    state.error = null;
  } catch (err) {
    if (id !== seq) return;
    state.error = err.message;
  }
  state.busy = false;
  renderHeader();
  renderBanner();
  renderAsk();
  renderShortlist();
  if (state.tab === "search") renderSearch();
  if (!state.error) refreshLens();
}

// ---------------------------------------------------------------------------
// Consistency lens: compare the shortlist with what similar past students were recommended.
// Informational only; it never reorders or blocks anything.
// ---------------------------------------------------------------------------

let lensTimer = null;
let lensSeq = 0;

function refreshLens() {
  clearTimeout(lensTimer);
  lensTimer = setTimeout(async () => {
    const id = ++lensSeq;
    try {
      const lens = await api.consistency({
        profile: buildProfile(), preset: state.preset, as_of: today(), recommended: state.picks, session_id: state.savedId,
      });
      if (id !== lensSeq) return;
      state.lens = lens;
    } catch {
      if (id !== lensSeq) return;
      state.lens = null; // the lens is a nice to have; the shortlist works without it
    }
    renderShortlist();
  }, 250);
}

function sessionSignature() {
  return JSON.stringify({ c: state.confirmed, p: [...state.picks].sort(), s: state.preset });
}

function togglePick(courseId) {
  const i = state.picks.indexOf(courseId);
  if (i === -1) state.picks.push(courseId);
  else state.picks.splice(i, 1);
  saveSession();
  renderShortlist();
  if (state.tab === "search") renderSearchResults();
  refreshLens();
}

function pickButton(courseId, name) {
  const on = state.picks.includes(courseId);
  return h(
    "button",
    {
      class: `pick${on ? " on" : ""}`,
      "aria-pressed": String(on),
      "aria-label": `${on ? "Remove" : "Mark"} ${name} ${on ? "from" : "as"} recommended`,
      title: on ? "Recommended to this student. Click to remove." : "Mark as recommended to this student",
      onclick: () => togglePick(courseId),
    },
    on ? "★" : "☆",
  );
}

async function saveToHistory() {
  try {
    const res = await api.saveSession({
      profile: buildProfile(), preset: state.preset, as_of: today(), recommended: state.picks,
      counsellor: counsellorName, session_id: state.savedId,
    });
    state.savedId = res.id;
    state.savedSignature = sessionSignature();
    saveSession();
  } catch (err) {
    alert(`Could not save the session: ${err.message}`);
  }
  renderShortlist();
  refreshLens();
}

function saveRow() {
  const n = state.picks.length;
  const saved = n && sessionSignature() === state.savedSignature;
  return h(
    "div",
    { class: "save-row" },
    h("span", { class: "muted" }, n ? `${n} recommended (★)` : "Mark the courses you recommend with ☆"),
    h(
      "button",
      {
        class: `btn small${saved ? "" : " primary"}`,
        disabled: !n || saved,
        title: "Save the confirmed profile and your recommended courses to the shared history. No names, no transcript.",
        onclick: saveToHistory,
      },
      saved ? "Saved ✓" : state.savedId ? "Update saved session" : "Save session",
    ),
  );
}

function lensBox() {
  const L = state.lens;
  if (!L) return null;
  const demo = L.demo ? ` (${L.demo === L.similar ? "all" : L.demo} illustrative)` : "";
  if (L.similar < 3) {
    return h("div", { class: "lens quiet" }, `Consistency: ${L.similar} similar past session${L.similar === 1 ? "" : "s"} in history, not enough to compare yet.`);
  }
  const who = `${L.similar} similar past students, ${L.counsellors} counsellor${L.counsellors === 1 ? "" : "s"}${demo}`;
  const common = h(
    "details",
    {},
    h("summary", {}, "What similar students were recommended"),
    h(
      "ul",
      {},
      L.common.map((c) =>
        h("li", {}, h("b", {}, c.course_name), ` ${c.count} of ${c.of}`, h("span", { class: "muted" }, c.here === "picked" ? " · your pick" : c.note ? ` · ${c.note}` : " · not listed here")),
      ),
    ),
  );
  const hint = L.compared === "top_ranked" ? h("div", { class: "hint" }, "Comparing the top five. Mark your recommendations with ☆ to compare your own picks.") : null;
  if (!L.divergences.length) {
    return h("div", { class: "lens ok" }, h("div", {}, `✓ Consistent with ${who}`), hint, common);
  }
  return h(
    "div",
    { class: "lens" },
    h("div", { class: "lens-title" }, "Consistency check"),
    h("div", { class: "muted" }, `Compared with ${who}`),
    h("ul", { class: "lens-list" }, L.divergences.slice(0, 3).map((d) => h("li", { class: d.kind }, h("b", {}, d.course_name), h("div", {}, d.text)))),
    L.divergences.length > 3 ? h("div", { class: "muted" }, `+${L.divergences.length - 3} more`) : null,
    hint,
    common,
  );
}

async function loadReference() {
  try {
    const [presets, vocab] = await Promise.all([api.presets(), api.vocabulary(), loadAiStatus()]);
    state.presets = presets;
    state.vocab = vocab;
    state.error = null;
  } catch (err) {
    state.error = err.message;
  }
}

// ---------------------------------------------------------------------------
// Rendering: header, banner, next question
// ---------------------------------------------------------------------------

function renderHeader() {
  const r = state.result;
  const dot = state.busy ? "busy" : state.error ? "err" : r ? "ok" : "";
  const options = state.presets.length
    ? state.presets
    : [{ key: state.preset, label: r?.preset_label || pretty(state.preset) }];

  const select = h(
    "select",
    {
      "aria-label": "Priority preset",
      onchange: (e) => {
        state.preset = e.target.value;
        if (state.suggestion?.preset === state.preset) state.suggestion = null;
        saveSession();
        renderHeader();
        refresh();
      },
    },
    options.map((p) => h("option", { value: p.key, selected: p.key === state.preset }, p.label)),
  );

  const conf = r ? r.confidence : 0;
  const confidence = h(
    "div",
    {
      class: "confidence",
      title: "Share of the preset's weight backed by confirmed profile data. Rankings sharpen as gaps close.",
    },
    h("span", {}, r ? `Confidence ${Math.round(conf * 100)}%` : "Confidence"),
    h("span", { class: "meter" }, h("span", { style: `width:${Math.round(conf * 100)}%` })),
  );

  $("#header").replaceChildren(
    h(
      "div",
      { class: "title-row" },
      h("span", { class: "title" }, h("span", { class: "wordmark" }, "GradGuide"), h("span", { class: "product" }, "Assist")),
      h("span", { class: `dot ${dot}`, title: state.error ? "Service unreachable" : state.busy ? "Updating" : "Connected" }),
      h("button", { class: "btn small", onclick: newStudent }, "New student"),
    ),
    h("div", { class: "controls" }, select, confidence),
    ...[presetSuggestion()].filter(Boolean),
  );
}

function presetSuggestion() {
  const s = state.suggestion;
  if (!s) return null;
  const label = state.presets.find((p) => p.key === s.preset)?.label || pretty(s.preset);
  return h(
    "div",
    { class: "suggest", title: s.evidence ? `Heard: “${s.evidence}”` : null },
    h("span", {}, s.evidence ? `Heard “${s.evidence.length > 48 ? s.evidence.slice(0, 46) + "…" : s.evidence}”` : "The student named a priority"),
    h(
      "span",
      { class: "suggest-actions" },
      h("button", { class: "btn small primary", onclick: () => answerSuggestion(true) }, `Switch to ${label}`),
      h("button", { class: "btn link", "aria-label": "Dismiss suggestion", onclick: () => answerSuggestion(false) }, "Not now"),
    ),
  );
}

function renderBanner() {
  const el = $("#banner");
  if (!state.error) return el.replaceChildren();
  el.replaceChildren(
    h("div", {}, h("b", {}, "Cannot reach the assist service"), ` at ${api.root} (${state.error}).`),
    h("div", {}, "Start it with ", h("code", {}, "uvicorn app.api:app --reload"), ". Profile editing still works."),
  );
}

function renderAsk() {
  const gaps = state.result?.gaps || [];
  if (!gaps.length) return $("#ask").replaceChildren();
  const [top, ...rest] = gaps;
  const label = (g) => FIELD_BY_KEY[g.field]?.label || pretty(g.field);
  $("#ask").replaceChildren(
    h(
      "div",
      { class: "ask" },
      h("div", { class: "ask-label" }, `Ask next: ${label(top)}`),
      h("div", { class: "ask-q" }, top.question),
      h(
        "div",
        { class: "ask-row" },
        h("button", { class: "btn small", onclick: () => openEditor(top.field) }, "Record answer"),
        top.courses_waiting
          ? h("span", { class: "muted" }, `Eligibility of ${top.courses_waiting} courses waits on this`)
          : h("span", { class: "muted" }, "Carries the most weight in this preset"),
      ),
      rest.length
        ? h(
            "details",
            {},
            h("summary", { class: "muted" }, `${rest.length} more unknown`),
            h(
              "ul",
              {},
              rest.map((g) =>
                h("li", {}, h("button", { class: "btn link", onclick: () => openEditor(g.field) }, label(g)), `: ${g.question}`),
              ),
            ),
          )
        : null,
    ),
  );
}

// ---------------------------------------------------------------------------
// Rendering: profile chips and editor
// ---------------------------------------------------------------------------

function chipsFor(f) {
  const out = [];
  const c = state.confirmed[f.key];
  const p = state.pending[f.key];
  if (c) {
    out.push(
      h(
        "button",
        { class: "chip confirmed", title: `Confirmed (${c.source}). Click to edit.`, onclick: () => openEditor(f.key) },
        h("span", { class: "k" }, f.label), formatValue(f, c.value),
      ),
    );
  }
  if (p) {
    const heard = formatValue(f, p.value);
    out.push(
      h(
        "span",
        { class: "chip pending", title: pendingTitle(f, p, c) },
        h("span", { class: "k" }, c ? `${f.label} heard` : f.label), `${heard}?`,
        h("button", { class: "act", "aria-label": `Accept ${f.label} ${heard}`, title: "Accept", onclick: () => confirmValue(f.key, p.value, p.source) }, "✓"),
        h("button", { class: "act", "aria-label": `Edit ${f.label}`, title: "Edit", onclick: () => openEditor(f.key, true) }, "✎"),
        h("button", { class: "act", "aria-label": `Dismiss ${f.label} ${heard}`, title: "Dismiss", onclick: () => dismissPending(f.key) }, "✕"),
      ),
    );
  }
  if (!c && !p) {
    out.push(h("button", { class: "chip unknown", onclick: () => openEditor(f.key) }, `+ ${f.label}`));
  }
  return out;
}

function pendingTitle(f, p, c) {
  const lines = [c ? `Proposed ${formatValue(f, p.value)}; confirmed value is ${formatValue(f, c.value)}` : "Not confirmed yet"];
  if (p.evidence) lines.push(`Heard: “${p.evidence}”`);
  if (p.note) lines.push(p.note);
  return lines.join("\n");
}

function renderProfile() {
  const groups = GROUPS.map((g) => {
    const fields = FIELDS.filter((f) => f.group === g);
    const stretchChip =
      g === "Money" && state.stretch > 0
        ? h("button", { class: "chip setting", title: "Counsellor setting: how far over budget a course may go and still rank", onclick: () => openEditor("budget_inr") }, h("span", { class: "k" }, "Stretch"), `${state.stretch}%`)
        : null;
    const editing = state.editing && fields.some((f) => f.key === state.editing.key);
    return h(
      "div",
      { class: "group" },
      h("div", { class: "group-label" }, g),
      h("div", { class: "chips" }, fields.map(chipsFor), stretchChip),
      editing ? renderEditor(FIELD_BY_KEY[state.editing.key], state.editing.fromPending) : null,
    );
  });
  $("#profile").replaceChildren(...groups);
  renderListen();
}

// ---------------------------------------------------------------------------
// Rendering: caption listening toggle and the notes box
// ---------------------------------------------------------------------------

function setListening(on) {
  state.captions.listen = on;
  ext?.storage.local.set({ ggListen: on });
  renderListen();
}

/** Caption listening and AI reading sit together at the top of Profile. */
function renderListen() {
  $("#listen").replaceChildren(...[captionControl(), aiControl()].filter(Boolean));
}

function captionControl() {
  if (!ext) return null; // captions need the extension; the notes box still works
  const { listen, state: s } = state.captions;
  if (!listen) {
    return h("button", { class: "btn small", title: "Read Meet's live captions in this browser and propose values as pending chips", onclick: () => setListening(true) }, "Listen to captions");
  }
  const label = s === "listening" ? "Listening" : s === "waiting" ? "Turn on captions in Meet (CC)" : "Open the Meet tab";
  return h(
    "span",
    { class: `listen ${s}` },
    h("span", { class: "live-dot", "aria-hidden": "true" }),
    label,
    h("button", { class: "btn link", onclick: () => setListening(false) }, "Stop"),
  );
}

// ---------------------------------------------------------------------------
// AI reading: a model on the backend reads the recent transcript, because Meet's captions mishear
// words ("eyelid" for IELTS) that rules cannot anticipate. Opt in per session, since the caption
// text leaves the browser. Its values are pending chips like any other, and the backend rejects
// any value whose quote is not in the transcript.
// ---------------------------------------------------------------------------

const AI_INTERVAL_MS = 20000; // at most one model call per 20 seconds while captions flow
const AI_SETTLE_MS = 4000; // let Meet finish revising the latest line first
const TRANSCRIPT_CHARS = 6000; // rolling window sent to the model

const transcript = []; // caption lines, in memory only
let aiTimer = null;
let aiDirty = false;

function addTranscriptLines(lines) {
  for (const raw of lines) {
    const line = raw.trim();
    const last = transcript[transcript.length - 1];
    // Meet revises the line being spoken; keep only the latest version of it.
    if (last && line.startsWith(last)) transcript[transcript.length - 1] = line;
    else if (line && line !== last) transcript.push(line);
  }
  while (transcript.join("\n").length > TRANSCRIPT_CHARS * 2) transcript.shift();
  aiDirty = true;
  scheduleAi();
}

function recentTranscript() {
  const text = transcript.join("\n");
  return text.length > TRANSCRIPT_CHARS ? text.slice(-TRANSCRIPT_CHARS) : text;
}

function scheduleAi() {
  if (!state.ai.on || !state.ai.available || aiTimer) return;
  const wait = Math.max(AI_SETTLE_MS, state.ai.lastRunAt + AI_INTERVAL_MS - Date.now());
  aiTimer = setTimeout(async () => {
    aiTimer = null;
    if (!aiDirty) return;
    aiDirty = false;
    await runAi(recentTranscript(), "caption");
    if (aiDirty) scheduleAi(); // more captions arrived during the call
  }, wait);
}

/** Send text to the backend model and turn its checked values into pending chips. */
async function runAi(text, source) {
  const out = { found: 0, added: 0 };
  if (!text.trim()) return out;
  state.ai.busy = true;
  state.ai.lastRunAt = Date.now();
  renderListen();
  try {
    const res = await api.extract(text);
    out.found = res.items.length;
    for (const r of res.items) {
      const note = [r.note, `Read by AI (${res.model})`].filter(Boolean).join(". ");
      if (propose(r.field, r.value, { source, evidence: r.evidence, note })) out.added++;
    }
    state.ai.error = "";
  } catch (err) {
    state.ai.error = err.message;
  }
  state.ai.busy = false;
  renderListen();
  return out;
}

function setAi(on) {
  if (on && !confirm(
    `AI reading sends the recent caption text and your typed notes to ${state.ai.provider} (${state.ai.model}) ` +
    "to find profile values. Turn it on only if the student has agreed. Continue?",
  )) return;
  state.ai.on = on;
  saveSession();
  renderNotes();
  renderListen();
  if (on) {
    aiDirty = transcript.length > 0;
    scheduleAi();
  }
}

async function loadAiStatus() {
  try {
    const s = await api.extractStatus();
    Object.assign(state.ai, { available: s.enabled, provider: s.provider, model: s.model });
  } catch {
    state.ai.available = false;
  }
}

async function findInNotes(text) {
  const found = globalThis.GGExtract?.extract(text) || [];
  let recognised = found.length;
  let added = 0;
  for (const r of found) {
    if (propose(r.field, r.value, { source: "manual", evidence: r.evidence, note: r.note })) added++;
  }
  if (state.ai.on && state.ai.available) {
    state.notesMessage = "AI is reading…";
    renderNotes();
    const ai = await runAi(text, "manual");
    recognised += ai.found;
    added += ai.added;
  }
  state.notesMessage = !recognised
    ? "Nothing recognised. Add values with the chips above."
    : added
      ? `${added} value${added > 1 ? "s" : ""} to check: see the amber chips.`
      : "Nothing new: those values are already in the profile or were dismissed.";
  renderNotes();
}

/** A switch: "AI off" / "AI on". Hover shows the provider, model and any error. */
function aiControl() {
  const ai = state.ai;
  if (!ai.available) {
    return h(
      "button",
      { class: "btn small ai-toggle", disabled: true, title: "AI reading needs GROQ_API_KEY or ANTHROPIC_API_KEY in backend/.env; restart the API after adding it" },
      "AI: no key",
    );
  }
  const status = ai.busy ? "reading…" : ai.error ? `problem: ${ai.error}` : ai.on ? `on (${ai.provider}, ${ai.model})` : "off";
  return h(
    "button",
    {
      class: `btn small ai-toggle${ai.on ? " on" : ""}${ai.error ? " ai-error" : ""}`,
      role: "switch",
      "aria-checked": String(ai.on),
      title: `AI reading ${status}. A model reads the recent captions to catch what the rules miss. Its values are pending chips like any other.`,
      onclick: () => setAi(!ai.on),
    },
    ai.busy ? "AI reading…" : ai.on ? "AI on" : "AI off",
  );
}

function renderNotes() {
  const root = $("#notes");
  const existing = root.querySelector("textarea");
  const text = existing ? existing.value : "";
  const open = root.querySelector("details")?.open || false;
  const area = h("textarea", { rows: "3", placeholder: "e.g. CGPA 8.2, IELTS 7 with 6 in writing, budget 40 lakhs, wants Canada", "aria-label": "Notes" });
  area.value = text;
  root.replaceChildren(
    h(
      "details",
      { open },
      h("summary", {}, "Type or paste what the student said"),
      area,
      h(
        "div",
        { class: "row" },
        h("button", { class: "btn small", disabled: state.ai.busy, onclick: () => findInNotes(area.value) }, "Find values"),
        h("span", { class: "muted" }, state.notesMessage),
      ),
      h("div", { class: "hint" }, "Values found here or in captions wait as amber chips until you accept them."),
    ),
  );
}

function renderEditor(f, fromPending) {
  const start = fromPending ? state.pending[f.key]?.value : state.confirmed[f.key]?.value;
  const source = fromPending ? state.pending[f.key]?.source || "caption" : "manual";
  const heard = fromPending ? state.pending[f.key] : null;
  const error = h("div", { class: "error", role: "alert" });
  let read; // returns the raw input for parseValue
  let control;
  let newStretch = null; // budget editor only

  if (f.kind === "tags" && state.vocab?.[f.vocab]?.length) {
    const chosen = new Set(start || []);
    const options = [...new Set([...state.vocab[f.vocab], ...(f.countries ? EXTRA_COUNTRIES : []), ...chosen])];
    control = h(
      "div",
      { class: "row" },
      options.map((t) =>
        h(
          "button",
          {
            class: "tag-opt",
            "aria-pressed": String(chosen.has(t)),
            onclick: (e) => {
              chosen.has(t) ? chosen.delete(t) : chosen.add(t);
              e.currentTarget.setAttribute("aria-pressed", String(chosen.has(t)));
            },
          },
          f.countries ? country(t) : pretty(t),
        ),
      ),
    );
    read = () => [...chosen];
  } else if (f.kind === "tags") {
    const input = h("input", { type: "text", value: (start || []).join(", "), placeholder: "comma separated", "aria-label": f.label });
    control = h("div", { class: "row" }, input);
    read = () => input.value;
  } else if (f.kind === "intake") {
    const intakes = state.vocab?.intakes || [];
    const select = h(
      "select",
      { "aria-label": f.label },
      h("option", { value: "" }, "Choose intake"),
      intakes.map((i, idx) => h("option", { value: String(idx), selected: same(i, start) }, intakeLabel(i))),
    );
    control = h("div", { class: "row" }, select);
    read = () => intakes[Number(select.value)] ?? (select.value === "" ? null : start);
  } else if (f.kind === "choice") {
    const select = h(
      "select",
      { "aria-label": f.label },
      h("option", { value: "" }, "Choose"),
      f.options.map((o) => h("option", { value: String(o), selected: o === start }, `${o}${f.unit || ""}`)),
    );
    control = h("div", { class: "row" }, select);
    read = () => select.value;
  } else if (f.kind === "lakhs") {
    const input = h("input", { type: "number", min: "0", step: "0.5", value: start != null ? String(start / 1e5) : "", "aria-label": "Budget in lakhs" });
    const stretch = h("input", { type: "number", min: "0", max: "50", step: "5", value: String(state.stretch), "aria-label": "Allowed stretch percent" });
    control = h(
      "div",
      {},
      h("div", { class: "row" }, "₹", input, "lakh total, including living costs"),
      h("div", { class: "row" }, "Allow", stretch, "% over budget", h("span", { class: "muted" }, "(your call, default 0)")),
    );
    read = () => {
      const s = Number(stretch.value || 0);
      newStretch = Number.isNaN(s) || s < 0 || s > 50 ? NaN : s;
      return input.value;
    };
  } else {
    const input = h("input", { type: "number", min: String(f.min), max: String(f.max), step: String(f.step), value: start != null ? String(start) : "", "aria-label": f.label });
    control = h("div", { class: "row" }, input, f.unit ? f.unit.trim() : null);
    read = () => input.value;
  }

  const submit = () => {
    const parsed = parseValue(f, read());
    if (parsed.error) return (error.textContent = parsed.error);
    if (Number.isNaN(newStretch)) return (error.textContent = "Stretch must be between 0 and 50");
    if (newStretch != null) state.stretch = newStretch;
    confirmValue(f.key, parsed.value, source);
  };
  const cancel = () => {
    state.editing = null;
    renderProfile();
  };

  return h(
    "div",
    {
      class: "editor",
      onkeydown: (e) => {
        if (e.key === "Escape") cancel();
        if (e.key === "Enter" && e.target.tagName === "INPUT") submit();
      },
    },
    h("div", { class: "editor-title" }, fromPending ? `${f.label}: check what was heard` : f.label),
    heard?.evidence ? h("div", { class: "heard" }, `“${heard.evidence}”`) : null,
    heard?.note ? h("div", { class: "hint" }, heard.note) : null,
    control,
    error,
    h(
      "div",
      { class: "row" },
      h("button", { class: "btn primary", onclick: submit }, "Confirm"),
      state.confirmed[f.key] && !fromPending ? h("button", { class: "btn", onclick: () => clearValue(f.key) }, "Clear") : null,
      h("button", { class: "btn", onclick: cancel }, "Cancel"),
    ),
  );
}

// ---------------------------------------------------------------------------
// Rendering: shortlist
// ---------------------------------------------------------------------------

function movement(courseId, index) {
  if (!state.prevOrder.length) return null;
  const before = state.prevOrder.indexOf(courseId);
  if (before === -1) return h("span", { class: "move", title: "Newly ranked" }, "new");
  const delta = before - index;
  if (delta === 0) return null;
  return h("span", { class: "move", title: `Was #${before + 1}` }, delta > 0 ? `▲${delta}` : `▼${-delta}`);
}

function courseCard(r, index) {
  const open = state.expanded.has(r.course_id);
  const positives = r.reasons.filter((x) => x.kind === "positive");
  const warnings = r.reasons.filter((x) => x.kind === "warning");
  const why = positives[0]?.text || r.reasons.find((x) => x.kind === "info")?.text;
  const tier = r.academic_tier ? ` · ${r.academic_tier}` : "";

  const head = h(
    "button",
    {
      class: "card-head",
      "aria-expanded": String(open),
      onclick: () => {
        open ? state.expanded.delete(r.course_id) : state.expanded.add(r.course_id);
        renderShortlist();
      },
    },
    h(
      "div",
      { class: "line1" },
      h("span", { class: "rank" }, index + 1),
      h("span", { class: "name" }, r.course_name),
      movement(r.course_id, index),
      h("span", { class: `pill ${bandClass(r.band)}` }, r.band),
    ),
    h("div", { class: "sub" }, `${r.university} · ${country(r.country)} · ${inr(r.total_cost_inr)}${tier}`),
    why ? h("div", { class: "why" }, why) : null,
    warnings.length
      ? h("div", { class: "flag" }, warnings[0].text, warnings.length > 1 ? h("span", { class: "muted" }, ` (+${warnings.length - 1} more)`) : null)
      : null,
  );

  const pick = pickButton(r.course_id, r.course_name);
  if (!open) return h("div", { class: "card" }, pick, head);

  const body = h(
    "div",
    { class: "card-body" },
    h("ul", { class: "reasons" }, r.reasons.map((x) => h("li", { class: x.kind }, x.text))),
    h(
      "div",
      { class: "bars", "aria-label": "Sub scores" },
      Object.entries(SUBSCORE_LABELS).map(([k, label]) => {
        const v = r.subscores[k] ?? 0;
        return [
          h("span", {}, label),
          h("span", { class: "bar" }, h("span", { style: `width:${Math.round(v * 100)}%` })),
          h("span", { class: "num" }, v.toFixed(2)),
        ];
      }),
    ),
    h(
      "div",
      { class: "meta" },
      h("span", {}, `Score ${r.score}`),
      h("span", {}, `Eligibility: ${pretty(r.eligibility)}`),
      r.data_status !== "verified" ? h("span", { class: "pill data", title: "Fees, deadlines and rules are approximations until checked" }, "Illustrative data") : null,
      r.source_url ? h("a", { href: r.source_url, target: "_blank", rel: "noopener noreferrer" }, "Official page") : null,
    ),
  );
  return h("div", { class: "card" }, pick, head, body);
}

function provenance(r) {
  return r ? `${r.preset_label} preset · config ${r.config_version} · as of ${longDate(r.as_of)}` : "";
}

function renderShortlist() {
  const root = $("#shortlist");
  const r = state.result;
  // Every result states the preset and config version that produced it.
  const prov = $("#provenance");
  if (prov) prov.textContent = provenance(r);
  if (!r) {
    root.replaceChildren(h("div", { class: "empty" }, state.error ? "No results while the service is unreachable." : "Loading…"));
    return;
  }

  const shown = state.showAll ? r.ranked : r.ranked.slice(0, SHORTLIST_SIZE);
  const parts = [];

  if (state.error) {
    parts.push(h("div", { class: "flag", style: "margin-top:8px" }, "Showing the last result; it may not reflect recent profile changes."));
  }

  if (r.confidence < LOW_CONFIDENCE) {
    parts.push(h("div", { class: "hint", style: "margin-top:8px" }, "Low confidence: few facts confirmed yet, so treat this order as provisional."));
  }

  for (const c of r.close_calls) {
    parts.push(h("div", { class: "close-call" }, h("b", {}, c.higher), " vs ", h("b", {}, c.lower), `. ${c.note}.`));
  }

  const lens = lensBox();
  if (lens) parts.push(lens);

  parts.push(
    r.ranked.length
      ? h("div", { class: "list" }, shown.map(courseCard))
      : h("div", { class: "empty" }, "No course passes every gate. Check the set aside list below."),
  );

  parts.push(saveRow());

  if (r.ranked.length > SHORTLIST_SIZE) {
    parts.push(
      h(
        "button",
        { class: "btn link", style: "margin-top:6px", onclick: () => { state.showAll = !state.showAll; renderShortlist(); } },
        state.showAll ? "Show top 5 only" : `Show all ${r.ranked.length} ranked`,
      ),
    );
  }

  if (r.set_aside.length) {
    parts.push(
      h(
        "details",
        { class: "aside" },
        h("summary", {}, `Set aside (${r.set_aside.length}): failed a dealbreaker`),
        r.set_aside.map((s) =>
          h(
            "div",
            { class: "aside-item" },
            h("div", { class: "line1" }, h("span", { class: "name" }, s.course_name), h("span", { class: "pill gate" }, GATE_LABELS[s.gate] || s.gate)),
            h("div", { class: "sub" }, `${s.university} · ${country(s.country)}`),
            s.reasons.map((t) => h("div", { class: "why" }, t)),
          ),
        ),
      ),
    );
  }

  root.replaceChildren(...parts);
}

// ---------------------------------------------------------------------------
// Rendering: search
// ---------------------------------------------------------------------------

let searchTimer = null;
let searchSeq = 0;

async function runSearch() {
  const id = ++searchSeq;
  const params = { q: state.search.q, limit: "30" };
  if (state.search.country) params.country = state.search.country;
  try {
    const results = await api.search(params);
    if (id !== searchSeq) return;
    state.search.results = results;
    state.search.error = null;
  } catch (err) {
    if (id !== searchSeq) return;
    state.search.error = err.message;
  }
  renderSearchResults();
}

function standing(courseId) {
  const r = state.result;
  if (!r) return null;
  const i = r.ranked.findIndex((x) => x.course_id === courseId);
  if (i !== -1) return h("span", { class: `pill ${bandClass(r.ranked[i].band)}` }, `#${i + 1} ${r.ranked[i].band}`);
  const s = r.set_aside.find((x) => x.course_id === courseId);
  if (s) return h("span", { class: "pill gate", title: s.reasons.join("; ") }, `Set aside: ${GATE_LABELS[s.gate] || s.gate}`);
  return null;
}

function searchCard(c) {
  const open = state.search.expanded.has(c.id);
  const head = h(
    "button",
    {
      class: "card-head",
      "aria-expanded": String(open),
      onclick: () => {
        open ? state.search.expanded.delete(c.id) : state.search.expanded.add(c.id);
        renderSearchResults();
      },
    },
    h("div", { class: "line1" }, h("span", { class: "name" }, c.name), standing(c.id)),
    h("div", { class: "sub" }, `${c.university} · ${country(c.country)} · ${c.total_cost_label} total · ${c.post_study_work_months} mo work rights`),
  );
  if (!open) return h("div", { class: "card" }, pickButton(c.id, c.name), head);

  const set = state.result?.set_aside.find((x) => x.course_id === c.id);
  const body = h(
    "div",
    { class: "card-body" },
    h(
      "dl",
      { class: "kv" },
      h("dt", {}, "Level"), h("dd", {}, `${pretty(c.degree_level)}, ${c.duration_months} months`),
      h("dt", {}, "Typical admit"), h("dd", {}, c.typical_admit_gpa_percent != null ? `${c.typical_admit_gpa_percent}% GPA` : "Not recorded"),
      h("dt", {}, "Intakes"),
      h("dd", {}, h("ul", {}, c.intakes.map((i) => h("li", {}, `${intakeLabel(i)}: ${i.application_deadline ? `apply by ${longDate(i.application_deadline)}` : "no deadline recorded"} (${i.status})`)))),
      h("dt", {}, "Entry rules"),
      h("dd", {}, c.eligibility_rules.length ? h("ul", {}, c.eligibility_rules.map((r) => h("li", {}, ruleText(r), r.note ? h("span", { class: "muted" }, ` (${r.note})`) : null))) : "None recorded"),
      h("dt", {}, "Fields"), h("dd", {}, c.field_tags.map(pretty).join(", ")),
      h("dt", {}, "Typical roles"), h("dd", {}, c.career_tags.map(pretty).join(", ")),
      set ? [h("dt", {}, "Set aside"), h("dd", {}, set.reasons.join("; "))] : null,
    ),
    h(
      "div",
      { class: "meta" },
      c.data_status !== "verified" ? h("span", { class: "pill data" }, "Illustrative data") : null,
      c.last_verified ? h("span", {}, `Checked ${longDate(c.last_verified)}`) : null,
      c.source_url ? h("a", { href: c.source_url, target: "_blank", rel: "noopener noreferrer" }, "Official page") : null,
    ),
  );
  return h("div", { class: "card" }, pickButton(c.id, c.name), head, body);
}

function renderSearchResults() {
  const box = $("#search-results");
  if (!box) return;
  const s = state.search;
  if (s.error) return box.replaceChildren(h("div", { class: "empty" }, `Search failed: ${s.error}`));
  if (!s.results) return box.replaceChildren(h("div", { class: "empty" }, "Searching…"));
  if (!s.results.length) return box.replaceChildren(h("div", { class: "empty" }, "No courses match."));
  box.replaceChildren(h("div", { class: "hint" }, `${s.results.length} courses`), h("div", { class: "list" }, s.results.map(searchCard)));
}

function renderSearch() {
  const input = h("input", {
    type: "search",
    placeholder: "Course, university, city or tag",
    value: state.search.q,
    "aria-label": "Search courses",
    oninput: (e) => {
      state.search.q = e.target.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(runSearch, 200);
    },
  });
  const countries = state.vocab?.countries || [];
  const select = h(
    "select",
    {
      "aria-label": "Country",
      onchange: (e) => {
        state.search.country = e.target.value;
        runSearch();
      },
    },
    h("option", { value: "" }, "All countries"),
    countries.map((c) => h("option", { value: c, selected: c === state.search.country }, country(c))),
  );
  $("#search").replaceChildren(h("div", { class: "search-row" }, input, select), h("div", { id: "search-results" }));
  renderSearchResults();
  if (!state.search.results) runSearch();
}

// ---------------------------------------------------------------------------
// Tabs, footer, start up
// ---------------------------------------------------------------------------

function selectTab(tab) {
  state.tab = tab;
  for (const name of ["shortlist", "search"]) {
    $(`#tab-${name}`).setAttribute("aria-selected", String(name === tab));
    $(`#${name}`).hidden = name !== tab;
  }
  if (tab === "search") {
    renderSearch();
    $("#search input")?.focus();
  }
}

function renderFooter() {
  const r = state.result;
  const input = h("input", { type: "text", value: apiBase, "aria-label": "Assist service address" });
  const name = h("input", { type: "text", value: counsellorName, placeholder: "Your name or initials", "aria-label": "Counsellor name" });
  $("#footer").replaceChildren(
    h("div", { id: "provenance" }, provenance(r)),
    h("div", {}, "Course data is illustrative. Verify fees, deadlines and rules before advising."),
    h(
      "details",
      {},
      h("summary", {}, "Settings"),
      h("div", { class: "row" }, name),
      h(
        "div",
        { class: "row" },
        input,
        h(
          "button",
          {
            class: "btn small",
            onclick: async () => {
              apiBase = input.value.trim() || DEFAULT_API_BASE;
              saveApiBase(apiBase);
              counsellorName = name.value.trim();
              saveCounsellor(counsellorName);
              api = makeApi(apiBase);
              await loadReference();
              renderAll();
              refresh();
            },
          },
          "Save",
        ),
      ),
    ),
  );
}

function renderAll() {
  renderHeader();
  renderBanner();
  renderAsk();
  renderProfile();
  renderNotes();
  renderShortlist();
  renderFooter();
  if (state.tab === "search") renderSearch();
}

async function init() {
  const saved = await loadSession();
  if (saved) {
    const { aiOn, ...rest } = saved;
    Object.assign(state, rest);
    state.ai.on = !!aiOn;
  }
  if (ext) {
    const { ggListen, ggCaptionStatus } = await ext.storage.local.get(["ggListen", "ggCaptionStatus"]);
    state.captions = { listen: !!ggListen, state: ggCaptionStatus?.state || "off" };
    ext.storage.onChanged.addListener((changes, area) => {
      if (area !== "local") return;
      if (changes.ggCaptionStatus) state.captions.state = changes.ggCaptionStatus.newValue?.state || "off";
      if (changes.ggListen) state.captions.listen = !!changes.ggListen.newValue;
      renderListen();
    });
  }
  apiBase = await loadApiBase();
  counsellorName = await loadCounsellor();
  api = makeApi(apiBase);

  $("#tab-shortlist").addEventListener("click", () => selectTab("shortlist"));
  $("#tab-search").addEventListener("click", () => selectTab("search"));

  renderAll();
  await loadReference();
  renderAll();
  refresh();
}

// Pending chips arrive from the caption listener; they wait for the counsellor.
globalThis.chrome?.runtime?.onMessage?.addListener((msg) => {
  if (msg?.type === "gg:propose") propose(msg.field, msg.value, { source: "caption", evidence: msg.evidence, note: msg.note });
  if (msg?.type === "gg:transcript" && Array.isArray(msg.lines)) addTranscriptLines(msg.lines);
});
globalThis.ggPropose = propose;
globalThis.ggTranscript = (...lines) => addTranscriptLines(lines); // simulate caption lines in demos

init();

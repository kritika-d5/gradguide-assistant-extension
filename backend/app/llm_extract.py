"""Model assisted extraction of profile values from a noisy live caption transcript.

Meet's captions mishear words ("eyelid" for IELTS, "btec" for B.Tech) and insert stray full
stops, which defeats pure pattern matching. A language model reads the recent transcript and
proposes values, but it is never trusted on its own:

1. Every proposal must quote the transcript. A quote that is not found in the transcript is
   rejected, so the model cannot invent a value nobody said.
2. The model returns every value as a string; this module parses, range checks and normalises it
   (the CGPA to percent conversion is done here, not by the model).
3. Tags are limited to the catalogue vocabulary.
4. Results are only ever *pending* chips in the side panel. The counsellor confirms each one, and
   the ranking engine stays deterministic on confirmed values. No model is in the ranking path.

The provider is pluggable: Groq (default, free tier) or Anthropic, chosen with LLM_PROVIDER.
Keys come from the environment or backend/.env. Without a key the endpoint reports itself as
disabled and the panel uses its in browser rules alone.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_MODELS = {"groq": "openai/gpt-oss-120b", "anthropic": "claude-opus-5-5"}
REQUEST_TIMEOUT_S = 20.0

# Same conversion as the browser rules (extension/extract.js), shown on the chip.
CGPA_TO_PERCENT = 9.5

# Countries a student may name beyond the catalogue; a preference for them is still useful to know.
EXTRA_COUNTRIES = ["usa", "australia", "ireland", "new_zealand"]

SCALAR_FIELDS = [
    "gpa_percent", "ielts_overall", "ielts_min_band", "backlogs",
    "work_exp_months", "degree_years", "budget_inr", "target_intake",
    "priority_preset",  # not a profile field: suggests a preset to the counsellor
]
LIST_FIELDS = ["interest_tags", "career_tags", "background_tags", "target_countries", "excluded_countries", "target_levels"]

# What the student says matters most, mapped to the priority presets in config/presets.yaml.
PRIORITY_PRESETS = {
    "budget": "budget_first", "cost": "budget_first",
    "career": "career_first", "job": "career_first",
    "work_rights": "work_rights_first", "work rights": "work_rights_first", "settle": "work_rights_first",
    "prestige": "prestige_first", "ranking": "prestige_first", "reputation": "prestige_first",
}
FIELDS = SCALAR_FIELDS + LIST_FIELDS

# Structured output schema shared by both providers. Values are strings on purpose: one simple
# schema works with strict decoding everywhere, and parsing stays in deterministic code.
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": FIELDS},
                    "value": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["field", "value", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class ExtractionUnavailable(Exception):
    """No provider configured."""


class ExtractionError(Exception):
    """The provider call failed or returned something unusable."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMConfig:
    provider: Optional[str]  # "groq", "anthropic" or None when disabled
    model: Optional[str]

    @property
    def enabled(self) -> bool:
        return self.provider is not None


def config_from_env() -> LLMConfig:
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider in ("none", "off"):
        return LLMConfig(None, None)
    if not provider:
        # Pick whichever key is present, Groq first because it is free.
        provider = "groq" if os.getenv("GROQ_API_KEY") else "anthropic" if os.getenv("ANTHROPIC_API_KEY") else ""
    if provider not in DEFAULT_MODELS:
        return LLMConfig(None, None)
    key = "GROQ_API_KEY" if provider == "groq" else "ANTHROPIC_API_KEY"
    if not os.getenv(key):
        return LLMConfig(None, None)
    return LLMConfig(provider, os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODELS[provider])


# ---------------------------------------------------------------------------
# Prompt (pure)
# ---------------------------------------------------------------------------

def build_system_prompt(vocab: dict[str, list]) -> str:
    def tags(key: str) -> str:
        return ", ".join(vocab.get(key, []))

    countries = ", ".join(sorted(set(vocab.get("countries", [])) | set(EXTRA_COUNTRIES)))
    return f"""You read the live caption transcript of a study abroad counselling call between a counsellor and an Indian student, and pull out facts the student states about themselves.

The captions come from automatic speech recognition, so expect misheard words and stray full stops in the middle of sentences. Read for meaning. Examples: "eyelid", "I lets" or "eye elts" usually mean IELTS; "btec", "b tech" or "be tech" mean B.Tech; "see GPA" means CGPA; "lacks" after a number means lakhs; "CR", "CRS" or "Cr" after a number mean crore (1 crore = 10000000 rupees); numbers are often spelled out ("eight", "one point five"); "My GPA is. 8, 8.0" means a GPA of 8.0.

Rules:
- Only extract what the student says about themselves, or clearly confirms. A counsellor's question alone is not a fact.
- If a value is garbled or implausible (for example an IELTS score of 99), leave it out. Leaving a field out is always better than guessing.
- "quote" must be copied exactly from the transcript, a short span that contains the evidence.
- When the student corrects themselves, use the latest value.
- Return each value as a string in the format below. Skip fields that were not mentioned.

Fields and value formats:
- gpa_percent: the grade as said, with its scale: "8.2/10", "3.4/4" or "78%"
- ielts_overall, ielts_min_band: a number such as "6.5" (lowest single band for ielts_min_band)
- backlogs: whole number, "0" for none
- work_exp_months: full time work experience in months, "0" for a fresher; internships do not count
- degree_years: "3" or "4" (B.Tech and BE are 4; BSc, BCA, BCom and BBA are usually 3)
- budget_inr: total budget in rupees as digits, e.g. "4000000" for 40 lakhs or "15000000" for 1.5 crore; if the student gives a range and then a preferred limit ("one to two crore, but preferably under 1.5"), use the preferred limit, otherwise the upper end of the range
- target_intake: "<term> <year>" with term jan, may or sep, year optional, e.g. "sep 2027" or "jan"
- target_countries: countries the student wants, comma separated, from: {countries}. Leave out countries the student rules out.
- excluded_countries: countries the student refuses or wants to avoid, from the same list ("not the UK or the US" gives "uk, usa")
- priority_preset: what the student says matters most, one of: budget, career, work_rights, prestige. Only when they clearly say it is their top priority or main concern.
- target_levels: from: {tags("levels")}
- interest_tags: subjects the student wants to study, from: {tags("interest_tags")}
- career_tags: roles the student wants after graduating, from: {tags("career_tags")}
- background_tags: the student's undergraduate subject, from: {tags("background_tags")}

Respond with JSON only, in the form {{"items": [{{"field": "...", "value": "...", "quote": "..."}}]}}. Use {{"items": []}} when nothing applies."""


def build_user_prompt(transcript: str) -> str:
    return f"<transcript>\n{transcript}\n</transcript>"


# ---------------------------------------------------------------------------
# Validation (pure)
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _numbers(text: str) -> list[float]:
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))]


def _tag(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def parse_value(field: str, raw: str, vocab: dict[str, list]) -> tuple[Any, Optional[str]]:
    """Returns (value, note). Raises ValueError when the value is unusable."""
    text = raw.strip().lower()
    nums = _numbers(text)

    if field == "gpa_percent":
        if not nums:
            raise ValueError("no number")
        v = nums[0]
        if "%" in text or "percent" in text or v > 10:
            if 40 <= v <= 100:
                return v, None
        elif re.search(r"(/|out of)\s*4\b", text):
            if v <= 4:
                return round(v / 4 * 100, 1), f"GPA {v:g}/4 converted to percent"
        elif v <= 10:
            return round(v * CGPA_TO_PERCENT, 1), f"CGPA {v:g} × {CGPA_TO_PERCENT:g}"
        raise ValueError("GPA out of range")

    if field in ("ielts_overall", "ielts_min_band"):
        if not nums or not (0 <= nums[0] <= 9) or (nums[0] * 2) % 1:
            raise ValueError("IELTS scores run 0 to 9 in half bands")
        return nums[0], None

    if field in ("backlogs", "work_exp_months"):
        if text in ("none", "no", "zero", "nil", "fresher"):
            return 0, None
        limit = 50 if field == "backlogs" else 600
        if not nums or nums[0] != int(nums[0]) or not (0 <= nums[0] <= limit):
            raise ValueError("not a plausible whole number")
        return int(nums[0]), None

    if field == "degree_years":
        if not nums or nums[0] not in (3, 4):
            raise ValueError("degree length must be 3 or 4")
        return int(nums[0]), None

    if field == "budget_inr":
        if not nums:
            raise ValueError("no amount")
        v = max(nums)
        if "crore" in text or re.search(r"\bcrs?\b", text):
            v *= 1e7
        elif "lakh" in text or "lac" in text:
            v *= 1e5
        if not (1e5 <= v <= 1e8):
            raise ValueError("budget outside ₹1L to ₹10Cr")
        return round(v), None

    if field == "priority_preset":
        preset = PRIORITY_PRESETS.get(text.replace("_first", "").strip())
        if not preset:
            raise ValueError("unknown priority")
        return preset, None

    if field == "target_intake":
        m = re.search(r"\b(jan|january|spring|may|summer|sep|sept|september|fall|autumn)\b\s*(20\d\d)?", text)
        if not m:
            raise ValueError("unknown intake")
        term = {"january": "jan", "spring": "jan", "summer": "may", "sept": "sep", "september": "sep", "fall": "sep", "autumn": "sep"}.get(m.group(1), m.group(1))
        year = int(m.group(2)) if m.group(2) else None
        if year is not None and not (2024 <= year <= 2040):
            raise ValueError("implausible year")
        return {"term": term, "year": year}, None

    # List fields: keep only tags the catalogue knows.
    allowed = {
        "interest_tags": vocab.get("interest_tags", []),
        "career_tags": vocab.get("career_tags", []),
        "background_tags": vocab.get("background_tags", []),
        "target_levels": vocab.get("levels", []),
        "target_countries": sorted(set(vocab.get("countries", [])) | set(EXTRA_COUNTRIES)),
        "excluded_countries": sorted(set(vocab.get("countries", [])) | set(EXTRA_COUNTRIES)),
    }[field]
    aliases = {"uk": "uk", "united_kingdom": "uk", "us": "usa", "the_us": "usa", "united_states": "usa", "master_s": "masters", "master": "masters"}
    tags = sorted({aliases.get(_tag(t), _tag(t)) for t in re.split(r"[,;/]| and ", text) if t.strip()} & set(allowed))
    if not tags:
        raise ValueError("no known tags")
    return tags, None


def validate(raw_json: str, transcript: str, vocab: dict[str, list]) -> tuple[list[dict], list[dict]]:
    """Check the model's output against the transcript. Returns (accepted, rejected)."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ExtractionError("model did not return JSON") from exc
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ExtractionError("model output has no items list")

    haystack = _norm(transcript)
    accepted: dict[str, dict] = {}
    rejected: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        field, value, quote = item.get("field"), str(item.get("value", "")), str(item.get("quote", ""))
        reason = None
        if field not in FIELDS:
            reason = "unknown field"
        elif len(_norm(quote)) < 2 or _norm(quote) not in haystack:
            reason = "quote not found in transcript"
        else:
            try:
                parsed, note = parse_value(field, value, vocab)
            except ValueError as exc:
                reason = str(exc)
        if reason:
            rejected.append({"field": field, "value": value, "quote": quote, "reason": reason})
            continue
        result = {"field": field, "value": parsed, "evidence": quote.strip()}
        if note:
            result["note"] = note
        if field in LIST_FIELDS and field in accepted:
            result["value"] = sorted(set(accepted[field]["value"]) | set(parsed))
        accepted[field] = result  # later mentions win, like the browser rules
    return list(accepted.values()), rejected


# ---------------------------------------------------------------------------
# Provider calls (the only part that touches the network)
# ---------------------------------------------------------------------------

def _call_groq(model: str, system: str, user: str) -> str:
    import groq

    client = groq.Groq(timeout=REQUEST_TIMEOUT_S, max_retries=1)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
    }
    if model.startswith(("openai/gpt-oss", "qwen/")):
        # These models support strict schema decoding; others get plain JSON mode.
        kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "profile_values", "strict": True, "schema": SCHEMA}}
    else:
        kwargs["response_format"] = {"type": "json_object"}
    if model.startswith("openai/gpt-oss"):
        kwargs["reasoning_effort"] = "low"
    try:
        response = client.chat.completions.create(**kwargs)
    except groq.AuthenticationError as exc:
        raise ExtractionError("Groq rejected the API key") from exc
    except groq.RateLimitError as exc:
        raise ExtractionError("Groq rate limit reached; rules still work") from exc
    except groq.APIStatusError as exc:
        raise ExtractionError(f"Groq error {exc.status_code}") from exc
    except groq.APIConnectionError as exc:
        raise ExtractionError("cannot reach Groq") from exc
    return response.choices[0].message.content or ""


def _call_anthropic(model: str, system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_S, max_retries=1)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 16000,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        # A short extraction task: low effort keeps latency down mid call.
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    }
    try:
        if model.startswith(("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")):
            # Server side fallback re-runs a declined request on another model.
            response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            response = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise ExtractionError("Anthropic rejected the API key") from exc
    except anthropic.RateLimitError as exc:
        raise ExtractionError("Anthropic rate limit reached; rules still work") from exc
    except anthropic.APIStatusError as exc:
        raise ExtractionError(f"Anthropic error {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionError("cannot reach Anthropic") from exc
    if response.stop_reason == "refusal":
        raise ExtractionError("the model declined this transcript")
    return next((b.text for b in response.content if b.type == "text"), "")


PROVIDERS = {"groq": _call_groq, "anthropic": _call_anthropic}


def extract(transcript: str, vocab: dict[str, list], cfg: Optional[LLMConfig] = None) -> dict:
    cfg = cfg or config_from_env()
    if not cfg.enabled:
        raise ExtractionUnavailable("no LLM provider configured")
    raw = PROVIDERS[cfg.provider](cfg.model, build_system_prompt(vocab), build_user_prompt(transcript))
    accepted, rejected = validate(raw, transcript, vocab)
    return {"provider": cfg.provider, "model": cfg.model, "items": accepted, "rejected": rejected}
